# opticmix-vision

Python SDK for the **Weevia S1** stereo infrared camera — a close-range stereo IR
camera with its own 850 nm (vendor spec) flood illuminator, presented to the host as a standard
UVC device.

No vendor driver, no GPU runtime. Two Python dependencies: `numpy` and `opencv-python`.
Camera controls and device listing run in a compiled extension that ships inside
the wheel, so there is nothing to build.

Install it from PyPI:

```bash
pip install opticmix-vision
```

On Windows x64 this installs a wheel — one abi3 wheel for Python 3.10 and newer;
installed and tested on 3.10, 3.12 and 3.14 (no camera). The same file is
attached to the GitHub release [v0.0.7](https://github.com/Opticmix/opticmix-vision/releases/tag/v0.0.7).
On Windows, verified on Windows 11 only. On Linux x86_64 it installs a
manylinux wheel — experimental, see Linux (experimental) below.
There is no wheel for macOS, and macOS has not been run. Elsewhere pip tries to
build from source: Rust 1.88 or newer (on Windows also the Microsoft C++ Build
Tools). A source install has been checked on Linux x86_64 only (Debian 12
container, Python 3.11, Rust 1.88.0, no camera).

### Linux (experimental)

**Experimental — not a supported platform.** From 0.0.4, PyPI also has a Linux
x86_64 wheel (`manylinux2014_x86_64`: glibc 2.17 or newer, Python 3.10 or newer),
so `pip install opticmix-vision` installs on Linux x86_64 without compiling
anything. The 0.0.6 manylinux wheel has been run with the camera once (one PC,
one camera, 2026-11-25): open, frames, recording and playback, read-only.

On a minimal system (a server, a slim container image) OpenCV needs `libGL.so.1`
from the system: on Debian/Ubuntu, `sudo apt install libgl1 libglib2.0-0`. Without it
`import opticmix_vision` still works, but everything that uses OpenCV, opening
the camera included, fails with `libGL.so.1: cannot open shared object file`.
In our container tests, installing `libgl1` and `libglib2.0-0` removed those
failures.

What has been run: on 2026-11-22, on an Ubuntu 26.04 (kernel 7.0) mini PC with
one Weevia S1 (firmware version not recorded) connected directly over USB
SuperSpeed, a candidate build of 0.0.3 from the same source negotiated
1600×800 MJPG at 90 fps. Over 20 seconds both
eyes changed on every frame: about 85.6 unique frames per second by our
measurement script, 88.95 per second by the kernel's buffer count, with no
kernel USB errors. The measurement opened the camera with
`DeviceConfig(layout="sbs", lr_order="first_is_right", backend="v4l2", device=0,
width=1600, height=800, fps=90, fourcc="MJPG", stall_frames=0)`
(`device` is the N of `/dev/videoN`; `stall_frames=0` because the script counted
repeated images itself).

From 0.0.4, `DeviceConfig.weevia_s1()` also finds the camera by name on Linux:
it picks the `/dev/videoN` capture node whose name contains `WN Dual Camera` in
`/sys/class/video4linux` (the node whose `index` is 0) and uses
`backend="v4l2"`. As on Windows, it stops when two match. Linux name lookup:
run with the camera once (one PC, one camera, 2026-11-25). To name the node yourself —
`DeviceConfig.weevia_s1(device="/dev/video0", backend="v4l2")`, with the
Weevia's own `/dev/videoN` (on a laptop `/dev/video0` may be the built-in
webcam).

From 0.0.7, `devices.list_devices()` lists the cameras on Linux too
(`backend="v4l2"`, the default there): one entry per video capture node, with the
N of `/dev/videoN` as its index, the USB device's sysfs name as its USB location
(e.g. `2-1`) and the USB IDs and link speed read from sysfs, as the doctor reads
them; `python examples/list_devices.py --verbose` prints them. It opens no device.
Run without a camera only (made-up sysfs trees); not run with the camera on Linux.

Not verified on Linux:

- camera controls and the illuminator — not verified and not supported;
- reading control ranges and defaults from the V4L2 driver (`Controls.describe()`, `Controls.driver_names`,
  from 0.0.7; the defaults are shown, not written) — run with the camera on Linux once (one PC, one camera,
  2026-11-25, from source before release): the driver answered on the read-only handle, also while streaming;
- finding the camera by name (`DeviceConfig.weevia_s1()` without `device=`) —
  implemented in 0.0.4, run with the camera once (one PC, one camera, 2026-11-25);
- listing the cameras with their USB IDs and link speed (`devices.list_devices()`,
  from 0.0.7) — not run with the camera on Linux;
