"""유닛별 스테레오 KB4 캘리브레이션 도구 — 체스보드 프레임 → `StereoCalibration` JSON (schema v1).

    # 실장비: 뷰어에서 's' 로 저장한 프레임 쌍(…_left.png / …_right.png) 디렉토리를 넣는다
    python -m opticmix_vision.calibrate --pairs-dir captures/ --board 9x6 --square-mm 25 \
        --serial OPX-S1-0001 --out calib/OPX-S1-0001.json

    # 하드웨어 없이 전 경로 드라이런 (합성 렌더 → 검출 → 캘리브 → JSON)
    python -m opticmix_vision.calibrate --synthetic --out out/synth_calib.json

모델: cv2.fisheye (Kannala-Brandt 4차). 눈별 `fisheye.calibrate` 뒤 `fisheye.stereoCalibrate(FIX_INTRINSIC)`.
합성 검증: 알려진 K/D/R/t 로 체스보드를 렌더한 뒤 도구가 그 값을 되찾는지 tests/test_calibrate.py 가 본다.

주의:
- 185°급 렌즈는 입사각 90° 근처 코너가 핀홀 근사에서 발산한다. 검출된 코너 중 θ 가 `max_theta_deg`
  를 넘는 뷰는 거부한다 (보드를 너무 가장자리로 보내지 말 것).
- KB4 계수가 화각 안에서 접히면(비단조) `CameraIntrinsics` 검증이 거부한다 — 뷰 커버리지가 부족할 때
  흔한 증상이다. 뷰를 더 다양하게(기울기·거리·가장자리) 찍는다.
"""

from __future__ import annotations

import argparse
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from .calibration import (
    CalibrationError, CameraIntrinsics, StereoCalibration, StereoExtrinsics, save_calibration,
)

__all__ = [
    "BoardSpec", "CalibrateError", "CalibrateResult", "detect_corners", "project_board",
    "render_chessboard_view", "synthetic_board_poses", "calibrate_stereo_from_points",
    "calibrate_from_pairs", "load_pairs_dir", "main",
]


class CalibrateError(RuntimeError):
    """입력 부족·검출 실패·해가 물리적으로 말이 안 되는 경우."""


def _fisheye_flag(name: str, fallback: int) -> int:
    """fisheye 플래그 이름→값. OpenCV 4: cv2.fisheye.CALIB_* (값 1,2,4,8,…). OpenCV 5: 메인 cv2.CALIB_* 로 통합
    (RECOMPUTE_EXTRINSIC=8388608, FIX_SKEW=33554432 등 — 4.x 값을 5 에 넣으면 다른 플래그가 되어 조용히 틀린 해가 나온다,
    실측 2026-09-07). 그래서 이름으로 찾고, 어디에도 없을 때만 4.x 값을 쓴다."""
    import cv2
    for attr in (getattr(cv2.fisheye, name, None), getattr(cv2, "fisheye_" + name, None), getattr(cv2, name, None)):
        if attr is not None:
            return int(attr)
    return fallback


# OpenCV 4.x fisheye 플래그 값 (폴백 전용)
_F_USE_INTRINSIC_GUESS = 1
_F_RECOMPUTE_EXTRINSIC = 2
_F_CHECK_COND = 4
_F_FIX_SKEW = 8
_F_FIX_INTRINSIC = 256


@dataclass(frozen=True)
class BoardSpec:
    """체스보드 **안쪽 코너** 수와 한 칸 크기(mm). OpenCV 관례: cols × rows, 코너 순서는 행 우선."""

    cols: int
    rows: int
    square_mm: float

    def __post_init__(self) -> None:
        if self.cols < 3 or self.rows < 3 or self.square_mm <= 0:
            raise CalibrateError(f"BoardSpec 이상: {self}")
        if self.cols == self.rows or (self.cols % 2 == self.rows % 2):
            # 대칭 보드는 180° 뒤집힘이 구분되지 않아 좌우 코너 순서가 어긋날 수 있다
            raise CalibrateError("cols/rows 는 홀수×짝수(비대칭)여야 한다 (예: 9x6)")

    @property
    def pattern_size(self) -> tuple[int, int]:
        return (self.cols, self.rows)

    @property
    def n_corners(self) -> int:
        return self.cols * self.rows

    def object_points(self) -> np.ndarray:
        """(N, 3) float64, z=0. 순서 = OpenCV 검출 순서(행 우선: row 마다 col 0..cols-1)."""
        grid = np.mgrid[0: self.cols, 0: self.rows].T.reshape(-1, 2).astype(np.float64)   # (N,2): (col,row)
        pts = np.zeros((self.n_corners, 3), dtype=np.float64)
        pts[:, :2] = grid * self.square_mm
        return pts

    @classmethod
    def parse(cls, text: str, square_mm: float) -> "BoardSpec":
        m = re.fullmatch(r"\s*(\d+)\s*[xX×]\s*(\d+)\s*", text)
        if not m:
            raise CalibrateError(f"--board 형식은 COLSxROWS (예: 9x6): {text!r}")
        return cls(cols=int(m.group(1)), rows=int(m.group(2)), square_mm=float(square_mm))


# ---------------------------------------------------------------------------
# 기하: 투영·합성 자세·렌더 (테스트와 --synthetic 이 쓴다)
# ---------------------------------------------------------------------------

def _compose_to_eye(rvec: np.ndarray, tvec: np.ndarray, extrinsics: StereoExtrinsics | None
                    ) -> tuple[np.ndarray, np.ndarray]:
    """보드→왼쪽카메라 (rvec,tvec) 를 오른쪽 눈이면 p_right = R·p_left + t 로 합성한다."""
    import cv2
    r = np.asarray(rvec, dtype=np.float64).reshape(3, 1)
    t = np.asarray(tvec, dtype=np.float64).reshape(3, 1)
    if extrinsics is None:
        return r, t
    Rb, _ = cv2.Rodrigues(r)
    R2 = extrinsics.R @ Rb
    t2 = extrinsics.R @ t + extrinsics.t.reshape(3, 1)
    r2, _ = cv2.Rodrigues(R2)
    return r2.reshape(3, 1), t2


