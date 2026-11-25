# 01 · Hello Weevia

Examples: [`list_devices.py`](../examples/list_devices.py), [`quickstart.py`](../examples/quickstart.py),
[`open_by_usb_location.py`](../examples/open_by_usb_location.py)

## What you will do

List the video devices on your computer, find the Weevia S1 among them by name, open it, print the mode the
driver negotiated, and read frames split into a left and a right eye. With two Weevia S1 connected, open one of
them by its USB location.

## Before you start

- Install the package: `pip install opticmix-vision` (see the [package README](../README.md) for the platforms
  it installs on; on Linux, which is experimental, `measure_fps.py`, `record_video.py`, `play_recording.py`, `save_stereo_pairs.py`, `list_controls.py` and `open_by_usb_location.py` have been run once with one camera (2026-11-25), and `export_recording.py` with a camera recording once, from source before release; the viewer, the explorer and the others have not).
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
python examples/open_by_usb_location.py          # each Weevia S1 and its USB location; add --usb-location to open one
```

Without a camera:

```bash
python examples/list_devices.py --synthetic
python examples/list_devices.py --synthetic --backend msmf
python examples/open_by_usb_location.py --synthetic
```

`quickstart.py` has no `--synthetic` option. [Tutorial 02](02-live-view-and-frame-rate.md) runs the same read
loop on generated frames.

### Rust

The Rust crate has the same `list_devices` example. In a checkout of the repository, from
`rust/opticmix-vision`:

```bash
cargo run --example list_devices                                 # the video devices Windows reports (DirectShow)
cargo run --example list_devices -- --backend msmf               # Media Foundation's list: the one capture opens from
cargo run --example list_devices -- --synthetic                  # no camera: the made-up list below
cargo run --example list_devices -- --synthetic --backend msmf   # no camera: the same devices, another order
cargo run --release --features test-support --example open_by_usb_location -- --synthetic   # no camera: pick one of two
```

```text
$ cargo run --example list_devices -- --synthetic
3 video device(s) in the DirectShow list:
  [0] WN Dual Camera   <- Weevia S1   USB location: PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(1)
  [1] Example Laptop Webcam   USB location: PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(5)
  [2] WN Dual Camera   <- Weevia S1   USB location: PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(4)#USB(2)
Weevia S1 ("WN Dual Camera") found: 2
More than one Weevia S1: the camera reports no USB serial number, so they cannot be told apart by name, and opening by name stops with MultipleMatches instead of guessing. Open one by the USB location shown above: cargo run --example open_by_usb_location -- --usb-location "<USB location>" (DeviceSelector::UsbLocation).
A USB location is not a serial number: it names the port the camera is plugged into, and it changes when the camera is plugged into another port. Using two cameras at the same time is not supported. Picking a camera by USB location has been run with the camera once (one PC, one camera, 2026-11-25): with one camera connected, the list showed its USB location, and given that location it opened at 1600x800 MJPG and read one frame; a location no device has was refused (Python DirectShow, Rust Media Foundation); not run with two cameras.
The Media Foundation list (--backend msmf) can be in a different order: an index goes only with the backend whose list it came from.
Capture opens from the Media Foundation list: DeviceSelector::Index(n) is entry n of it (--backend msmf).
```

The Rust crate captures through Media Foundation, so an index you pass as `DeviceSelector::Index` counts in the
list `--backend msmf` prints, not in the default DirectShow one. Of the Rust examples, `list_devices` and
`measure_fps` have been run with the camera once (one PC, one camera, 2026-11-24) — `list_devices` with its
default DirectShow list; `--backend msmf` has been run without a camera only (a made-up list), not with the
camera. `open_by_usb_location`, `record_video` and `play_recording` have been run with the camera once (one PC, one
camera, 2026-11-25). The others: see the crate README, "Examples".

## What you should see

Output with `--synthetic` (generated frames); with the camera the numbers differ.

```text
$ python examples/list_devices.py --synthetic
3 video device(s) in the DirectShow list:
  [0] WN Dual Camera   <- Weevia S1   USB location: PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(1)
  [1] Example Laptop Webcam   USB location: PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(5)
  [2] WN Dual Camera   <- Weevia S1   USB location: PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(4)#USB(2)
