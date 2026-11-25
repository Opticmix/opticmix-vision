"""Selection state and building the settings window rows. Does not import Tk, so it is tested without a display."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

from ..controls import CONTROLS, DEFAULTS_NOT_WRITTEN, ControlInfo
from ..devices import DeviceInfo, Mode

__all__ = ["ExplorerState", "KNOWN_MODE", "LED_MAX", "LED_ONSET", "Row", "build_rows", "locked", "value_text",
           "banner_text", "reset_lines", "MONO_NOTE", "RANGE_NOTE", "READ_ONLY_NOTE"]

# Measured: values above 96 are ignored (two units); 0..16 is off and it lights at 24 (one unit).
LED_MAX = 96
LED_ONSET = 24

# Fallback when the modes could not be determined. This mode was confirmed at 90.5 fps for 51 minutes straight.
KNOWN_MODE = Mode(fourcc="MJPG", width=1600, height=800, max_fps=90.0)


@dataclass
class ExplorerState:
    device: DeviceInfo | None = None
    mode: Mode | None = None
    modes: list[Mode] = field(default_factory=list)
    mode_note: str = ""

    def select_device(self, device: DeviceInfo) -> None:
        """Changing the device clears the mode; the previous device's mode does not belong to the new device."""
        self.device = device
        self.mode = None
        self.modes = []
        self.mode_note = ""

    def set_modes(self, modes: list[Mode]) -> None:
        """An empty list means 'could not determine', not 'there are no modes'."""
        if modes:
            self.modes = list(modes)
            self.mode_note = ""
        else:
            self.modes = [KNOWN_MODE]
            self.mode_note = "Could not read the mode list (needs ffmpeg) — opening 1600×800 MJPG"

    def select_mode(self, mode: Mode) -> None:
        self.mode = mode

    @property
    def can_stream(self) -> bool:
        return self.device is not None and self.mode is not None

    @staticmethod
    def illuminator_label(value: float) -> str:
        """Below 24 it says 'off' instead of a number, so the user does not have to guess why the
        slider moves but the image stays the same."""
        return "off" if value < LED_ONSET else f"{value:g}"


MONO_NOTE = "no effect on mono sensor"
RANGE_NOTE = "range not reported — set it from code"
# Linux: the Settings window shows every control and writes none (its controls and Reset/Load preset are disabled).
READ_ONLY_NOTE = "Camera controls on Linux are not verified and not supported — shown, not written"
LABELS = {
    "backlight_compensation": "IR LED (BACKLIGHT)", "auto_exposure": "AUTO EXPOSURE",
    "auto_exposure_priority": "LOW-LIGHT COMP.", "power_line_frequency": "POWER LINE",
    "auto_white_balance": "AUTO WB", "white_balance": "WHITE BALANCE",
}
LOCKS = {"auto_exposure": ("exposure", "auto"), "auto_white_balance": ("white_balance", True)}


@dataclass(frozen=True)
class Row:
    name: str
    label: str
    widget: Literal["slider", "check", "menu", "readonly"]
    minimum: int | None = None
    maximum: int | None = None
    choices: tuple[str, ...] = ()
    mono: bool = False


def _order() -> list[str]:
    """Name-table order, except that a mode (auto_*) goes right above its value."""
    names = [c.name for c in CONTROLS]
    for mode, (value, _on) in LOCKS.items():
        names.remove(mode)
        names.insert(names.index(value), mode)
    return names


def build_rows(infos: Mapping[str, ControlInfo]) -> tuple[list[Row], list[Row]]:
    """(main rows, rows with no effect on a monochrome sensor). Built from describe() results; only ranges the device reported are used."""
    main: list[Row] = []
    mono: list[Row] = []
    for name in _order():
        info = infos.get(name)
        if info is None:
            continue
        if info.kind == "menu":
            widget = "menu"
        elif info.kind == "bool":
            widget = "check"
        elif info.minimum is not None and info.maximum is not None:
            widget = "slider"
        else:
            widget = "readonly"
        row = Row(name, LABELS.get(name, name.upper().replace("_", " ")), widget, info.minimum, info.maximum,
                  tuple(info.choices or ()), info.effect == "none_expected_mono")
        (mono if row.mono else main).append(row)
    return main, mono


def locked(values: Mapping[str, Any]) -> set[str]:
    """Names of values that cannot be changed because their mode is auto."""
    return {value for mode, (value, on) in LOCKS.items() if values.get(mode) == on}


def value_text(name: str, value: Any) -> str:
    if value is None:
        return "?"
    if name == "backlight_compensation":
        return ExplorerState.illuminator_label(float(value))
    if isinstance(value, bool):
        return "on" if value else "off"
    return f"{value:g}" if isinstance(value, (int, float)) else str(value)


def banner_text(backend_name: str, backend_note: str, read_only: bool = False) -> str:
    """The line over the rows; read_only (Linux) adds why nothing can be changed and why Reset is off."""
    if backend_name == "dshow":
        text = ""
    elif backend_name == "sim":
        text = "Simulated controls — ranges and defaults are made up"
    else:
        text = f"Limited controls (OpenCV fallback): {backend_note}"
    lines = [text, READ_ONLY_NOTE, DEFAULTS_NOT_WRITTEN] if read_only else [text]
    return "\n".join(line for line in lines if line)


def reset_lines(plan: Mapping[str, tuple[Any, Any]], *, nothing: str = "Everything is already at the device defaults."
                ) -> list[str]:
    """One line per change, then how many writes: a mode and its value (one group) are one write."""
    if not plan:
        return [nothing]
    lines = [f"{n}  {value_text(n, cur)} → {value_text(n, dflt)}" for n, (cur, dflt) in plan.items()]
    group = {c.name: c.key for c in CONTROLS}
    n = len({group.get(name, name) for name in plan})
    lines.append(f"This will write to the camera {n} time{'s' if n != 1 else ''}.")
    return lines