def project_board(board: BoardSpec, rvec: np.ndarray, tvec: np.ndarray, intr: CameraIntrinsics, *,
                  extrinsics: StereoExtrinsics | None = None) -> np.ndarray:
    """보드 코너를 한 눈에 투영. (N, 2) 픽셀. extrinsics 를 주면 오른쪽 눈."""
    import cv2
    r, t = _compose_to_eye(rvec, tvec, extrinsics)
    obj = board.object_points().reshape(-1, 1, 3)
    pts, _ = cv2.fisheye.projectPoints(obj, r, t, intr.K, intr.D.reshape(4, 1))
    return pts.reshape(-1, 2)


def _theta_deg_of_pixels(pts: np.ndarray, intr: CameraIntrinsics) -> np.ndarray:
    """픽셀 → 입사각 θ (deg). KB4 를 수치로 역산한다 (undistortPoints 의 반복과 동일한 의미)."""
    import cv2
    p = np.asarray(pts, dtype=np.float64).reshape(-1, 1, 2)
    n = cv2.fisheye.undistortPoints(p, intr.K, intr.D.reshape(4, 1)).reshape(-1, 2)
    return np.degrees(np.arctan(np.linalg.norm(n, axis=1)))


def synthetic_board_poses(board: BoardSpec, intr_l: CameraIntrinsics, intr_r: CameraIntrinsics,
                          extrinsics: StereoExtrinsics, n: int, seed: int = 0, *,
                          margin_px: float = 12.0, max_theta_deg: float = 75.0,
                          max_tries: int = 20000) -> list[tuple[np.ndarray, np.ndarray]]:
    """두 눈 모두에 완전히 들어오는 무작위 보드 자세 n 개 (rvec, tvec: 보드→왼쪽카메라, mm)."""
    import cv2
    rng = np.random.default_rng(seed)
    w, h = intr_l.size
    center = np.array([(board.cols - 1) * board.square_mm / 2.0, (board.rows - 1) * board.square_mm / 2.0, 0.0])
    out: list[tuple[np.ndarray, np.ndarray]] = []
    tries = 0
    while len(out) < n and tries < max_tries:
        tries += 1
        yaw, pitch = rng.uniform(-35, 35, size=2)
        roll = rng.uniform(-40, 40)
        Rz, _ = cv2.Rodrigues(np.array([0.0, 0.0, math.radians(roll)]))
        Ry, _ = cv2.Rodrigues(np.array([0.0, math.radians(yaw), 0.0]))
        Rx, _ = cv2.Rodrigues(np.array([math.radians(pitch), 0.0, 0.0]))
        R = Rx @ Ry @ Rz
        z = rng.uniform(180.0, 650.0)
        # 보드 중심을 시야 안 무작위 위치에: 정규화 좌표(tan θ)로 뽑아 거리 z 에 둔다.
        # 절반은 가장자리 쪽으로 몰아 관측 θ 가 60° 근처까지 닿게 한다 — 중앙에만 몰리면 k3/k4 가 폭주해
        # 폴백에 의존하고, 주변부 KB4 는 외삽이 된다 (R3 치명 3).
        if tries % 2 == 0:
            nx, ny = rng.uniform(-0.45, 0.45), rng.uniform(-0.3, 0.3)
        else:
            # 너무 바깥(|nx|>0.9)은 어안 왜곡이 심해 체스보드 검출기가 실패한다(실측) — 검출 가능한 범위까지만
            nx = rng.choice([-1.0, 1.0]) * rng.uniform(0.5, 0.78)
            ny = rng.uniform(-0.42, 0.42)
        target = np.array([nx * z, ny * z, z])
        t = target - R @ center
        rvec, _ = cv2.Rodrigues(R)
        ok = True
        for intr, ext in ((intr_l, None), (intr_r, extrinsics)):
            p = project_board(board, rvec, t, intr, extrinsics=ext)
            if not (np.all(p[:, 0] >= margin_px) and np.all(p[:, 0] < w - margin_px)
                    and np.all(p[:, 1] >= margin_px) and np.all(p[:, 1] < h - margin_px)):
                ok = False; break
            if _theta_deg_of_pixels(p, intr).max() > max_theta_deg:
                ok = False; break
            # 카메라 앞에 있어야 한다 (모든 코너 z > 0)
            r_e, t_e = _compose_to_eye(rvec, t, ext)
            Re, _ = cv2.Rodrigues(r_e)
            cam = (Re @ board.object_points().T + t_e).T
            if np.any(cam[:, 2] <= 50.0):
                ok = False; break
        if ok:
            out.append((rvec.reshape(3), t.reshape(3)))
    if len(out) < n:
        raise CalibrateError(f"합성 자세를 {n}개 만들지 못했다 ({len(out)}/{n}, tries={tries})")
    return out


