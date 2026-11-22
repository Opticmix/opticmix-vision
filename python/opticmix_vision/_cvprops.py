"""OpenCV CAP_PROP 이름 ↔ 제어 이름 — 옛 `set_prop`/`get_prop` 호환 전용.

CAP_PROP_EXPOSURE 는 예전처럼 알아서 수동으로 바꾼다(controls.set("exposure") 는 자동 중이면 거부한다).
CAP_PROP_AUTO_EXPOSURE 숫자는 백엔드마다 뜻이 다르다 — DirectShow: cvRound(v)==1 이면 자동(0.75 관례),
V4L2: 3 = 자동(aperture priority), 1 = 수동.
"""
from __future__ import annotations

from typing import Any

__all__ = ["CONTROL_PROPS", "prop_name", "to_controls", "from_controls", "ae_is_auto", "ae_number"]

_VALUE: dict[str, str] = {
    "CAP_PROP_BRIGHTNESS": "brightness", "CAP_PROP_CONTRAST": "contrast", "CAP_PROP_HUE": "hue",
    "CAP_PROP_SATURATION": "saturation", "CAP_PROP_SHARPNESS": "sharpness", "CAP_PROP_GAMMA": "gamma",
    "CAP_PROP_GAIN": "gain", "CAP_PROP_BACKLIGHT": "backlight_compensation",
    "CAP_PROP_EXPOSURE": "exposure", "CAP_PROP_WB_TEMPERATURE": "white_balance",
}
CONTROL_PROPS = frozenset(_VALUE) | {"CAP_PROP_AUTO_EXPOSURE", "CAP_PROP_AUTO_WB"}


def prop_name(prop: str | int) -> str:
    """이름은 그대로, 정수 ID 는 cv2 이름으로(제어 이름 먼저)."""
    if isinstance(prop, str):
        return prop
    import cv2
    for name in sorted(CONTROL_PROPS):
        if getattr(cv2, name, None) == int(prop):
            return name
    for name in dir(cv2):
        if name.startswith("CAP_PROP_") and getattr(cv2, name) == int(prop):
            return name
    return f"PROP_{int(prop)}"


def ae_is_auto(value: float, flavor: str) -> bool:
    return round(float(value)) == (3 if flavor == "v4l2" else 1)


def ae_number(auto: bool | None, flavor: str) -> float:
    if auto is None:
        return -1.0
    if flavor == "v4l2":
        return 3.0 if auto else 1.0
    return 0.75 if auto else 0.25


def to_controls(name: str, value: float, flavor: str) -> dict[str, Any] | None:
    """CAP_PROP 쓰기 → controls.set_many 인자. 제어가 아니면 None."""
    if name == "CAP_PROP_EXPOSURE":
        return {"auto_exposure": "manual", "exposure": value}
    if name == "CAP_PROP_WB_TEMPERATURE":
        return {"auto_white_balance": False, "white_balance": value}
    if name == "CAP_PROP_AUTO_EXPOSURE":
        return {"auto_exposure": "auto" if ae_is_auto(value, flavor) else "manual"}
    if name == "CAP_PROP_AUTO_WB":
        return {"auto_white_balance": bool(round(float(value)))}
    if name in _VALUE:
        return {_VALUE[name]: value}
    return None


def from_controls(name: str, controls: Any, flavor: str) -> float | None:
    """CAP_PROP 읽기 → OpenCV 관례의 숫자. 제어가 아니면 None, 모르는 모드는 -1."""
    if name == "CAP_PROP_AUTO_EXPOSURE":
        mode = controls.get("auto_exposure")
        return ae_number(None if mode is None else mode == "auto", flavor)
    if name == "CAP_PROP_AUTO_WB":
        v = controls.get("auto_white_balance")
        return -1.0 if v is None else float(bool(v))
    if name in _VALUE:
        v = controls.get(_VALUE[name])
        return None if v is None else float(v)
    return None
