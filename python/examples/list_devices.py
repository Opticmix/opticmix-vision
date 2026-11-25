"""list_devices — list the video devices Windows (or Linux) reports, and how many of them are a Weevia S1.

Usage:
  python examples/list_devices.py                             # DirectShow's list (the default; Linux: V4L2's)
  python examples/list_devices.py --backend msmf              # Media Foundation's list
  python examples/list_devices.py --backend v4l2              # Linux: the V4L2 capture nodes (the default there)
  python examples/list_devices.py --synthetic                 # no camera: a made-up list with two Weevia S1
  python examples/list_devices.py --synthetic --backend msmf  # no camera: the same made-up devices, another order
  python examples/list_devices.py --synthetic --backend v4l2  # no camera: the same made-up devices as V4L2 lists them
  python examples/list_devices.py --verbose                   # also each device's USB IDs and link speed

Prints every video device in the order of one capture backend's list — laptop webcams included — and marks the
ones whose name contains the Weevia S1's USB name. The index is the one OpenCV opens with that backend:
cv2.VideoCapture(index, cv2.CAP_DSHOW) for DirectShow, cv2.CAP_MSMF for Media Foundation. The two lists can be
in a different order: on one PC with one camera (2026-11-24), devices.list_devices(backend="msmf") and
list_devices() returned the same three cameras in a different order, once. So an index from one list does not
go with the other backend. An index also shifts when another camera is plugged in or unplugged; that is why
DeviceConfig.weevia_s1() finds the camera by name, in the list of the backend it opens with.

The camera reports no USB serial number, so two Weevia S1 have the same name and cannot be told apart by name:
with two connected, finding the camera by name stops with an error. Each line ends with the device's USB location
when Windows reports one (DeviceInfo.usb_location); open one of two cameras by it with
DeviceConfig.weevia_s1(usb_location=...) (examples/open_by_usb_location.py). A USB location is not a serial
number: it names the port the camera is plugged into, and it changes when the camera is plugged into another
port. Using two cameras at the same time is not supported. Picking a camera by USB location has been run with the camera
once (one PC, one camera, 2026-11-25): with one camera connected, the list showed its USB location, and given that
location it opened at 1600x800 MJPG and read one frame; a location no device has was refused (Python DirectShow, Rust
Media Foundation); not run with two cameras.

With --verbose, each device also gets one line for each of DeviceInfo.vid, pid, bcd_device (the USB vendor ID,
product ID and device revision) and link_speed (the USB link as the hub reports it: Low, Full, High, Super or
Super+), read as the doctor reads them, without opening the device; "not read" where Windows did not say. Not
reported by the camera: a USB serial number, a firmware version (bcd_device is the USB device revision; how it
relates to the firmware version has not been verified), a temperature.

Linux (--backend v4l2, the default there): the video capture nodes in /sys/class/video4linux, as
DeviceConfig.weevia_s1() looks them up (a UVC camera's metadata node is left out). The index is the N of
/dev/videoN, which cv2.VideoCapture(index, cv2.CAP_V4L2) opens; the USB location is the USB device's sysfs name
(e.g. 2-1), and --verbose reads the IDs and link speed from sysfs, as the doctor does. --backend v4l2 off Linux
says where the lists are instead of printing an empty one. --synthetic --backend v4l2 gives the same made-up
devices as V4L2 lists them (made-up /dev/videoN numbers and sysfs names).

examples/list_devices.py --backend msmf, the USB locations, --verbose and the doctor's Media Foundation list have
been run without a camera only (made-up lists); they have not been run with the camera. --backend v4l2: run without
a camera only (made-up sysfs trees); not run with the camera on Linux.
Needs: opticmix-vision.
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

# A made-up device list with made-up USB locations, IDs and link speeds — not read from any computer. The webcam's
# IDs and link speed are left unread.
SYNTHETIC_LIST = [(WEEVIA_S1_NAME, "PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(1)", 0x1D6B, 0x0200, 0x0409, "Super"),
                  ("Example Laptop Webcam", "PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(5)"),
                  (WEEVIA_S1_NAME, "PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(4)#USB(2)", 0x1D6B, 0x0200, 0x0409, "High")]
# The same made-up video devices as Media Foundation might list them: in another order.
SYNTHETIC_MF_ORDER = [1, 0, 2]
# ... and as V4L2 lists them: /dev/videoN (a UVC camera's metadata node, the odd numbers here, left out), the name
# and USB location as sysfs gives them.
SYNTHETIC_V4L2 = [(0, "Webcam gadget: WN Dual Camera", "2-1"), (2, "Example Laptop Webcam", "1-5"),
                  (4, "Webcam gadget: WN Dual Camera", "1-4.2")]
LIST_NAME = {"dshow": "DirectShow", "msmf": "Media Foundation", "v4l2": "V4L2"}
USB_LOCATION_NOTE = ("A USB location is not a serial number: it names the port the camera is plugged into, and it "
                     "changes when the camera is plugged into another port. Using two cameras at the same time is not "
                     "supported. Picking a camera by USB location has been run with the camera once (one PC, one "
                     "camera, 2026-11-25): with one camera connected, the list showed its USB location, and given "
                     "that location it opened at 1600x800 MJPG and read one frame; a location no device has was "
                     "refused (Python DirectShow, Rust Media Foundation); not run with two cameras.")
NOT_REPORTED = ("Not reported by the camera: a USB serial number, a firmware version (bcd_device is the USB device "
                "revision; how it relates to the firmware version has not been verified), a temperature.")


def _hex(v: int | None) -> str:
    return "not read" if v is None else f"0x{v:04x}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--synthetic", action="store_true", help="a made-up device list instead of this computer's")
    ap.add_argument("--backend", choices=("dshow", "msmf", "v4l2"),
                    help="whose list: DirectShow (OpenCV CAP_DSHOW, the default), Media Foundation (CAP_MSMF) or, on "
                         "Linux, V4L2 (CAP_V4L2, the default there without --synthetic)")
    ap.add_argument("--verbose", action="store_true", help="also each device's USB IDs and link speed, one line each")
    a = ap.parse_args()
    if a.backend is None:
        a.backend = "v4l2" if sys.platform.startswith("linux") and not a.synthetic else "dshow"
    if a.backend == "v4l2" and not a.synthetic and not sys.platform.startswith("linux"):
        print("The V4L2 list is Linux's. On Windows: the DirectShow list (the default) or --backend msmf (Media "
              "Foundation's).")
        return 0

    if a.synthetic and a.backend == "v4l2":
        devices = [DeviceInfo(i, name, at, *SYNTHETIC_LIST[j][2:])
                   for j, (i, name, at) in enumerate(SYNTHETIC_V4L2)]
    elif a.synthetic:
        order = SYNTHETIC_MF_ORDER if a.backend == "msmf" else range(len(SYNTHETIC_LIST))
        devices = [DeviceInfo(i, *SYNTHETIC_LIST[j]) for i, j in enumerate(order)]
    else:
        devices = list_devices(backend=a.backend)
    weevia = [d for d in devices if WEEVIA_S1_NAME.lower() in d.name.lower()]
    print(f"{len(devices)} video device(s) in the {LIST_NAME[a.backend]} list:")
    for d in devices:
        at = f"   USB location: {d.usb_location}" if d.usb_location else ""
        print(f"  [{d.index}] {d.name}{'   <- Weevia S1' if d in weevia else ''}{at}")
        if a.verbose:
            for field, value in (("vid", _hex(d.vid)), ("pid", _hex(d.pid)), ("bcd_device", _hex(d.bcd_device)),
                                 ("link_speed", d.link_speed or "not read")):
                print(f"      {field}: {value}")
    if a.verbose:
        print(NOT_REPORTED)

    print(f"Weevia S1 ({WEEVIA_S1_NAME!r}) found: {len(weevia)}")
    if not weevia:
        print("Re-insert the cable; if the camera is missing from the list, "
              f"{'Linux' if a.backend == 'v4l2' else 'Windows'} does not see it either.")
    elif len(weevia) > 1:
        print("More than one Weevia S1: the camera reports no USB serial number, so they cannot be told apart "
              "by name, and DeviceConfig.weevia_s1() stops with an error instead of guessing. Open one by the USB "
              'location shown above: DeviceConfig.weevia_s1(usb_location="<USB location>") '
              "(examples/open_by_usb_location.py).")
        print(USB_LOCATION_NOTE)
    if a.backend != "v4l2":
        other = "msmf" if a.backend == "dshow" else "dshow"
        print(f"The {LIST_NAME[other]} list (--backend {other}) can be in a different order: "
              "an index goes only with the backend whose list it came from.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