def render_chessboard_view(board: BoardSpec, rvec: np.ndarray, tvec: np.ndarray, intr: CameraIntrinsics, *,
                           extrinsics: StereoExtrinsics | None = None, supersample: int = 2,
                           border_squares: float = 1.0, blur: bool = True) -> np.ndarray:
    """알려진 KB4 모델로 체스보드를 렌더한 (H, W) uint8 영상. 역매핑(픽셀→광선→보드 평면)이라 정확하다.

    supersample: 안티에일리어싱용 정수 배 렌더 후 평균 축소. 코너 검출 정확도가 여기에 달려 있다.
    """
    import cv2
    w, h = intr.size
    s = max(1, int(supersample))
    W, H = w * s, h * s
    # 고해상도 픽셀 중심을 원 해상도 픽셀 좌표로 (픽셀 중심 규약: 정수 = 픽셀 중심)
    xs = (np.arange(W) + 0.5) / s - 0.5
    ys = (np.arange(H) + 0.5) / s - 0.5
    gx, gy = np.meshgrid(xs, ys)
    pix = np.stack([gx.ravel(), gy.ravel()], axis=1).reshape(-1, 1, 2).astype(np.float64)
    norm = cv2.fisheye.undistortPoints(pix, intr.K, intr.D.reshape(4, 1)).reshape(-1, 2)
    rays = np.concatenate([norm, np.ones((norm.shape[0], 1))], axis=1)          # (M,3), z=1

    r, t = _compose_to_eye(rvec, tvec, extrinsics)
    R, _ = cv2.Rodrigues(r)
    t = t.reshape(3)
    nrm = R[:, 2]                                   # 보드 법선 (카메라 좌표)
    denom = rays @ nrm
    numer = float(nrm @ t)
    with np.errstate(divide="ignore", invalid="ignore"):
        sdist = numer / denom
    hit = np.isfinite(sdist) & (sdist > 0)
    P = rays * sdist[:, None]                       # 카메라 좌표의 교점
    B = (P - t) @ R                                 # 보드 좌표 (Rᵀ (P − t))
    u, v = B[:, 0], B[:, 1]
    sq = board.square_mm
    # 체스보드 영역: 칸 경계는 코너 위치. 코너 (0,0)..(cols-1, rows-1) 이므로 칸은 [-sq, cols*sq] × [-sq, rows*sq]
    in_board = hit & (u >= -sq) & (u < board.cols * sq) & (v >= -sq) & (v < board.rows * sq)
    bs = border_squares * sq
    in_border = hit & (u >= -sq - bs) & (u < board.cols * sq + bs) & (v >= -sq - bs) & (v < board.rows * sq + bs)
    parity = (np.floor(u / sq).astype(np.int64) + np.floor(v / sq).astype(np.int64)) & 1
    img = np.full(W * H, 110.0, dtype=np.float32)   # 배경 중간 회색
    img[in_border] = 235.0
    img[in_board] = np.where(parity[in_board] == 0, 25.0, 235.0)
    img = img.reshape(H, W)
    if s > 1:
        img = img.reshape(h, s, w, s).mean(axis=(1, 3))
    out = np.clip(img, 0, 255).astype(np.uint8)
    if blur:
        out = cv2.GaussianBlur(out, (3, 3), 0.6)
    return out


# ---------------------------------------------------------------------------
# 검출
# ---------------------------------------------------------------------------

def detect_corners(gray: np.ndarray, board: BoardSpec) -> np.ndarray | None:
    """체스보드 안쪽 코너 (N, 2) float64 (행 우선). 못 찾으면 None. 서브픽셀 정밀."""
    import cv2
    g = np.asarray(gray)
    if g.ndim == 3:
        g = cv2.cvtColor(g, cv2.COLOR_BGR2GRAY)
    if g.dtype != np.uint8:
        # 16-bit(또는 float) 입력은 **dtype 범위** 기준으로 8-bit 로 되맞춘다. clip(0,255) 만 하면 16-bit 는 전부 포화돼
        # 검출 실패한다(R3). 관측 최대값으로 정규화하면 대비가 늘어나 8-bit 원본과 코너가 미세하게 달라진다(실측 0.01 px).
        gf = g.astype(np.float32)
        if g.dtype == np.uint16:
            scaled = gf / 257.0
        elif np.issubdtype(g.dtype, np.floating) and gf.size and float(gf.max()) <= 1.0:
            scaled = gf * 255.0
        else:
            scaled = gf
        g = np.clip(np.rint(scaled), 0, 255).astype(np.uint8)
    found, corners = False, None
    if hasattr(cv2, "findChessboardCornersSB"):
        found, corners = cv2.findChessboardCornersSB(
            g, board.pattern_size, flags=cv2.CALIB_CB_EXHAUSTIVE | cv2.CALIB_CB_ACCURACY)
    if not found:
        found, corners = cv2.findChessboardCorners(
            g, board.pattern_size, flags=cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_NORMALIZE_IMAGE)
        if found:
            corners = cv2.cornerSubPix(g, np.asarray(corners, dtype=np.float32), (5, 5), (-1, -1),
                                       (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 60, 1e-4))
    if not found or corners is None or len(corners) != board.n_corners:
        return None
    pts = np.asarray(corners, dtype=np.float64).reshape(-1, 2)
    return _canonical_order(pts, g, board)


def _sample(g: np.ndarray, xy: np.ndarray) -> float:
    x, y = int(round(float(xy[0]))), int(round(float(xy[1])))
    h, w = g.shape[:2]
    return float(g[min(max(y, 0), h - 1), min(max(x, 0), w - 1)])


def _canonical_order(pts: np.ndarray, g: np.ndarray, board: BoardSpec) -> np.ndarray:
    """코너 순서를 패턴 기준으로 고정한다: **첫 코너 (0,0)과 (1,1) 사이의 칸이 검정**.

    OpenCV 검출기(SB / 고전)는 비대칭 보드에서 180° 뒤집힌 순서를 낼 수 있고, 두 검출기의 규약이 같다는
    보장도 없다. 좌/우·뷰 간 순서가 어긋나면 stereoCalibrate 가 조용히 틀린 R,t 를 낸다.
    칸 색은 이웃 칸과의 **상대** 비교로 판정하므로 노출에 무관하다.
    """
    c = board.cols
    sq00 = pts[[0, 1, c, c + 1]].mean(axis=0)          # 코너(0,0),(1,0),(0,1),(1,1) 가운데 = 칸 (0,0)
    sq10 = pts[[1, 2, c + 1, c + 2]].mean(axis=0)      # 이웃 칸 (1,0)
    if _sample(g, sq00) > _sample(g, sq10):            # 칸 (0,0)이 밝다 → 뒤집힌 순서
        return pts[::-1].copy()
    return pts


# ---------------------------------------------------------------------------
# 캘리브레이션
# ---------------------------------------------------------------------------

