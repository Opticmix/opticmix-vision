"""Listing connected cameras — by name.

This camera reports no USB serial number. Indexes shift just by plugging in or
unplugging another camera, so picking a device by index silently opens a different
camera. Selection is therefore always by name.

Windows: parsing the ffmpeg output and picking by name run in the Rust core (`devices.rs`). Running ffmpeg,
and falling back to enumerating DirectShow directly, stay here in Python so a caller can swap that step.

Linux: `find_v4l2_node()` reads `/sys/class/video4linux` (no extension needed). Linux name lookup:
implemented, not yet run on the camera.
"""
from __future__ import annotations

import subprocess
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


def list_devices(*, _raw: str | None = None) -> list[DeviceInfo]:
    """Video devices in DirectShow enumeration order (Windows). Laptop webcams are included — the user has to
    pick among them, so nothing is filtered out.

    ffmpeg is usually not installed; without it DirectShow is enumerated directly, in the same order as OpenCV."""
    if _raw is None:
        try:
            _raw = _ffmpeg_devices()
        except (OSError, subprocess.SubprocessError):
            return [DeviceInfo(index=i, name=n) for i, n in enumerate(_native_devices())]
    return [DeviceInfo(index=i, name=n) for i, n in _native.core().list_devices(_raw)]


def resolve_by_name(needle: str, *, _raw: str | None = None) -> DeviceInfo:
    """Picks one device by partial name match (case-insensitive), Windows. Does not guess when it is ambiguous."""
    devices = list_devices(_raw=_raw)
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

