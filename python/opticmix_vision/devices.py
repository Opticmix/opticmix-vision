"""Listing connected cameras — by name.

This camera reports no USB serial number. Indexes shift just by plugging in or
unplugging another camera, so picking a device by index silently opens a different
camera. Selection is therefore always by name.

Windows: parsing the ffmpeg output and picking by name run in the Rust core (`devices.rs`). Running ffmpeg,
and falling back to enumerating DirectShow directly, stay here in Python so a caller can swap that step.
`backend="msmf"` lists Media Foundation's video sources instead (Rust core), in the order OpenCV's CAP_MSMF
counts device indexes. Media Foundation listing: run with the camera once (one PC, one camera, 2026-11-24);
picking by name in that list (`resolve_by_name(backend="msmf")`): run without a camera only.

Linux: `find_v4l2_node()` reads `/sys/class/video4linux` (no extension needed). Linux name lookup:
implemented, not yet run on the camera.
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from opticmix_vision import _native

__all__ = [
    "DeviceInfo", "Mode", "NoMatch", "MultipleMatches",
    "list_devices", "list_modes", "resolve_by_name", "find_v4l2_node",
]

SYSFS_V4L = "/sys/class/video4linux"


@dataclass(frozen=True)
class DeviceInfo:
    index: int
    name: str


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


def _native_devices() -> list[str]:
    """Enumerates DirectShow directly, without ffmpeg (Windows, no dependency). Other OSes or a failure: empty list."""
    try:
        from ._ctrl_dshow import enumerate_video_devices
        return [n or "" for n in enumerate_video_devices()]
    except Exception:  # noqa: BLE001 — a failed enumeration shows up as "not found"
        return []


def _mf_devices() -> list[str]:
    """Media Foundation's video capture sources (Windows), names in the order OpenCV's CAP_MSMF counts device
    indexes — see `enumerate_mf_video_devices` in the Rust core's `devices.rs` for the source. Activates no
    device. Other OSes: empty list."""
    return list(_native.core().enumerate_mf_video_devices())


def _os_name(device: int | str, backend: str) -> str | None:
    """The camera's name as the OS lists it, read only: sysfs on Linux (OpenCV index N is /dev/videoN), the
    DirectShow list on Windows with backend "dshow", the Media Foundation list with backend "msmf" (OpenCV's
    indices for each follow its list). None when unknown."""
    try:
        if sys.platform.startswith("linux"):
            node = Path(device).name if isinstance(device, str) else f"video{device}"
            return (Path(SYSFS_V4L) / node / "name").read_text().strip()
        if sys.platform == "win32" and backend in ("dshow", "msmf") and isinstance(device, int):
            names = _native_devices() if backend == "dshow" else _mf_devices()
            return names[device] if 0 <= device < len(names) else None
    except (OSError, ValueError, RuntimeError):  # a name we cannot read is left out, never an error
        pass
    return None


def list_devices(*, backend: str = "dshow", _raw: str | None = None) -> list[DeviceInfo]:
    """Video devices in the order a Windows capture backend lists them — the index is the one OpenCV's backend of
    that name takes. Laptop webcams are included — the user has to pick among them, so nothing is filtered out.

    backend="dshow": DirectShow. ffmpeg is usually not installed; without it DirectShow is enumerated directly,
    in the same order as OpenCV. backend="msmf": Media Foundation's video sources (`MFEnumDeviceSources`, the
    list OpenCV's CAP_MSMF indexes). The two lists need not be in the same order."""
    if backend == "msmf":
        return [DeviceInfo(index=i, name=n) for i, n in enumerate(_mf_devices())]
    if backend != "dshow":
        raise ValueError(f"backend must be 'dshow' or 'msmf': {backend!r}")
    if _raw is None:
        try:
            _raw = _ffmpeg_devices()
        except (OSError, subprocess.SubprocessError):
            return [DeviceInfo(index=i, name=n) for i, n in enumerate(_native_devices())]
    return [DeviceInfo(index=i, name=n) for i, n in _native.core().list_devices(_raw)]


def resolve_by_name(needle: str, *, backend: str = "dshow", _raw: str | None = None) -> DeviceInfo:
    """Picks one device by partial name match (case-insensitive) from `list_devices(backend=...)`, Windows. Does
    not guess when it is ambiguous."""
    devices = list_devices(backend=backend, _raw=_raw)
    index, name = _native.core().pick_by_name(needle, [(d.index, d.name) for d in devices])
    return DeviceInfo(index=index, name=name)


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


def find_v4l2_node(needle: str, *, _sysfs: str | None = None) -> str:
    """Linux: the `/dev/videoN` of the one video capture node whose name contains `needle` (case-insensitive).

    A UVC camera gets more than one node with the same name (video and metadata); the capture node is the one
    whose sysfs `index` is 0. One match: its path. None: `NoMatch`, listing what is there. Two or more (two
    identical cameras): `MultipleMatches` — it does not open either. Linux name lookup: implemented, not yet
    run on the camera.
    """
    root = Path(_sysfs or SYSFS_V4L)
    nodes = []
    for d in sorted(root.glob("video*")):
        try:
            nodes.append((f"/dev/{d.name}", (d / "name").read_text().strip(), (d / "index").read_text().strip()))
        except OSError:          # a node that went away while we looked
            continue
    hits = [p for p, n, i in nodes if needle.lower() in n.lower() and i == "0"]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        seen = [f"{p} {n}" for p, n, _ in nodes]
        raise NoMatch(f"No video capture node matches {needle!r}. Video nodes found: {seen}")
    raise MultipleMatches(f"{len(hits)} video capture nodes match {needle!r} — they cannot be told apart "
                          f"(no USB serial number). Unplug all but one: {hits}")

