"""Diagnostics to paste into a bug report: one command, one block of text.

    python -m opticmix_vision.doctor                    # does not open the camera
    python -m opticmix_vision.doctor --open             # also opens it: mode, 3 s of frames, the controls (read only)
    python -m opticmix_vision.doctor --json             # the same report as JSON
    python -m opticmix_vision.doctor --output r.json    # written as UTF-8 (`>` in PowerShell 5.1 writes UTF-16)
    python -m opticmix_vision.doctor --synthetic --open # no hardware at all: generated frames, simulated controls

Without --open the camera is not opened. The USB details come from the operating system's device list
(Windows: the device tree through cfgmgr32; Linux: sysfs). On Windows the link speed is asked of the USB hub
driver the camera is plugged into, as Microsoft's USBView does. On Windows the video devices are printed as
DirectShow lists them and as Media Foundation lists them (OpenCV's CAP_DSHOW and CAP_MSMF indexes); the two lists
can be in a different order, and when the Weevia S1's index differs a line says so. Listing opens no device.

With --open the camera is opened as `Device(DeviceConfig.weevia_s1(stall_frames=0))` does: the capture format
(size, frame rate, pixel format) is set as on every open, and no camera control is written — the report
prints the SDK's control-write count before and after to show it. --backend msmf opens it through OpenCV's
Media Foundation backend instead (the camera found by name in Media Foundation's list); there, frames whose two
eyes both repeated the previous image are a finding — seen through that path on one PC with one camera
(2026-11-24), cause not established. --open --backend msmf has been run with the camera once (one PC, one
camera, 2026-11-25, OpenCV 4.13.0; the finding said 276 of 904 frames); the other --backend values: run without
a camera only.

What this camera does not report is printed as such: it has no USB serial number, no verified way to read a
firmware version (the USB device revision, bcdDevice, is shown instead; how it relates to the firmware version
has not been verified), and no known temperature readout.

--open has been run with the camera once (one PC, one camera, 2026-11-24); without --open, --json and Linux:
run without a camera only
(generated frames, fake Windows and Linux device lists).
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import platform
import re
import shutil
import struct
import sys
import time
import warnings
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

__all__ = ["collect", "format_text", "main"]

REPORT_FORMAT = 1
SYSFS_V4L = "/sys/class/video4linux"
NOT_AVAILABLE = {
    "serial_number": "not reported — the camera has no USB serial number",
    "firmware_version": ("not reported — no verified way to read it; the USB device revision (bcdDevice) is "
                         "shown instead, and how it relates to the firmware version has not been verified"),
    "temperature": "not available — no temperature readout is known for this camera",
}
_RATE = {"Low": "1.5 Mb/s", "Full": "12 Mb/s", "High": "480 Mb/s", "Super": "5 Gb/s", "Super+": "10 Gb/s or more"}
_LINUX_SPEED = {"1.5": "Low", "12": "Full", "480": "High", "5000": "Super"}   # sysfs `speed`, Mb/s


def _home(p: Any) -> str:
    """A path with the home folder shown as ~ (the report is meant to be pasted in public)."""
    s, h = os.path.abspath(str(p)), os.path.abspath(str(Path.home()))
    return "~" + s[len(h):] if os.path.normcase(s).startswith(os.path.normcase(h) + os.sep) else s


def _err(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


# ---- system and SDK ---------------------------------------------------------------------------

def _system() -> dict[str, Any]:
    out = {"os": platform.platform(), "machine": platform.machine(),
           "python": f"{platform.python_version()} ({platform.python_implementation()}, "
                     f"{struct.calcsize('P') * 8}-bit)"}
    if sys.platform.startswith("linux"):
        try:
            out["distribution"] = platform.freedesktop_os_release().get("PRETTY_NAME")
        except OSError:
            out["distribution"] = None
        out["libc"] = " ".join(platform.libc_ver()).strip() or None
    return out


def _sdk() -> dict[str, Any]:
    import importlib.metadata as md

    import numpy

    import opticmix_vision
    from opticmix_vision import _native
    dists = {}
    for d in ("opticmix-vision", "opticmix-vision-core"):
        try:
            dists[d] = md.version(d)
        except md.PackageNotFoundError:
            dists[d] = None
    core = sys.modules.get("opticmix_vision._core")
    out: dict[str, Any] = {
        "opticmix_vision": opticmix_vision.__version__,
        "package_dir": _home(Path(opticmix_vision.__file__).parent),
        "core": {"present": _native.available(),
                 "file": _home(core.__file__) if core is not None and getattr(core, "__file__", None) else None,
                 "error": None if _native.available() else str(_native.IMPORT_ERROR)},
        "distributions": dists, "numpy": numpy.__version__,
        "write_log": _home(os.environ["OPTICMIX_WRITE_LOG"]) if os.environ.get("OPTICMIX_WRITE_LOG") else None,
    }
    try:
        import cv2
        out["opencv"] = cv2.__version__
        out["opencv_camera_backends"] = list(dict.fromkeys(
            cv2.videoio_registry.getBackendName(b) for b in cv2.videoio_registry.getCameraBackends()))
    except Exception as exc:  # noqa: BLE001 — e.g. libGL.so.1 missing on Linux: report it, keep going
        out["opencv"], out["opencv_camera_backends"] = None, []
        out["opencv_error"] = _err(exc)
    return out


# ---- Windows: device tree (cfgmgr32) and the hub's view of the port ----------------------------

_CLASS_GUIDS = ("{ca3e7ab9-b4c3-4ae6-8251-579ef933890f}",    # Camera
                "{6bdd1fc6-810f-11d0-bec7-08002be2092f}")    # Image (older UVC drivers)
_PK = "a45c254e-df1c-4efd-8020-67d146a850e0"                 # devpkey.h: DEVPKEY_Device_*
_PKEYS = {"friendly_name": 14, "desc": 2, "hardware_ids": 3, "location_info": 15, "address": 30,
          "location_paths": 37}


def _win_cameras() -> list[dict[str, Any]]:
    """Present camera-class USB devices from the device tree. Reads the tree only; no device is opened."""
    import ctypes as C
    import uuid
    from ctypes import wintypes as W

    cfg = C.WinDLL("cfgmgr32")

    class PK(C.Structure):
        _fields_ = [("fmtid", C.c_ubyte * 16), ("pid", W.ULONG)]

    def prop(inst: int, key: str) -> Any:
        k = PK((C.c_ubyte * 16).from_buffer_copy(uuid.UUID(_PK).bytes_le), _PKEYS[key])
        t, n = W.ULONG(), W.ULONG(0)
        cfg.CM_Get_DevNode_PropertyW(W.DWORD(inst), C.byref(k), C.byref(t), None, C.byref(n), 0)   # size
        if not n.value:
            return None
        buf = (C.c_ubyte * n.value)()
        if cfg.CM_Get_DevNode_PropertyW(W.DWORD(inst), C.byref(k), C.byref(t), buf, C.byref(n), 0):
            return None
        raw = bytes(buf)[:n.value]
        if t.value == 0x12:                                    # DEVPROP_TYPE_STRING
            return raw.decode("utf-16-le").rstrip("\0")
        if t.value == 0x2012:                                  # DEVPROP_TYPE_STRING_LIST
            return [s for s in raw.decode("utf-16-le").split("\0") if s]
        if t.value == 0x7:                                     # DEVPROP_TYPE_UINT32
            return int.from_bytes(raw[:4], "little")
        return None

    def locate(dev_id: str) -> int | None:
        h = W.DWORD()
        return h.value if cfg.CM_Locate_DevNodeW(C.byref(h), dev_id, 0) == 0 else None   # present devices only

    def parent_id(inst: int) -> str | None:
        p, b = W.DWORD(), C.create_unicode_buffer(512)
        return None if cfg.CM_Get_Parent(C.byref(p), W.DWORD(inst), 0) or cfg.CM_Get_Device_IDW(p, b, 512, 0) \
            else b.value

    rows = []
    for guid in _CLASS_GUIDS:
        flags, n = 0x200 | 0x100, W.ULONG()                   # CM_GETIDLIST_FILTER_CLASS | _PRESENT
        if cfg.CM_Get_Device_ID_List_SizeW(C.byref(n), guid, flags):
            continue
        buf = C.create_unicode_buffer(n.value)
        if cfg.CM_Get_Device_ID_ListW(guid, buf, n, flags):
            continue
        for dev_id in (s for s in buf[:n.value].split("\0") if s.upper().startswith("USB\\")):
            inst = locate(dev_id)
            if inst is None:
                continue
            st, pr = W.ULONG(), W.ULONG()
            cfg.CM_Get_DevNode_Status(C.byref(st), C.byref(pr), W.DWORD(inst), 0)
            usb_id = parent_id(inst) if "&MI_" in dev_id.upper() else dev_id   # one interface of a composite device
            u = locate(usb_id) if usb_id else None
            rows.append({"name": prop(inst, "friendly_name") or prop(inst, "desc"),
                         "started": bool(st.value & 0x8), "problem": pr.value if st.value & 0x400 else 0,
                         **({k: prop(u, k) for k in ("hardware_ids", "location_paths", "location_info", "address")}
                            if u is not None else {}),
                         "usb_id": usb_id, "hub_id": parent_id(u) if u is not None else None})
    return rows


# USB_NODE_CONNECTION_INFORMATION_EX (usbioctl.h, packed): ConnectionIndex ULONG, USB_DEVICE_DESCRIPTOR (18 bytes:
# bLength, bDescriptorType, bcdUSB, class, subclass, protocol, bMaxPacketSize0, idVendor, idProduct, bcdDevice,
# 3 string indexes, bNumConfigurations), CurrentConfigurationValue UCHAR, Speed UCHAR, DeviceIsHub BOOLEAN,
# DeviceAddress USHORT, NumberOfOpenPipes ULONG, ConnectionStatus ULONG (pipe list after).
_EX = struct.Struct("<I BBHBBBB HHH BBBB B B B H I I")
# USB_NODE_CONNECTION_INFORMATION_EX_V2: ConnectionIndex, Length, SupportedUsbProtocols, Flags (all ULONG).
_V2 = struct.Struct("<IIII")
_SPEED = {0: "Low", 1: "Full", 2: "High", 3: "Super"}      # USB_DEVICE_SPEED


def _parse_ex(buf: bytes) -> dict[str, int]:
    """The parts of a USB_NODE_CONNECTION_INFORMATION_EX reply the report uses."""
    f = _EX.unpack_from(buf)
    return {"vid": f[8], "pid": f[9], "bcd_device": f[10], "speed": f[16], "status": f[20]}


def _parse_v2(buf: bytes) -> int:
    """Flags of a USB_NODE_CONNECTION_INFORMATION_EX_V2 reply: bit 0 operating at SuperSpeed or higher, bit 1
    SuperSpeed-capable or higher, bit 2 operating at SuperSpeedPlus or higher, bit 3 SuperSpeedPlus-capable."""
    return _V2.unpack_from(buf)[3]


def _link_name(speed: int, flags: int | None) -> tuple[str, bool | None]:
    """(link speed name, SuperSpeed-capable). The EX speed can say High for a SuperSpeed link; the V2 flags win."""
    name = _SPEED.get(speed, "unknown")
    if flags is not None and flags & 0x4:
        name = "Super+"
    elif flags is not None and flags & 0x1:
        name = "Super"
    return name, None if flags is None else bool(flags & 0x2)


def _hub_link(hub_id: str, port: int) -> tuple[dict[str, int], int | None] | None:
    """(EX reply, V2 flags or None) for `port` of the hub, as the hub driver reports it; None if it cannot be asked.

    IOCTL_USB_GET_NODE_CONNECTION_INFORMATION_EX(_V2) on the hub, as USBView does. The camera is not opened."""
    import ctypes as C
    import uuid
    from ctypes import wintypes as W

    cfg, k32 = C.WinDLL("cfgmgr32"), C.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = W.HANDLE
    k32.CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, C.c_void_p, W.DWORD, W.DWORD, W.HANDLE]
    k32.DeviceIoControl.argtypes = [W.HANDLE, W.DWORD, C.c_void_p, W.DWORD, C.c_void_p, W.DWORD,
                                    C.POINTER(W.DWORD), C.c_void_p]
    k32.CloseHandle.argtypes = [W.HANDLE]
    guid = (C.c_ubyte * 16).from_buffer_copy(uuid.UUID("f18a0e88-c30c-11d0-8815-00a0c906bed8").bytes_le)  # USB hub
    n = W.ULONG()
    if cfg.CM_Get_Device_Interface_List_SizeW(C.byref(n), guid, W.LPCWSTR(hub_id), 0) or n.value < 2:
        return None
    buf = C.create_unicode_buffer(n.value)
    if cfg.CM_Get_Device_Interface_ListW(guid, W.LPCWSTR(hub_id), buf, n, 0):
        return None
    h = k32.CreateFileW(buf.value, 0x40000000, 0x2, None, 3, 0, None)    # GENERIC_WRITE, FILE_SHARE_WRITE (as USBView)
    if h in (None, W.HANDLE(-1).value):
        return None
    try:
        got = W.DWORD()
        ex = (C.c_ubyte * 512)()
        struct.pack_into("<I", ex, 0, port)
        if not k32.DeviceIoControl(h, 0x220448, ex, 512, ex, 512, C.byref(got), None) or got.value < _EX.size:
            return None
        v2 = (C.c_ubyte * _V2.size)()
        _V2.pack_into(v2, 0, port, _V2.size, 0x7, 0)                        # ask about USB 1.1, 2.0 and 3.0
        ok = k32.DeviceIoControl(h, 0x22045C, v2, _V2.size, v2, _V2.size, C.byref(got), None)
        return _parse_ex(bytes(ex)), (_parse_v2(bytes(v2)) if ok else None)
    finally:
        k32.CloseHandle(h)


def _usb_windows(records: list[dict[str, Any]], link: Callable[[str, int], Any]) -> list[dict[str, Any]]:
    out, links = [], {}
    for r in records:
        m = re.search(r"VID_([0-9A-F]{4})&PID_([0-9A-F]{4})&REV_([0-9A-F]{4})", " ".join(r.get("hardware_ids") or []),
                      re.I)
        hub, port = r.get("hub_id"), r.get("address")
        key = (hub, port)
        if key not in links:
            try:
                links[key] = link(hub, int(port)) if hub and port else None
            except Exception:  # noqa: BLE001 — an unknown link speed is reported as unknown
                links[key] = None
        speed, capable = "unknown", None
        if links[key] and m:
            ex, flags = links[key]
            # only when the hub's port holds this very device (connected, same VID:PID) — not a neighbour's speed
            if ex["status"] == 1 and (ex["vid"], ex["pid"]) == (int(m.group(1), 16), int(m.group(2), 16)):
                speed, capable = _link_name(ex["speed"], flags)
        problem = int(r.get("problem") or 0)
        paths = r.get("location_paths") or []
        out.append({
            "name": r.get("name"),
            "vid": m.group(1).lower() if m else None, "pid": m.group(2).lower() if m else None,
            "usb_device_revision": m.group(3).upper() if m else None,
            "status": f"problem code {problem}" if problem else ("OK" if r.get("started") else "not started"),
            "location": paths[0] if paths else None, "port": r.get("location_info"),
            "link_speed": speed, "superspeed_capable": capable,
        })
    return out


# ---- Linux: sysfs -----------------------------------------------------------------------------

def _read(p: Path) -> str | None:
    try:
        return p.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None


def _linux(root: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(video nodes, their USB devices) from sysfs."""
    nodes, usb = [], {}

    def num(p: Path) -> tuple[int, str]:
        d = p.name[5:]
        return (int(d) if d.isdigit() else 1 << 30, p.name)

    for d in sorted(Path(root).glob("video*"), key=num):
        name, idx = _read(d / "name"), _read(d / "index")
        node = {"path": f"/dev/{d.name}", "name": name, "index": int(idx) if idx and idx.isdigit() else idx}
        real = Path(os.path.realpath(d / "device"))            # the USB interface; its parent is the USB device
        udev = next((c for c in [real, *real.parents][:4] if (c / "idVendor").is_file()), None)
        if udev is not None:
            node["usb"] = udev.name
            if udev.name not in usb:
                mbps = _read(udev / "speed")
                speed = _LINUX_SPEED.get(mbps or "") or ("Super+" if mbps and mbps.isdigit() and int(mbps) > 5000
                                                         else "unknown")
                usb[udev.name] = {"name": name, "vid": _read(udev / "idVendor"), "pid": _read(udev / "idProduct"),
                                  "usb_device_revision": (_read(udev / "bcdDevice") or "").upper() or None,
                                  "status": "OK", "location": udev.name, "port": None,
                                  "link_speed": speed, "superspeed_capable": None}
        nodes.append(node)
    return nodes, list(usb.values())


