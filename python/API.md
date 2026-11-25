# opticmix-vision API reference

Reference for the public Python API of `opticmix_vision` 0.0.8. It lists every
public name, with its signature as `inspect.signature` reports it. What has been
run against the camera, and on which platform, is stated in [README.md](README.md)
and not repeated here.

Public modules: [`opticmix_vision`](#opticmix_vision) (package),
[`device`](#opticmix_visiondevice), [`devices`](#opticmix_visiondevices),
[`controls`](#opticmix_visioncontrols), [`led`](#opticmix_visionled),
[`synthetic`](#opticmix_visionsynthetic), [`recording`](#opticmix_visionrecording),
[`calibration`](#opticmix_visioncalibration),
[`calib`](#opticmix_visioncalib), [`presets`](#opticmix_visionpresets), [`doctor`](#opticmix_visiondoctor),
[`viewer`](#opticmix_visionviewer),
[`explorer`](#opticmix_visionexplorer). Names starting with `_` are private, and so
are modules not listed here.

Conventions:

- Signatures are written as Python reports them. Keyword-only parameters whose
  names start with `_` are test hooks and are left out.
- `<factory>` marks a dataclass field whose default is built per instance
  (an empty `dict` or `list`).
- Most examples use `opticmix_vision.synthetic` so they run without a camera. Replace
  `SyntheticStereoSource()` with `Device(DeviceConfig.weevia_s1()).open()` for the
  camera.
- Camera controls, device listing and calibration checks run in the compiled
  extension `opticmix_vision._core`, which the wheel contains. Without it (a source
  checkout that was not installed), using them raises `ControlsUnavailable`;
  frame capture still works.

---

## `opticmix_vision`

The package re-exports the most used names, so `from opticmix_vision import Device`
works. `opticmix_vision.__all__`:

| Name | Defined in |
|---|---|
| `__version__` | package — the version string, `"0.0.8"` |
| `Device`, `DeviceConfig`, `DeviceError`, `EndOfRecording`, `NegotiatedMode`, `StereoFrame`, `split_stereo`, `LAYOUTS`, `LR_ORDERS` | [`opticmix_vision.device`](#opticmix_visiondevice) |
| `LedControl`, `LedControlUnavailable`, `DEFAULT_LED_PROP` | [`opticmix_vision.led`](#opticmix_visionled) |
| `Controls`, `ControlInfo`, `WriteResult`, `ControlsUnavailable`, `ControlStateError`, `CONTROLS`, `write_counts`, `reset_write_counts` | [`opticmix_vision.controls`](#opticmix_visioncontrols) |

```python
import opticmix_vision
print(opticmix_vision.__version__)
```

---

## `opticmix_vision.device`

Opening the camera with OpenCV `VideoCapture`, reading frames, and splitting one
frame into the left and right eye.

How the two eyes are packed into one frame (`layout`) and which comes first
(`lr_order`) are settings. `DeviceConfig.weevia_s1()` fills them in for the
Weevia S1; for any other camera you give both, and there are no defaults.

| `layout` | Frame | Split |
|---|---|---|
| `"sbs"` | side by side, (H, 2W) | first = left half, second = right half |
| `"tb"` | stacked, (2H, W) | first = top half, second = bottom half |
| `"plane_pack"` | two channels, (H, W, 2) | first = channel 0, second = channel 1 |

| `lr_order` | Meaning |
|---|---|
| `"first_is_left"` | the first half is the left camera |
| `"first_is_right"` | the first half is the right camera (the Weevia S1 setting) |

### `LAYOUTS`

`("sbs", "tb", "plane_pack")` — accepted values of `layout`.

### `LR_ORDERS`

`("first_is_left", "first_is_right")` — accepted values of `lr_order`.

### `BACKENDS`

`("any", "dshow", "msmf", "v4l2", "avfoundation")` — accepted values of
`DeviceConfig.backend`, mapped to OpenCV `CAP_ANY`, `CAP_DSHOW`, `CAP_MSMF`,
`CAP_V4L2` and `CAP_AVFOUNDATION`.

### `DeviceError`

Subclass of `RuntimeError`. Opening or reading the camera failed. `read()` never
returns an empty frame; it raises this instead.

### `EndOfRecording`

Subclass of `DeviceError`. `read()` reached the end of a recording file opened
with `DeviceConfig.recording`.

### `DeviceConfig(layout: str, lr_order: str, device: int | str = 0, backend: str = "any", width: int | None = None, height: int | None = None, fps: float | None = None, fourcc: str | None = None, to_gray: bool = True, buffer_size: int | None = 1, extra_props: Mapping[str, float] = <factory>, stall_frames: int = 90)`

Camera settings (frozen dataclass). `width`, `height`, `fps` and `fourcc` are
requests; what the driver gave is in `Device.mode`.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `layout` | `str` | required | one of `LAYOUTS` |
| `lr_order` | `str` | required | one of `LR_ORDERS` |
| `device` | `int \| str` | `0` | OpenCV device index, or a path such as `"/dev/video0"` |
| `backend` | `str` | `"any"` | one of `BACKENDS` |
| `width`, `height` | `int \| None` | `None` | requested frame size; `None` sends nothing and keeps the driver's mode |
| `fps` | `float \| None` | `None` | requested frame rate; `None` sends nothing |
| `fourcc` | `str \| None` | `None` | requested pixel format, 4 characters (e.g. `"MJPG"`); `None` sends nothing |
| `to_gray` | `bool` | `True` | convert a 3-channel decoded frame to 8-bit gray before splitting |
| `buffer_size` | `int \| None` | `1` | `CAP_PROP_BUFFERSIZE`; `None` sends nothing |
| `extra_props` | `Mapping[str, float]` | `{}` | OpenCV properties set after opening, e.g. `{"CAP_PROP_EXPOSURE": -5}`. Camera controls among them go through the `controls` write guard. `CAP_PROP_FRAME_WIDTH`, `CAP_PROP_FRAME_HEIGHT`, `CAP_PROP_FPS` and `CAP_PROP_FOURCC` are refused here (`DeviceError` on `open()`): use the fields above |
| `stall_frames` | `int` | `90` | `read()` raises `DeviceError` when one eye sends the byte-identical image this many frames in a row. `0` turns the check off; otherwise 2 or more. The same comparison counts `Device.repeated_frames`, so `0` turns that off too (it is then `None`). Background: README, "When something goes wrong" |

Raises `ValueError` when a field is out of range (unknown `layout`, `lr_order` or
`backend`; non-positive `width`, `height` or `fps`; a `fourcc` that is not 4
characters; `stall_frames` negative or 1).

```python
from opticmix_vision import DeviceConfig

cfg = DeviceConfig(layout="sbs", lr_order="first_is_right", device=1,
                   width=1600, height=800, fps=90, fourcc="MJPG")
```

#### `DeviceConfig.weevia_s1(*, usb_location: str | None = None, **overrides: Any) -> DeviceConfig`

Class method. The settings for a Weevia S1, with the camera found by name:
`layout="sbs"`, `lr_order="first_is_right"`, `backend="dshow"`, 1600×800,
`fps=90.0`, `fourcc="MJPG"`. Any field can be overridden by keyword.

- Windows: the device index is found with `devices.resolve_by_name("WN Dual Camera")`
  (the DirectShow list).
- Windows with `backend="msmf"`: the index is found with
  `devices.resolve_by_name("WN Dual Camera", backend="msmf")` — the Media Foundation
  list, which is the list OpenCV's `CAP_MSMF` counts device indexes in (OpenCV 4.13.0
  `modules/videoio/src/cap_msmf.cpp`: `CvCapture_MSMF::open(int)` activates entry N of
  `MFEnumDeviceSources` with the video-capture source type). The two lists need not be
  in the same order. Media Foundation lookup and Python MSMF capture: run once on one PC
  with one camera (2026-11-24); see "Media Foundation through OpenCV" under `StereoFrame`.
  Opened through MSMF, the camera controls are the OpenCV fallback: no ranges or
  defaults, and no `power_line_frequency` or `low_light_compensation` — the price of
  getting `StereoFrame.driver_ns`.
- Windows with `backend="any"`: OpenCV tries its capture backends in priority order
  (Media Foundation before DirectShow in OpenCV 4.13, `videoio_registry.cpp`) and,
  when one fails to open, tries the next with the same index. So the index is used
  only when the Weevia S1 has the same index in both lists; otherwise `DeviceError`
  asks for `backend="msmf"` or `backend="dshow"`. `Device.open()` also refuses a
  device that `backend="any"` opened by index on Windows through any other backend.
- Linux: the device is `devices.find_v4l2_node("WN Dual Camera")` and `backend`
  defaults to `"v4l2"`. As the README puts it: "Linux name lookup:
  run with the camera once (one PC, one camera, 2026-11-25)."
- Passing `device=` skips the lookup.
- `usb_location=`: with two Weevia S1 connected, the one at that USB location
  (`DeviceInfo.usb_location` as `examples/list_devices.py` prints it; on Linux the USB
  device's sysfs name, e.g. `1-4.2`) — passed to the lookup of the backend it opens
  with (`resolve_by_name(..., usb_location=)` or `find_v4l2_node(..., usb_location=)`).
  The location is turned into the backend's index (or node) when the config is made, and the
  camera is opened by that index: a camera plugged in or out in between can leave that index
  to another device (the tools' `--usb-location` works the same way).
  Passing it with `device=` raises `ValueError`. A USB location is not a serial number: it names the port the camera is plugged into, and it changes when the camera is plugged into another port. Using two cameras at the same time is not supported. Picking a camera by USB location has been run with the camera once (one PC, one camera, 2026-11-25): with one camera connected, the list showed its USB location, and given that location it opened at 1600x800 MJPG and read one frame; a location no device has was refused (Python DirectShow, Rust Media Foundation); not run with two cameras.

Raises `DeviceError` when no camera, or more than one, matches the name (and the
USB location, when given).

```python
cfg = DeviceConfig.weevia_s1(fps=60)
cfg = DeviceConfig.weevia_s1(device="/dev/video2", backend="v4l2")   # name the node yourself
cfg = DeviceConfig.weevia_s1(usb_location="PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(4)#USB(2)")   # one of two
```

#### `DeviceConfig.recording(path: str | Path, **overrides: Any) -> DeviceConfig`

Class method. Plays a recording (written by [`recording.Recorder`](#recorderpath-str--path-source-any--codec-str--mjpg-fps-float--none--none-controls-callable-dictstr-any--none--none--none-controls_read_at-str--none--none-queue_frames-int--0),
the viewer's `v` key or `examples/record_video.py`: both eyes side by side, left eye
in the left half) as if it were the camera: `layout="sbs"`,
`lr_order="first_is_left"`, `stall_frames=0`. This is the order in the file, not the
camera's: the recording holds the eyes after the split, left first; the camera
itself is `first_is_right`. `read()` raises `EndOfRecording` after the last frame.

On the opened `Device`, [`playback`](#deviceplayback) seeks, sets the speed and
real-time pacing, loops and pauses; `mode` is the mode the recording was made in and
`recording_info` is its JSON. A frame's `index` is its number in the file. `host_ns`
is the time the frame was read back, unless `playback.recorded_time` is set (then it
is the time recorded in the CSV). A compressed (MJPG) recording does not have the
camera's pixel values; the lossless (FFV1) file keeps the pixel values the SDK
delivered (generated frames only; not run with the camera).
Recording with `Recorder` and reading a recording back, seeking and looping: verified with the published 0.0.7 wheel on one Weevia S1 unit, Windows 11, one PC, two-level USB hub, two USB re-insertions, read-only (the files were written and read back with the same frame count — not that both eyes showed the scene in every recording: see "One eye goes black or stops moving"). The speed factor (`set_speed`): not verified; pausing: generated frames only; not run with the camera. The Rust crate (`opticmix-vision`: `Recorder`, `Device::open_recording`) writes and plays the same MJPG `.avi` files, CSV and JSON; it neither writes nor plays FFV1, and its recordings name a checked V4L2 clock `v4l2_monotonic` in the CSV. Rust recording and playback: generated frames only; not run with the camera.

Raises `DeviceError` when the file does not exist.

```python
with Device(DeviceConfig.recording("recording.avi")) as cam:
    pb = cam.playback
    print(pb.frame_count, pb.duration_s, cam.mode.to_dict())
    pb.seek(seconds=2.0)              # or pb.seek(120) for frame 120
    pb.real_time = True               # at the recorded intervals
    pb.set_speed(0.5)                 # half speed
    try:
        while True:
            frame = cam.read()        # frame.index is the frame number in the file
    except EndOfRecording:
        pass
```

#### `DeviceConfig.from_bringup_constants(path: str | Path, **overrides: Any) -> DeviceConfig`

Class method. Builds a config from an `oem_uvc_constants.py` file written by a
bring-up tool: a Python file of upper-case constant assignments (`BACKEND`,
`DEVICE`, `RESOLUTION`, `FPS`, `FOURCC`, `LAYOUT`, `LR_ORDER`). The file is
executed — pass only a file you trust.

Raises `ValueError` when `BACKEND` is not `"opencv"`, or when `LAYOUT` or
`LR_ORDER` is missing or not an accepted value. Nothing is guessed.

#### `DeviceConfig.replace(**changes: Any) -> DeviceConfig`

A copy with the given fields changed (`dataclasses.replace`).

### `NegotiatedMode(width: int, height: int, fps: float, fourcc: str, backend_name: str)`

The mode the driver actually gave, read back with `cap.get` after opening. It can
differ from what was requested. Fields: `width`, `height` (whole frame, both eyes),
`fps`, `fourcc` (`""` when the driver reports none), `backend_name` (OpenCV's
backend name, `""` when not available). Through `backend="msmf"`, `fourcc` is OpenCV's output format
code (RGB32 `'\x16\x00\x00\x00'`, or RGB24 `'\x14\x00\x00\x00'` without Direct3D 11), not the
camera's format; see Media Foundation under `StereoFrame`.

#### `NegotiatedMode.to_dict() -> dict[str, Any]`

The five fields as a `dict`.

### `StereoFrame(left: np.ndarray, right: np.ndarray, host_ns: int, index: int, driver_ns: int | None = None, driver_clock: str | None = None, driver_sequence: int | None = None)`

One stereo frame (frozen dataclass).

| Field | Meaning |
|---|---|
| `left`, `right` | (h, w) `uint8` images of the left and right eye. With `split_stereo`, these can be views into the raw frame |
| `host_ns` | host monotonic clock (`time.perf_counter_ns()`) taken right after the frame was read. It is not a device timestamp. `time.perf_counter_ns()` gives now on the same clock (the Rust crate's `host_ns` is another clock: its `host_now_ns()`, from 0.0.8) |
| `index` | frame number counted by this `Device`, from 0 (from a recording: the frame's number in the file). It is not a device counter |
| `driver_ns` | the operating system capture driver's time stamp for the frame, in ns, as OpenCV passes it on in `CAP_PROP_POS_MSEC` (OpenCV 4.13 sources: `cap_msmf.cpp`, `cap_v4l.cpp`), converted back to whole driver units. `None` with DirectShow — OpenCV returns -1 there, and DirectShow is the Weevia S1 default on Windows (`DeviceConfig.weevia_s1(backend="msmf")` gives Media Foundation; see "Media Foundation through OpenCV" below) — and for a recording, unless `Playback.recorded_time` is set: then the value recorded in its CSV. Opened through MSMF, the camera controls are the OpenCV fallback (no ranges or defaults, no `power_line_frequency` or `low_light_compensation`): that is the price of this time stamp |
| `driver_clock` | the clock `driver_ns` is on, `None` exactly when it is: `"media_foundation"` = the sample time (100 ns units) Media Foundation's source reader gives with the converted sample OpenCV receives (see below). On one PC (Windows 11) with one camera (2026-11-24, once) the first Media Foundation time was about 364,400 s — not the start of streaming; on this PC it was within a few seconds of Python's `time.perf_counter_ns()` (compared across two runs by file times, not within one process), but what the clock's zero is has not been established. Use differences between frames, not the absolute value; `"v4l2"` = the V4L2 buffer time stamp (microseconds) — OpenCV does not pass on the buffer's clock flag, so whether it is `CLOCK_MONOTONIC` is not checked (the Rust crate checks it); `"v4l2_monotonic"` = only when playing back a recording the Rust crate wrote (`Playback.recorded_time`): the Rust crate's name in the CSV for a V4L2 time it checked to be on `CLOCK_MONOTONIC`, kept apart from `"v4l2"`. Rust recording on Linux has not been run |
| `driver_sequence` | the driver's frame sequence number. Always `None` here: OpenCV does not pass on the V4L2 sequence number, and Media Foundation has none. The Rust crate gives it on Linux, with `Device::driver_missed_frames()` counting the gaps, and also `driver_time_source` (start of exposure or end of frame, as the V4L2 driver reports it in the buffer flags, which OpenCV does not pass on) |

None of the times is a camera (device) time: the camera sends no clock this package reads. Whether a
driver time marks the exposure or the arrival of the frame is not known; the gap between the driver time and
`host_ns` has not been measured. Frames missing between driver sequence numbers are counted from the driver's
own numbers in Rust only; `Device.late_frames` and `Device.estimated_missed_frames` are estimated from
`host_ns` intervals instead, so the two can differ. V4L2 driver times have been run without a camera only (made-up
driver times); not run with the camera.

Media Foundation through OpenCV (`backend="msmf"`). OpenCV 4.13.0 `modules/videoio/src/cap_msmf.cpp`: it asks Media Foundation's source reader to convert the camera's format to RGB — RGB32 when it opens a Direct3D 11 device, otherwise RGB24 (lines 1153–1159, 1039) — and `CAP_PROP_FOURCC` returns the first field of that output type (lines 2170–2171: RGB32 is 22, `'\x16\x00\x00\x00'`; RGB24 is 20), not `MJPG`; `CAP_PROP_POS_MSEC` is the time the source reader gave with that converted sample, divided by 10,000 (lines 1824, 2181–2182); each read takes the next sample from a queue of at most three and drops the queued ones when it is full (lines 411, 461–472, 536–548), so OpenCV's live-capture path does not hand back the previous sample. So `driver_ns` here is the converted sample's time; the Rust crate reads the camera's MJPG samples from Media Foundation without conversion. Run with the camera once (one PC, one camera, 2026-11-24; `examples/measure_fps.py`, Media Foundation, 900 frames): 276 of 900 frames per eye repeated the previous image, and the intervals between `driver_ns` times were 11.11 ms (mean, min and max), so on this path `driver_ns` does not show whether a new image arrived; the cause has not been established. On the same PC and camera, DirectShow gave 0 of 900 and 0 of 450 repeated images, and the Rust crate's `measure_fps` (Media Foundation, no conversion) gave 0 of 450 with driver-time intervals from 0.01 to 16.90 ms (mean 11.23). Host-measured rates: 90.1 frames per second (Python, Media Foundation) and 89.1 (Rust). `Device.repeated_frames` counts such frames, and `read()` warns once per `open()` when they pass 5 % of the last 300 through this backend.

#### `StereoFrame.size`

Property. `(width, height)` of one eye's image.

### `split_stereo(frame: np.ndarray, layout: str, lr_order: str, *, to_gray: bool = True) -> tuple[np.ndarray, np.ndarray]`

Pure function: splits one frame into `(left, right)`. Returns views where possible;
copy them if you keep them past the next frame.

- `sbs` / `tb`: a 3-channel frame is converted to gray first when `to_gray=True`.
  With `to_gray=False` the frame must already be 2-D.
- `plane_pack`: expects (H, W, 2); a 2-D (H, 2W) buffer is read as (H, W, 2).

Raises `ValueError` for an unknown `layout` or `lr_order`, an odd width (`sbs`) or
height (`tb`), or a frame shape the layout cannot split.

```python
import numpy as np
from opticmix_vision import split_stereo

frame = np.zeros((800, 1600), np.uint8)
left, right = split_stereo(frame, "sbs", "first_is_right")
print(left.shape)   # (800, 800)
```

### `Device(config: DeviceConfig, *, capture: Any = None)`

A thin wrapper over OpenCV `VideoCapture`. Use it as a context manager:
`with Device(cfg) as cam:` opens on entry and closes on exit.

`capture` takes an object with the `VideoCapture` methods `get`, `set`, `read`,
`isOpened` and `release` (and optionally `getBackendName`), for tests. An injected
capture is not released by `close()`.

The settings are kept in the attribute `config`.

#### `Device.open() -> Device`

Opens the camera, sends the requested mode, reads back the negotiated mode, then
applies `extra_props`. Returns `self`; calling it again on an open device does
nothing. Each `open()` starts the frame statistics, the `stall_frames` count and
`repeated_frames` afresh: the first frame after it is not compared with a frame read
before `close()`.

On DirectShow the pixel format is set after size and frame rate; on V4L2 it is set
before the frame rate. The order is handled here, so set the mode through
`DeviceConfig`, not with your own `cap.set` calls.

Raises `DeviceError` when the camera cannot be opened, when the OpenCV build lacks
the requested backend, when `extra_props` names a format property, or when an
`extra_props` control write fails. On Windows with `backend="any"` and a device
index, it also raises (and lets the device go) when OpenCV opened it through a
backend other than Media Foundation or DirectShow: OpenCV tries every camera backend
with the same index, so the index may name another device there.

#### `Device.close() -> None`

Closes the controls (pending writes end as `cancelled`) and releases the capture.
Safe to call more than once.

#### `Device.is_open`

Property. `True` between `open()` and `close()`.

#### `Device.mode`

Property. The `NegotiatedMode`. For a recording, the mode it was recorded in (from
its JSON; without one, the video file's own size, rate and codec). Raises
`DeviceError` before `open()`.

#### `Device.read() -> StereoFrame`

Reads one frame and splits it according to `config.layout` and `config.lr_order`.
Raises `DeviceError` when the read fails (timeout or camera unplugged), when
called before `open()`, or when the `stall_frames` check trips (the message names
the eye). From a recording, `EndOfRecording` after the last frame.

#### `Device.playback`

Property. The [`recording.Playback`](#playbackpath-str--path) when the device plays a
recording (`DeviceConfig.recording`): `seek`, `position`, `frame_count`,
`duration_s`, `set_speed`, `real_time`, `loop`, `recorded_time`, `pause`,
`resume`. `None` for a camera.

#### `Device.recording_info`

Property. The capture info saved with the recording (its `.json`, see
[`capture_info`](#capture_infosource-any--controls-dictstr-any--none--none-controls_read_at-str--none--none-extra-any---dictstr-any)): the mode it was
recorded in, the camera's layout and left/right order, the control values when
recording started, the SDK version, codec, `compressed`, frame count. `None` for a
camera, or for a recording without a `.json`.

#### `Device.late_frames`

Property. Frames since `open()` that arrived more than 1.5 frame periods (at
`mode.fps`) after the frame before. Estimated from the host times (`host_ns`), not
read from a device frame counter: a late frame can be delivery jitter rather than a
lost frame. For a recording, counted on the recorded times. Unrelated to the Rust
`Device::dropped_frames()` (undecodable samples skipped).

#### `Device.estimated_missed_frames`

Property. Frames estimated missing since `open()`: for each late interval,
`round(interval / period) - 1`. Estimated from host times, not a device counter.

#### `Device.actual_fps`

Property. Frames per second over the last 30 frames, from host times; `0.0` until
two frames in a row are read. For a recording, the recorded rate.

The three statistics have been run on generated frames only; not run with the
camera. The Rust crate has the same three, with the same rule: `Device::late_frames()`,
`estimated_missed_frames()` and `actual_fps()` (Rust `measure_fps` printed them with the camera once: one PC, one camera, 2026-11-24). Rust counts every sample,
including those `read_raw()` returns and those that did not decode; Python counts the frames `read()` returns.

#### `Device.repeated_frames`

Property. Frames `read()` returned since `open()` whose two eyes were both
byte-identical to the frame before — counted by the comparison the `stall_frames`
check already makes, so it costs nothing more. `None` when `stall_frames=0` (no
comparison is made). Opened through OpenCV's Media Foundation backend
(`mode.backend_name == "MSMF"`), `read()` gives one `UserWarning` per `open()` when
more than 5 % of the last 300 frames repeated (more than 15 of 300); it names
DirectShow (`backend="dshow"`, `DeviceConfig.weevia_s1()`'s default on Windows) and
the Rust crate. No warning through other backends or from a recording. Why: on one
PC with one camera (2026-11-24) 276 of 900 frames per eye through OpenCV's Media
Foundation backend repeated the previous image; repeats were not seen through
DirectShow (0 of 900) or the Rust crate (0 of 450); the cause has not been
established (see "Media Foundation through OpenCV" under `StereoFrame`). Whether a
live eye sends byte-identical images in a dark or covered scene has not been measured. The count and the warning have been run
with the camera once through each backend (one PC, one camera, 2026-11-25;
`examples/measure_fps.py`, whose output does not record the OpenCV version): through `backend="msmf"` the warning came once and the count was 271 of 900;
through DirectShow 0 of 900, no warning. The Rust crate has the same count,
`Device::repeated_frames() -> Option<u64>` (`None` when `stall_frames` is 0), from the
same comparison of the frames its `read()` decoded — not the samples it skipped as
undecodable or those `read_raw()` returns — and with no warning; run with the camera once (one PC, one camera, 2026-11-25, Media Foundation, 450 frames): 0, the same as the `measure_fps` example's own per-eye counts (left 0, right 0); a count above 0: generated frames only.

#### `Device.read_raw() -> tuple[np.ndarray, int]`

The raw frame before the split, and the host time in ns
(`time.perf_counter_ns()`). Raises `DeviceError` like `read()`. Does not run the
`stall_frames` check.

#### `Device.controls`

Property. The camera's `Controls`, created on first use. On Windows with
`backend="dshow"` and an integer `device`, controls go to DirectShow directly; if
that is not available, or on other backends, they go through OpenCV
(`Controls.backend_note` says why). Raises `DeviceError` before `open()`, and for a
recording (its control values are in `recording_info["controls"]`).

On Linux (V4L2) the OpenCV path also asks the driver for each control's range and
default (`VIDIOC_QUERYCTRL` on a second, read-only handle to the device node).
This only adds information: `Controls.describe()` gives `minimum`, `maximum`,
`step` and `default` for the controls the driver reports, `Controls.driver_names`
says which those are, and `set()` / `set_many()` refuse a value outside a range
the driver reports before anything is sent. `names()`, `get()`, `set()` and
presets otherwise work as in 0.0.6, through OpenCV. `reset_to_defaults()` still
writes nothing on Linux: the driver's defaults are shown, not written
(`plan_reset()` is empty, `Controls.writes_defaults` is `False`). If the driver
cannot be asked, there are no ranges or defaults, as before, and
`Controls.backend_note` says why. Run with the camera on Linux once (one PC, one camera, 2026-11-25, from
source before release): the driver answered on the read-only handle, also while streaming. Camera controls on
Linux remain not verified and not supported.

#### `Device.calibration`

Property. The `StereoCalibration` installed with
`python -m opticmix_vision.calib add <file.json>`, when exactly one is installed
(see [`calib.resolve`](#resolve---stereocalibration)). The SDK cannot check that the
file belongs to this camera: the camera reports no USB serial number
(`calib.check` measures whether it fits). Raises
`calib.CalibLookupError` when none or more than one is installed, or when the
file's eye size does not match the frame (half the frame width × frame height).
Frame capture does not need a calibration.

Rust: `Device::calibration()` in the `opticmix-vision` crate reads the same file by
the same rules (`calib::resolve()`, then the eye size against the opened mode), with
the same messages. Run without a camera only (made-up calibration files); not run
with the camera.

#### `Device.get_prop(prop: str | int) -> float`

Compatibility API. Reads an OpenCV property by name (`"CAP_PROP_GAIN"`) or id.
Camera controls are read through `controls`. Raises `DeviceError` before `open()`.

#### `Device.set_prop(prop: str | int, value: float, *, wait: bool = True) -> bool`

Compatibility API. Writes a camera control by OpenCV property name; the write goes
through the `controls` guard. `CAP_PROP_EXPOSURE` also switches exposure to manual.
With `wait=True` it waits until the write is done. Returns `True` when written or
already at that value, `False` when the driver refused it.

Raises `DeviceError` before `open()` or for a property that is not a camera control
(set size, frame rate and format in `DeviceConfig`), and `ValueError` for a
fractional value on an integer control.

```python
from opticmix_vision import Device, DeviceConfig

with Device(DeviceConfig.weevia_s1()) as cam:
    print(cam.mode.to_dict())
    frame = cam.read()
    print(frame.size, frame.index)
```

### `prop_write_counts() -> dict[str, int]`

Compatibility alias of [`controls.write_counts()`](#write_counts---dictstr-int).

### `reset_prop_write_counts() -> None`

Compatibility alias of [`controls.reset_write_counts()`](#reset_write_counts---none).

---

## `opticmix_vision.devices`

Listing connected cameras and picking one by name. The camera reports no USB
serial number and device indexes shift when another camera is plugged in, so
selection is by name — and, when two cameras share the name, by USB location.
`list_devices` is for Windows and Linux (`backend="v4l2"`, the default on Linux);
`resolve_by_name`, `pick_by_usb_location` and `list_modes` are for Windows;
`find_v4l2_node` is for Linux.

A USB location is not a serial number: it names the port the camera is plugged into, and it changes when the camera is plugged into another port. Using two cameras at the same time is not supported. Picking a camera by USB location has been run with the camera once (one PC, one camera, 2026-11-25): with one camera connected, the list showed its USB location, and given that location it opened at 1600x800 MJPG and read one frame; a location no device has was refused (Python DirectShow, Rust Media Foundation); not run with two cameras.

### `DeviceInfo(index: int, name: str, usb_location: str | None = None, vid: int | None = None, pid: int | None = None, bcd_device: int | None = None, link_speed: str | None = None)`

A video device: its index in the list it came from (DirectShow, or Media Foundation
with `backend="msmf"`; each in the order OpenCV's backend of that name uses), its
name, and where it is plugged in. `usb_location` is the first entry of the USB
device's `DEVPKEY_Device_LocationPaths` (for one interface of a composite device,
its parent's), read from the Windows device tree through cfgmgr32 without opening
the device — for example `PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(4)#USB(2)`, the same
string `python -m opticmix_vision.doctor` prints as `location`. The device is found
from its DirectShow DevicePath (ffmpeg's "Alternative name") or its Media Foundation
symbolic link. `None` when the device is not a present USB device (a virtual camera,
for example) or the location cannot be read. Linux (`list_devices(backend="v4l2")`):
the index is the N of `/dev/videoN`, the name the node's sysfs `name`, and
`usb_location` the sysfs name of the USB device the node belongs to
(`/sys/class/video4linux/videoN/device` and its parents, the nearest with an
`idVendor` file), for example `2-1` — the `location` the doctor prints on Linux.

The other four describe the same USB device, read the way the doctor reads them and
without opening the device; each is `None` when it cannot be read:

- `vid`, `pid`, `bcd_device` (`int`): the USB vendor ID, product ID and device
  revision (`idVendor`, `idProduct`, `bcdDevice`, the last in binary-coded decimal),
  from the USB device's hardware IDs in the device tree
  (`USB\VID_1D6B&PID_0200&REV_0409` gives `0x1D6B`, `0x0200`, `0x0409`) — the
  doctor's `VID:PID` and "USB device revision (bcdDevice)". `bcd_device` is not a
  firmware version: how it relates to the firmware version has not been verified.
  `None` when the device is not a present USB device or its hardware IDs carry no
  `VID_…&PID_…&REV_…`.
- `link_speed` (`str`): the speed of the USB link as the hub driver the device is
  plugged into reports it for that port (asked as Microsoft's USBView asks; the
  doctor's "link speed"): `"Low"` (1.5 Mb/s), `"Full"` (12 Mb/s), `"High"`
  (480 Mb/s), `"Super"` (5 Gb/s) or `"Super+"` (10 Gb/s or more). `None` when the
  hub cannot be asked, when the hub's port holds another device, or when `vid` and
  `pid` could not be read. `"Low"`, `"Full"` or `"High"` is what the doctor reports
  as a finding: the README's measured figures were over SuperSpeed and USB 2 is not
  qualified — connect the camera to a USB 3 port directly.

`list_devices()` fills them on Windows and, with `backend="v4l2"`, on Linux, where
they come from the USB device's sysfs folder: `idVendor`, `idProduct` and
`bcdDevice` (hex) and `speed` (Mb/s: 1.5 `"Low"`, 12 `"Full"`, 480 `"High"`, 5000
`"Super"`, more than 5000 `"Super+"`), read by the function the doctor reads them
with, so the two give one value. Picking from a list (`pick_by_usb_location`) keeps
them. `resolve_by_name()`, which `DeviceConfig.weevia_s1()` uses to find the camera
before opening it on Windows, reads the USB location only, as 0.0.6 did — no
hardware IDs and no hub query before opening — so the `DeviceInfo` it returns has
them `None`; on Linux `DeviceConfig.weevia_s1()` uses `find_v4l2_node`, which reads
the USB location only too. A `DeviceInfo` you build yourself has them `None` unless
you pass them. Not reported by the camera, so there is no field for
them: a USB serial number, a firmware version (no verified way to read it) and a
temperature (no readout is known). Reading the USB IDs and the link speed into
these fields has been run without a camera only (made-up device lists; on Linux
made-up sysfs trees); it has not been run with the camera.

### `Mode(fourcc: str, width: int, height: int, max_fps: float)`

A mode the device reports.

#### `Mode.label() -> str`

Display text, e.g. `"1600×800 MJPG · 90 fps"`.

### `NoMatch`

Subclass of `LookupError`. No camera matches the name. The message lists what was
found.

### `MultipleMatches`

Subclass of `LookupError`. More than one camera matches the name; without a serial
number they cannot be told apart by name, so none is picked. When every match has a
USB location, the message lists the locations and says to pick one by
`usb_location`.

### `list_devices(*, backend: str | None = None) -> list[DeviceInfo]`

Video devices in the order a capture backend lists them; the index is the one
OpenCV's backend of that name takes. Built-in webcams are included.

- `backend=None` (the default): `"v4l2"` on Linux, `"dshow"` elsewhere — on Windows
  the default list is DirectShow's, as before.
- `backend="dshow"`: DirectShow. Uses `ffmpeg` when it is on `PATH`, otherwise
  enumerates DirectShow directly; when that fails too, returns an empty list.
- `backend="msmf"`: Media Foundation's video capture sources (`MFEnumDeviceSources`
  with the video-capture source type), the list OpenCV's `CAP_MSMF` counts device
  indexes in (OpenCV 4.13.0 `modules/videoio/src/cap_msmf.cpp`,
  `CvCapture_MSMF::open(int)`). Activates no device. An empty list off Windows.
  Microsoft does not document the order, or that two listings agree; a camera plugged
  in between listing and opening can shift it. Run with the camera once (one PC, one
  camera, 2026-11-24): it listed the camera by name; `resolve_by_name(backend="msmf")`
  run without a camera only.
- `backend="v4l2"`: Linux. The video capture nodes in `/sys/class/video4linux`,
  chosen as `find_v4l2_node` chooses them (sysfs `index` 0, so a UVC camera's
  metadata node is left out), in `/dev/videoN` order; the index is N, the device
  `DeviceConfig(device=N, backend="v4l2")` opens (OpenCV's `CAP_V4L2` opens
  `/dev/videoN`). Each carries its USB device's sysfs name and its IDs and link speed
  from sysfs (see `DeviceInfo`). Reads sysfs only; opens no device. An empty list off
  Linux. Run without a camera only (made-up sysfs trees); not run with the camera on Linux.

The two lists can be in a different order: on one PC with one camera (2026-11-24), `devices.list_devices(backend="msmf")` and `list_devices()` returned the same three cameras in a different order, once. An index goes only with the backend whose list it came from.
`examples/list_devices.py --backend msmf` prints the Media Foundation list, and
`python -m opticmix_vision.doctor` prints both. `examples/list_devices.py --backend msmf` and the doctor's Media Foundation list have been run without a camera only (made-up lists); they have not been run with the camera. Any other `backend`
raises `ValueError`. An extension (`opticmix_vision._core`) built from an older source
than these Python files raises `ControlsUnavailable` saying to rebuild it.

### `resolve_by_name(needle: str, *, usb_location: str | None = None, backend: str = "dshow") -> DeviceInfo`

The one device in `list_devices(backend=backend)` whose name contains `needle`
(case-insensitive), Windows. Raises `NoMatch` or `MultipleMatches`. With
`usb_location`, only the device at that USB location counts
(`pick_by_usb_location`); an empty or blank one raises `ValueError`. It reads the
USB location only, as 0.0.6 did (it is how `DeviceConfig.weevia_s1()` finds the
camera before opening it), so `vid`, `pid`, `bcd_device` and `link_speed` of what it
returns are `None`; `list_devices()` has them.
Picking a camera by USB location has been run with the camera once (one PC, one camera, 2026-11-25): with one camera
connected, the list showed its USB location, and given that location it opened at 1600x800 MJPG and read one frame; a
location no device has was refused (Python DirectShow, Rust Media Foundation); not run with two cameras.

```python
from opticmix_vision.devices import resolve_by_name

info = resolve_by_name("WN Dual Camera")                    # DirectShow index
mf = resolve_by_name("WN Dual Camera", backend="msmf")      # Media Foundation index
print(info.index, mf.index, info.name, info.usb_location)
```

### `pick_by_usb_location(needle: str, usb_location: str, devices: list[DeviceInfo]) -> DeviceInfo`

The one device in `devices` whose name contains `needle` (case-insensitive) and
whose `usb_location` equals `usb_location` (ignoring case) — how to choose between
cameras that share a name. Raises `NoMatch`, listing each device's name and USB
location (`None` where it could not be read), or `MultipleMatches`; an empty or
blank `usb_location` raises `ValueError`. A USB location is not a serial number: it
names the port the camera is plugged into, and it changes when the camera is plugged
into another port. Using two cameras at the same time is not supported.
Picking a camera by USB location has been run with the camera once (one PC, one camera, 2026-11-25): with one camera
connected, the list showed its USB location, and given that location it opened at 1600x800 MJPG and read one frame; a
location no device has was refused (Python DirectShow, Rust Media Foundation); not run with two cameras.

```python
from opticmix_vision.devices import list_devices, pick_by_usb_location

for d in list_devices():
    print(d.index, d.name, d.usb_location)
info = pick_by_usb_location("WN Dual Camera", "PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(4)#USB(2)", list_devices())
```

### `list_modes(name: str) -> list[Mode]`

The modes the named device reports (Windows, through `ffmpeg`). An empty list means
the modes could not be found out (for example, `ffmpeg` is not installed), not that
there are none.

### `find_v4l2_node(needle: str, *, usb_location: str | None = None) -> str`

Linux. The `/dev/videoN` path of the one video capture node whose name contains
`needle` (case-insensitive), read from `/sys/class/video4linux`. A UVC camera has
more than one node with the same name; the capture node is the one whose sysfs
`index` is 0. Raises `NoMatch` (listing the nodes found) or `MultipleMatches`. With
`usb_location`, only the node at that USB location counts: the sysfs name of the USB
device the node belongs to (`/sys/class/video4linux/videoN/device` and its parents,
the nearest with an `idVendor` file), for example `1-4.2`, compared ignoring case —
the `location` the doctor prints on Linux; an empty or blank one raises
`ValueError`. Run on Linux with one camera once (one PC, one camera, 2026-11-25, the 0.0.6 wheel): opened at its
location; a location with no camera refused. Not run with two cameras.

---

## `opticmix_vision.controls`

Every UVC control the camera advertises: read the value, range and default, and
write. Get a `Controls` from `Device.controls` (or
`SyntheticStereoSource.controls`); you do not build one yourself for a camera.

Writes go through one worker per camera, in order. Identical values are not
written, requests inside the minimum interval collapse to the latest value, and
writes are spaced by the guard constants below. Before writing to the camera, read
README, "Camera controls" — it says: "The guard does not make writes safe.
**Write once at startup.**"

Control names (`CONTROLS` order): `brightness`, `contrast`, `hue`, `saturation`,
`sharpness`, `gamma`, `white_balance`, `auto_white_balance`,
`backlight_compensation`, `gain`, `power_line_frequency`, `exposure`,
`auto_exposure`, `auto_exposure_priority`. Values are `int` for integer controls,
`bool` for on/off controls, and a string for menus: `power_line_frequency` takes
`"disabled"`, `"50hz"`, `"60hz"`; `auto_exposure` takes `"manual"`, `"auto"`.
What each control does on the camera: README, "Camera controls".

Errors from reads and writes:

| Exception | When |
|---|---|
| `KeyError` | the name is not a control of this camera |
| `ValueError` | the value is invalid for the control: not a whole number for an integer control, not `True`/`False` for an on/off control, not one of `choices` for a menu |
| `ControlStateError` | the current mode cannot accept the write, e.g. `exposure` alone while `auto_exposure` is `"auto"` — give both in one `set_many` |
| `ControlsUnavailable` | the control channel cannot be used: backend connection failed, `Controls` closed, or the compiled extension missing |
| `OSError` | the backend failed |

Write requests are checked when they are made; if any value in a `set_many` is
invalid, nothing is queued.

Setting the environment variable `OPTICMIX_WRITE_LOG=<path>` appends one JSON line
per actual write (with the read-back value) to that file. Off by default.

### `ControlsUnavailable`

Subclass of `RuntimeError`. The control channel cannot be opened or used: backend
connection failed, a different device, a call after `close()`, or the compiled
extension is missing.

### `ControlStateError`

Subclass of `RuntimeError`. A write the current mode cannot accept.

### `Controls(backend: Backend, *, guard: bool = True, note: str = "")`

All controls of one camera. Reads happen immediately; writes are queued to the
worker. `guard=False` removes the spacing and the counters; it is for the synthetic
camera, not for a real device. Usable as a context manager (closes on exit).

Attributes: `backend_name` (`str`, the backend in use), `backend_note` (`str`,
why a fallback backend is in use, or what it lacks; may be empty) and `model`
(`str | None`: `"WN Dual Camera"` — the camera's USB name — when the DirectShow
backend has checked it, or, for the OpenCV fallback, when `Device` finds that name
for the opened device in the DirectShow list, or the Media Foundation list with
`backend="msmf"` (Windows), or in
`/sys/class/video4linux/videoN/name` (Linux); `"synthetic"` for the simulated
controls; `None` when it cannot be told). Presets record `model` and check it when
loading.

#### `Controls.names() -> tuple[str, ...]`

Control names usable on this camera, in table order — only those the backend
supports.

Whether the camera itself reports a control: on Windows (DirectShow) the backend
reads each control from the device, so a control the camera lacks is not in
`names()`. The OpenCV path lists every control OpenCV can address, whether or not
the camera has it; on Linux it also asks the driver, and `driver_names` holds the
controls the driver reported (with their ranges in `describe()`). `driver_names`
is `None` when the backend did not ask the driver.

#### `Controls.driver_names`

Read only. `frozenset[str] | None`: names of the controls the camera's driver
reported when asked — on Windows (DirectShow) the same as `names()`; on Linux
(V4L2) those `VIDIOC_QUERYCTRL` answered for; `None` for the OpenCV path on
Windows, the simulated controls, and on Linux when the driver could not be asked.

#### `Controls.writes_defaults`

Read only. `bool`: `False` when `reset_to_defaults()` writes nothing and
`plan_reset()` is empty whatever the device reports — the OpenCV path on Linux
(V4L2), where camera control writes are not verified. The explorer's Reset to
defaults, `presets load defaults` (exit status 1) and the viewer's `p`/`P` on the
defaults then say that the defaults are shown, not written. The Rust crate has the
same `Controls::writes_defaults()`: `false` for its V4L2 controls on Linux, where
its `plan_reset()` is empty and `reset_to_defaults()` writes nothing (0.0.6 wrote
them); not run with the camera on Linux.

#### `Controls.get(name: str) -> Any`

The device's current value. `None` when the backend cannot read it.

#### `Controls.describe(name: str | None = None) -> Any`

Read only. A `ControlInfo` for `name`, or `{name: ControlInfo}` for every control
when `name` is `None`.

#### `Controls.snapshot() -> dict[str, Any]`

All current values that can be read, as `{name: value}`. Pass it to `restore()`.

#### `Controls.set(name: str, value: Any) -> WriteResult`

Queues one write and returns without waiting: the result's `status` is `"pending"`,
or `"skipped_same"` when the device already has that value. The final result comes
from `wait()`, `result()` or `flush()`.

#### `Controls.set_many(values: Mapping[str, Any]) -> list[WriteResult]`

Queues several writes, one result per name. A mode and its value (e.g.
`auto_exposure` and `exposure`) are merged into one write. If any value fails
validation, nothing is queued.

#### `Controls.flush(timeout: float | None = None) -> list[WriteResult]`

Waits until pending writes are done, then returns the results finished since the
last `flush()`.

#### `Controls.wait(tickets: Iterable[int], timeout: float | None = None) -> list[WriteResult]`

Waits for the given requests (by `WriteResult.ticket`) and returns their final
results. Those not done within `timeout` come back as `"pending"`.

#### `Controls.result(ticket: int) -> WriteResult | None`

The final result of one request, or `None` if it is not done yet.

#### `Controls.pending() -> dict[str, float]`

Pending request name → seconds left until it is written.

#### `Controls.restore(snap: Mapping[str, Any]) -> list[WriteResult]`

Writes back the values of a `snapshot()`, only those that differ. Values of groups
that are being returned to auto are left out. Does not wait.

#### `Controls.plan_reset(*, include_mono: bool = False) -> dict[str, tuple[Any, Any]]`

What `reset_to_defaults()` would change, as `{name: (current, default)}`. Writes
nothing. Auto/manual modes are not touched, values currently in auto are left out,
and controls whose default the device does not report are left out. Controls
expected to have no effect on a monochrome sensor are included only with
`include_mono=True`. Empty on Linux (V4L2), where `reset_to_defaults()` writes nothing.

#### `Controls.reset_to_defaults(*, include_mono: bool = False) -> list[WriteResult]`

Writes what `plan_reset()` lists. Does not wait.

#### `Controls.save_preset(path: str | Path, name: str | None = None, note: str | None = None) -> Path`

Reads every control and writes a preset file (JSON, see
[`presets`](#opticmix_visionpresets)): each readable value with its units, the
auto/manual modes, `model`, the backend, the SDK version and the time (UTC). Reads
only. `name` defaults to the file name without `.json`. Overwrites `path`. Returns
`path`.

#### `Controls.plan_preset(path: str | Path, *, force: bool = False, include_mono: bool = False) -> dict[str, tuple[Any, Any]]`

What `load_preset()` would change, as `{name: (current, preset)}`. Checks the whole
preset the same way and raises the same errors; writes nothing.

#### `Controls.load_preset(path: str | Path, *, force: bool = False, include_mono: bool = False, timeout: float | None = 10.0) -> PresetLoad`

Writes the preset's values that differ from the camera's, in one `set_many()` call
(a mode and its value, e.g. `auto_exposure` and `exposure`, are one write), then
waits up to `timeout` seconds for the results (`None`: no limit; `0`: does not
wait). Returns a [`presets.PresetLoad`](#opticmix_visionpresets)
with a `WriteResult` per control written (`status`, `readback`, `kept`), the
controls already at the preset's value, those left to auto and the mono-only ones
not written.

Controls expected to have no effect on the monochrome sensor (`hue`, `saturation`,
`white_balance`, `auto_white_balance`) are saved, but loading leaves them alone
unless `include_mono=True` — the same rule as `plan_reset()`. Every write is a risk
on this camera, so a write that cannot change the image is not made.

Before anything is written the whole preset is checked, and `presets.PresetError`
is raised with nothing written when: the file is not a preset; it was saved from
another `model`, or this camera's model is unknown (`presets.PresetModelError`;
`force=True` loads it anyway); it names a control this camera does not have; a
value's units differ from this camera's (saved on another platform or backend —
save it again here, or delete that control from the file; `force` does not override
this); or a value is the wrong type, out of the camera's range or not one of its
choices. A value of a group the preset puts in auto (e.g. `exposure` with
`auto_exposure` `"auto"`) is not written.

Run without a camera only; writing a preset to the camera writes several controls
at once — see README, "Camera controls": write once at startup.

#### `Controls.close() -> None`

Closes the control channel. Pending writes are not sent; they end as
`"cancelled"`. `Device.close()` calls this.

#### `Controls.closed`

Property. `True` after `close()`.

```python
from opticmix_vision.synthetic import SyntheticStereoSource

with SyntheticStereoSource() as src:
    c = src.controls
    print(c.describe("gain"))
    c.set_many({"auto_exposure": "manual", "exposure": -6})
    for done in c.flush():
        print(done.name, done.status, done.readback, done.kept)
```

### `ControlInfo(name: str, kind: Kind, current: Any, minimum: int | None, maximum: int | None, step: int | None, default: Any, choices: tuple[str, ...] | None, units: str, effect: Effect, ledger: str | None, group: str, note: str = "")`

One row of `describe()` (frozen dataclass). Values the device does not report are
`None`.

| Field | Meaning |
|---|---|
| `name` | control name |
| `kind` | `"int"`, `"bool"` or `"menu"` |
| `current` | current value, in the same form `set()` takes |
| `minimum`, `maximum`, `step` | range reported by the device |
| `default` | default reported by the device |
| `choices` | menu values, for `kind == "menu"` |
| `units` | `"raw"`, `"log2_s_nominal"` or `"100us"` |
| `effect` | verification state recorded for the control: `"verified"`, `"partial"`, `"unverified"` or `"none_expected_mono"` |
| `ledger` | verification record id, or `None` |
| `group` | write group: controls in one group share one device property and one write |
| `note` | free text |

### `WriteResult(name: str, requested: Any, status: Status, due_in_s: float | None = None, error: str | None = None, ticket: int = 0, readback: Any = None)`

Result of one write request (frozen dataclass).

| Field | Meaning |
|---|---|
| `name`, `requested` | what was asked |
| `status` | see below |
| `due_in_s` | for `"pending"`: seconds until the write |
| `error` | for `"failed"`: the error text |
| `ticket` | id for `wait()` and `result()` |
| `readback` | for `"written"`: the value read back right after the write (`None` if it could not be read) |

| `status` | Meaning |
|---|---|
| `"pending"` | queued, not written yet |
| `"written"` | the driver accepted the value |
| `"skipped_same"` | the device already had that value; nothing was written |
| `"superseded"` | replaced by a later request for the same control before it was written, or dropped because the same group was switched to auto |
| `"cancelled"` | not sent because the controls were closed |
| `"failed"` | the write failed (`error` says why) |

`"written"` means only that the driver accepted the value; check `kept`.

#### `WriteResult.kept`

Property. `True` when `readback == requested`, `False` when it differs, `None` when
the status is not `"written"` or there is no readback.

### `ControlSpec(name: str, key: str, role: Literal["value", "auto"], kind: Kind, effect: Effect, ledger: str | None = None, choices: tuple[str, ...] | None = None, mono: bool = False, note: str = "")`

One row of the control table (frozen dataclass). `key` is the backend key and write
group; `role` is `"value"` or `"auto"` (the auto/manual flag of that key); `mono`
marks controls expected to have no effect on a monochrome sensor.

### `CONTROLS`

`tuple[ControlSpec, ...]` — the control table, from the compiled extension. Empty
when the extension is not installed.

### `RawRange(minimum: int, maximum: int, step: int, default: int | None, can_auto: bool)`

`NamedTuple`. A device value range as a backend reports it. `default` is `None`
when the device reports none.

### `Backend`

`typing.Protocol` for a control backend: attributes `name`, `exposure_units`,
`note` and the methods below. `Controls` calls them only from its worker thread.
You need this only to write your own backend.

#### `Backend.start() -> None`

Called once on the worker thread before any other call.

#### `Backend.supports(key: str) -> bool`

Whether the backend has the control `key`.

#### `Backend.query(key: str) -> RawRange | None`

The range of `key`, or `None` when unknown.

#### `Backend.read(key: str) -> tuple[int, bool | None]`

The current device value and auto flag (`None` when unknown).

#### `Backend.write(key: str, value: int | None, auto: bool | None) -> None`

Writes a device value and/or auto flag (`None` leaves that part unchanged).

#### `Backend.close() -> None`

Releases the backend.

### `VENDOR_MIN_COMMAND_GAP_S`

`0.012` — seconds; the base spacing used by the guard constants below.

### `GROUP_MIN_INTERVAL_S`

`{"exposure": 0.012}` — minimum seconds between consecutive writes to the same
group.

### `GLOBAL_MIN_GAP_S`

`0.012` — minimum seconds between any two writes.

### `WARN_AFTER_TOTAL`

`3` — a `RuntimeWarning` is issued when this process's total write count reaches
this number, and every this many writes after.

The guard constants are module globals, read before every write request; a change
applies from the next request.

### `write_counts() -> dict[str, int]`

Actual writes made by this process, per group plus `"_total"`. Writes skipped as
identical are not counted.

### `reset_write_counts() -> None`

Resets the counts. Call it only after the USB cable was re-inserted and the device
is back in its initial state.

---

## `opticmix_vision.led`

The IR illuminator. It is the UVC backlight-compensation control
(`backlight_compensation` in `controls`), reached through
`Device.set_prop("CAP_PROP_BACKLIGHT", ...)`, so writes go through the same guard.
The usable range on the camera is in README, "IR illuminator".

### `DEFAULT_LED_PROP`

`"CAP_PROP_BACKLIGHT"` — the property `LedControl` uses when `prop` is not given.

### `LedControlUnavailable`

Subclass of `NotImplementedError`. Raised when `prop=None` was given (LED control
disabled), or when `set_level`/`get_level` is called without `value_range`.

### `UNAVAILABLE_REASON`

`str` — the message of `LedControlUnavailable` when `prop=None` was given.

### `RANGE_UNKNOWN_REASON`

`str` — the message of `LedControlUnavailable` when `value_range` is missing.

### `PropAccessor`

`typing.Protocol` (runtime-checkable) for what `LedControl` needs: `get_prop` and
`set_prop`. `Device` and `SyntheticStereoSource` satisfy it.

#### `PropAccessor.get_prop(prop: str | int) -> float`

Reads a property.

#### `PropAccessor.set_prop(prop: str | int, value: float) -> bool`

Writes a property.

### `LedControl(props: PropAccessor, *, prop: str | int | None | object = <unset>, value_range: tuple[float, float] | None = None)`

`props` is an open `Device` (or any `PropAccessor`). `prop` left out (`<unset>`)
means `DEFAULT_LED_PROP`; a string (OpenCV property name) or an integer id selects
another; `None` disables the control. `value_range=(min, max)` is needed only for
the normalized `set_level`/`get_level`.

Raises `ValueError` when `value_range` does not have `max > min`. Attributes:
`prop`, `value_range`.

#### `LedControl.available`

Property. `False` when `prop` is `None`.

#### `LedControl.get() -> float`

The current raw value.

#### `LedControl.set(value: float) -> float`

Writes a raw value and returns the value read back afterwards. The readback is what
the driver accepted, not evidence that the light changed; check the image.

#### `LedControl.set_level(level: float) -> float`

Writes a level in 0.0–1.0, mapped onto `value_range` and rounded to a whole number.
Returns the read-back level. Raises `ValueError` outside 0.0–1.0 and
`LedControlUnavailable` without `value_range`.

#### `LedControl.get_level() -> float`

The current value as a 0.0–1.0 level of `value_range`. Raises
`LedControlUnavailable` without `value_range`.

#### `LedControl.probe(candidates: Iterable[str | int]) -> dict[str, float | None]`

Reads each candidate property (writes nothing); a failed read is `None`. For
bring-up of a different camera.

```python
from opticmix_vision import LedControl
from opticmix_vision.synthetic import SyntheticStereoSource

with SyntheticStereoSource() as src:
    led = LedControl(src, value_range=(0, 96))
    print(led.set(48), led.get_level())
```

---

## `opticmix_vision.synthetic`

Generated stereo sources for running code without a camera. They are not measured
data. In `SyntheticStereoSource` the right image is the left image shifted by a
fixed disparity (R(x) = L(x + d), d > 0), with a moving bright dot so frames
visibly change; `SyntheticPlaneSource` renders a plane through a calibration.

### `SyntheticStereoSource(*, eye_size: tuple[int, int] = (320, 200), fps: float = 90.0, disparity: int = 8, seed: int = 0, realtime: bool = False)`

The same surface as `Device`: `read`, `get_prop`, `set_prop`, `controls`, `mode`,
`close`, and use as a context manager. `eye_size` is one eye's `(width, height)`;
`mode` reports a side-by-side frame of `2 × width`, fourcc `"SYNT"`, backend
`"synthetic"`. `realtime=True` paces `read()` to `fps`. Its controls use made-up
ranges and defaults with no write spacing; exposure, gain and
`backlight_compensation` change the image brightness. It also has `Device`'s frame
statistics `late_frames`, `estimated_missed_frames` and `actual_fps`.

#### `SyntheticStereoSource.read() -> StereoFrame`

The next generated frame.

#### `SyntheticStereoSource.controls`

Property. A `Controls` over the simulated backend.

#### `SyntheticStereoSource.get_prop(prop: str | int) -> float`

Reads a simulated property by OpenCV name or id.

#### `SyntheticStereoSource.set_prop(prop: str | int, value: float) -> bool`

Writes a simulated property and waits for it. Returns `False` if the write failed.

#### `SyntheticStereoSource.close() -> None`

Closes the controls.

```python
from opticmix_vision.synthetic import SyntheticStereoSource

with SyntheticStereoSource(eye_size=(640, 400)) as src:
    frame = src.read()
    print(frame.size, src.mode.to_dict())
```

### `SyntheticPlaneSource(calibration: StereoCalibration, *, seed: int = 0)`

Generated frames of a textured plane 350–600 mm in front of the left eye, moved at
random between frames, as both eyes of `calibration` (a `StereoCalibration`) see
it — so a calibration check on its frames knows which file fits. `calibration` is
kept as an attribute, as on `Device`. No controls, no `mode`. Not measured data.

#### `SyntheticPlaneSource.read() -> StereoFrame`

The next generated frame (eye size = the calibration's).

---

## `opticmix_vision.recording`

Recording frames to a file, playing a recording back, and the capture-info JSON
saved next to captures. Recording with `Recorder` and reading a recording back, seeking and looping: verified with the published 0.0.7 wheel on one Weevia S1 unit, Windows 11, one PC, two-level USB hub, two USB re-insertions, read-only (the files were written and read back with the same frame count — not that both eyes showed the scene in every recording: see "One eye goes black or stops moving"). The speed factor (`set_speed`): not verified; pausing: generated frames only; not run with the camera.
The Rust crate (`opticmix-vision`: `Recorder`, `Device::open_recording`) writes and plays the same MJPG `.avi` files, CSV and JSON; it neither writes nor plays FFV1, and its recordings name a checked V4L2 clock `v4l2_monotonic` in the CSV. Rust recording and playback: generated frames only; not run with the camera.

A recording is three files with the same name. The `.csv` and `.json` are named
after the video without its extension, so two recordings in one folder need
different names, not only different extensions (`Recorder` refuses rather than
overwrite another recording's files):

| File | Contents |
|---|---|
| `<name>.avi` / `.mkv` | both eyes side by side (left eye in the left half), 8-bit gray |
| `<name>.csv` | `frame, host_ns, driver_ns, driver_clock`: when each frame arrived (host monotonic clock, not a camera timestamp), and the frame's `StereoFrame.driver_ns` and `driver_clock` — the capture driver's time stamp and its clock — with both cells empty where the frame had none (DirectShow, the Windows default; the synthetic source). A CSV of only `frame, host_ns` (written by earlier versions) plays back as before, with no driver times. On Windows fill it with `--backend msmf` (`examples/record_video.py`, `examples/save_stereo_pairs.py`; in code `DeviceConfig.weevia_s1(backend="msmf")`); the controls then go through OpenCV's fallback (no ranges or defaults). Through OpenCV's Media Foundation path the driver times came evenly 11.11 ms apart on one PC with one camera (once, 2026-11-24) while images repeated; these times do not show whether a new image arrived. Run without a camera only; not run with the camera. |
| `<name>.json` | `capture_info` at the start, plus `recording`: `codec`, `compressed`, `layout`/`lr_order` of the file, `fps_label`, `eye_size`, `frames`, `first_host_ns`/`last_host_ns`, `duration_s`, `breaks` (frame numbers after each `Recorder.pause`), `control_changes` (`{"frame", "name", "value", "status"}` for each change made while recording), `controls_at_end` and `controls_at_end_source` (`"camera"`, `"simulated"`, `"given"` or `null`, as `controls_source`), `late_frames`/`estimated_missed_frames` over the recording, `write_queue` (the `queue_frames` it recorded with; 0: none), `dropped_frames` (`Recorder.dropped_frames`; `estimated_missed_frames` includes `dropped_frames` — do not add them), `closed` |

The codec `"MJPG"` (default) is compressed: pixel values change. `"FFV1"`
(`LOSSLESS_CODEC`): the lossless file keeps the pixel values the SDK delivered
(generated frames only; not run with the camera). Its files are larger, and each
frame takes longer to write than MJPG, so it may not keep up with 90 fps: the JSON's
`estimated_missed_frames` and `Recorder.estimated_missed_frames` say how many frames
were missed; then record MJPG, ask a lower frame rate, or keep frames in memory
(`examples/capture_dataset_npz.py`). FFV1 needs an OpenCV build with the FFV1
encoder (checked: the opencv-python 4.12, 4.13, 4.14 and 5.1 wheels on Windows;
other builds not checked). With the 4.12 and 4.13 wheels, writing grayscale frames
to FFV1 failed now and then (`cv2.error` on a frame, with cv2 alone too, without this
package; one PC, generated frames); with 4.14 and 5.1 it did not in the same test.
Whether the failure is in OpenCV or in the FFmpeg its wheel bundles has not been
established; other OpenCV versions and Linux have not been run. With OpenCV before 4.14 `write()`
then raises `RuntimeError` saying to upgrade OpenCV (`pip install -U opencv-python`)
or record MJPG; the recording is closed with the frames written before it (read back after a failure with the 4.13 wheel and cv2 alone; with 4.12 not checked).

A recording that was not closed (the process stopped while recording): an MJPG
`.avi` cannot be opened (`DeviceError` says so); an FFV1 `.mkv` plays up to where it
was written. The CSV is written line by line; when its frame count and the video's
differ, `Playback` plays the smaller count and warns (`RuntimeWarning`).

### `LOSSLESS_CODEC`

`str` — `"FFV1"`.

### `capture_info(source: Any, *, controls: dict[str, Any] | None = None, controls_read_at: str | None = None, **extra: Any) -> dict[str, Any]`

What was capturing, as a JSON-ready `dict`: `opticmix_vision` (SDK version),
`created` (local wall-clock time, ISO 8601), `host_clock`, `mode` (the negotiated
mode), `source` (`"camera"`, `"synthetic"` or `"recording"`), `camera` (`device`,
`device_name` as DirectShow (Windows, `backend="dshow"`) or sysfs (Linux) lists it,
`None` when unknown, `backend`, `layout`, `lr_order` from the `DeviceConfig`; camera
only), `calibration_files` (the installed calibration files with their sha256;
camera only — the SDK cannot check that a file belongs to this camera), `controls`
(`controls.snapshot()`: every control value; simulated values for the synthetic
source, `None` with the reason in `controls_note` when controls cannot be read) and,
for a recording played back, `recorded_from` (its own info). `extra` is added as is.
`controls`, when given, is written as `controls` instead of reading the camera, and
`controls_note` says the values were given, not read then (the viewer gives the
values it knows: read when it opened, then its own writes). Two fields say where
the values came from, for a program to read: `controls_source` — `"camera"` (read
here from `source.controls`), `"simulated"` (the synthetic source), `"given"`
(`controls`), or `None` (no controls; `controls_note` says why) — and
`controls_read_at`, when they were read (local wall-clock time, ISO 8601 to the
millisecond): the time of the read here, or for given values `controls_read_at` as
passed (`None` when not passed). The viewer passes the time it read every control,
when it opened; a control whose write was refused is read once again later, and
the values include the viewer's own writes since. Reads controls unless given,
never writes them.

Rust (from 0.0.8): `opticmix_vision::capture_info(&mut device)` — the same field names
for a camera, the Rust examples' `--synthetic` stand-in (`source` `"synthetic"`, with
its `camera` fields) or a recording; `created` in UTC, `calibration_files` `null` (not listed), no
`controls=`. A test of the crate compares its field names with this function's. Run
without a camera only (generated frames); not run with the camera.

### `write_capture_info(path: str | Path, source: Any, **extra: Any) -> dict[str, Any]`

`capture_info(source, **extra)` written to `path` as JSON (`controls=` too). Returns the `dict`. The
viewer's `s` key and the examples `save_stereo_pairs.py` and
`capture_dataset_npz.py` write it next to what they save. Rust (from 0.0.8):
`write_capture_info(path, &mut device, extra)`, which the Rust `save_stereo_pairs` and
`capture_dataset` examples write as `capture_info.json`.

### `Recorder(path: str | Path, source: Any, *, codec: str = "MJPG", fps: float | None = None, controls: Callable[[], dict[str, Any] | None] | None = None, controls_read_at: str | None = None, queue_frames: int = 0)`

Writes frames to `path` with the `.csv` and `.json` next to it. `source` is the
`Device` (or synthetic source) the frames come from; its capture info is taken when
the recorder is created. The three files are opened when the recorder is created,
at the eye size the source's negotiated mode gives (`sbs`, or `tb` when the
`DeviceConfig` says so), so opening them does not hold up the first frames. When
that size is unknown (a source without a `mode`, `plane_pack`) or the first frame
has another size, they are opened on the first `write()`; frames that arrive while
they open are missed and counted like any other. Raises `OSError` (with what to
check) when the video file cannot be opened, and `FileExistsError` when a file with
the same name and another extension (`.avi`, `.mkv`, `.mp4`, `.mov`, or the `.npz` of
`examples/capture_dataset_npz.py`) is in the folder: the `.csv` and `.json` next to it
would be overwritten; the message says to record to another name or move that file.
Recording again to the same `path` replaces that recording: it is moved to
`<file>.replaced` while recording, deleted when the new recording closes with a frame,
and put back when it closes with none (or the files cannot be opened or moved). If the
process stops while recording, `<name>.avi`/`.csv`/`.json` are the unfinished recording
and the `.replaced` files the earlier one, and recording to that path raises
`FileExistsError`: to keep the earlier recording, delete or move the three unfinished
files first, then take `.replaced` off the names; to keep the unfinished one, delete the
`.replaced` files. `codec` is
four characters: `"MJPG"` (with `.avi`) or `LOSSLESS_CODEC` (with `.mkv` or `.avi`).
`fps` is the frame rate written into the file, a label only (default: the source's
negotiated rate, or 30). `controls` is a function giving the control values for the
JSON, called when the recorder is created and when it closes (`controls_at_end`),
instead of reading the camera then (`controls.snapshot()`); when it returns `None`
the camera is read. The viewer passes the values it knows. `controls_read_at`: when
those values were read from the camera (ISO 8601), written as the JSON's
`controls_read_at` (see `capture_info`).

`queue_frames`: `0` (default) — `write()` encodes the frame before it returns.
`N > 0` — `write()` hands the frame to a writing thread through a queue of `N`
frames and returns at once, so encoding does not hold up the caller (the viewer's
`v` draws the window while a thread encodes). A frame that finds the queue full is
not recorded: `dropped_frames` and the JSON's `dropped_frames` count it, and the
frame numbers (the CSV's `frame`, `breaks`, `control_changes`) stay those of the
file. The frames are written in the order `write()` took them. An error on the
writing thread (for example a full disk) is raised by the next `write()` — and by
every later one; nothing more is recorded — or, if no `write()` follows, by
`close()`. `close()` first writes the frames still queued, so it waits for them. The
queued frames' arrays are kept until written: do not change them after `write()`.
The queue holds whole frames in memory (64 frames of 1600x800 gray are about 82 MB).
A queue does not make an encoder that is slower than the camera keep up — it does not
make FFV1 keep up with 90 fps: the frames the writing thread cannot take are dropped
and counted in `dropped_frames`. `write()` after `close()` raises `ValueError`.
Writing on a thread: run on generated frames only; not run with the camera.

A context manager: `close()` on exit. Attributes: `path`,
`csv_path`, `info_path`, `codec`, `compressed`, `fps`, `frames` (written so far;
with a queue, not counting the frames still queued), `paused`, `info`.
`late_frames` / `estimated_missed_frames` count over the frames written so far, from
their `host_ns` intervals at the source's negotiated rate (the rule of
`Device.late_frames`); a pause is not counted as a gap. A dropped frame leaves a gap
in the recorded times, so these estimates count it as well: `estimated_missed_frames`
includes `dropped_frames` — do not add them.

```python
from opticmix_vision.recording import LOSSLESS_CODEC, Recorder
from opticmix_vision.synthetic import SyntheticStereoSource

with SyntheticStereoSource() as src, Recorder("take1.mkv", src, codec=LOSSLESS_CODEC) as rec:
    for _ in range(90):
        rec.write(src.read())
print(rec.frames, rec.info["recording"]["compressed"])   # 90 False
```

#### `Recorder.write(frame: Any) -> None`

Appends one `StereoFrame` and its CSV row: `frame`, `host_ns`, `driver_ns`, `driver_clock` (the last two
empty when `driver_ns` is `None`). Does nothing while paused. Raises
`OSError` (with what to check) when the video file is opened here (see above) and
cannot be. With `queue_frames`, queues the frame for the writing thread, or drops and
counts it when the queue is full, and raises the error the writing thread met.
Raises `ValueError` after `close()`.

#### `Recorder.note_control(name: str, value: Any, status: str | None = None) -> None`

Records a control change made while recording in the JSON's `control_changes`:
`{"frame", "name", "value"}` (and `"status"` when given), where `frame` is the
number of the next frame written (after the frames still queued, with
`queue_frames`). The viewer calls it for each LED, exposure, gain
and auto-exposure change it writes while recording, and for a preset it loads
(name `"preset"`, the file name as the value, then each control it queues). `status` is the write's status
when it was requested (for example `"pending"`), not a confirmation that the camera
applied it; the values the camera reported when recording stopped are in `controls_at_end`.

#### `Recorder.pause() -> None`

Stops writing until `resume()`; frames in between are not recorded. The JSON lists
the frame number after each pause in `breaks`; playback and the late / missed
estimates do not count the pause as a gap.

#### `Recorder.resume() -> None`

Writes again.

#### `Recorder.close() -> None`

Writes the frames still queued (with `queue_frames`), then closes the files and
writes the final frame count, first and last `host_ns`, duration, late / missed
estimates, `dropped_frames`, `controls_at_end` (after waiting up to 1 s for queued
control writes) and `closed: true` into the JSON, and deletes the recording it
replaced. With no frame written, removes its three files instead (an empty video
cannot be played) and puts back the recording it replaced. Then raises the writing
thread's error if no `write()` raised it. Safe to call more than once; `write()` after
it raises `ValueError`. `controls_at_end_source` says where `controls_at_end` came from.

#### `Recorder.dropped_frames`

Property. Frames `write()` took (not paused) that are not in the file: the write
queue was full, or writing had failed. Always `0` without a queue (`write()` then
raises instead).

#### `Recorder.missed_advice`

Property. `""` when no frame was missed; otherwise one ASCII line on what to do:
record MJPG (only for a lossless recording), ask a lower frame rate (`--fps`), or keep
frames in memory (`examples/capture_dataset_npz.py`). `examples/record_video.py` and
the viewer print it; the viewer puts `First try examples/record_video.py (it records
with no preview window).` before it.

### `Playback(path: str | Path)`

A recording read back frame by frame. `Device(DeviceConfig.recording(path))` uses
it as its capture and returns it as `Device.playback`. It reads the `.csv` for frame
times (without it: frame number / the file's frame rate) and the driver times recorded with them
(`recorded_driver`), and the `.json` as `info`
(`None` without it). Raises `OSError` when OpenCV cannot open the file.

| Attribute | Meaning |
|---|---|
| `real_time` | `False` (default): `read()` returns the next frame as soon as it is decoded. `True`: `read()` waits so frames come at the recorded intervals (the CSV's `host_ns`) divided by `speed`; a pause in recording (`breaks`) is not waited out. Frames are not dropped when the caller is slower |
| `speed` | playback speed for `real_time`, set with `set_speed` (default 1.0) |
| `loop` | `True`: after the last frame, playback starts again at frame 0 instead of ending |
| `recorded_time` | `True`: a frame's `host_ns` is the recorded time from the CSV instead of the time it was read back, and its `driver_ns` / `driver_clock` are the ones recorded with it (`None` where the CSV cells are empty, the CSV has no such columns, or there is no CSV). `False` (default): `driver_ns` and `driver_clock` are `None` — a recorded driver time is not put beside a read-back `host_ns`, so `host_ns - driver_ns` never mixes the two |
| `paused` | while paused, `read()` returns the current frame again; `seek()` still moves |
| `info` | the recording's JSON as a `dict`, or `None` |
| `path` | the video file |
| `breaks` | frame numbers that follow a pause in recording (from the JSON) |
| `late_frames`, `estimated_missed_frames` | over the whole recording, from the CSV's intervals at the recorded rate, pauses excluded — known when the file is opened. `Device.late_frames` counts only the frames read |

It also has the `VideoCapture` methods `Device` calls (`read`, `get`, `set`,
`isOpened`, `release`, `getBackendName`).

#### `Playback.status`

Property. `"playing"`, `"paused"` or `"ended"` (after the last frame, without
`loop`; a `seek` leaves it). There is no status callback: read `status`, and
`read()` ends with `EndOfRecording`.

#### `Playback.frame_count`

Property. Frames in the recording.

#### `Playback.duration_s`

Property. Recorded time from the first frame to the last, in seconds.

#### `Playback.position`

Property. The file frame number of the current frame: the one `read()` returned
last, or the one a `seek()` moved to (it is returned next).

#### `Playback.position_s`

Property. Recorded time of `position` from the first frame, in seconds.

#### `Playback.time_s(index: int) -> float`

Recorded time of frame `index` from the first frame, in seconds.

#### `Playback.recorded_ns(index: int) -> int`

The `host_ns` recorded for frame `index` (clamped to the recording).

#### `Playback.recorded_driver(index: int) -> tuple[int | None, str | None]`

`(driver_ns, driver_clock)` recorded for frame `index` (clamped to the recording), whatever
`recorded_time` is; `(None, None)` where the CSV cells are empty, the CSV has no driver columns (earlier versions), or
there is no CSV. Run without a camera only; not run with the camera.

#### `Playback.seek(frame: int | None = None, *, seconds: float | None = None) -> int`

Moves to a frame number, or to the first frame recorded at or after `seconds` from
the first frame; give one of the two (`ValueError` otherwise). Clamped to the
recording. The next `read()` returns that frame. Returns the frame number.

#### `Playback.set_speed(speed: float) -> None`

Playback speed for `real_time`: `2.0` is twice as fast as recorded, `0.5` half.
`ValueError` unless positive.

#### `Playback.pause() -> None`

Holds the current frame: `read()` returns it again until `resume()`.

#### `Playback.resume() -> None`

Plays on from the current frame.

**Frames out of a recording.** `examples/export_recording.py` reads a recording through
`Device(DeviceConfig.recording(path))` with `recorded_time = True` and writes, into a new or empty folder, each
frame as a left/right PNG pair (`left_000123.png`, 123 = the frame's number in the recording), `pairs.csv`
(`index, host_ns, driver_ns, driver_clock, left, right`, as `examples/save_stereo_pairs.py` writes it, with the
times recorded in the recording's CSV), a copy of the recording's JSON as `capture_info.json`, and with `--npz`
`frames.npz` (`left`, `right`, `host_ns`, as `examples/capture_dataset_npz.py` writes it). From an MJPG recording
the PNGs hold what it decodes to, not the camera's pixel values. A recording without its `.csv` has no recorded
times: `host_ns` in `pairs.csv` is then frame number / the file's frame rate and the driver columns are empty, and
the example prints a line saying so. Run with generated recordings, and with a camera recording once on Linux, from source before release; not run with a camera recording on Windows.

---

## `opticmix_vision.calibration`

The calibration file: per-eye Kannala-Brandt 4th-order (KB4, as `cv2.fisheye`)
intrinsics and the left-to-right extrinsics, in JSON.

```
r_d = θ (1 + k1 θ² + k2 θ⁴ + k3 θ⁶ + k4 θ⁸),  θ = angle from the optical axis
pixel = K · [r_d cos φ, r_d sin φ, 1]ᵀ
```

File schema (`schema_version` 1):

```json
{
  "schema": "opticmix_vision.stereo_calibration",
  "schema_version": 1,
  "units": "mm",
  "device": {"model": "...", "serial": "..."},
  "left":  {"model": "KB4", "K": [[fx, 0, cx], [0, fy, cy], [0, 0, 1]], "D": [k1, k2, k3, k4], "size": [w, h]},
  "right": {"model": "KB4", "K": [[...]], "D": [...], "size": [w, h]},
  "extrinsics": {"R": [[...3x3...]], "t": [tx, ty, tz], "convention": "p_right = R @ p_left + t"},
  "meta": {}
}
```

Extrinsics follow OpenCV `stereoCalibrate`: `R`, `t` take left-camera coordinates
to right-camera coordinates (`p_right = R @ p_left + t`). Camera axes: X right,
Y down, Z forward along the optical axis; millimetres. The baseline is `|t|`.

Checks: an unknown key is an error; NaN and infinity are neither written nor read;
KB4 coefficients that fold inside the field of view (not monotonic) are refused.
`device` holds strings only; `meta` is free form and not checked.

### `SCHEMA_NAME`

`"opticmix_vision.stereo_calibration"` — the `schema` value of the file.

### `SCHEMA_VERSION`

`1` — the `schema_version` value of the file.

### `DEFAULT_MAX_THETA_DEG`

`92.5` — the largest angle (degrees) over which `CameraIntrinsics.validate` checks
that KB4 does not fold.

### `CalibrationError`

Subclass of `ValueError`. The file or the values break the schema or the geometry
checks. The message says which.

### `CameraIntrinsics(K: np.ndarray, D: np.ndarray, size: tuple[int, int], model: str = "KB4")`

One eye's KB4 intrinsics (frozen dataclass). `K` is 3×3 in pixels, `D` is
`(k1, k2, k3, k4)`, `size` is `(width, height)`. Values are converted to `float64`
arrays and checked on construction; raises `CalibrationError`.

Properties `fx`, `fy`, `cx`, `cy` read `K`.

The package has no projection function; OpenCV computes the same model with `K` and `D`:
`cv2.fisheye.projectPoints(p.reshape(-1, 1, 3), np.zeros(3), np.zeros(3), intr.K, intr.D)` gives the pixels of
points `p` (N×3 in this eye's coordinates: X right, Y down, Z forward, mm), and
`cv2.fisheye.undistortPoints(np.ascontiguousarray(uv, dtype=np.float64).reshape(-1, 1, 2), intr.K, intr.D)` gives
x/z, y/z of the rays through pixels `uv`. OpenCV divides by Z, so for a point behind the eye (Z < 0) it returns the
pixel of -p. Neither checks the pixel against `size`. The Rust crate's `Intrinsics::project` and `unproject` compute
the same model up to 92.5° from the optical axis (`None` beyond) and give a point behind the eye its own pixel.
Tutorial 08 has an example.

#### `CameraIntrinsics.fx`

Property. `K[0, 0]`.

#### `CameraIntrinsics.fy`

Property. `K[1, 1]`.

#### `CameraIntrinsics.cx`

Property. `K[0, 2]`.

#### `CameraIntrinsics.cy`

Property. `K[1, 2]`.

#### `CameraIntrinsics.validate(max_theta_deg: float = 92.5) -> list[str]`

The problems found, as messages; empty when valid.

#### `CameraIntrinsics.to_dict() -> dict[str, Any]`

The JSON form (`model`, `K`, `D`, `size`).

#### `CameraIntrinsics.from_dict(d: Mapping[str, Any], where: str = "intrinsics") -> CameraIntrinsics`

Class method. Checks and reads the JSON form; `where` names the block in error
messages. Raises `CalibrationError`.

### `StereoExtrinsics(R: np.ndarray, t: np.ndarray)`

Left → right transform (frozen dataclass): `p_right = R @ p_left + t`, `t` in mm.
Raises `CalibrationError` when `R` is not a 3×3 rotation matrix, `t` is not 3 values,
a value is NaN or infinite, or `t` is zero.

#### `StereoExtrinsics.baseline_mm`

Property. `|t|` in mm.

#### `StereoExtrinsics.to_dict() -> dict[str, Any]`

The JSON form (`R`, `t`, `convention`).

#### `StereoExtrinsics.from_dict(d: Mapping[str, Any]) -> StereoExtrinsics`

Class method. Checks and reads the JSON form. Raises `CalibrationError`.

### `StereoCalibration(left: CameraIntrinsics, right: CameraIntrinsics, extrinsics: StereoExtrinsics, device: dict[str, str] = <factory>, meta: dict[str, Any] = <factory>, units: str = "mm")`

A whole calibration file (frozen dataclass). `units` must be `"mm"`; `device` takes
string → string entries only. Raises `CalibrationError`.

#### `StereoCalibration.baseline_mm`

Property. `extrinsics.baseline_mm`.

#### `StereoCalibration.to_dict() -> dict[str, Any]`

The full JSON form, including `schema` and `schema_version`.

#### `StereoCalibration.from_dict(d: Mapping[str, Any]) -> StereoCalibration`

Class method. Checks and reads the JSON form. Raises `CalibrationError`.

#### `StereoCalibration.to_json() -> str`

The file text (indented JSON, trailing newline).

#### `StereoCalibration.from_json(text: str) -> StereoCalibration`

Class method. Checks and reads file text, including NaN/Infinity tokens. Raises
`CalibrationError`.

### `load_calibration(path: str | Path) -> StereoCalibration`

Reads a calibration file (UTF-8). Raises `CalibrationError` for an invalid file and
`OSError` when it cannot be read.

### `save_calibration(calib: StereoCalibration, path: str | Path) -> Path`

Writes a calibration file, creating the parent folder. Returns the path.

### `kb4_is_injective(D: np.ndarray, max_theta_rad: float, n: int = 1024) -> tuple[bool, float | None]`

Whether dθ_d/dθ = 1 + 3k1θ² + 5k2θ⁴ + 7k3θ⁶ + 9k4θ⁸ is positive on
[0, `max_theta_rad`], sampled at `n` points. Returns `(True, None)` when monotonic,
`(False, first folding angle in radians)` otherwise. Raises `ValueError` unless `D`
has 4 coefficients.

### `synthetic_equidistant(size: tuple[int, int], fov_deg: float) -> CameraIntrinsics`

An ideal equidistant fisheye (`D` = 0) for tests and first guesses — not a measured
calibration. The image circle is inscribed in the short side:
f = (min(w, h) / 2) / (fov / 2 in radians), principal point at the image centre.

```python
import tempfile, pathlib
from opticmix_vision.calibration import (StereoCalibration, StereoExtrinsics,
                                         load_calibration, save_calibration,
                                         synthetic_equidistant)

eye = synthetic_equidistant((800, 800), 180)
cal = StereoCalibration(left=eye, right=eye,
                        extrinsics=StereoExtrinsics(R=[[1, 0, 0], [0, 1, 0], [0, 0, 1]], t=[-50, 0, 0]),
                        device={"serial": "S1-0001"})
path = save_calibration(cal, pathlib.Path(tempfile.mkdtemp()) / "S1-0001.json")
print(load_calibration(path).baseline_mm)   # 50.0
```

---

## `opticmix_vision.calib`

Installing the calibration file for your camera, so `Device.calibration` finds it.
The camera reports no USB serial number, so the SDK cannot tell which unit is
connected: you install the file for your camera, and with exactly one file
installed, that file is used. `show` prints what a file holds; `check` measures
whether a file fits the camera.

Files are stored as `<serial>.json` in `store_dir()`.

Rust: the `opticmix-vision` crate's `calib` module reads the installed files by the
same rules — `calib::store_dir()`, `installed()`, `resolve()` (exactly one file, the
same messages), `installed_file()` (its path) and `load(path)` — and has the
`calibration_info` example. It reads files only; install them with `add` here.
It projects a point to a pixel and a pixel to a unit ray (`Intrinsics::project`,
`unproject`, KB4, up to 92.5° from the optical axis); it has no rectification or
depth. Run without a camera only (made-up calibration files); not run with the camera.

### `CalibLookupError`

Subclass of `LookupError`. No usable calibration; the message says what to do next.

### `parse_label(text: str) -> tuple[str, str]`

Normalizes a camera label `S1-NNNN-XXXX` (case-insensitive, surrounding spaces
ignored) and returns `(serial, label)`, e.g. `"s1-0042-k7q2"` →
`("S1-0042", "S1-0042-K7Q2")`. Raises `CalibLookupError` when the text is not a
label.

### `store_dir() -> Path`

The folder for installed files: `%APPDATA%\opticmix\calib` on Windows;
`$XDG_CONFIG_HOME/opticmix/calib`, or `~/.config/opticmix/calib`, elsewhere.

### `add(path: str) -> tuple[str, str]`

Checks a calibration file (schema, and a `device.serial` of the form `S1-NNNN`) and
copies it to `store_dir()/<serial>.json`. Returns `(serial, status)` with status
`"added"`, `"replaced"` or `"unchanged"`. Raises `CalibLookupError` when the file
does not exist or has no `S1-NNNN` serial (a label instead of a file is refused:
installing by label is not available), and `CalibrationError` for an invalid file.

### `installed() -> list[str]`

Serials of the installed files, sorted.

### `resolve() -> StereoCalibration`

The installed calibration when there is exactly one. Raises `CalibLookupError`
when none is installed, or when more than one is (the message lists them, the
folder, and `calib check --all`, which compares them on the connected camera).

### `show(target: str | None = None) -> dict[str, Any]`

What a calibration file holds, as plain values: `calib show` prints them and
`calib show --json` prints this dict. `target` is `None` for the installed file
(exactly one installed), an installed file's serial `S1-NNNN` (or the camera's label
`S1-NNNN-XXXX`), or the path of a file, installed or not. Reads the file only: the
camera is not opened, and as the camera reports no USB serial number the file is not
matched to it. Keys:

- `source`: `"installed"` or `"file"` (a file that is not installed); `path`;
  `sha256` of the file's bytes — the value a recording's `.json` lists under
  `calibration_files`.
- `matched_to_camera`: always `False`; `note`: `"installed file, not matched to this
  camera (no USB serial)"` (or `"file not installed, …"`).
- `used_by_device_calibration` and `use`: whether `Device.calibration` returns this
  file, and why — `"yes: the only installed file"`, `"no: 2 files are installed, and
  it needs exactly one"`, or `"no: not installed (… calib add <path> installs it)"`.
- `schema`, `schema_version`, `units`, `device` (model and serial, as in the file).
- `left`, `right`: `model`, `size` (`[width, height]`), `mode` (`[2 × width, height]`,
  the side-by-side mode the file is for), `fx`, `fy`, `cx`, `cy`, `K` (3×3) and `D`
  (k1..k4).
- `extrinsics`: `convention` (`"p_right = R @ p_left + t"`), `R` (3×3), `t` (mm),
  `baseline_mm`, `rvec_rad` (the rotation as a Rodrigues vector, `cv2.Rodrigues(R)`),
  `rvec_deg`, `rotation_deg` (its length, the rotation angle, in degrees) and
  `rotation_axis` (unit vector; `None` when there is no rotation).
- `meta`: the file's free-form block as is.

Raises `CalibLookupError` saying what to do when no file is installed, when more than
one is and `target` is `None` (it names a serial to pass), when `target` is a serial
that is not installed, and when it is neither an existing file nor a serial;
`CalibrationError` for an invalid file. Run without a camera only (made-up
calibration files); not run with the camera.

### `check(device: Any, calibration: StereoCalibration | None = None, *, frames: int = 10, every: int = 10) -> CheckResult`

Measures whether `calibration` fits the camera `device` reads from. `device` is an
open `Device` (or anything with `read()` returning a `StereoFrame`); `calibration`
defaults to `device.calibration`. `read_pairs(device, frames=frames, every=every)`
first, then `check_pairs`: all frames are read before any matching starts. Point
the camera at a textured scene in view of both eyes. Raises `ValueError` when
`frames` or `every` is below 1 or the eye size differs from the calibration's.

It returns numbers only — **there is no pass/fail threshold**. Two values have been
measured, once each on Windows: on one camera a matching file gave a median of
0.44 px; another camera's file gave 30.4 px. They tell a file that fits from one
that does not; they are not a precision figure. `check` itself has been run
without a camera only (generated frames); not run with the camera.

### `check_pairs(pairs: Iterable[tuple[np.ndarray, np.ndarray]], calibration: StereoCalibration) -> CheckResult`

The calculation of `check` on `(left, right)` images you already have (grayscale,
or BGR converted to grayscale), such as pairs saved by the viewer. For each pair:
SIFT features matched between the eyes (ratio test 0.7; a pair with fewer than 20
matches is skipped), undistorted with each eye's KB4 model, kept where |x| and |y|
are below 1.2 in normalized coordinates in both eyes; then the distance of each
right-eye point from the epipolar line of its left-eye point (essential matrix from
`R`, `t`), multiplied by the left eye's fx to give pixels. Raises `ValueError` when
an image size differs from the calibration's eye size (the message names the
mode to open, e.g. `Open the 1600x800 mode.`).

### `compare_pairs(pairs: Iterable[tuple[np.ndarray, np.ndarray]], calibrations: Mapping[str, StereoCalibration]) -> dict[str, CheckResult]`

`check_pairs` for several files on the same pairs; the features are matched once.
`calibrations` maps your names (serials, say) to files; the result keeps that
order. The file with the smallest median is not necessarily the camera's: when
every median is large, none of the files may belong to it. Raises `ValueError`
naming the file whose eye size differs from the images.

### `read_pairs(device: Any, *, frames: int = 10, every: int = 10) -> list[tuple[np.ndarray, np.ndarray]]`

Reads from `device` (anything with `read()` returning a `StereoFrame`): drops 30
frames, then reads `frames * every` and keeps a copy of one `(left, right)` pair
every `every` frames. Raises `ValueError` when `frames` or `every` is below 1.

### `load_pairs(folder: str | Path) -> list[tuple[np.ndarray, np.ndarray]]`

Grayscale `(left, right)` pairs from PNG files in `folder`, sorted by name: the
viewer's `<name>_left.png` + `<name>_right.png` and `examples/save_stereo_pairs.py`'s
`left_<n>.png` + `right_<n>.png`. Non-ASCII paths work. Raises `ValueError` when
there is no pair or a file is not an image.

### `CheckResult(median_px: float, p95_px: float, frac_le_1px: float, frac_le_2px: float, pairs: int, points: int, disparity_positive: float)`

The result of `check` (frozen dataclass). `median_px`, `p95_px`: median and 95th
percentile of the epipolar distance in pixels. `frac_le_1px`, `frac_le_2px`: the
fraction of points within 1 px and 2 px. `pairs`: pairs with at least 20 matches;
`points`: points used. `disparity_positive`: the fraction of points that lie further
right in the left eye than in the right (normalized coordinates, averaged over
pairs) — near 1 when the file's left and right are the camera's left and right,
near 0 when they are swapped. The numbers are `nan` when no point was used.

#### `CheckResult.enough`

Property. `True` when there are at least `MIN_PAIRS` pairs and `MIN_POINTS` points;
with fewer, the numbers do not tell anything.

#### `CheckResult.summary() -> str`

The text `calib check` prints: the numbers; a note when fewer than half the points
have positive disparity (the file's left and right look swapped against the
camera's); the two measured values above; and, as the next step, to compare the
file's serial with the camera's label when the median is far above 0.44 px. With
too few points it prints only the counts and asks for a scene with more texture;
with enough points but fewer than `MIN_PAIRS` pairs, it asks for 5 pairs or more.

### `MIN_PAIRS`

`int`, 5 — the fewest pairs with matches for `CheckResult.enough`.

### `MIN_POINTS`

`int`, 200 — the fewest points for `CheckResult.enough`.

### `main(argv: list[str] | None = None) -> int`

The command line below. Returns the exit code.

### Command line: `python -m opticmix_vision.calib`

```
usage: python -m opticmix_vision.calib [-h] {add,list,show,check} ...

positional arguments:
  {add,list,show,check}
    add                 install the calibration file for your camera
    list                show installed calibrations and the folder
    show                print what a calibration file holds (reads the file only)
    check               measure whether a calibration fits the connected camera (no pass/fail)
```

| Command | Does |
|---|---|
| `add FILE` | `add(FILE)`; prints the serial, the status and the installed path |
| `list` | prints `store_dir()` and the installed serials |
| `show [SERIAL\|FILE] [--json] [--synthetic]` | `show(...)` as text: the file's path, `installed file, not matched to this camera (no USB serial)`, its sha256 and whether `Device.calibration` uses it; for each eye the model, size (and the mode it is for), fx fy cx cy and D (k1..k4); then the baseline, t, all nine values of R, the Rodrigues vector in rad and in degrees, the rotation angle and axis, and the file's `meta`. With no file installed or several, it says what to do (exit code 1). `--json` prints the dict `show()` returns, and on an error `{"error": "<the message>"}` (the message also on stderr, exit code 1). `--synthetic` shows a generated calibration instead of a file. Opens no camera |
| `check [--file FILE]... [--all] [--frames N] [--pairs-dir DIR] [--synthetic] [--usb-location LOCATION]` | opens the camera with `DeviceConfig.weevia_s1()` (with `--usb-location`, the Weevia S1 at that USB location: `DeviceConfig.weevia_s1(usb_location=...)`; not with `--pairs-dir` or `--synthetic`; run without a camera only, not with two cameras), reads `N` pairs (`read_pairs`, default 10, at least `MIN_PAIRS`), closes it, then prints `CheckResult.summary()` for the installed file or for `FILE`. With more than one file — several installed, `--all` (every installed file), or `--file` given more than once — it prints one row per file (serial and source, median, 95th percentile, share within 1 px, share of positive disparity, points) from `compare_pairs` and names the smallest median without calling it the camera's file. `--pairs-dir` reads saved pairs (`load_pairs`) instead of the camera. `--synthetic` uses generated frames of a generated calibration (`SyntheticPlaneSource`) instead of the camera; with `--file`, checks that file against them |

Exit code 0 on success, 1 on an error (message on stderr; when the camera is not
found or stops, the message adds what to do and that `--pairs-dir` works without
it), 2 for a command-line error (such as `--frames` below 5, or `show --synthetic`
with a serial or file, or a `--usb-location` with no Weevia S1 there or an empty one); `check` also exits 2 when
there were too few matches to tell for every file. The commands print UTF-8.

```
python -m opticmix_vision.calib add S1-0042.json
python -m opticmix_vision.calib list
python -m opticmix_vision.calib show
python -m opticmix_vision.calib show S1-0042 --json
python -m opticmix_vision.calib check
python -m opticmix_vision.calib check --all
python -m opticmix_vision.calib check --pairs-dir captures/20261124-101500 --file S1-0042.json
```

---

## `opticmix_vision.presets`

Control presets: every readable control saved to a JSON file, and loaded back with
`Controls.load_preset()` in one guarded `set_many()` call. Save and load with
`Controls.save_preset()`, `Controls.plan_preset()` and `Controls.load_preset()`;
this module has the file format, the preset folder and the command line. The
viewer (`n` / `p` / `P` keys, `--preset`) and the explorer's Settings window use
the same files and the same calls.

Run without a camera only; writing a preset to the camera writes several controls
at once — see README, "Camera controls": write once at startup.

Rust: the `presets` module of the `opticmix-vision` crate (from 0.0.8) reads and
saves the same files in the same folder — `store_dir()`, `preset_path()`,
`read_preset()`, `list_presets()` and `PresetInfo`, `Controls::save_preset()`, and
`Controls::plan_preset()`, whose `changes` are what `plan_preset()` returns here,
with `unchanged`, `left_out` and `skipped_mono` as in `PresetLoad` — with the same
checks and messages (`SdkError::Preset` for `PresetError`, `SdkError::PresetModel`
for `PresetModelError`); `Controls::model()` is `Controls.model`. Differences: Rust
lists controls in the camera's control order, Python in the file's (the files both
write are in that order); a file it cannot read is `SdkError::Preset` (here
`OSError`), and for a file that is not JSON the parser's words differ. Applying a
preset to the camera is not in the Rust crate yet (Python: `presets load`). Run
without a camera only (simulated controls, preset files written by the Python
package); not run with the camera.

A preset file:

```json
{
  "format": "opticmix-controls-preset",
  "version": 1,
  "name": "bench",
  "note": "lab bench",
  "device": {"model": "WN Dual Camera", "product": "Weevia S1", "backend": "dshow"},
  "sdk_version": "0.0.5",
  "saved_at": "2026-11-24T09:00:00+00:00",
  "controls": {
    "gain": {"value": 28, "units": "raw"},
    "exposure": {"value": -5, "units": "log2_s_nominal"},
    "auto_exposure": {"value": "manual", "units": "raw"}
  }
}
```

`controls` holds every control the camera could read, in `CONTROLS` order, in the
form `set()` takes; `units` is `ControlInfo.units`. A file may leave controls out;
those are not touched. The device values in the example above are illustrative.

### `FORMAT`

`"opticmix-controls-preset"`, the `format` of every preset file.

### `VERSION`

`1`, the file version this SDK writes and reads.

### `BUILTIN`

`"defaults"`: on the command line, `load defaults` is
`Controls.reset_to_defaults()` (the camera's own defaults; auto/manual modes are
left alone; not yet run against the camera). It is not a file.

### `PresetError`

Subclass of `ValueError`. The file is not a preset, or it does not fit the connected
camera. Nothing was written.

### `PresetModelError`

Subclass of `PresetError`. The preset was saved from another camera model, or this
camera's model is unknown (`Controls.model` is `None`). `force=True` loads it
anyway; the explorer offers "Load anyway".

### `PresetInfo(name: str, path: Path, model: str | None, saved_at: str, note: str)`

One preset in `store_dir()` (named tuple). `name` is the file name without `.json`.

### `PresetLoad(results: tuple[WriteResult, ...], unchanged: tuple[str, ...], left_out: tuple[str, ...], skipped_mono: tuple[str, ...] = ())`

What `Controls.load_preset()` did (frozen dataclass).

| Field | Meaning |
|---|---|
| `results` | one `WriteResult` per control written: final, or `"pending"` if `timeout` ran out |
| `unchanged` | controls already at the preset's value — not written |
| `left_out` | values of a group the preset puts in auto — not written |
| `skipped_mono` | controls with no effect on the mono sensor whose value differs — not written (`include_mono=True` writes them) |

#### `PresetLoad.ok`

Property. `True` when every write finished and the camera kept every value it read
back (no `"failed"`, `"pending"` or `kept == False`).

### `store_dir() -> Path`

The preset folder: `%APPDATA%\opticmix\presets` on Windows;
`$XDG_CONFIG_HOME/opticmix/presets`, or `~/.config/opticmix/presets`, elsewhere —
beside the calibration folder.

### `preset_path(name: str) -> Path`

`store_dir()/<name>.json`. Raises `PresetError` when `name` is a path rather than a
file name.

### `read_preset(path: str | Path) -> dict[str, Any]`

The preset as a `dict`, after checking `format`, `version`, `device` and
`controls`. Raises `PresetError` for a file that is not a preset and `OSError` when
it cannot be read. Does not compare it with a camera.

### `list_presets(model: str | None = None) -> list[PresetInfo]`

The presets in `store_dir()`, sorted by name. With `model`, only those saved from
that model — pass `Controls.model` to see the presets for the connected camera.
Files that are not presets are skipped (a warning is logged).

### `main(argv: list[str] | None = None) -> int`

The command line below. Returns the exit code.

### Command line: `python -m opticmix_vision.presets`

```
usage: python -m opticmix_vision.presets [-h] {list,show,save,load} ...

positional arguments:
  {list,show,save,load}
    list                the presets in the preset folder (for the connected
                        model, if one is found)
    show                what a preset holds
    save                read every control from the camera into NAME
    load                write a preset to the camera ('defaults': the camera's
                        own defaults)
```

| Command | Opens the camera | Does |
|---|---|---|
| `list [--model MODEL] [--all]` | no | prints `store_dir()`, the built-in `defaults` and each preset's name, model, time and note. By default only the presets of the connected model, when a Weevia S1 is in the device list (DirectShow list or sysfs, read without opening the camera); otherwise every preset. `--model` picks a model, `--all` shows every preset |
| `show NAME\|FILE` | no | prints a preset's header and values |
| `save NAME [--note TEXT] [--overwrite] [--usb-location LOCATION]` | yes, reads only | `save_preset(preset_path(NAME))`; refuses an existing name without `--overwrite` |
| `load NAME\|FILE\|defaults [--dry-run] [--force] [--include-mono] [--usb-location LOCATION]` | yes | `--dry-run` prints `plan_preset()` (or `plan_reset()`) and writes nothing; otherwise `load_preset()` (or `reset_to_defaults()`) and prints each result with `kept`, what was not written and why, then this process's write count |

`save` and `load` take `--synthetic` to use simulated controls instead of a camera, and `--usb-location LOCATION`
to open the Weevia S1 at that USB location (`DeviceConfig.weevia_s1(usb_location=...)`), to choose between two that
share a name: every control read and written is that camera's. A USB location is not a serial number: it names the port the camera is plugged into, and it changes when the camera is plugged into another port. Run without a camera only (made-up device lists); not run with a camera, and not run with two cameras.
A `NAME` is looked up in `store_dir()` (a missing one says so and names the
folder); an existing file or a path ending in `.json` is used as it is. Exit code 0 on success; 1 on an error (message on
stderr) or when a loaded value was refused or not kept; 2 for a command-line error, and for a `--usb-location` with
no Weevia S1 there or an empty one (nothing opened, nothing written).

```
python -m opticmix_vision.presets save bench --note "lab bench"
python -m opticmix_vision.presets load bench --dry-run
python -m opticmix_vision.presets load bench
```


## `opticmix_vision.doctor`

One command that prints what to paste into a bug report. Without `--open` it does
not open the camera: the USB details come from the operating system's device list
(Windows: the device tree through cfgmgr32; Linux: sysfs). On Windows the link
speed is asked of the USB hub driver the camera is plugged into, as Microsoft's
USBView does; when that fails it is `unknown`.

The report holds:

| Part | What |
|---|---|
| Findings | One line per problem found: the compiled extension missing, no Weevia S1 or two of them, a device the OS reports with a problem code, a USB link below SuperSpeed, a camera that did not open, a negotiated format other than MJPG, frames arriving well below the negotiated rate, a read error, a stalled eye, repeated images through OpenCV's Media Foundation backend (`--backend msmf`: more than 5 % of the frames repeated the previous image in both eyes), any control write |
| System, SDK | OS, Python, `opticmix_vision` and the installed pip packages, `_core` (present or the import error), numpy, OpenCV and its camera backends, and whether `OPTICMIX_WRITE_LOG` is set |
| Video devices | Windows: the DirectShow list with its index (`DeviceConfig(device=...)`), then the Media Foundation list (`list_devices(backend="msmf")`, the indexes `backend="msmf"` takes; listed, no device opened) and, when the Weevia S1's index differs between the two, a line saying so — information, not a finding (the two lists can be in a different order). Linux: `/dev/videoN`, name and sysfs `index`. How many Weevia S1 are connected, counted as `DeviceConfig.weevia_s1()` looks them up |
| USB | For each camera: VID:PID, USB device revision (`bcdDevice`), status (`OK`, `problem code N`), location path, link speed (`Low`, `Full`, `High`, `Super`, `Super+` or `unknown`) and, on Windows, whether the device is SuperSpeed-capable |
| Not reported by this camera | Serial number (the camera has none), firmware version (no verified way to read it — the USB device revision is shown instead, and how it relates to the firmware version has not been verified), temperature (no readout is known) |
| Calibration | The `calib.store_dir()` folder, the files in it, what `calib.resolve()` says, and the command that prints the file's values (`python -m opticmix_vision.calib show`) |
| Camera (`--open` only) | The modes the camera advertises (Windows, when ffmpeg is on the PATH); the negotiated mode; for `--seconds` (default 3): frames, frame rate and intervals on the host clock, late intervals (over 1.5× the median), images identical to the previous one per eye and in both eyes, and the longest run, compared with `DeviceConfig`'s `stall_frames` default (90); every control's value, range, default, units and verification state (read only); the SDK's control-write count before and after (`write_counts()`), which should not change |
| Log | Warnings logged by `opticmix_vision` and Python warnings raised during the run (for example the control-write warnings) |

Paths under your home folder are shown starting with `~`; no USB serial number or
device instance ID is included.

### `collect(*, open_camera: bool = False, synthetic: bool = False, seconds: float = 3.0, device: int | str | None = None, timestamps: str | Path | None = None, backend: str | None = None) -> dict[str, Any]`

The report as a `dict` that `json.dumps` accepts (keys `report`, `format`, `time`,
`synthetic`, `system`, `sdk`, `video_devices`, `usb`, `weevia_s1`, `not_available`,
`calibration`, `camera`, `log`, `findings`; `weevia_s1.count` is `None` where the
devices were not looked for). On Windows, `video_devices.media_foundation` holds the
Media Foundation list: `devices` (`index`, `name`), `error` and `note` (the line
printed when the Weevia S1's index differs from DirectShow's, else `None`); it is
absent on other systems and with `synthetic=True` (what the two lists look like: the
example output in tutorial 01). `video_devices.source` (and `usb.source`) is a label for
people to read — do not compare it in a program. `open_camera=True` opens the
camera as `Device(DeviceConfig.weevia_s1(stall_frames=0))` (or with `device=` and
`backend=`: `"dshow"`, `"msmf"` or `"v4l2"`; `None` keeps the Weevia S1 default), sets
no control, reads frames for `seconds` and reads every control; `timestamps` names a
CSV file to write each frame's `frame, host_ns, interval_ms, left_identical,
right_identical` to. `synthetic=True` reads no hardware: the device and USB parts
are skipped, and `open_camera` uses `SyntheticStereoSource` (generated frames,
simulated controls). Errors are reported in the dict, not raised.

### `format_text(report: dict[str, Any]) -> str`

The report as the text the command prints.

### `main(argv: list[str] | None = None) -> int`

The command line below. Returns 0 when the report was printed (problems are in the
report, under Findings); 2 for a command-line error, and for a `--usb-location` with no Weevia S1 there or an
empty one (no report is printed).

### Command line: `python -m opticmix_vision.doctor`

```
usage: python -m opticmix_vision.doctor [-h] [--open] [--synthetic] [--json] [--seconds SECONDS]
                                        [--device DEVICE] [--usb-location LOCATION] [--timestamps FILE]
                                        [--backend {dshow,msmf,v4l2}] [--output FILE]

options:
  --open             open the camera: mode, frame rate, stalls, controls (read only)
  --synthetic        read no hardware: generated frames, simulated controls
  --json             print the report as JSON
  --seconds SECONDS  how long --open reads frames (default 3)
  --device DEVICE    with --open: the device index or /dev/videoN instead of finding it by name
  --usb-location LOCATION
                     with --open: open the Weevia S1 plugged in at this USB location, to choose
                     between two that share a name (DeviceConfig.weevia_s1(usb_location=...));
                     Windows: as examples/list_devices.py or python -m opticmix_vision.doctor
                     (location) prints it; Linux: the USB device's sysfs name, e.g. 1-4.2; no
                     Weevia S1 there, or an empty one: exit code 2. Not a serial number: it
                     changes when the camera is plugged into another port. This option: run
                     without a camera only (made-up device lists); not run with a camera, and
                     not run with two cameras
  --timestamps FILE  with --open: write each frame's host time to a CSV file
  --backend {dshow,msmf,v4l2}
                     with --open: the OpenCV capture backend (default: the Weevia S1 default for
                     this OS)
  --output FILE      write the report to FILE in UTF-8 instead of printing it (JSON when FILE ends
                     in .json, or with --json)
```

```
python -m opticmix_vision.doctor                  # paste the output into the issue
python -m opticmix_vision.doctor --open           # also opens the camera, writes no control
python -m opticmix_vision.doctor --open --backend msmf   # through OpenCV's Media Foundation backend: repeated images are a finding
python -m opticmix_vision.doctor --open --usb-location "PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(1)"   # one of two Weevia S1
python -m opticmix_vision.doctor --output report.json   # UTF-8 JSON file to attach
python -m opticmix_vision.doctor --synthetic --open
```

Use `--output` rather than `>` to save the report: in Windows PowerShell 5.1, `>`
writes UTF-16, which `json.load` does not read as UTF-8.

`--open --usb-location LOCATION` opens the Weevia S1 that `DeviceConfig.weevia_s1(usb_location=...)` finds at
that USB location (with `--backend`, in that backend's list) and reads that camera's controls; not with `--device`
or `--synthetic`. The report's Camera section then begins with that USB location and the device opened (`camera.usb_location` and `camera.device` in the JSON) and a line saying that the modes the camera advertises are not listed (ffmpeg asks by name). A USB location is not a serial number: it names the port the camera is plugged into, and it changes when the camera is plugged into another port. Run without a camera only (made-up device lists); not run with a camera, and not run with two cameras.

The explorer's top bar shows the same USB line for the Weevia S1 (VID:PID, USB
device revision, link speed, no serial), read without opening the camera.

`--open` has been run with the camera once (one PC, one camera, 2026-11-24); `--open --backend msmf` once (one PC, one camera, 2026-11-25, OpenCV 4.13.0: the repeated-images finding said 276 of 904); without `--open`, `--json`, the other `--backend` values and Linux: run without a camera only
(generated frames, and fake device lists on Windows and Linux).

---

## `opticmix_vision.viewer`

A window showing the left and right images side by side, with keys for the
illuminator, exposure and gain, saving of left/right PNGs, and recording to a video
file; it also plays such a recording back, with keys to pause, step, jump and change
the speed. The top line shows the frame index, frames per second and the late /
missed frame estimates (`Device.late_frames`, `estimated_missed_frames`,
`actual_fps`), and `repeated N` once a frame's two eyes both repeated the previous
image (`Device.repeated_frames`; run without a camera only). A second line shows each eye's share of saturated pixels, for setting
the LED and exposure. The viewer reads every control once, when it opens
(`controls.snapshot()`). After that, the LED / exposure / gain line and the
controls in the `.json` of `s` and `v` are those values with the viewer's own
writes (the value read back after each); a control whose write was refused is read
once again, and `n`, `p` and `P` read every control (to save or compare a preset).
A change made by another program shows when the viewer is opened again. Keys `n`,
`p` and `P` save, choose and load control presets
(the [`presets`](#opticmix_visionpresets) files), and the camera's own defaults
(`Controls.plan_reset` / `reset_to_defaults`, as the explorer's Reset to defaults).
Key `c` shows the installed calibration file (`Device.calibration`), read from the
file only. Recording with `v` has been run with the camera once (one PC, one camera, 2026-11-24): one recording started and stopped with `v` (392 frames, about 130 missed while recording; 0.0.5, before the writing thread), no stall error; its `.json` carries `controls_note`. Playback keys, the frame
statistics, the saturation line, the preset keys, the defaults and `c` have been run on generated
frames only; not run with the camera. The functions below are the loop the
command uses, usable on their own (with a window, or headless).

### `KEY_HELP`

`str` — the key help shown with `h` (wrapped to the window's width, about 9 pixels a character; the
calibration lines and messages wrap the same way):
`q/ESC quit | s save | v record | l/L LED -/+ | e/E exposure -/+ | g/G gain -/+ | a auto-exposure | n save preset | p/P choose/load preset or defaults | c calibration file | recording: space pause ,/. step [/] -/+1 s -/+ speed | LED/exp/gain line: as opened + viewer's writes | h help`.

### `ViewerState(led_step: float = 8.0, exposure_step: float = 1.0, gain_step: float = 8.0, saved: int = 0, show_help: bool = False, key_help: str = "q/ESC quit | s save | v record | l/L LED -/+ | e/E exposure -/+ | g/G gain -/+ | a auto-exposure | n save preset | p/P choose/load preset or defaults | c calibration file | recording: space pause ,/. step [/] -/+1 s -/+ speed | LED/exp/gain line: as opened + viewer's writes | h help", last_error: str = "", messages: list[str] = <factory>, saturation_level: int = 255, preset_name: str | None = None)`

Key-handling state (dataclass). The `*_step` fields set how far one key press moves
the illuminator, exposure and gain. `saved` counts saved pairs, `messages` collects
status lines, `last_error` keeps the last error. While a key is held, only the
target value changes; it is written once after the keys have been quiet for 0.6 s.
A key goes on from the value the overlay shows (read once, then the viewer's own
writes), not from a fresh read of the camera.
`saturation_level`: the overlay counts pixel values at this level or above as
saturated. `preset_name`: the name `n` saves under (`None`: `viewer_<time>`).
Preset messages are also printed (`[viewer] ...`; errors to stderr).

#### `ViewerState.handle_key(key: int, dev: Any, now: float | None = None) -> str | None`

Handles one key code for the source `dev`. Returns `"quit"`, `"save"`, `"record"`
(the `v` key) or `None`. `n`, `p` and `P` call `save_preset`, `choose_preset`
and `load_preset` below; `c` calls `show_calibration`.
On quit, pending changes are written first. When `dev` plays a recording
(`dev.playback`), space pauses and resumes (at the end: plays again from the
start), `,`/`.` pause and step one frame back/forward, `[`/`]` jump one second
back/forward, and `-`/`+` (or `=`) halve or double the speed (1/16× to 16×); the
control and preset keys only show that a recording has no camera controls and
that the values when it was recorded are in its `.json`.

#### `ViewerState.commit(dev: Any, now: float | None = None, *, force: bool = False) -> None`

Writes the accumulated target values once the keys have been quiet long enough, or
immediately with `force=True`.

#### `ViewerState.save_preset(dev: Any) -> Path | None`

The `n` key: `Controls.save_preset` of every readable control into
`presets.preset_path(preset_name or "viewer_<time>")`. Reads only. An existing file
is not replaced (the message says to choose another `--preset-name`). Returns the
file, or `None` with the reason in `last_error`.

#### `ViewerState.choose_preset(dev: Any, path: str | Path | None = None) -> None`

The `p` key: the next preset in `presets.list_presets(controls.model)` (the presets
saved from this camera's model, in name order), after the last one `defaults` — the
camera's own defaults, what the explorer's Reset to defaults and
`python -m opticmix_vision.presets load defaults` write — then the first again; or
`path` (`"defaults"`, `presets.BUILTIN`, for the defaults; a file named `defaults`
as a `Path`). It shows what loading it would change (`Controls.plan_preset`;
`Controls.plan_reset` for the defaults: auto/manual modes left alone, values in auto
left out), as `preset NAME (i/n): k to write: gain 100->32, ... - P loads` or
`preset defaults: the camera's own defaults, auto/manual modes left alone; k to
write: ... - P loads`; with no saved preset it says so first. Writes nothing. A
preset that does not fit this camera (a control it lacks, other units, a value out
of range) is refused with the reason (`... not usable: ... Nothing was written.`)
and `P` then loads nothing. A preset saved from another model is shown with
`saved from X, not this camera (Y)` and `P loads anyway` — like the explorer's
Load anyway.

#### `ViewerState.load_preset(dev: Any) -> None`

The `P` key: writes the preset chosen with `p` (or `--preset`) with
`Controls.load_preset` — the whole file is checked first, then one `set_many()` of
the values that differ; controls with no effect on the mono sensor are not written.
For `defaults` it calls `Controls.reset_to_defaults()` (the recording's JSON entry is
`{"name": "preset", "value": "defaults", ...}`). Either way the values written, as
read back, become the values the overlay and the `.json` of `s` and `v` show.
A change from the LED / exposure / gain keys still waiting is written first, so the
preset's values win. Does not wait for the writes. Inside `run_viewer`, a load made
while recording goes into the recording's JSON when it is requested
(`Recorder.note_control`, like the keys): `{"name": "preset", "value": "<file name>",
"status": "loaded"}`, then each control queued with its status at the request (for
example `"pending"`).

#### `ViewerState.show_calibration(dev: Any) -> None`

The `c` key: the installed calibration file as four lines on the window and in the log
— `calibration SERIAL (MODEL)  file PATH - installed file, not matched to this camera
(no USB serial)`, then per eye
`left   KB4 WxH  fx ..  fy ..  cx ..  cy ..  D k1 k2 k3 k4`, then
`baseline .. mm  t tx ty tz mm  R .. deg` (the rotation angle between the eyes);
`c` again hides them. The file is `dev.calibration` (`Device.calibration`: exactly one
installed file, its eye size checked against the mode), or `calib.resolve()` for a
source without it. Reads the file only: no control is read or written, and no
rectified view is drawn. With no file, more than one, or another eye size, the lines
are `calib.CalibLookupError`'s message (`calibration: No calibration installed.
Install the file for your camera: python -m opticmix_vision.calib add <file.json>`).

#### `ViewerState.poll(timeout: float | None = None) -> None`

Reports a preset load once every write has its final result: `preset NAME loaded:
k written`, or each control that was refused (`gain failed (the driver's error)`)
or that the camera did not keep (`gain: camera kept 32, not 40`) in `last_error`,
with one line per write in the log. It does not write to the recording; the camera's
values when recording stops are in `controls_at_end`. Only looks; with `timeout`,
waits up to that long first (`run_viewer` waits 2 s at the end).

### `compose_view(frame: StereoFrame, *, texts: Sequence[str] = (), gap: int = 8, scale: float = 1.0, left: np.ndarray | None = None, right: np.ndarray | None = None) -> np.ndarray`

Left | right side by side as one BGR image, separated by `gap` pixels, scaled by
`scale`, with `texts` drawn line by line at the top left. `left`/`right`, when
given, are drawn instead of the frame's images.

### `run_viewer(source: Any, *, headless: bool = False, max_frames: int | None = None, out_dir: str | Path | None = None, key_feed: Iterable[int] | None = None, window: str = "Weevia S1 Viewer", scale: float = 1.0, state: ViewerState | None = None, view: View | None = None, wait_ms: int = 1, lossless: bool = False) -> dict[str, Any]`

The loop: read → view → compose → show → key. `source` is a `Device` or
`SyntheticStereoSource`.

- `headless=True` runs without a window; `key_feed` then supplies the key codes,
  one per frame, and the loop ends when it runs out. Each fed key is a separate press.
- In the window, `s`, `v`, `n`, `p`, `P`, `a`, `c`, `h` and space act once per press:
  OpenCV reports no key release, so a key held down comes again and again, and the
  same key within 0.6 s of the last time it came is ignored — after a short pause
  it acts again. The window says so once per press (`v ignored: same key within
  0.6 s (held?) - press again after a pause`), not on every repeat. A keyboard
  repeat delay set longer than 0.6 s in the OS lets one repeat through. The LED /
  exposure / gain and seek keys go on while held.
- `max_frames` stops after that many frames. `out_dir` is where `s` saves
  (default `captures`): `<time>_<frame>_left.png`, `_right.png` and a `.json` of
  [`capture_info`](#capture_infosource-any--controls-dictstr-any--none--none-controls_read_at-str--none--none-extra-any---dictstr-any) with the frame's
  index and `host_ns`.
- `view(frame, state) -> (left, right, extra_text_lines)` changes only what is
  drawn; `s` always saves, and `v` always records, the frame as received.
- `v` starts and stops a recording in `out_dir` with
  [`recording.Recorder`](#recorderpath-str--path-source-any--codec-str--mjpg-fps-float--none--none-controls-callable-dictstr-any--none--none--none-controls_read_at-str--none--none-queue_frames-int--0):
  `<time>_<frame>.avi` (MJPG, both eyes side by side, left eye in the left half), or
  `.mkv` (FFV1, lossless) with `lossless=True`, and the `.csv` and `.json` next to
  it — the files `examples/record_video.py` writes. The files are opened when `v` is
  pressed and the recording starts with the next frame. Control changes written while
  recording go into the JSON (`Recorder.note_control`). A thread encodes while the
  window draws (`Recorder(..., queue_frames=REC_QUEUE)`, `REC_QUEUE` = 64 frames): a
  frame that finds the queue full is not recorded, and the REC line
  (`missed~N since start (dropped N)`), the JSON's `dropped_frames` and the message
  when recording stops count it. A dropped frame is also a gap in the frame times, so
  the missed estimate includes it: the message says `about N frames missed while
  recording (estimated from the frame times; the N frames the write queue dropped are
  among them, not in addition)`, and when the dropped frames are most of the missed
  ones it advises MJPG (when lossless) or a lower `--fps`, or
  `examples/capture_dataset_npz.py`, instead of `examples/record_video.py`. Stopping — `v`, `q`, `max_frames`, the end of the loop — writes the frames
  still queued before the files close, so the window waits for them. The REC line
  also shows the frames written so far and those estimated missed since recording
  started; when recording stops with missed frames, the message says what to do:
  first `examples/record_video.py`, which records with no preview window, then
  `Recorder.missed_advice` (MJPG when it was lossless, a lower `--fps`,
  `examples/capture_dataset_npz.py`). `v` runs alongside the preview and may not
  keep up with 90 fps even with MJPG. Writing on a thread: run on generated frames
  only; not run with the camera. If the video writer cannot be opened, or writing
  fails (for example a full disk, or any other error the encoder raises), the reason,
  with the frames written and dropped, goes to `last_error`, the window and stderr, and the files are closed. A recording
  still open when the loop ends is closed. The `.json` of `s` and `v` has
  `controls_source` `"given"` and `controls_read_at`, the time the viewer read every
  control when it opened.
- Each frame the window shows `saturated (>=255)  L 0.00%  R 0.00%`: the percent of
  each eye's pixel values at `state.saturation_level` or above, counted on every
  pixel of the frame as received (not what `view` draws). It is not written to the
  recording or capture JSON.
- `state.poll()` runs every frame, so a preset loaded with `P` reports its results
  as they arrive; the load itself goes into a recording's JSON when `P` is pressed.
- `wait_ms` is how long the window waits for a key each frame.
- When `source` is a recording (`DeviceConfig.recording`), the window shows its
  position, length, speed and status with a seek bar under the image
  (`cv2.createTrackbar`), and the LED / exposure line is hidden. After the last
  frame (without `source.playback.loop`) the window stays on it, paused, until `q`;
  headless, the loop ends quietly.

Returns `{"frames": int, "saved": int, "fps": float, "last_error": str}`.

```python
from opticmix_vision.synthetic import SyntheticStereoSource
from opticmix_vision.viewer import run_viewer

with SyntheticStereoSource() as src:
    stats = run_viewer(src, headless=True, max_frames=30)
print(stats["frames"])   # 30
```

### `main(argv: Sequence[str] | None = None) -> int`

The command line below. Returns the exit code.

### Command line: `python -m opticmix_vision.viewer`

```
usage: python -m opticmix_vision.viewer [-h] [--synthetic]
                                        [--recording RECORDING] [--loop]
                                        [--lossless] [--eye-size EYE_SIZE]
                                        [--device DEVICE]
                                        [--usb-location LOCATION]
                                        [--backend {any,dshow,msmf}]
                                        [--layout {sbs,tb,plane_pack}]
                                        [--lr-order {first_is_left,first_is_right}]
                                        [--width WIDTH] [--height HEIGHT]
                                        [--fps FPS] [--fourcc FOURCC]
                                        [--out OUT] [--scale SCALE]
                                        [--headless] [--frames FRAMES]
                                        [--preset NAME|FILE]
                                        [--preset-name NAME]
                                        [--saturation-level N]
```

| Option | Default | Meaning |
|---|---|---|
| `--synthetic` | off | use `SyntheticStereoSource`, no camera |
| `--recording FILE` | none | play a recording made with `v` or `examples/record_video.py` (`DeviceConfig.recording`), at the recorded pace (the CSV's frame times; as fast as it decodes with `--headless`); stays on the last frame (headless: exits after it). Not with `--synthetic` or `--device` |
| `--loop` | off | with `--recording`: start again after the last frame |
| `--lossless` | off | `v` records FFV1 `.mkv` (the pixel values the SDK delivered) instead of MJPG `.avi`; may not keep up with 90 fps |
| `--eye-size WxH` | `320x200` | synthetic eye size |
| `--device DEVICE` | none | camera index, or a path. Without it the Weevia S1 is found by name (`DeviceConfig.weevia_s1()`) |
| `--usb-location LOCATION` | none | the Weevia S1 at this USB location, to choose between two that share a name: `DeviceConfig.weevia_s1(usb_location=...)` (with `--backend`, in that backend's list); the keys, `p`/`P` and `--preset` read and write that camera's controls. Windows: as `examples/list_devices.py` prints it; Linux: the USB device's sysfs name, e.g. `1-4.2`. None there (the message lists each device's name and USB location), an empty one, or given with `--device`, `--synthetic` or `--recording`: exit code 2. A USB location is not a serial number: it names the port the camera is plugged into, and it changes when the camera is plugged into another port. Run without a camera only (made-up device lists); not run with a camera, and not run with two cameras |
| `--backend {any,dshow,msmf}` | `dshow` | OpenCV backend, with or without `--device` (without it the Weevia S1 is looked up in that backend's list: `DeviceConfig.weevia_s1(backend=...)`; Linux: `v4l2` unless given). With `msmf` the camera controls (keys, presets, defaults) go through the OpenCV fallback, which has fewer controls. `--backend` without `--device`: run without a camera only |
| `--layout {sbs,tb,plane_pack}` | none | required with `--device` |
| `--lr-order {first_is_left,first_is_right}` | none | required with `--device` |
| `--width`, `--height`, `--fps`, `--fourcc` | none | requested mode; override the Weevia S1 settings when given. `--fps` also sets the synthetic rate (default 90) |
| `--out OUT` | `captures` | folder for the `s` and `v` keys |
| `--scale SCALE` | `1.0` | display scale |
| `--headless` | off | no window |
| `--frames FRAMES` | none | read this many frames, then exit |
| `--preset NAME\|FILE` | none | before the first frame, load this preset (a name from `python -m opticmix_vision.presets list`, or a file): what it changes is printed, then written in one `set_many()`. A preset from another model is not loaded at start; `P` loads it. A missing preset, or `defaults` (the presets command's built-in), exits with code 2 before the camera is opened. Not with `--recording` |
| `--preset-name NAME` | `viewer_<time>` | the name `n` saves under; an existing preset is not replaced |
| `--saturation-level N` | `255` | the overlay counts pixel values at `N` or above (1–255) as saturated |

Keys: `q`/`Esc` quit, `s` save left/right PNG, `v` start/stop recording,
`l`/`L` LED −/+, `e`/`E`
exposure −/+, `g`/`G` gain −/+, `a` auto-exposure on/off, `n` save the controls as
a new preset, `p` next preset saved from this camera's model (shows what loading it
would change; writes nothing), `P` load that preset, `h` key help.
Playing a recording: space pause/resume (at the end: play again), `,`/`.` one frame
back/forward, `[`/`]` one second back/forward, `-`/`+` half/double speed, and the bar
under the image seeks.

Exit code 0 on success; 2 when the camera cannot be opened or a frame read fails
(one line on stderr), and for a `--usb-location` with no Weevia S1 there. `--device` without `--layout` and `--lr-order` exits with a
message.

```
python -m opticmix_vision.viewer
python -m opticmix_vision.viewer --synthetic
python -m opticmix_vision.viewer --recording captures/20261124_101500_000123.avi
python -m opticmix_vision.viewer --recording captures/20261124_101500_000123.avi --loop
python -m opticmix_vision.viewer --lossless
python -m opticmix_vision.viewer --preset bench
python -m opticmix_vision.viewer --device 1 --layout sbs --lr-order first_is_right --width 1600 --height 800 --fps 90 --fourcc MJPG
```

---

## `opticmix_vision.explorer`

A window (Tk) to see whether the camera works and to change its controls (on Linux, to look at them). Only the
command and `main` are public. The Settings window (gear button) has
"Reset to defaults…", "Save preset…" (`Controls.save_preset()` to a file you pick,
starting in `presets.store_dir()`) and "Load preset…" (the presets saved from this
camera's model — name, time, note — or "Other file…"; a dialog lists what would
change and how many writes that is, and nothing is written until "Load"; a preset
from another model offers "Load anyway"). Preset buttons: run without a camera only.

### `main(argv=None) -> int`

Runs the window with the arguments below. Returns the exit code.

### Command line: `python -m opticmix_vision.explorer`

```
usage: opticmix-explorer [-h] [--synthetic] [--usb-location LOCATION]

options:
  -h, --help            show this help message and exit
  --synthetic           run on generated frames, with no camera attached
  --usb-location LOCATION
                        open the Weevia S1 plugged in at this USB location, to
                        choose between two that share a name
                        (DeviceConfig.weevia_s1(usb_location=...)); as
                        examples/list_devices.py or python -m
                        opticmix_vision.doctor (location) prints it; on Linux
                        the USB device's sysfs name, e.g. 1-4.2; no Weevia S1
                        there, or an empty one: exit code 2. Not a serial
                        number: it changes when the camera is plugged into
                        another port. This option: run without a camera only
                        (made-up device lists); not run with a camera, and not
                        run with two cameras
```

`--usb-location`: the device list holds only the Weevia S1 that `DeviceConfig.weevia_s1(usb_location=...)` finds there (the refresh button looks again; nothing there: the message says what is where), it is found again by that USB location at every open (not there: it is not opened, and the window shows the error), the USB line above it (VID:PID, revision, link speed) is that camera's, and the Settings window reads and writes that camera's controls (on Linux, reads them only). No Weevia S1 at that location, or an empty one: exit code 2 before the window opens; not with `--synthetic`. A USB location is not a serial number: it names the port the camera is plugged into, and it changes when the camera is plugged into another port. Run without a camera only (made-up device lists); not run with a camera, and not run with two cameras.

Without `--usb-location`, two cameras that share a name are listed with their USB locations (`WN Dual Camera · <USB location>`), the one chosen in the list is opened, found again by its USB location at every open (the list's order can change), and the Settings window writes to it; two of one name whose USB locations cannot be read are not opened (the window says to connect one at a time). With two of one name, or with `--usb-location`, the mode list is not asked of ffmpeg (it asks by name): the window opens 1600×800 MJPG and says why. Run without a camera only (made-up device lists); not run with two cameras.

On Linux (from 0.0.8) the device list is V4L2's (`devices.list_devices()`, the default there: one entry per `/dev/videoN` capture node, with the USB device's sysfs name as its USB location), the camera is opened with `backend="v4l2"` at `/dev/videoN` by its number and found again by that USB location at every open, `--usb-location` takes the sysfs name (e.g. `1-4.2`), and the USB line is read from sysfs as the doctor reads it. The mode list is not read there: the window opens 1600×800 MJPG and says why. The Settings window is read only: every control shows its value, range and default but is disabled, and so are Reset to defaults… and Load preset… (Save preset… reads only) — camera controls on Linux are not verified and not supported; the window says so, with the defaults line of Reset to defaults. Run without a camera only (made-up sysfs trees, fake widgets); not run with the camera on Linux, and not run with a display on Linux.
