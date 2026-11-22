"""Weevia S1 Viewer — 설치 없이 "카메라가 되는지" 5초 만에 보는 창 + 좌/우 프레임 저장.

    python -m opticmix_vision.viewer                                            # Weevia S1 (이름으로 찾음, 1600x800 MJPG 90)
    python -m opticmix_vision.viewer --device 1 --layout sbs --lr-order first_is_right --width 1600 --height 800 --fps 90 --fourcc MJPG
    python -m opticmix_vision.viewer --synthetic                                # 하드웨어 없이

키:  q/ESC 종료 · s 좌/우 PNG 저장(--out) · l/L LED −/+ · e/E 노출 −/+ · g/G 게인 −/+ · a 자동노출 토글 · h 도움말

헤드리스 모드(--headless)는 창 없이 같은 루프를 돌린다 — 테스트·CI 용.
실장비 경로(VideoCapture)는 하드웨어 없이 검증하지 못했다. 합성 소스로 루프·키·저장만 검증됨.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import numpy as np


from .controls import ControlStateError
from .device import StereoFrame

__all__ = ["ViewerState", "compose_view", "run_viewer", "main", "KEY_HELP"]

# 키를 누르는 동안은 목표값만 쌓고, 이만큼 조용하면 한 번 쓴다 — 쓰기 간격이 12 ms 라(2026-09-28) 키 반복(초당 ~30회)이
# 그대로 쓰기가 되지 않게. Windows 키 반복 시작 지연(기본 약 0.5 s)보다 길어야 누르고 있는 동안 중간에 쓰지 않는다.
SETTLE_S = 0.6

KEY_HELP = "q/ESC quit | s save | l/L LED -/+ | e/E exposure -/+ | g/G gain -/+ | a auto-exposure | h help"


@dataclass
class ViewerState:
    led_step: float = 8.0
    exposure_step: float = 1.0
    gain_step: float = 8.0
    saved: int = 0
    show_help: bool = False
    key_help: str = KEY_HELP
    last_error: str = ""
    messages: list[str] = field(default_factory=list)
    _pending: dict[str, int] = field(default_factory=dict)
    _pending_at: float = 0.0

    def _bump(self, dev: Any, prop: str, delta: float) -> None:
        try:
            v = dev.get_prop(prop)
            ok = dev.set_prop(prop, v + delta)
            self.messages.append(f"{prop.replace('CAP_PROP_', '')} {v:g} -> {v + delta:g}" + ("" if ok else " (rejected)"))
        except Exception as exc:                      # 장치가 컨트롤을 거부해도 뷰어는 계속
            self.last_error = f"{prop}: {type(exc).__name__}: {exc}"
            self.messages.append(self.last_error)

    def _nudge(self, dev: Any, name: str, delta: float, legacy_prop: str, now: float | None = None) -> None:
        """제어 하나를 delta 만큼 — 목표값만 쌓는다. 쓰기는 키가 멈춘 뒤 commit() 이 한 번."""
        controls = getattr(dev, "controls", None)
        if controls is None:
            self._bump(dev, legacy_prop, delta)
            return
        try:
            if name == "exposure" and controls.get("auto_exposure") == "auto":
                self.messages.append("auto-exposure is on — press a to switch to manual first")
                return
            base = self._pending.get(name)
            if base is None:
                base = controls.get(name)
                if base is None:
                    raise ValueError(f"{name} is not readable")
            target = int(round(base + delta))
            self._pending[name] = target
            self._pending_at = time.monotonic() if now is None else now
            self.messages.append(f"{name} {base:g} -> {target:g} (writes when you stop)")
        except Exception as exc:                      # 장치가 컨트롤을 거부해도 뷰어는 계속
            self.last_error = f"{name}: {type(exc).__name__}: {exc}"
            self.messages.append(self.last_error)

    def commit(self, dev: Any, now: float | None = None, *, force: bool = False) -> None:
        """쌓인 목표값을 한 번 쓴다 — 마지막 키 뒤 SETTLE_S 만큼 조용했거나 force 일 때."""
        if not self._pending:
            return
        t = time.monotonic() if now is None else now
        if not force and t - self._pending_at < SETTLE_S:
            return
        pending, self._pending = self._pending, {}
        controls = getattr(dev, "controls", None)
        for name, target in pending.items():
            try:
                r = controls.set(name, target)
                if r.status == "skipped_same":
                    self.messages.append(f"{name} {target:g} (no change)")
            except ControlStateError:
                self.messages.append("auto-exposure is on — press a to switch to manual first")
            except Exception as exc:                  # 장치가 컨트롤을 거부해도 뷰어는 계속
                self.last_error = f"{name}: {type(exc).__name__}: {exc}"
                self.messages.append(self.last_error)

    def handle_key(self, key: int, dev: Any, now: float | None = None) -> str | None:
        """키 하나 처리. 반환 'quit' | 'save' | None. 끝낼 때 기다리던 변경은 쓰고 나간다."""
        if key in (ord("q"), ord("Q"), 27):
            self.commit(dev, now, force=True)
            return "quit"
        if key in (ord("s"), ord("S")):
            return "save"
        if key == ord("l"):
            self._nudge(dev, "backlight_compensation", -self.led_step, "CAP_PROP_BACKLIGHT", now)
        elif key == ord("L"):
            self._nudge(dev, "backlight_compensation", +self.led_step, "CAP_PROP_BACKLIGHT", now)
        elif key == ord("e"):
            self._nudge(dev, "exposure", -self.exposure_step, "CAP_PROP_EXPOSURE", now)
        elif key == ord("E"):
            self._nudge(dev, "exposure", +self.exposure_step, "CAP_PROP_EXPOSURE", now)
        elif key == ord("g"):
            self._nudge(dev, "gain", -self.gain_step, "CAP_PROP_GAIN", now)
        elif key == ord("G"):
            self._nudge(dev, "gain", +self.gain_step, "CAP_PROP_GAIN", now)
        elif key in (ord("a"), ord("A")):
            try:
                controls = getattr(dev, "controls", None)
                if controls is not None:
                    new = "manual" if controls.get("auto_exposure") == "auto" else "auto"
                    controls.set("auto_exposure", new)
                    self.messages.append(f"auto-exposure {'on' if new == 'auto' else 'off'}")
                else:
                    cur = dev.get_prop("CAP_PROP_AUTO_EXPOSURE")
                    new_n = 0.25 if cur >= 0.5 else 0.75          # DirectShow 관례: 0.75 auto / 0.25 manual
                    dev.set_prop("CAP_PROP_AUTO_EXPOSURE", new_n)
                    self.messages.append(f"auto-exposure {'off' if new_n == 0.25 else 'on'}")
            except Exception as exc:
                self.last_error = f"AUTO_EXPOSURE: {exc}"
                self.messages.append(self.last_error)
        elif key in (ord("h"), ord("H")):
            self.show_help = not self.show_help
        return None


def compose_view(frame: StereoFrame, *, texts: Sequence[str] = (), gap: int = 8, scale: float = 1.0,
                 left: np.ndarray | None = None, right: np.ndarray | None = None) -> np.ndarray:
    """좌|우 나란히 (BGR). texts 는 좌상단에 줄 단위로. left/right 를 주면 그걸 그린다(view 가 바꾼 영상)."""
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
    """cv2.imwrite 는 비-ASCII(한글) 경로에서 False 를 돌려주고 파일을 안 만든다(Windows 실측).
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


