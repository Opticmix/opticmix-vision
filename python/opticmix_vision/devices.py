"""Listing connected cameras — by name, and by USB location.

This camera reports no USB serial number. Indexes shift just by plugging in or
unplugging another camera, so picking a device by index silently opens a different
camera. Selection is therefore by name — and, when two cameras share the name, by USB location
(`DeviceInfo.usb_location`, `resolve_by_name(usb_location=)`, `find_v4l2_node(usb_location=)`).

A USB location is not a serial number: it names the port the camera is plugged into, and it changes when the
camera is plugged into another port. Using two cameras at the same time is not supported. Picking a camera by USB
location has been run without a camera only (made-up device lists); it has not been run with a camera, and not run
with two cameras.

Windows: parsing the ffmpeg output and picking by name run in the Rust core (`devices.rs`). Running ffmpeg,
and falling back to enumerating DirectShow directly, stay here in Python so a caller can swap that step.
`backend="msmf"` lists Media Foundation's video sources instead (Rust core), in the order OpenCV's CAP_MSMF
counts device indexes. Media Foundation listing: run with the camera once (one PC, one camera, 2026-11-24);
picking by name in that list (`resolve_by_name(backend="msmf")`): run without a camera only.

Linux: `find_v4l2_node()` reads `/sys/class/video4linux` (no extension needed). Linux name lookup:
implemented, not yet run on the camera.
"""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from opticmix_vision import _native

__all__ = [
    "DeviceInfo", "Mode", "NoMatch", "MultipleMatches",
    "list_devices", "list_modes", "resolve_by_name", "pick_by_usb_location", "find_v4l2_node",
]

SYSFS_V4L = "/sys/class/video4linux"


@dataclass(frozen=True)
class DeviceInfo:
    index: int
    name: str
    usb_location: str | None = None     # where it is plugged in; None when the list cannot say


def _located(listed: list) -> list[tuple[str, str | None]]:
    """(name, USB location) pairs; a bare name is a device whose USB location is not known."""
    return [(x, None) if isinstance(x, str) else (x[0], x[1]) for x in listed]


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


def _native_devices() -> list[tuple[str, str | None]]:
    """(name, USB location) of each DirectShow video device, enumerated directly without ffmpeg (Windows, no
    dependency; `list_dshow_devices` in the Rust core's `devices.rs`). Other OSes or a failure: empty list."""
    if sys.platform != "win32":
        return []
    try:
        return [(n, loc) for _, n, loc in _native.core().list_dshow_devices()]
    except Exception:  # noqa: BLE001 — a failed enumeration shows up as "not found"
        return []


def _mf_devices() -> list[tuple[str, str | None]]:
    """Media Foundation's video capture sources (Windows): (name, USB location) in the order OpenCV's CAP_MSMF
    counts device indexes — see `list_mf_devices` in the Rust core's `devices.rs` for the source. Activates no
    device. Other OSes: empty list."""
    return [(n, loc) for _, n, loc in _native.core().list_mf_devices()]


def _os_name(device: int | str, backend: str) -> str | None:
    """The camera's name as the OS lists it, read only: sysfs on Linux (OpenCV index N is /dev/videoN), the
    DirectShow list on Windows with backend "dshow", the Media Foundation list with backend "msmf" (OpenCV's
    indices for each follow its list). None when unknown."""
    try:
        if sys.platform.startswith("linux"):
            node = Path(device).name if isinstance(device, str) else f"video{device}"
            return (Path(SYSFS_V4L) / node / "name").read_text().strip()
        if sys.platform == "win32" and backend in ("dshow", "msmf") and isinstance(device, int):
            names = [n for n, _ in _located(_native_devices() if backend == "dshow" else _mf_devices())]
            return names[device] if 0 <= device < len(names) else None
    except (OSError, ValueError, RuntimeError):  # a name we cannot read is left out, never an error
        pass
    return None


