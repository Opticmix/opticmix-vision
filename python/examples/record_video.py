"""record_video — record both eyes into one video file, side by side (left | right).

Usage:
  python examples/record_video.py                                   # 5 s to recording.avi (MJPG)
  python examples/record_video.py --seconds 30 --out run1.avi
  python examples/record_video.py --codec mp4v --out run1.mp4       # needs a codec your OpenCV build has
  python examples/record_video.py --synthetic --seconds 1           # no camera: generated frames

Writes two files:
  recording.avi   grayscale, left eye in the left half; the file's frame rate is the rate the camera negotiated
  recording.csv   frame, host_ns — when each frame arrived (host monotonic clock, not a camera timestamp)

The file's frame rate is only a label. If encoding is slower than the camera, frames are skipped and the
video plays too fast; the CSV is the record of real timing. Compressed video also changes pixel values —
for training data use save_stereo_pairs.py (PNG) or capture_dataset_npz.py instead.

Needs: opencv-python (installed with opticmix-vision).
Writes to the camera: none.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # runs without installing
for _stream in (sys.stdout, sys.stderr):                          # Hangul/symbols on a cp949 console
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from opticmix_vision import Device, DeviceConfig, DeviceError  # noqa: E402


def open_camera(synthetic: bool):
    """The Weevia S1, found by name — or, with --synthetic, generated frames (not measured data)."""
    if synthetic:
        from opticmix_vision.synthetic import SyntheticStereoSource
        return SyntheticStereoSource(realtime=True)
    return Device(DeviceConfig.weevia_s1()).open()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--synthetic", action="store_true", help="generated frames instead of a camera")
    ap.add_argument("--seconds", type=float, default=5.0, help="how long to record (default 5)")
    ap.add_argument("--out", type=Path, default=Path("recording.avi"))
    ap.add_argument("--codec", default="MJPG", help="four-character code (default MJPG, with a .avi file)")
    a = ap.parse_args()
    if len(a.codec) != 4:
        ap.error("--codec takes four characters, e.g. MJPG or mp4v")

    import cv2
    try:
        with open_camera(a.synthetic) as cam:
            mode = cam.mode
            print("negotiated:", mode.to_dict())
            f = cam.read()
            w, h = f.size
            fps = mode.fps if mode.fps > 0 else 30.0           # some drivers report 0
            writer = cv2.VideoWriter(str(a.out), cv2.VideoWriter_fourcc(*a.codec), fps, (2 * w, h), isColor=False)
            if not writer.isOpened():
                print(f"OpenCV could not open a video writer for codec {a.codec!r} and file {str(a.out)!r}. "
                      "Check that the extension suits the codec (MJPG with .avi, mp4v with .mp4), that your "
                      "OpenCV build has the codec, and that the folder exists and is writable.", file=sys.stderr)
                return 3
            n, first_ns = 0, f.host_ns
            with open(a.out.with_suffix(".csv"), "w", newline="", encoding="utf-8") as fh:
                rows = csv.writer(fh)
                rows.writerow(["frame", "host_ns"])
                t_end = time.perf_counter() + a.seconds
                try:
                    while True:
                        writer.write(np.hstack([f.left, f.right]))
                        rows.writerow([n, f.host_ns])
                        n += 1
                        last_ns = f.host_ns
                        if time.perf_counter() >= t_end:
                            break
                        f = cam.read()
                finally:
                    writer.release()
    except DeviceError as exc:
        print(f"Camera error: {exc}", file=sys.stderr)
        return 2
    rate = (n - 1) * 1e9 / (last_ns - first_ns) if n > 1 and last_ns > first_ns else 0.0
    print(f"wrote {n} frames ({2 * w}x{h}) to {a.out}, labelled {fps:g} fps; "
          f"frames arrived at {rate:.1f} per second (host-measured)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