- the viewer, the explorer and the other examples — of the examples, `measure_fps.py`, `record_video.py`,
  `play_recording.py`, `save_stereo_pairs.py`, `list_controls.py` and `open_by_usb_location.py` have been run
  once with one camera (2026-11-25), and `export_recording.py` with a camera recording once, from source
  before release;
- other distributions and more than one camera.

## Quickstart

```python
from opticmix_vision import Device, DeviceConfig

with Device(DeviceConfig.weevia_s1()) as cam:
    print(cam.mode.to_dict())   # what the driver actually negotiated
    frame = cam.read()          # raises on failure, never returns an empty frame
    left, right = frame.left, frame.right   # each (800, 800) uint8
```

`DeviceConfig.weevia_s1()` finds the camera **by name** and applies the settings
measured on it: both eyes side by side, the first half is the **right** eye
(measured on one unit, 2026-09-16), 1600×800 MJPG at 90 fps. It does not pick a camera by index — plugging in any
other camera shifts the order, and a laptop webcam is often listed first.
Override any field, e.g. `DeviceConfig.weevia_s1(fps=60)`. Finding the camera by name: measured with the earlier Python implementation; the Rust implementation (since 0.0.3) has not yet been verified against the camera. On Linux it reads `/sys/class/video4linux` instead (run with the camera once, one PC, one camera, 2026-11-25).

Each frame carries `host_ns`, the host clock when it was read. For the capture driver's own time stamp
(`frame.driver_ns`), open with `DeviceConfig.weevia_s1(backend="msmf")` on Windows — the camera is found by
name in the Media Foundation list, and its controls are then the OpenCV fallback (no ranges or defaults) — or
use V4L2 on Linux; DirectShow, the Windows default, gives none. `python examples/measure_fps.py --backend msmf`
prints both. On one PC (Windows 11) with one camera (2026-11-24, once) the first Media Foundation time was about 364,400 s — not the start of streaming; on this PC it was within a few seconds of Python's `time.perf_counter_ns()` (compared across two runs by file times, not within one process), but what the clock's zero is has not been established. Use differences between frames, not the absolute value. Through MSMF, OpenCV asks Media Foundation for RGB, and the time is that converted sample's; run with the camera once (one PC, one camera, 2026-11-24): through MSMF 276 of 900 frames per eye repeated the previous image while the driver-time intervals were 11.11 ms (mean, min and max) — `driver_ns` does not show whether a new image arrived; cause not established. DirectShow: 0 of 900 and 0 of 450; Rust (Media Foundation, no conversion): 0 of 450, driver-time intervals 0.01 to 16.90 ms (mean 11.23). See `StereoFrame` in `API.md`.
V4L2: not run with the camera.

