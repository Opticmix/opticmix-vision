# 01 · Hello Weevia

Examples: [`list_devices.py`](../examples/list_devices.py), [`quickstart.py`](../examples/quickstart.py)

## What you will do

List the video devices on your computer, find the Weevia S1 among them by name, open it, print the mode the
driver negotiated, and read frames split into a left and a right eye.

## Before you start

- Install the package: `pip install opticmix-vision` (see the [package README](../README.md) for the platforms
  it installs on; on Linux, which is experimental, the examples have not been run).
- Run the commands from the `python/` folder of the repository — see the [tutorials README](README.md) for
  which version to check out and which `python` to use.
- Connect the Weevia S1 to a USB 3 port directly. No camera? Every command below marked `--synthetic` runs
  without one.

## Run it

With the camera:

```bash
python examples/list_devices.py                  # the video devices Windows reports, in DirectShow's order
python examples/list_devices.py --backend msmf   # the video devices in Media Foundation's order
python examples/quickstart.py                    # open the camera, show both eyes; q quits, s saves a pair
python examples/quickstart.py --no-window        # no window: read 100 frames and print the frame rate
```

Without a camera:

```bash
python examples/list_devices.py --synthetic
python examples/list_devices.py --synthetic --backend msmf
```

`quickstart.py` has no `--synthetic` option. [Tutorial 02](02-live-view-and-frame-rate.md) runs the same read
loop on generated frames.

### Rust

The Rust crate has the same `list_devices` example. In a checkout of the repository, from
`rust/opticmix-vision`:

```bash
cargo run --example list_devices                  # the video devices Windows reports
cargo run --example list_devices -- --synthetic   # no camera: the made-up list below
```

```text
$ cargo run --example list_devices -- --synthetic
3 video device(s):
  [0] WN Dual Camera   <- Weevia S1
  [1] Example Laptop Webcam
  [2] WN Dual Camera   <- Weevia S1
Weevia S1 ("WN Dual Camera") found: 2
More than one Weevia S1: the camera reports no USB serial number, so they cannot be told apart by name, and opening by name stops with MultipleMatches instead of guessing. Using two units on one computer has not been tested — connect one at a time.
```

Of the Rust examples, `list_devices` and `measure_fps` have been run with the camera once (one PC, one camera, 2026-11-24); the others have been run without a camera only (`--synthetic`). See the
crate README, "Examples".

## What you should see

Output with `--synthetic` (generated frames); with the camera the numbers differ.

```text
$ python examples/list_devices.py --synthetic
3 video device(s) in the DirectShow list:
  [0] WN Dual Camera   <- Weevia S1
  [1] Example Laptop Webcam
  [2] WN Dual Camera   <- Weevia S1
Weevia S1 ('WN Dual Camera') found: 2
More than one Weevia S1: the camera reports no USB serial number, so they cannot be told apart by name, and DeviceConfig.weevia_s1() stops with an error instead of guessing. Using two units on one computer has not been tested — connect one at a time.
The Media Foundation list (--backend msmf) can be in a different order: an index goes only with the backend whose list it came from.
$ python examples/list_devices.py --synthetic --backend msmf
3 video device(s) in the Media Foundation list:
  [0] Example Laptop Webcam
  [1] WN Dual Camera   <- Weevia S1
  [2] WN Dual Camera   <- Weevia S1
Weevia S1 ('WN Dual Camera') found: 2
More than one Weevia S1: the camera reports no USB serial number, so they cannot be told apart by name, and DeviceConfig.weevia_s1() stops with an error instead of guessing. Using two units on one computer has not been tested — connect one at a time.
The DirectShow list (--backend dshow) can be in a different order: an index goes only with the backend whose list it came from.
```

The synthetic list is made up on purpose: it has two Weevia S1 and a webcam, so you see what happens when
two units are connected. With your camera the list is the devices Windows reports, and one line should be
marked `<- Weevia S1`.

