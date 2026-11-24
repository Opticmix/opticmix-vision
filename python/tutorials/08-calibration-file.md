# 08 · Calibration file

Examples: [`calibration_info.py`](../examples/calibration_info.py),
[`calibration_check.py`](../examples/calibration_check.py)

## What you will do

Install the calibration file for your camera, and print what it holds: `K`, `D` and the eye size for each
eye, the rotation `R` and translation `t` from the left eye to the right eye, and the baseline in mm. Then
check in numbers whether the file fits the camera.

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
- Run the commands from the `python/` folder of the repository — see the [tutorials README](README.md) for
  which version to check out and which `python` to use.

## Run it

With your calibration file:

```bash
python -m opticmix_vision.calib add D:/S1-NNNN.json   # install the file for your camera
python -m opticmix_vision.calib list                  # what is installed, and where
python examples/calibration_info.py
python examples/calibration_check.py                  # point the camera at a textured scene in view of both eyes
python examples/calibration_check.py --pairs-dir captures   # saved PNG pairs instead of the camera
```

Without a calibration file (a made-up calibration in a temporary folder; your calibration folder is not
touched):

```bash
python examples/calibration_info.py --synthetic
python examples/calibration_check.py --synthetic
```

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
```

With no file installed, or more than one, `calibration_info.py` prints the SDK's message as is and exits with
code 1.

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

- `calib add` checks the file before it installs it (format, and that it carries an S1-NNNN serial) and
  stores it under that serial: `%APPDATA%\opticmix\calib\` on Windows.
- `cam.calibration` uses the installed file when there is exactly one; with more than one it raises and lists
  them. It also compares the eye size in the file with the mode the camera was opened in.
- Two cameras on one computer are not supported yet.
- The video does not need a calibration — without one, only `cam.calibration` raises.
- `calibration_check.py` reads 10 pairs, matches features between the eyes and measures how far each match is
  from where the calibration says it must be (the epipolar distance), with `calib.check(cam)` for the camera
  or `calib.check_pairs(calib.load_pairs(folder), calibration)` for saved pairs. It prints numbers only; there
  is no pass/fail threshold. `python -m opticmix_vision.calib check` does the same from the command line.
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
