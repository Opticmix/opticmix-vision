"""led_level — set the IR illuminator level once and compare the image before and after.

Usage:
  python examples/led_level.py                  # full level (1.0 = 96)
  python examples/led_level.py --level 0        # off
  python examples/led_level.py --synthetic      # no camera: simulated control, generated frames

The illuminator is the UVC backlight-compensation control: off up to 16, light from 24, ceiling 96
(see IR illuminator in the README). This example uses value_range=(0, 96): --level 0.0-1.0 maps onto
it, rounded to a whole number. Measured with the earlier Python implementation; the Rust
implementation (since 0.0.3) has not yet been verified against the camera.

The readback is only what the driver accepted — it is not evidence that the light changed. The image
is. Frame mean is a weak check here: the illuminator lights part of the field of view, so a lit patch
can brighten a lot while the mean barely moves. This example prints both the mean and the share of
pixels that changed by more than 10 (of 255). The image also changes without any write (the scene
can move, and in one run the frames right after opening differed from later ones), so when the level is already the requested one the example
writes nothing and says so — the changes it prints then are not the illuminator.

Needs: the opticmix-vision wheel (camera controls run in its compiled extension).
Writes to the camera: one write (none if the illuminator is already at that level).
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

from opticmix_vision import ControlsUnavailable, Device, DeviceConfig, DeviceError, LedControl  # noqa: E402


def open_camera(synthetic: bool):
    """The Weevia S1, found by name — or, with --synthetic, generated frames (not measured data)."""
    if synthetic:
        from opticmix_vision.synthetic import SyntheticStereoSource
        return SyntheticStereoSource(realtime=True)
    return Device(DeviceConfig.weevia_s1()).open()


def average(cam, n: int) -> tuple[np.ndarray, np.ndarray]:
    """Mean image of each eye over n frames (averaging takes out sensor noise)."""
    frames = [cam.read() for _ in range(n)]
    return (np.mean([f.left for f in frames], axis=0, dtype=np.float32),
            np.mean([f.right for f in frames], axis=0, dtype=np.float32))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--synthetic", action="store_true", help="simulated control and generated frames")
    ap.add_argument("--level", type=float, default=1.0, help="0.0-1.0 of the range 0-96 (default 1.0)")
    ap.add_argument("--frames", type=int, default=10, help="frames averaged before and after (default 10)")
    ap.add_argument("--settle", type=int, default=10,
                    help="frames dropped after opening and again after the write (default 10; how long the light "
                         "takes to change has not been measured — raise this if 'after' looks unchanged)")
    a = ap.parse_args()
    if not 0.0 <= a.level <= 1.0:
        ap.error("--level is 0.0-1.0")

    try:
        with open_camera(a.synthetic) as cam:
            led = LedControl(cam, value_range=(0, 96))
            was = led.get()
            for _ in range(a.settle):                 # frames right after opening are still settling
                cam.read()
            before = average(cam, a.frames)
            led.set_level(a.level)                    # one write, waits for it; rounds to a whole number
            now = led.get()
            for _ in range(a.settle):
                cam.read()
            after = average(cam, a.frames)
    except (DeviceError, ControlsUnavailable) as exc:
        print(f"Camera error: {exc}", file=sys.stderr)
        return 2

    requested = round(96 * a.level)
    print(f"illuminator: {was:g} -> requested {requested}, readback {now:g}")
    if was == requested:
        print(f"no write (already {was:g}) — the changes below are not the illuminator")
    for eye, b, f in zip(("left", "right"), before, after):
        changed = float(np.mean(np.abs(f - b) > 10.0)) * 100.0
        print(f"{eye:5s} eye: mean {b.mean():6.1f} -> {f.mean():6.1f}   pixels changed by >10: {changed:5.1f} %")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
