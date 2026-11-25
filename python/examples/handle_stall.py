"""handle_stall — when read() raises DeviceError: close the camera, ask for the USB cable to be re-inserted,
wait, open it again; give up after N tries.

Usage:
  python examples/handle_stall.py                       # read 900 frames, up to 3 reopens
  python examples/handle_stall.py --retries 5 --wait 10
  python examples/handle_stall.py --synthetic           # no camera: a fake capture where one eye stops

read() raises DeviceError when a frame cannot be read (timeout or camera unplugged) and when one eye has
sent the byte-identical image stall_frames times in a row (default 90) — on the units we have tested one
eye's stream has occasionally stopped, and re-inserting the USB cable brought it back. Opening the camera
can raise DeviceError too, for example "Weevia S1 ... not found" while the cable is out.

On each error this example prints the message, closes the camera, asks you to re-insert the USB cable,
waits --wait seconds and opens a new Device. After --retries failed reopens it stops with exit code 2.
Every attempt opens a new Device here. Device.open() also starts the stall count afresh, so opening the
same Device again judges the new stream on its own frames, not on the count of the stream before.

--synthetic uses a fake VideoCapture (not a camera) where one eye repeats one image after 10 frames on
the first open only, so the error and the reopen run without hardware.

Needs: opticmix-vision.
Writes to the camera: none.
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

from opticmix_vision import Device, DeviceConfig, DeviceError  # noqa: E402


class FakeCapture:
    """Stands in for cv2.VideoCapture (isOpened/get/set/read/release) — generated frames, not a camera.

    After stall_after frames the first half of the frame stops changing (stall_after=None: never)."""

    def __init__(self, stall_after: int | None, size: tuple[int, int] = (320, 100)) -> None:
        self.stall_after, self.size, self.n, self.props = stall_after, size, 0, {}
        self.rng = np.random.default_rng(0)
        self.frozen = None

    def isOpened(self) -> bool:
        return True

    def get(self, prop: int) -> float:
        return float(self.props.get(prop, 0.0))

    def set(self, prop: int, value: float) -> bool:
        self.props[prop] = value
        return True

    def read(self):
        w, h = self.size
        img = self.rng.integers(0, 256, (h, w), dtype=np.uint8)
        if self.stall_after is not None and self.n >= self.stall_after:
            if self.frozen is None:
                self.frozen = img[:, : w // 2].copy()
            img[:, : w // 2] = self.frozen
        self.n += 1
        return True, img

    def release(self) -> None:
        pass


def open_camera(synthetic: bool, attempt: int) -> Device:
    """A new Device each attempt: the Weevia S1 found by name — or, with --synthetic, the fake capture."""
    if synthetic:
        fake = FakeCapture(stall_after=10 if attempt == 0 else None)
        return Device(DeviceConfig.weevia_s1(device=0), capture=fake).open()
    return Device(DeviceConfig.weevia_s1()).open()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--synthetic", action="store_true", help="a fake capture instead of a camera")
    ap.add_argument("--frames", type=int, default=900, help="frames to read in total (default 900)")
    ap.add_argument("--retries", type=int, default=3, help="reopens before giving up (default 3)")
    ap.add_argument("--wait", type=float, default=10.0, help="seconds between closing and reopening (default 10)")
    a = ap.parse_args()
    if a.frames < 1 or a.retries < 0 or a.wait < 0:
        ap.error("--frames needs at least 1; --retries and --wait cannot be negative")

    got = reopens = 0
    while got < a.frames:
        try:
            with open_camera(a.synthetic, reopens) as cam:
                while got < a.frames:
                    cam.read()                     # your processing goes here
                    got += 1
        except DeviceError as exc:                 # leaving the with-block closed the camera
            print(f"Camera error after {got} frames: {exc}", file=sys.stderr)
            if reopens >= a.retries:
                print(f"Gave up after {reopens} reopen(s). Re-insert the USB cable and run again.", file=sys.stderr)
                return 2
            reopens += 1
            print(f"Camera closed. If an eye stopped or the camera is gone, re-insert the USB cable now: unplug "
                  f"it and plug it back in. Reopening in {a.wait:g} s (reopen {reopens} of {a.retries}).")
            time.sleep(a.wait)

    print(f"read {got} frames, {reopens} reopen(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