@dataclass
class CalibrateResult:
    calibration: StereoCalibration
    rms_left: float
    rms_right: float
    rms_stereo: float
    n_views: int
    n_rejected: int = 0
    rejected_reasons: list[tuple[int, str]] = field(default_factory=list)
    coverage_left: float = 0.0
    coverage_right: float = 0.0
    per_view_rms_left: list[float] = field(default_factory=list)
    per_view_rms_right: list[float] = field(default_factory=list)
    theta_max_deg_left: float = 0.0        # 관측된 코너의 최대 입사각 — 그 밖의 KB4 는 외삽이다
    theta_max_deg_right: float = 0.0

    def summary(self) -> str:
        c = self.calibration
        return (f"views {self.n_views} (rejected {self.n_rejected}) | rms L {self.rms_left:.3f} R {self.rms_right:.3f} "
                f"stereo {self.rms_stereo:.3f} px | baseline {c.baseline_mm:.3f} mm (t=({c.extrinsics.t[0]:+.1f},"
                f"{c.extrinsics.t[1]:+.1f},{c.extrinsics.t[2]:+.1f})) | fx L {c.left.fx:.1f} R {c.right.fx:.1f} | "
                f"coverage L {self.coverage_left:.0%} R {self.coverage_right:.0%} | theta max L {self.theta_max_deg_left:.0f}°"
                f" R {self.theta_max_deg_right:.0f}°")

    def save(self, path: str | Path) -> Path:
        return save_calibration(self.calibration, path)


def _coverage(points: Sequence[np.ndarray], size: tuple[int, int], grid: tuple[int, int] = (8, 6)) -> float:
    """코너들이 영상을 얼마나 고르게 덮는가: 격자 칸 중 코너가 하나라도 있는 비율."""
    if not points:
        return 0.0
    all_pts = np.concatenate([np.asarray(p).reshape(-1, 2) for p in points], axis=0)
    w, h = size
    gx = np.clip((all_pts[:, 0] / w * grid[0]).astype(int), 0, grid[0] - 1)
    gy = np.clip((all_pts[:, 1] / h * grid[1]).astype(int), 0, grid[1] - 1)
    occupied = np.zeros(grid, dtype=bool)
    occupied[gx, gy] = True
    return float(occupied.mean())


def _mono_calibrate(obj: list[np.ndarray], img: list[np.ndarray], size: tuple[int, int], flags: int | None
                    ) -> tuple[np.ndarray, np.ndarray, float, list[float]]:
    import cv2
    # OpenCV 5 Python 바인딩: imagePoints 는 (1, N, 2) 여야 한다 ((N,1,2) 는 arithm 크기 불일치로 실패 — 실측)
    objs = [np.asarray(o, dtype=np.float64).reshape(1, -1, 3) for o in obj]
    imgs = [np.asarray(p, dtype=np.float64).reshape(1, -1, 2) for p in img]
    K = np.zeros((3, 3)); D = np.zeros((4, 1))
    f = (_fisheye_flag("CALIB_RECOMPUTE_EXTRINSIC", _F_RECOMPUTE_EXTRINSIC)
         | _fisheye_flag("CALIB_FIX_SKEW", _F_FIX_SKEW)) if flags is None else int(flags)
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 300, 1e-10)
    rms, K, D, rvecs, tvecs = cv2.fisheye.calibrate(objs, imgs, tuple(size), K, D, flags=f, criteria=crit)
    per_view: list[float] = []
    for o, p, r, t in zip(objs, imgs, rvecs, tvecs):
        proj, _ = cv2.fisheye.projectPoints(o, r, t, K, D)
        per_view.append(float(np.sqrt(np.mean(np.sum((proj.reshape(-1, 2) - p.reshape(-1, 2)) ** 2, axis=1)))))
    return K, D.reshape(4), float(rms), per_view


PER_VIEW_RMS_FACTOR = 3.0     # per-view rms > max(1 px, 3×중앙값) 인 뷰는 오염으로 보고 제외한다 (R3 치명 2)
PER_VIEW_RMS_FLOOR_PX = 1.0


def _mono_calibrate_robust(obj: list[np.ndarray], img: list[np.ndarray], size: tuple[int, int], flags: int | None,
                           name: str) -> tuple[np.ndarray, np.ndarray, float, list[float], list[int], list[str]]:
    """오염된 뷰를 걸러내며 눈 하나를 푼다. 반환 (K, D, rms, per_view, kept_idx, notes).

    두 종류의 오염을 다룬다:
      1) 몇 px 밀린 뷰 — 전체 rms 는 작아 보여도 per-view rms 가 튄다 → 제외 후 재적합
      2) 수십 px 밀린 뷰 — cv2.fisheye.calibrate 가 'abs_max < threshold' 로 뷰 번호 없이 죽는다 → 하나씩 빼 보며 범인 특정
    """
    import cv2
    idx = list(range(len(obj)))
    notes: list[str] = []
    for _round in range(3):
        try:
            K, D, rms, pv = _mono_calibrate([obj[i] for i in idx], [img[i] for i in idx], size, flags)
        except cv2.error as e:
            # 크래시: 뷰를 하나씩 빼 보고 성공하는 제외 중 rms 최소를 택한다
            best = None
            for j in range(len(idx)):
                sub = idx[:j] + idx[j + 1:]
                if len(sub) < 3:
                    break
                try:
                    Kj, Dj, rj, pj = _mono_calibrate([obj[i] for i in sub], [img[i] for i in sub], size, flags)
                except cv2.error:
                    continue
                if best is None or rj < best[0]:
                    best = (rj, j)
            if best is None:
                raise CalibrateError(f"{name}: cv2.fisheye.calibrate 실패 ({str(e).splitlines()[0][:80]}) — "
                                     f"뷰 하나를 빼도 복구되지 않음") from e
            bad = idx.pop(best[1])
            notes.append(f"{name}: view {bad} rejected (calibrate crashed: {str(e).splitlines()[0][:60]})")
            continue
        med = float(np.median(pv)) if pv else 0.0
        thr = max(PER_VIEW_RMS_FLOOR_PX, PER_VIEW_RMS_FACTOR * med)
        bad = [i for i, v in zip(idx, pv) if v > thr]
        if not bad or len(idx) - len(bad) < 3:
            return K, D, rms, pv, idx, notes
        for b in bad:
            notes.append(f"{name}: view {b} rejected (per-view rms {pv[idx.index(b)]:.2f} px > {thr:.2f})")
        idx = [i for i in idx if i not in bad]
    K, D, rms, pv = _mono_calibrate([obj[i] for i in idx], [img[i] for i in idx], size, flags)
    return K, D, rms, pv, idx, notes


