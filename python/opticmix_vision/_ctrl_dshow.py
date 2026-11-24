"""Windows 네이티브 백엔드 — DirectShow IAMVideoProcAmp / IAMCameraControl.

OpenCV 가 못 하는 것: 전원 주파수(ProcAmp id 13), 낮은 빛 보상(CameraControl id 19), 범위·기본값(GetRange),
자동 노출 읽기(Get 의 flags). id 13/19 는 DirectShow 열거형 밖이지만 kswdmcap.ax 10.0.26100.1 이 id 범위 검사 없이
KS 속성으로 넘긴다(2026-09-24 확인). 26100 이전 빌드는 미검증.

모든 COM 호출은 Controls 워커 스레드 한 곳에서만 한다(start/close 포함). 장치는 OpenCV 와 같은 순서로 센 index 번째
모니커이고, 이름이 Weevia S1 이 아니면 거부한다 — 다른 카메라에 쓰지 않는다.

구현(2026-10-29): COM 호출은 Rust 코어(`rust/opticmix-vision/src/dshow.rs`, windows 크레이트)가 한다. 이 모듈은
그 얇은 래퍼다. Controls 에 넘긴 DShowBackend 는 안의 Rust 백엔드가 그대로 워커 스레드로 옮겨 간다.
"""
from __future__ import annotations

import ctypes
import sys
import uuid

from opticmix_vision import _native

from .controls import ControlsUnavailable, RawRange

__all__ = ["DShowBackend", "enumerate_video_devices", "choose", "KEYS", "PROCAMP", "CAMERA", "GUID",
           "EXPECTED_NAMES"]

EXPECTED_NAMES = ("WN Dual Camera",)     # 🔴 펌웨어 USB 장치 이름 — 제품명이 바뀌어도 바꾸지 않는다
if _native.available() and tuple(_native.core().EXPECTED_NAMES) != EXPECTED_NAMES:  # 어긋나면 다른 카메라를 고를 수 있다
    raise ImportError(f"EXPECTED_NAMES {EXPECTED_NAMES} != core {tuple(_native.core().EXPECTED_NAMES)}")
PROCAMP, CAMERA = "procamp", "camera"
KEYS: dict[str, tuple[str, int]] = ({k: (iface, pid) for k, (iface, pid) in _native.core().KEYS.items()}
                                     if _native.available() else {})
AUTO_CAPABLE = frozenset({"exposure", "white_balance"})
FLAG_AUTO, FLAG_MANUAL = 1, 2


class GUID(ctypes.Structure):
    """COM GUID(16바이트). 코어의 GUID 값이 이 파일의 문자열과 바이트 단위로 같은지 Rust 시험이 확인한다."""
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16), ("Data3", ctypes.c_uint16),
                ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, text: str) -> "GUID":
        g = cls()
        ctypes.memmove(ctypes.byref(g), uuid.UUID(text).bytes_le, 16)
        return g

    def __str__(self) -> str:
        return str(uuid.UUID(bytes_le=bytes(self)))


CLSID_SystemDeviceEnum = GUID.parse("62BE5D10-60EB-11D0-BD3B-00A0C911CE86")
IID_ICreateDevEnum = GUID.parse("29840822-5B84-11D0-BD3B-00A0C911CE86")
CLSID_VideoInputDeviceCategory = GUID.parse("860BB310-5D01-11D0-BD3B-00A0C911CE86")
IID_IPropertyBag = GUID.parse("55272A00-42CB-11CE-8135-00AA004BB851")
IID_IBaseFilter = GUID.parse("56A86895-0AD4-11CE-B03A-0020AF0BA770")
IID_IAMVideoProcAmp = GUID.parse("C6E13360-30AC-11D0-A18C-00A0C9118956")
IID_IAMCameraControl = GUID.parse("C6E13370-30AC-11D0-A18C-00A0C9118956")


def enumerate_video_devices() -> list[str | None]:
    """DirectShow 영상 입력 장치 이름을 OpenCV 와 같은 순서로. 아무 장치에도 바인드하지 않는다(읽기만)."""
    if sys.platform != "win32":
        return []
    return list(_native.core().enumerate_video_devices())


def choose(names: list[str | None], index: int, expected: tuple[str, ...] = EXPECTED_NAMES) -> None:
    """index 번째 장치가 Weevia S1 인지 확인한다. 아니면 ControlsUnavailable — 다른 카메라에 쓰지 않는다."""
    _native.core().choose(list(names), int(index), list(expected))


class DShowBackend:
    """Rust DShowBackend 의 래퍼. Controls(DShowBackend(i)) 로 쓰면 COM 은 전부 Rust 워커 스레드에서 돈다.

    단독으로 start() → query/read/raw_get → close() 도 된다(진단 스크립트) — 한 스레드에서 부를 것.
    """
    name = "dshow"
    exposure_units = "log2_s_nominal"
    note = ""

    def __init__(self, index: int, *, expected: tuple[str, ...] = EXPECTED_NAMES) -> None:
        if sys.platform != "win32":
            raise ControlsUnavailable("DirectShow controls are Windows-only")
        self._index = int(index)
        self._expected = expected
        self.model = expected[0] if len(expected) == 1 else None   # the core refuses a device not named so
        self._core_backend = _native.core().DShowBackend(self._index, list(expected))

    def start(self) -> None:
        self._core_backend.start()

    def supports(self, key: str) -> bool:
        return bool(self._core_backend.supports(key))

    def query(self, key: str) -> RawRange | None:
        t = self._core_backend.query(key)
        return None if t is None else RawRange(*t)

    def read(self, key: str) -> tuple[int, bool | None]:
        return tuple(self._core_backend.read(key))  # type: ignore[return-value]

    def write(self, key: str, value: int | None, auto: bool | None) -> None:
        self._core_backend.write(key, value, auto)

    # ---- 진단(읽기 전용) — 이름 표 밖의 id 를 볼 때만 --------------------------------------------
    def raw_get(self, iface: str, pid: int) -> dict:
        return dict(self._core_backend.raw_get(iface, int(pid)))

    def raw_range(self, iface: str, pid: int) -> dict:
        return dict(self._core_backend.raw_range(iface, int(pid)))

    def close(self) -> None:
        self._core_backend.close()