Weevia S1 ('WN Dual Camera') found: 2
More than one Weevia S1: the camera reports no USB serial number, so they cannot be told apart by name, and DeviceConfig.weevia_s1() stops with an error instead of guessing. Open one by the USB location shown above: DeviceConfig.weevia_s1(usb_location="<USB location>") (examples/open_by_usb_location.py).
A USB location is not a serial number: it names the port the camera is plugged into, and it changes when the camera is plugged into another port. Using two cameras at the same time is not supported. Picking a camera by USB location has been run with the camera once (one PC, one camera, 2026-11-25): with one camera connected, the list showed its USB location, and given that location it opened at 1600x800 MJPG and read one frame; a location no device has was refused (Python DirectShow, Rust Media Foundation); not run with two cameras.
The Media Foundation list (--backend msmf) can be in a different order: an index goes only with the backend whose list it came from.
$ python examples/list_devices.py --synthetic --backend msmf
3 video device(s) in the Media Foundation list:
  [0] Example Laptop Webcam   USB location: PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(5)
  [1] WN Dual Camera   <- Weevia S1   USB location: PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(1)
  [2] WN Dual Camera   <- Weevia S1   USB location: PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(4)#USB(2)
Weevia S1 ('WN Dual Camera') found: 2
More than one Weevia S1: the camera reports no USB serial number, so they cannot be told apart by name, and DeviceConfig.weevia_s1() stops with an error instead of guessing. Open one by the USB location shown above: DeviceConfig.weevia_s1(usb_location="<USB location>") (examples/open_by_usb_location.py).
A USB location is not a serial number: it names the port the camera is plugged into, and it changes when the camera is plugged into another port. Using two cameras at the same time is not supported. Picking a camera by USB location has been run with the camera once (one PC, one camera, 2026-11-25): with one camera connected, the list showed its USB location, and given that location it opened at 1600x800 MJPG and read one frame; a location no device has was refused (Python DirectShow, Rust Media Foundation); not run with two cameras.
The DirectShow list (--backend dshow) can be in a different order: an index goes only with the backend whose list it came from.
$ python examples/open_by_usb_location.py --synthetic
Weevia S1 in the made-up list:
  [0] USB location: PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(1)
  [2] USB location: PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(4)#USB(2)
picked [2] at USB location PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(4)#USB(2)
negotiated: {'width': 640, 'height': 200, 'fps': 90.0, 'fourcc': 'SYNT', 'backend_name': 'synthetic'}
one frame: left 320x200, right 320x200
```

The synthetic list is made up on purpose: it has two Weevia S1 and a webcam, so you see what happens when
two units are connected. Its USB locations are made up too. With your camera the list is the devices Windows
reports, and one line should be marked `<- Weevia S1`; a line ends with `USB location:` when Windows reports
where that device is plugged in.

The two made-up lists hold the same devices in a different order. Windows has two capture APIs, DirectShow
and Media Foundation, and each has its own list of cameras. The two lists can be in a different order: on one PC with one camera (2026-11-24), `devices.list_devices(backend="msmf")` and `list_devices()` returned the same three cameras in a different order, once. The index is the one OpenCV opens
with that backend — `cv2.VideoCapture(index,
cv2.CAP_DSHOW)` or `cv2.CAP_MSMF` — so an index from one list does not go with the other backend.

`quickstart.py` prints `negotiated:` followed by the mode the driver gave, then opens a window with the left
eye on the left and the right eye on the right; the window title shows the eye size and the frame rate.

## How it works

`list_devices.py` reads the device list and marks every entry whose name contains the camera's USB name:

```python
devices = list_devices(backend=a.backend)
weevia = [d for d in devices if WEEVIA_S1_NAME.lower() in d.name.lower()]
print(f"{len(devices)} video device(s) in the {LIST_NAME[a.backend]} list:")
for d in devices:
    at = f"   USB location: {d.usb_location}" if d.usb_location else ""
    print(f"  [{d.index}] {d.name}{'   <- Weevia S1' if d in weevia else ''}{at}")
