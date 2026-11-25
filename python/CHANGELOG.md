# Changelog

## 0.0.8 — 2026-11-25

### Added

- Rust examples `quickstart`, `brightness_stats` and `capture_dataset` (sixteen Rust examples, from thirteen).
  `quickstart` does the steps of `quickstart.py` without its window (a window needs a GUI crate, which the crate
  does not depend on): it opens the camera by name, prints the negotiated mode, reads 100 frames and prints the
  rate they came at, saves the last frame's two eyes as `left_000099.pgm` and `right_000099.pgm`, and closes the
  camera. `brightness_stats` writes the CSV columns of `brightness_stats.py` and prints its summary.
  `capture_dataset` is `capture_dataset_npz.py` without the `.npz` (writing one needs a zip crate): it keeps N
  frames in memory, then writes left/right 8-bit PGM pairs, `pairs.csv` (`index,host_ns,left,right`) and
  `capture_info.json` into a new or empty folder. Run without a camera only (generated frames); not run with the camera.
- Rust `capture_info(&mut device)` and `write_capture_info(path, &mut device, extra)` (module `recording`, also at
  the crate root): the capture info the Python package's `capture_info` and `write_capture_info` give — the same
  field names and meanings (a test of the crate compares them with the Python function's), `created` in UTC, the
  calibration file list `null`. `Recorder` writes the same, with its `writer` and `recording` fields, as before.
  The Rust `save_stereo_pairs` example now writes `capture_info.json` beside its pairs, as the Python example does.
  Run without a camera only (generated frames); not run with the camera.
- Rust `host_now_ns()`: now, on the clock of `StereoFrame::host_ns`, to compare a frame's time with other events of
  the same process. Python's `host_ns` is `time.perf_counter_ns()`, which gives now on its clock. Run without a
  camera only.
- `examples/list_devices.py --synthetic --backend v4l2` and the Rust `list_devices -- --synthetic --backend v4l2`:
  the same made-up devices as V4L2 lists them (made-up `/dev/videoN` numbers and sysfs names). The Rust
  `list_devices` takes `--backend v4l2` (the default on Linux, as in Python). Run without a camera only (made-up
  device lists).
- Rust presets (module `presets`): `Controls::save_preset` saves every readable control to a preset file,
  `presets::read_preset` and `list_presets` read the files and the preset folder (`store_dir()`, `preset_path()`),
  and `Controls::plan_preset` checks a preset against the camera and gives what loading it would change, what it
  would leave out (a value whose mode the preset puts in auto, controls with no effect on the mono sensor) and what
  is already set — the Python package's file format, folder and messages (`SdkError::Preset`,
  `SdkError::PresetModel`; a test of the crate reads the files the Python package saves and has the Python package
  read the crate's). `Controls::model()`: the model presets record. The Rust example `presets` (seventeen Rust
  examples) saves, `--plan`s and `--list`s. Applying a preset to the camera is not in the Rust crate yet (Python:
  `presets load`). Run without a camera only (simulated controls, preset files written by the Python package); not
  run with the camera.
- The explorer on Linux: the device list is V4L2's (`devices.list_devices()`, the default there), the camera is
  opened with `backend="v4l2"` at `/dev/videoN` by its number, and, as on Windows, a camera with a USB location
  (the USB device's sysfs name) is found again there at every open (not there: it is not opened) and two of one
  name are listed with their locations. The mode list is not read on Linux: it opens 1600×800 MJPG and says why.
  The USB line is read from sysfs as the doctor reads it. The Settings window is read only on Linux: every control
  shows its value, range and default but is disabled, and so are Reset to defaults… and Load preset… (Save
  preset… reads only); the window says why (camera controls on Linux are not verified and not supported). 0.0.7
  listed no cameras there. Run without a camera only (made-up sysfs trees, fake widgets); not run with the camera
  on Linux, and not run with a display on Linux.

### Changed

- `examples/list_devices.py --synthetic --backend v4l2` lists made-up V4L2 devices; 0.0.7 stopped with exit code 2.
  The Rust `list_devices` refused `--backend v4l2` as an unknown option (exit code 2); on Linux, its
  `--backend dshow` now says the DirectShow list is Windows' (0.0.7 listed the V4L2 nodes).
- The capture info JSON the Rust crate writes says `host_ns` is "the capturing process's monotonic clock" (0.0.7:
  "the recording process's").
- The `--usb-location` help of the viewer, the explorer, `doctor --open`, `presets save` and `load` and
  `calib check` ends "This option: run without a camera only (made-up device lists); not run with a camera, and
  not run with two cameras" (0.0.7: "Picking by USB location: run without a camera only (made-up device lists)",
  which read as if picking a camera by USB location had not been run with the camera).
- Documentation: corrected the 0.0.7 entries below and the pages the 0.0.7 claims audit named — the 0.0.7
  `### Changed` items on Linux ranges and `presets load defaults` now end "Run without a camera only (a made-up
  driver, simulated controls); not run with the camera on Linux.", and its Windows documentation item says "with
  the 0.0.6 code, before the 0.0.7 changes to the device lists" (it said "with the code unchanged"); the crate
  README's Linux `reset_to_defaults()` line says it has not been run with the camera on Linux; the Linux sample is
  named the same way in `v4l2_ctrl.rs` (one PC, one camera, 2026-11-25, crate 0.0.6) and in `find_v4l2_node` (the
  0.0.6 wheel); `list_devices.py` (tutorial 01, the examples and tutorials READMEs), the `measure_fps.py` row and
  the V4L2 driver-time line of the README now say what was run with the camera, as the rest of each page does.
  Rust `devices::list_devices()` says it is Windows' list and points to `list_v4l2_devices()` on Linux; tutorial
  01's Rust section has the Linux `list_devices` line.
- The explorer's `--usb-location` is no longer refused on Linux (0.0.7: exit code 2): it takes the USB device's
  sysfs name, e.g. `1-4.2`; no Weevia S1 there, or an empty one, is exit code 2 before the window opens, as on
  Windows.
- Documentation: what has been verified on the camera is said so, with its scope — verified on one Weevia S1
  unit, Windows 11, one PC, two-level USB hub, two USB re-insertions, read-only, with the published 0.0.7 wheel
  (and, where named, the Rust crate 0.0.7): `python -m opticmix_vision.doctor --open`; recording (`Recorder`,
  `examples/record_video.py`) and reading a recording back, seeking and looping (`examples/play_recording.py`);
  reading the camera controls, with the ranges and defaults the camera reports (`examples/list_controls.py`, and
  the Rust `list_controls` of crate 0.0.7); Media Foundation driver time stamps on every frame, through the
  Python package (`backend="msmf"`) and the Rust crate 0.0.7, with their intervals and the first value printed
  (`examples/measure_fps.py --backend msmf`, the Rust `measure_fps`) — not the accuracy of the times. Where the
  README, `API.md`, the tutorials, `examples/README.md` and the descriptions of the doctor and
  `play_recording.py` said `doctor --open`, `Recorder`, `record_video.py` or `play_recording.py`, or reading the
  controls, had been "run with the camera once" or "not yet verified against the camera", or, for seeking and
  looping, "generated frames only", they now give this scope. Not verified: writing the controls and the
  illuminator with the current implementation (measured with the earlier Python implementation), the `--speed`
  factor of playback, pausing, the viewer's recording (`v`), Linux, a second unit, a direct USB connection, that both eyes show the scene in every recording (one eye was all zero in two short recordings, once; see the README). The
  0.0.8 wheel itself has not been run with the camera.
