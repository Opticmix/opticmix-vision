# 08 · Calibration file

Examples: [`calibration_info.py`](../examples/calibration_info.py),
[`calibration_check.py`](../examples/calibration_check.py)

## What you will do

Install the calibration file for your camera, and print what it holds: `K`, `D` and the eye size for each
eye, the rotation `R` (with its Rodrigues vector and angle) and translation `t` from the left eye to the right
eye, and the baseline in mm. Then check in numbers whether the file fits the camera.

## Before you start

- Calibration files are a **preview**. A calibration file belongs to one unit: a fisheye (Kannala-Brandt,
  `cv2.fisheye`) model per eye and the left-to-right rotation and translation (mm), in JSON.
- **The SDK cannot check that a file belongs to the camera you opened** — the camera does not send a USB
  serial number, so another unit's file loads without an error. Install only the file for your camera.
- Downloading a file by the label under the camera is not available yet.
- `calibration_info.py` does not open the camera and writes nothing to it. It was added in 0.0.4 and
  has been run without a camera only.
- `calibration_check.py` opens the camera (or reads saved pairs) and writes nothing to it. Run without a
  camera only (generated frames); not run with the camera.
- `python -m opticmix_vision.calib show` reads the file only and does not open the camera. Run without a
  camera only (made-up calibration files); not run with the camera.
- Run the commands from the `python/` folder of the repository — see the [tutorials README](README.md) for
  which version to check out and which `python` to use.

## Run it

With your calibration file:

```bash
python -m opticmix_vision.calib add D:/S1-NNNN.json   # install the file for your camera
python -m opticmix_vision.calib list                  # what is installed, and where
python -m opticmix_vision.calib show                  # what the installed file holds (--json for a program)
python examples/calibration_info.py
python examples/calibration_check.py                  # point the camera at a textured scene in view of both eyes
python examples/calibration_check.py --pairs-dir captures   # saved PNG pairs instead of the camera
```

Without a calibration file (a made-up calibration in a temporary folder; your calibration folder is not
touched):

```bash
python examples/calibration_info.py --synthetic
python examples/calibration_check.py --synthetic
python -m opticmix_vision.calib show --synthetic
```

### Rust

The Rust crate reads the installed file by the same rules — the same folder, exactly one file, the same messages —
with `calib::resolve()` and `Device::calibration()`, and has the same `calibration_info` example. It reads files
only: install the file with the Python command above. In a checkout of the repository, from
`rust/opticmix-vision`, without a calibration file (a made-up one in a temporary folder; your calibration folder is
not touched):

```bash
cargo run --example calibration_info -- --synthetic
```

```text
$ cargo run --example calibration_info -- --synthetic
synthetic: made-up values, not a camera
calibration folder: <temporary folder>\opticmix\calib
file: <temporary folder>\opticmix\calib\S1-0000.json
      installed file, not matched to this camera (no USB serial)
device: {'model': 'synthetic', 'serial': 'S1-0000'}
left: model KB4, eye size 800x800
  K =
  [ 254.6479    0.0000  399.5000]
  [   0.0000  254.6479  399.5000]
  [   0.0000    0.0000    1.0000]
  D (k1..k4) = [0.0000 0.0000 0.0000 0.0000]
  optical axis -> pixel [399.5000 399.5000]
right: model KB4, eye size 800x800
  K =
  [ 254.6479    0.0000  399.5000]
  [   0.0000  254.6479  399.5000]
  [   0.0000    0.0000    1.0000]
  D (k1..k4) = [0.0000 0.0000 0.0000 0.0000]
  optical axis -> pixel [399.5000 399.5000]
R (left -> right) =
  [   1.0000    0.0000    0.0000]
  [   0.0000    1.0000    0.0000]
  [   0.0000    0.0000    1.0000]
t (mm) = [-50.0000 0.0000 0.0000]
baseline: 50.00 mm
a point in the left eye, and in the right eye by p_right = R @ p_left + t:
  left  [100.0000 -20.0000 400.0000] mm -> pixel [461.8341 387.0332] -> unproject [0.2423 -0.0485 0.9690] (direction [0.2423 -0.0485 0.9690])
  right [50.0000 -20.0000 400.0000] mm -> pixel [431.1407 386.8437] -> unproject [0.1239 -0.0496 0.9911] (direction [0.1239 -0.0496 0.9911])
```

Leave out `-- --synthetic` to read your installed file. In your own code:

```rust
let cam = Device::open(DeviceConfig::weevia_s1())?;
let c = cam.calibration()?; // the one installed file, its eye size checked against cam.mode()
println!("{:?} {:?} {:?} {:?}", c.left.k, c.left.d, c.extrinsics.r, c.extrinsics.t);
let px = c.left.project([100.0, -20.0, 400.0]); // a point in the left eye's coordinates (mm) -> Some([u, v])
let ray = c.left.unproject([320.0, 240.0]); // a left-eye pixel -> Some(unit ray)
```