# ---- devices, USB, calibration ----------------------------------------------------------------

def _weevia_indexes(rows: list[dict[str, Any]], needle: str) -> str:
    return ", ".join(str(d["index"]) for d in rows if needle in (d.get("name") or "").lower()) or "not listed"


def _hardware(synthetic: bool, _win_records: Any, _link: Any, _sysfs: str | None, _platform: str | None,
              mf: bool = True) -> dict[str, Any]:
    from .device import WEEVIA_S1_NAME
    plat = _platform or sys.platform
    needle = WEEVIA_S1_NAME.lower()
    video: dict[str, Any] = {"source": None, "devices": [], "error": None}
    usb: dict[str, Any] = {"source": None, "devices": [], "error": None}
    if synthetic:
        video["source"] = usb["source"] = "skipped (--synthetic)"
    elif plat == "win32":
        from . import devices
        video["source"] = "DirectShow, through ffmpeg" if shutil.which("ffmpeg") else "DirectShow"
        try:
            video["devices"] = [{"index": d.index, "name": d.name} for d in devices.list_devices()]
        except Exception as exc:  # noqa: BLE001
            video["error"] = _err(exc)
        if mf:                    # Media Foundation's list too: listed only, no device is activated
            m: dict[str, Any] = {"devices": [], "error": None, "note": None}
            try:
                m["devices"] = [{"index": d.index, "name": d.name} for d in devices.list_devices(backend="msmf")]
            except Exception as exc:  # noqa: BLE001
                m["error"] = _err(exc)
            ds, ms = _weevia_indexes(video["devices"], needle), _weevia_indexes(m["devices"], needle)
            if not video["error"] and not m["error"] and ds != ms:      # information, not a finding
                m["note"] = (f"Weevia S1 index: DirectShow {ds}, Media Foundation {ms}. The two lists can be in a "
                             "different order; an index goes only with the backend whose list it came from (OpenCV "
                             "CAP_DSHOW or CAP_MSMF). DeviceConfig.weevia_s1() finds the camera by name in the list "
                             "of its backend; with backend='any' it stops when the two indexes differ.")
            video["media_foundation"] = m
        usb["source"] = "device tree (cfgmgr32)"
        try:
            usb["devices"] = _usb_windows(_win_cameras() if _win_records is None else _win_records,
                                          _hub_link if _link is None else _link)
        except Exception as exc:  # noqa: BLE001
            usb["error"] = _err(exc)
    elif plat.startswith("linux"):
        video["source"] = usb["source"] = "sysfs"
        try:
            video["devices"], usb["devices"] = _linux(_sysfs or SYSFS_V4L)
        except Exception as exc:  # noqa: BLE001
            video["error"] = usb["error"] = _err(exc)
    else:
        video["source"] = usb["source"] = f"not read on {plat}"
    count = sum(1 for d in video["devices"] if needle in (d.get("name") or "").lower()
                and ("path" not in d or d.get("index") == 0))          # Linux: capture nodes only, as find_v4l2_node
    listed = not synthetic and not video["error"] and (plat == "win32" or plat.startswith("linux"))
    return {"video_devices": video, "usb": usb,
            "weevia_s1": {"name": WEEVIA_S1_NAME, "count": count if listed else None}}   # None: not looked for


