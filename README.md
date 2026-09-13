# opticmix-vision

Python SDK for the **Weave S1** stereo infrared camera — a close-range stereo IR
camera with its own 850 nm flood illuminator, presented to the host as a standard
UVC device.

No vendor driver, no GPU runtime. Two dependencies: `numpy` and `opencv-python`.

```bash
pip install git+https://github.com/Opticmix/opticmix-vision
pip install "opticmix-vision[hands] @ git+https://github.com/Opticmix/opticmix-vision"  # optional inference runtime
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
    lr_order="first_is_left",   # confirm this visually, see below
    device=0, backend="dshow",
    width=1600, height=800,
)

with Device(cfg) as cam:
    print(cam.mode.to_dict())   # what the driver actually negotiated
    frame = cam.read()          # raises on failure, never returns an empty frame
    left, right = frame.left, frame.right   # each (h, w) uint8
```

`examples/quickstart.py` runs this end to end.

**Confirm `lr_order` before you trust any stereo result.** Hold a hand 20–30 cm
in front of the camera and check which half it moves in. Nothing in the stream
declares the order, so this is the only way to know.

### Capture modes

The camera reports these itself:

| Format | Resolutions | fps |
|---|---|---|
| MJPG | 2560×800, 1600×800, 1280×400, 800×400 | 30 – 90 |
| YUY2 | same four | 10 – 45 |

At 1600×800 each eye is 800×800. The driver may not give you the mode you asked
for — read `cam.mode` rather than assuming.

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
cam.set_prop("CAP_PROP_EXPOSURE", -8)   # driver-scale value, not microseconds
print(cam.get_prop("CAP_PROP_EXPOSURE"))
```

Measured on this camera: `EXPOSURE`, `GAIN`, `SHARPNESS` and `BACKLIGHT` reach
the image. `AUTO_EXPOSURE`, `FOCUS`, `AUTOFOCUS`, `ZOOM`, `PAN` and `TILT` all
read back `-1` — not implemented, and writing them does nothing. The lens is
mechanically fixed, so there is no focus to drive. **Read a property back before
relying on it.**

## IR illuminator

The illuminator is exposed as the UVC backlight-compensation control.

```python
from opticmix_vision import LedControl

led = LedControl(cam)
led.set(96)              # writes, and returns the driver's readback
```

Swept on two units: **0–16 is off** — not a single pixel changes. Light first
appears at **24** and grows to **96**. Values above 96 are ignored; 112, 128 and
255 all read back as 96. So the usable range is 24–96, and the normalized
0.0–1.0 interface takes `value_range=(0, 96)`.

A readback only means the driver accepted the number. Check the image — and note
that frame mean is a poor check here, because the illuminator lights part of a
185° field: a region can go from near black to saturated while the average moves
under 2 DN.

Illuminance, working distance and beam coverage have **not** been measured, so
this package publishes no such figures.

## Without a camera

`opticmix_vision.synthetic` drives the viewer with generated frames, so the
capture path runs with nothing attached. It is a generator, not measured data.
The examples themselves need a real camera.

## Verified, and not

Run against hardware: capture, stereo split, camera controls, the illuminator.

**Not run against hardware** — the code exists and imports, but no result from it
has been checked on a real unit, so do not treat its output as trustworthy yet:
`calibration`, `rectify`, `depth` (host-side SGBM; there is no on-board depth
engine), `ros` (`camera_info` YAML).

`hands` ships the data types and the coordinate contract only. **No trained model
is included**, and installing the optional runtime does not give you hand
tracking. The API is published early so integrators can write against a contract
that will not move.

## Coordinates

Right-handed, millimetres. X right, Y up, Z toward the user, origin at the
device. Rotations are quaternions ordered `(x, y, z, w)`.

## Tests

```bash
pip install -e ".[dev]"
python -m pytest -q
```

Hardware tests are skipped unless `OPTICMIX_HW=1` and a camera is attached.

## License

Apache-2.0. See [LICENSE](LICENSE).