THETA_LOW_COVERAGE_DEG = 60.0   # 관측 θ 최대가 이 아래면 어안 주변부는 외삽 — 가장자리 뷰를 더 찍으라고 경고


def calibrate_stereo_from_points(obj_points: Sequence[np.ndarray], img_left: Sequence[np.ndarray],
                                 img_right: Sequence[np.ndarray], size: tuple[int, int], *,
                                 device: dict[str, str] | None = None, flags: int | None = None,
                                 meta: dict[str, Any] | None = None,
                                 expected_baseline_mm: float | None = None, baseline_tol: float = 0.10
                                 ) -> CalibrateResult:
    """이미 검출된 코너로 눈별 KB4 + 스테레오 외부 파라미터를 푼다. 검출과 분리돼 있어 합성 검증이 가능하다.

    R3 이후 하는 검사:
      - per-view rms 이상치·calibrate 크래시 뷰 제외(`_mono_calibrate_robust`) — 양쪽 눈 공통 부분집합으로 최종 적합
      - t.x 부호: 오른쪽 카메라는 왼쪽 좌표계의 +X 에 있으므로 p_right = R·p_left + t 의 **t.x < 0** 이어야 한다.
        t.x > 0 이면 좌/우가 바뀐 입력(lr_order 반대)이다 — rms 는 0 이라 다른 검사로는 못 잡는다
      - expected_baseline_mm 이 주어지면 |t| 와 대조 (--square-mm 오타는 rms 가 그대로라 이걸로만 잡힌다)
      - 관측 θ 최대를 기록 — 그 밖의 KB4 는 외삽이다
    """
    import cv2
    n = len(obj_points)
    if not (n == len(img_left) == len(img_right)):
        raise CalibrateError("obj/left/right 뷰 수가 다르다")
    if n < 3:
        raise CalibrateError(f"최소 3 뷰 필요 (현재 {n})")
    size = (int(size[0]), int(size[1]))
    notes: list[str] = []
    fix_flags = (_fisheye_flag("CALIB_RECOMPUTE_EXTRINSIC", _F_RECOMPUTE_EXTRINSIC)
                 | _fisheye_flag("CALIB_FIX_SKEW", _F_FIX_SKEW)
                 | _fisheye_flag("CALIB_FIX_K3", 64) | _fisheye_flag("CALIB_FIX_K4", 128))

    def _eye(obj_l: list, img_l: list, name: str):
        K, D, rms, pv, kept, nt = _mono_calibrate_robust(obj_l, img_l, size, flags, name)
        notes.extend(nt)
        try:
            intr = CameraIntrinsics(K=K, D=D, size=size)
        except CalibrationError as e1:
            # k3/k4 는 가장자리 커버리지가 부족하면 폭주해 KB4 가 화각 안에서 접힌다 → k3=k4=0 고정 재적합 (meta 에 기록)
            K, D, rms, pv, kept, nt2 = _mono_calibrate_robust(obj_l, img_l, size, fix_flags, name)
            notes.extend(nt2)
            try:
                intr = CameraIntrinsics(K=K, D=D, size=size)
            except CalibrationError as e2:
                raise CalibrateError(f"{name}: 해가 물리적으로 부적합 (뷰 커버리지·기울기 부족): {e1}; "
                                     f"k3/k4 고정 재시도도 실패: {e2}") from e2
            notes.append(f"{name}: k3/k4 fixed to 0 (free fit folded: {e1})")
        return intr, rms, pv, kept

    all_idx = list(range(n))
    obj_all, l_all, r_all = list(obj_points), list(img_left), list(img_right)
    left, rms1, pv1, keep_l = _eye(obj_all, l_all, "left")
    right, rms2, pv2, keep_r = _eye(obj_all, r_all, "right")
    keep = [i for i in all_idx if i in set(keep_l) and i in set(keep_r)]
    if len(keep) < 3:
        raise CalibrateError(f"오염 뷰 제외 후 남은 뷰 {len(keep)} < 3 — 재촬영 필요. notes: {notes}")
    if len(keep) < n:
        # 양쪽 눈이 같은 뷰 집합을 쓰도록 공통 부분집합으로 다시 푼다 (per-view 재검사 1회 더)
        obj_k = [obj_all[i] for i in keep]
        left, rms1, pv1, k2l = _eye(obj_k, [l_all[i] for i in keep], "left")
        right, rms2, pv2, k2r = _eye(obj_k, [r_all[i] for i in keep], "right")
        keep = [keep[j] for j in range(len(keep)) if j in set(k2l) and j in set(k2r)]
        if len(keep) < 3:
            raise CalibrateError(f"오염 뷰 제외 후 남은 뷰 {len(keep)} < 3 — 재촬영 필요. notes: {notes}")
    rejected_views = [i for i in all_idx if i not in keep]
    reason_by_view: dict[int, str] = {}
    for line in notes:
        mm = re.search(r"view (\d+) rejected \((.*)\)", line)
        if mm:
            reason_by_view.setdefault(int(mm.group(1)), mm.group(2))
    rejected_reasons = [(i, reason_by_view.get(i, "per-view rejection")) for i in rejected_views]

    K1, D1, K2, D2 = left.K, left.D, right.K, right.D
    objs = [np.asarray(obj_all[i], dtype=np.float64).reshape(-1, 1, 3) for i in keep]
    l = [np.asarray(l_all[i], dtype=np.float64).reshape(-1, 1, 2) for i in keep]
    r = [np.asarray(r_all[i], dtype=np.float64).reshape(-1, 1, 2) for i in keep]
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 300, 1e-10)
    out = cv2.fisheye.stereoCalibrate(objs, l, r, K1.copy(), D1.reshape(4, 1).copy(), K2.copy(),
                                      D2.reshape(4, 1).copy(), size,
                                      flags=_fisheye_flag("CALIB_FIX_INTRINSIC", _F_FIX_INTRINSIC), criteria=crit)
    rms_s, _, _, _, _, R, T = out[:7]
    T = np.asarray(T, dtype=np.float64).reshape(3)
    try:
        ext = StereoExtrinsics(R=np.asarray(R), t=T)
    except CalibrationError as e:
        raise CalibrateError(f"외부 파라미터 부적합: {e}") from e
    if T[0] > 0:
        raise CalibrateError(
            f"좌/우가 바뀐 입력으로 보인다: t = ({T[0]:+.2f}, {T[1]:+.2f}, {T[2]:+.2f}) mm 인데 오른쪽 카메라는 왼쪽의 +X 에 있어 "
            f"t.x 는 음수여야 한다. lr_order 를 뒤집어서 다시 돌려라 (rms 로는 못 잡는 오류)")
    if expected_baseline_mm is not None:
        b = float(np.linalg.norm(T))
        if abs(b - expected_baseline_mm) > baseline_tol * expected_baseline_mm:
            raise CalibrateError(
                f"베이스라인 {b:.2f} mm 가 공칭 {expected_baseline_mm:.2f} mm 와 {100 * abs(b - expected_baseline_mm) / expected_baseline_mm:.0f}% 차이 — "
                f"--square-mm(보드 칸 크기) 오기입이 가장 흔한 원인 (rms 는 그대로라 이 검사로만 잡힌다)")

    th_l = float(_theta_deg_of_pixels(np.concatenate([np.asarray(p).reshape(-1, 2) for p in l]), left).max())
    th_r = float(_theta_deg_of_pixels(np.concatenate([np.asarray(p).reshape(-1, 2) for p in r]), right).max())
    if min(th_l, th_r) < THETA_LOW_COVERAGE_DEG:
        notes.append(f"observed theta max L {th_l:.1f}° R {th_r:.1f}° < {THETA_LOW_COVERAGE_DEG:g}° — "
                     f"그 밖의 KB4 는 외삽. 보드를 영상 가장자리까지 보내 더 찍을 것")

    m: dict[str, Any] = {
        "tool": "opticmix_vision.calibrate", "model": "cv2.fisheye KB4",
        "n_views": len(keep), "n_views_input": n, "rejected_views": rejected_views,
        "rms_left_px": round(rms1, 5), "rms_right_px": round(rms2, 5),
        "rms_stereo_px": round(float(rms_s), 5),
        "coverage_left": round(_coverage(l, size), 4),
        "coverage_right": round(_coverage(r, size), 4),
        "theta_max_deg_left": round(th_l, 2), "theta_max_deg_right": round(th_r, 2),
    }
    if expected_baseline_mm is not None:
        m["expected_baseline_mm"] = float(expected_baseline_mm)
    if notes:
        m["notes"] = notes
    if meta:
        m.update(meta)
    calib = StereoCalibration(left=left, right=right, extrinsics=ext, device=dict(device or {}), meta=m)
    return CalibrateResult(calibration=calib, rms_left=rms1, rms_right=rms2, rms_stereo=float(rms_s),
                           n_views=len(keep), n_rejected=len(rejected_views), rejected_reasons=rejected_reasons,
                           coverage_left=m["coverage_left"], coverage_right=m["coverage_right"],
                           per_view_rms_left=pv1, per_view_rms_right=pv2,
                           theta_max_deg_left=m["theta_max_deg_left"], theta_max_deg_right=m["theta_max_deg_right"])


