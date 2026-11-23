"""Screen colors and fonts, in one place.

Dark background, lime accent, monospace font. Lime is from our brand color family.
"""
from __future__ import annotations

__all__ = ["BG", "PANEL", "VIDEO_BG", "LIME", "TEXT", "TEXT_DIM", "TRACK", "ORANGE", "RED", "MONO", "MONO_SMALL"]

BG = "#1e1e1e"          # window background
PANEL = "#2a2a2a"       # top bar, bottom bar
VIDEO_BG = "#000000"    # video letterbox
LIME = "#d4ff3f"        # accent: active tab, slider, icons, values
TEXT = "#e8e8e8"
TEXT_DIM = "#8a8a8a"    # inactive row
TRACK = "#4a4a4a"       # empty part of the slider
ORANGE = "#f2a33a"      # write-count warning
RED = "#ff5c5c"         # write refused by the driver

MONO = ("Consolas", 10)
MONO_SMALL = ("Consolas", 9)
