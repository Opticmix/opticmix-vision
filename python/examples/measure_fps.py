"""measure_fps — measure the frame rate you actually get, from the time each frame arrives.

Usage:
  python examples/measure_fps.py                    # 450 frames
  python examples/measure_fps.py --frames 2700      # a longer run
  python examples/measure_fps.py --synthetic        # no camera: generated frames, paced in software

Prints the mode the camera negotiated (read it rather than assuming the one you asked for), then the
intervals between frames: mean, min, max, the rate they add up to, how many came late, and how many
frames repeated the previous image byte for byte.

Times are host_ns: the host's monotonic clock right after each frame was read, not a camera timestamp,
so they include the host's scheduling jitter.

Stall check: if one eye sends a byte-identical image stall_frames times in a row (default 90),
read() raises DeviceError naming the eye — on the units we have tested one eye's stream has
occasionally stopped, and re-inserting the USB cable brought it back. This example reports it and
exits with code 2. DeviceConfig.weevia_s1(stall_frames=0) turns the check off.

Needs: opticmix-vision.
Writes to the camera: none.
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
    ap.add_argument("--frames", type=int, default=450, help="frames to time (default 450)")
    a = ap.parse_args()
    if a.frames < 2:
        ap.error("--frames needs at least 2")

    try:
        with open_camera(a.synthetic) as cam:
            print("negotiated:", cam.mode.to_dict())
            prev = cam.read()                 # the first frame carries start-up delay; time from the next one
            stamps, repeats = [], {"left": 0, "right": 0}
            for _ in range(a.frames):
                f = cam.read()
                stamps.append(f.host_ns)
                repeats["left"] += np.array_equal(f.left, prev.left)
                repeats["right"] += np.array_equal(f.right, prev.right)
                prev = f
    except DeviceError as exc:
        print(f"Camera error: {exc}", file=sys.stderr)
        return 2

    dt = np.diff(np.array(stamps, dtype=np.int64)) / 1e6      # ms between frames
    late = int(np.sum(dt > 1.5 * np.median(dt)))
    print(f"{len(stamps)} frames, {len(dt)} intervals (host-measured)")
    print(f"interval ms: mean {dt.mean():.2f}  min {dt.min():.2f}  max {dt.max():.2f}  "
          f"-> {1000.0 / dt.mean():.1f} frames per second")
    print(f"intervals over 1.5x the median: {late}")
    print(f"frames identical to the previous one: left {repeats['left']}, right {repeats['right']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