def calibrate_from_pairs(pairs: Sequence[tuple[np.ndarray, np.ndarray]], board: BoardSpec, *,
                         device: dict[str, str] | None = None, min_views: int = 8,
                         max_theta_deg: float | None = None, flags: int | None = None,
                         expected_baseline_mm: float | None = None, baseline_tol: float = 0.10) -> CalibrateResult:
    """(left, right) 영상 쌍들 → 검출 → 캘리브. 검출 실패·크기 불일치·오염 뷰는 버리되 **숫자와 사유를 남긴다**.

    기준 크기는 첫 쌍이 아니라 **다수결**이다 — 첫 쌍이 이상 크기면 정상 쌍 전부가 거부되던 결함(R3) 방지.
    """
    from collections import Counter
    if not pairs:
        raise CalibrateError("영상 쌍이 없다")
    sizes = Counter((int(a.shape[1]), int(a.shape[0])) for a, _ in pairs)
    size = sizes.most_common(1)[0][0]
    obj: list[np.ndarray] = []; L: list[np.ndarray] = []; R: list[np.ndarray] = []
    det_idx: list[int] = []                       # 검출 성공 뷰 → 원래 쌍 인덱스
    rejected: list[tuple[int, str]] = []
    for i, (a, b) in enumerate(pairs):
        if a.shape[:2] != b.shape[:2] or (a.shape[1], a.shape[0]) != size:
            rejected.append((i, f"크기 불일치 {a.shape[:2]} vs {b.shape[:2]} (기준 {size}, 다수결)")); continue
        pa = detect_corners(a, board); pb = detect_corners(b, board)
        if pa is None or pb is None:
            rejected.append((i, "왼쪽 검출 실패" if pa is None else "오른쪽 검출 실패")); continue
        obj.append(board.object_points()); L.append(pa); R.append(pb); det_idx.append(i)
    if len(obj) < min_views:
        raise CalibrateError(f"최소 {min_views} 뷰 필요, 검출 성공 {len(obj)} (거부 {len(rejected)}: "
                             f"{rejected[:3]}{'…' if len(rejected) > 3 else ''})")
    res = calibrate_stereo_from_points(obj, L, R, size, device=device, flags=flags,
                                       expected_baseline_mm=expected_baseline_mm, baseline_tol=baseline_tol,
                                       meta={"board": f"{board.cols}x{board.rows}", "square_mm": board.square_mm})
    if max_theta_deg is not None and res.theta_max_deg_left > max_theta_deg:
        raise CalibrateError(f"코너 입사각 최대 {res.theta_max_deg_left:.1f}° > {max_theta_deg}° — 보드를 가장자리에서 떼라")
    # 점 단위 거부(오염 뷰)의 인덱스를 원래 쌍 번호로 되돌려 검출 거부와 합친다
    mapped = [(det_idx[vi], why) for vi, why in res.rejected_reasons]
    res.rejected_reasons = sorted(rejected + mapped)
    res.n_rejected = len(res.rejected_reasons)
    res.calibration.meta["n_rejected"] = res.n_rejected
    return res


