"""list_devices — list the video devices Windows reports, and how many of them are a Weevia S1.

Usage:
  python examples/list_devices.py                             # DirectShow's list (the default)
  python examples/list_devices.py --backend msmf              # Media Foundation's list
  python examples/list_devices.py --synthetic                 # no camera: a made-up list with two Weevia S1
  python examples/list_devices.py --synthetic --backend msmf  # no camera: the same made-up devices, another order

Prints every video device in the order of one capture backend's list — laptop webcams included — and marks the
ones whose name contains the Weevia S1's USB name. The index is the one OpenCV opens with that backend:
cv2.VideoCapture(index, cv2.CAP_DSHOW) for DirectShow, cv2.CAP_MSMF for Media Foundation. The two lists can be
in a different order: on one PC with one camera (2026-11-24), devices.list_devices(backend="msmf") and
list_devices() returned the same three cameras in a different order, once. So an index from one list does not
go with the other backend. An index also shifts when another camera is plugged in or unplugged; that is why
DeviceConfig.weevia_s1() finds the camera by name, in the list of the backend it opens with.

The camera reports no USB serial number, so two Weevia S1 have the same name and cannot be told apart:
with two connected, finding the camera by name stops with an error. Using two units on one computer has
not been tested — connect one at a time.

examples/list_devices.py --backend msmf and the doctor's Media Foundation list have been run without a camera
only (made-up lists); they have not been run with the camera.
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
from opticmix_vision.devices import DeviceInfo, list_devices  # noqa: E402

# A made-up device list in ffmpeg's DirectShow format — not read from any computer.
SYNTHETIC_LIST = f"""[dshow @ 0] "{WEEVIA_S1_NAME}" (video)
[dshow @ 0] "Example Laptop Webcam" (video)
[dshow @ 0] "{WEEVIA_S1_NAME}" (video)
[dshow @ 0] "Microphone (Example Audio)" (audio)
"""
# The same made-up video devices as Media Foundation might list them: in another order.
SYNTHETIC_MF_LIST = ["Example Laptop Webcam", WEEVIA_S1_NAME, WEEVIA_S1_NAME]
LIST_NAME = {"dshow": "DirectShow", "msmf": "Media Foundation"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--synthetic", action="store_true", help="a made-up device list instead of this computer's")
    ap.add_argument("--backend", choices=("dshow", "msmf"), default="dshow",
                    help="whose list: DirectShow (OpenCV CAP_DSHOW, the default) or Media Foundation (CAP_MSMF)")
    a = ap.parse_args()

    if a.synthetic and a.backend == "msmf":
        devices = [DeviceInfo(i, n) for i, n in enumerate(SYNTHETIC_MF_LIST)]
    else:
        devices = list_devices(backend=a.backend, _raw=SYNTHETIC_LIST if a.synthetic else None)
    weevia = [d for d in devices if WEEVIA_S1_NAME.lower() in d.name.lower()]
    print(f"{len(devices)} video device(s) in the {LIST_NAME[a.backend]} list:")
    for d in devices:
        print(f"  [{d.index}] {d.name}{'   <- Weevia S1' if d in weevia else ''}")

    print(f"Weevia S1 ({WEEVIA_S1_NAME!r}) found: {len(weevia)}")
    if not weevia:
        print("Re-insert the cable; if the camera is missing from the list, Windows does not see it either.")
    elif len(weevia) > 1:
        print("More than one Weevia S1: the camera reports no USB serial number, so they cannot be told apart "
              "by name, and DeviceConfig.weevia_s1() stops with an error instead of guessing. "
              "Using two units on one computer has not been tested — connect one at a time.")
    other = "msmf" if a.backend == "dshow" else "dshow"
    print(f"The {LIST_NAME[other]} list (--backend {other}) can be in a different order: "
          "an index goes only with the backend whose list it came from.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
