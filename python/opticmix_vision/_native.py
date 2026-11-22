"""Loads the compiled extension (`opticmix_vision._core`) — the package still imports without it.

A source checkout on the path has the Python files but not the extension. Importing must not fail there,
because frame capture does not need it. What does need it (camera controls, device listing) raises
`ControlsUnavailable` when it is actually used, and says what to install.
"""
from __future__ import annotations

from typing import Any

try:
    from opticmix_vision import _core as _ext
    IMPORT_ERROR: BaseException | None = None
except ImportError as exc:  # a source tree with no wheel installed
    _ext = None
    IMPORT_ERROR = exc

MISSING = ("opticmix_vision._core (the compiled extension) is not installed — pip install the opticmix-vision wheel"
           " from the release page; a source checkout on its own does not contain it")


def available() -> bool:
    return _ext is not None


def core() -> Any:
    """The extension module. Raises ControlsUnavailable (with install instructions) when it is missing."""
    if _ext is None:
        from opticmix_vision.controls import ControlsUnavailable
        raise ControlsUnavailable(f"{MISSING} [{IMPORT_ERROR}]")
    return _ext