# ---------------------------------------------------------------------------
# 파일 입출력 + CLI
# ---------------------------------------------------------------------------

def load_pairs_dir(directory: str | Path, *, layout: str | None = None, lr_order: str = "first_is_left",
                   return_orphans: bool = False):
    """뷰어 저장 형식(`*_left.png` + `*_right.png`) 또는 layout 을 주면 합쳐진 프레임(`*.png`)을 읽는다.

    짝이 없거나 읽히지 않는 파일은 **버리지 않고 이름을 남긴다** (return_orphans=True 면 (pairs, orphans) 반환).
    "뷰어에서 10장 찍었는데 8장만 풀렸다" 를 조용히 넘기지 않기 위해서다.
    """
    import cv2
    from .device import split_stereo
    d = Path(directory)
    pairs: list[tuple[np.ndarray, np.ndarray]] = []
    orphans: list[str] = []
    lefts = sorted(d.glob("*_left.png"))
    rights = {p.name for p in d.glob("*_right.png")}
    if lefts or rights:
        for lp in lefts:
            rname = lp.name[: -len("_left.png")] + "_right.png"
            rp = lp.with_name(rname)
            if rname not in rights:
                orphans.append(lp.name); continue
            rights.discard(rname)
            a = cv2.imread(str(lp), cv2.IMREAD_GRAYSCALE); b = cv2.imread(str(rp), cv2.IMREAD_GRAYSCALE)
            if a is None or b is None:
                orphans.append(lp.name if a is None else rname); continue
            pairs.append((a, b))
        orphans.extend(sorted(rights))                        # left 없는 right
    else:
        if layout is None:
            raise CalibrateError(f"{d}: *_left.png/*_right.png 쌍이 없다. 합쳐진 프레임이면 --layout 을 지정")
        for p in sorted(d.glob("*.png")):
            img = cv2.imread(str(p), cv2.IMREAD_UNCHANGED)
            if img is None:
                orphans.append(p.name); continue
            a, b = split_stereo(img, layout, lr_order)
            pairs.append((np.ascontiguousarray(a), np.ascontiguousarray(b)))
    return (pairs, orphans) if return_orphans else pairs


def _synthetic_pairs(board: BoardSpec, n: int, seed: int = 0
                     ) -> tuple[list[tuple[np.ndarray, np.ndarray]], StereoCalibration]:
    import cv2
    size = (640, 400)
    gl = CameraIntrinsics(K=[[300.0, 0, 319.5], [0, 300.0, 199.5], [0, 0, 1]], D=[-0.02, 0.005, -0.001, 0.0002], size=size)
    gr = CameraIntrinsics(K=[[302.0, 0, 321.0], [0, 301.0, 198.0], [0, 0, 1]], D=[-0.018, 0.004, -0.0008, 0.0001], size=size)
    Ry, _ = cv2.Rodrigues(np.array([0.0, math.radians(0.5), 0.0]))
    gx = StereoExtrinsics(R=Ry, t=[-64.0, 0.2, -0.1])
    poses = synthetic_board_poses(board, gl, gr, gx, n=n, seed=seed)
    pairs = [(render_chessboard_view(board, r, t, gl), render_chessboard_view(board, r, t, gr, extrinsics=gx))
             for r, t in poses]
    truth = StereoCalibration(left=gl, right=gr, extrinsics=gx, device={"model": "SYNTH"}, meta={"truth": True})
    return pairs, truth


_PT_PER_MM = 72.0 / 25.4


