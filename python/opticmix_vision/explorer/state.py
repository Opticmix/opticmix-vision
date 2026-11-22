"""선택 상태와 설정 창의 행 만들기. Tk 를 import 하지 않으므로 화면 없이 테스트된다."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

from ..controls import CONTROLS, ControlInfo
from ..devices import DeviceInfo, Mode

__all__ = ["ExplorerState", "KNOWN_MODE", "LED_MAX", "LED_ONSET", "Row", "build_rows", "locked", "value_text",
           "banner_text", "reset_lines", "MONO_NOTE", "RANGE_NOTE"]

# 실측값 — 2개체에서 재현됐다. 0..16 은 소등, 24 에서 점등, 96 초과는 무시된다.
LED_MAX = 96
LED_ONSET = 24

# 모드를 알아내지 못했을 때 물러설 곳. 51분 연속 90.5 fps 로 확인된 모드다.
KNOWN_MODE = Mode(fourcc="MJPG", width=1600, height=800, max_fps=90.0)


@dataclass
class ExplorerState:
    device: DeviceInfo | None = None
    mode: Mode | None = None
    modes: list[Mode] = field(default_factory=list)
    mode_note: str = ""

    def select_device(self, device: DeviceInfo) -> None:
        """장치를 바꾸면 모드는 비운다 — 이전 장치의 모드는 새 장치의 것이 아니다."""
        self.device = device
        self.mode = None
        self.modes = []
        self.mode_note = ""

    def set_modes(self, modes: list[Mode]) -> None:
        """빈 목록은 '모드가 없다' 가 아니라 '알아내지 못했다' 이다."""
        if modes:
            self.modes = list(modes)
            self.mode_note = ""
        else:
            self.modes = [KNOWN_MODE]
            self.mode_note = "Could not read the mode list (needs ffmpeg) — opening 1600×800 MJPG, the mode verified on the Weevia S1"

    def select_mode(self, mode: Mode) -> None:
        self.mode = mode

    @property
    def can_stream(self) -> bool:
        return self.device is not None and self.mode is not None

    @staticmethod
    def illuminator_label(value: float) -> str:
        """24 아래는 숫자가 아니라 '꺼짐' 이라고 말한다 — 슬라이더가 움직이는데
        화면이 그대로인 이유를 사용자가 짐작하지 않게."""
        return "off" if value < LED_ONSET else f"{value:g}"


MONO_NOTE = "no effect on mono sensor"
RANGE_NOTE = "range not reported — set it from code"
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
    """이름 표 순서, 단 모드(auto_*)는 그 값 바로 위."""
    names = [c.name for c in CONTROLS]
    for mode, (value, _on) in LOCKS.items():
        names.remove(mode)
        names.insert(names.index(value), mode)
    return names


def build_rows(infos: Mapping[str, ControlInfo]) -> tuple[list[Row], list[Row]]:
    """(주 행, 흑백 무효 행). describe() 결과에서 만든다 — 범위는 장치가 준 것만 쓴다."""
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
    """모드가 자동이라 손댈 수 없는 값 이름."""
    return {value for mode, (value, on) in LOCKS.items() if values.get(mode) == on}


def value_text(name: str, value: Any) -> str:
    if value is None:
        return "?"
    if name == "backlight_compensation":
        return ExplorerState.illuminator_label(float(value))
    if isinstance(value, bool):
        return "on" if value else "off"
    return f"{value:g}" if isinstance(value, (int, float)) else str(value)


def banner_text(backend_name: str, backend_note: str) -> str:
    if backend_name == "dshow":
        return ""
    if backend_name == "sim":
        return "Simulated controls — ranges and defaults are made up"
    return f"Limited controls (OpenCV fallback): {backend_note}"


def reset_lines(plan: Mapping[str, tuple[Any, Any]]) -> list[str]:
    if not plan:
        return ["Everything is already at the device defaults."]
    lines = [f"{n}  {value_text(n, cur)} → {value_text(n, dflt)}" for n, (cur, dflt) in plan.items()]
    n = len(plan)
    lines.append(f"This will write to the camera {n} time{'s' if n != 1 else ''}.")
    return lines
