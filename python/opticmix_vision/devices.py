"""Listing connected cameras — by name, and by USB location.

This camera reports no USB serial number. Indexes shift just by plugging in or
unplugging another camera, so picking a device by index silently opens a different
camera. Selection is therefore by name — and, when two cameras share the name, by USB location
(`DeviceInfo.usb_location`, `resolve_by_name(usb_location=)`, `find_v4l2_node(usb_location=)`).

A USB location is not a serial number: it names the port the camera is plugged into, and it changes when the
camera is plugged into another port. Using two cameras at the same time is not supported. Picking a camera by USB
location has been run with the camera once (one PC, one camera, 2026-11-25): with one camera connected, the list showed
its USB location, and given that location it opened at 1600x800 MJPG and read one frame; a location no device has was
refused (Python DirectShow, Rust Media Foundation); not run with two cameras.

Windows: parsing the ffmpeg output and picking by name run in the Rust core (`devices.rs`). Running ffmpeg,
and falling back to enumerating DirectShow directly, stay here in Python so a caller can swap that step.
`backend="msmf"` lists Media Foundation's video sources instead (Rust core), in the order OpenCV's CAP_MSMF
counts device indexes. Media Foundation listing: run with the camera on one PC (2026-11-24, and four more runs on
2026-11-25; one camera connected in each run, not known to be the same unit in all of them). Picking by name in that list (`resolve_by_name(backend="msmf")`): on 2026-11-25 it found
the camera by name and `examples/measure_fps.py --backend msmf` opened it at 1600x800, in two USB re-insert cycles
(one PC, one camera, the published 0.0.7 wheel).

Linux: `find_v4l2_node()` and `list_devices(backend="v4l2")` (the default there) read `/sys/class/video4linux`
(no extension needed). Linux name lookup: run with the camera once (one PC, one camera, 2026-11-25).

Each listed device also carries its USB IDs (`DeviceInfo.vid`, `pid`, `bcd_device`) and link speed
(`DeviceInfo.link_speed`), read without opening the device — the values the doctor prints for it. Windows: in the
Rust core, from the device tree and the USB hub driver. Linux: from sysfs (`idVendor`, `idProduct`, `bcdDevice`,
`speed`), by the function the doctor uses. `resolve_by_name()`, which `DeviceConfig.weevia_s1()` uses to find
the camera before opening it on Windows, and `find_v4l2_node()`, which it uses on Linux, read the USB location only,
as 0.0.6 did. The camera reports no USB serial number, no firmware version and no temperature, so there are no
fields for them. Reading the USB IDs and the link speed into these fields has been run with the camera in two USB
re-insert cycles (one Weevia S1 unit, Windows 11, one PC, two-level USB hub, 2026-11-25, the published 0.0.7 wheel;
not opened); on Linux run without a camera only (made-up sysfs trees).
"""
from __future__ import annotations

import os
import subprocess
import sys
from contextvars import ContextVar
from dataclasses import astuple, dataclass
from pathlib import Path

from opticmix_vision import _native
from opticmix_vision.controls import ControlsUnavailable

__all__ = [
    "DeviceInfo", "Mode", "NoMatch", "MultipleMatches",
    "list_devices", "list_modes", "resolve_by_name", "pick_by_usb_location", "find_v4l2_node",
]

SYSFS_V4L = "/sys/class/video4linux"
_LINUX_SPEED = {"1.5": "Low", "12": "Full", "480": "High", "5000": "Super"}   # sysfs `speed`, Mb/s


@dataclass(frozen=True)
class DeviceInfo:
    index: int
    name: str
    usb_location: str | None = None     # where it is plugged in; None when the list cannot say
    vid: int | None = None              # USB vendor ID (idVendor); None when it cannot be read
    pid: int | None = None              # USB product ID (idProduct)
    bcd_device: int | None = None       # USB device revision (bcdDevice) — not a firmware version
    link_speed: str | None = None       # "Low", "Full", "High", "Super" or "Super+": the hub's (Linux: sysfs) word


def _located(listed: list) -> list[tuple]:
    """The fields after `index` of each entry: (name, USB location, vid, pid, bcd_device, link_speed) as the Rust core
    lists them, or shorter; a bare name is a device of which nothing else is known."""
    return [(x,) if isinstance(x, str) else tuple(x) for x in listed]


@dataclass(frozen=True)
class Mode:
    fourcc: str
    width: int
    height: int
    max_fps: float

    def label(self) -> str:
        """The text a combo box shows."""
        return f"{self.width}×{self.height} {self.fourcc} · {self.max_fps:g} fps"


class NoMatch(LookupError):
    """No camera matches the name."""