View = Callable[[StereoFrame, "ViewerState"], "tuple[np.ndarray, np.ndarray, list[str]]"]


def run_viewer(source: Any, *, headless: bool = False, max_frames: int | None = None,
               out_dir: str | Path | None = None, key_feed: Iterable[int] | None = None,
               window: str = "Weevia S1 Viewer", scale: float = 1.0,
               state: ViewerState | None = None, view: View | None = None) -> dict[str, Any]:
    """루프: read → view → compose → show → key.

    state: 키 처리 상태(기본 ViewerState()). view: 프레임 → (왼쪽, 오른쪽, 덧붙일 글줄) — 화면에 그릴 것만 바꾼다.
    's' 저장은 view 와 상관없이 언제나 받은 그대로의 프레임(frame.left/right)이다.
    반환 {'frames', 'saved', 'fps', 'last_error'}.
    """
    import cv2
    state = ViewerState() if state is None else state
    out = Path(out_dir) if out_dir is not None else Path("captures")
    keys = iter(key_feed) if key_feed is not None else None
    _EXHAUSTED = object()
    n = 0
    t_prev = time.perf_counter(); fps = 0.0; dt_avg = 0.0
    try:
        while True:
            fed_key: Any = None
            if headless and keys is not None:
                fed_key = next(keys, _EXHAUSTED)
                if fed_key is _EXHAUSTED:                 # 키 시퀀스 소진 = 시나리오 끝 (무한루프 방지)
                    break
            frame = source.read()
            n += 1
            now = time.perf_counter()
            dt = now - t_prev; t_prev = now
            if dt > 0:                                    # 간격을 평균하고 역수 — 1/dt 평균은 흔들림에 부푼다
                dt_avg = 0.9 * dt_avg + 0.1 * dt if dt_avg > 0 else dt
                fps = 1.0 / dt_avg
            L, R, extra = (frame.left, frame.right, []) if view is None else view(frame, state)
            if not headless:
                texts = [f"frame {frame.index}  fps {fps:5.1f}  {L.shape[1]}x{L.shape[0]}/eye", *extra]
                try:
                    texts.append(f"LED {source.get_prop('CAP_PROP_BACKLIGHT'):g}  exp {source.get_prop('CAP_PROP_EXPOSURE'):g}"
                                 f"  gain {source.get_prop('CAP_PROP_GAIN'):g}")
                except Exception:
                    pass
                texts += state.messages[-2:]
                if state.show_help:
                    texts.append(state.key_help)
                cv2.imshow(window, compose_view(frame, texts=texts, scale=scale, left=L, right=R))
                key = cv2.waitKey(1) & 0xFF
                if key == 255:
                    key = -1
            else:
                key = fed_key if keys is not None else -1
            action = state.handle_key(key, source) if key not in (-1, 255) else None
            state.commit(source)                                  # 키가 멈춘 지 SETTLE_S 가 지났으면 한 번 쓴다
            if action == "save":
                # 화면을 view 로 바꿔 두었어도 저장은 **받은 그대로**(frame.left/right)다 — 바뀐 영상이 저장되면
                # 그 파일을 입력으로 쓰는 쪽이 조용히 틀린 결과를 낸다(실제로 있었던 일).
                try:
                    lp, rp = _save_pair(frame, out, frame.left, frame.right)
                except OSError as exc:
                    state.last_error = f"저장 실패: {exc}"
                    state.messages.append(state.last_error)
                else:
                    state.saved += 1
                    state.messages.append(f"saved {lp.name}" + ("  (as captured)" if view is not None else ""))
            elif action == "quit":
                break
            if max_frames is not None and n >= max_frames:
                state.commit(source, force=True)
                break
    finally:
        if not headless:
            try:
                cv2.destroyWindow(window)
            except Exception:
                pass
    return {"frames": n, "saved": state.saved, "fps": fps, "last_error": state.last_error}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m opticmix_vision.viewer", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--synthetic", action="store_true", help="하드웨어 없이 합성 소스")
    p.add_argument("--eye-size", default="320x200", help="합성 한 눈 크기 WxH")
    p.add_argument("--constants", type=Path, default=None, help=argparse.SUPPRESS)   # 내부 브링업 산출물
    p.add_argument("--device", default=None, help="카메라 인덱스 (또는 경로)")
    p.add_argument("--backend", default="dshow", choices=("any", "dshow", "msmf"))
    p.add_argument("--layout", default=None, choices=("sbs", "tb", "plane_pack"))
    p.add_argument("--lr-order", default=None, choices=("first_is_left", "first_is_right"))
    p.add_argument("--width", type=int, default=None); p.add_argument("--height", type=int, default=None)
    p.add_argument("--fps", type=float, default=None); p.add_argument("--fourcc", default=None)
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
    elif a.device is None:
        over.pop("backend", None)
        cfg = DeviceConfig.weevia_s1(**over)          # 이름으로 찾은 Weevia S1, 실측값 — 준 인자만 덮어쓴다
    else:
        if "layout" not in over or "lr_order" not in over:
            raise SystemExit("--device 를 주면 --layout 과 --lr-order 도 지정해야 한다 (추측하지 않는다)")
        cfg = DeviceConfig(**over)
    return Device(cfg).open()


def serve(a: argparse.Namespace, run: Callable[[Any], dict[str, Any]]) -> int:
    """인자로 카메라(또는 합성 소스)를 열고 run(source) 을 돌린 뒤 닫는다. 실패는 트레이스백 대신 한 줄."""
    from .device import DeviceError
    try:
        src = _open_source(a)
    except DeviceError as exc:
        print(f"[viewer] 카메라를 열지 못했다: {exc}", file=sys.stderr)
        return 2
    try:
        stats = run(src)
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


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")   # cp949 콘솔에서 '—' 같은 문자로 죽지 않게
    a = build_parser().parse_args(argv)
    return serve(a, lambda src: run_viewer(src, headless=a.headless, max_frames=a.frames, out_dir=a.out,
                                           scale=a.scale))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
