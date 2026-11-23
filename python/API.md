# opticmix-vision API reference

Reference for the public Python API of `opticmix_vision` 0.0.4. It lists every
public name, with its signature as `inspect.signature` reports it. What has been
run against the camera, and on which platform, is stated in [README.md](README.md)
and not repeated here.

Public modules: [`opticmix_vision`](#opticmix_vision) (package),
[`device`](#opticmix_visiondevice), [`devices`](#opticmix_visiondevices),
[`controls`](#opticmix_visioncontrols), [`led`](#opticmix_visionled),
[`synthetic`](#opticmix_visionsynthetic), [`calibration`](#opticmix_visioncalibration),
[`calib`](#opticmix_visioncalib), [`viewer`](#opticmix_visionviewer),
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
| `__version__` | package — the version string, `"0.0.4"` |
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
| `stall_frames` | `int` | `90` | `read()` raises `DeviceError` when one eye sends the byte-identical image this many frames in a row. `0` turns the check off; otherwise 2 or more. Background: README, "When something goes wrong" |

Raises `ValueError` when a field is out of range (unknown `layout`, `lr_order` or
`backend`; non-positive `width`, `height` or `fps`; a `fourcc` that is not 4
characters; `stall_frames` negative or 1).

```python
from opticmix_vision import DeviceConfig

cfg = DeviceConfig(layout="sbs", lr_order="first_is_right", device=1,
                   width=1600, height=800, fps=90, fourcc="MJPG")
```

#### `DeviceConfig.weevia_s1(**overrides: Any) -> DeviceConfig`

Class method. The settings for a Weevia S1, with the camera found by name:
`layout="sbs"`, `lr_order="first_is_right"`, `backend="dshow"`, 1600×800,
`fps=90.0`, `fourcc="MJPG"`. Any field can be overridden by keyword.

- Windows: the device index is found with `devices.resolve_by_name("WN Dual Camera")`.
- Linux: the device is `devices.find_v4l2_node("WN Dual Camera")` and `backend`
  defaults to `"v4l2"`. As the README puts it: "Linux name lookup: implemented, not
  yet run on the camera."
- Passing `device=` skips the lookup.

Raises `DeviceError` when no camera, or more than one, matches the name.

```python
cfg = DeviceConfig.weevia_s1(fps=60)
cfg = DeviceConfig.weevia_s1(device="/dev/video2", backend="v4l2")   # name the node yourself
```

#### `DeviceConfig.recording(path: str | Path, **overrides: Any) -> DeviceConfig`

Class method. Plays a video file written by `examples/record_video.py` (both eyes
side by side, left eye in the left half) as if it were the camera: `layout="sbs"`,
`lr_order="first_is_left"`, `stall_frames=0`. This is the order in the file, not the
camera's: `record_video.py` writes the eyes after the split, left first; the camera
itself is `first_is_right`. `read()` raises `EndOfRecording` after the last frame. `host_ns` is the time the frame was read back, not when it
was captured — the recording's CSV has the capture times. A compressed recording
does not have the camera's pixel values.

Raises `DeviceError` when the file does not exist.

```python
with Device(DeviceConfig.recording("recording.avi")) as cam:
    try:
        while True:
            frame = cam.read()
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
backend name, `""` when not available).

#### `NegotiatedMode.to_dict() -> dict[str, Any]`

The five fields as a `dict`.

### `StereoFrame(left: np.ndarray, right: np.ndarray, host_ns: int, index: int)`

One stereo frame (frozen dataclass).

| Field | Meaning |
|---|---|
| `left`, `right` | (h, w) `uint8` images of the left and right eye. With `split_stereo`, these can be views into the raw frame |
| `host_ns` | host monotonic clock (`time.perf_counter_ns()`) taken right after the frame was read. It is not a device timestamp |
| `index` | frame number counted by this `Device`, from 0. It is not a device counter |

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
nothing.

On DirectShow the pixel format is set after size and frame rate; on V4L2 it is set
before the frame rate. The order is handled here, so set the mode through
`DeviceConfig`, not with your own `cap.set` calls.

Raises `DeviceError` when the camera cannot be opened, when the OpenCV build lacks
the requested backend, when `extra_props` names a format property, or when an
`extra_props` control write fails.

#### `Device.close() -> None`

Closes the controls (pending writes end as `cancelled`) and releases the capture.
Safe to call more than once.

#### `Device.is_open`

Property. `True` between `open()` and `close()`.

#### `Device.mode`

Property. The `NegotiatedMode`. Raises `DeviceError` before `open()`.

#### `Device.read() -> StereoFrame`

Reads one frame and splits it according to `config.layout` and `config.lr_order`.
Raises `DeviceError` when the read fails (timeout or camera unplugged), when
called before `open()`, or when the `stall_frames` check trips (the message names
the eye).

#### `Device.read_raw() -> tuple[np.ndarray, int]`

The raw frame before the split, and the host time in ns
(`time.perf_counter_ns()`). Raises `DeviceError` like `read()`. Does not run the
`stall_frames` check.

#### `Device.controls`

Property. The camera's `Controls`, created on first use. On Windows with
`backend="dshow"` and an integer `device`, controls go to DirectShow directly; if
that is not available, or on other backends, they go through OpenCV
(`Controls.backend_note` says why). Raises `DeviceError` before `open()`.

#### `Device.calibration`

Property. The `StereoCalibration` installed with
`python -m opticmix_vision.calib add <file.json>`, when exactly one is installed
(see [`calib.resolve`](#resolve---stereocalibration)). The SDK cannot check that the
file belongs to this camera: the camera reports no USB serial number. Raises
`calib.CalibLookupError` when none or more than one is installed, or when the
file's eye size does not match the frame (half the frame width × frame height).
Frame capture does not need a calibration.

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
selection is by name. `list_devices`, `resolve_by_name` and `list_modes` are for
Windows; `find_v4l2_node` is for Linux.

### `DeviceInfo(index: int, name: str)`

A video device: its DirectShow enumeration index (the same order OpenCV uses) and
its name.

### `Mode(fourcc: str, width: int, height: int, max_fps: float)`

A mode the device reports.

#### `Mode.label() -> str`

Display text, e.g. `"1600×800 MJPG · 90 fps"`.

### `NoMatch`

Subclass of `LookupError`. No camera matches the name. The message lists what was
found.

### `MultipleMatches`

Subclass of `LookupError`. More than one camera matches the name; without a serial
number they cannot be told apart, so none is picked.

### `list_devices() -> list[DeviceInfo]`

Video devices in DirectShow enumeration order (Windows). Built-in webcams are
included. Uses `ffmpeg` when it is on `PATH`, otherwise enumerates DirectShow
directly; when that fails too, returns an empty list.

### `resolve_by_name(needle: str) -> DeviceInfo`

The one device whose name contains `needle` (case-insensitive), Windows. Raises
`NoMatch` or `MultipleMatches`.

```python
from opticmix_vision.devices import resolve_by_name

info = resolve_by_name("WN Dual Camera")
print(info.index, info.name)
```

### `list_modes(name: str) -> list[Mode]`

The modes the named device reports (Windows, through `ffmpeg`). An empty list means
the modes could not be found out (for example, `ffmpeg` is not installed), not that
there are none.

### `find_v4l2_node(needle: str) -> str`

Linux. The `/dev/videoN` path of the one video capture node whose name contains
`needle` (case-insensitive), read from `/sys/class/video4linux`. A UVC camera has
more than one node with the same name; the capture node is the one whose sysfs
`index` is 0. Raises `NoMatch` (listing the nodes found) or `MultipleMatches`.

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

Attributes: `backend_name` (`str`, the backend in use) and `backend_note` (`str`,
why a fallback backend is in use, or what it lacks; may be empty).

#### `Controls.names() -> tuple[str, ...]`

Control names usable on this camera, in table order — only those the backend
supports.

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
`include_mono=True`.

#### `Controls.reset_to_defaults(*, include_mono: bool = False) -> list[WriteResult]`

Writes what `plan_reset()` lists. Does not wait.

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

A generated stereo source for running code without a camera. It is not measured
data. The right image is the left image shifted by a fixed disparity
(R(x) = L(x + d), d > 0), with a moving bright dot so frames visibly change.

### `SyntheticStereoSource(*, eye_size: tuple[int, int] = (320, 200), fps: float = 90.0, disparity: int = 8, seed: int = 0, realtime: bool = False)`

The same surface as `Device`: `read`, `get_prop`, `set_prop`, `controls`, `mode`,
`close`, and use as a context manager. `eye_size` is one eye's `(width, height)`;
`mode` reports a side-by-side frame of `2 × width`, fourcc `"SYNT"`, backend
`"synthetic"`. `realtime=True` paces `read()` to `fps`. Its controls use made-up
ranges and defaults with no write spacing; exposure, gain and
`backlight_compensation` change the image brightness.

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
installed, that file is used.

Files are stored as `<serial>.json` in `store_dir()`.

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
when none is installed, or when more than one is (the message lists them and the
folder).

### `main(argv: list[str] | None = None) -> int`

The command line below. Returns the exit code.

### Command line: `python -m opticmix_vision.calib`

```
usage: python -m opticmix_vision.calib [-h] {add,list} ...

positional arguments:
  {add,list}
    add       install the calibration file for your camera
    list      show installed calibrations and the folder
```

| Command | Does |
|---|---|
| `add FILE` | `add(FILE)`; prints the serial, the status and the installed path |
| `list` | prints `store_dir()` and the installed serials |

Exit code 0 on success, 1 on an error (message on stderr).

```
python -m opticmix_vision.calib add S1-0042.json
python -m opticmix_vision.calib list
```

---

## `opticmix_vision.viewer`

A window showing the left and right images side by side, with keys for the
illuminator, exposure and gain, and saving of left/right PNGs. The functions below
are the loop the command uses, usable on their own (with a window, or headless).

### `KEY_HELP`

`str` — the key help line shown with `h`:
`q/ESC quit | s save | l/L LED -/+ | e/E exposure -/+ | g/G gain -/+ | a auto-exposure | h help`.

### `ViewerState(led_step: float = 8.0, exposure_step: float = 1.0, gain_step: float = 8.0, saved: int = 0, show_help: bool = False, key_help: str = "q/ESC quit | s save | l/L LED -/+ | e/E exposure -/+ | g/G gain -/+ | a auto-exposure | h help", last_error: str = "", messages: list[str] = <factory>)`

Key-handling state (dataclass). The `*_step` fields set how far one key press moves
the illuminator, exposure and gain. `saved` counts saved pairs, `messages` collects
status lines, `last_error` keeps the last error. While a key is held, only the
target value changes; it is written once after the keys have been quiet for 0.6 s.

#### `ViewerState.handle_key(key: int, dev: Any, now: float | None = None) -> str | None`

Handles one key code for the source `dev`. Returns `"quit"`, `"save"` or `None`.
On quit, pending changes are written first.

#### `ViewerState.commit(dev: Any, now: float | None = None, *, force: bool = False) -> None`

Writes the accumulated target values once the keys have been quiet long enough, or
immediately with `force=True`.

### `compose_view(frame: StereoFrame, *, texts: Sequence[str] = (), gap: int = 8, scale: float = 1.0, left: np.ndarray | None = None, right: np.ndarray | None = None) -> np.ndarray`

Left | right side by side as one BGR image, separated by `gap` pixels, scaled by
`scale`, with `texts` drawn line by line at the top left. `left`/`right`, when
given, are drawn instead of the frame's images.

### `run_viewer(source: Any, *, headless: bool = False, max_frames: int | None = None, out_dir: str | Path | None = None, key_feed: Iterable[int] | None = None, window: str = "Weevia S1 Viewer", scale: float = 1.0, state: ViewerState | None = None, view: View | None = None) -> dict[str, Any]`

The loop: read → view → compose → show → key. `source` is a `Device` or
`SyntheticStereoSource`.

- `headless=True` runs without a window; `key_feed` then supplies the key codes,
  one per frame, and the loop ends when it runs out.
- `max_frames` stops after that many frames. `out_dir` is where `s` saves
  (default `captures`).
- `view(frame, state) -> (left, right, extra_text_lines)` changes only what is
  drawn; `s` always saves the frame as received.

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
                                        [--eye-size EYE_SIZE]
                                        [--device DEVICE]
                                        [--backend {any,dshow,msmf}]
                                        [--layout {sbs,tb,plane_pack}]
                                        [--lr-order {first_is_left,first_is_right}]
                                        [--width WIDTH] [--height HEIGHT]
                                        [--fps FPS] [--fourcc FOURCC]
                                        [--out OUT] [--scale SCALE]
                                        [--headless] [--frames FRAMES]
```

| Option | Default | Meaning |
|---|---|---|
| `--synthetic` | off | use `SyntheticStereoSource`, no camera |
| `--eye-size WxH` | `320x200` | synthetic eye size |
| `--device DEVICE` | none | camera index, or a path. Without it the Weevia S1 is found by name (`DeviceConfig.weevia_s1()`) |
| `--backend {any,dshow,msmf}` | `dshow` | OpenCV backend; used only with `--device` |
| `--layout {sbs,tb,plane_pack}` | none | required with `--device` |
| `--lr-order {first_is_left,first_is_right}` | none | required with `--device` |
| `--width`, `--height`, `--fps`, `--fourcc` | none | requested mode; override the Weevia S1 settings when given. `--fps` also sets the synthetic rate (default 90) |
| `--out OUT` | `captures` | folder for the `s` key |
| `--scale SCALE` | `1.0` | display scale |
| `--headless` | off | no window |
| `--frames FRAMES` | none | read this many frames, then exit |

Keys: `q`/`Esc` quit, `s` save left/right PNG, `l`/`L` LED −/+, `e`/`E`
exposure −/+, `g`/`G` gain −/+, `a` auto-exposure on/off, `h` key help.

Exit code 0 on success; 2 when the camera cannot be opened or a frame read fails
(one line on stderr). `--device` without `--layout` and `--lr-order` exits with a
message.

```
python -m opticmix_vision.viewer
python -m opticmix_vision.viewer --synthetic
python -m opticmix_vision.viewer --device 1 --layout sbs --lr-order first_is_right --width 1600 --height 800 --fps 90 --fourcc MJPG
```

---

## `opticmix_vision.explorer`

A window (Tk) to see whether the camera works and to change its controls. Only the
command and `main` are public.

### `main(argv=None) -> int`

Runs the window with the arguments below. Returns the exit code.

### Command line: `python -m opticmix_vision.explorer`

```
usage: opticmix-explorer [-h] [--synthetic]

options:
  -h, --help   show this help message and exit
  --synthetic  run on generated frames, with no camera attached
```
