"""opticmix_vision: a minimal Python SDK for the Weevia S1 stereo IR camera.

The camera is a standard UVC device, so it is opened with OpenCV `VideoCapture`, and the two eye images
carried in one frame are split into left / right according to the `layout` / `lr_order` settings.

Public modules:
  device       open the camera, grab frames, split stereo (`Device`, `DeviceConfig`, `split_stereo`)
  led          IR flood illuminator control (UVC Backlight; control range 0-96, measured on two units)
  controls     every UVC control the camera advertises: read, range/default, write guard (`Controls`).
               Measured with the earlier Python implementation; the Rust implementation (since 0.0.3) has
               not yet been verified against the camera.
  calib        install and read a per-unit calibration file (preview)

Making a calibration, rectification, depth and hand tracking are not in this package.
"""

from __future__ import annotations

# When the wheel holding only the extension (_core), opticmix-vision-core, is added on top of an editable install,
# its _core.pyd sits in site-packages/opticmix_vision/. Looking only at the source-tree package would miss it, so
# the other opticmix_vision/ directories on sys.path are added to the submodule search path too.
__path__ = __import__("pkgutil").extend_path(__path__, __name__)

from .device import (
    LAYOUTS,
    LR_ORDERS,
    Device,
    DeviceConfig,
    DeviceError,
    EndOfRecording,
    NegotiatedMode,
    StereoFrame,
    split_stereo,
)
from .controls import (
    CONTROLS, ControlInfo, Controls, ControlsUnavailable, ControlStateError, WriteResult,
    reset_write_counts, write_counts,
)
from .led import DEFAULT_LED_PROP, LedControl, LedControlUnavailable

__version__ = "0.0.4"

__all__ = [
    "__version__",
    # device — capture and stereo split, run against the camera
    "Device", "DeviceConfig", "DeviceError", "EndOfRecording", "NegotiatedMode", "StereoFrame",
    "split_stereo", "LAYOUTS", "LR_ORDERS",
    # led — illuminator, control range measured on two units
    "LedControl", "LedControlUnavailable", "DEFAULT_LED_PROP",
    # controls — every control the camera advertises (writes guarded, see README)
    "Controls", "ControlInfo", "WriteResult", "ControlsUnavailable", "ControlStateError", "CONTROLS",
    "write_counts", "reset_write_counts",
]
