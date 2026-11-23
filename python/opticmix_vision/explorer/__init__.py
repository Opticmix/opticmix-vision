"""Weevia S1 Explorer: a window to see whether the camera works.

The window itself (`app`) imports Tk, so it is not pulled in here ahead of time. The state object
must be usable without a display, and tests run only through that path.
"""
from __future__ import annotations

__all__ = ["main"]


def main(argv=None) -> int:
    from .app import main as _main
    return _main(argv)