class MultipleMatches(LookupError):
    """More than one camera matches the name — with no serial number they cannot be told apart."""


def _ffmpeg_devices() -> str:
    return subprocess.run(
        ["ffmpeg", "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
        capture_output=True, encoding="utf-8", errors="replace",
    ).stderr


def _ffmpeg_options(name: str) -> str:
    return subprocess.run(
        ["ffmpeg", "-hide_banner", "-f", "dshow", "-list_options", "true", "-i", f"video={name}"],
        capture_output=True, encoding="utf-8", errors="replace",
    ).stderr


# False while resolve_by_name lists: finding a camera to open reads the USB location only, as 0.0.6 did — no hardware
# IDs and no hub query in the steps before opening.
_IDS_AND_LINK: ContextVar[bool] = ContextVar("opticmix_vision_devices_ids_and_link", default=True)
STALE_CORE = ("opticmix_vision._core was built from an older source than these Python files (its device lists give "
              "no USB IDs or link speed): rebuild it — pip install the opticmix-vision wheel of this version, or build "
              "the extension again from this source")


class _StaleCore(ControlsUnavailable):
    pass


def _core_rows(name: str, *args: object) -> list[tuple]:
    """The rows of one of the Rust core's device lists: (index, name, USB location, vid, pid, bcd_device, link_speed).
    An extension built before these fields (no `ids_and_link`, rows of three) is `ControlsUnavailable`, not a
    TypeError."""
    try:
        rows = getattr(_native.core(), name)(*args, ids_and_link=_IDS_AND_LINK.get())
    except TypeError as exc:
        raise _StaleCore(f"{STALE_CORE} [{exc}]") from exc
    if any(len(r) != 7 for r in rows):
        raise _StaleCore(STALE_CORE)
    return rows


def _native_devices() -> list[tuple]:
    """(name, USB location, vid, pid, bcd_device, link_speed) of each DirectShow video device, enumerated directly
    without ffmpeg (Windows, no dependency; `list_dshow_devices` in the Rust core's `devices.rs`). Other OSes or a
    failure: empty list; an extension too old for these Python files: `ControlsUnavailable`."""
    if sys.platform != "win32":
        return []
    try:
        return [row[1:] for row in _core_rows("list_dshow_devices")]
    except _StaleCore:
        raise
    except Exception:  # noqa: BLE001 — a failed enumeration shows up as "not found"
        return []


def _mf_devices() -> list[tuple]:
    """Media Foundation's video capture sources (Windows): (name, USB location, vid, pid, bcd_device, link_speed) in
    the order OpenCV's CAP_MSMF counts device indexes — see `list_mf_devices` in the Rust core's `devices.rs` for the
    source. Activates no device. Other OSes: empty list."""
    return [row[1:] for row in _core_rows("list_mf_devices")]


def _os_name(device: int | str, backend: str) -> str | None:
    """The camera's name as the OS lists it, read only: sysfs on Linux (OpenCV index N is /dev/videoN), the
    DirectShow list on Windows with backend "dshow", the Media Foundation list with backend "msmf" (OpenCV's
    indices for each follow its list). None when unknown."""
    try:
        if sys.platform.startswith("linux"):
            node = Path(device).name if isinstance(device, str) else f"video{device}"
            return (Path(SYSFS_V4L) / node / "name").read_text().strip()
        if sys.platform == "win32" and backend in ("dshow", "msmf") and isinstance(device, int):
            names = [x[0] for x in _located(_native_devices() if backend == "dshow" else _mf_devices())]
            return names[device] if 0 <= device < len(names) else None
    except (OSError, ValueError, RuntimeError):  # a name we cannot read is left out, never an error
        pass
    return None


def list_devices(*, backend: str | None = None, _raw: str | None = None,
                 _sysfs: str | None = None) -> list[DeviceInfo]:
    """Video devices in the order a capture backend lists them — the index is the one OpenCV's backend of that name
    takes. Laptop webcams are included — the user has to pick among them, so nothing is filtered out. Each carries
    its USB location, USB IDs and link speed when the OS reports them (`usb_location`, `vid`, `pid`, `bcd_device`,
    `link_speed`: read as the doctor reads them; no device is opened).

    backend None: "v4l2" on Linux, "dshow" elsewhere. backend="dshow": DirectShow. ffmpeg is usually not installed;
    without it DirectShow is enumerated directly, in the same order as OpenCV. backend="msmf": Media Foundation's
    video sources (`MFEnumDeviceSources`, the list OpenCV's CAP_MSMF indexes). The two lists need not be in the same
    order; both read the device tree and the USB hub driver. backend="v4l2" (Linux): the video capture nodes in
    `/sys/class/video4linux`, chosen as `find_v4l2_node` chooses them (sysfs `index` 0, so a UVC camera's metadata
    node is left out), in /dev/videoN order; the index is N, the one OpenCV's CAP_V4L2 opens (`DeviceConfig(device=N,
    backend="v4l2")`); the USB location is the USB device's sysfs name (e.g. `2-1`), and the IDs and link speed are
    its `idVendor`, `idProduct`, `bcdDevice` and `speed`. Empty off Linux. The Linux list has been run without a
    camera only (made-up sysfs trees); not run with the camera on Linux."""
    if backend is None:
        backend = "v4l2" if sys.platform.startswith("linux") else "dshow"
    if backend == "v4l2":
        return _v4l2_devices(Path(_sysfs or SYSFS_V4L)) if _sysfs or sys.platform.startswith("linux") else []
    if backend == "msmf":
        return [DeviceInfo(i, *row) for i, row in enumerate(_located(_mf_devices()))]
    if backend != "dshow":
        raise ValueError(f"backend must be 'dshow', 'msmf' or 'v4l2': {backend!r}")
    if _raw is None:
        try:
            _raw = _ffmpeg_devices()
        except (OSError, subprocess.SubprocessError):
            return [DeviceInfo(i, *row) for i, row in enumerate(_located(_native_devices()))]
    return [DeviceInfo(*row) for row in _core_rows("list_devices", _raw)]


def resolve_by_name(needle: str, *, usb_location: str | None = None, backend: str = "dshow",
                    _raw: str | None = None) -> DeviceInfo:
    """Picks one device by partial name match (case-insensitive) from `list_devices(backend=...)`, Windows. Does
    not guess when it is ambiguous. With `usb_location`, only the device at that USB location counts
    (`pick_by_usb_location`; an empty or blank one is `ValueError`). It reads the USB location only, as 0.0.6 did
    (`DeviceConfig.weevia_s1()` finds the camera through it before opening), so `vid`, `pid`, `bcd_device` and
    `link_speed` of what it returns are None — `list_devices()` has them. Picking a camera by USB location has been run
    with the camera once (one PC, one camera, 2026-11-25): with one camera connected, the list showed its USB location,
    and given that location it opened at 1600x800 MJPG and read one frame; a location no device has was refused (Python
    DirectShow, Rust Media Foundation); not run with two cameras."""
    token = _IDS_AND_LINK.set(False)
    try:
        devices = list_devices(backend=backend, _raw=_raw)
    finally:
        _IDS_AND_LINK.reset(token)
    if usb_location is not None:
        return pick_by_usb_location(needle, usb_location, devices)
    return DeviceInfo(*_native.core().pick_by_name(needle, [astuple(d) for d in devices]))


def pick_by_usb_location(needle: str, usb_location: str, devices: list[DeviceInfo]) -> DeviceInfo:
    """The one device in `devices` whose name contains `needle` (case-insensitive) and whose `usb_location` is
    `usb_location` (ignoring case) — how to choose between cameras that share a name. `NoMatch` lists each
    device's name and USB location; more than one is `MultipleMatches`; an empty or blank `usb_location` is
    `ValueError`. A USB location is not a serial number: it names the port the camera is plugged into, and it
    changes when the camera is plugged into another port. Picking a camera by USB location has been run with the camera
    once (one PC, one camera, 2026-11-25): with one camera connected, the list showed its USB location, and given that
    location it opened at 1600x800 MJPG and read one frame; a location no device has was refused (Python DirectShow,
    Rust Media Foundation); not run with two cameras."""
    return DeviceInfo(*_native.core().pick_by_usb_location(needle, usb_location, [astuple(d) for d in devices]))


def list_modes(name: str, *, _raw: str | None = None) -> list[Mode]:
    """The modes the device reports itself (Windows, through ffmpeg).

    An empty list does not mean "no modes" — it means **"could not find out"**. A caller should fall back
    to one known mode, not say that there are no modes.
    """
    if _raw is None:
        try:
            _raw = _ffmpeg_options(name)
        except (OSError, subprocess.SubprocessError):
            return []
    return [Mode(fourcc=f, width=w, height=h, max_fps=fps) for f, w, h, fps in _native.core().list_modes(name, _raw)]


def _sysfs_usb_device(node: Path) -> Path | None:
    """The sysfs folder of the USB device a video node belongs to: of `device` (the USB interface) and its parents,
    the nearest with an `idVendor` file (four in all). Its name is the USB location (e.g. `1-4.2`). None when not on
    USB. The doctor uses it too."""
    real = Path(os.path.realpath(node / "device"))
    return next((p for p in [real, *real.parents][:4] if (p / "idVendor").is_file()), None)


def _sysfs_usb_location(node: Path) -> str | None:
    """The sysfs name of the USB device a video node belongs to (e.g. `1-4.2`). None when not on USB."""
    usb = _sysfs_usb_device(node)
    return None if usb is None else usb.name


def _sysfs_usb_ids(usb: Path) -> tuple[int | None, int | None, int | None, str | None]:
    """(vid, pid, bcd_device, link_speed) of a sysfs USB device folder, as `DeviceInfo` holds them: `idVendor`,
    `idProduct` and `bcdDevice` in hex, and `speed` (Mb/s) named as the Windows lists name a link: 1.5 Low, 12 Full,
    480 High, 5000 Super, more than 5000 Super+. None for a value that cannot be read. The doctor prints these."""
    def read(name: str) -> str:
        try:
            return (usb / name).read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return ""

    def hex_(name: str) -> int | None:
        try:
            return int(read(name), 16)
        except ValueError:
            return None
    mbps = read("speed")
    speed = _LINUX_SPEED.get(mbps) or ("Super+" if mbps.isdigit() and int(mbps) > 5000 else None)
    return hex_("idVendor"), hex_("idProduct"), hex_("bcdDevice"), speed


def _v4l2_devices(root: Path) -> list[DeviceInfo]:
    """`list_devices(backend="v4l2")`: one DeviceInfo per video capture node under `root`, in /dev/videoN order."""
    out = []
    for n, d in sorted((int(d.name[5:]), d) for d in root.glob("video*") if d.name[5:].isdigit()):
        try:
            name, index = (d / "name").read_text().strip(), (d / "index").read_text().strip()
        except OSError:          # a node that went away while we looked
            continue
        if index == "0":         # the capture node, as find_v4l2_node picks it
            usb = _sysfs_usb_device(d)
            out.append(DeviceInfo(n, name) if usb is None else DeviceInfo(n, name, usb.name, *_sysfs_usb_ids(usb)))
    return out


def find_v4l2_node(needle: str, *, usb_location: str | None = None, _sysfs: str | None = None) -> str:
    """Linux: the `/dev/videoN` of the one video capture node whose name contains `needle` (case-insensitive).

    A UVC camera gets more than one node with the same name (video and metadata); the capture node is the one
    whose sysfs `index` is 0. One match: its path. None: `NoMatch`, listing what is there. Two or more (two
    identical cameras): `MultipleMatches` — it does not open either; when every match has a USB location, the
    message lists them. With `usb_location` (the USB device's sysfs name, e.g. `1-4.2`, compared ignoring case),
    only the node at that USB location counts; an empty or blank one is `ValueError`. Linux name lookup: run with
    the camera once (one PC, one camera, 2026-11-25). Picking by USB location: run on Linux with one camera once
    (opened at its location; a location with no camera refused); not run with two cameras.
    """
    if usb_location is not None and not usb_location.strip():
        raise ValueError("usb_location is empty: pass a USB location as the device list or the doctor prints it")
    root = Path(_sysfs or SYSFS_V4L)
    nodes = []
    for d in sorted(root.glob("video*")):
        try:
            nodes.append((f"/dev/{d.name}", (d / "name").read_text().strip(), (d / "index").read_text().strip(),
                          _sysfs_usb_location(d)))
        except OSError:          # a node that went away while we looked
            continue
    hits = [(p, loc) for p, n, i, loc in nodes if needle.lower() in n.lower() and i == "0"
            and (usb_location is None or (loc is not None and loc.lower() == usb_location.lower()))]
    if len(hits) == 1:
        return hits[0][0]
    if not hits and usb_location is not None:
        seen = [(f"{p} {n}", loc) for p, n, _, loc in nodes]
        none = ". None: the USB location could not be read" if any(loc is None for *_, loc in nodes) else ""
        raise NoMatch(f"No video capture node matches {needle!r} at USB location {usb_location!r}. Video nodes found "
                      f"(node, USB location): {seen}{none}")
    if not hits:
        seen = [f"{p} {n}" for p, n, _, _ in nodes]
        raise NoMatch(f"No video capture node matches {needle!r}. Video nodes found: {seen}")
    if usb_location is None and all(loc for _, loc in hits):
        raise MultipleMatches(f"{len(hits)} video capture nodes match {needle!r} — they cannot be told apart by name "
                              "(no USB serial number). Unplug all but one, or pick one by USB location (usb_location: "
                              f"the port it is plugged into, not a serial number): {hits}")
    at = "" if usb_location is None else f" at USB location {usb_location!r}"
    raise MultipleMatches(f"{len(hits)} video capture nodes match {needle!r}{at} — they cannot be told apart "
                          f"(no USB serial number). Unplug all but one: {[p for p, _ in hits]}")

