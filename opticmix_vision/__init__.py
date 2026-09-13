"""opticmix_vision — Opticmix Vision 스테레오 IR 카메라용 최소 파이썬 SDK.

이 패키지는 **하드웨어 없이 import·테스트가 가능한 뼈대**다. 카메라는 UVC 표준이므로
OpenCV `VideoCapture` 로 열고, 한 프레임에 실려 오는 두 눈 영상을 `layout` / `lr_order`
설정에 따라 left / right 로 분리한다.

공개 모듈 — 실기기에서 돌려본 것만 배포한다:
  device       카메라 열기·프레임 획득·스테레오 분리 (`Device`, `DeviceConfig`, `split_stereo`)
  led          IR 플러드 조명 제어 (UVC Backlight; 제어 범위 0–96 실측, 2개체 재현)

캘리브레이션·rectify·깊이·ROS·손추적은 코드가 있지만 **카메라에서 돌려본 적이 없어
배포판에서 제외한다.** 라벨을 붙이는 것보다 빼는 것이 정직하다 — 라벨은 import 를
막지 못하고, 검증된 적 없는 숫자가 그대로 나온다. 각 모듈은 해당 기능의 검증이
끝나는 날 돌아온다.
"""

from __future__ import annotations

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
from .led import DEFAULT_LED_PROP, LedControl, LedControlUnavailable

__version__ = "0.0.2"

__all__ = [
    "__version__",
    # device — capture and stereo split, run against the camera
    "Device", "DeviceConfig", "DeviceError", "NegotiatedMode", "StereoFrame",
    "split_stereo", "LAYOUTS", "LR_ORDERS",
    # led — illuminator, control range measured on two units
    "LedControl", "LedControlUnavailable", "DEFAULT_LED_PROP",
]

# Modules that are developed but not published, because they have never been run
# against a camera: calibration, calibrate, rectify, depth, ros, hands. They are
# excluded from the distribution (see .gitattributes), so importing one here
# would fail for everybody who installed the package. Whoever is working inside
# the monorepo still has the files and can import them directly:
#
#     from opticmix_vision.depth import depth_from_pair
#
# A module rejoins this list on the day its row in capabilities.csv moves — the
# same gate the website uses, where an unverified page ships no route.
_WITHHELD = ("calibration", "calibrate", "rectify", "depth", "ros", "hands",
             "hands_preprocess")


def __getattr__(name: str):
    """Explain the absence instead of raising a bare AttributeError."""
    for mod in _WITHHELD:
        try:
            module = __import__(f"opticmix_vision.{mod}", fromlist=[name])
        except ModuleNotFoundError:
            continue
        if hasattr(module, name):
            raise AttributeError(
                f"{name!r} lives in opticmix_vision.{mod}, which is not exported: it has "
                f"not been run against the camera. Import it directly if you accept that."
            )
    raise AttributeError(f"module 'opticmix_vision' has no attribute {name!r}")
