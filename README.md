# opticmix-vision

Python SDK for the **Weevia S1** stereo infrared camera — a close-range stereo IR
camera with its own 850 nm (vendor spec) flood illuminator, presented to the host
as a standard UVC device.

No vendor driver, no GPU runtime. Two dependencies: `numpy` and `opencv-python`.

```bash
pip install git+https://github.com/Opticmix/opticmix-vision
```

Python 3.10 or newer. There is no package-index release yet.

## Quickstart

```python
from opticmix_vision import Device, DeviceConfig

# layout and lr_order have no defaults — the SDK will not guess how the two
# eyes are packed into one frame, because guessing wrong silently mirrors
# everything downstream.
cfg = DeviceConfig(
    layout="sbs",               # both eyes side by side in one frame
    lr_order="first_is_right",  # measured on the Weevia S1 — confirm it, see below
    device=0, backend="dshow",  # device is the OpenCV index, see below
    width=1600, height=800,     # a request — read cam.mode for what you got
    fps=90, fourcc="MJPG",      # without MJPG the stream has run at 9.3 fps
)

with Device(cfg) as cam:
    print(cam.mode.to_dict())   # what the driver actually negotiated
    frame = cam.read()          # raises on failure, never returns an empty frame
    left, right = frame.left, frame.right   # each (h, w) uint8
```

**Confirm `lr_order` before you trust any stereo result.** On the Weevia S1 the
frame is side by side and the first half is the right eye (measured on one
unit). Hold a hand 20–30 cm in front of the camera and check which half it moves
in. Nothing in the stream declares the order, so check it once on your setup.

`device=0` is the first camera Windows lists. On a PC with a built-in webcam the
Weevia S1 can be at 1 or higher — raise the index and look at the image: the
correct one shows two eyes side by side.

### Capture modes

The camera reports these itself:

| Format | Resolutions | fps |
|---|---|---|
| MJPG | 2560×800, 1600×800, 1280×400, 800×400 | 30 – 90 |
| YUY2 | same four | 10 – 45 |

At 1600×800 each eye is 800×800. The driver may not give you the mode you asked
for — read `cam.mode` rather than assuming.

Request the fourcc: without `fourcc="MJPG"` taking effect, the stream has run at
9.3 fps (measured 2026-09-13, one unit).

### Stereo packing

| `layout` | frame shape | split |
|---|---|---|
| `sbs` | (H, 2W) | left half / right half |
| `tb` | (2H, W) | top half / bottom half |
| `plane_pack` | (H, W, 2) raw, needs `CAP_PROP_CONVERT_RGB=0` | channel 0 / channel 1 |

`lr_order` is `first_is_left` or `first_is_right`.

## Camera controls

Everything goes through standard UVC properties.

```python
# cam is an open Device — inside the with block above
cam.set_prop("CAP_PROP_EXPOSURE", -5)   # driver-scale value, not microseconds
print(cam.get_prop("CAP_PROP_EXPOSURE"))
```

Measured on this camera: `EXPOSURE`, `GAIN` and `BACKLIGHT` reach the image
(exposure and gain on one unit). `SHARPNESS` accepts a value, but its effect on
the image has not been measured. `AUTO_EXPOSURE` reads back `-1` through OpenCV —
a readback artifact, not a missing feature; the camera does have auto exposure.
`FOCUS`, `AUTOFOCUS`, `ZOOM`, `PAN` and `TILT` are not advertised by the camera
and read back `-1`; the lens is mechanically fixed, so there is no focus to
drive. **Read a property back before relying on it** — a gain write has not
always been kept.

On the units we have tested, a control write has occasionally been followed by
one eye's stream stopping until the USB cable was re-inserted. Write once at
startup and check that both halves still change.

## IR illuminator

The illuminator is exposed as the UVC backlight-compensation control.

```python
from opticmix_vision import LedControl

led = LedControl(cam)   # cam must still be open
led.set(96)              # writes, and returns the driver's readback
```

The ceiling of 96 was seen on two units; the dead zone and the onset were swept
on one. **0–16 is off** — no pixel rose more than 10 DN above the off frame.
Light first appears at **24** and grows to **96**. Values above 96 are ignored; 112, 128 and
255 all read back as 96. So the usable range is 24–96, and the normalized
0.0–1.0 interface takes `value_range=(0, 96)`.

A readback only means the driver accepted the number. Check the image — and note
that frame mean is a poor check here, because the illuminator lights part of a
185° (vendor spec) field: on one unit a lit patch went from 62 to 238 DN while
the frame mean moved 1.7 DN.

Illuminance, working distance and beam coverage have **not** been measured, so
this package publishes no such figures.

## Without a camera

`opticmix_vision.synthetic` drives the viewer with generated frames, so the
capture path runs with nothing attached. It is a generator, not measured data.

## What is in this release

Run against hardware: capture, stereo split, camera controls, the illuminator.

**Not in this release:** calibration, rectification, depth and hand tracking.
They are in development and have not been validated for release, so they are
left out of the package rather than shipped with a warning. Each one is added
once it has been validated on a camera.

## Tests

```bash
pip install -e ".[dev]"
python -m pytest -q
```

Hardware tests are skipped unless `OPTICMIX_HW=1` and a camera is attached.

## License

Apache-2.0. See [LICENSE](LICENSE).