def _calibration() -> dict[str, Any]:
    from . import calib
    d = calib.store_dir()
    out: dict[str, Any] = {"folder": _home(d), "files": [], "installed": [], "check": None}
    try:
        out["files"] = sorted(p.name for p in d.iterdir()) if d.is_dir() else []
        out["installed"] = calib.installed()
        c = calib.resolve()
        out["check"] = f"ok — {c.device.get('serial', '?')}, {c.left.size[0]}x{c.left.size[1]} eyes"
    except Exception as exc:  # noqa: BLE001 — what resolve() says is the diagnosis
        out["check"] = str(exc)
    return out


# ---- the camera (--open) ----------------------------------------------------------------------

def _writes() -> int | None:
    try:
        from .controls import write_counts
        return int(write_counts().get("_total", 0))
    except Exception:  # noqa: BLE001 — no extension: no counter
        return None


def _stall_frames() -> int:
    from .device import DeviceConfig
    return int(DeviceConfig.__dataclass_fields__["stall_frames"].default)


def _measure(cam: Any, seconds: float, timestamps: str | Path | None) -> dict[str, Any]:
    import numpy as np

    rows, err = [], None
    try:
        prev = cam.read()                  # the first frame carries start-up delay; time from the next one
        end = time.perf_counter() + seconds
        while time.perf_counter() < end:
            f = cam.read()
            rows.append((f.index, f.host_ns, bool(np.array_equal(f.left, prev.left)),
                         bool(np.array_equal(f.right, prev.right))))
            prev = f
    except Exception as exc:  # noqa: BLE001 — a failed read is a finding; the frames before it still count
        err = _err(exc)
    out: dict[str, Any] = {"seconds": seconds, "frames": len(rows), "read_error": err, "fps": None,
                           "interval_ms": None, "late_intervals": None}
    if len(rows) >= 2:
        t = np.array([r[1] for r in rows], dtype=np.int64)
        dt = np.diff(t) / 1e6
        out["fps"] = round((len(rows) - 1) / ((t[-1] - t[0]) / 1e9), 2) if t[-1] > t[0] else None
        out["interval_ms"] = {"mean": round(float(dt.mean()), 2), "min": round(float(dt.min()), 2),
                              "max": round(float(dt.max()), 2)}
        out["late_intervals"] = int(np.sum(dt > 1.5 * np.median(dt)))
    stall = _stall_frames()
    for i, eye in ((2, "left"), (3, "right")):
        run = best = 0
        for r in rows:
            run = run + 1 if r[i] else 0
            best = max(best, run)
        out[f"{eye}_identical_frames"] = sum(1 for r in rows if r[i])
        out[f"{eye}_longest_identical_run"] = best + 1 if best else 0      # images in a row, as stall_frames counts
    out["both_identical_frames"] = sum(1 for r in rows if r[2] and r[3])
    out["stall_frames"] = stall
    out["stalled"] = [e for e in ("left", "right") if out[f"{e}_longest_identical_run"] >= stall]
    if timestamps:
        try:
            with open(timestamps, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["frame", "host_ns", "interval_ms", "left_identical", "right_identical"])
                for k, r in enumerate(rows):
                    w.writerow([r[0], r[1], "" if k == 0 else f"{(r[1] - rows[k - 1][1]) / 1e6:.3f}",
                                int(r[2]), int(r[3])])
            out["timestamps_file"] = _home(Path(timestamps).resolve())
        except OSError as exc:
            out["timestamps_file"] = f"not written: {_err(exc)}"
    return out