def write_chessboard_pdf(path: str | Path, board: BoardSpec, *, page_mm: tuple[float, float] = (297.0, 210.0),
                         ruler_mm: float = 100.0) -> Path:
    """인쇄용 체스보드 PDF (stdlib 만으로 직접 작성 — 채우기 사각형이라 벡터·실치수).

    (cols, rows) 안쪽 코너 → 사각 (cols+1)x(rows+1), 왼쪽 위 사각이 검정. 페이지 중앙 배치.
    아래에 ruler_mm 길이 눈금 막대 하나 — 인쇄 배율 검증용(100 % 로 인쇄해도 프린터마다 1~2 % 틀어진다).
    """
    pw, ph = page_mm
    nx, ny = board.cols + 1, board.rows + 1
    bw, bh = nx * board.square_mm, ny * board.square_mm
    if bw > pw - 10 or bh > ph - 20:
        raise ValueError(f"board {bw:g}x{bh:g} mm 가 page {pw:g}x{ph:g} mm 에 안 들어간다 (--square-mm 줄이기)")
    x0, y0 = (pw - bw) / 2, (ph - bh) / 2 + 4
    mm = _PT_PER_MM
    ops = ["0 g"]
    for r in range(ny):
        for c in range(nx):
            if (r + c) % 2 == 0:
                x = x0 + c * board.square_mm
                y = y0 + (ny - 1 - r) * board.square_mm          # PDF 원점은 왼쪽 아래
                ops.append(f"{x * mm:.3f} {y * mm:.3f} {board.square_mm * mm:.3f} {board.square_mm * mm:.3f} re\nf")
    ops.append(f"{x0 * mm:.3f} {(y0 - 12) * mm:.3f} {ruler_mm * mm:.3f} {2 * mm:.3f} re\nf")
    caption = (f"Chessboard {board.cols}x{board.rows} inner corners, {board.square_mm:g} mm squares. "
               f"Print at 100% (actual size). Bar = {ruler_mm:g} mm. Measure squares, pass --square-mm.")
    ops.append(f"BT /F1 9 Tf {x0 * mm:.3f} {(y0 - 17) * mm:.3f} Td ({caption}) Tj ET")
    content = "\n".join(ops) + "\n"
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {pw * mm:.2f} {ph * mm:.2f}] "
        "/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        f"<< /Length {len(content.encode('latin-1'))} >>\nstream\n{content}endstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = "%PDF-1.4\n"
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out.encode("latin-1")))
        out += f"{i} 0 obj\n{body}\nendobj\n"
    xref = len(out.encode("latin-1"))
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n" + "".join(f"{o:010d} 00000 n \n" for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(out.encode("latin-1"))
    return path


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m opticmix_vision.calibrate", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--pairs-dir", type=Path, help="뷰어 저장 프레임 디렉토리 (*_left.png/*_right.png 또는 합쳐진 *.png)")
    src.add_argument("--synthetic", action="store_true", help="하드웨어 없이 합성 렌더로 드라이런")
    src.add_argument("--board-pdf", type=Path, metavar="PDF", help="인쇄용 체스보드 PDF 만 생성 (A4 가로, 실치수 mm)")
    p.add_argument("--layout", choices=("sbs", "tb", "plane_pack"), default=None, help="합쳐진 프레임일 때")
    p.add_argument("--lr-order", choices=("first_is_left", "first_is_right"), default="first_is_left")
    p.add_argument("--board", default="9x6", help="안쪽 코너 COLSxROWS (비대칭)")
    p.add_argument("--square-mm", type=float, default=25.0)
    p.add_argument("--min-views", type=int, default=8)
    p.add_argument("--expect-baseline-mm", type=float, default=None,
                   help="공칭 베이스라인(mm). 주면 |t| 와 ±10%% 대조 — --square-mm 오기입을 잡는 유일한 검사")
    p.add_argument("--max-theta-deg", type=float, default=None, help="코너 입사각 상한 (초광각 안전장치)")
    p.add_argument("--views", type=int, default=24, help="--synthetic 뷰 수 (실장비도 24장 이상 권장)")
    p.add_argument("--model", default="OPX-S1")
    p.add_argument("--serial", default="")
    p.add_argument("--out", type=Path, default=None, help="캘리브 JSON 출력 (--board-pdf 외엔 필수)")
    p.add_argument("--ros-camera-info-dir", type=Path, default=None,
                   help="ROS camera_info YAML 을 이 디렉토리에 같이 쓴다 (equidistant = KB4)")
    p.add_argument("--rectified-hfov-deg", type=float, default=None,
                   help="--ros-camera-info-dir 의 정류 화각 (기본 90°)")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")   # cp949 콘솔에서 '—' 같은 문자로 죽지 않게
    p = build_parser()
    a = p.parse_args(argv)
    board = BoardSpec.parse(a.board, a.square_mm)
    if a.board_pdf is not None:
        try:
            path = write_chessboard_pdf(a.board_pdf, board)
        except ValueError as e:
            print(f"[calibrate] 실패: {e}", file=sys.stderr)
            return 1
        print(f"[calibrate] wrote {path} - {board.cols}x{board.rows} inner corners, {board.square_mm:g} mm squares. "
              "100% 배율(실제 크기)로 인쇄, 눈금자 100 mm 확인, 칸 크기 실측값을 --square-mm 에 넣는다")
        return 0
    if a.out is None:
        p.error("--out 이 필요하다")
    try:
        expect_b = a.expect_baseline_mm
        if a.synthetic:
            pairs, truth = _synthetic_pairs(board, a.views)
            expect_b = expect_b if expect_b is not None else truth.baseline_mm
        else:
            pairs, orphans = load_pairs_dir(a.pairs_dir, layout=a.layout, lr_order=a.lr_order, return_orphans=True)
            truth = None
            for name in orphans:
                print(f"[calibrate] orphan (짝 없음/읽기 실패, 제외): {name}")
        print(f"[calibrate] pairs {len(pairs)} board {board.cols}x{board.rows} @ {board.square_mm} mm"
              + (f" expect baseline {expect_b:g} mm" if expect_b is not None else " (공칭 베이스라인 미지정 — --square-mm 오류 검사 없음)"))
        device = {"model": a.model}
        if a.serial:
            device["serial"] = a.serial
        res = calibrate_from_pairs(pairs, board, device=device, min_views=a.min_views, max_theta_deg=a.max_theta_deg,
                                   expected_baseline_mm=expect_b)
    except CalibrateError as e:
        print(f"[calibrate] 실패: {e}", file=sys.stderr)
        return 1
    print("[calibrate] " + res.summary())
    for i, why in res.rejected_reasons:
        print(f"[calibrate]   rejected #{i}: {why}")
    for line in res.calibration.meta.get("notes", []):
        print(f"[calibrate]   note: {line}")
    if truth is not None:
        dfx = res.calibration.left.fx - truth.left.fx
        db = res.calibration.baseline_mm - truth.baseline_mm
        print(f"[calibrate] synthetic truth check: Δfx(L) {dfx:+.3f} px, Δbaseline {db:+.3f} mm")
    path = res.save(a.out)
    print(f"[calibrate] wrote {path}")
    if a.ros_camera_info_dir is not None:
        from .rectify import DEFAULT_RECTIFIED_HFOV_DEG, stereo_rectify_maps
        from .ros import write_stereo_camera_info
        hfov = a.rectified_hfov_deg if a.rectified_hfov_deg is not None else DEFAULT_RECTIFIED_HFOV_DEG
        maps = stereo_rectify_maps(res.calibration, rectified_hfov_deg=hfov)
        prefix = (a.serial or a.model or "camera").replace(" ", "_")
        for q in write_stereo_camera_info(res.calibration, a.ros_camera_info_dir, maps=maps,
                                          name_prefix=prefix):
            print(f"[calibrate] wrote {q}")
        print(f"[calibrate]   rectified hfov {hfov:g} deg — 이 값이 바뀌면 YAML 도 다시 만들어야 한다")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
