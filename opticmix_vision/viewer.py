"""Opticmix Vision Viewer — 설치 없이 "카메라가 되는지" 5초 만에 보는 창 + 캘리브용 프레임 저장.

    python -m opticmix_vision.viewer --constants oem_uvc_constants.py          # 브링업 산출물로 열기
    python -m opticmix_vision.viewer --device 0 --layout sbs --lr-order first_is_left --width 1600 --height 800
    python -m opticmix_vision.viewer --synthetic                                # 하드웨어 없이

키:  q/ESC 종료 · s 좌/우 PNG 저장(--out) · l/L LED −/+ · e/E 노출 −/+ · g/G 게인 −/+ · a 자동노출 토글
     r rectify 토글(--calib 필요) · d 라이브 깊이(--calib 필요) · h 도움말

헤드리스 모드(--headless)는 창 없이 같은 루프를 돌린다 — 테스트·CI 용.
실장비 경로(VideoCapture)는 하드웨어 없이 검증하지 못했다. 합성 소스로 루프·키·저장만 검증됨.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np

from .depth import DepthParams, colorize_depth, depth_from_rectified

from .device import StereoFrame

__all__ = ["ViewerState", "compose_view", "run_viewer", "main", "KEY_HELP"]

KEY_HELP = ("q/ESC quit | s save | l/L LED -/+ | e/E exposure -/+ | g/G gain -/+ | "
            "a auto-exposure | r rectify | d depth(--calib) | h help")


@dataclass
class ViewerState:
    led_step: float = 8.0
    exposure_step: float = 1.0
    gain_step: float = 8.0
    rectify: bool = False
    depth: bool = False
    has_calib: bool = False
    saved: int = 0
    show_help: bool = False
    last_error: str = ""
    messages: list[str] = field(default_factory=list)

    def _bump(self, dev: Any, prop: str, delta: float) -> None:
        try:
            v = dev.get_prop(prop)
            ok = dev.set_prop(prop, v + delta)
            self.messages.append(f"{prop.replace('CAP_PROP_', '')} {v:g} -> {v + delta:g}" + ("" if ok else " (rejected)"))
        except Exception as exc:                      # 장치가 컨트롤을 거부해도 뷰어는 계속
            self.last_error = f"{prop}: {type(exc).__name__}: {exc}"
            self.messages.append(self.last_error)

    def handle_key(self, key: int, dev: Any) -> str | None:
        """키 하나 처리. 반환 'quit' | 'save' | None."""
        if key in (ord("q"), ord("Q"), 27):
            return "quit"
        if key in (ord("s"), ord("S")):
            return "save"
        if key == ord("l"):
            self._bump(dev, "CAP_PROP_BACKLIGHT", -self.led_step)
        elif key == ord("L"):
            self._bump(dev, "CAP_PROP_BACKLIGHT", +self.led_step)
        elif key == ord("e"):
            self._bump(dev, "CAP_PROP_EXPOSURE", -self.exposure_step)
        elif key == ord("E"):
            self._bump(dev, "CAP_PROP_EXPOSURE", +self.exposure_step)
        elif key == ord("g"):
            self._bump(dev, "CAP_PROP_GAIN", -self.gain_step)
        elif key == ord("G"):
            self._bump(dev, "CAP_PROP_GAIN", +self.gain_step)
        elif key in (ord("a"), ord("A")):
            try:
                cur = dev.get_prop("CAP_PROP_AUTO_EXPOSURE")
                new = 0.25 if cur >= 0.5 else 0.75          # DirectShow 관례: 0.75 auto / 0.25 manual
                dev.set_prop("CAP_PROP_AUTO_EXPOSURE", new)
                self.messages.append(f"auto-exposure {'off' if new == 0.25 else 'on'}")
            except Exception as exc:
                self.last_error = f"AUTO_EXPOSURE: {exc}"
                self.messages.append(self.last_error)
        elif key in (ord("r"), ord("R")):
            self.rectify = not self.rectify
        elif key in (ord("d"), ord("D")):
            if not self.has_calib:
                self.messages.append("depth: --calib 이 있어야 한다 (Z = f·B/d 에 캘리브가 필요)")
            else:
                self.depth = not self.depth
                self.messages.append(f"depth {'on' if self.depth else 'off'}")
        elif key in (ord("h"), ord("H")):
            self.show_help = not self.show_help
        return None


def compose_view(frame: StereoFrame, *, texts: Sequence[str] = (), gap: int = 8, scale: float = 1.0,
                 left: np.ndarray | None = None, right: np.ndarray | None = None) -> np.ndarray:
    """좌|우 나란히 (BGR). texts 는 좌상단에 줄 단위로. left/right 를 주면 그걸 그린다(rectified 표시용)."""
    import cv2
    L = frame.left if left is None else left
    R = frame.right if right is None else right
    if L.ndim == 2:
        L = cv2.cvtColor(L, cv2.COLOR_GRAY2BGR)
    if R.ndim == 2:
        R = cv2.cvtColor(R, cv2.COLOR_GRAY2BGR)
    h = max(L.shape[0], R.shape[0])
    sep = np.full((h, gap, 3), 40, dtype=np.uint8)
    canvas = np.hstack([L, sep, R])
    if scale != 1.0:
        canvas = cv2.resize(canvas, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    y = 18
    for t in texts:
        cv2.putText(canvas, t, (6, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(canvas, t, (6, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (80, 255, 80), 1, cv2.LINE_AA)
        y += 18
    return canvas


def _imwrite(path: Path, img: np.ndarray) -> None:
    """cv2.imwrite 는 비-ASCII(한글) 경로에서 False 를 돌려주고 파일을 안 만든다(Windows 실측, R3 치명 4).
    imencode → tofile 로 쓰고, 실패는 예외로 올린다 — 조용히 '저장됨' 이 되면 안 된다."""
    import cv2
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise OSError(f"PNG 인코딩 실패: {path.name}")
    buf.tofile(str(path))
    if not path.exists() or path.stat().st_size == 0:
        raise OSError(f"저장 실패 (파일이 생기지 않음): {path}")


def _save_pair(frame: StereoFrame, out_dir: Path, left: np.ndarray, right: np.ndarray) -> tuple[Path, Path]:
    if out_dir.exists() and not out_dir.is_dir():
        raise OSError(f"저장 경로가 디렉토리가 아니다: {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{time.strftime('%Y%m%d_%H%M%S')}_{frame.index:06d}"
    lp, rp = out_dir / f"{stem}_left.png", out_dir / f"{stem}_right.png"
    _imwrite(lp, left); _imwrite(rp, right)
    return lp, rp


def run_viewer(source: Any, *, headless: bool = False, max_frames: int | None = None,
               out_dir: str | Path | None = None, key_feed: Iterable[int] | None = None,
               calib: Any = None, window: str = "Opticmix Vision Viewer", scale: float = 1.0,
               depth_params: dict[str, Any] | None = None) -> dict[str, Any]:
    """루프: read → (rectify | depth) → compose → show → key.

    반환 {'frames', 'saved', 'fps', 'last_error', 'depth_valid_fraction'}.
    'd' 는 오른쪽 패널을 깊이 컬러맵으로 바꾼다(캘리브 필요) — 온보드 깊이 엔진이 없으므로 호스트 계산이다.
    """
    import cv2
    from .rectify import remap_pair, stereo_rectify_maps
    state = ViewerState(has_calib=calib is not None)
    out = Path(out_dir) if out_dir is not None else Path("captures")
    keys = iter(key_feed) if key_feed is not None else None
    _EXHAUSTED = object()
    maps = None
    dparams = DepthParams(**(depth_params or {}))
    depth_valid = float("nan")
    if calib is not None:
        maps = stereo_rectify_maps(calib)
    n = 0
    size_warned = False
    t_prev = time.perf_counter(); fps = 0.0
    try:
        while True:
            fed_key: Any = None
            if headless and keys is not None:
                fed_key = next(keys, _EXHAUSTED)
                if fed_key is _EXHAUSTED:                 # 키 시퀀스 소진 = 시나리오 끝 (무한루프 방지, R3)
                    break
            frame = source.read()
            n += 1
            now = time.perf_counter()
            dt = now - t_prev; t_prev = now
            if dt > 0:
                fps = 0.9 * fps + 0.1 * (1.0 / dt) if fps > 0 else 1.0 / dt
            L, R = frame.left, frame.right
            if (state.rectify or state.depth) and maps is not None:
                if maps.size != frame.size and not size_warned:
                    state.last_error = f"calib size {maps.size} != frame {frame.size} — rectify 결과 신뢰 불가"
                    state.messages.append(state.last_error)
                    size_warned = True
                L, R = remap_pair(L, R, maps)
            depth_text = ""
            if state.depth and maps is not None:
                try:
                    res = depth_from_rectified(L, R, maps, dparams)
                    depth_valid = res.valid_fraction
                    R = colorize_depth(res)                       # 오른쪽 패널을 깊이로 대체
                    depth_text = res.summary()
                except Exception as exc:                          # 깊이 계산 실패로 뷰어가 죽지 않게
                    state.last_error = f"depth: {type(exc).__name__}: {exc}"
                    state.messages.append(state.last_error)
                    state.depth = False
            if not headless:
                texts = [f"frame {frame.index}  fps {fps:5.1f}  {L.shape[1]}x{L.shape[0]}/eye"
                         f"{'  [rectified]' if state.rectify and maps is not None else ''}"
                         f"{'  [depth]' if state.depth else ''}"]
                if depth_text:
                    texts.append(depth_text)
                try:
                    texts.append(f"LED {source.get_prop('CAP_PROP_BACKLIGHT'):g}  exp {source.get_prop('CAP_PROP_EXPOSURE'):g}"
                                 f"  gain {source.get_prop('CAP_PROP_GAIN'):g}")
                except Exception:
                    pass
                texts += state.messages[-2:]
                if state.show_help:
                    texts.append(KEY_HELP)
                cv2.imshow(window, compose_view(frame, texts=texts, scale=scale, left=L, right=R))
                key = cv2.waitKey(1) & 0xFF
                if key == 255:
                    key = -1
            else:
                key = fed_key if keys is not None else -1
            action = state.handle_key(key, source) if key not in (-1, 255) else None
            if action == "save":
                # 저장은 캘리브 입력용 → 화면에 rectify 를 켜 두었어도 **원본**(frame.left/right)을 쓴다.
                # 정류된 영상을 저장하면 calibrate 가 조용히 틀린 K/D 를 낸다 (감사 R2 에서 실제로 잡힘).
                try:
                    lp, rp = _save_pair(frame, out, frame.left, frame.right)
                except OSError as exc:
                    state.last_error = f"저장 실패: {exc}"
                    state.messages.append(state.last_error)
                else:
                    state.saved += 1
                    state.messages.append(f"saved {lp.name}" + ("  (raw, not rectified)" if state.rectify else ""))
            elif action == "quit":
                break
            if max_frames is not None and n >= max_frames:
                break
    finally:
        if not headless:
            try:
                cv2.destroyWindow(window)
            except Exception:
                pass
    return {"frames": n, "saved": state.saved, "fps": fps, "last_error": state.last_error,
            "depth_valid_fraction": depth_valid}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m opticmix_vision.viewer", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--synthetic", action="store_true", help="하드웨어 없이 합성 소스")
    p.add_argument("--eye-size", default="320x200", help="합성 한 눈 크기 WxH")
    p.add_argument("--constants", type=Path, default=None, help="브링업 산출물 oem_uvc_constants.py")
    p.add_argument("--device", default=None, help="카메라 인덱스 (또는 경로)")
    p.add_argument("--backend", default="dshow", choices=("any", "dshow", "msmf", "v4l2", "avfoundation"))
    p.add_argument("--layout", default=None, choices=("sbs", "tb", "plane_pack"))
    p.add_argument("--lr-order", default=None, choices=("first_is_left", "first_is_right"))
    p.add_argument("--width", type=int, default=None); p.add_argument("--height", type=int, default=None)
    p.add_argument("--fps", type=float, default=None); p.add_argument("--fourcc", default=None)
    p.add_argument("--calib", type=Path, default=None, help="캘리브 JSON (r 정류 · d 깊이 표시에 필요)")
    p.add_argument("--num-disparities", type=int, default=96, help="d 깊이 탐색 폭 (16 배수, 가까이 볼수록 크게)")
    p.add_argument("--block-size", type=int, default=7, help="d 깊이 블록 크기 (홀수)")
    p.add_argument("--out", type=Path, default=Path("captures"), help="s 키 저장 폴더")
    p.add_argument("--scale", type=float, default=1.0)
    p.add_argument("--headless", action="store_true")
    p.add_argument("--frames", type=int, default=None, help="이 수만큼 읽고 종료")
    return p


def _open_source(a: argparse.Namespace) -> Any:
    if a.synthetic:
        from .synthetic import SyntheticStereoSource
        w, h = (int(v) for v in a.eye_size.lower().split("x"))
        return SyntheticStereoSource(eye_size=(w, h), fps=a.fps or 90.0, realtime=not a.headless)
    from .device import Device, DeviceConfig
    over: dict[str, Any] = {}
    for k in ("backend", "layout", "lr_order", "width", "height", "fps", "fourcc"):
        v = getattr(a, k)
        if v is not None:
            over[k] = v
    if a.device is not None:
        over["device"] = int(a.device) if str(a.device).isdigit() else a.device
    if a.constants is not None:
        cfg = DeviceConfig.from_bringup_constants(a.constants, **over)
    else:
        if "layout" not in over or "lr_order" not in over:
            raise SystemExit("--constants 가 없으면 --layout 과 --lr-order 를 지정해야 한다 (추측하지 않는다)")
        over.setdefault("device", 0)
        cfg = DeviceConfig(**over)
    return Device(cfg).open()


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")   # cp949 콘솔에서 '—' 같은 문자로 죽지 않게
    a = build_parser().parse_args(argv)
    calib = None
    if a.calib is not None:
        from .calibration import load_calibration
        calib = load_calibration(a.calib)
    from .device import DeviceError
    try:
        src = _open_source(a)
    except DeviceError as exc:
        print(f"[viewer] 카메라를 열지 못했다: {exc}", file=sys.stderr)
        return 2
    try:
        stats = run_viewer(src, headless=a.headless, max_frames=a.frames, out_dir=a.out, calib=calib,
                           scale=a.scale,
                           depth_params={"num_disparities": a.num_disparities, "block_size": a.block_size})
    except DeviceError as exc:                      # 스트리밍 중 분리·타임아웃: 트레이스백 대신 한 줄
        print(f"[viewer] 프레임 읽기 실패 (장치 분리?): {exc}", file=sys.stderr)
        return 2
    finally:
        try:
            src.close()
        except Exception:
            pass
    print(f"[viewer] frames {stats['frames']} saved {stats['saved']} fps {stats['fps']:.1f}"
          + (f" last_error: {stats['last_error']}" if stats["last_error"] else ""))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