def _controls(cam: Any) -> dict[str, Any]:
    try:
        c = cam.controls
        rows = {n: asdict(i) for n, i in c.describe().items()}        # describe() reads; it never writes
        return {"backend": c.backend_name, "note": c.backend_note, "count": len(rows), "controls": rows}
    except Exception as exc:  # noqa: BLE001
        return {"error": _err(exc)}


def _camera(synthetic: bool, seconds: float, device: int | str | None, timestamps: str | Path | None,
            _open: Callable[[], Any] | None, plat: str, backend: str | None = None) -> dict[str, Any]:
    from .device import WEEVIA_S1_NAME, Device, DeviceConfig
    out: dict[str, Any] = {"opened": False, "advertised_modes": None, "error": None}
    before = _writes()
    if not synthetic and _open is None and plat == "win32" and device is None:
        from . import devices
        try:
            modes = devices.list_modes(WEEVIA_S1_NAME)       # through ffmpeg; empty = could not find out
            out["advertised_modes"] = [m.label() for m in modes] or None
        except Exception:  # noqa: BLE001
            pass
    try:
        if _open is not None:
            cam = _open()
        elif synthetic:
            from .synthetic import SyntheticStereoSource
            cam = SyntheticStereoSource(realtime=True)
        else:
            kw: dict[str, Any] = {"stall_frames": 0}         # the doctor counts identical frames itself
            if device is not None:
                kw["device"] = device
            if backend is not None:
                kw["backend"] = backend
            cam = Device(DeviceConfig.weevia_s1(**kw)).open()
    except Exception as exc:  # noqa: BLE001 — "not found" / "failed to open" is the finding
        out["error"] = _err(exc)
    else:
        out["opened"] = True
        try:
            out["mode"] = cam.mode.to_dict()
            out["measure"] = _measure(cam, seconds, timestamps)
            out["controls"] = _controls(cam)
        except Exception as exc:  # noqa: BLE001
            out["error"] = _err(exc)
        finally:
            cam.close()
    after = _writes()
    out["control_writes"] = {"before": before, "after": after,
                             "by_this_run": None if before is None or after is None else after - before}
    return out


