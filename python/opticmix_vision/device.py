"""Opening the camera, reading frames, splitting the two eyes.

The camera is a standard UVC device, picked up by the OS's own driver; this module opens it with OpenCV
`VideoCapture`. Resolution, fps, fourcc and backend are all **settings** — this module does not know which
modes the camera delivers; whatever it needs comes in through `DeviceConfig`.

How the two eyes are packed into one frame (`layout`) and which one comes first (`lr_order`) differ from
camera to camera. For the Weevia S1, `DeviceConfig.weevia_s1()` fills in the measured values. For any other
camera the caller states them — there are no defaults.

  layout    "sbs"         one frame, side by side [first | second]   (W = 2·w)
            "tb"          one frame, stacked [first / second]        (H = 2·h)
            "plane_pack"  a two-channel frame (H, W, 2). Channel 0 = first, channel 1 = second.
                          (A raw buffer tagged YUY2 whose Y bytes are the first eye and U/V bytes the second.
                           OpenCV delivers it as (H, W, 2) only with CAP_PROP_CONVERT_RGB=0)
  lr_order  "first_is_left"   the first half is the left camera
            "first_is_right"  the first half is the right camera

Timestamps: `StereoFrame.host_ns` is the host monotonic clock (`time.perf_counter_ns`) right after
`cap.read()` returns. It is not a device timestamp: the camera sends no clock this package reads.
`StereoFrame.driver_ns` is the operating system capture driver's time stamp for the frame, as OpenCV passes it
on in `CAP_PROP_POS_MSEC`, with `driver_clock` naming the clock:
  "media_foundation"  OpenCV CAP_MSMF: the sample time (100 ns units) Media Foundation's source reader gives with
                      the sample it hands OpenCV — OpenCV 4.13 asks the reader to convert the camera's format to
                      RGB (cap_msmf.cpp), so it is the converted sample's time. On one PC (Windows 11) with one
                      camera (2026-11-24, once) the first time was about 364,400 s — not the start of
                      streaming; what the clock's zero is has not been established. Use differences between
                      frames, not the absolute value. Run with the camera once (one PC, one camera, 2026-11-24):
                      276 of 900 frames per eye repeated the previous image while the intervals were
                      11.11 ms (mean, min and max) — driver_ns does not show whether a new image
                      arrived; cause not established. See API.md
  "v4l2"              OpenCV CAP_V4L2: the V4L2 buffer time stamp (microseconds). OpenCV does not pass on the
                      buffer's clock flag, so whether it is CLOCK_MONOTONIC is not checked here
Both are None with DirectShow (OpenCV returns -1 there; it is the Weevia S1 default on Windows) and for a
recording, unless `Playback.recorded_time` is set: then they are the values recorded in its CSV (with the
recorded host_ns). `driver_sequence` is always None: OpenCV does not pass on the V4L2 sequence number. Whether a driver
time marks the exposure or the arrival of the frame is not known.

Frame statistics (`Device.late_frames`, `estimated_missed_frames`, `actual_fps`) are estimated from the
intervals between those host times, not read from a device frame counter.
"""

from __future__ import annotations

import logging
import re
import sys
import threading
import time
import warnings
from collections import deque
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from ._cvprops import CONTROL_PROPS, from_controls, prop_name, to_controls
from .controls import Controls, ControlsUnavailable, reset_write_counts, write_counts

__all__ = [
    "prop_write_counts", "reset_prop_write_counts",
    "LAYOUTS", "LR_ORDERS", "BACKENDS",
    "DeviceConfig", "NegotiatedMode", "StereoFrame", "DeviceError", "EndOfRecording",
    "split_stereo", "Device",
]

log = logging.getLogger("opticmix_vision.device")

LAYOUTS = ("sbs", "tb", "plane_pack")
LR_ORDERS = ("first_is_left", "first_is_right")

# Setting any of these renegotiates the capture format, which discards a pixel
# format set earlier. They belong in DeviceConfig, where the open order is
# controlled, never in extra_props.
_FORMAT_PROPS = frozenset({
    "CAP_PROP_FRAME_WIDTH", "CAP_PROP_FRAME_HEIGHT", "CAP_PROP_FPS", "CAP_PROP_FOURCC",
})
BACKENDS = ("any", "dshow", "msmf", "v4l2", "avfoundation")

# OpenCV backends whose CAP_PROP_POS_MSEC is the driver's time stamp of the last frame read (OpenCV 4.13
# cap_msmf.cpp: the source reader's sample time / 1e4; cap_v4l.cpp: the V4L2 buffer time stamp), with the clock
# name and the driver's units per millisecond (100 ns, microseconds). DirectShow returns -1 for it.
_DRIVER_CLOCKS = {"MSMF": ("media_foundation", 10_000), "V4L2": ("v4l2", 1_000)}