`examples/quickstart.py` runs this end to end; `python -m opticmix_vision.viewer`
and `python -m opticmix_vision.explorer` open the same camera in a window.
Step-by-step: the [tutorials](https://github.com/Opticmix/opticmix-vision/blob/v0.0.7/python/tutorials/README.md)
(the `tutorials/` folder next to this README).

In the viewer, `s` saves a left/right PNG pair and `v` starts and stops a
recording (the files `examples/record_video.py` writes, in `--out`, default
`captures`); `h` lists the other keys. A key held down saves, records or toggles
once: the same key again within 0.6 s is ignored (the window says so once per
press), so press it again after a pause. A keyboard repeat delay set longer than
0.6 s in the OS lets one repeat through.
Each save and recording gets a `.json` next to it with the negotiated mode, the
camera's layout and left/right order, device name, installed calibration files,
every control value and the SDK version (`opticmix_vision.recording.capture_info`); a recording's `.json` also lists the
LED, exposure and gain changes made while recording and the values at the end. A
compressed (MJPG, the default) recording does not have the camera's pixel values.
With `--lossless` the viewer records FFV1 `.mkv`: the lossless file keeps the pixel values the SDK delivered (generated frames only; not run with the camera).
With the opencv-python 4.12 and 4.13 wheels FFV1 writing failed now and then (one Windows PC, generated
frames; not with 4.14 or 5.1): see `opticmix_vision.recording` in `API.md`.
Lossless takes longer per frame to write than MJPG, so it may not keep up with
90 fps; the REC line and the `.json` count the frames missed while recording.
The viewer's `v` runs alongside the preview and may not keep up with 90 fps even
with MJPG: to record every frame, use `examples/record_video.py` first (it records
with no window), then a lower `--fps` or `examples/capture_dataset_npz.py`.
A thread encodes while the window draws; a frame that finds its queue full is not
recorded, and the REC line, the `.json` (`dropped_frames`) and the message when
recording stops count it; `estimated_missed_frames` includes `dropped_frames` — do
not add them. A queue does not make lossless keep up with 90 fps: the frames it
cannot take are dropped and counted. Stopping writes the frames still queued first. The
`.json` says where its control values come from and when the viewer read them
(`controls_source`, `controls_read_at`). Run on generated frames only; not run with
the camera.
Recording with `v` has been run with the camera once (one PC, one camera, 2026-11-24): one
recording started and stopped with `v` (392 frames, about 130 missed while recording; 0.0.5,
before the writing thread), no stall
error; its `.json` carries `controls_note`. Lossless recording and the `s` key's `.json`:
generated frames only; not run with the camera. The Rust crate records and plays the same MJPG
`.avi` files; lossless recording is Python only. Rust recording and playback have been run with the camera once (one PC, one camera, 2026-11-25): `record_video` 10 s, 881 frames, estimated missed 5, dropped by the write queue 0; Rust and Python played the same file back as 881 frames.

The viewer's top line shows the frame index, frames per second over the last 30
frames, and how many frames came late or are estimated missing:
`Device.actual_fps`, `Device.late_frames` (an interval over 1.5 frame periods) and
`Device.estimated_missed_frames`. They are estimated from host times (`host_ns`),
not read from a device frame counter, so a late frame can be delivery jitter rather
than a lost frame. Run on generated frames only; not run with the camera.
The `LED …  exp …  gain …` line shows the values the viewer knows, and ends with
`(as opened + viewer's writes)`: every control is read from the camera once when the viewer opens, then changed only by the viewer's
own keys and presets (the value read back after each write). The `.json` of `s` and
`v` has the same values. A control whose write was refused is read once again, and
`n`, `p` and `P` read every control; a change made by another program shows when the
viewer is opened again.

The next line shows each eye's share of saturated pixels (value 255, or
`--saturation-level N` and above), for setting the LED and exposure. `n` saves every
control as a new preset (`--preset-name`, default `viewer_<time>`; an existing one
is not replaced), `p` steps through the presets saved from this camera's model,
then `defaults` (the camera's own defaults, as the explorer's Reset to defaults and
`presets load defaults`), and shows what loading one would change without writing,
and `P` writes it in one `set_many()` (`reset_to_defaults()` for the defaults);
`--preset NAME` loads one before the first frame. `c` shows the installed
calibration file — serial, file, each eye's size, fx fy cx cy and distortion, and the
baseline, t and rotation between the eyes — read from the file, not the camera
(`c` again hides it). A preset that does
not fit the camera is refused with the reason and nothing written; each write's
result (written, refused, or a value the camera did not keep) is shown and logged,
and a load made while recording goes into the recording's `.json`. Presets, the
defaults and `c`: Run on generated frames only; not run with the camera.

For another camera, build `DeviceConfig(layout=..., lr_order=..., device=...)`
yourself (`device` is then an index; plugging in another camera changes it). `layout` and `lr_order` then have no defaults — the SDK will not guess
how two eyes are packed into one frame, because guessing wrong silently mirrors
everything downstream. To check the order, stand behind the camera, looking where
it looks, cover the lens on your left with a finger, and see which image goes dark.

### Capture modes

The camera reports these itself:

| Format | Resolutions | fps |
|---|---|---|
| MJPG | 2560×800, 1600×800, 1280×400, 800×400 | 30 – 90 |
| YUY2 | same four | 10 – 45 |

At 1600×800 each eye is 800×800. The driver may not give you the mode you asked
for — read `cam.mode` rather than assuming. Request the fourcc: without
`fourcc="MJPG"` taking effect, the stream has run at 9.3 fps (measured 2026-09-13, one unit).

### Stereo packing

| `layout` | frame shape | split |
|---|---|---|
| `sbs` | (H, 2W) | left half / right half |
| `tb` | (2H, W) | top half / bottom half |
| `plane_pack` | (H, W, 2) raw, needs `CAP_PROP_CONVERT_RGB=0` | channel 0 / channel 1 |

`lr_order` is `first_is_left` or `first_is_right`.

## Camera controls