# ---- report -----------------------------------------------------------------------------------

def _msmf(md: dict[str, Any]) -> bool:
    return str(md.get("backend_name") or "").upper() == "MSMF"


def _fourcc_text(s: str) -> str:
    """The four characters, or the code in hex (0x00000016) when they are not all printable, as through MSMF."""
    return s if s.isprintable() else f"0x{int.from_bytes(s.encode('latin-1'), 'little'):08x}"


def _findings(r: dict[str, Any]) -> list[str]:
    from .device import _MSMF_REPEAT_WARN
    f = []
    sdk = r["sdk"]
    if not sdk["core"]["present"]:
        f.append("The compiled extension (opticmix_vision._core) is missing: finding the camera by name, the "
                 "controls and calibration files do not work. Install the wheel: pip install opticmix-vision.")
    core_v = sdk["distributions"].get("opticmix-vision-core")
    if core_v and core_v != sdk["opticmix_vision"]:
        f.append(f"opticmix-vision-core {core_v} is installed beside opticmix_vision {sdk['opticmix_vision']}: "
                 "the versions differ.")
    n = r["weevia_s1"]["count"]
    if n == 0:
        f.append("No Weevia S1 in the video device list. Re-insert the USB cable.")
    elif n and n > 1:
        f.append(f"{n} Weevia S1 are connected. They cannot be told apart by name (no USB serial number): connect one "
                 "at a time, or open one by its USB location (`location` below): "
                 "DeviceConfig.weevia_s1(usb_location=...). A USB location is the port, not a serial number. Picking a "
                 "camera by USB location has been run without a camera only (made-up device lists); it has not been "
                 "run with a camera, and not run with two cameras.")
    needle = r["weevia_s1"]["name"].lower()
    for d in r["usb"]["devices"]:
        if needle not in (d["name"] or "").lower():         # the same partial match the device count uses
            continue
        if d["status"] != "OK":
            f.append(f"The operating system reports the Weevia S1 as: {d['status']}.")
        if d["link_speed"] in ("Low", "Full", "High"):
            f.append(f"The Weevia S1 USB link is {d['link_speed']} ({_RATE[d['link_speed']]}). The README's measured "
                     "figures were over SuperSpeed; USB 2 is not qualified. Connect to a USB 3 port directly.")
    cam = r.get("camera")
    if cam:
        if cam["error"]:
            f.append(("The camera failed after opening: " if cam["opened"] else "The camera did not open: ")
                     + cam["error"])
        m = cam.get("measure") or {}
        md = cam.get("mode") or {}
        if md and not r["synthetic"]:
            # device.py records why: with the MJPG request dropped the stream negotiated YUY2 and ran far below 90 fps.
            # Not through MSMF: there OpenCV reads back its own output format code (RGB32/RGB24), not the camera's.
            if md["fourcc"] != "MJPG" and not _msmf(md):
                f.append(f"The negotiated pixel format is {_fourcc_text(md['fourcc']) or 'not reported'}, not MJPG. "
                         "The Weevia S1 is opened in MJPG for its 90 fps mode (DeviceConfig.weevia_s1()); in other "
                         "formats the frame "
                         "rate falls short of it. If you pass your own settings, request MJPG (DeviceConfig sets it in the "
                         "order the backend needs). See the README, 'The frame rate is far below 90'.")
            # 0.8 is a judgment line, not a measured bound. For reference, one unit at 1600x800 MJPG read
            # 300 frames near 90 fps; 0.8 x 90 = 72 leaves room for host jitter.
            if m.get("fps") and md["fps"] > 0 and m["fps"] < 0.8 * md["fps"]:
                f.append(f"Frames arrived at {m['fps']:g} fps (host clock), well below the negotiated "
                         f"{md['fps']:g} fps. Check the USB link speed above and close other programs that may use "
                         "the camera.")
        if m.get("read_error"):
            f.append(f"Reading frames failed: {m['read_error']}")
        if m.get("stalled"):
            f.append(f"The {' and '.join(m['stalled'])} eye sent the same image {m['stall_frames']}+ frames in a row: "
                     "its stream stopped. Re-insert the USB cable.")
        elif (md.get("backend_name", "").upper() == "MSMF" and m.get("frames")
              and m["both_identical_frames"] > _MSMF_REPEAT_WARN * m["frames"]):
            f.append(f"Repeated images (MSMF): {m['both_identical_frames']} of {m['frames']} frames repeated the "
                     "previous image in both eyes through OpenCV's Media Foundation backend. Seen on one PC with one "
                     "camera (2026-11-24): 276 of 900 frames per eye through this path; not seen through DirectShow "
                     "(the default; 0 of 900) or the Rust crate (0 of 450); cause not established. driver_ns does "
                     "not show whether a new image arrived.")
        w = cam["control_writes"]["by_this_run"]
        if w:
            f.append(f"{w} control writes happened during this run (expected 0).")
    return f


