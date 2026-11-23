"""live_preview — show both eyes side by side, with the frame rate this loop measures.

Usage:
  python examples/live_preview.py                                  # Weevia S1, found by name
  python examples/live_preview.py --synthetic                      # no camera: generated frames
  python examples/live_preview.py --synthetic --headless --frames 60   # no window

Needs: opencv-python (installed with opticmix-vision). Keys: q quit.
Writes to the camera: none.
"""

from __future__ import annotations

import argparse
import sys
from collections import deque
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # runs without installing
for _stream in (sys.stdout, sys.stderr):                          # Hangul/symbols on a cp949 console
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from opticmix_vision import Device, DeviceConfig, DeviceError  # noqa: E402

WINDOW = "opticmix_vision live preview"


def open_camera(synthetic: bool):
    """The Weevia S1, found by name — or, with --synthetic, generated frames (not measured data)."""
    if synthetic:
        from opticmix_vision.synthetic import SyntheticStereoSource
        return SyntheticStereoSource(realtime=True)
    return Device(DeviceConfig.weevia_s1()).open()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--synthetic", action="store_true", help="generated frames instead of a camera")
    ap.add_argument("--headless", action="store_true", help="no window (use with --frames)")
    ap.add_argument("--frames", type=int, help="stop after this many frames")
    a = ap.parse_args()

    import cv2
    try:
        with open_camera(a.synthetic) as cam:
            print("negotiated:", cam.mode.to_dict())      # what the driver gave, not what was asked for
            stamps: deque[int] = deque(maxlen=31)         # host time of the last 31 frames = 30 intervals
            n, fps = 0, 0.0
            while a.frames is None or n < a.frames:
                f = cam.read()
                n += 1
                stamps.append(f.host_ns)
                if len(stamps) > 1:
                    # The rate frames reach this loop. Drawing the window can make it lower than the camera's.
                    fps = (len(stamps) - 1) * 1e9 / (stamps[-1] - stamps[0])
                if a.headless:
                    continue
                view = cv2.cvtColor(np.hstack([f.left, f.right]), cv2.COLOR_GRAY2BGR)
                w, h = f.size
                cv2.putText(view, f"L | R   {w}x{h} per eye   {fps:5.1f} fps", (10, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
                cv2.imshow(WINDOW, view)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
            print(f"{n} frames, {fps:.1f} fps over the last {len(stamps) - 1} intervals (host-measured)")
    except DeviceError as exc:
        print(f"Camera error: {exc}", file=sys.stderr)
        return 2
    finally:
        if not a.headless:
            cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
