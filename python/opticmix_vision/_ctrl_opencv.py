"""대체 백엔드 — OpenCV VideoCapture 의 get/set 으로 되는 제어만.

쓰는 곳: 테스트용 가짜 캡처, Windows 가 아닌 곳, 네이티브(DirectShow) 연결에 실패했을 때.
OpenCV 에는 전원 주파수·낮은 빛 보상 속성이 없고, 범위·기본값을 돌려주지 않는다. DirectShow 에서는 자동 노출을
읽지 못해(-1) 마지막으로 쓴 값으로만 안다.
Linux(V4L2): 범위·기본값은 장치 노드에 VIDIOC_QUERYCTRL 로 따로 묻는다(읽기 전용 핸들, `v4l2_ranges`). 덧붙이는 정보일 뿐 —
제어 이름·값 읽기·쓰기는 0.0.6 과 같이 OpenCV, 기본값은 쓰지 않는다(reset_to_defaults 는 아무것도 안 씀).
"""
from __future__ import annotations

import os
import struct
from typing import Any, Iterable

from opticmix_vision import _native

from ._cvprops import ae_number
from .controls import RawRange

__all__ = ["OpenCVBackend", "v4l2_ranges"]

_VALUE_PROPS = {
    "brightness": "CAP_PROP_BRIGHTNESS", "contrast": "CAP_PROP_CONTRAST", "hue": "CAP_PROP_HUE",
    "saturation": "CAP_PROP_SATURATION", "sharpness": "CAP_PROP_SHARPNESS", "gamma": "CAP_PROP_GAMMA",
    "white_balance": "CAP_PROP_WB_TEMPERATURE", "backlight_compensation": "CAP_PROP_BACKLIGHT",
    "gain": "CAP_PROP_GAIN", "exposure": "CAP_PROP_EXPOSURE",
}
_AUTO_PROPS = {"exposure": "CAP_PROP_AUTO_EXPOSURE", "white_balance": "CAP_PROP_AUTO_WB"}

# struct v4l2_queryctrl (linux/videodev2.h): id, type, name[32], minimum, maximum, step, default_value, flags, reserved[2].
# Its size is in the ioctl number (_core.V4L2_VIDIOC_QUERYCTRL, checked against the kernel header by the Rust tests).
QUERYCTRL = struct.Struct("=II32siiiiI2I")
CTRL_FLAG_DISABLED = 0x0001


def v4l2_ranges(node: str, keys: Iterable[str], *, _ioctl: Any = None) -> dict[str, RawRange]:
    """Linux: {key: RawRange} for the controls the V4L2 driver at `node` reports, by VIDIOC_QUERYCTRL. Reads only.

    A control the driver lacks (EINVAL) or marks disabled is left out. Any other failure (not a V4L2 node, no
    permission) raises OSError.
    """
    import errno
    if _ioctl is None:
        import fcntl
        _ioctl = fcntl.ioctl
    core = _native.core()
    request, cids = core.V4L2_VIDIOC_QUERYCTRL, core.V4L2_CID
    out: dict[str, RawRange] = {}
    fd = os.open(node, os.O_RDONLY)   # a second, read-only handle for the query: it does not stream. The camera's
    # driver answered VIDIOC_QUERYCTRL on it once with the camera (one PC, 2026-11-25), also while streaming
    try:
        for key in keys:
            buf = bytearray(QUERYCTRL.pack(cids[key], 0, b"", 0, 0, 0, 0, 0, 0, 0))
            try:
                _ioctl(fd, request, buf)
            except OSError as exc:
                if exc.errno == errno.EINVAL:
                    continue
                raise
            _, _, _, lo, hi, step, default, flags, _, _ = QUERYCTRL.unpack(buf)
            if not flags & CTRL_FLAG_DISABLED:
                out[key] = RawRange(lo, hi, step, default, key in _AUTO_PROPS)
    finally:
        os.close(fd)
    return out


class OpenCVBackend:
    def __init__(self, cap: Any, flavor: str = "dshow", *, reason: str = "", model: str | None = None,
                 node: str | None = None) -> None:
        import cv2
        self._cap = cap
        self.model = model          # Controls.model — OpenCV cannot tell; Device passes the USB name it looked up
        self._cv2 = cv2
        self.flavor = flavor
        self.name = f"opencv-{flavor}"
        self.exposure_units = "100us" if flavor == "v4l2" else "log2_s_nominal"
        self._last_auto: dict[str, bool] = {}
        # Linux (V4L2): the driver's defaults are shown, not written — Controls.reset_to_defaults() writes nothing there
        # (Linux control writes are not verified), as before the ranges were read
        self.writes_defaults = flavor != "v4l2"
        # node: the V4L2 device node (Linux) to read ranges and defaults from; None = no ranges (as before)
        self._ranges: dict[str, RawRange] | None = None
        self.driver_keys: frozenset[str] | None = None   # keys the driver reported (Controls.driver_names)
        if node is not None:
            try:
                self._ranges = v4l2_ranges(node, _VALUE_PROPS)
                self.driver_keys = frozenset(self._ranges)
            except OSError as exc:
                reason = "; ".join(r for r in (reason, f"V4L2 ranges not readable from {node}: {exc}") if r)
        parts = ["no power line frequency, no low-light compensation"
                 + (", no ranges or defaults" if self._ranges is None else "; ranges and defaults from the V4L2 driver")]
        if flavor != "v4l2":
            parts.append("auto-exposure state is not readable through OpenCV — shown as last written")
        if reason:
            parts.append(reason)
        self.note = "; ".join(parts)

    def start(self) -> None:
        pass

    def supports(self, key: str) -> bool:
        return key in _VALUE_PROPS   # as 0.0.6 — what the driver reports only adds ranges (driver_keys)

    def query(self, key: str) -> RawRange | None:
        return None if self._ranges is None else self._ranges.get(key)

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