`cam.controls` covers every control the camera advertises — the same set the Windows driver's property
page (and the vendor's AMCap tool) shows. Ranges and defaults come from the device, not from this package.
Measured with the earlier Python implementation; the Rust implementation (since 0.0.3) has not yet been verified against the camera.

```python
cfg = DeviceConfig.weevia_s1()
with Device(cfg) as cam:
    c = cam.controls
    c.describe("gain")          # current, min/max/step, device default, units, verification status
    c.set("gain", 28)           # does not wait — returns WriteResult(status="pending")
    c.set_many({"auto_exposure": "manual", "exposure": -5})   # mode and value in one write
    c.flush()                   # wait for queued writes
```

| control | kind | on this camera |
|---|---|---|
| brightness, contrast, gamma | int | 0–100; image effect not verified |
| sharpness | int | 0–100; image effect not measured |
| gain | int | 0–255. 28 is kept; 16 was kept on one day and read back as 0 on another, and 8 read back as 0 once — cause not known yet |
| backlight_compensation | int | the IR illuminator — see below |
| exposure | int | −13–0 in the driver's units (`log2_s_nominal`); not converted to time, because −3 ran at 35–38 fps where 125 ms would give 8 |
| auto_exposure | `manual`/`auto` | readable here (OpenCV reports −1). Switching back to `auto` once froze one eye for about 1 s; it recovered by itself |
| auto_exposure_priority | bool | only matters in auto. The camera reports 5 (range 5–5) instead of 0/1; writing it is untested |
| power_line_frequency | `disabled`/`50hz`/`60hz` | the device default is `disabled`; the 60 Hz seen at start appears to come from the Windows driver |
| hue, saturation, white_balance, auto_white_balance | — | advertised, but no effect is expected on this mono sensor |

Focus, zoom, pan, tilt and iris are not advertised — the lens is mechanically fixed. The driver still answers
for iris, which is why AMCap shows that row; the SDK does not expose it.

The table was measured with the earlier Python implementation; the Rust implementation (since 0.0.3) has not
yet been verified against the camera.

**Writing is risky on this camera.** On the units we have tested, a control write has occasionally been
followed by one eye's stream stopping until the USB cable was re-inserted. Every write goes through one guard: equal values are skipped, requests inside
the interval collapse to the latest value, and writes are spaced at least 12 ms apart (the camera
vendor's recommended minimum between exposure commands; used for every control). The explorer's sliders
write once when released and the viewer's keys once you stop pressing, so a drag is one write, not dozens.
The guard does not make writes safe. **Write once at startup.** Right after an exposure change the two eyes can
disagree in brightness for about 90 ms (measured on one unit) — drop those frames before stereo matching or
training. Writing exposure while
`auto_exposure` is `auto` raises `ControlStateError`; give both in one `set_many`. Measured with the earlier Python implementation; the Rust implementation (since 0.0.3) has not yet been verified against the camera.

A `written` result only means the driver accepted the value. Every write is read back:
`result.readback` is what the camera reports afterwards and `result.kept` says whether it matches — on
this camera a gain of 8 was accepted and read back as 0 while auto exposure was on. Measured with the earlier Python implementation; the Rust implementation (since 0.0.3) has not yet been verified against the camera.

`plan_reset()` lists what differs and writes nothing; `reset_to_defaults()` writes those values — not yet
run against the camera. It leaves auto/manual modes alone. `snapshot()`/`restore()` undo your own changes.

Presets keep a set of values in a file. `c.save_preset(path)` reads every control (value, units, the
auto/manual modes) into JSON with the camera model and the SDK version; `c.plan_preset(path)` lists what
loading it would change; `c.load_preset(path)` writes what differs in one `set_many()` and returns each
result with `kept`. The whole file is checked first — another model, a control this camera lacks, other
units or a value out of range raise `PresetError` with nothing written. Controls with no effect on the mono
sensor are saved but not written unless `include_mono=True`, as with `plan_reset()`. On the command line,
`python -m opticmix_vision.presets save NAME` / `load NAME` / `list` / `show NAME` use a preset folder beside
the calibration folder; the explorer's Settings window has Save preset… and Load preset…, and the viewer has
the `n` / `p` / `P` keys and `--preset NAME` (below). Run without a
camera only; writing a preset to the camera writes several controls at once — see **Write once at startup**
above.

The old OpenCV-style `cam.set_prop("CAP_PROP_EXPOSURE", -5)` goes through the same guard. It takes whole
numbers for integer controls: `set_prop("CAP_PROP_GAIN", 28.5)` raises `ValueError`.

On Windows the SDK talks to DirectShow directly (no extra dependency), and it reads controls while OpenCV
streams. If that fails, it falls back to OpenCV, which has no power line frequency, no
low-light compensation and no ranges — `cam.controls.backend_note` says so. The property page opened by
AMCap or `CAP_PROP_SETTINGS` writes outside the guard. Measured with the earlier Python implementation; the Rust implementation (since 0.0.3) has not yet been verified against the camera.

## IR illuminator

The illuminator is exposed as the UVC backlight-compensation control.

```python
from opticmix_vision import LedControl

led = LedControl(cam)    # cam is an open Device
led.set(96)              # writes, and returns the driver's readback
```

The ceiling of 96 was seen on two units; the dead zone and the onset were swept
on one. **0–16 is off** — no pixel rose more than 10 DN above the off frame.
Light first appears at **24** and grows to **96**. Values above 96 are ignored; 112, 128 and
255 all read back as 96. So the usable range is 24–96, and the normalized
0.0–1.0 interface takes `value_range=(0, 96)` and rounds to a whole number before writing.
The figures in this paragraph: measured with the earlier Python implementation; the Rust implementation (since 0.0.3) has not yet been verified against the camera.

A readback only means the driver accepted the number. Check the image — and note
that frame mean is a poor check here, because the illuminator lights part of a
185° (vendor spec) field: on one unit a lit patch went from 62 to 238 DN while the
frame mean moved 1.7 DN.

Illuminance, working distance and beam coverage have **not** been measured, so
this package publishes no such figures.

## Without a camera

`opticmix_vision.synthetic` drives the viewer and the explorer with generated
frames (`python -m opticmix_vision.explorer --synthetic`): the window, keys and
saving run with nothing attached; the camera's own capture path does not. It is a
generator, not measured data. The examples
that open a camera, other than `quickstart.py`, take `--synthetic` as well;
`torch_dataloader.py` needs no camera — see [examples/README.md](https://github.com/Opticmix/opticmix-vision/blob/v0.0.7/python/examples/README.md).

A recording written by `examples/record_video.py` or the viewer's `v` key can be read
back the same way as the camera: `Device(DeviceConfig.recording("recording.avi"))` — see
`examples/play_recording.py`. `cam.playback` seeks (by frame or by seconds), plays
at the recorded pace (`real_time`, from the recording's CSV) at any `set_speed`,
loops and pauses; `cam.mode` and `cam.recording_info` give the mode and control
values it was recorded with, and `playback.recorded_time` makes `host_ns` the
recorded time (and `driver_ns`/`driver_clock` the driver time stamp the CSV kept, where the
capture backend gave one — not with DirectShow; run without a camera only, not run with the camera). Watch one in the viewer with
`python -m opticmix_vision.viewer --recording recording.avi`: space pauses, `,`/`.`
step one frame, `[`/`]` jump one second, `-`/`+` change the speed, the bar under the
image seeks, and the window stays on the last frame until `q`. `cam.playback` also
gives the late / missed estimates over the whole recording, and `status`
(`"playing"`, `"paused"`, `"ended"`). `play_recording.py` has read back a 443-frame MJPG recording made with the
camera once (one PC, one camera, 2026-11-24); seeking, speed, pausing and playback in the viewer: generated
recordings only (`record_video.py --synthetic`). Every public name is in
[API.md](https://github.com/Opticmix/opticmix-vision/blob/v0.0.7/python/API.md).

## Calibration

A calibration file belongs to one unit: a fisheye (Kannala-Brandt, `cv2.fisheye`)
model per eye and the left-to-right rotation and translation (mm), in JSON. The
camera does not report a serial number, so you install the file for your camera
once:

```
python -m opticmix_vision.calib add D:/S1-NNNN.json   # install the file for your camera
python -m opticmix_vision.calib list                  # what is installed, and where
```

Downloading a file by the label under the camera is not available yet.

The file is checked before it is installed (format, and that it carries an
S1-NNNN serial) and stored under that serial: `%APPDATA%\opticmix\calib\` on
Windows; on Linux `$XDG_CONFIG_HOME/opticmix/calib/` or `~/.config/opticmix/calib/`
(not run on Linux). After that:

```python
from opticmix_vision import Device, DeviceConfig

with Device(DeviceConfig.weevia_s1()) as cam:
    c = cam.calibration              # StereoCalibration
    print(c.left.K, c.left.D, c.extrinsics.R, c.extrinsics.t)
```

`cam.calibration` uses the installed file when there is exactly one; with more
than one it raises and lists them — keep only the file for the camera you use.
Using two cameras at the same time is not supported; with two connected, open one by
its USB location (see "Two Weevia S1 are connected" under When something goes wrong).
The installed file is still not matched to the camera (no USB serial). The video does not need a
calibration — without one, only `cam.calibration` raises, and its message says
what to do.

`calib show` prints what a file holds, read from the file (the camera is not opened):

```
python -m opticmix_vision.calib show                    # the installed file
python -m opticmix_vision.calib show S1-NNNN            # one of several installed files
python -m opticmix_vision.calib show D:/S1-NNNN.json    # a file, installed or not
python -m opticmix_vision.calib show --json             # the same as JSON (calib.show() in Python)
```

For each eye: the model, the eye size (and the mode it is for), fx, fy, cx, cy and the
distortion k1..k4. Between the eyes (left to right, `p_right = R @ p_left + t`, mm):
the baseline, t, all nine values of the rotation matrix R, its Rodrigues vector in
rad and in degrees, and the rotation angle and axis. Also the file's sha256 (the
value a recording's `.json` lists), its `meta` block, and whether `cam.calibration`
uses it. It says `installed file, not matched to this camera (no USB serial)`: the
camera cannot confirm the file is its own. With no file installed, or several and no
serial named, it says what to do. `calib show` has been run without a camera only
(made-up calibration files); not run with the camera.

**The SDK cannot check that a file belongs to the camera you opened** — the camera
does not send a USB serial number, so another unit's file loads without an error.
`calib check` shows in numbers whether a file fits the connected camera:

```
python -m opticmix_vision.calib check                         # the installed file
python -m opticmix_vision.calib check --file D:/S1-NNNN.json  # a file that is not installed
python -m opticmix_vision.calib check --pairs-dir captures/x  # saved PNG pairs instead of the camera
```

Point the camera at a textured scene in view of both eyes, such as printed text or
a bookshelf. It reads 10 pairs of frames, matches features between the eyes and
prints how far the matches lie from the epipolar lines the file predicts (median,
95th percentile, share within 1 px and 2 px), and the share of points with positive
disparity — near 0 means the file's left and right are swapped against the
camera's. **It shows the numbers only; there is no pass/fail threshold.** With too
few matches it says so and exits with code 2. From Python, `calib.check(cam)`
returns the same numbers as a `CheckResult`; `examples/calibration_check.py` shows
it. If the median is far above the 0.44 px below, the file may be another unit's:
compare its serial with the label on the camera. `calib check` has been run
without a camera only (generated frames and generated PNG pairs); not run with the
camera.

**When more than one file is installed** (or with `--all`, or `--file` given more
than once), `calib check` reads the frames once and prints one row per file —
serial, median, 95th percentile, share within 1 px, share of positive disparity —
and names the file with the smallest median. The smallest is not necessarily this
camera's file: if every median is far above 0.44 px, none of them may be. Keep
only the file whose serial matches the label on the camera
(`calib.compare_pairs` does the same in Python).

Run against the camera (Windows, once each): on one camera, a file that had fit
it in an earlier check, installed with `calib add <file>` and read back through
`cam.calibration`, gave a median epipolar error of 0.44 px (USB 2 link). Its
`left` and `right` are the same eyes as `cam.read().left` and `.right` (on that
camera, 97.9 % of matches had positive disparity; with the eyes swapped, 7.9 %).
On one other camera the same file gave 30.4 px. Both values came from the
calculation `calib check` runs, before the command existed; they tell a file that
fits from one that does not, and are not a precision figure. Installing by label,
Linux, and two cameras on one computer have not been run.

## When something goes wrong

- **Opening an issue?** Paste the output of `python -m opticmix_vision.doctor` into it:
  OS, Python, SDK and OpenCV versions, the video devices, each camera's USB details
  (VID:PID, USB device revision, link speed) and the installed calibration — without
  opening the camera. `python -m opticmix_vision.doctor --open` also opens it and adds
  the mode, 3 s of frame timing, stalled eyes and every control's value and range; it
  writes no control. `doctor --open` has been run with the camera once (one PC, one camera, 2026-11-24); without `--open` the doctor has been run without a camera only (generated frames and fake device lists).
- **One eye goes black or stops moving.** On the units we have tested, one eye's
  stream has occasionally stopped; re-inserting the USB cable brought it back.
  `read()` raises `DeviceError` saying which eye once it has sent the byte-identical
  image 90 frames in a row. Turn the check off with
  `DeviceConfig.weevia_s1(stall_frames=0)`.
- **The frame rate is far below 90.** Check that the mode is MJPG (`cam.mode`).
- **"… frames repeated the previous image in both eyes, read through OpenCV's Media Foundation
  backend".** On one PC with one camera (2026-11-24), 276 of 900 frames per eye read with
  `backend="msmf"` repeated the previous image; repeats were not seen through DirectShow
  (`DeviceConfig.weevia_s1()`'s default on Windows; 0 of 900) or the Rust crate (0 of 450); the cause has not
  been established. `read()` warns once per `open()` when more than 5 % of the last 300 frames do, and
  `cam.repeated_frames` counts them (with any backend).
  `python -m opticmix_vision.doctor --open --backend msmf` reports it as a finding. The count and the
  warning have been run with the camera once through each backend (one PC, one camera, 2026-11-25;
  `examples/measure_fps.py`, whose output does not record the OpenCV version): through `msmf` the warning
  came once and the count was 271 of 900; through DirectShow 0 of 900, no warning. The doctor's
  `--open --backend msmf` once (same PC and camera, OpenCV 4.13.0): its finding said 276 of 904. The doctor's
  other `--backend` values: run without a camera only.
- **"Weevia S1 not found".** The message lists the cameras Windows reported.
  Re-insert the cable; if the camera is missing from the list, Windows does not
  see it either. On Linux the message lists the video nodes it found; you can
  also pass `device="/dev/videoN"` — see Linux (experimental).
- **A setting did not stick.** Look at `result.kept` — see Camera controls.
- **Two Weevia S1 are connected.** Finding the camera by name stops (the camera
  reports no USB serial number); the error begins "Weevia S1 ... not found" and ends
  with the two USB locations — or, where they could not be read, the list
  ['WN Dual Camera', 'WN Dual Camera'] (on Linux, their `/dev/video` paths). Open one
  of them by its USB location: `DeviceConfig.weevia_s1(usb_location="...")`, with a
  location `python examples/list_devices.py` prints (`examples/open_by_usb_location.py`;
  on Linux the USB device's sysfs name, e.g. `1-4.2`, which the doctor prints) — or
  connect one at a time. The viewer, the explorer (Windows), `doctor --open`, `presets save` and `load` and
  `calib check` take it as `--usb-location LOCATION` (the controls they read and write are that camera's; run without a camera only).
  A USB location is not a serial number: it names the port the camera is plugged into, and it changes when the camera is plugged into another port. Using two cameras at the same time is not supported. Picking a camera by USB location has been run with the camera once (one PC, one camera, 2026-11-25): with one camera connected, the list showed its USB location, and given that location it opened at 1600x800 MJPG and read one frame; a location no device has was refused (Python DirectShow, Rust Media Foundation); not run with two cameras.
- **An index opens another camera with `backend="msmf"`.** OpenCV's Media Foundation
  backend (`CAP_MSMF`) counts indexes in Media Foundation's device list and DirectShow
  (`CAP_DSHOW`) in its own. The two lists can be in a different order: on one PC with one camera (2026-11-24), `devices.list_devices(backend="msmf")` and `list_devices()` returned the same three cameras in a different order, once.
  `python examples/list_devices.py --backend msmf` prints the Media Foundation list, and
  `python -m opticmix_vision.doctor` prints both and a line when the Weevia S1's index
  differs. `DeviceConfig.weevia_s1(backend="msmf")` finds the camera by name in the
  Media Foundation list. `examples/list_devices.py --backend msmf` and the doctor's Media Foundation list have been run without a camera only (made-up lists); they have not been run with the camera.
- **Which USB port.** Connect to a USB 3 port directly. The figures on this page whose
  USB link was recorded were measured over SuperSpeed; USB 2 is not qualified.
- **pip finds no version, or tries to build.** Python older than 3.10: pip finds no
  version (3.10 or newer is required). Otherwise, when no wheel fits — Windows
  without x64 Python, Linux other than x86_64 with glibc 2.17 or newer — pip tries
  to build from source (see the top of this page).
- **"ControlsUnavailable: opticmix_vision._core (the compiled extension) is not installed".**
  The package was imported without its compiled extension, as from a source checkout; frames can
  still be read, but finding the camera by name on Windows, the camera controls and reading a
  calibration file cannot. Install
  the wheel — `pip install opticmix-vision` — instead of using a checkout on its own.
- **"failed to open the camera: device=… backend=… (unplugged, wrong index, or in use by another
  app?)".** OpenCV could not open the device named in the message; the SDK cannot tell which of the
  three it was. Check the cable, the `device` you passed, and any other program that may have the
  camera open — close it and try again.
- **"frame read failed (timeout or camera unplugged)".** `read()` raises this when OpenCV returns
  no image. Check that the camera is still connected, then open it again.
- **"ControlStateError: auto_exposure is on — set it to 'manual' in the same call" or "exposure
  cannot be set in the same call that turns auto_exposure on".** An exposure value is refused while
  `auto_exposure` is `auto`, or in the same call that switches it to `auto`. Give the mode and the
  value in one call: `set_many({"auto_exposure": "manual", "exposure": -5})` — see Camera controls.
- **"… is not a camera control — set resolution, fps and format in DeviceConfig", or "… must not be
  set through extra_props".** Resolution, frame rate and format are set when the camera opens;
  `set_prop()` and `extra_props` refuse them. Pass them to the config instead, e.g.
  `DeviceConfig.weevia_s1(fps=60)`, and read `cam.mode` for what the driver gave.
- **Left and right look swapped.** `lr_order` decides which half of the frame is the left eye;
  `DeviceConfig.weevia_s1()` uses `first_is_right`, measured on one unit. Check the order with the
  finger test under Quickstart and pass the matching `lr_order` (`DeviceConfig.weevia_s1(lr_order=...)`
  takes it too).
- **"value_range not set — the normalized (0..1) API needs a range".** `LedControl.set_level()` and
  `get_level()` need the control's range, and `LedControl` has none unless you give it. Create it as
  `LedControl(cam, value_range=(0, 96))`, or use `set()`/`get()` with raw values — see IR illuminator.
- **"No calibration installed" or "N calibrations installed (…)".** `cam.calibration` uses the
  installed file only when there is exactly one. Install the file for your camera with
  `python -m opticmix_vision.calib add <file.json>`, or delete the other files from the folder the
  message names; `python -m opticmix_vision.calib check --all` shows which of them fits the connected
  camera, and `python -m opticmix_vision.calib show S1-NNNN` what one of them holds — see Calibration.
- **"This calibration is for 800x800 eyes. Open the 1600x800 mode."** `cam.calibration` compares
  the eye size in the file with the mode the camera was opened in (half the frame width, the full
  height). Open the mode the message names; `DeviceConfig.weevia_s1()` opens 1600×800 unless you
  override `width` or `height`.
- **"libGL.so.1: cannot open shared object file" (Linux).** OpenCV needs `libGL.so.1` from the
  system; without it `import opticmix_vision` still works, but everything that uses OpenCV, opening
  the camera included, fails. On Debian/Ubuntu, `sudo apt install libgl1 libglib2.0-0` — see
  Linux (experimental).

## Getting help

- Bugs and questions about the SDK: open an issue at
  https://github.com/Opticmix/opticmix-vision/issues and paste the output of
  `python -m opticmix_vision.doctor` (add `--open` if the camera opens) — see
  When something goes wrong.
- Questions, ideas and show-and-tell: GitHub Discussions,
  https://github.com/Opticmix/opticmix-vision/discussions
- Anything else, or if you cannot post publicly: contact@opticmix.kr

We reply to issues and email within 2 business days (Korea time).

We aim to release about once a month; what changed in each release is in
[CHANGELOG.md](https://github.com/Opticmix/opticmix-vision/blob/v0.0.7/python/CHANGELOG.md).

## What is in this package, and what is not

Run against the camera: capture, stereo split, the illuminator, and camera
controls — all fourteen read through the native path on one unit (with ranges
and defaults, while streaming). Native writes have been checked on one unit for power line
frequency and the auto-exposure mode; writes of exposure, gain and the
illuminator were checked earlier through OpenCV. Measured with the earlier Python implementation; the Rust implementation (since 0.0.3) has not yet been verified against the camera.
Calibration files (install and read): run on two cameras on Windows, once each —
see Calibration. The calibration check (`calib check`): run without a camera only
(generated frames); not run with the camera. `calib show`: run without a camera
only (made-up calibration files); not run with the camera.

**Not in this package:** rectification, depth and hand tracking.
The code for them exists but has not been validated for release, so it is
withheld rather than shipped with a warning label. Each returns when it has been.

## Tests

From a checkout of the repository, in this folder (`python/`):

```bash
pip install -e ".[dev]"     # compiles the extension: needs Rust 1.88 or newer (and the MSVC build tools on Windows)
python -m pytest -q
```

Hardware tests are skipped unless `OPTICMIX_HW=1` and a camera is attached.

### Working from a checkout

Camera controls and device listing run in a compiled extension,
`opticmix_vision._core`, built from `../rust` with maturin. `pip install -e .`
compiles it. If this source tree is on the path without having been installed,
`import opticmix_vision` and frame capture still work, but camera controls and
device listing raise `ControlsUnavailable` with a message naming the missing
extension. Install the wheel into that environment as well.

## License

Apache-2.0. See [LICENSE](https://github.com/Opticmix/opticmix-vision/blob/v0.0.7/python/LICENSE). The compiled extension also contains Rust
crates under MIT / Apache-2.0; their notices are in
[THIRD_PARTY_NOTICES.txt](https://github.com/Opticmix/opticmix-vision/blob/v0.0.7/python/THIRD_PARTY_NOTICES.txt).
