# Changelog

## 0.0.4 — 2026-11-24

### Added

- Per-unit calibration files (preview): `python -m opticmix_vision.calib add <file>`
  installs the file for your camera and `Device.calibration` returns it
  (`opticmix_vision.calibration`, schema v1, is now part of the package). Run on
  Windows with two cameras, once each; installing by label (download) is not
  available yet. See the README, "Calibration".
- A Linux x86_64 wheel on PyPI (`cp310-abi3-manylinux2014_x86_64`, glibc 2.17 or
  newer), so `pip install opticmix-vision` installs on Linux x86_64 without a Rust
  toolchain. Experimental — not a supported platform. The 0.0.4 manylinux wheel
  installed and passed the package tests in clean Python 3.10, 3.12 and 3.14
  containers (no camera); no manylinux wheel has been run with the camera. On a minimal system OpenCV also needs `libGL.so.1`
  (Debian/Ubuntu: `sudo apt install libgl1 libglib2.0-0`) — see the README, "Linux (experimental)".
- `DeviceConfig.weevia_s1()` finds the camera by name on Linux too: the
  `/dev/videoN` capture node whose name contains `WN Dual Camera` in `/sys/class/video4linux`
  (the one whose `index` is 0). It refuses when two match, as on Windows. On Linux
  `backend` now defaults to `"v4l2"` (it was `"dshow"`, which Linux does not have).
  The lookup itself is `opticmix_vision.devices.find_v4l2_node()`. Linux name
  lookup: implemented, not yet run on the camera.
- Fifteen examples in `examples/` beside `quickstart.py`. Nine were on the
  repository after 0.0.3.post1 — live preview, saving PNG pairs, recording video,
  listing controls, writing controls once, the illuminator level, measuring fps,
  collecting an `.npz` dataset and a PyTorch data loader. Six are new: listing
  video devices, capturing on a thread, brightness statistics, reopening after a
  read error, printing the installed calibration, and playing a recording back —
  these six have been run without a camera only.
  See `examples/README.md` for what each one writes to the camera.
- A recording made by `examples/record_video.py` can be read back through
  `Device`: `DeviceConfig.recording(path)`; `read()` raises `EndOfRecording`
  after the last frame. Run on generated recordings only, not on a recording of
  the camera.
- Rust crate `opticmix-vision` 0.0.4 on crates.io (preview): frame capture, stereo
  split and camera controls from Rust. Run on Windows 11 on one camera; not run on
  Linux — see `rust/opticmix-vision/README.md`.
- `API.md`: a reference for every public name of the package; a test keeps its
  signatures equal to the code.
- README: answers to common problems, by the message you see (the compiled
  extension is missing, the camera does not open, a read fails, exposure while
  auto exposure is on, calibration messages, `libGL.so.1`, two cameras connected,
  which USB port, pip finds no version or tries to build), GitHub Discussions
  under "Getting help", and the release cadence — we aim to release about once a
  month.

### Changed

- Error messages, printed output and command-line help are in English.
- Docstrings of the public modules (`device`, `devices`, `controls`, `led`,
  `synthetic`, the package itself, the viewer and the explorer) are in English.

### Fixed

- The test suite skips the explorer tests when Python has `tkinter` but the Tk
  library is missing (a slim Linux container); with pytest 9.2 it stopped with a
  collection error.

## 0.0.3.post1 — 2026-11-23

First release on PyPI: `pip install opticmix-vision`. The code is the same as
0.0.3; only the package description changed — install from PyPI, where to get
help, and how to open the camera on Linux. PyPI has the Windows x64 wheel and
the source; Linux installs from source.

## 0.0.3 — 2026-11-22

Camera controls and device listing now run in a compiled extension
(`opticmix_vision._core`, written in Rust; its source is in `rust/`). The
extension ships inside the wheel, so the release file is a Windows x64 wheel
(`cp310-abi3`, Python 3.10 and newer) rather than a pure-Python one — with an
experimental Linux x86_64 wheel beside it — and
installing from the repository instead of the wheel needs a Rust toolchain.
Everything exported by 0.0.2 is still there with the same names and arguments.

### Upgrading from 0.0.2

Install the 0.0.3 wheel over 0.0.2 —
`pip install --upgrade opticmix_vision-0.0.3-cp310-abi3-win_amd64.whl` — with no
uninstall first. Every name 0.0.2 exported from the package is still there with
the same arguments; 0.0.3 only adds optional ones. `set_prop()` and `get_prop()`
behave differently in the cases listed under *Changed* below, and the viewer
dropped options that did nothing in the package (*Removed*). Installing from the
repository instead of the wheel: the package is now in `python/`, so add
`#subdirectory=python` to the URL, and the build needs a Rust toolchain, 1.88 or newer (the package has been built and tested with 1.88 and 1.99), and on
Windows the Microsoft C++ Build Tools, which Rust's default MSVC toolchain links with. New in the distribution: `THIRD_PARTY_NOTICES.txt`, the licenses of the
Rust crates compiled into the extension.

