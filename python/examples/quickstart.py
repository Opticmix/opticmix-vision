"""quickstart — open the Weevia S1, split the two eyes, show them side by side.

Usage:
  python examples/quickstart.py                 # Weevia S1, found by name, 1600x800 MJPG 90 fps
  python examples/quickstart.py --no-window     # no window: read 100 frames and print the frame rate

  # another camera, or settings of your own (layout and lr_order have no defaults then)
  python examples/quickstart.py --device 1 --layout sbs --lr-order first_is_right \
                                --width 1600 --height 800 --fps 90 --fourcc MJPG

Keys: q quit, s save the current left/right PNG.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # runs without installing
for _stream in (sys.stdout, sys.stderr):                          # Hangul/symbols on a cp949 console
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from opticmix_vision import LAYOUTS, LR_ORDERS, Device, DeviceConfig, DeviceError  # noqa: E402


def parse() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--device", help="camera index or path (default: find the Weevia S1 by name)")
    ap.add_argument("--backend", default="dshow", choices=["any", "dshow", "msmf"])
    ap.add_argument("--layout", choices=list(LAYOUTS))
    ap.add_argument("--lr-order", choices=list(LR_ORDERS))
    ap.add_argument("--width", type=int)
    ap.add_argument("--height", type=int)
    ap.add_argument("--fps", type=float)
    ap.add_argument("--fourcc")
    ap.add_argument("--no-window", action="store_true", help="read 100 frames and print the frame rate")
    return ap.parse_args()


def build_config(a: argparse.Namespace) -> DeviceConfig:
    mode = {k: v for k, v in dict(width=a.width, height=a.height, fps=a.fps, fourcc=a.fourcc).items() if v}
    if a.device is None:          # the Weevia S1, found by name; any flag given overrides its measured value
        over = {k: v for k, v in dict(layout=a.layout, lr_order=a.lr_order).items() if v}
        return DeviceConfig.weevia_s1(**over, **mode)
    if not (a.layout and a.lr_order):
        sys.exit("With --device, give --layout and --lr-order too: the SDK does not guess how the eyes are packed.")
    dev: int | str = int(a.device) if str(a.device).isdigit() else str(a.device)
    return DeviceConfig(layout=a.layout, lr_order=a.lr_order, device=dev, backend=a.backend, **mode)


def main() -> int:
    a = parse()
    try:
        cfg = build_config(a)
        cam = Device(cfg).open()
    except DeviceError as exc:
        print(f"Could not open the camera: {exc}", file=sys.stderr)
        return 2
    with cam:
        print("negotiated:", cam.mode.to_dict())
        if a.no_window:
            t0 = time.perf_counter()
            n = 100
            for _ in range(n):
                cam.read()
            dt = time.perf_counter() - t0
            print(f"{n} frames in {dt:.3f} s -> {n / dt:.1f} fps (host-measured)")
            return 0

        import cv2
        n, t0 = 0, time.perf_counter()
        while True:
            f = cam.read()
            n += 1
            if n % 30 == 0:
                fps = n / (time.perf_counter() - t0)
                cv2.setWindowTitle("opticmix_vision quickstart", f"L | R  {f.size[0]}x{f.size[1]}  {fps:.1f} fps")
            cv2.imshow("opticmix_vision quickstart", np.hstack([f.left, f.right]))
            k = cv2.waitKey(1) & 0xFF
            if k == ord("q"):
                break
            if k == ord("s"):
                for eye, img in (("left", f.left), ("right", f.right)):   # imwrite fails silently on Hangul paths
                    cv2.imencode(".png", img)[1].tofile(f"{eye}_{f.index:06d}.png")
                print(f"saved left/right_{f.index:06d}.png")
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
