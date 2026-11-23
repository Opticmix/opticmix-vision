"""set_controls_once — set manual exposure and gain once, at startup, and check what the camera kept.

Usage:
  python examples/set_controls_once.py                         # exposure -5, gain 28
  python examples/set_controls_once.py --exposure -6 --gain 40
  python examples/set_controls_once.py --synthetic             # no camera: simulated controls (made-up ranges)

Writing is risky on this camera. On the units we have tested, a control write has occasionally been
followed by one eye's stream stopping until the USB cable was re-inserted. Every write goes through
one guard in the SDK — equal values are skipped, requests close together collapse to the latest value,
writes are spaced at least 12 ms apart — but the guard does not make writes safe. So: write once, at
startup, and not in a loop. This example makes one set_many() call and does not restore the old values
afterwards (that would be another write).

Needs: the opticmix-vision wheel (camera controls run in its compiled extension).
Writes to the camera: one set_many() call — one write for auto-exposure mode + exposure (they share a
UVC property) and one for gain; a value the camera already has is skipped.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # runs without installing
for _stream in (sys.stdout, sys.stderr):                          # Hangul/symbols on a cp949 console
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from opticmix_vision import ControlsUnavailable, Device, DeviceConfig, DeviceError  # noqa: E402

NAMES = ("auto_exposure", "exposure", "gain")


def open_camera(synthetic: bool):
    """The Weevia S1, found by name — or, with --synthetic, generated frames (not measured data)."""
    if synthetic:
        from opticmix_vision.synthetic import SyntheticStereoSource
        return SyntheticStereoSource(realtime=True)
    return Device(DeviceConfig.weevia_s1()).open()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--synthetic", action="store_true", help="simulated controls instead of a camera")
    ap.add_argument("--exposure", type=int, default=-5,
                    help="driver units (log2 seconds, nominal); list_controls.py shows the range (default -5)")
    ap.add_argument("--gain", type=int, default=28, help="list_controls.py shows the range (default 28)")
    a = ap.parse_args()

    try:
        with open_camera(a.synthetic) as cam:
            c = cam.controls
            print("before:", {n: c.get(n) for n in NAMES})

            # One call. auto_exposure goes with exposure: writing exposure while auto exposure is on
            # raises ControlStateError. set_many() does not wait; wait() returns the final results.
            try:
                results = c.set_many({"auto_exposure": "manual", "exposure": a.exposure, "gain": a.gain})
            except ValueError as exc:                  # e.g. outside the device's range: nothing was sent
                print(f"Not written: {exc}", file=sys.stderr)
                return 2
            done = [r for r in results if r.status != "pending"]
            done += c.wait([r.ticket for r in results if r.status == "pending"], timeout=5)
            t_written = time.perf_counter()

            # "written" only means the driver accepted the value. readback is what the camera reports
            # afterwards and kept says whether it matches — check it before relying on the setting.
            for r in done:
                print(f"{r.name:14s} requested {r.requested!s:7s} {r.status:12s} "
                      f"readback {r.readback!s:7s} kept {r.kept}" + (f"  error: {r.error}" if r.error else ""))

            # Right after an exposure change the two eyes can disagree in brightness for a short time
            # (about 90 ms on one unit, see the README; measured with the earlier Python implementation,
            # the Rust implementation (since 0.0.3) has not yet been verified against the camera).
            # Drop those frames before using any.
            while time.perf_counter() - t_written < 0.1:
                cam.read()
            f = cam.read()
            print(f"first frame after the write: mean left {f.left.mean():.1f}, right {f.right.mean():.1f}")
    except (DeviceError, ControlsUnavailable) as exc:
        print(f"Camera error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