```

`open_by_usb_location.py` opens the Weevia S1 at the USB location you give and reads one frame:

```python
cam = Device(DeviceConfig.weevia_s1(usb_location=location)).open()
...
with cam:
    print("negotiated:", cam.mode.to_dict())
    f = cam.read()
```

`DeviceInfo.usb_location` is where Windows says the device is plugged in: the USB device's first location
path, the same string `python -m opticmix_vision.doctor` prints as `location` (on Linux, the USB device's sysfs
name, e.g. `1-4.2`). A USB location is not a serial number: it names the port the camera is plugged into, and it changes when the camera is plugged into another port. Using two cameras at the same time is not supported. Picking a camera by USB location has been run with the camera once (one PC, one camera, 2026-11-25): with one camera connected, the list showed its USB location, and given that location it opened at 1600x800 MJPG and read one frame; a location no device has was refused (Python DirectShow, Rust Media Foundation); not run with two cameras.

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
- The camera reports no USB serial number, so finding the camera by name stops when two Weevia S1 match.
  `DeviceConfig.weevia_s1(usb_location=...)` opens the one at that USB location instead. Using two cameras at
  the same time is not supported. Picking a camera by USB location has been run with the camera once (one PC, one camera, 2026-11-25): with one camera connected, the list showed its USB location, and given that location it opened at 1600x800 MJPG and read one frame; a location no device has was refused (Python DirectShow, Rust Media Foundation); not run with two cameras.
- `DeviceConfig.weevia_s1()` looks the name up in the list of the backend it opens with: DirectShow by
  default, Media Foundation with `backend="msmf"`.
- `list_devices.py` is one of the six examples added in 0.0.4; its DirectShow list has been run with the camera once (one PC, one camera, 2026-11-25, with the 0.0.6 code): with one camera connected, the list showed its USB location.
  `examples/list_devices.py --backend msmf` and the doctor's Media Foundation list have been run without a camera only (made-up lists); they have not been run with the camera.
- Linux: `python examples/list_devices.py` lists the V4L2 capture nodes (`--backend v4l2`, the default there) — the
  index is the N of `/dev/videoN`, the USB location the USB device's sysfs name (e.g. `2-1`). Run without a camera only (made-up sysfs trees); not run with the camera on Linux.

## If it does not work

First run `python -m opticmix_vision.doctor`: it prints the OS, Python and SDK versions, the video devices,
the camera's USB details and the installed calibration, without opening the camera — paste its output into
an issue. On Windows it prints both device lists, DirectShow's and Media Foundation's, and a line when the
Weevia S1's index differs between them. `python -m opticmix_vision.doctor --open` also opens the camera and adds the mode, frame timing and
every control's value, read only. `doctor --open` (its report includes everything the doctor prints without `--open`): verified with the published 0.0.7 wheel on one Weevia S1 unit, Windows 11, one PC, two-level USB hub, two USB re-insertions, read-only; not verified on Linux.

See [When something goes wrong](../README.md#when-something-goes-wrong) in the package README, in
particular:

- **"Weevia S1 not found"** — the message lists the cameras Windows reported.
- **Two Weevia S1 are connected** — open one by its USB location (`DeviceConfig.weevia_s1(usb_location=...)`,
  the locations are in `python examples/list_devices.py`), or connect one at a time. Picking a camera by USB location has been run with the camera once (one PC, one camera, 2026-11-25): with one camera connected, the list showed its USB location, and given that location it opened at 1600x800 MJPG and read one frame; a location no device has was refused (Python DirectShow, Rust Media Foundation); not run with two cameras.
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

## Rust

The Rust `list_devices` commands and their `--synthetic` output are under Run it above. The other steps
without a camera, in a checkout of the repository, from `rust/opticmix-vision`:

```bash
cargo run --example list_devices -- --synthetic --backend msmf
cargo run --release --features test-support --example open_by_usb_location -- --synthetic
cargo run --release --features test-support --example quickstart -- --synthetic
```

Output with `--synthetic` (generated frames); with the camera the numbers differ.

```text
$ cargo run --example list_devices -- --synthetic --backend msmf
3 video device(s) in the Media Foundation list:
  [0] Example Laptop Webcam   USB location: PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(5)
  [1] WN Dual Camera   <- Weevia S1   USB location: PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(1)
  [2] WN Dual Camera   <- Weevia S1   USB location: PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(4)#USB(2)