- The explorer's note when it does not read the mode list ends at "opening 1600×800 MJPG" (0.0.7: "…, the mode
  verified on the Weevia S1").

### Fixed

- `examples/list_devices.py --backend v4l2` off Linux printed an empty V4L2 list and "Linux does not see it
  either"; it now says the V4L2 list is Linux's and which lists Windows has. The Rust `list_devices` says the same.
  Run without a camera only.
- `examples/play_recording.py`: its last line, `frames read 0..53: late …, estimated missed …`, read as if the
  late and missed counts were over frames 0 to 53; they are over every frame read, which with `--loop` is not
  that range (150 frames in tutorial 04). It now says `over the 150 frames read (first frame 0, last frame
  53): late …, estimated missed …`.

## 0.0.7 — 2026-11-25

### Added

- New example `export_recording` (Python and Rust): writes the frames of a recording as left/right image pairs
  into a new or empty folder (`left_000123`, 123 = the frame's number in the recording), with `pairs.csv`
  (`index,host_ns,driver_ns,driver_clock,left,right`, the times recorded in the recording's CSV) and a copy of the
  recording's JSON as `capture_info.json`; `--start`, `--every` and `--count` pick the frames, and the last line
  gives the frames exported and the frames in the recording. Python writes PNG (the names and `pairs.csv` of
  `examples/save_stereo_pairs.py`) and with `--npz` also `frames.npz` (the format of
  `examples/capture_dataset_npz.py`); Rust writes 8-bit PGM, as its `save_stereo_pairs` example does, and plays
  MJPG `.avi` recordings only. `--synthetic` records generated frames first (Python: lossless FFV1; Rust: `--features
  test-support`). From an MJPG recording the images hold what it decodes to, not the camera's pixel values. Run with
  generated recordings; the Python example also with a camera recording once on Linux, from source before release.
  Not run with a camera recording on Windows, and the Rust example not with a camera recording.
- `DeviceInfo` has the USB IDs and link speed of a listed device: `vid`, `pid` and `bcd_device` (`int`; the USB
  vendor ID, product ID and device revision, from the USB device's hardware IDs in the Windows device tree) and
  `link_speed` (`"Low"`, `"Full"`, `"High"`, `"Super"` or `"Super+"`, as the USB hub driver reports the
  port), read without opening the device; `None` when they cannot be read. Rust: `DeviceInfo::vid`, `pid`,
  `bcd_device` (`Option<u16>`) and `link_speed` (`Option<String>`), the same names and meanings, filled by
  `devices::list_devices()`, `list_dshow_devices()` and `list_mf_devices()`; `DeviceInfo::new()` leaves them `None`.
  Finding a camera to open reads the USB location only, as before (Python `devices.resolve_by_name()`, which
  `DeviceConfig.weevia_s1()` uses, returns them `None`; Rust `Device` opening the same). Linux: see `list_devices(backend="v4l2")`
  below. `python -m opticmix_vision.doctor` prints these fields for a device
  the DirectShow list has, so the report and `devices.list_devices()` give one value, and keeps its own reading
  where a field is `None`; its output is unchanged. An extension built from an older source now says to rebuild it
  (`ControlsUnavailable`) instead of failing on the shape of its rows. `examples/list_devices.py --verbose` and the
  Rust `list_devices -- --verbose` print one line for each. `bcd_device` is the USB device revision, not a firmware
  version; the camera reports no USB serial number, no firmware version and no temperature, so there are no fields
  for them. Run without a camera only (made-up device lists); not run with the camera.
- Linux: the camera controls report each control's range and default. The OpenCV path that Linux uses now also asks
  the V4L2 driver with `VIDIOC_QUERYCTRL` (on a second, read-only handle to the device node), so `Controls.describe()`
  gives `minimum`, `maximum`, `step` and `default` for the controls the driver reports, as on Windows. New read-only
  `Controls.driver_names` (the controls the driver reported when asked: on Windows the same as `names()`, on Linux
  those the query answered for, `None` when the backend does not ask) and `Controls.writes_defaults` (`False` on
  Linux). `names()`, `get()`, `set()` and presets otherwise work as in 0.0.6, through OpenCV. `reset_to_defaults()`
  still writes nothing on Linux: the driver's defaults are shown, not written. When the driver cannot be asked there
  are no ranges or defaults, as before, and `Controls.backend_note` says why. Run without a camera (a made-up driver,
  and on Linux a real ioctl on a file that is not a camera), and run with the camera on Linux once (one PC, one
  camera, 2026-11-25, from source before release): the driver answered on the read-only handle, also while streaming.
  Camera controls on Linux remain not verified and not supported.
- `tutorials/`: every page ends with a Rust section — for each step that has a Rust example, the `cargo run`
  command and its output with `--synthetic` (generated frames), and for each step that has none, a line saying
  so; with the Rust differences each example states (for example, Rust writes and plays MJPG `.avi` only, and
  `save_stereo_pairs` and `export_recording` write PGM). Of the Rust example files, `list_devices` and `measure_fps`
  have been run with the camera once (one PC, one camera, 2026-11-24), and `open_by_usb_location`, `record_video` and
  `play_recording` once (one PC, one camera, 2026-11-25); on Linux, `measure_fps`, `record_video`, `play_recording`
  and `list_controls` once (one PC, one camera, 2026-11-25, crate 0.0.6); the others not yet.
- `--usb-location LOCATION` in `python -m opticmix_vision.viewer`, `python -m opticmix_vision.explorer`,
  `python -m opticmix_vision.doctor --open`, `python -m opticmix_vision.presets save` and `load`, and
  `python -m opticmix_vision.calib check`: opens the Weevia S1 at that USB location, to choose between two that
  share a name — each tool finds it with `DeviceConfig.weevia_s1(usb_location=...)`, and the camera controls it
  reads and writes (the viewer's keys and `--preset`, the explorer's Settings window, the doctor's control readout,
  presets) are those of the same camera; the explorer's USB line (VID:PID, revision, link speed) is that camera's,
  and the doctor's report names the USB location and the device it opened. The explorer finds the camera again by
  that location each time it opens it (not there: it is not opened), and takes `--usb-location` on Windows only (on
  Linux, where it lists no cameras: exit code 2). Windows: the location `examples/list_devices.py` or the doctor
  prints; Linux: the USB device's sysfs name, e.g. `1-4.2`. No Weevia S1 at that location (the message lists each device's name and USB
  location) or an empty or blank location: exit code 2, before anything is opened (the explorer before its window
  opens; the doctor prints no report). With another way of choosing what to open (`--device`, `--synthetic`, the
  viewer's `--recording`, `calib check --pairs-dir`): exit code 2. `doctor` needs `--open` for it. A USB location
  is not a serial number: it names the port the camera is plugged into, and it changes when the camera is plugged
  into another port. Using two cameras at the same time is not supported. Run without a camera only (made-up device
  lists); not run with a camera, and not run with two cameras.
- Linux: `devices.list_devices(backend="v4l2")` lists the cameras — one `DeviceInfo` per video capture node in
  `/sys/class/video4linux` (chosen as `find_v4l2_node` chooses them, so a UVC camera's metadata node is left out), in
  `/dev/videoN` order: `index` is the N (what `DeviceConfig(device=N, backend="v4l2")` opens), `name` the node's
  name, `usb_location` the USB device's sysfs name (e.g. `2-1`), and `vid`, `pid`, `bcd_device` and `link_speed` its
  `idVendor`, `idProduct`, `bcdDevice` and `speed` (the same words as on Windows: 480 Mb/s `"High"`, 5000 `"Super"`,
  more than 5000 `"Super+"`); `None` for what cannot be read. Reads sysfs only, opens no device; an empty list off
  Linux. The doctor reads these values with the same function, so its Linux report and the list give one value; its
  output is unchanged. `DeviceConfig.weevia_s1()` on Linux still finds the camera with `find_v4l2_node`, reading the
  USB location only. `examples/list_devices.py` prints this list on Linux (`--backend v4l2`, the default there; with
  `--verbose` the IDs and link speed). Rust: `devices::list_v4l2_devices()` (feature `capture`) gives the same
  entries. The explorer still lists DirectShow's devices, the list of the backend it opens with, so on Linux it lists
  no cameras, as in 0.0.6. Run without a camera only (made-up sysfs trees); not run with the camera on Linux.

### Changed

- Linux: `Controls.set()` / `set_many()` raise `ValueError`, and `load_preset()` raises `PresetError`, for a value
  outside a range the V4L2 driver reports, before anything is written; 0.0.6 passed such a value to OpenCV. Controls
  whose range the driver does not report are not checked, as before. Run without a camera only (a made-up driver, simulated controls); not run with the camera on Linux.
- Linux: the explorer's Reset to defaults, `python -m opticmix_vision.presets load defaults` and the viewer's
  `p`/`P` on the defaults say that the camera's defaults are shown, not written, instead of that everything is
  already at the defaults; `presets load defaults` (also with `--dry-run`) then exits with status 1 (0.0.6: 0).
  Run without a camera only (a made-up driver, simulated controls); not run with the camera on Linux.
- Rust on Linux: `Controls::reset_to_defaults()` no longer writes the defaults and `Controls::plan_reset()` is empty
  (0.0.6 wrote them through V4L2), as in the Python package; `describe()` still gives the driver's defaults. New
  read-only `Controls::writes_defaults()` (`false` for the V4L2 controls on Linux), the same name and meaning as
  Python's `Controls.writes_defaults`. Windows unchanged. Run without a camera only; not run with the camera on Linux.
- The explorer with two cameras that share a name: the device list shows each one's USB location
  (`WN Dual Camera · <USB location>`), and the one chosen in the list is the one opened and written to by the
  Settings window — found again by its USB location each time it is opened, since the list's order can change. 0.0.6
  chose by name, so it always opened, and wrote to, the first of the two. Two of one name whose USB locations cannot
  be read are not opened (the window says to connect one at a time). With two of one name, or with `--usb-location`,
  the mode list is not asked of ffmpeg, which asks by name: the window opens 1600×800 MJPG and says why. Run without a
  camera only (made-up device lists); not run with a camera, and not run with two cameras.
- Linux: `devices.list_devices()` with no `backend` lists the V4L2 capture nodes (`backend="v4l2"`); 0.0.6 gave
  an empty list there (it read DirectShow's). On Windows the default is still `"dshow"`, and an unknown `backend` now
  says `'dshow', 'msmf' or 'v4l2'`.
- Documentation: what 0.0.6 said had not been run with the camera and has since been run once on Windows (one PC,
  one camera, 2026-11-25), with the 0.0.6 code, before the 0.0.7 changes to the device lists — picking a camera by
  USB location
  (`DeviceConfig.weevia_s1(usb_location=...)`, `DeviceSelector::UsbLocation`, the `open_by_usb_location` examples):
  with one camera connected, the list showed its USB location, and given that location it opened at 1600x800 MJPG
  and read one frame; a location no device has was refused (Python DirectShow, Rust Media Foundation); not run with
  two cameras. Rust recording and playback (`Recorder`, `Device::open_recording`, the `record_video` and
  `play_recording` examples): `record_video` 10 s, 881 frames, estimated missed 5, dropped by the write queue 0;
  Rust and Python played the same file back as 881 frames. The pages now say so.
- Documentation, Linux (experimental; once, one PC, one camera, 2026-11-25): the 0.0.6 manylinux wheel has been
  run with the camera — open, frames, recording and playback, read-only; finding the camera by name, and picking it
  by USB location (opened at its location; a location with no camera refused; not run with two cameras); the
  examples `measure_fps.py`, `record_video.py`, `play_recording.py`, `save_stereo_pairs.py`, `list_controls.py`
  and `open_by_usb_location.py`. The Rust crate 0.0.6: `measure_fps`, `record_video`, `play_recording` and
  reading the controls (`list_controls`); writing not run. Camera controls on Linux remain not verified and not
  supported.

### Fixed

- Linux: `examples/open_by_usb_location.py` without `--usb-location` lists each Weevia S1 and its USB location
  from V4L2's list (`devices.list_devices()`), as it does from DirectShow's on Windows; 0.0.6 printed no device
  lines there (seen once with the camera on Linux, 2026-11-25). The new listing: run without a camera only (a
  made-up sysfs tree); not run with the camera.
- Rust on Linux: the `open_by_usb_location` example without `--usb-location` lists each Weevia S1 and its USB
  location from the V4L2 list (`devices::list_v4l2_devices()`), as the Python example does; 0.0.6 printed no device
  lines there. Its test uses an empty sysfs; not run with the camera on Linux.
- Rust on Linux: the `list_devices` example lists the V4L2 capture nodes (`devices::list_v4l2_devices()`, with
  the default `capture` feature; `--verbose` adds the USB IDs and link speed) instead of saying the listing is for
  Windows; `DeviceSelector::Index(n)` opens the `/dev/videoN` it shows. Its tests use made-up sysfs trees; not run
  with the camera on Linux.

## 0.0.6 — 2026-11-25

### Added

- Picking one of two cameras that share a name by USB location. `DeviceInfo.usb_location` (Python) /
  `DeviceInfo::usb_location` (Rust): on Windows the USB device's first `DEVPKEY_Device_LocationPaths` entry, read
  from the device tree through cfgmgr32 without opening the device (the doctor's `location`); on Linux the USB
  device's sysfs name, e.g. `1-4.2`; `None` when it cannot be read. Python: `DeviceConfig.weevia_s1(usb_location=)`,
  `devices.resolve_by_name(..., usb_location=)`, `devices.pick_by_usb_location()` and
  `devices.find_v4l2_node(..., usb_location=)`. Rust: `DeviceSelector::UsbLocation { name, usb_location }` (the
  controls then find the camera at the same location), `devices::pick_by_usb_location()`,
  `devices::list_mf_devices()`, `devices::list_dshow_devices()` and `DeviceInfo::new()`. An empty or blank USB
  location is an error (Python `ValueError`, Rust `SdkError::InvalidValue`), never a match for a device whose
  location could not be read. With two cameras of one name, `MultipleMatches`
  now lists their USB locations when they are known. `examples/list_devices.py` and the Rust `list_devices`
  example end each line with the device's USB location; new example `open_by_usb_location` (Python and Rust)
  opens the camera at a location and reads one frame. A USB location is not a serial number: it names the port the camera is plugged into, and it changes when the camera is plugged into another port. Using two cameras at the same time is not supported. Picking a camera by USB location has been run without a camera only (made-up device lists); it has not been run with a camera, and not run with two cameras.
- `python -m opticmix_vision.calib show [SERIAL|FILE]` and `calib.show()`: what a calibration file holds —
  for each eye the model, size (and the mode it is for), fx fy cx cy and distortion k1..k4; between the eyes
  the baseline, t, the full rotation matrix R, its Rodrigues vector (rad and degrees), angle and axis; the
  file's sha256 (the value a recording's `.json` lists) and `meta`; and whether `Device.calibration` uses it.
  It says `installed file, not matched to this camera (no USB serial)`, and what to do when no file or several
  are installed. `--json` prints the same as JSON (on an error, `{"error": ...}`); `--synthetic` shows a
  generated calibration. The doctor's Calibration section names the command. Reads the file only. Run
  without a camera only (made-up calibration files); not run with the camera.
- Rust: the `list_devices` example takes `--backend msmf` and prints Media Foundation's list
  (`devices::enumerate_mf_video_devices()`), the list Rust capture opens from and `DeviceSelector::Index`
  counts in; `--synthetic --backend msmf` prints the same made-up devices in another order. The crate
  README's platform table says which list an index belongs to. The two lists can be in a different order —
  seen once on one PC, with Python's `list_devices(backend="msmf")` — not with this example. `--backend msmf`:
  run without a camera only (a made-up list); not run with the camera.
- The bug report form asks, for a calibration problem, for the output of `python -m opticmix_vision.calib show`
  as well as the doctor's.
- The capture-info JSON says where its control values came from and when they were read: `controls_source`
  (`"camera"`, `"simulated"`, `"given"` or `null`) and `controls_read_at` (ISO 8601); a recording's
  `controls_at_end_source` says the same of `controls_at_end`. The viewer's `s` and `v` give the time it read
  every control when it opened (`capture_info(..., controls_read_at=)`, `Recorder(..., controls_read_at=)`).
  Run on generated frames only; not run with the camera.
- `Device.repeated_frames`: the frames `read()` returned since `open()` whose two eyes were both
  byte-identical to the frame before, counted by the comparison the `stall_frames` check already makes
  (`None` with `stall_frames=0`). Through OpenCV's Media Foundation backend (`backend="msmf"`), `read()`
  warns once per `open()` when more than 5 % of the last 300 frames repeated, naming DirectShow and the
  Rust crate — on one PC with one camera (2026-11-24) 276 of 900 frames per eye did through that path and 0 of
  900 through DirectShow; cause not established. The viewer's top line shows `repeated N`; `python -m
  opticmix_vision.doctor --open` counts frames repeated in both eyes and takes `--backend`, and through
  `msmf` repeated images are a finding. The warning and the count in `examples/measure_fps.py` have been run with the
  camera once through each backend (one PC, one camera, 2026-11-25; the example's output does not record the OpenCV
  version): through `msmf` the warning came once and 271 of 900 frames repeated in both eyes; through DirectShow 0 of
  900, no warning. The doctor's `--open --backend msmf` once (same PC and camera, OpenCV 4.13.0): its finding said
  276 of 904. The viewer's `repeated N`: run on generated frames only; not run with the camera.
- Rust: `Device::repeated_frames() -> Option<u64>`, the name and meaning of the Python
  `Device.repeated_frames`: the frames `read()` decoded since open whose two eyes were both byte-identical to
  the frame before, counted by the stall check's comparison (`None` with `stall_frames` 0); samples `read()`
  skipped as undecodable and those `read_raw()` returns are not compared. A count only, with no warning. The
  `measure_fps` example prints it. Run with the camera once (one PC, one camera, 2026-11-25, Media Foundation, 450
  frames): 0, the same as the `measure_fps` example's own per-eye counts (left 0, right 0). A count above 0:
  generated frames only.
- `examples/record_video.py --queue-frames N` (default 0) encodes on a thread through a queue of `N` frames, as
  the viewer's `v` does; its last line gives the frames the write queue dropped (among the estimated missed
  ones). Run on generated frames only; not run with the camera.
- Rust: reading the installed calibration file — `calib::resolve()` and `Device::calibration()`, with
  `calib::store_dir()`, `installed()`, `installed_file()` and `load(path)` — by the rules of the Python package's
  `calib.resolve()` and `Device.calibration`: the same folder, the file used only when exactly one is installed,
  the same checks and messages, and the eye size against the opened mode. Values: per eye the model (KB4), K,
  D and the eye size; R, t and the baseline (mm); the file's `device` block and `meta`; the file's path. New errors
  `SdkError::Calibration` and `SdkError::CalibLookup`. The `calibration_info` example (`--synthetic`: a made-up
  calibration in a temporary folder). It reads files only (install with `python -m opticmix_vision.calib add`);
  installed file, not matched to this camera (no USB serial). Run without a camera only (made-up calibration
  files); not run with the camera.
- Rust: points through a calibration's KB4 model — `Intrinsics::project` (a point in the eye's camera
  coordinates to its pixel) and `Intrinsics::unproject` (a pixel to the unit ray), up to 92.5° from the optical
  axis (`None` beyond, at the origin or for values not finite); the `calibration_info` example prints the pixel
  of each eye's optical axis and one point projected into both eyes (`R·p + t`) and unprojected back.
  Compared with OpenCV's `cv2.fisheye.projectPoints` and `cv2.fisheye.undistortPoints` on made-up values. For a
  point behind the eye (Z < 0) within 92.5° the two differ: OpenCV divides by Z and returns the pixel of -p,
  `project` returns the point's own pixel. Tutorial 08 shows the same calculation in Python with OpenCV. No
  rectification or depth. Run without a camera only (made-up calibration files); not run with the camera.
- Rust: recording and playback in the Python package's file format — `Recorder::create(path, &mut device)`,
  `write(&frame)` and `close()` write the MJPG `.avi` (both eyes side by side, left eye first), the `.csv`
  (`frame,host_ns,driver_ns,driver_clock`) and the `.json` (mode, the camera's layout and left/right order, control
  values at the start and end, crate version, frame count, late / missed frame estimates); `Device::open_recording`
  plays one through the same `read()` as a camera, with each frame's recorded `host_ns` and driver time and its
  number in the file as `index`, and `SdkError::EndOfRecording` after the last frame; `Device::playback()` seeks
  (by frame or seconds), loops, paces at the recorded intervals divided by a speed, and gives the frame count and
  length. A recording the Rust crate wrote plays in Python (`DeviceConfig.recording`), and a Python MJPG recording
  plays in Rust. The frames are encoded to JPEG again, as in Python, so pixel values change a little. As in
  Python: a write queue (`Recorder::create_with_queue(path, &mut device, n)` encodes on a writing thread; a frame
  that finds the queue full is dropped and counted in `dropped_frames()` and the JSON's `dropped_frames`, the
  JSON's `write_queue` is `n`, a writing-thread error comes back from the next `write` or from `close`, `close`
  writes the frames still queued), pausing a recording (`Recorder::pause` / `resume`, the JSON's `breaks`), and
  pausing playback (`Playback::set_paused`, `Playback::status` → `PlaybackStatus`). Not in Rust: FFV1 (`.mkv`),
  `note_control`, the calibration file list in the JSON, replacing a recording of the same name (refused), an
  `.avi` over 2 GiB (`write` returns an error; record on to a new file). A V4L2 time Rust checked to be on
  `CLOCK_MONOTONIC` is named `v4l2_monotonic` in the CSV, apart from Python's `v4l2`. New errors
  `SdkError::Recording` and `SdkError::EndOfRecording`; new dependency `jpeg-encoder` with `capture` (license
  `(MIT OR Apache-2.0) AND IJG`; without `capture`, as the Python extension is built, it is not a dependency).
  Examples `record_video` (encodes on a writing thread, `--queue-frames`, default 64) and `play_recording`. Run
  without a camera only (generated frames, and files the Python package wrote from generated frames with
  opencv-python 4.14 on Windows); not run with the camera.

### Changed

- `python -m opticmix_vision.calib` prints UTF-8, as the doctor does, so a path or a file's `meta` with
  non-ASCII characters no longer stops it on a console with another code page.
- The viewer's `v` encodes on a thread while the window draws: `recording.Recorder(..., queue_frames=N)` hands
  each frame to a writing thread through a queue of `N` frames (the viewer: 64). A frame that finds the queue
  full is not recorded and is counted — `Recorder.dropped_frames`, the JSON's `dropped_frames`, the viewer's REC
  line (`missed~N since start (dropped N)`) and its message when recording stops; `estimated_missed_frames`
  includes `dropped_frames` (do not add them), and the message says so. Frame numbers (the CSV, `breaks`,
  `control_changes`) stay those of the file. An error on the writing thread is raised by the next `write()` or
  by `close()`; `close()` writes the frames still queued first. Without `queue_frames` `Recorder` writes as
  before. A queue does not make lossless keep up with 90 fps: the frames it cannot take are dropped and
  counted. The viewer's `v` may still not keep up with 90 fps; `examples/record_video.py` remains the first
  thing to try to record every frame, unless the write queue dropped most of the missed frames (then the
  message advises MJPG, a lower `--fps` or `examples/capture_dataset_npz.py`). Any error the encoder raises
  ends the recording with a message and the window still closes. Run on generated frames only; not run with
  the camera.
- `Device.open()` starts the `stall_frames` check afresh: after `close()` and `open()` on the same `Device`
  the first frame is no longer compared with the last frame before `close()`, and the count of identical
  images in a row starts again from zero (before, it carried on). An eye that had already stopped before
  `close()` is therefore reported up to `stall_frames` frames later than before. A new `Device` behaved
  this way already. Run on generated frames only; not run with the camera.
- `Recorder.write()` after `close()` raises `ValueError("recorder is closed")` (before: with a queue the
  frames were taken without a word; without one, `AttributeError`).
- Rust: code that matches `SdkError` exhaustively stops compiling: four variants were added
  (`SdkError::Calibration`, `SdkError::CalibLookup`, `SdkError::Recording`, `SdkError::EndOfRecording`) and the enum
  is now `#[non_exhaustive]` — add a wildcard arm.
- Rust: code that builds a `devices::DeviceInfo` with a struct literal stops compiling: the field `usb_location`
  was added and the struct is now `#[non_exhaustive]` — build one with `DeviceInfo::new(index, name, usb_location)`.
  Its fields are still public to read.
- Rust: code that matches `DeviceSelector` exhaustively stops compiling: the variant `DeviceSelector::UsbLocation`
  was added and the enum is now `#[non_exhaustive]` — add a wildcard arm. Building a `DeviceSelector` is unchanged.
- Python: `devices.resolve_by_name()` takes a new keyword `usb_location=`, and `DeviceConfig.weevia_s1()` a new
  keyword-only `usb_location=` (with `device=` as well: `ValueError`); calls without it behave as before.
  `devices.DeviceInfo` has a third field, `usb_location` (default `None`): `DeviceInfo(index, name)` still works,
  but an entry of `list_devices()` whose USB location is known no longer equals `DeviceInfo(index, name)`.
- With two cameras of one name, the `MultipleMatches` message ends with their USB locations when they are known
  (Python and Rust; Rust's Media Foundation and V4L2 messages list each device's location, `None` where it could
  not be read) — code that matches the old text needs updating.
- The doctor's finding for two Weevia S1 and the explorer's tooltip on the USB details now say to open one by its
  USB location (`DeviceConfig.weevia_s1(usb_location=...)`; the port, not a serial number) as well as to connect one
  at a time, and that picking by USB location has been run without a camera only; not run with a camera or two
  cameras.
- Python: needs `opencv-python>=4.14` (before: `>=4.8`), because FFV1 writing failed now and then with the 4.12 and
  4.13 wheels (see Fixed). `pip install -U opencv-python` if `pip` reports a conflict. With the 4.14 wheel on Windows
  (one PC, one camera through a 2-level USB hub, 2026-11-25, two runs): `examples/measure_fps.py` read 900 frames at
  88.2 frames per second with no repeated image (first run). In a second run after a USB re-insert, with a different
  build of the native module: a 10 s MJPG recording (888 frames, 88.7 frames per second, estimated missed 29) and a
  10 s `--lossless` recording (443 frames, 44.3 frames per second in the read-and-record loop, estimated missed 450,
  no error) closed and played back with the same frame counts; and a separate 90-frame FFV1 file, read back by this
  package, gave the pixel values `Device.read()` had delivered in 90 of 90 frames, both eyes. On Linux the camera has
  been run with the 5.1 wheel for capture only (package 0.0.3, 2026-11-23); recording has not been run on Linux.

### Fixed

- `python -m opticmix_vision.doctor --open --backend msmf` no longer reports that the negotiated pixel format is
  not MJPG and that the frame rate falls short of 90 fps: through OpenCV's Media Foundation backend
  `CAP_PROP_FOURCC` reads back OpenCV's output format code (RGB32 `0x00000016`, RGB24 `0x00000014`), not the
  camera's format, so the finding came from that code rather than the camera — seen on one PC with one camera
  (2026-11-25), in a report that also counted 904 frames in 10 s (90.27 fps). The negotiated mode line now shows
  such a code in hex and says what it is. DirectShow and V4L2 keep the finding. Run without a camera only (made-up captures).
  The 0.0.5 notes say OpenCV has Media Foundation convert 'the MJPG' to RGB; which format reaches that conversion has
  not been established (one PC, one camera, once, 2026-11-25).
- FFV1 (lossless) recording with OpenCV before 4.14: when the encoder fails on a frame, `Recorder.write()` raises
  `RuntimeError` saying to upgrade OpenCV (`pip install -U opencv-python`) or record MJPG, instead of OpenCV's bare
  `Unknown C++ exception from OpenCV code`; the recording is closed with the frames written before it (read back after a failure with the 4.13 wheel and cv2 alone; with 4.12 not checked). On one Windows PC, with
  generated grayscale frames, FFV1 writing failed now and then with the opencv-python 4.12 and 4.13 wheels and not
  with 4.14 or 5.1. The failure happens with cv2 alone, without this package; whether it is in OpenCV or in the
  FFmpeg its wheel bundles has not been established. Other OpenCV versions and Linux: not run. This changes only
  the message. Not run with the camera.

## 0.0.5 — 2026-11-24

### Added

- `tutorials/`: eight numbered tutorials built on the existing examples, and a page on the viewer and the
  explorer; a test runs every command they show for use without a camera and compares what it prints with
  the output on the page, and checks that every published example is in a tutorial.
- The viewer records and plays back: `v` starts and stops a recording in `--out`
  (MJPG `.avi`, both eyes side by side, and a CSV of frame times — the files
  `examples/record_video.py` writes), and `--recording <file>` plays one back.
  Recording with `v` has been run with the camera once (one PC, one camera, 2026-11-24): one recording started and stopped with `v` (392 frames, about 130 missed while recording), no stall error; its `.json` carries `controls_note`. Playback with `--recording`: generated frames only; not run with the camera. A compressed recording does not have the camera's
  pixel values.
- Playback of a recording: `Device.playback` (`opticmix_vision.recording.Playback`)
  seeks by frame or by seconds, reports `position`, `frame_count` and `duration_s`,
  plays at the recorded pace (`real_time`, from the CSV) at any `set_speed`, loops,
  pauses, reports `status` (playing, paused, ended; no callback), and with
  `recorded_time` gives the recorded `host_ns`. It also gives the late / missed
  estimates over the whole recording. A frame's `index` is now its number in the
  file. `Device.mode` and `Device.recording_info` come from the recording's new
  `.json`. A recording that was not closed: the FFV1 `.mkv` plays what was written
  (with a warning), the MJPG `.avi` cannot be opened and the error says why. The
  viewer plays with keys (space, `,`/`.`, `[`/`]`, `-`/`+`), a seek bar and `--loop`,
  and stays on the last frame; `examples/play_recording.py` takes `--start`,
  `--start-seconds`, `--real-time`, `--speed`, `--loop`, `--frames` and
  `--recorded-time`. `play_recording.py` has been run with the camera once (one PC, one camera, 2026-11-24): it read back the 443 frames of an MJPG recording of the camera. Seeking, speed, looping and the viewer's playback keys: generated frames
  only; not run with the camera.
- Lossless recording: `recording.Recorder(..., codec=LOSSLESS_CODEC)` (FFV1),
  `examples/record_video.py --lossless` (and `--fps`) and `viewer --lossless`; the lossless file keeps the pixel values the SDK delivered (generated frames only; not run with the camera).
  It takes longer per frame than MJPG and may not keep up with 90 fps: the JSON, the
  viewer's REC line and `record_video.py` count the frames missed while recording
  and say what to do. Recorder can pause and resume; a pause is listed as a break,
  not as missed frames. Run on generated frames only; not run with the camera.
- Capture info JSON (`recording.capture_info`, `write_capture_info`): negotiated
  mode, the camera's layout and left/right order, device name, installed
  calibration files (sha256), every control value, SDK version and time, written
  next to recordings, the viewer's PNG pairs, and the files of
  `save_stereo_pairs.py` and `capture_dataset_npz.py`. A recording's JSON also lists
  the control changes made while recording and the values at the end. Run on
  generated frames only; not run with the camera.
- Recording and playback are Python only; the Rust crate has no recording API yet.
- Frame statistics: `Device.late_frames`, `Device.estimated_missed_frames` and
  `Device.actual_fps`, estimated from host times (not a device counter), shown on
  the viewer's top line. Run on generated frames only; not run with the camera.
- Rust: `Device::late_frames()`, `Device::estimated_missed_frames()` and `Device::actual_fps()`, the same
  statistics with the same rule as Python, from `host_ns` on Windows and Linux; every sample counts, including
  undecodable ones (those are `undecodable_frames()`, not missed). `measure_fps` and `threaded_capture` print them. `measure_fps`:
  run with the camera once (one PC, one camera, 2026-11-24); `threaded_capture`: run without a camera only.
- Driver time stamps and sequence numbers on each frame, beside `host_ns`: `StereoFrame.driver_ns`,
  `driver_clock` and `driver_sequence` (Python and Rust). The clock is named — Media Foundation sample time,
  V4L2 buffer time (Rust: only when the driver flags it
  `CLOCK_MONOTONIC`) — and is an operating-system driver time, not a camera clock; whether it marks the
  exposure or the arrival of the frame is not known. On one PC (Windows 11) with one camera (2026-11-24, once) the first Media Foundation time (Python and Rust) was about 364,400 s — not the start of streaming; on this PC it was within a few seconds of Python's `time.perf_counter_ns()` (compared across two runs by file times, not within one process), but what the clock's zero is has not been established. Use differences between frames, not the absolute value. Python gets the time through OpenCV with
  `backend="msmf"` on Windows or V4L2 on Linux (`None` with DirectShow, the Windows default) and never the
  sequence number. Through OpenCV MSMF the time is that of the sample after OpenCV has Media Foundation convert the MJPG to RGB (Rust reads the MJPG samples without conversion); run with the camera once (one PC, one camera, 2026-11-24): through MSMF 276 of 900 frames per eye repeated the previous image while the driver-time intervals were 11.11 ms (mean, min and max) — `driver_ns` does not show whether a new image arrived; cause not established. DirectShow: 0 of 900 and 0 of 450; Rust (Media Foundation, no conversion): 0 of 450, driver-time intervals 0.01 to 16.90 ms (mean 11.23). See `StereoFrame` in `API.md`. Rust also gives `driver_time_source` on Linux: start of exposure or end of frame, as the V4L2
  driver reports it in the buffer flags (not checked on the camera). Rust: `Device::driver_missed_frames()`
  counts the gaps in the V4L2 sequence numbers
  (`None` on Windows); `Device::undecodable_frames()` is the new name of `dropped_frames()` (kept), which
  counts MJPG samples that did not decode, not lost frames. `measure_fps` (both languages) prints the
  driver-time intervals and how far each frame trails the earliest. Windows: run once on one PC with
  one camera (2026-11-24); Linux: run without a camera only (made-up driver reports), not run with the
  camera.
- Driver time stamps in the recording CSV: `recording.Recorder` writes `frame, host_ns, driver_ns,
  driver_clock` (the frame's `StereoFrame.driver_ns` and `driver_clock`; both cells empty where the frame had
  none — DirectShow, the Windows default, and the synthetic source), and so do the viewer's `v` key and
  `examples/record_video.py`; `examples/save_stereo_pairs.py` adds the same two columns to `pairs.csv`. Both
  examples take `--backend {dshow,msmf,v4l2}` and `--device`: on Windows `--backend msmf` fills `driver_ns` (the
  controls then go through OpenCV's fallback, no ranges or defaults). Through OpenCV's Media Foundation path the
  driver times came evenly 11.11 ms apart on one PC with one camera (once, 2026-11-24) while images repeated;
  these times do not show whether a new image arrived. Played
  back with `Playback.recorded_time`, a frame's `driver_ns` and `driver_clock` are the recorded ones, beside
  the recorded `host_ns`; without it they stay `None`. `Playback.recorded_driver(index)` gives them for any
  frame. A CSV of only `frame, host_ns` (earlier versions) plays back as before. Run without a camera only; not run with
  the camera.
- Seeing the Media Foundation device list (Windows): `examples/list_devices.py --backend msmf` (DirectShow
  stays the default), and `python -m opticmix_vision.doctor` prints it under the DirectShow list
  (`video_devices.media_foundation` in the JSON; listed, no device opened) with a line, not a finding, when the
  Weevia S1's index differs between the two. The two lists can be in a different order: on one PC with one camera (2026-11-24), `devices.list_devices(backend="msmf")` and `list_devices()` returned the same three cameras in a different order, once. `examples/list_devices.py --backend msmf` and the doctor's Media Foundation list have been run without a camera only (made-up lists); they have not been run with the camera. The report's
  `video_devices.source` changes from `"ffmpeg"` to `"DirectShow, through ffmpeg"` (a label to read, not to
  compare); `REPORT_FORMAT` stays 1, since only keys were added.
- Media Foundation device list (Windows): `devices.list_devices(backend="msmf")` and
  `devices.resolve_by_name(needle, backend="msmf")` list Media Foundation's video sources in the order
  OpenCV's `CAP_MSMF` counts device indexes (Rust: `devices::enumerate_mf_video_devices()`; empty off
  Windows). `DeviceConfig.weevia_s1(backend="msmf")` now finds the Weevia S1 by name in that list instead
  of needing `device=`; with `backend="any"` it does so only when the camera has the same index in the Media
  Foundation and DirectShow lists (OpenCV may open either), and `Device.open()` refuses a device that
  `backend="any"` opened by index through another backend. With `backend="msmf"` the controls also read the
  camera's model from that list (`Controls.model`), so loading a preset saved from a Weevia S1 is no longer
  refused as coming from an unknown model. Run without a camera only
  (made-up device lists); not run with the camera.
- Control presets: `Controls.save_preset(path)` writes every readable control (value, units, auto/manual
  modes), the camera model and the SDK version to a JSON file; `Controls.plan_preset(path)` lists what
  loading it would change; `Controls.load_preset(path)` writes what differs in one `set_many()` call and
  returns each result with `kept`. A different model, an unknown control, other units or a value out of
  range raise `PresetError` with nothing written; controls with no effect on the mono sensor are not written
  unless `include_mono=True`. `python -m opticmix_vision.presets` saves, loads, lists
  and shows presets in a folder beside the calibration folder; the explorer's Settings window has Save
  preset… and Load preset…; `examples/presets.py`. Run without a camera only; writing a preset to the
  camera writes several controls at once — see README, "Camera controls".
- Viewer presets and saturation: `n` saves the controls as a new preset (`--preset-name`), `p` steps
  through the presets of this camera's model and shows what loading one would change, `P` writes it in one
  `set_many()` and shows each result; `--preset NAME` loads one at start. A load made while recording goes
  into the recording's JSON. A new line shows each eye's share of saturated pixels (`--saturation-level`).
  Run on generated frames only; not run with the camera.
- Rust: eight examples in `rust/opticmix-vision/examples/` — `list_devices`, `measure_fps`,
  `save_stereo_pairs`, `list_controls`, `set_controls_once`, `threaded_capture`, `led_level`,
  `handle_stall`, each the Rust version
  of the Python example of the same name. `list_devices` and `measure_fps` have been run with the camera once (one PC, one camera, 2026-11-24); the others
  without a camera only (`--synthetic`: generated frames and simulated controls). See the crate README, "Examples".
- `python -m opticmix_vision.doctor` prints what to paste into an issue (`--json`
  for JSON): OS, Python, SDK, `_core` and OpenCV versions, the video devices and
  how many Weevia S1 are connected, each camera's USB details from the operating
  system (VID:PID, USB device revision `bcdDevice`, status, location, link speed)
  and the installed calibration files, without opening the camera. `--open` adds
  the negotiated mode, frame timing and stalled eyes over 3 s, and every control's
  value and range, read only; the report shows the control-write count stays at 0.
  Findings at the top name the problems found (no camera, two cameras, a USB 2
  link, a format other than MJPG, frames well below the negotiated rate, a stalled
  eye), and a Log part keeps the SDK's warnings from the run. `--output FILE`
  writes the report in UTF-8. The serial number, firmware version and temperature
  are reported as not available from this camera. The explorer's top bar shows the
  camera's VID:PID, USB device revision and link speed where it showed "SN: —".
  `--open` has been run with the camera once (one PC, one camera, 2026-11-24); without `--open`, `--json` and Linux: run without a camera only
  (generated frames, fake device lists for Windows and Linux).
- `python -m opticmix_vision.calib check` and `calib.check()` / `calib.check_pairs()`:
  show in numbers whether a calibration file fits the connected camera — the
  epipolar distance of features matched between the eyes (median, 95th percentile,
  share within 1 px and 2 px) and the share of points with positive disparity
  (left/right swapped). Numbers only, no pass/fail threshold; with too few matches
  it says so (exit code 2). With more than one file installed, `--all`, or several
  `--file`, it compares the files on the same frames (`calib.compare_pairs()`);
  `--pairs-dir` checks saved PNG pairs (`calib.load_pairs()`) instead of the camera.
  New example `examples/calibration_check.py`; `synthetic.SyntheticPlaneSource`
  renders a textured plane through a given calibration. Run without a camera only
  (generated frames and generated PNG pairs); not run with the camera. See the
  README, "Calibration".

- Viewer: `c` shows the installed calibration file (`Device.calibration`, one file;
  not matched to the camera, which reports no USB serial) — serial, file, each eye's model, size, fx fy cx cy and distortion, and the
  baseline, t and rotation angle between the eyes — read from the file only (no
  control read); with no file it says `python -m opticmix_vision.calib add
  <file.json>`. `p` now steps to `defaults` after the saved presets: what the
  camera's own defaults would change (`Controls.plan_reset`, as the explorer's
  Reset to defaults), and `P` writes them (`reset_to_defaults()`); the values written
  show on the control line without reading the camera again. Run on generated frames
  only; not run with the camera.

### Fixed

- The viewer's LED / exp / gain line ends with `(as opened + viewer's writes)`, and the
  key help (`h`, now wrapped to fit the window) says the same: the values are those
  read when the viewer opened plus its own writes, not a live reading. A once-per-press
  key repeated within 0.6 s now says once per press that it was ignored, instead of
  nothing. When frames were missed while recording with `v`, which runs alongside the
  preview, the viewer's message first suggests `examples/record_video.py` (no preview
  window). The calibration lines, messages and help wrap to the window's width. Checked
  with simulated key presses and generated frames; not run with the camera.
- The viewer's `--backend` was ignored without `--device`; it now goes to
  `DeviceConfig.weevia_s1(backend=...)` (default still DirectShow; V4L2 on Linux). With
  `msmf` the controls go through the OpenCV fallback. Run without a camera only.
- `recording.Recorder` opens its video, `.csv` and `.json` when it is created (at
  the eye size of the source's negotiated mode), no longer on the first `write()`:
  the time to open them sat between the first two frames and was counted as missed
  frames, so even a generated MJPG recording reported missed frames and the advice
  to record MJPG. A source without a mode, or a first frame of another size, still
  opens them on the first `write()`; frames really missed are counted as before,
  the first interval included. An unopenable codec now raises `OSError` from
  `Recorder(...)`. The viewer's `v` records from the frame after the key. Run on
  generated frames only; not run with the camera. A recorder closed before any frame
  was written removes its files (the viewer's `v` pressed twice in a row leaves
  nothing); a recording that fails on its first frame stops the recording, not the
  viewer. Recording again to the same path keeps the earlier recording as
  `<file>.replaced` until the new one closes with a frame, and puts it back if no frame
  came or a file could not be moved (e.g. the `.csv` open in another program). After a
  process that stopped mid-recording, the `.replaced` files hold the earlier recording
  and the files without `.replaced` the unfinished one; the error says how to keep
  either (delete the unfinished files before taking `.replaced` off the names).
- A recording no longer overwrites another one's `.csv` and `.json`: `Recorder`
  raises `FileExistsError` when a video (or a `capture_dataset_npz.py` `.npz`) with
  the same name and another extension is in the folder (recording again to the
  same path still replaces it), and
  `examples/record_video.py --lossless` now writes `recording_lossless.mkv` by default
  instead of `recording.mkv`, whose `.csv` and `.json` were the MJPG
  `recording.avi`'s.
- The missed-frames advice of `record_video.py` and the viewer suggests MJPG only for
  a lossless recording; both print the new `Recorder.missed_advice`.
- The viewer's LED / exposure / gain keys and `a`: a key pressed while the previous
  change (a key, `a` or a preset loaded with `P`) was still queued started from the
  camera's old value, so two presses made one step, `a` twice stayed manual and an
  exposure key right after `a` was refused (`Controls.get` reads the camera and does
  not see queued writes). They now go on from the value the viewer asked for until
  that write has finished.
- Holding `v` down in the viewer window started and stopped a recording with each
  key repeat (several files from one press, seen with the camera), and holding `s`,
  `n`, `p`, `P` or `a` saved, read or wrote the controls again and again. `s`, `v`,
  `n`, `p`, `P`, `a`, `h` and space now act once per press: the same key within
  0.6 s of the last time it came is ignored. Keys given with `key_feed` are separate
  presses.
  Checked with simulated key presses and generated frames; not run with the camera.
- The viewer read the LED, exposure and gain from the camera on every frame to draw
  them. On the one camera tested (once, through a USB hub), reading these three values
  on every frame was followed by the stall check reporting the left eye (the right eye
  was not checked) in 2 of 2 test-script runs, while 300 frames read without them in
  the same session streamed normally. Whether the reads cause it is not established.
  The viewer now reads every control once when it opens: the line shows those values
  with its own writes (the value read back after each write, so a value the camera
  did not keep shows), a refused write reads that control once again, and a change
  made by another program shows when the viewer is opened again; exposure shows
  `auto` while the viewer has turned auto exposure on. The LED / exposure / gain keys
  and `a` go on from the same values instead of reading the camera on each press. The
  explorer's Settings window and redraw read no control while streaming (checked;
  unchanged). Run with the camera (one camera, one session, through a USB hub, 2026-11-24): the viewer
  with this change showed 2700 frames on each of three opens and 1686 frames on a fourth, with no stall
  error. The earlier version was not run in that session, so whether this change is what prevents the
  stall is not established.
- The viewer's `s` and `v` read every control from the camera for their `.json` while
  streaming: at each save, and at the start and end of each recording. They now write
  the values the viewer knows (read when it opened, then its own writes; the
  `controls_note` field says so). `capture_info` and `write_capture_info` take
  `controls=`, and `Recorder` takes `controls=`, a function called when it is created
  and when it closes; without it they read the camera as before. Run with the camera
  once (one PC, one camera, 2026-11-24): one recording started and stopped with v (392 frames, about 130 missed while
  recording), no stall error; the .json carries the controls_note. The `s` key's `.json`: generated frames
  only; not run with the camera.

## 0.0.4 — 2026-11-23

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