The two made-up lists hold the same devices in a different order. Windows has two capture APIs, DirectShow
and Media Foundation, and each has its own list of cameras. The two lists can be in a different order: on one PC with one camera (2026-11-24), `devices.list_devices(backend="msmf")` and `list_devices()` returned the same three cameras in a different order, once. The index is the one OpenCV opens
with that backend — `cv2.VideoCapture(index,
cv2.CAP_DSHOW)` or `cv2.CAP_MSMF` — so an index from one list does not go with the other backend.

`quickstart.py` prints `negotiated:` followed by the mode the driver gave, then opens a window with the left
eye on the left and the right eye on the right; the window title shows the eye size and the frame rate.

## How it works

`list_devices.py` reads the device list and marks every entry whose name contains the camera's USB name:

```python
devices = list_devices(backend=a.backend, _raw=SYNTHETIC_LIST if a.synthetic else None)
weevia = [d for d in devices if WEEVIA_S1_NAME.lower() in d.name.lower()]
print(f"{len(devices)} video device(s) in the {LIST_NAME[a.backend]} list:")
for d in devices:
    print(f"  [{d.index}] {d.name}{'   <- Weevia S1' if d in weevia else ''}")
```

`quickstart.py` opens the camera with `DeviceConfig.weevia_s1()` when you give no `--device`, and reads
from it:

```python
cfg = build_config(a)
cam = Device(cfg).open()
...
with cam:
    print("negotiated:", cam.mode.to_dict())
    ...
        f = cam.read()
        ...
        cv2.imshow("opticmix_vision quickstart", np.hstack([f.left, f.right]))
```

- `DeviceConfig.weevia_s1()` finds the camera **by name** and applies the settings measured on it: both eyes
  side by side, the first half is the **right** eye (measured on one unit, 2026-09-16), 1600×800 MJPG at
  90 fps. It does not pick a camera by index — plugging in any other camera shifts the order, and a laptop
  webcam is often listed first. Finding the camera by name: measured with the earlier Python implementation;
  the Rust implementation (since 0.0.3) has not yet been verified against the camera.
- The driver may not give you the mode you asked for — read `cam.mode` rather than assuming.
- `cam.read()` raises on failure and never returns an empty frame. `f.left` and `f.right` are the two eyes.
- Two Weevia S1 on one computer are not supported yet: the camera reports no USB serial number, so finding
  the camera by name stops when two match.
- `DeviceConfig.weevia_s1()` looks the name up in the list of the backend it opens with: DirectShow by
  default, Media Foundation with `backend="msmf"`.
- `list_devices.py` is one of the six examples added in 0.0.4; it has been run without a camera only.
  `examples/list_devices.py --backend msmf` and the doctor's Media Foundation list have been run without a camera only (made-up lists); they have not been run with the camera.

## If it does not work

First run `python -m opticmix_vision.doctor`: it prints the OS, Python and SDK versions, the video devices,
the camera's USB details and the installed calibration, without opening the camera — paste its output into
an issue. On Windows it prints both device lists, DirectShow's and Media Foundation's, and a line when the
Weevia S1's index differs between them. `python -m opticmix_vision.doctor --open` also opens the camera and adds the mode, frame timing and
every control's value, read only. `doctor --open` has been run with the camera once (one PC, one camera, 2026-11-24); without `--open` the doctor has been run without a camera only (generated frames and fake device lists).

See [When something goes wrong](../README.md#when-something-goes-wrong) in the package README, in
particular:

- **"Weevia S1 not found"** — the message lists the cameras Windows reported.
- **Two Weevia S1 are connected** — connect one at a time.
- **"ControlsUnavailable: opticmix_vision._core (the compiled extension) is not installed"** — finding the
  camera by name on Windows needs the compiled extension that ships in the wheel; install the wheel with the same `python` you run the example with
  (`python -m pip install opticmix-vision`).
- **"failed to open the camera: …"** — check the cable, the `device` you passed, and other programs that may
  have the camera open.
- **Left and right look swapped** — check the order with the finger test under
  [Quickstart](../README.md#quickstart).

## Next

[02 · Live view and frame rate](02-live-view-and-frame-rate.md): watch both eyes and measure the frame rate
you actually get.