def collect(*, open_camera: bool = False, synthetic: bool = False, seconds: float = 3.0,
            device: int | str | None = None, timestamps: str | Path | None = None, backend: str | None = None,
            _win_records: Any = None, _link: Any = None, _sysfs: str | None = None, _platform: str | None = None,
            _open: Callable[[], Any] | None = None) -> dict[str, Any]:
    """The report as a dict (JSON-ready). The camera is opened only with open_camera=True; synthetic=True reads
    no hardware at all (generated frames and simulated controls instead of the camera)."""
    plat = _platform or sys.platform
    r: dict[str, Any] = {"report": "opticmix_vision.doctor", "format": REPORT_FORMAT,
                         "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "synthetic": synthetic,
                         "system": _system(), "sdk": _sdk()}
    keep, lg = _Keep(), logging.getLogger("opticmix_vision")     # the SDK's warnings, from any thread, into the report
    lg.addHandler(keep)
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            r.update(_hardware(synthetic, _win_records, _link, _sysfs, plat))
            r["not_available"] = dict(NOT_AVAILABLE)
            r["calibration"] = _calibration()
            r["camera"] = _camera(synthetic, seconds, device, timestamps, _open, plat, backend) if open_camera else None
    finally:
        lg.removeHandler(keep)
    r["log"] = keep.lines + [f"{w.category.__name__}: {w.message}" for w in caught]
    r["findings"] = _findings(r)
    return r