def _driver_ns(ms: float, per_ms: int) -> int | None:
    """OpenCV's millisecond float back to whole driver units, then ns. None for a negative or NaN value."""
    if not ms >= 0:
        return None
    return round(ms * per_ms) * (1_000_000 // per_ms)


# Repeated images through OpenCV's Media Foundation path: Device warns once per open() when more than
# _MSMF_REPEAT_WARN of the last _MSMF_REPEAT_WINDOW frames repeated the previous image in both eyes.
# Run with the camera (one PC, one camera, 2026-11-24; API.md, "Media Foundation through OpenCV"): through
# CAP_MSMF 276 of 900 frames per eye repeated the previous image (30.7 %); through DirectShow 0 of 900, and the Rust crate reading Media
# Foundation without OpenCV 0 of 450. Cause not established. 5 % sits far below 30.7 % and above 0;
# 300 frames is about 3.3 s at 90 fps. Whether a live eye sends byte-identical images in a dark or covered
# scene has not been measured.
_MSMF_REPEAT_WINDOW = 300
_MSMF_REPEAT_WARN = 0.05


# The Weevia S1, as measured — not assumed. The first half of the side-by-side
# frame is the RIGHT eye (2026-09-16), and MJPG has to be requested: without it
# when the MJPG request was dropped the stream ran at 9.3 fps (measured 2026-09-13, one unit).
# DeviceConfig.weevia_s1() uses these.
WEEVIA_S1_NAME = "WN Dual Camera"
from opticmix_vision import _native  # noqa: E402 — check at import that the name equals the core's (when the extension is there)
if _native.available() and WEEVIA_S1_NAME != _native.core().WEEVIA_S1_NAME:   # the firmware's USB name — never change it
    raise ImportError(f"WEEVIA_S1_NAME {WEEVIA_S1_NAME!r} != core {_native.core().WEEVIA_S1_NAME!r}")
WEEVIA_S1 = {"layout": "sbs", "lr_order": "first_is_right", "backend": "dshow",
            "width": 1600, "height": 800, "fps": 90.0, "fourcc": "MJPG"}
# The --usb-location help of the tools that open the camera (viewer, explorer, doctor, presets, calib check): each
# passes it to DeviceConfig.weevia_s1(usb_location=...).
_USB_LOCATION_HELP = ("open the Weevia S1 plugged in at this USB location, to choose between two that share a name "
                      "(DeviceConfig.weevia_s1(usb_location=...)); Windows: as examples/list_devices.py or python -m "
                      "opticmix_vision.doctor (location) prints it; "
                      "Linux: the USB device's sysfs name, e.g. 1-4.2; no Weevia S1 there, or an empty one: exit code "
                      "2. Not a serial number: it changes when the camera is plugged into another port. This option: run "
                      "without a camera only (made-up device lists); not run with a camera, and not run with two "
                      "cameras")


class DeviceError(RuntimeError):
    """Opening or reading the camera failed. An empty frame is never returned silently."""


class EndOfRecording(DeviceError):
    """`read()` reached the end of a recording file (see `DeviceConfig.recording`)."""


# --- UVC control writes ----------------------------------------------------------------------
# The write guard (skip equal values, collapse to the latest, spacing) lives in controls.py. What is left here are old-name aliases.

def prop_write_counts() -> dict[str, int]:
    """(Compatibility) controls.write_counts() — per group, plus '_total'."""
    return write_counts()


def reset_prop_write_counts() -> None:
    """(Compatibility) controls.reset_write_counts() — only after re-inserting the USB cable."""
    reset_write_counts()


@dataclass(frozen=True)
class DeviceConfig:
    """Camera settings. width/height/fps/fourcc are **requests** — check what was negotiated with `Device.mode`.

    device     OpenCV device index (int) or a path string ("/dev/video0").
    backend    "any" | "dshow" | "msmf" (Windows) | "v4l2" (Linux) | "avfoundation" (macOS).
    width/height/fps/fourcc  None keeps the driver's default mode (the setting is not sent).
    layout / lr_order        Required, no default (for the Weevia S1, weevia_s1() fills in the measured values).
    to_gray    True converts a frame decoded to 3 channels to 8-bit gray before splitting (a mono sensor, so
               nothing is lost).
    stall_frames  When one eye sends the byte-identical image this many frames in a row, read() raises
               DeviceError — on the units we have tested one eye has occasionally stopped and came back after
               re-inserting the USB cable. A live eye has never been recorded sending byte-identical frames in a
               row (one unit, a lit room). 0 turns the check off; otherwise 2 or more. The same comparison
               counts `Device.repeated_frames`, so 0 turns that count off too (it is then None).
    """

    layout: str
    lr_order: str
    device: int | str = 0
    backend: str = "any"
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    fourcc: str | None = None
    to_gray: bool = True
    buffer_size: int | None = 1          # keeps queueing latency low. None: not set.
    extra_props: Mapping[str, float] = field(default_factory=dict)   # {"CAP_PROP_EXPOSURE": -5, ...} — goes through the controls guard
    stall_frames: int = 90               # 1 s at 90 fps

    def __post_init__(self) -> None:
        if self.layout not in LAYOUTS:
            raise ValueError(f"layout must be one of {LAYOUTS}: {self.layout!r}")
        if self.lr_order not in LR_ORDERS:
            raise ValueError(f"lr_order must be one of {LR_ORDERS}: {self.lr_order!r}")
        if self.backend not in BACKENDS:
            raise ValueError(f"backend must be one of {BACKENDS}: {self.backend!r}")
        for name in ("width", "height"):
            v = getattr(self, name)
            if v is not None and int(v) <= 0:
                raise ValueError(f"{name} must be positive: {v!r}")
        if self.fps is not None and float(self.fps) <= 0:
            raise ValueError(f"fps must be positive: {self.fps!r}")
        if self.fourcc is not None and len(self.fourcc) != 4:
            raise ValueError(f"fourcc must be 4 characters: {self.fourcc!r}")
        if int(self.stall_frames) < 0 or int(self.stall_frames) == 1:
            raise ValueError(f"stall_frames must be 0 (off) or 2 or more: {self.stall_frames!r}")
        object.__setattr__(self, "extra_props", dict(self.extra_props))

    @classmethod
    def weevia_s1(cls, *, usb_location: str | None = None, **overrides: Any) -> "DeviceConfig":
        """The measured settings for a Weevia S1, with the camera found by name.

        Picking a camera by index is how the wrong one gets opened: plugging in any
        other camera shifts the order. Pass ``device=`` to skip the lookup.

        Windows: DirectShow lists the cameras. Linux: the `/dev/videoN` capture node whose name contains
        'WN Dual Camera' in `/sys/class/video4linux`, and `backend` defaults to "v4l2".
        Linux name lookup: run with the camera once (one PC, one camera, 2026-11-25).

        Windows with backend="msmf": the index comes from Media Foundation's list (`devices.list_devices(backend=
        "msmf")`), the list OpenCV's CAP_MSMF counts in. With backend="any", OpenCV tries its backends in priority
        order (Media Foundation before DirectShow in OpenCV 4.13) and falls back to the next one with the same index,
        so the index is used only when the camera has the same index in both lists; otherwise pass backend="msmf"
        or "dshow". Media Foundation lookup: run without a camera only; not run with the camera.

        usb_location: with two cameras of the same name connected, the one at this USB location (`DeviceInfo.
        usb_location` as `examples/list_devices.py` prints it; Linux: the USB device's sysfs name, e.g. `1-4.2`;
        an empty or blank one is `ValueError`).
        A USB location is not a serial number: it names the port the camera is plugged into, and it changes when
        the camera is plugged into another port. Using two cameras at the same time is not supported. Picking a camera
        by USB location has been run with the camera once (one PC, one camera, 2026-11-25): with one camera connected,
        the list showed its USB location, and given that location it opened at 1600x800 MJPG and read one frame; a
        location no device has was refused (Python DirectShow, Rust Media Foundation); not run with two cameras.
        """
        from . import devices
        linux = sys.platform.startswith("linux")
        if linux:
            overrides.setdefault("backend", "v4l2")
        if "device" in overrides and usb_location is not None:
            raise ValueError("pass device= or usb_location=, not both")
        if "device" not in overrides:
            backend = overrides.get("backend")
            try:
                if linux:
                    overrides["device"] = devices.find_v4l2_node(WEEVIA_S1_NAME, usb_location=usb_location)
                elif backend in ("msmf", "any"):
                    mf = devices.resolve_by_name(WEEVIA_S1_NAME, usb_location=usb_location, backend="msmf").index
                    if backend == "any" and mf != (ds := devices.resolve_by_name(WEEVIA_S1_NAME,
                                                                                 usb_location=usb_location).index):
                        raise DeviceError(
                            f"backend='any': the Weevia S1 is index {mf} in the Media Foundation list but {ds} in the "
                            "DirectShow list, and OpenCV may open either backend with the index it is given. Pass "
                            "backend='msmf' or backend='dshow'.")
                    overrides["device"] = mf
                else:
                    overrides["device"] = devices.resolve_by_name(WEEVIA_S1_NAME, usb_location=usb_location).index
            except LookupError as exc:
                raise DeviceError(f"Weevia S1 ({WEEVIA_S1_NAME!r}) not found. {exc}") from exc
        return cls(**{**WEEVIA_S1, **overrides})

    @classmethod
    def recording(cls, path: str | Path, **overrides: Any) -> "DeviceConfig":
        """Plays a recording (`recording.Recorder`, the viewer's v key, `examples/record_video.py`: both eyes side
        by side, left eye in the left half) as if it were the camera. `read()` raises `EndOfRecording` after the
        last frame. `Device.playback` seeks, sets the speed, loops and pauses; `Device.mode` and
        `Device.recording_info` come from the recording's JSON. `host_ns` is the time the frame was read back and
        `driver_ns` / `driver_clock` are None, unless `playback.recorded_time` is set: then all three are the
        recorded values from the recording's CSV. A compressed (MJPG) recording does not have the camera's pixel
        values."""
        p = Path(path)
        if not p.is_file():
            raise DeviceError(f"no such recording: {p}")
        return cls(**{"layout": "sbs", "lr_order": "first_is_left", "device": str(p), "backend": "any",
                      "buffer_size": None, "stall_frames": 0, **overrides})

    @classmethod
    def from_bringup_constants(cls, path: str | Path, **overrides: Any) -> "DeviceConfig":
        """Reads `oem_uvc_constants.py`, the output of a bring-up tool.

        The file is Python with upper-case constant assignments only (BACKEND / DEVICE / RESOLUTION / FPS /
        FOURCC / LAYOUT / LR_ORDER ...). A BACKEND other than 'opencv' cannot be opened with this SDK, so it is
        an error (ffmpeg / pyuvc backends are out of scope). LAYOUT 'single' or an undecided LR_ORDER leave the
        stereo split undefined, so they are errors too — nothing is filled in by guessing.
        """
        ns: dict[str, Any] = {}
        src = Path(path).read_text(encoding="utf-8")
        exec(compile(src, str(path), "exec"), ns)  # noqa: S102 — a trusted file of constant assignments
        kw: dict[str, Any] = {}

        backend = str(ns.get("BACKEND", "opencv"))
        if backend != "opencv":
            raise ValueError(f"BACKEND={backend!r}: this SDK supports the OpenCV backend only")

        if "DEVICE" in ns:
            dev = str(ns["DEVICE"])
            m = re.match(r"index (\d+)(?: \((\w+)\))?", dev)      # 'index 0 (dshow)'
            if m:
                kw["device"] = int(m.group(1))
                if m.group(2) in BACKENDS:
                    kw["backend"] = m.group(2)
            else:
                kw["device"] = dev
        if "RESOLUTION" in ns:
            w, h = ns["RESOLUTION"]
            kw["width"], kw["height"] = int(w), int(h)
        if "FPS" in ns:
            kw["fps"] = float(ns["FPS"])
        if "FOURCC" in ns and ns["FOURCC"]:
            kw["fourcc"] = str(ns["FOURCC"])

        layout = str(ns.get("LAYOUT", ""))
        if layout not in LAYOUTS:
            raise ValueError(f"LAYOUT={layout!r}: cannot define the stereo split (re-run the bring-up tool)")
        lr = str(ns.get("LR_ORDER", ""))
        if lr not in LR_ORDERS:
            raise ValueError(f"LR_ORDER={lr!r}: left/right order is not set — it must be first_is_left or first_is_right")
        kw["layout"], kw["lr_order"] = layout, lr
        kw.update(overrides)
        return cls(**kw)

    def replace(self, **changes: Any) -> "DeviceConfig":
        return replace(self, **changes)


@dataclass(frozen=True)
class NegotiatedMode:
    """The mode the device actually gave (`cap.get` readback). It can differ from what was requested.

    Through MSMF, fourcc is OpenCV's output format code (RGB32 or RGB24), not the camera's format."""

    width: int
    height: int
    fps: float
    fourcc: str
    backend_name: str

    def to_dict(self) -> dict[str, Any]:
        return {"width": self.width, "height": self.height, "fps": self.fps,
                "fourcc": self.fourcc, "backend_name": self.backend_name}


@dataclass(frozen=True)
class StereoFrame:
    left: np.ndarray            # (h, w) uint8
    right: np.ndarray           # (h, w) uint8
    host_ns: int                # host monotonic clock, right after cap.read()
    index: int                  # frame number from this Device (not a device counter)
    driver_ns: int | None = None        # capture driver's time stamp (ns) on driver_clock; not a camera clock
    driver_clock: str | None = None     # "media_foundation" | "v4l2" | None; also "v4l2_monotonic" when
                                        # playing back a Rust crate recording (recorded_time)
    driver_sequence: int | None = None  # driver frame sequence number; None here (OpenCV does not give it)

    @property
    def size(self) -> tuple[int, int]:
        """(width, height) of one eye's image."""
        h, w = self.left.shape[:2]
        return int(w), int(h)


# ---------------------------------------------------------------------------
# Pure function: stereo split
# ---------------------------------------------------------------------------

def _to_gray(frame: np.ndarray) -> np.ndarray:
    """(H, W) / (H, W, 1) / (H, W, 3) → (H, W) uint8. Two channels are not handled here."""
    a = np.asarray(frame)
    if a.ndim == 2:
        return a
    if a.ndim == 3 and a.shape[2] == 1:
        return a[:, :, 0]
    if a.ndim == 3 and a.shape[2] == 3:
        import cv2
        return cv2.cvtColor(a, cv2.COLOR_BGR2GRAY)
    raise ValueError(f"cannot convert a frame of shape {a.shape} to gray")


def split_stereo(frame: np.ndarray, layout: str, lr_order: str, *, to_gray: bool = True
                 ) -> tuple[np.ndarray, np.ndarray]:
    """Splits one frame into (left, right). Returns views where possible — copy them yourself if needed.

    An sbs / tb frame that arrives with 3 channels (BGR decode) is converted to gray first when to_gray=True.
    plane_pack expects (H, W, 2); a 2-D (H, 2W) buffer is read as (H, W, 2).
    """
    if layout not in LAYOUTS:
        raise ValueError(f"layout must be one of {LAYOUTS}: {layout!r}")
    if lr_order not in LR_ORDERS:
        raise ValueError(f"lr_order must be one of {LR_ORDERS}: {lr_order!r}")
    a = np.asarray(frame)

    if layout == "plane_pack":
        if a.ndim == 3 and a.shape[2] == 2:
            first, second = a[:, :, 0], a[:, :, 1]
        elif a.ndim == 2 and a.shape[1] % 2 == 0:
            packed = a.reshape(a.shape[0], a.shape[1] // 2, 2)
            first, second = packed[:, :, 0], packed[:, :, 1]
        else:
            raise ValueError(f"plane_pack needs an (H, W, 2) or (H, 2W) frame: {a.shape}")
    else:
        g = _to_gray(a) if to_gray else a
        if g.ndim != 2:
            raise ValueError(f"sbs/tb split needs a 2D gray frame (with to_gray=False, convert it yourself): {g.shape}")
        h, w = g.shape
        if layout == "sbs":
            if w % 2:
                raise ValueError(f"sbs: frame width is odd ({w})")
            first, second = g[:, : w // 2], g[:, w // 2:]
        else:
            if h % 2:
                raise ValueError(f"tb: frame height is odd ({h})")
            first, second = g[: h // 2], g[h // 2:]

    if first.shape != second.shape:
        raise ValueError(f"the two halves differ in size: {first.shape} vs {second.shape}")
    return (first, second) if lr_order == "first_is_left" else (second, first)


# ---------------------------------------------------------------------------
# Device
# ---------------------------------------------------------------------------

class _FrameStats:
    """Late and missed frames estimated from the intervals between capture times, and the recent frame rate.

    An interval longer than 1.5 frame periods (at the negotiated fps) is one late frame, and round(interval /
    period) - 1 frames are counted as missed. Only consecutive frames count: the same frame again (a paused
    playback), a jump (seek, loop) or `restart` (the first frame after a pause in recording) starts over.
    """

    WINDOW = 30

    def __init__(self) -> None:
        self.late = 0
        self.missed = 0
        self._last: tuple[int, int] | None = None
        self._recent: deque[int] = deque(maxlen=self.WINDOW + 1)

    def add(self, index: int, ns: int, fps: float, *, restart: bool = False) -> None:
        last, self._last = self._last, (index, ns)
        if restart or last is None or index != last[0] + 1:   # first frame, the same frame again, a jump, a break
            self._recent.clear()
        elif fps > 0:
            period = 1e9 / fps
            dt = ns - last[1]
            if dt > 1.5 * period:
                self.late += 1
                self.missed += round(dt / period) - 1
        self._recent.append(ns)

    @property
    def fps(self) -> float:
        r = self._recent
        return (len(r) - 1) * 1e9 / (r[-1] - r[0]) if len(r) > 1 and r[-1] > r[0] else 0.0


class Device:
    """A thin wrapper over OpenCV VideoCapture. `with Device(cfg) as cam: frame = cam.read()`.

    The capture argument takes a VideoCapture-like object (get/set/read/isOpened/release) — for tests.
    """

    def __init__(self, config: DeviceConfig, *, capture: Any = None) -> None:
        self.config = config
        self._cap = capture
        self._injected = capture is not None
        self._mode: NegotiatedMode | None = None
        self._index = 0
        self._prev: tuple[np.ndarray, np.ndarray] | None = None
        self._same = [0, 0]              # identical frames in a row, per eye
        self._repeated = 0               # frames whose two eyes both repeated the previous image, since open()
        self._repeat_window: deque[bool] = deque(maxlen=_MSMF_REPEAT_WINDOW)
        self._repeat_warned = False
        self._driver: tuple[str, int] | None = None   # (clock name, units per ms) when the backend gives one
        self._controls: Controls | None = None
        self._controls_lock = threading.Lock()
        self._playback: Any = None
        self._stats = _FrameStats()

    @property
    def calibration(self):
        """This camera's calibration (StereoCalibration): the file installed with
        `python -m opticmix_vision.calib add <file.json>`, when exactly one is installed. The SDK cannot check
        that the file belongs to this camera (no USB serial). The video does not need it.
        Raises calib.CalibLookupError saying what to do when none fits."""
        if getattr(self, "_calib", None) is None:
            from . import calib
            c = calib.resolve()
            w, h = c.left.size
            got = ((self._mode.width, self._mode.height) if self._mode is not None   # negotiated, once open
                   else (self.config.width, self.config.height))
            if got[0] is not None and (got[0] // 2, got[1]) != (w, h):
                raise calib.CalibLookupError(f"This calibration is for {w}x{h} eyes. Open the {2 * w}x{h} mode.")
            self._calib = c
        return self._calib

    # ---- lifecycle -------------------------------------------------------
    def open(self) -> "Device":
        if self._mode is not None:
            return self
        import cv2

        if self._cap is None and isinstance(self.config.device, str) and Path(self.config.device).is_file():
            from .recording import Playback
            try:
                self._cap = Playback(self.config.device)
            except (OSError, ValueError) as exc:
                raise DeviceError(f"could not read the recording {self.config.device}: {exc}") from exc
        if self._cap is None:
            api_name = {"any": "CAP_ANY", "dshow": "CAP_DSHOW", "msmf": "CAP_MSMF",
                        "v4l2": "CAP_V4L2", "avfoundation": "CAP_AVFOUNDATION"}[self.config.backend]
            if not hasattr(cv2, api_name):
                raise DeviceError(f"this OpenCV build has no {api_name} (backend={self.config.backend})")
            self._cap = cv2.VideoCapture(self.config.device, getattr(cv2, api_name))
        cap = self._cap
        if not cap.isOpened():
            raise DeviceError(f"failed to open the camera: device={self.config.device!r} backend={self.config.backend} "
                              "(unplugged, wrong index, or in use by another app?)")

        c = self.config
        # Order matters, and getting it wrong is silent. Size and frame rate
        # each renegotiate the format, so a fourcc set before them is thrown
        # away and the driver keeps its default. Measured on the camera (DirectShow):
        # fourcc first gave YUY2 at 9.3 fps; fourcc last gave MJPG at 90.4 fps,
        # from the same requested mode. Set the pixel format last.
        # OpenCV CAP_V4L2 needs the other order for the frame rate. Measured
        # (one unit, 2/2 each): FPS then FOURCC committed 45 fps (frames still
        # arrived ~90 in a 3 s probe), FOURCC then FPS committed 90 fps.
        # Consistent with YUYV 1600x800 max 45 — mechanism unverified. So on
        # V4L2 set the fourcc before the frame rate. DirectShow keeps fourcc
        # last (FOURCC then FPS untested there).
        try:                                            # backend="any" on Linux also lands on V4L2
            opened_with = str(cap.getBackendName()) if hasattr(cap, "getBackendName") else ""
        except Exception:
            opened_with = ""
        # CAP_ANY falls through every camera backend with the same index (OpenCV 4.13 cap.cpp; the installed
        # registry also has GSTREAMER, FFMPEG, UEYE, OBSENSOR). An index meant for Media Foundation or DirectShow
        # names some other device elsewhere, so do not keep a capture another backend opened.
        if (not self._injected and sys.platform == "win32" and c.backend == "any" and isinstance(c.device, int)
                and opened_with.upper() not in ("MSMF", "DSHOW")):
            self.close()
            raise DeviceError(f"backend='any' opened device {c.device} through {opened_with or 'an unknown backend'}, "
                              "not Media Foundation or DirectShow, so the index may name another device. Pass "
                              "backend='msmf' or backend='dshow'.")
        fourcc_first = c.backend == "v4l2" or opened_with.upper() == "V4L2"
        if c.width is not None:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(c.width))
        if c.height is not None:
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(c.height))
        if c.fourcc and fourcc_first:
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*c.fourcc))
        if c.fps is not None:
            cap.set(cv2.CAP_PROP_FPS, float(c.fps))
        if c.fourcc and not fourcc_first:
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*c.fourcc))
        if c.buffer_size is not None:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, int(c.buffer_size))
        if c.layout == "plane_pack":
            cap.set(cv2.CAP_PROP_CONVERT_RGB, 0)       # the raw two-channel buffer as is
        fcc = int(cap.get(cv2.CAP_PROP_FOURCC))
        fcc_s = "".join(chr((fcc >> (8 * i)) & 0xFF) for i in range(4)) if fcc > 0 else ""
        backend_name = ""
        if hasattr(cap, "getBackendName"):
            try:
                backend_name = str(cap.getBackendName())
            except Exception:                           # not supported by some builds
                backend_name = ""
        self._mode = NegotiatedMode(
            width=int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            height=int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            fps=float(cap.get(cv2.CAP_PROP_FPS)),
            fourcc=fcc_s, backend_name=backend_name,
        )
        from .recording import Playback
        self._playback = self._cap if isinstance(self._cap, Playback) else None
        self._stats = _FrameStats()
        self._prev, self._same, self._repeated = None, [0, 0], 0     # counted from this open()
        self._repeat_window.clear()
        self._repeat_warned = False
        # a recording (Playback reports the backend it was recorded with) has no driver time
        recording = isinstance(c.device, str) and Path(c.device).is_file()
        self._driver = None if recording else _DRIVER_CLOCKS.get(backend_name.upper())
        self._apply_extra_props()
        return self

    def _apply_extra_props(self) -> None:
        import cv2
        values: dict[str, Any] = {}
        for name, value in self.config.extra_props.items():
            # Same trap from the other direction: these renegotiate the format,
            # so setting one here would undo the fourcc above and drop the
            # stream to the driver's default. Exposure, gain and the
            # illuminator were measured to be safe here; these are not.
            if name in _FORMAT_PROPS:
                self.close()
                raise DeviceError(
                    f"{name} must not be set through extra_props — the format is renegotiated and fourcc is ignored. "
                    f"Use width/height/fps in DeviceConfig."
                )
            mapped = to_controls(name, float(value), self._flavor())
            if mapped is None:
                self._cap.set(getattr(cv2, name), float(value))      # capture settings that are not controls go straight through
            else:
                values.update(mapped)
        if not values:
            return
        results = self.controls.set_many(values)
        done = [r for r in results if r.status != "pending"]
        done += self.controls.wait([r.ticket for r in results if r.status == "pending"])
        bad = [r for r in done if r.status not in ("written", "skipped_same", "superseded")]
        if bad:
            self.close()
            raise DeviceError("extra_props write failed: " + ", ".join(f"{r.name}={r.requested!r} {r.status} {r.error or ''}" for r in bad))

    def close(self) -> None:
        if self._controls is not None:
            self._controls.close()
            self._controls = None
        if self._cap is not None and not self._injected:
            self._cap.release()
            self._cap = None
        self._playback = None
        self._mode = None

    def __enter__(self) -> "Device":
        return self.open()

    def __exit__(self, *exc: Any) -> None:
        self.close()

    @property
    def is_open(self) -> bool:
        return self._mode is not None

    @property
    def mode(self) -> NegotiatedMode:
        """The negotiated mode. For a recording, the mode it was recorded in (from its JSON; without one, the file's)."""
        if self._mode is None:
            raise DeviceError("no negotiated mode before open()")
        return self._mode

    @property
    def playback(self) -> Any:
        """The `recording.Playback` when this device plays a recording (seek, speed, loop, pause); None for a camera."""
        return self._playback

    @property
    def recording_info(self) -> dict[str, Any] | None:
        """The capture info saved with the recording (its .json); None for a camera or a recording without one."""
        return self._playback.info if self._playback is not None else None

    # ---- frame statistics (host intervals, not device counters) ------------------------------
    @property
    def late_frames(self) -> int:
        """Frames that arrived more than 1.5 frame periods after the one before, since open().
        Estimated from host times; a late frame can be delivery jitter rather than a lost frame."""
        return self._stats.late

    @property
    def estimated_missed_frames(self) -> int:
        """Frames estimated missing since open(): for each late interval, round(interval / period) - 1.
        Estimated from host times, not a device frame counter."""
        return self._stats.missed

    @property
    def actual_fps(self) -> float:
        """Frames per second over the last 30 frames, from host times (0.0 until two frames are read)."""
        return self._stats.fps

    @property
    def repeated_frames(self) -> int | None:
        """Frames read() returned since open() whose two eyes were both byte-identical to the frame before —
        the comparison the stall_frames check makes. None when stall_frames=0 (no comparison is made)."""
        return self._repeated if int(self.config.stall_frames) else None

    # ---- frames ------------------------------------------------------------
    def read_raw(self) -> tuple[np.ndarray, int]:
        """The raw frame before the split, and the host time (ns). A failure raises."""
        if self._mode is None:
            raise DeviceError("call open() first")
        ok, img = self._cap.read()
        t = time.perf_counter_ns()
        pb = self._playback
        if not ok or img is None:
            if pb is not None:
                raise EndOfRecording(f"end of the recording: {self.config.device}")
            raise DeviceError("frame read failed (timeout or camera unplugged)")
        if pb is not None and pb.recorded_time:
            t = pb.recorded_ns(pb.position)
        return img, t

    def read(self) -> StereoFrame:
        """The next frame. From a recording, `index` is the frame's number in the file."""
        img, t = self.read_raw()
        left, right = split_stereo(img, self.config.layout, self.config.lr_order,
                                   to_gray=self.config.to_gray)
        self._check_stall(left, right)
        pb = self._playback
        index = pb.position if pb is not None else self._index
        driver_ns = clock = None
        if self._driver is not None:
            import cv2
            clock, per_ms = self._driver
            driver_ns = _driver_ns(float(self._cap.get(cv2.CAP_PROP_POS_MSEC)), per_ms)
            clock = clock if driver_ns is not None else None
        elif pb is not None and pb.recorded_time:      # the recorded driver time, beside the recorded host_ns
            driver_ns, clock = pb.recorded_driver(index)
        frame = StereoFrame(left=left, right=right, host_ns=t, index=index,
                            driver_ns=driver_ns, driver_clock=clock)
        self._index += 1
        # a recording's statistics describe the recording: its recorded times, at its recorded rate
        if pb is None:
            self._stats.add(index, t, self._mode.fps)
        else:
            self._stats.add(index, pb.recorded_ns(index), self._mode.fps, restart=index in pb.breaks)
        return frame

    def _check_stall(self, left: np.ndarray, right: np.ndarray) -> None:
        n = int(self.config.stall_frames)
        prev, self._prev = self._prev, (left, right)
        if not n or prev is None:
            return
        same = (np.array_equal(left, prev[0]), np.array_equal(right, prev[1]))
        self._count_repeat(same[0] and same[1])
        for i, eye in enumerate(("left", "right")):
            self._same[i] = self._same[i] + 1 if same[i] else 0
            if self._same[i] + 1 >= n:
                raise DeviceError(
                    f"The {eye} eye has sent the same image for {n} frames in a row: its stream has stopped. "
                    "Re-insert the USB cable: unplug it and plug it back in (it sometimes takes two tries). "
                    "stall_frames=0 in DeviceConfig turns this check off.")

    def _count_repeat(self, repeated: bool) -> None:
        self._repeated += repeated
        if self._repeat_warned or self._playback is not None or self._mode.backend_name.upper() != "MSMF":
            return
        w = self._repeat_window
        w.append(repeated)
        k = sum(w)
        if len(w) == w.maxlen and k > _MSMF_REPEAT_WARN * w.maxlen:
            self._repeat_warned = True
            warnings.warn(
                f"{k} of the last {w.maxlen} frames repeated the previous image in both eyes, read through "
                "OpenCV's Media Foundation backend (backend='msmf'). Seen on one PC with one camera (2026-11-24): "
                "276 of 900 frames per eye through this path; not seen through DirectShow (backend='dshow', "
                "DeviceConfig.weevia_s1()'s default on Windows; 0 of 900) or the Rust crate, which reads Media "
                "Foundation without OpenCV (0 of 450); cause not established. driver_ns does not show whether a "
                "new image arrived. Device.repeated_frames counts them; this warning is given once per open().",
                stacklevel=4)

    # ---- camera controls--------------------------------------------------------------------------
    def _flavor(self) -> str:
        """The backend whose auto-exposure numbering applies: dshow | v4l2 | msmf. Unknown: the dshow convention."""
        if self.config.backend in ("dshow", "v4l2", "msmf"):
            return self.config.backend
        name = (self._mode.backend_name if self._mode else "").upper()
        return {"DSHOW": "dshow", "V4L2": "v4l2", "MSMF": "msmf"}.get(name, "dshow")

    @property
    def controls(self) -> Controls:
        """Every UVC control of this camera (controls.py). Created on first use."""
        if self._cap is None or self._mode is None:
            raise DeviceError("call open() first")
        if self._playback is not None:
            raise DeviceError("a recording has no camera controls; the values when it was recorded are in "
                              "recording_info['controls']")
        with self._controls_lock:
            if self._controls is None:
                self._controls = self._make_controls()
            return self._controls

    def _make_controls(self) -> Controls:
        from ._ctrl_opencv import OpenCVBackend
        reason = ""
        if (not self._injected and sys.platform == "win32" and isinstance(self.config.device, int)
                and self._mode.backend_name.upper() == "DSHOW"):
            from ._ctrl_dshow import DShowBackend
            try:
                return Controls(DShowBackend(self.config.device))
            except ControlsUnavailable as exc:
                reason = f"native DirectShow controls unavailable: {exc}"
                log.warning("%s — falling back to OpenCV", reason)
        node = None   # Linux V4L2: the device node, so the fallback can read ranges and defaults (VIDIOC_QUERYCTRL)
        if not self._injected and sys.platform.startswith("linux") and self._flavor() == "v4l2":
            d = self.config.device
            node = f"/dev/video{d}" if isinstance(d, int) else str(d)
        return Controls(OpenCVBackend(self._cap, self._flavor(), reason=reason, model=self._usb_model(), node=node))

    def _usb_model(self) -> str | None:
        """WEEVIA_S1_NAME when the opened device has that USB name (Linux: sysfs; Windows: the DirectShow list, or
        the Media Foundation list with backend="msmf", read only). The OpenCV fallback cannot ask the device, so this
        is how presets know the model there."""
        from . import devices
        name = devices._os_name(self.config.device, self.config.backend) or ""
        return WEEVIA_S1_NAME if WEEVIA_S1_NAME.lower() in name.lower() else None

    def get_prop(self, prop: str | int) -> float:
        """(Compatibility) Reads by OpenCV name. Camera controls go through controls (AUTO_EXPOSURE now reads too)."""
        import cv2
        if self._cap is None:
            raise DeviceError("call open() first")
        name = prop_name(prop)
        if name in CONTROL_PROPS and self._mode is not None:
            try:
                v = from_controls(name, self.controls, self._flavor())
            except (KeyError, ControlsUnavailable):
                v = None
            if v is not None:
                return v
        pid = getattr(cv2, name) if hasattr(cv2, name) else int(prop)
        return float(self._cap.get(pid))

    def set_prop(self, prop: str | int, value: float, *, wait: bool = True) -> bool:
        """(Compatibility) Writes a camera control by OpenCV name — the write itself goes through the controls guard.

        CAP_PROP_EXPOSURE switches to manual by itself, as before (controls.set("exposure") refuses while auto).
        wait=True waits until the write is done (it can be delayed by the guard's spacing).
        Returns True when written or already at that value, False when the driver refused it.
        """
        if self._cap is None or self._mode is None:
            raise DeviceError("call open() first")
        name = prop_name(prop)
        mapped = to_controls(name, float(value), self._flavor())
        if mapped is None:
            raise DeviceError(f"{name} is not a camera control — set resolution, fps and format in DeviceConfig")
        try:
            results = self.controls.set_many(mapped)
        except KeyError as exc:
            raise DeviceError(f"{name}: {exc}") from exc
        if not wait:
            return all(r.status != "failed" for r in results)
        done = [r for r in results if r.status != "pending"]
        done += self.controls.wait([r.ticket for r in results if r.status == "pending"])
        return all(r.status in ("written", "skipped_same", "superseded") for r in done)
