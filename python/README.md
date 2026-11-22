# opticmix-vision

Python SDK for the **Weevia S1** stereo infrared camera — a close-range stereo IR
camera with its own 850 nm (vendor spec) flood illuminator, presented to the host as a standard
UVC device.

No vendor driver, no GPU runtime. Two Python dependencies: `numpy` and `opencv-python`.
Camera controls and device listing run in a compiled extension that ships inside
the wheel, so there is nothing to build.

Download the wheel from the
[latest release](https://github.com/Opticmix/opticmix-vision/releases/latest) and install it:

```bash
pip install opticmix_vision-0.0.3-cp310-abi3-win_amd64.whl
```

Windows x64, Python 3.10 or newer: an abi3 wheel for Python 3.10 and newer;
installed and tested on 3.10, 3.12 and 3.14 (no camera). There is
no package-index release yet. On Windows, verified on Windows 11 only. There is
no wheel for macOS, and macOS has not been run.

### Linux (experimental)

**Experimental — not a supported platform.** The release also carries a Linux
x86_64 wheel, `opticmix_vision-0.0.3-cp310-abi3-linux_x86_64.whl`.

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

Not verified on Linux:

- camera controls and the illuminator — not verified and not supported;
- finding the camera by name (`DeviceConfig.weevia_s1()` without `device=`);
- the viewer, the explorer and the examples;
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
Override any field, e.g. `DeviceConfig.weevia_s1(fps=60)`. Finding the camera by name: measured with the earlier Python implementation; the Rust implementation in 0.0.3 has not yet been verified against the camera.

`examples/quickstart.py` runs this end to end; `python -m opticmix_vision.viewer`
and `python -m opticmix_vision.explorer` open the same camera in a window.

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
Measured with the earlier Python implementation; the Rust implementation in 0.0.3 has not yet been verified against the camera.

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

The table was measured with the earlier Python implementation; the Rust implementation in 0.0.3 has not
yet been verified against the camera.

**Writing is risky on this camera.** On the units we have tested, a control write has occasionally been
followed by one eye's stream stopping until the USB cable was re-inserted. Every write goes through one guard: equal values are skipped, requests inside
the interval collapse to the latest value, and writes are spaced at least 12 ms apart (the camera
vendor's recommended minimum between exposure commands; used for every control). The explorer's sliders
write once when released and the viewer's keys once you stop pressing, so a drag is one write, not dozens.
The guard does not make writes safe. **Write once at startup.** Right after an exposure change the two eyes can
disagree in brightness for about 90 ms (measured on one unit) — drop those frames before stereo matching or
training. Writing exposure while
`auto_exposure` is `auto` raises `ControlStateError`; give both in one `set_many`. Measured with the earlier Python implementation; the Rust implementation in 0.0.3 has not yet been verified against the camera.

A `written` result only means the driver accepted the value. Every write is read back:
`result.readback` is what the camera reports afterwards and `result.kept` says whether it matches — on
this camera a gain of 8 was accepted and read back as 0 while auto exposure was on. Measured with the earlier Python implementation; the Rust implementation in 0.0.3 has not yet been verified against the camera.

`plan_reset()` lists what differs and writes nothing; `reset_to_defaults()` writes those values — not yet
run against the camera. It leaves auto/manual modes alone. `snapshot()`/`restore()` undo your own changes.
The old OpenCV-style `cam.set_prop("CAP_PROP_EXPOSURE", -5)` goes through the same guard. It takes whole
numbers for integer controls: `set_prop("CAP_PROP_GAIN", 28.5)` raises `ValueError`.

On Windows the SDK talks to DirectShow directly (no extra dependency), and it reads controls while OpenCV
streams. If that fails, it falls back to OpenCV, which has no power line frequency, no
low-light compensation and no ranges — `cam.controls.backend_note` says so. The property page opened by
AMCap or `CAP_PROP_SETTINGS` writes outside the guard. Measured with the earlier Python implementation; the Rust implementation in 0.0.3 has not yet been verified against the camera.

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
The figures in this paragraph: measured with the earlier Python implementation; the Rust implementation in 0.0.3 has not yet been verified against the camera.

A readback only means the driver accepted the number. Check the image — and note
that frame mean is a poor check here, because the illuminator lights part of a
185° (vendor spec) field: on one unit a lit patch went from 62 to 238 DN while the
frame mean moved 1.7 DN.

Illuminance, working distance and beam coverage have **not** been measured, so
this package publishes no such figures.

## Without a camera

`opticmix_vision.synthetic` drives the viewer and the explorer with generated
frames (`python -m opticmix_vision.explorer --synthetic`), so the capture path
runs with nothing attached. It is a generator, not measured data. The examples
themselves need a real camera.

## When something goes wrong

- **One eye goes black or stops moving.** On the units we have tested, one eye's
  stream has occasionally stopped; re-inserting the USB cable brought it back.
  `read()` raises `DeviceError` saying which eye once it has sent the byte-identical
  image 90 frames in a row. Turn the check off with
  `DeviceConfig.weevia_s1(stall_frames=0)`.
- **The frame rate is far below 90.** Check that the mode is MJPG (`cam.mode`).
- **"Weevia S1 not found".** The message lists the cameras Windows reported.
  Re-insert the cable; if the camera is missing from the list, Windows does not
  see it either.
- **A setting did not stick.** Look at `result.kept` — see Camera controls.

## What is in this package, and what is not

Run against the camera: capture, stereo split, the illuminator, and camera
controls — all fourteen read through the native path on one unit (with ranges
and defaults, while streaming). Native writes have been checked on one unit for power line
frequency and the auto-exposure mode; writes of exposure, gain and the
illuminator were checked earlier through OpenCV. Measured with the earlier Python implementation; the Rust implementation in 0.0.3 has not yet been verified against the camera.

**Not in this package:** calibration, rectification, depth and hand tracking.
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

Apache-2.0. See [LICENSE](LICENSE). The compiled extension also contains Rust
crates under MIT / Apache-2.0; their notices are in
[THIRD_PARTY_NOTICES.txt](THIRD_PARTY_NOTICES.txt).