class _Keep(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(f"{record.levelname} {record.name}: {record.getMessage()}")


def _usb_summary(synthetic: bool = False) -> str:
    """One line for a status bar, without opening the camera: VID:PID · REV · link speed · no serial."""
    if synthetic:
        return "synthetic · no serial"
    try:
        h = _hardware(False, None, None, None, None, mf=False)
    except Exception:  # noqa: BLE001 — the explorer must open even when this cannot be read
        return "USB: unknown · no serial"
    needle = h["weevia_s1"]["name"].lower()
    d = next((d for d in h["usb"]["devices"] if needle in (d["name"] or "").lower()), None)
    if d is None:
        return "USB: — · no serial"
    return f"{d['vid']}:{d['pid']} · REV {d['usb_device_revision']} · {d['link_speed']} · no serial"


def _v(x: Any) -> str:
    return "-" if x is None else str(x)


def format_text(report: dict[str, Any]) -> str:
    """The report as the plain text the command prints."""
    r, L = report, []
    s, k = r["system"], r["sdk"]
    L.append(f"opticmix_vision doctor (format {r['format']}, {r['time']})" + (" — SYNTHETIC, no hardware read" if
                                                                            r["synthetic"] else ""))
    L.append("== Findings ==")
    L += [f"- {x}" for x in r["findings"]] or ["- none"]
    L.append("== System ==")
    L.append(f"OS:              {s['os']} ({s['machine']})")
    if "distribution" in s:
        L.append(f"distribution:    {_v(s['distribution'])}, libc {_v(s['libc'])}")
    L.append(f"Python:          {s['python']}")
    L.append("== SDK ==")
    L.append(f"opticmix_vision: {k['opticmix_vision']} ({k['package_dir']})")
    c = k["core"]
    L.append("_core:           " + (f"present ({c['file']})" if c["present"] else f"MISSING ({c['error']})"))
    L.append("pip packages:    " + ", ".join(f"{d} {v or 'not installed'}" for d, v in k["distributions"].items()))
    L.append(f"numpy:           {k['numpy']}")
    L.append(f"write log:       {k['write_log'] or 'off'} (OPTICMIX_WRITE_LOG: one line per control write)")
    L.append(f"OpenCV:          {_v(k['opencv'])}" + (f" (camera backends: {', '.join(k['opencv_camera_backends'])})"
                                                     if k["opencv_camera_backends"] else "")
             + (f" — {k['opencv_error']}" if k.get("opencv_error") else ""))
    v = r["video_devices"]
    L.append(f"== Video devices ({v['source']}) ==")
    if v["error"]:
        L.append(f"error: {v['error']}")
    for d in v["devices"]:
        where = d.get("path", d.get("index"))
        L.append(f"  {where}  {d.get('name')}" + (f"  (index {d['index']})" if "path" in d else ""))
    if not v["devices"] and not v["error"]:
        L.append("  (none)")
    m = v.get("media_foundation")
    if m is not None:
        L.append('Media Foundation (backend="msmf"; listed, not opened):')
        L += [f"  error: {m['error']}"] if m["error"] else \
            [f"  {d['index']}  {d['name']}" for d in m["devices"]] or ["  (none)"]
    w = r["weevia_s1"]
    L.append(f"Weevia S1 ({w['name']!r}) connected: {_v(w['count'])}")
    if m is not None and m["note"]:
        L.append(m["note"])
    u = r["usb"]
    L.append(f"== USB ({u['source']}; the camera is not opened) ==")
    if u["error"]:
        L.append(f"error: {u['error']}")
    for d in u["devices"]:
        rate = f" ({_RATE[d['link_speed']]})" if d["link_speed"] in _RATE else ""
        cap = {True: ", device is SuperSpeed-capable", False: ", device is not SuperSpeed-capable"}.get(
            d["superspeed_capable"], "")
        L.append(f"{d['name']}")
        L.append(f"  VID:PID                          {_v(d['vid'])}:{_v(d['pid'])}")
        L.append(f"  USB device revision (bcdDevice)  {_v(d['usb_device_revision'])}")
        L.append(f"  status                           {d['status']}")
        L.append(f"  location                         {_v(d['location'])}" + (f" [{d['port']}]" if d["port"] else ""))
        L.append(f"  link speed                       {d['link_speed']}{rate}{cap}")
    if not u["devices"] and not u["error"]:
        L.append("  (none)")
    L.append("Not reported by this camera:")
    L += [f"  {name.replace('_', ' '):<17}{text}" for name, text in r["not_available"].items()]
    cal = r["calibration"]
    L.append("== Calibration ==")
    L.append(f"folder:          {cal['folder']}")
    L.append(f"files:           {', '.join(cal['files']) or '(none)'}")
    L.append(f"check:           {cal['check']}")
    L.append("details:         python -m opticmix_vision.calib show")
    cam = r["camera"]
    L.append("== Camera ==")
    if cam is None:
        L.append("not opened (add --open to measure the mode, the frame rate and read the controls)")
        return "\n".join(L + _log_lines(r)) + "\n"
    if cam["advertised_modes"]:
        L.append("advertised modes: " + "; ".join(cam["advertised_modes"]))
    if cam["error"]:
        L.append(("error after opening: " if cam["opened"] else "open failed: ") + cam["error"])
    if cam["opened"] and "controls" in cam:
        md = cam["mode"]
        L.append(f"negotiated mode: {md['width']}x{md['height']} {_fourcc_text(md['fourcc'])} {md['fps']:g} fps "
                 f"({md['backend_name']})")
        if _msmf(md):
            L.append("format:          OpenCV's Media Foundation backend reports its output format code here, not the "
                     "camera's format")
        m = cam["measure"]
        L.append(f"frames:          {m['frames']} in {m['seconds']:g} s, {_v(m['fps'])} fps (host clock; first frame "
                 "not counted)")
        if m["interval_ms"]:
            i = m["interval_ms"]
            L.append(f"interval ms:     mean {i['mean']}  min {i['min']}  max {i['max']}; "
                     f"late (over 1.5x the median): {m['late_intervals']}")
        L.append(f"identical to the previous image: left {m['left_identical_frames']}, right "
                 f"{m['right_identical_frames']}, both eyes {m['both_identical_frames']}; longest run left "
                 f"{m['left_longest_identical_run']}, right {m['right_longest_identical_run']} -> "
                 + (f"{' and '.join(m['stalled'])} eye stalled" if m["stalled"] else "no eye stalled")
                 + f" (threshold {m['stall_frames']})")
        if m["read_error"]:
            L.append(f"read error:      {m['read_error']}")
        if m.get("timestamps_file"):
            L.append(f"timestamps:      {m['timestamps_file']}")
        ct = cam["controls"]
        if "error" in ct:
            L.append(f"controls: {ct['error']}")
        else:
            L.append(f"controls ({ct['count']}, backend {ct['backend']}; read only)"
                     + (f": {ct['note']}" if ct["note"] else ""))
            rows = [("name", "current", "range", "default", "units", "effect")]
            for name, i in ct["controls"].items():
                rng = ("/".join(i["choices"]) if i["choices"] else
                       "-" if i["minimum"] is None else f"{i['minimum']}..{i['maximum']} step {_v(i['step'])}")
                rows.append((name, _v(i["current"]), rng, _v(i["default"]), i["units"], i["effect"]))
            wd = [max(len(row[j]) for row in rows) for j in range(6)]
            L += ["  " + "  ".join(cell.ljust(x) for cell, x in zip(row, wd)).rstrip() for row in rows]
    w = cam["control_writes"]
    L.append(f"control writes by this run: {_v(w['by_this_run'])} (SDK write counter before {_v(w['before'])}, "
             f"after {_v(w['after'])})")
    return "\n".join(L + _log_lines(r)) + "\n"


def _log_lines(r: dict[str, Any]) -> list[str]:
    return ["== Log (opticmix_vision warnings during this run) =="] + ([f"  {x}" for x in r["log"]] or ["  (none)"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m opticmix_vision.doctor",
                                 description="Print what to paste into a bug report. Does not open the camera "
                                             "unless --open is given.")
    ap.add_argument("--open", action="store_true", help="open the camera: mode, frame rate, stalls, controls (read only)")
    ap.add_argument("--synthetic", action="store_true", help="read no hardware: generated frames, simulated controls")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    ap.add_argument("--seconds", type=float, default=3.0, help="how long --open reads frames (default 3)")
    ap.add_argument("--device", help="with --open: the device index or /dev/videoN instead of finding it by name")
    ap.add_argument("--timestamps", metavar="FILE", help="with --open: write each frame's host time to a CSV file")
    ap.add_argument("--backend", choices=("dshow", "msmf", "v4l2"),
                    help="with --open: the OpenCV capture backend (default: the Weevia S1 default for this OS)")
    ap.add_argument("--output", metavar="FILE", help="write the report to FILE in UTF-8 instead of printing it "
                                                     "(JSON when FILE ends in .json, or with --json)")
    a = ap.parse_args(argv)
    if a.seconds <= 0:
        ap.error("--seconds must be positive")
    if (a.timestamps or a.device or a.backend) and not a.open:
        ap.error("--timestamps, --device and --backend need --open")
    for stream in (sys.stdout, sys.stderr):                    # names with non-ASCII characters on a cp949 console
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    dev = int(a.device) if a.device and a.device.isdigit() else a.device
    r = collect(open_camera=a.open, synthetic=a.synthetic, seconds=a.seconds, device=dev, timestamps=a.timestamps,
                backend=a.backend)
    as_json = a.json or (a.output or "").lower().endswith(".json")
    text = json.dumps(r, indent=2, ensure_ascii=False) + "\n" if as_json else format_text(r)
    if a.output:                   # written here, not through `>`: PowerShell 5.1 redirection writes UTF-16
        Path(a.output).write_text(text, encoding="utf-8")
        print(f"Report written to {a.output}")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
