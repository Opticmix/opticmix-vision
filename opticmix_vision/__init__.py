"""opticmix_vision — Opticmix Vision 스테레오 IR 카메라용 최소 파이썬 SDK.

이 패키지는 **하드웨어 없이 import·테스트가 가능한 뼈대**다. 카메라는 UVC 표준이므로
OpenCV `VideoCapture` 로 열고, 한 프레임에 실려 오는 두 눈 영상을 `layout` / `lr_order`
설정에 따라 left / right 로 분리한다.

모듈:
  device       카메라 열기·프레임 획득·스테레오 분리 (`Device`, `DeviceConfig`, `split_stereo`)
  calibration  KB4 어안 캘리브레이션 파일(JSON) 로드/저장/검증 (`StereoCalibration`)
  rectify      cv2.fisheye 기반 undistort / stereo rectify 맵 생성
  led          IR LED PWM 제어 래퍼 (UVC Backlight Compensation; 값 범위는 ❓ 주입식)
  depth        SGBM 시차 → 깊이(mm). 이 카메라는 온보드 깊이 엔진이 없어 호스트에서 계산한다
  ros          캘리브 JSON → ROS camera_info YAML (equidistant = KB4)
  hands        손추적 API (Hand/HandTracker; 정보모델은 내부 스펙, 런타임은 [hands] extra 의 onnxruntime)

❓ 표시가 붙은 값은 실기기 브링업으로 확정되기 전까지 **미검증** 이다. 이 패키지는
그런 값을 기본값으로 박아 두지 않고 호출자가 명시하도록 강제한다.
"""

from __future__ import annotations

from .calibration import (
    CalibrationError,
    CameraIntrinsics,
    StereoCalibration,
    StereoExtrinsics,
    load_calibration,
    save_calibration,
    synthetic_equidistant,
)
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
from .depth import (
    DepthParams,
    DepthResult,
    colorize_depth,
    compute_disparity,
    depth_from_pair,
    depth_from_rectified,
    disparity_to_depth_mm,
)
from .led import DEFAULT_LED_PROP, LedControl, LedControlUnavailable
from .ros import camera_info_yaml, write_stereo_camera_info
from .rectify import (
    DEFAULT_RECTIFIED_HFOV_DEG,
    RectifyMaps,
    pinhole_matrix,
    remap_pair,
    stereo_rectify_maps,
    undistort_maps,
)
from .hands import (
    Arm,
    Bone,
    Chirality,
    Finger,
    FingerKind,
    FrameTransform,
    Hand,
    HandTracker,
    Palm,
    TrackingFrame,
    assemble_hand,
)
from .hands_preprocess import EyeGeometry, eyes_to_model_input

__version__ = "0.0.1"

__all__ = [
    "__version__",
    # device
    "Device", "DeviceConfig", "DeviceError", "NegotiatedMode", "StereoFrame",
    "split_stereo", "LAYOUTS", "LR_ORDERS",
    # calibration
    "CameraIntrinsics", "StereoExtrinsics", "StereoCalibration", "CalibrationError",
    "load_calibration", "save_calibration", "synthetic_equidistant",
    # rectify
    "RectifyMaps", "undistort_maps", "stereo_rectify_maps", "remap_pair", "pinhole_matrix",
    "DEFAULT_RECTIFIED_HFOV_DEG",
    # led
    "LedControl", "LedControlUnavailable", "DEFAULT_LED_PROP",
    # depth (온보드 깊이 엔진이 없으므로 호스트에서 SGBM 으로 계산한다)
    "DepthParams", "DepthResult", "compute_disparity", "disparity_to_depth_mm",
    "depth_from_rectified", "depth_from_pair", "colorize_depth",
    # ros
    "camera_info_yaml", "write_stereo_camera_info",
    # hands (손추적 — 자체 심볼. 런타임은 선택 extra: pip install opticmix-vision[hands])
    "Hand", "HandTracker", "TrackingFrame", "Finger", "Bone", "Palm", "Arm", "FingerKind", "Chirality",
    "FrameTransform", "assemble_hand", "EyeGeometry", "eyes_to_model_input",
]
