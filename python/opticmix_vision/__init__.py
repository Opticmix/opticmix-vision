"""opticmix_vision — Weevia S1 스테레오 IR 카메라용 최소 파이썬 SDK.

이 패키지는 **하드웨어 없이 import·테스트가 가능한 뼈대**다. 카메라는 UVC 표준이므로
OpenCV `VideoCapture` 로 열고, 한 프레임에 실려 오는 두 눈 영상을 `layout` / `lr_order`
설정에 따라 left / right 로 분리한다.

공개 모듈:
  device       카메라 열기·프레임 획득·스테레오 분리 (`Device`, `DeviceConfig`, `split_stereo`)
  led          IR 플러드 조명 제어 (UVC Backlight; 제어 범위 0–96, 2개체에서 측정)
  controls     카메라가 광고하는 UVC 제어 전부 — 읽기·범위/기본값·쓰기 가드 (`Controls`).
               제어 읽기·쓰기의 실기기 기록은 이전 Python 구현으로 잰 것이다. 0.0.3 의 Rust 구현은
               아직 카메라에서 검증하지 않았다.

Calibration, rectification, depth and hand tracking are not in this package.
"""

from __future__ import annotations

# 확장(_core)만 담은 휠(opticmix-vision-core)을 editable 설치 위에 더하면 그 _core.pyd 는 site-packages/opticmix_vision/
# 에 있다. 소스 트리 패키지만 보면 못 찾으므로 sys.path 의 다른 opticmix_vision/ 디렉터리도 하위 모듈 경로에 넣는다.
__path__ = __import__("pkgutil").extend_path(__path__, __name__)

from .device import (
    LAYOUTS,
    LR_ORDERS,
    Device,
    DeviceConfig,
    DeviceError,
    NegotiatedMode,
    StereoFrame,
    split_stereo,
)
from .controls import (
    CONTROLS, ControlInfo, Controls, ControlsUnavailable, ControlStateError, WriteResult,
    reset_write_counts, write_counts,
)
from .led import DEFAULT_LED_PROP, LedControl, LedControlUnavailable

__version__ = "0.0.3.post1"

__all__ = [
    "__version__",
    # device — capture and stereo split, run against the camera
    "Device", "DeviceConfig", "DeviceError", "NegotiatedMode", "StereoFrame",
    "split_stereo", "LAYOUTS", "LR_ORDERS",
    # led — illuminator, control range measured on two units
    "LedControl", "LedControlUnavailable", "DEFAULT_LED_PROP",
    # controls — every control the camera advertises (writes guarded, see README)
    "Controls", "ControlInfo", "WriteResult", "ControlsUnavailable", "ControlStateError", "CONTROLS",
    "write_counts", "reset_write_counts",
]