def list_devices(*, backend: str = "dshow", _raw: str | None = None) -> list[DeviceInfo]:
    """Video devices in the order a Windows capture backend lists them — the index is the one OpenCV's backend of
    that name takes. Laptop webcams are included — the user has to pick among them, so nothing is filtered out.
    Each carries its USB location when Windows reports one (`usb_location`, read from the device tree; no device
    is opened).

    backend="dshow": DirectShow. ffmpeg is usually not installed; without it DirectShow is enumerated directly,
    in the same order as OpenCV. backend="msmf": Media Foundation's video sources (`MFEnumDeviceSources`, the
    list OpenCV's CAP_MSMF indexes). The two lists need not be in the same order."""
    if backend == "msmf":
        return [DeviceInfo(i, n, loc) for i, (n, loc) in enumerate(_located(_mf_devices()))]
    if backend != "dshow":
        raise ValueError(f"backend must be 'dshow' or 'msmf': {backend!r}")
    if _raw is None:
        try:
            _raw = _ffmpeg_devices()
        except (OSError, subprocess.SubprocessError):
            return [DeviceInfo(i, n, loc) for i, (n, loc) in enumerate(_located(_native_devices()))]
    return [DeviceInfo(i, n, loc) for i, n, loc in _native.core().list_devices(_raw)]


def resolve_by_name(needle: str, *, usb_location: str | None = None, backend: str = "dshow",
                    _raw: str | None = None) -> DeviceInfo:
    """Picks one device by partial name match (case-insensitive) from `list_devices(backend=...)`, Windows. Does
    not guess when it is ambiguous. With `usb_location`, only the device at that USB location counts
    (`pick_by_usb_location`; an empty or blank one is `ValueError`). Picking a camera by USB
    location has been run without a camera only (made-up device lists); it has not been run with a camera, and not
    run with two cameras."""
    devices = list_devices(backend=backend, _raw=_raw)
    if usb_location is not None:
        return pick_by_usb_location(needle, usb_location, devices)
    return DeviceInfo(*_native.core().pick_by_name(needle, [(d.index, d.name, d.usb_location) for d in devices]))


def pick_by_usb_location(needle: str, usb_location: str, devices: list[DeviceInfo]) -> DeviceInfo:
    """The one device in `devices` whose name contains `needle` (case-insensitive) and whose `usb_location` is
    `usb_location` (ignoring case) — how to choose between cameras that share a name. `NoMatch` lists each
    device's name and USB location; more than one is `MultipleMatches`; an empty or blank `usb_location` is
    `ValueError`. A USB location is not a serial number: it names the port the camera is plugged into, and it
    changes when the camera is plugged into another port. Picking a camera by USB
    location has been run without a camera only (made-up device lists); it has not been run with a camera, and not
    run with two cameras."""
    return DeviceInfo(*_native.core().pick_by_usb_location(
        needle, usb_location, [(d.index, d.name, d.usb_location) for d in devices]))


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


def _sysfs_usb_location(node: Path) -> str | None:
    """The sysfs name of the USB device a video node belongs to (e.g. `1-4.2`): of `device` (the USB interface) and
    its parents, the nearest with an `idVendor` file (four in all, as the doctor looks). None when not on USB."""
    real = Path(os.path.realpath(node / "device"))
    return next((p.name for p in [real, *real.parents][:4] if (p / "idVendor").is_file()), None)


def find_v4l2_node(needle: str, *, usb_location: str | None = None, _sysfs: str | None = None) -> str:
    """Linux: the `/dev/videoN` of the one video capture node whose name contains `needle` (case-insensitive).

    A UVC camera gets more than one node with the same name (video and metadata); the capture node is the one
    whose sysfs `index` is 0. One match: its path. None: `NoMatch`, listing what is there. Two or more (two
    identical cameras): `MultipleMatches` — it does not open either; when every match has a USB location, the
    message lists them. With `usb_location` (the USB device's sysfs name, e.g. `1-4.2`, compared ignoring case),
    only the node at that USB location counts; an empty or blank one is `ValueError`. Linux name lookup:
    implemented, not yet run on the camera. Picking a camera by USB
    location has been run without a camera only (made-up device lists); it has not been run with a camera, and not
    run with two cameras.
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