### Added

- `DeviceConfig.weevia_s1()` finds the camera by name and applies the settings
  measured on it (side by side, first half is the right eye, 1600×800 MJPG at
  90 fps). An index is no longer needed for the Weevia S1. Measured with the earlier Python implementation; the Rust implementation in 0.0.3 has not yet been verified against the camera.
- `opticmix_vision.devices` lists the cameras Windows reports and, when ffmpeg is
  on PATH, the modes the camera reports (without it the list is empty), and
  selects a camera by name. Measured with the earlier Python implementation; the Rust implementation in 0.0.3 has not yet been verified against the camera.
- `cam.controls` (`opticmix_vision.controls`) covers every control the camera
  advertises: current value, range and default read from the device, and
  writes that go through one guard (equal values skipped, writes spaced at
  least 12 ms apart) and are read back afterwards (`WriteResult.kept`). Measured with the earlier Python implementation; the Rust implementation in 0.0.3 has not yet been verified against the camera.
- `python -m opticmix_vision.explorer` opens the camera in a window with a mode
  list and a settings panel; `--synthetic` runs it with no camera. Measured with the earlier Python implementation; the Rust implementation in 0.0.3 has not yet been verified against the camera.
- `THIRD_PARTY_NOTICES.txt`: name, version, license and license text of each Rust
  crate linked into the extension. It is in the wheel (`.dist-info/licenses/`),
  the source archive and `python/`.
- An experimental Linux x86_64 wheel (`cp310-abi3-linux_x86_64`), not a
  supported platform. Run on one Ubuntu 26.04 machine with one camera
  (capture only, `backend="v4l2"` and a device index); camera controls on Linux
  have not been verified and are not supported. See the README, "Linux
  (experimental)".
- `read()` raises `DeviceError` naming the eye when one eye has sent the
  byte-identical image 90 frames in a row (`DeviceConfig.stall_frames`, 0 turns
  the check off).

### Changed

- The repository holds the Python package in `python/` and the Rust core in
  `rust/`. On 0.0.2 the package sat at the top of the repository.
- `Device.set_prop()`, `get_prop()` and `extra_props` go through the same write
  guard as `cam.controls`.
- `set_prop()` raises `DeviceError` for a property that is not a camera control
  (focus, zoom, frame size and so on). On 0.0.2 such a write was handed to the
  driver. Resolution, frame rate and pixel format are set through `DeviceConfig`.
- Integer controls take whole numbers: `set_prop("CAP_PROP_GAIN", 28.5)` raises
  ValueError; 0.0.2 handed it to the driver.
- `get_prop("CAP_PROP_AUTO_EXPOSURE")` reports the auto-exposure mode when the
  native Windows control path is in use (0.75 auto, 0.25 manual). 0.0.2
  returned -1, which is what OpenCV reports. Measured with the earlier Python implementation; the Rust implementation in 0.0.3 has not yet been verified against the camera.
- `python -m opticmix_vision.viewer` with no camera arguments opens the Weevia S1
  by name. Its keys write once you stop pressing, not on every key repeat. Measured with the earlier Python implementation; the Rust implementation in 0.0.3 has not yet been verified against the camera.
- With `backend="v4l2"`, or `"any"` when OpenCV opens the camera through V4L2,
  the pixel format is set before the frame rate. Other backends keep the pixel
  format last.
- The README states the left/right order measured on the camera
  (`first_is_right`) and the control behaviour measured on it.

### Fixed

- `examples/quickstart.py` stopped at its imports on 0.0.2, because it asked for
  calibration functions that are not in the package. It now opens the camera,
  or prints the frame rate with `--no-window`.

### Removed

- The `hands` optional dependency. It installed an inference runtime for a
  feature that is not in the package.
- The viewer's `--calib`, `--num-disparities` and `--block-size` options, the
  `calib` and `depth_params` arguments of `run_viewer()`, the `rectify`, `depth`
  and `has_calib` fields of `ViewerState`, and the `depth_valid_fraction` entry
  of what `run_viewer()` returns. They did nothing in the package: the modules
  they need are not in it.
- The `v4l2` and `avfoundation` choices of `--backend` in the viewer and in
  `examples/quickstart.py`. The viewer and the example have not been run on
  Linux or macOS.

## 0.0.2 — 2026-09-13

- The package contains only the modules that have been run against the camera:
  `device`, `led`, `synthetic`, `viewer`. Calibration, rectification, depth, ROS
  `camera_info` and hand tracking are left out until they have been validated.

## 0.0.1 — 2026-09-13

- First public release.