Points: `Intrinsics::project` (camera coordinates to pixel) and `Intrinsics::unproject` (pixel to unit ray) use
the KB4 model with the file's K and D, up to 92.5° from the optical axis (`None` beyond). The crate has no
rectification and no depth.

The Rust `calibration_info` example, `Device::calibration()`, `project` and `unproject` have been run without a
camera only (made-up calibration files); not run with the camera.

## What you should see

Output with `--synthetic` (a made-up calibration, not a camera). The temporary folder's path is shortened to `<temporary folder>`.

```text
$ python examples/calibration_info.py --synthetic
synthetic: made-up values, not a camera
calibration folder: <temporary folder>\store
device: {'model': 'synthetic', 'serial': 'S1-0000'}
left: model KB4, eye size 800x800
  K =
[[254.6479   0.     399.5   ]
 [  0.     254.6479 399.5   ]
 [  0.       0.       1.    ]]
  D (k1..k4) = [0. 0. 0. 0.]
right: model KB4, eye size 800x800
  K =
[[254.6479   0.     399.5   ]
 [  0.     254.6479 399.5   ]
 [  0.       0.       1.    ]]
  D (k1..k4) = [0. 0. 0. 0.]
R (left -> right) =
[[1. 0. 0.]
 [0. 1. 0.]
 [0. 0. 1.]]
t (mm) = [-50.   0.   0.]
baseline: 50.00 mm

$ python examples/calibration_check.py --synthetic
synthetic: generated frames of a made-up calibration, not a camera

the calibration the frames were made with:
epipolar distance: median 0.11 px, 95th percentile 0.51 px, within 1 px 99%, within 2 px 100% (14455 points in 10 pairs with matches)
positive disparity: 99.9% of points
Reference, measured once on Windows: on one camera a matching file gave a median of 0.44 px; another camera's file gave 30.4 px. This tells a file that fits from one that does not; it is not a precision figure, and there is no pass/fail threshold.
If the median is far above the matching-file value (0.44 px), the file may be another unit's: compare its serial with the label on the camera.

a wrong one (principal point moved 12 px down):
epipolar distance: median 17.23 px, 95th percentile 27.12 px, within 1 px 0%, within 2 px 0% (13833 points in 10 pairs with matches)
positive disparity: 99.9% of points
Reference, measured once on Windows: on one camera a matching file gave a median of 0.44 px; another camera's file gave 30.4 px. This tells a file that fits from one that does not; it is not a precision figure, and there is no pass/fail threshold.
If the median is far above the matching-file value (0.44 px), the file may be another unit's: compare its serial with the label on the camera.

$ python -m opticmix_vision.calib show --synthetic
calibration synthetic (synthetic)
          generated calibration (--synthetic): made-up values, not a file and not a camera
used by Device.calibration: no: generated, not a file
schema:   opticmix_vision.stereo_calibration version 1, units mm
left:     KB4, eye size 800x800 (the 1600x800 mode)
  fx 254.6479  fy 254.6479  cx 399.5000  cy 399.5000
  D (k1..k4)  0  0  0  0
right:    KB4, eye size 800x800 (the 1600x800 mode)
  fx 254.6479  fy 254.6479  cx 399.5000  cy 399.5000
  D (k1..k4)  0  0  0  0
left to right (p_right = R @ p_left + t, mm):
  baseline  64.0000 mm
  t         -64.0000  0.0000  0.0000 mm
  R         +1.000000  +0.000000  +0.000000
            +0.000000  +1.000000  +0.000000
            +0.000000  +0.000000  +1.000000
  rvec      +0.000000  +0.000000  +0.000000 rad = +0.0000  +0.0000  +0.0000 deg
  rotation  0.0000 deg
```

With no file installed, or more than one, `calibration_info.py` prints the SDK's message as is and exits with
code 1. `calib show` says what to do in the same cases: install the file, or name the serial of the one to
show (`calib show S1-NNNN`). With your file it starts with the file's path and the line `installed file, not
matched to this camera (no USB serial)` — the camera cannot confirm the file is its own — then its sha256,
and ends with the file's `meta`.

`calibration_check.py --synthetic` renders a textured plane through a made-up calibration and checks the
frames against that calibration and against a deliberately wrong one, so you see what a file that fits and
one that does not look like.

## How it works

`calibration_info.py` reads the installed file with `calib.resolve()` — the same file `cam.calibration`
returns:

```python
try:
    c = calib.resolve()
