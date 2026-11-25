"""measure_fps — measure the frame rate you actually get, from the time each frame arrives.

Usage:
  python examples/measure_fps.py                    # 450 frames
  python examples/measure_fps.py --frames 2700      # a longer run
  python examples/measure_fps.py --synthetic        # no camera: generated frames, paced in software
  python examples/measure_fps.py --backend msmf     # Windows: the path with driver time stamps; the Weevia S1
                                                    # is found by name in the Media Foundation list
                                                    # (--device N names the index yourself)

Prints the mode the camera negotiated (read it rather than assuming the one you asked for), then the
intervals between frames: mean, min, max, the rate they add up to, how many came late, and how many
frames repeated the previous image byte for byte (per eye, and Device.repeated_frames: both eyes).

Times are host_ns: the host's monotonic clock right after each frame was read, not a camera timestamp,
so they include the host's scheduling jitter. Where the capture driver's own time stamp comes through OpenCV
(StereoFrame.driver_ns: Media Foundation sample time with --backend msmf on Windows, V4L2 buffer time on
Linux; none with DirectShow, the Weevia S1 default on Windows), the same interval figures are printed for it,
the first timed frame's driver time, and how much later than the
earliest one each frame reached this process (host_ns - driver_ns, less its smallest value: the two clocks
have different zero points). These are operating-system driver times, not a
camera clock; whether they mark the exposure or the arrival of a frame is not known.
On one PC (Windows 11) with one camera (2026-11-24, once) the first Media Foundation time was about 364,400 s —
not the start of streaming; what the clock's zero is has not been established. Use differences between frames,
not the absolute value. With --backend msmf, OpenCV has Media Foundation convert the camera's format to RGB first:
the time is that converted sample's, and the mode's fourcc is OpenCV's output format code, not the camera's
format. Run with the camera once (one PC, one camera, 2026-11-24): through MSMF
276 of 900 frames per eye repeated the previous image while the driver-time intervals were 11.11 ms (mean,
min and max), so there driver_ns does not show whether a new image arrived (cause not established);
DirectShow gave 0 of 900. See StereoFrame in API.md. V4L2 driver times: not run with the camera.
The Device.repeated_frames line came later; run with the camera once (one PC, one camera, 2026-11-25; this
example's output does not record the OpenCV version): through MSMF 271 of 900, with the repeated-images warning
once; through DirectShow 0 of 900, no warning.

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


def open_camera(synthetic: bool, backend: str | None = None, device: int | None = None):
    """The Weevia S1, found by name (or at --device) — or, with --synthetic, generated frames (not measured data)."""
    if synthetic:
        from opticmix_vision.synthetic import SyntheticStereoSource
        return SyntheticStereoSource(realtime=True)
    given = {k: v for k, v in (("backend", backend), ("device", device)) if v is not None}
    return Device(DeviceConfig.weevia_s1(**given)).open()


def intervals(stamps: np.ndarray) -> tuple[np.ndarray, int]:
    """Intervals between stamps (ns) in ms, and how many are over 1.5x the median."""
    dt = np.diff(stamps) / 1e6
    return dt, int(np.sum(dt > 1.5 * np.median(dt)))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--synthetic", action="store_true", help="generated frames instead of a camera")
    ap.add_argument("--frames", type=int, default=450, help="frames to time (default 450)")
    ap.add_argument("--backend", choices=("dshow", "msmf", "v4l2"),
                    help="OpenCV capture backend (default: the Weevia S1 default for this OS)")
    ap.add_argument("--device", type=int,
                    help="camera index in that backend's list, instead of finding the Weevia S1 by name")
    a = ap.parse_args()
    if a.frames < 2:
        ap.error("--frames needs at least 2")

    try:
        with open_camera(a.synthetic, a.backend, a.device) as cam:
            print("negotiated:", cam.mode.to_dict())
            backend = cam.mode.backend_name or "unknown"   # read while open: a closed Device has no mode
            prev = cam.read()                 # the first frame carries start-up delay; time from the next one
            stamps, repeats, drivers = [], {"left": 0, "right": 0}, []
            for _ in range(a.frames):
                f = cam.read()
                stamps.append(f.host_ns)
                drivers.append((f.driver_ns, f.driver_clock))      # numbers only, not the images
                repeats["left"] += np.array_equal(f.left, prev.left)
                repeats["right"] += np.array_equal(f.right, prev.right)
                prev = f
            both = getattr(cam, "repeated_frames", None)  # Device's own count; generated frames have none
    except DeviceError as exc:
        print(f"Camera error: {exc}", file=sys.stderr)
        return 2

    host = np.array(stamps, dtype=np.int64)
    dt, late = intervals(host)                                 # ms between frames
    print(f"{len(stamps)} frames, {len(dt)} intervals (host-measured)")
    print(f"interval ms: mean {dt.mean():.2f}  min {dt.min():.2f}  max {dt.max():.2f}  "
          f"-> {1000.0 / dt.mean():.1f} frames per second")
    print(f"intervals over 1.5x the median: {late}")
    print(f"frames identical to the previous one: left {repeats['left']}, right {repeats['right']}")
    if both is not None:
        print(f"frames identical in both eyes (Device.repeated_frames): {both}")

    clocks = {c for _, c in drivers}
    if len(clocks) == 1 and None not in clocks and all(ns is not None for ns, _ in drivers):
        driver = np.array([ns for ns, _ in drivers], dtype=np.int64)
        ddt, dlate = intervals(driver)
        print(f"driver time stamps ({clocks.pop()}): interval ms: mean {ddt.mean():.2f}  min {ddt.min():.2f}  "
              f"max {ddt.max():.2f}")
        print(f"driver time stamps: intervals over 1.5x the median: {dlate}")
        print(f"driver time stamps: first timed frame at {driver[0] / 1e9:.6f} s on that clock")
        delay = (host - driver) - (host - driver).min()
        print(f"host_ns - driver_ns, above its smallest value, ms: median {np.median(delay) / 1e6:.2f}  "
              f"95th percentile {np.percentile(delay, 95) / 1e6:.2f}  max {delay.max() / 1e6:.2f}")
    else:
        hint = " — on Windows use --backend msmf" if backend.upper() == "DSHOW" else ""
        print(f"driver time stamps: none from this capture backend ({backend}){hint}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