Weevia S1 ("WN Dual Camera") found: 2
More than one Weevia S1: the camera reports no USB serial number, so they cannot be told apart by name, and opening by name stops with MultipleMatches instead of guessing. Open one by the USB location shown above: cargo run --example open_by_usb_location -- --usb-location "<USB location>" (DeviceSelector::UsbLocation).
A USB location is not a serial number: it names the port the camera is plugged into, and it changes when the camera is plugged into another port. Using two cameras at the same time is not supported. Picking a camera by USB location has been run with the camera once (one PC, one camera, 2026-11-25): with one camera connected, the list showed its USB location, and given that location it opened at 1600x800 MJPG and read one frame; a location no device has was refused (Python DirectShow, Rust Media Foundation); not run with two cameras.
The DirectShow list (--backend dshow) can be in a different order: an index goes only with the backend whose list it came from.
Capture opens from the Media Foundation list: DeviceSelector::Index(n) is entry n of it.

$ cargo run --release --features test-support --example open_by_usb_location -- --synthetic
Weevia S1 in the made-up list:
  [0] USB location: PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(1)
  [2] USB location: PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(4)#USB(2)
picked [2] at USB location PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(4)#USB(2)
negotiated: 1600x800 MJPG 90.0 fps (synthetic)
one frame: left 800x800, right 800x800

$ cargo run --release --features test-support --example quickstart -- --synthetic
negotiated: 1600x800 MJPG 90.0 fps (synthetic)
100 frames in 1.103 s -> 90.7 fps (host-measured)
saved left/right_000099.pgm
```

- `quickstart` does the steps of `quickstart.py` without its window (a window needs a GUI crate, which the
  crate does not depend on): it finds the camera by name and opens it, prints the negotiated mode, reads 100
  frames and prints the rate they came at (as `quickstart.py --no-window` does), saves the last frame's two eyes
  as `left_000099.pgm` and `right_000099.pgm` (what the `s` key saves, as 8-bit gray PGM), and closes the
  camera. Run without a camera only (generated frames); not run with the camera.
- Rust `--synthetic` uses the crate's test doubles: generated 1600×800 frames paced at 90 per second in
  software, so the eyes are 800×800 here.
- `open_by_usb_location` lists each Weevia S1 with its USB location from Media Foundation's list, the one Rust
  capture opens from. Run with the camera once (one PC, one camera, 2026-11-25): it listed the camera at its USB
  location, opened it there at 1600x800 MJPG and read one frame; a location no device has was refused. Not run
  with two cameras.
- Linux: `cargo run --example list_devices` lists the V4L2 capture nodes (`devices::list_v4l2_devices()`;
  `--backend v4l2`, the default there), and `DeviceSelector::Index(n)` opens the `/dev/videoN` it shows; with
  `--synthetic --backend v4l2` it lists made-up V4L2 devices. Its tests use made-up sysfs trees; not run with the
  camera on Linux.