except calib.CalibLookupError as exc:
    print(exc, file=sys.stderr)
    return 1
...
for name, eye in (("left", c.left), ("right", c.right)):
    print(f"{name}: model {eye.model}, eye size {eye.size[0]}x{eye.size[1]}")
    print(f"  K =\n{eye.K}")
    print(f"  D (k1..k4) = {eye.D}")
print(f"R (left -> right) =\n{c.extrinsics.R}")
print(f"t (mm) = {c.extrinsics.t}")
print(f"baseline: {c.baseline_mm:.2f} mm")
```

In your own code, the open camera gives you the same object:

```python
with Device(DeviceConfig.weevia_s1()) as cam:
    c = cam.calibration              # StereoCalibration
    print(c.left.K, c.left.D, c.extrinsics.R, c.extrinsics.t)
```

To find where a point appears in an eye, or the ray through a pixel, use OpenCV's fisheye functions with the file's
`K` and `D` — the package has no function of its own for it (the Rust crate's `Intrinsics::project` and
`unproject` compute the same model):

```python
import cv2
import numpy as np
from opticmix_vision import calib

c = calib.resolve()
p = np.array([100.0, -20.0, 400.0])        # a point in the left eye's coordinates: X right, Y down, Z forward, mm
uv, _ = cv2.fisheye.projectPoints(p.reshape(1, 1, 3), np.zeros(3), np.zeros(3), c.left.K, c.left.D)
xy = cv2.fisheye.undistortPoints(uv, c.left.K, c.left.D)   # x/z, y/z of the ray through that pixel
ray = np.append(xy[0, 0], 1.0)
ray /= np.linalg.norm(ray)                 # the unit ray: the direction of p
p_right = c.extrinsics.R @ p + c.extrinsics.t   # the same point in the right eye's coordinates
uv_right, _ = cv2.fisheye.projectPoints(p_right.reshape(1, 1, 3), np.zeros(3), np.zeros(3), c.right.K, c.right.D)
print(uv[0, 0], ray, uv_right[0, 0])
```

`cv2.fisheye.projectPoints` divides by Z: for a point behind the eye (Z < 0) it returns the pixel of -p, the point
on the opposite side of the eye, so use it for points in front (Z > 0). The Rust crate's `Intrinsics::project`
returns the point's own pixel up to 92.5° from the optical axis and `None` beyond. Neither checks the pixel against
the eye size (`size`): a pixel outside the image comes back as it is.

- `calib add` checks the file before it installs it (format, and that it carries an S1-NNNN serial) and
  stores it under that serial: `%APPDATA%\opticmix\calib\` on Windows.
- `cam.calibration` uses the installed file when there is exactly one; with more than one it raises and lists
  them. It also compares the eye size in the file with the mode the camera was opened in.
- Using two cameras at the same time is not supported. With two connected, open one by its USB location
  ([01 · Hello Weevia](01-hello-weevia.md)); the installed file is still not matched to the camera (no USB serial).
- The video does not need a calibration — without one, only `cam.calibration` raises.
- `calibration_check.py` reads 10 pairs, matches features between the eyes and measures how far each match is
  from where the calibration says it must be (the epipolar distance), with `calib.check(cam)` for the camera
  or `calib.check_pairs(calib.load_pairs(folder), calibration)` for saved pairs. It prints numbers only; there
  is no pass/fail threshold. `python -m opticmix_vision.calib check` does the same from the command line.
- `calib show` prints every value of the file: R in full, and the same rotation as a Rodrigues vector
  (`cv2.Rodrigues(R)`, in rad and in degrees) with its angle and axis. `calib show --json`, or `calib.show()` in
  Python, gives the same values as a dict.
- Rectification and depth are not in this package; the calibration is the file and its values.

## If it does not work

See [When something goes wrong](../README.md#when-something-goes-wrong) and
[Calibration](../README.md#calibration) in the package README:

- **"No calibration installed" or "N calibrations installed (…)"** — install the file for your camera with
  `python -m opticmix_vision.calib add <file.json>`, or delete the other files from the folder the message
  names.
- **"This calibration is for 800x800 eyes. Open the 1600x800 mode."** — open the mode the message names;
  `DeviceConfig.weevia_s1()` opens 1600×800 unless you override `width` or `height`.
- **"ControlsUnavailable: opticmix_vision._core (the compiled extension) is not installed"** — reading a
  calibration file needs the compiled extension; install the wheel with the same `python` you run the example with
  (`python -m pip install opticmix-vision`).

## Next

- [Viewer and explorer](tools.md): the two windows that come with the package.
- [API.md](../API.md): every public name of the package.
- [examples/README.md](../examples/README.md): all the examples in one table.
- Questions: [Getting help](../README.md#getting-help) in the package README.
