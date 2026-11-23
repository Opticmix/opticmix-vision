"""list_devices — list the video devices Windows reports, and how many of them are a Weevia S1.

Usage:
  python examples/list_devices.py               # the devices connected now (Windows)
  python examples/list_devices.py --synthetic   # no camera: a made-up list with two Weevia S1

Prints every video device in enumeration order — laptop webcams included — and marks the ones whose name
contains the Weevia S1's USB name. The index is the order Windows reports, and it shifts when another
camera is plugged in or unplugged; that is why DeviceConfig.weevia_s1() finds the camera by name.

The camera reports no USB serial number, so two Weevia S1 have the same name and cannot be told apart:
with two connected, finding the camera by name stops with an error. Using two units on one computer has
not been tested — connect one at a time.

Needs: opticmix-vision. Linux: this listing is for Windows; on Linux DeviceConfig.weevia_s1() looks in
/sys/class/video4linux instead.
Writes to the camera: none.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # runs without installing
for _stream in (sys.stdout, sys.stderr):                          # Hangul/symbols on a cp949 console
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from opticmix_vision.device import WEEVIA_S1_NAME  # noqa: E402
from opticmix_vision.devices import list_devices  # noqa: E402

# A made-up device list in ffmpeg's DirectShow format — not read from any computer.
SYNTHETIC_LIST = f"""[dshow @ 0] "{WEEVIA_S1_NAME}" (video)
[dshow @ 0] "Example Laptop Webcam" (video)
[dshow @ 0] "{WEEVIA_S1_NAME}" (video)
[dshow @ 0] "Microphone (Example Audio)" (audio)
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--synthetic", action="store_true", help="a made-up device list instead of this computer's")
    a = ap.parse_args()

    devices = list_devices(_raw=SYNTHETIC_LIST if a.synthetic else None)
    weevia = [d for d in devices if WEEVIA_S1_NAME.lower() in d.name.lower()]
    print(f"{len(devices)} video device(s):")
    for d in devices:
        print(f"  [{d.index}] {d.name}{'   <- Weevia S1' if d in weevia else ''}")

    print(f"Weevia S1 ({WEEVIA_S1_NAME!r}) found: {len(weevia)}")
    if not weevia:
        print("Re-insert the cable; if the camera is missing from the list, Windows does not see it either.")
    elif len(weevia) > 1:
        print("More than one Weevia S1: the camera reports no USB serial number, so they cannot be told apart "
              "by name, and DeviceConfig.weevia_s1() stops with an error instead of guessing. "
              "Using two units on one computer has not been tested — connect one at a time.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
