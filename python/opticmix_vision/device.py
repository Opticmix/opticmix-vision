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
`cap.read()` returns. It is not a device timestamp — whether the device exposes one is not verified, and on
a live device OpenCV `CAP_PROP_POS_MSEC` is often the driver's or the host's clock, so it is not used here.
"""

from __future__ import annotations

import logging
import re
import sys
import threading
import time
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
               row (one unit, a lit room). 0 turns the check off; otherwise 2 or more.
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
    def weevia_s1(cls, **overrides: Any) -> "DeviceConfig":
        """The measured settings for a Weevia S1, with the camera found by name.

        Picking a camera by index is how the wrong one gets opened: plugging in any
        other camera shifts the order. Pass ``device=`` to skip the lookup.

        Windows: DirectShow lists the cameras. Linux: the `/dev/videoN` capture node whose name contains
        'WN Dual Camera' in `/sys/class/video4linux`, and `backend` defaults to "v4l2".
        Linux name lookup: implemented, not yet run on the camera.
        """
        from . import devices
        linux = sys.platform.startswith("linux")
        if linux:
            overrides.setdefault("backend", "v4l2")
        if "device" not in overrides:
            try:
                overrides["device"] = (devices.find_v4l2_node(WEEVIA_S1_NAME) if linux
                                       else devices.resolve_by_name(WEEVIA_S1_NAME).index)
            except LookupError as exc:
                raise DeviceError(f"Weevia S1 ({WEEVIA_S1_NAME!r}) not found. {exc}") from exc
        return cls(**{**WEEVIA_S1, **overrides})

    @classmethod
    def recording(cls, path: str | Path, **overrides: Any) -> "DeviceConfig":
        """Plays a video file made by `examples/record_video.py` (both eyes side by side, left eye in the left
        half) as if it were the camera. `read()` raises `EndOfRecording` after the last frame. `host_ns` is the
        time the frame was read back, not when it was captured — the recording's CSV has the capture times.
        A compressed recording does not have the camera's pixel values."""
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
    """The mode the device actually gave (`cap.get` readback). It can differ from what was requested."""

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
        self._controls: Controls | None = None
        self._controls_lock = threading.Lock()

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
        if self._mode is None:
            raise DeviceError("no negotiated mode before open()")
        return self._mode

    # ---- frames ------------------------------------------------------------
    def read_raw(self) -> tuple[np.ndarray, int]:
        """The raw frame before the split, and the host time (ns). A failure raises."""
        if self._mode is None:
            raise DeviceError("call open() first")
        ok, img = self._cap.read()
        t = time.perf_counter_ns()
        if not ok or img is None:
            if isinstance(self.config.device, str) and Path(self.config.device).is_file():
                raise EndOfRecording(f"end of the recording: {self.config.device}")
            raise DeviceError("frame read failed (timeout or camera unplugged)")
        return img, t

    def read(self) -> StereoFrame:
        img, t = self.read_raw()
        left, right = split_stereo(img, self.config.layout, self.config.lr_order,
                                   to_gray=self.config.to_gray)
        self._check_stall(left, right)
        frame = StereoFrame(left=left, right=right, host_ns=t, index=self._index)
        self._index += 1
        return frame

    def _check_stall(self, left: np.ndarray, right: np.ndarray) -> None:
        n = int(self.config.stall_frames)
        prev, self._prev = self._prev, (left, right)
        if not n or prev is None:
            return
        for i, (eye, now, before) in enumerate((("left", left, prev[0]), ("right", right, prev[1]))):
            self._same[i] = self._same[i] + 1 if np.array_equal(now, before) else 0
            if self._same[i] + 1 >= n:
                raise DeviceError(
                    f"The {eye} eye has sent the same image for {n} frames in a row: its stream has stopped. "
                    "Re-insert the USB cable: unplug it and plug it back in (it sometimes takes two tries). "
                    "stall_frames=0 in DeviceConfig turns this check off.")

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
        return Controls(OpenCVBackend(self._cap, self._flavor(), reason=reason))

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
