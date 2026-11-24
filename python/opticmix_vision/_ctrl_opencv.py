"""대체 백엔드 — OpenCV VideoCapture 의 get/set 으로 되는 제어만.

쓰는 곳: 테스트용 가짜 캡처, Windows 가 아닌 곳, 네이티브(DirectShow) 연결에 실패했을 때.
OpenCV 에는 전원 주파수·낮은 빛 보상 속성이 없고, 범위·기본값을 돌려주지 않는다. DirectShow 에서는 자동 노출을
읽지 못해(-1) 마지막으로 쓴 값으로만 안다.
"""
from __future__ import annotations

from typing import Any

from ._cvprops import ae_number
from .controls import RawRange

__all__ = ["OpenCVBackend"]

_VALUE_PROPS = {
    "brightness": "CAP_PROP_BRIGHTNESS", "contrast": "CAP_PROP_CONTRAST", "hue": "CAP_PROP_HUE",
    "saturation": "CAP_PROP_SATURATION", "sharpness": "CAP_PROP_SHARPNESS", "gamma": "CAP_PROP_GAMMA",
    "white_balance": "CAP_PROP_WB_TEMPERATURE", "backlight_compensation": "CAP_PROP_BACKLIGHT",
    "gain": "CAP_PROP_GAIN", "exposure": "CAP_PROP_EXPOSURE",
}
_AUTO_PROPS = {"exposure": "CAP_PROP_AUTO_EXPOSURE", "white_balance": "CAP_PROP_AUTO_WB"}


class OpenCVBackend:
    def __init__(self, cap: Any, flavor: str = "dshow", *, reason: str = "", model: str | None = None) -> None:
        import cv2
        self._cap = cap
        self.model = model          # Controls.model — OpenCV cannot tell; Device passes the USB name it looked up
        self._cv2 = cv2
        self.flavor = flavor
        self.name = f"opencv-{flavor}"
        self.exposure_units = "100us" if flavor == "v4l2" else "log2_s_nominal"
        self._last_auto: dict[str, bool] = {}
        parts = ["no power line frequency, no low-light compensation, no ranges or defaults"]
        if flavor != "v4l2":
            parts.append("auto-exposure state is not readable through OpenCV — shown as last written")
        if reason:
            parts.append(reason)
        self.note = "; ".join(parts)

    def start(self) -> None:
        pass

    def supports(self, key: str) -> bool:
        return key in _VALUE_PROPS

    def query(self, key: str) -> RawRange | None:
        return None

    def _pid(self, prop: str) -> int:
        return int(getattr(self._cv2, prop))

    def read(self, key: str) -> tuple[int, bool | None]:
        value = int(round(float(self._cap.get(self._pid(_VALUE_PROPS[key])))))
        auto: bool | None = None
        if key in _AUTO_PROPS:
            a = float(self._cap.get(self._pid(_AUTO_PROPS[key])))
            if key == "exposure" and self.flavor == "v4l2" and a in (1.0, 3.0):
                auto = a == 3.0
            elif key == "white_balance" and a in (0.0, 1.0):
                auto = a == 1.0
            else:
                auto = self._last_auto.get(key)
        return value, auto

    def write(self, key: str, value: int | None, auto: bool | None) -> None:
        if auto is not None and key in _AUTO_PROPS:
            # DShow: 값 쓰기(Flags 0 / WB 는 Manual)가 곧 수동 전환이라 모드를 따로 쓰지 않는다 — 옛 SDK 와 같은 1회.
            # V4L2: 커널 6.3+ 는 수동이 아니면 값 쓰기를 거부하므로 모드를 먼저 쓴다.
            if auto or value is None or self.flavor == "v4l2":
                number = ae_number(auto, self.flavor) if key == "exposure" else (1.0 if auto else 0.0)
                self._set(_AUTO_PROPS[key], number)
            self._last_auto[key] = auto
            if auto:
                return
        if value is not None:
            self._set(_VALUE_PROPS[key], float(value))

    def _set(self, prop: str, number: float) -> None:
        if not self._cap.set(self._pid(prop), number):
            raise OSError(f"the driver rejected {prop}={number:g}")

    def close(self) -> None:
        pass
