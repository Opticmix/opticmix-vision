"""정류된 스테레오 쌍 → 시차(disparity) → 깊이(mm). OpenCV SGBM 만 쓴다(새 의존성 없음).

이 카메라는 **온보드 깊이 엔진이 없다.** 좌·우 원본 IR 프레임만 나온다. 그래서 깊이가 필요하면
호스트에서 계산해야 하고, 이 모듈이 그 최소 경로다:

    원본 좌/우 → rectify(KB4 맵) → SGBM 시차 → Z = f_rect · B / d

`f_rect` 는 **정류 후 핀홀 초점거리**(`RectifyMaps.P1[0,0]`)이지 원본 K 의 fx 가 아니다. 어안에서
이 둘은 크게 다르므로 원본 fx 를 쓰면 깊이가 통째로 틀린다. 그래서 이 모듈은 f 를 받지 않고
`RectifyMaps` 에서 직접 읽는다.

정직한 한계:
  - SGBM 은 텍스처가 있어야 매칭한다. 850 nm 플러드 조명 아래 맨 벽·피부 안쪽은 무효가 많이 난다.
    무효 화소는 **NaN** 이다. 0 이나 큰 수로 채우지 않는다 — 조용히 틀린 깊이가 제일 나쁘다.
  - 정확도 수치(RMSE 등)는 아직 없다. 실기기 측정 전까지 이 모듈의 출력을 사양으로 쓰지 말 것.
  - 파라미터 기본값은 800×800 IR 모노를 염두에 둔 출발점이지 튜닝 결과가 아니다.

사용:
    from opticmix_vision import load_calibration, depth_from_pair
    calib = load_calibration("OPX-S1-0001.json")
    res = depth_from_pair(frame.left, frame.right, calib, rectified_hfov_deg=90.0)
    print(res.summary())          # 유효 비율·깊이 범위
    z_mm = res.depth_mm           # float32, 무효는 NaN

CLI:
    python -m opticmix_vision.depth --calib calib.json --pairs-dir captures/unit1 --out out/depth
    python -m opticmix_vision.depth --calib calib.json --constants oem_uvc_constants.py --live
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np

from .calibration import StereoCalibration, load_calibration
from .rectify import DEFAULT_RECTIFIED_HFOV_DEG, RectifyMaps, remap_pair, stereo_rectify_maps

__all__ = ["DepthParams", "DepthResult", "disparity_to_depth_mm", "compute_disparity",
           "depth_from_rectified", "depth_from_pair", "colorize_depth", "main"]


@dataclass(frozen=True)
class DepthParams:
    """SGBM 파라미터. 기본값은 800×800 IR 모노 기준 **출발점**이며 실측 튜닝 전이다.

    num_disparities 는 탐색 폭이다. 최소 측정 거리 Z_min 에 대응하는 시차가 f_rect·B/Z_min 이므로,
    가까이 보려면 키워야 하고 키우는 만큼 느려진다(그리고 왼쪽 가장자리 무효 폭이 늘어난다).
    """

    num_disparities: int = 96          # 16 의 배수
    block_size: int = 7                # 홀수
    min_disparity: int = 0
    uniqueness_ratio: int = 10
    speckle_window_size: int = 100
    speckle_range: int = 2
    disp12_max_diff: int = 1
    p1_factor: int = 8                 # P1 = p1_factor · block_size²  (OpenCV 문서 권장식)
    p2_factor: int = 32
    mode: str = "sgbm_3way"            # sgbm | sgbm_3way | hh

    def __post_init__(self) -> None:
        if self.num_disparities <= 0 or self.num_disparities % 16 != 0:
            raise ValueError(f"num_disparities 는 양수이면서 16 의 배수여야 한다: {self.num_disparities}")
        if self.block_size < 3 or self.block_size % 2 == 0:
            raise ValueError(f"block_size 는 3 이상 홀수여야 한다: {self.block_size}")
        if self.mode not in ("sgbm", "sgbm_3way", "hh"):
            raise ValueError(f"mode 는 sgbm | sgbm_3way | hh: {self.mode!r}")

    @property
    def invalid_fixed_point(self) -> int:
        """SGBM 이 무효 화소에 쓰는 값 (16 배 고정소수점)."""
        return (self.min_disparity - 1) * 16


def _as_mono_u8(a: np.ndarray, where: str) -> np.ndarray:
    g = np.asarray(a)
    if g.ndim == 3:
        import cv2
        g = cv2.cvtColor(g, cv2.COLOR_BGR2GRAY)
    if g.ndim != 2:
        raise ValueError(f"{where}: 2차원 mono 영상이어야 한다 (shape={np.asarray(a).shape})")
    if g.dtype != np.uint8:
        gf = g.astype(np.float32)
        if g.dtype == np.uint16:
            gf = gf / 257.0
        elif np.issubdtype(g.dtype, np.floating) and gf.size and float(gf.max()) <= 1.0:
            gf = gf * 255.0
        g = np.clip(np.rint(gf), 0, 255).astype(np.uint8)
    return np.ascontiguousarray(g)


def compute_disparity(left: np.ndarray, right: np.ndarray, params: DepthParams | None = None) -> np.ndarray:
    """정류된 좌/우 → 시차 (float32 px). **무효 화소는 NaN**, SGBM 의 ×16 고정소수점은 여기서 없앤다."""
    import cv2
    p = params or DepthParams()
    l = _as_mono_u8(left, "left")
    r = _as_mono_u8(right, "right")
    if l.shape != r.shape:
        raise ValueError(f"좌우 영상 크기가 다르다: {l.shape} vs {r.shape}")

    modes = {"sgbm": cv2.STEREO_SGBM_MODE_SGBM, "sgbm_3way": cv2.STEREO_SGBM_MODE_SGBM_3WAY,
             "hh": cv2.STEREO_SGBM_MODE_HH}
    matcher = cv2.StereoSGBM_create(
        minDisparity=p.min_disparity, numDisparities=p.num_disparities, blockSize=p.block_size,
        P1=p.p1_factor * p.block_size * p.block_size, P2=p.p2_factor * p.block_size * p.block_size,
        disp12MaxDiff=p.disp12_max_diff, uniquenessRatio=p.uniqueness_ratio,
        speckleWindowSize=p.speckle_window_size, speckleRange=p.speckle_range, mode=modes[p.mode])
    raw = matcher.compute(l, r)                       # int16, ×16 고정소수점
    disp = raw.astype(np.float32) / 16.0
    invalid = raw <= p.invalid_fixed_point             # 미매칭·speckle 제거 화소
    disp[invalid] = np.nan
    return disp


def disparity_to_depth_mm(disparity_px: np.ndarray, fx_rect_px: float, baseline_mm: float) -> np.ndarray:
    """Z = f_rect · B / d. d ≤ 0 또는 NaN 은 **NaN** (무한대를 만들지 않는다).

    fx_rect_px 는 반드시 **정류 후** 초점거리다 (`RectifyMaps.rectified_focal_px`).
    """
    f = float(fx_rect_px)
    b = float(baseline_mm)
    if not f > 0.0:
        raise ValueError(f"fx_rect_px 는 양수여야 한다: {fx_rect_px!r}")
    if not b > 0.0:
        raise ValueError(f"baseline_mm 은 양수여야 한다: {baseline_mm!r}")
    d = np.asarray(disparity_px, dtype=np.float32)
    with np.errstate(divide="ignore", invalid="ignore"):
        z = (f * b) / d
    z = np.asarray(z, dtype=np.float32)
    z[~np.isfinite(d) | (d <= 0.0)] = np.nan
    return z


@dataclass(frozen=True, eq=False)
class DepthResult:
    """시차·깊이와 그 기하. 무효 화소는 두 배열 모두 NaN 이고 `valid` 가 False 다."""

    disparity_px: np.ndarray
    depth_mm: np.ndarray
    valid: np.ndarray
    fx_rect_px: float
    baseline_mm: float
    params: DepthParams

    @property
    def valid_fraction(self) -> float:
        return float(self.valid.mean()) if self.valid.size else 0.0

    def percentile_mm(self, q: float) -> float:
        v = self.depth_mm[self.valid]
        return float(np.percentile(v, q)) if v.size else float("nan")

    def summary(self) -> str:
        v = self.depth_mm[self.valid]
        if not v.size:
            return (f"valid 0.0% — 매칭된 화소가 없다 (텍스처 부족·조명·num_disparities "
                    f"{self.params.num_disparities} 확인)")
        return (f"valid {self.valid_fraction * 100:.1f}% / depth p5 {np.percentile(v, 5):.0f} mm · "
                f"median {np.median(v):.0f} mm · p95 {np.percentile(v, 95):.0f} mm "
                f"(f_rect {self.fx_rect_px:.1f} px, B {self.baseline_mm:.2f} mm)")


def depth_from_rectified(left_rect: np.ndarray, right_rect: np.ndarray, maps: RectifyMaps,
                         params: DepthParams | None = None) -> DepthResult:
    """이미 정류된 쌍에서 깊이. f 와 B 는 맵에서 읽는다(호출자가 틀린 f 를 넣을 여지를 없앤다)."""
    p = params or DepthParams()
    disp = compute_disparity(left_rect, right_rect, p)
    f = maps.rectified_focal_px
    b = maps.baseline_from_P2_mm
    z = disparity_to_depth_mm(disp, f, b)
    return DepthResult(disparity_px=disp, depth_mm=z, valid=np.isfinite(z),
                       fx_rect_px=f, baseline_mm=b, params=p)


def depth_from_pair(left: np.ndarray, right: np.ndarray, calib: StereoCalibration, *,
                    maps: RectifyMaps | None = None, params: DepthParams | None = None,
                    rectified_hfov_deg: float = DEFAULT_RECTIFIED_HFOV_DEG) -> DepthResult:
    """원본(미정류) 좌/우 → 깊이. `maps` 를 주면 재사용하고, 없으면 만든다(매 프레임 만들지 말 것)."""
    m = maps if maps is not None else stereo_rectify_maps(calib, rectified_hfov_deg=rectified_hfov_deg)
    lr, rr = remap_pair(_as_mono_u8(left, "left"), _as_mono_u8(right, "right"), m)
    return depth_from_rectified(lr, rr, m, params)


def colorize_depth(res: DepthResult, *, near_mm: float | None = None, far_mm: float | None = None
                   ) -> np.ndarray:
    """표시용 컬러맵 (BGR uint8). 무효 화소는 검정. 범위 미지정 시 유효 화소의 p5~p95."""
    import cv2
    n = res.percentile_mm(5) if near_mm is None else float(near_mm)
    f = res.percentile_mm(95) if far_mm is None else float(far_mm)
    if not np.isfinite(n) or not np.isfinite(f) or f <= n:
        return np.zeros(res.depth_mm.shape + (3,), dtype=np.uint8)
    z = np.clip((res.depth_mm - n) / (f - n), 0.0, 1.0)
    u8 = np.zeros(res.depth_mm.shape, dtype=np.uint8)
    u8[res.valid] = (255 - z[res.valid] * 255).astype(np.uint8)      # 가까울수록 밝게
    out = cv2.applyColorMap(u8, cv2.COLORMAP_TURBO)
    out[~res.valid] = 0
    return out


# ------------------------------------------------------------------ CLI

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m opticmix_vision.depth", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--calib", type=Path, required=True, help="캘리브 JSON (schema v1)")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--pairs-dir", type=Path, help="뷰어가 저장한 좌/우 PNG 디렉토리")
    src.add_argument("--constants", type=Path, help="브링업 상수 파일 — 라이브 카메라에서 계산")
    p.add_argument("--out", type=Path, default=None, help="깊이 PNG(컬러맵)·NPY 저장 디렉토리")
    p.add_argument("--frames", type=int, default=1, help="--constants 일 때 처리할 프레임 수")
    p.add_argument("--rectified-hfov-deg", type=float, default=DEFAULT_RECTIFIED_HFOV_DEG)
    p.add_argument("--num-disparities", type=int, default=96, help="16 의 배수. 가까이 볼수록 크게")
    p.add_argument("--block-size", type=int, default=7, help="홀수")
    p.add_argument("--layout", default=None, choices=("sbs", "tb", "plane_pack"))
    p.add_argument("--lr-order", default=None, choices=("first_is_left", "first_is_right"))
    return p


def _save(res: DepthResult, out_dir: Path, stem: str) -> None:
    import cv2
    out_dir.mkdir(parents=True, exist_ok=True)
    ok, buf = cv2.imencode(".png", colorize_depth(res))
    if not ok:
        raise OSError(f"PNG 인코딩 실패: {stem}")
    buf.tofile(str(out_dir / f"{stem}_depth.png"))           # 한글 경로 안전
    np.save(out_dir / f"{stem}_depth_mm.npy", res.depth_mm)


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    a = build_parser().parse_args(argv)
    try:
        calib = load_calibration(a.calib)
        params = DepthParams(num_disparities=a.num_disparities, block_size=a.block_size)
        maps = stereo_rectify_maps(calib, rectified_hfov_deg=a.rectified_hfov_deg)
        print(f"[depth] f_rect {maps.rectified_focal_px:.1f} px, baseline {maps.baseline_from_P2_mm:.2f} mm, "
              f"num_disparities {params.num_disparities} "
              f"→ 측정 하한 약 {maps.rectified_focal_px * maps.baseline_from_P2_mm / params.num_disparities:.0f} mm")

        if a.pairs_dir is not None:
            from .calibrate import load_pairs_dir
            pairs = load_pairs_dir(a.pairs_dir, layout=a.layout,
                                   lr_order=a.lr_order or "first_is_left")
            if not pairs:
                print(f"[depth] {a.pairs_dir} 에 쌍이 없다", file=sys.stderr)
                return 1
            for i, (l, r) in enumerate(pairs):
                res = depth_from_pair(l, r, calib, maps=maps, params=params)
                print(f"[depth] pair {i:03d}: {res.summary()}")
                if a.out:
                    _save(res, a.out, f"pair{i:03d}")
        else:
            from .device import Device, DeviceConfig
            cfg = DeviceConfig.from_bringup_constants(a.constants)
            with Device(cfg) as cam:
                for i in range(max(1, a.frames)):
                    fr = cam.read()
                    res = depth_from_pair(fr.left, fr.right, calib, maps=maps, params=params)
                    print(f"[depth] frame {i:03d}: {res.summary()}")
                    if a.out:
                        _save(res, a.out, f"frame{i:03d}")
    except Exception as ex:                       # noqa: BLE001 — CLI 경계에서 한 줄로 보고
        print(f"[depth] 실패: {type(ex).__name__}: {ex}", file=sys.stderr)
        return 1
    if a.out:
        print(f"[depth] wrote {a.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
