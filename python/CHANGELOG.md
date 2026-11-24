# Changelog

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
