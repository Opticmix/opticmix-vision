"""play_recording — read a video made by record_video.py back through Device, as if it were the camera.

Usage:
  python examples/play_recording.py recording.avi              # show both eyes; q or Esc quits
  python examples/play_recording.py recording.avi --headless   # no window: just count the frames

The same loop you write for the camera works on the file: `cam.read()` gives `left` and `right`.
After the last frame `read()` raises `EndOfRecording`.

`host_ns` here is when the frame was read back, not when it was captured — the capture times are in the
CSV record_video.py wrote next to the video. A compressed recording does not have the camera's pixel
values; for training data use save_stereo_pairs.py (PNG) or capture_dataset_npz.py.

Needs: opencv-python (installed with opticmix-vision).
Writes to the camera: no camera is opened.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # runs without installing
for _stream in (sys.stdout, sys.stderr):                          # Hangul/symbols on a cp949 console
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from opticmix_vision import Device, DeviceConfig, DeviceError, EndOfRecording  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("video", type=Path, help="a file written by record_video.py")
    ap.add_argument("--headless", action="store_true", help="no window; read to the end and print the count")
    a = ap.parse_args()
    n = 0
    try:
        with Device(DeviceConfig.recording(a.video)) as cam:
            print("recording:", cam.mode.to_dict())
            try:
                while True:
                    f = cam.read()
                    n += 1
                    if not a.headless:
                        import cv2
                        cv2.imshow("play_recording (left | right)", np.hstack([f.left, f.right]))
                        if cv2.waitKey(1) in (27, ord("q")):
                            break
            except EndOfRecording:
                pass
    except DeviceError as exc:
        print(f"Could not play {a.video}: {exc}", file=sys.stderr)
        return 2
    print(f"read {n} frames from {a.video}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
