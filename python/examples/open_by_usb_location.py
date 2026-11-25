"""open_by_usb_location — open one Weevia S1 by the USB port it is plugged into, and read one frame.

Usage:
  python examples/open_by_usb_location.py                                  # list each Weevia S1 and its USB location
  python examples/open_by_usb_location.py --usb-location "PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(1)"
  python examples/open_by_usb_location.py --synthetic                      # no camera: a made-up list of two

Two Weevia S1 have the same name (the camera reports no USB serial number), so DeviceConfig.weevia_s1() stops
with an error when both are connected. DeviceConfig.weevia_s1(usb_location=...) opens the one with the name at a
USB location — Windows: the USB device's first location path, as examples/list_devices.py and this example print
it (DirectShow's list, the one DeviceConfig.weevia_s1() opens from); Linux: the USB device's sysfs name, e.g.
1-4.2 (python -m opticmix_vision.doctor prints it as "location"; without --usb-location this example lists it from
V4L2's list, devices.list_devices()).

A USB location is not a serial number: it names the port the camera is plugged into, and it changes when the
camera is plugged into another port. Using two cameras at the same time is not supported. Picking a camera by USB
location has been run with the camera once (one PC, one camera, 2026-11-25): with one camera connected, the list showed
its USB location, and given that location it opened at 1600x800 MJPG and read one frame; a location no device has was
refused (Python DirectShow, Rust Media Foundation); not run with two cameras.

--synthetic picks from a made-up list of two Weevia S1 (made-up USB locations; the second one unless
--usb-location names another) and reads a generated frame, not measured data.
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

from opticmix_vision import Device, DeviceConfig, DeviceError  # noqa: E402
from opticmix_vision.device import WEEVIA_S1_NAME  # noqa: E402
from opticmix_vision.devices import DeviceInfo, list_devices, pick_by_usb_location  # noqa: E402

# A made-up device list with made-up USB locations — not read from any computer.
SYNTHETIC_LIST = [DeviceInfo(0, WEEVIA_S1_NAME, "PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(1)"),
                  DeviceInfo(1, "Example Laptop Webcam", "PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(5)"),
                  DeviceInfo(2, WEEVIA_S1_NAME, "PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(4)#USB(2)")]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--usb-location", help="the USB location of the Weevia S1 to open")
    ap.add_argument("--synthetic", action="store_true", help="a made-up list of two Weevia S1 and generated frames")
    a = ap.parse_args()

    windows, linux = sys.platform == "win32", sys.platform.startswith("linux")
    listed = SYNTHETIC_LIST if a.synthetic else (list_devices() if windows or linux else [])  # Linux: V4L2's list
    weevia = [d for d in listed if WEEVIA_S1_NAME.lower() in d.name.lower()]
    if a.synthetic or windows or linux:
        print(f"Weevia S1 in the {'made-up' if a.synthetic else 'DirectShow' if windows else 'V4L2'} list:")
        for d in weevia:
            print(f"  [{d.index}] USB location: {d.usb_location or 'not known'}")
    location = a.usb_location
    if location is None:
        if not a.synthetic:
            print("Pass --usb-location with one of them" + ("." if windows else
                  " — on Linux, the USB device's sysfs name (python -m opticmix_vision.doctor prints it)."))
            return 0
        location = weevia[-1].usb_location

    try:
        if a.synthetic:
            from opticmix_vision.synthetic import SyntheticStereoSource
            d = pick_by_usb_location(WEEVIA_S1_NAME, location, listed)     # the pick a real open makes
            print(f"picked [{d.index}] at USB location {location}")
            cam = SyntheticStereoSource()
        else:
            cam = Device(DeviceConfig.weevia_s1(usb_location=location)).open()
    except (LookupError, ValueError, DeviceError) as exc:     # ValueError: an empty --usb-location
        print(f"error: {exc}", file=sys.stderr)
        return 2
    with cam:
        print("negotiated:", cam.mode.to_dict())
        f = cam.read()
        print(f"one frame: left {f.left.shape[1]}x{f.left.shape[0]}, right {f.right.shape[1]}x{f.right.shape[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
