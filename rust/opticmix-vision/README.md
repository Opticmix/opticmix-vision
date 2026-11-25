# opticmix-vision (Rust)

Rust API for the Weevia S1 stereo-IR camera — **preview**. The API is 0.x and may change.

Status: on Windows 11 this crate has been run on one camera — 1600×800 MJPG at 90 fps through Media
Foundation, 90–92 frames per second in 300-frame runs, fourteen controls read while streaming, no
control written. On Linux it has been run once (one PC, one camera, 2026-11-25, crate 0.0.6): `measure_fps`,
`record_video` and `play_recording`, and the controls read (`list_controls`), none written. Not run: a second
unit. Status: partial — not yet verified for release; the
Python package `opticmix_vision` ([repository](https://github.com/Opticmix/opticmix-vision)) is the
measured path.

## Install

```sh
cargo add opticmix-vision
```

MSRV 1.88.

## Example

```rust
use opticmix_vision::{Device, DeviceConfig, UserValue};

fn main() -> opticmix_vision::Result<()> {
    let mut cam = Device::open(DeviceConfig::weevia_s1())?;
    println!("{:?}", cam.mode()); // what the camera actually gave — check it
    // Once, at start: mode and value in one write.
    // Exposure is in the driver's units, not converted to time — Windows (DirectShow): -5. Linux (V4L2): the V4L2 standard's 100 µs unit, not run on the camera.
    // Writing through this crate's Device has not yet been run on the camera.
    cam.controls()?.set_many(&[
        ("auto_exposure".into(), UserValue::Choice("manual".into())),
        ("exposure".into(), UserValue::Int(-5)),
    ])?;
    let frame = cam.read()?; // frame.left, frame.right: 8-bit gray
    println!("{}x{}", frame.left.width, frame.left.height);
    Ok(())
}
```

## Examples

Seventeen programs in `examples/`, each the Rust version of the Python example of the same name
(`capture_dataset`: of `capture_dataset_npz.py`). In a checkout of the repository, run them from
`rust/opticmix-vision`:

```sh
cargo run --example list_devices
cargo run --release --example measure_fps
cargo run --release --features test-support --example measure_fps -- --synthetic   # no camera
```

Use `--release` for anything that reads frames: in a debug build, decoding the frames is much slower than
the camera sends them.

| Example | What it does | Writes to the camera | Without a camera | With the camera |
|---|---|---|---|---|
| `list_devices` | Lists the video devices Windows reports and how many are a Weevia S1 (on Linux, from 0.0.7, the V4L2 capture nodes: `devices::list_v4l2_devices()`): DirectShow's list (where the controls look), or with `-- --backend msmf` Media Foundation's (the list capture opens from, which `DeviceSelector::Index` counts in); `-- --verbose` adds one line each for a device's `vid`, `pid`, `bcd_device` and `link_speed` | none, does not open it | `-- --synthetic` (a made-up list; with `--backend msmf`, the same devices in another order) | DirectShow list: run with the camera once (one PC, one camera, 2026-11-24). `--backend msmf` and `--verbose`: run without a camera only (a made-up list); not run with the camera. Linux: not run with the camera |
| `open_by_usb_location` | Lists each Weevia S1 with its USB location (Media Foundation's list; on Linux, from 0.0.7, the V4L2 list); `-- --usb-location "<location>"` opens the one there with `DeviceSelector::UsbLocation` and reads one frame — how to open one of two cameras that share a name | none | `--features test-support -- --synthetic` (a made-up list of two) | run with the camera once (one PC, one camera, 2026-11-25): it listed the camera at its USB location, opened it there at 1600x800 MJPG and read one frame; a location no device has was refused. Not run with two cameras |
| `measure_fps` | Opens the camera, prints the negotiated mode, times N frames: interval mean/min/max, late intervals, repeated images, skipped MJPG samples, the `Device` statistics (late, estimated missed, actual fps); where the driver gives them, the same for the driver time stamps, how far each frame trails the earliest (host minus driver time) and frames missing between driver sequence numbers | none | `--features test-support -- --synthetic` | run with the camera once (one PC, one camera, 2026-11-24); its `Device::repeated_frames` line came later: run with the camera once (one PC, one camera, 2026-11-25, Media Foundation, 450 frames): 0, the same as the `measure_fps` example's own per-eye counts (left 0, right 0). A count above 0: generated frames only |
| `quickstart` | The steps of `quickstart.py` without its window (a window needs a GUI crate): opens the camera by name, prints the negotiated mode, reads 100 frames and prints the rate they came at, saves the last frame's two eyes as 8-bit PGM, closes the camera | none | `--features test-support -- --synthetic` | run without a camera only (generated frames); not run with the camera |
| `save_stereo_pairs` | Saves every Nth left/right pair as 8-bit PGM, with `pairs.csv` (frame index, host time, file names) and `capture_info.json` (`write_capture_info`, from 0.0.8) | none (reads every control for the JSON) | `--features test-support -- --synthetic` | not run yet |
| `capture_dataset` | `capture_dataset_npz.py` without the `.npz` (that needs a zip crate): keeps N frames in memory, then writes left/right 8-bit PGM pairs, `pairs.csv` and `capture_info.json` (`write_capture_info`, with the frame count, eye size and late / estimated missed frames under `pgm_pairs`) into a new or empty folder | none (reads every control for the JSON) | `--features test-support -- --synthetic` | run without a camera only (generated frames); not run with the camera |
| `brightness_stats` | Per-eye mean, min, max and share of saturated pixels for N frames into a CSV with the columns of `brightness_stats.py`, and its summary | none | `--features test-support -- --synthetic` | run without a camera only (generated frames); not run with the camera |
| `list_controls` | Prints every control: current value, range, step, default, units, how far its effect is verified | none — prints the write count | `--features test-support -- --synthetic` | same calls run on Windows on one camera¹; on Linux this file read the controls once (one PC, one camera, 2026-11-25) |
| `presets` | The steps of `presets.py` that do not write: saves every control to a preset file (`Controls::save_preset`), `-- --plan FILE` prints what loading it would change and what it would leave alone (`Controls::plan_preset`, as `presets.py --load FILE --dry-run`), `-- --list` lists the preset folder. The Python package's files and folder; a preset that does not fit stops with its message. Applying a preset to the camera: Python only (`presets load`) | none — prints the write count | `--features test-support -- --synthetic` | run without a camera only (simulated controls, preset files written by the Python package); not run with the camera |
| `set_controls_once` | Sets manual exposure and gain in one `set_many` at startup, waits, prints the status, the read-back value and whether the camera kept it | one `set_many` call | `--features test-support -- --synthetic` | not run yet |
| `threaded_capture` | Moves the `Device` to a reader thread; the main thread processes the latest frame and counts the skipped ones; the reader's late / estimated missed frames and actual fps come back when its thread is joined | none | `--features test-support -- --synthetic` | same calls run on Windows on one camera¹ (not the statistics line) |
| `led_level` | Sets the IR illuminator level once at startup and compares the image before and after (mean, share of pixels changed) | one write (none if already at that level) | `--features test-support -- --synthetic` | not run yet |
| `handle_stall` | When `read()` fails or an eye stalls: closes the camera, asks for the USB cable to be re-inserted, reopens, gives up after N tries | none | `--features test-support -- --synthetic --wait 0` | not run yet |
| `record_video` | Records the two eyes for N seconds to an MJPG `.avi` with its `.csv` and `.json` (the Python package's format; see "Recording and playback"), encoding on a writing thread (`--queue-frames`, default 64), then prints the frame count and the late / estimated missed / dropped frames | none (reads every control for the JSON) | `--features test-support -- --synthetic` | run with the camera once (one PC, one camera, 2026-11-25): 10 s, 881 frames, estimated missed 5, dropped by the write queue 0. On Linux once (one PC, one camera, 2026-11-25) |
| `play_recording` | Plays a recording through `Device::open_recording` — the same `read()` as the camera — from a frame (`--start`), at the recorded pace (`--real-time`, `--speed`), looping (`--loop`); prints the first frames' times and the late / estimated missed frames over the frames read | none, does not open it | any recording, e.g. one `record_video -- --synthetic` made | does not use the camera; it played back a camera recording `record_video` made once (one PC, one camera, 2026-11-25): 881 frames, as Python did. On Linux once (one PC, one camera, 2026-11-25) |
| `calibration_info` | Prints the installed calibration file: its path, K, D, eye size and the pixel of the optical axis (`Intrinsics::project`) per eye, R, t and the baseline (mm), then one point in the left eye and, by R and t, the right eye: its pixel in each and the ray back (`unproject`); or the crate's message when none or several are installed | none, does not open it | `-- --synthetic` (a made-up calibration in a temporary folder; your calibration folder is not touched) | does not use the camera. Run without a camera only (made-up calibration files) |
| `export_recording` | Writes the frames of an MJPG `.avi` recording as left/right 8-bit PGM pairs into a new or empty folder (`left_000123.pgm`, 123 = the frame's number in the recording), with `pairs.csv` (index, and the host and driver times as the recording's CSV has them, file names) and a copy of the recording's JSON as `capture_info.json`; `--start`, `--every`, `--count` pick the frames; the last line gives the frames exported and the frames in the recording. The images hold what the MJPG decodes to; the Python example decodes the same recording with another JPEG decoder, so the two can differ by up to 1 in a pixel value (the largest difference seen on two recordings of generated frames, one written by each; 1 to 2 % of the pixels differed). Without the recording's `.csv`, `host_ns` is frame number / frame rate and the example says so | none, does not open it | `--features test-support -- --synthetic` (records 30 generated frames first) | does not use the camera; run with generated recordings only; not run with a camera recording |

¹ Opening the camera, reading and timing 300 frames, reading from a thread other than the one that
opened the camera, and reading every control while streaming were run on Windows 11 on one camera in an
internal check of this crate. Of the example files, `list_devices` and `measure_fps` have been run with the camera once (one PC, one camera, 2026-11-24), and `open_by_usb_location`, `record_video` and `play_recording` once (one PC, one camera, 2026-11-25); on Linux, `measure_fps`, `record_video`, `play_recording` and `list_controls` once (one PC, one camera, 2026-11-25, crate 0.0.6); the others not yet.
Writing through this crate has not been run on the camera.

`--synthetic` uses the crate's test doubles (the `test-support` feature): generated 1600×800 frames paced
at 90 per second in software, and simulated controls with made-up ranges. Its output is not measured data.

## Frame times and frame counts

Each `StereoFrame` carries two clocks, named so it is clear which one a time is on:

| Field | Clock |
|---|---|
| `host_ns` | This process's monotonic clock, right after the capture backend handed over the sample. Not comparable across processes |
| `driver_ns`, `driver_clock` | The operating system capture driver's time stamp, in ns. Windows (`DriverClock::MediaFoundation`): the Media Foundation sample time from `IMFSourceReader::ReadSample` (100 ns units) of the camera's MJPG sample, read without conversion. On one PC (Windows 11) with one camera (2026-11-24, once) the first Media Foundation time was about 364,400 s — not the start of streaming; what the clock's zero is has not been established. Use differences between frames, not the absolute value. Linux (`DriverClock::V4l2Monotonic`): the V4L2 buffer time stamp, kept only when the driver flags it `CLOCK_MONOTONIC`. `None` otherwise |
| `driver_sequence` | Linux: the V4L2 buffer sequence number. `None` on Windows (Media Foundation gives none) |
| `driver_time_source` | Linux: where the V4L2 driver says it took `driver_ns` — `StartOfExposure` or `EndOfFrame` (`V4L2_BUF_FLAG_TSTAMP_SRC_*`; a driver that sets no source reports end of frame). The value the driver reports, not checked on the camera. `None` on Windows |

The camera sends no clock of its own that this crate reads, so none of these is a camera (device) time.
Whether a driver time marks the exposure or the arrival of the frame is not known; the gap between the
driver time and `host_ns` has not been measured.

`host_now_ns()` gives the time now on the `host_ns` clock, to compare a frame's `host_ns` with other events of
the same process (for example, how long ago the frame was read). Run without a camera only.

Counts and rates on `Device`, each for a different thing:

| Method | Counts |
|---|---|
| `undecodable_frames()` (also `dropped_frames()`, its earlier name) | MJPG samples that arrived but did not decode, which `read()` skipped. Not in `estimated_missed_frames()`: these samples did arrive. For frames that never arrived use `estimated_missed_frames()` (Windows and Linux) or `driver_missed_frames()` (Linux) |
| `driver_missed_frames()` | Frames missing between the driver's sequence numbers since the first sample read (0, 1, 2, 5, 6 gives 2). V4L2 calls these dropped frames: received but not stored for lack of a free buffer; its documentation says frames dropped on the camera side of the USB link may not be counted. `None` on Windows |
| `late_frames()` | Samples since open received by `read()` more than 1.5 frame periods (at `mode().fps`) after the one before, from `host_ns`. Windows and Linux. A late frame can be host delivery jitter rather than a lost frame, and a `read()` loop slower than the frame period counts here (and in `estimated_missed_frames()`) the same as a frame lost on the camera side — the two cannot be told apart. If it is not 0: build with `--release`, connect the camera directly to a USB 3 port, and move processing to another thread (`threaded_capture`) |
| `estimated_missed_frames()` | For each late interval, round(interval / period) - 1 (half to even), from `host_ns`. Windows and Linux. An estimate from host times, where `driver_missed_frames()` (Linux) counts the driver's own numbers — the two can differ |
| `actual_fps()` | Samples per second over the last 30 intervals, from `host_ns`; 0.0 until two samples |
| `repeated_frames()` | Frames `read()` decoded since open whose two eyes were both byte-identical to the frame before, counted by the comparison the stall check already makes (a frame that ends in the stall error counts too). `None` when `stall_frames` is 0. Only decoded frames are compared: not the samples `read()` skipped or those `read_raw()` returns. The name and meaning of the Python package's `Device.repeated_frames`, without its warning (that one is for OpenCV's Media Foundation backend). `measure_fps` prints it. Run with the camera once (one PC, one camera, 2026-11-25, Media Foundation, 450 frames): 0, the same as the `measure_fps` example's own per-eye counts (left 0, right 0). A count above 0: generated frames only |
| `index` on each frame | Frames delivered by this `Device`, from 0 — no gaps |

`late_frames()`, `estimated_missed_frames()` and `actual_fps()` have the names and the rule of the Python
package's `Device.late_frames`, `estimated_missed_frames` and `actual_fps`; a test runs the Python definition on
the same host times and compares the numbers. Every sample counts, including those `read_raw()` returns and
those `read()` skips as undecodable (Python counts the frames its `read()` returns). They are not read from a
device frame counter. `measure_fps` and `threaded_capture` print them, and `measure_fps` says what to try when frames were missed. `measure_fps`: run with the camera once (one PC, one camera, 2026-11-24); `threaded_capture`: run without a camera only
(made-up host times and generated frames).

The Python package's `StereoFrame` has the same `driver_ns`, `driver_clock` and `driver_sequence` fields
(through OpenCV; see its API reference). They are not the same times on Windows: OpenCV asks Media Foundation
to convert the camera's format to RGB and passes on the converted sample's time. Windows driver times: run with the camera once (one PC, one camera, 2026-11-24): this crate's `measure_fps` gave driver-time intervals from 0.01 to 16.90 ms (mean 11.23) and 0 of 450 repeated images; the Python package through OpenCV's Media Foundation backend gave intervals of 11.11 ms (mean, min and max) with 276 of 900 frames per eye repeating the previous image — there `driver_ns` does not show whether a new image arrived; the cause has not been established. Linux driver times, sequence numbers and `driver_missed_frames`: run without a camera only (made-up driver
reports); not run with the camera.

## Platforms

| | Windows | Linux |
|---|---|---|
| Capture | Media Foundation | V4L2 — run with the camera once (one PC, one camera, 2026-11-25: measure_fps, record_video, play_recording) |
| Controls | DirectShow | V4L2 — reading run with the camera once (list_controls, 2026-11-25); writing not run. `reset_to_defaults()` writes nothing and `plan_reset()` is empty: the driver's defaults are shown by `describe()`, not written (`Controls::writes_defaults()` is `false`; 0.0.6 wrote them); not run with the camera on Linux |
| Which list an index belongs to (`DeviceSelector::Index`) | Media Foundation's, as `cargo run --example list_devices -- --backend msmf` prints it — not the default DirectShow list, which can be in a different order (seen once on one PC, with Python's `list_devices(backend="msmf")` — not with this example) | `/dev/videoN` |
| A USB location (`DeviceInfo::usb_location`, `DeviceSelector::UsbLocation`) | The USB device's first `DEVPKEY_Device_LocationPaths` entry, read through cfgmgr32 from the Media Foundation symbolic link (capture) and the DirectShow DevicePath (controls, `list_devices`) — the doctor's `location` | The USB device's sysfs name, e.g. `1-4.2` (`/sys/class/video4linux/videoN/device`) |
| USB IDs and link speed (`DeviceInfo::vid`, `pid`, `bcd_device`, `link_speed`) | Read by `list_devices()`, `list_dshow_devices()` and `list_mf_devices()` from the USB device's hardware IDs and from the USB hub driver, without opening the device — the doctor's VID:PID, bcdDevice and link speed; `cargo run --example list_devices -- --verbose` prints them. `bcd_device` is the USB device revision, not a firmware version. Run without a camera only (made-up lists); not run with the camera | Read by `list_v4l2_devices()` (feature `capture`; one entry per capture node, the index the N of `/dev/videoN`) from sysfs: `idVendor`, `idProduct`, `bcdDevice` and `speed`, as the doctor reads them, without opening the device. Run without a camera only (made-up sysfs trees); not run with the camera on Linux |

A USB location is not a serial number: it names the port the camera is plugged into, and it changes when the camera is plugged into another port. Using two cameras at the same time is not supported. Picking a camera by USB location has been run with the camera once (one PC, one camera, 2026-11-25): with one camera connected, the list showed its USB location, and given that location it opened at 1600x800 MJPG and read one frame; a location no device has was refused (Python DirectShow, Rust Media Foundation); not run with two cameras.

Windows: run on one camera (above). Linux: run once on one camera (2026-11-25, crate 0.0.6; capture and reading the controls, see the table).

Requirements:

- Windows: the crate uses Media Foundation (mf.dll, mfplat.dll) with or without `capture` — the device list
  uses it as well as capture. It may be absent on Windows N/KN editions unless the Media Feature Pack is
  installed.
- Linux: building needs libclang and kernel headers (Ubuntu: `sudo apt install libclang-dev linux-libc-dev`)
  because the `v4l` crate generates its bindings at build time.

## Features

- `capture` (default): frame capture (`Device`), and on Linux the V4L2 controls.
- Controls only, without capture: `default-features = false`. This keeps the Windows DirectShow controls,
  `split_stereo`, the stall check and the LED.
- `devices::enumerate_mf_video_devices()` (with or without `capture`): Media Foundation's video sources, names
  in the order OpenCV's `CAP_MSMF` counts device indexes; activates no device; empty off Windows. Run on
  Windows once, through Python's list_devices(backend="msmf"), which calls it (one PC, one camera,
  2026-11-24); not run from Rust directly — `cargo run --example list_devices -- --backend msmf` prints it, and
  that has been run without a camera only (a made-up list, which does not call it).
- Presets (module `presets`, from 0.0.8): `Controls::save_preset` saves every control to a preset file,
  `presets::read_preset` / `list_presets` read the files and the preset folder (`store_dir()`, beside the calibration
  folder), and `Controls::plan_preset` checks a preset against the camera and gives what loading it would change,
  leave out (a value whose mode the preset puts in auto; controls with no effect on the mono sensor) or find already
  set — the Python package's file format, folder and messages (`SdkError::Preset`, `SdkError::PresetModel`), so
  each reads the other's files. `Controls::model()` is the model presets record. Applying a preset to the camera is
  not in the Rust crate yet (Python: `presets load`). Run without a camera only (simulated controls, preset files
  written by the Python package); not run with the camera.
- No window: the examples print and write files. `live_preview.py` has no Rust example: it needs a window library.

## Recording and playback

`Recorder` writes frames to a recording and `Device::open_recording` plays one back through the same `read()` as
the camera — in the Python package's file format (`opticmix_vision.recording`), so a recording made here plays in
Python and a Python MJPG recording plays here.

```rust
use opticmix_vision::{Device, DeviceConfig, Recorder, SdkError};

let mut cam = Device::open(DeviceConfig::weevia_s1())?;
// run1.avi, run1.csv, run1.json; frames are encoded on a writing thread through a queue of 64
let mut rec = Recorder::create_with_queue("run1.avi", &mut cam, 64)?;
for _ in 0..450 {
    rec.write(&cam.read()?)?;
}
rec.close()?; // writes the frames still queued
println!("{} frames, {} dropped by the full queue", rec.frames(), rec.dropped_frames());

let mut cam = Device::open_recording("run1.avi")?; // the one line that changes
loop {
    let frame = match cam.read() {
        Ok(f) => f,
        Err(SdkError::EndOfRecording(_)) => break,
        Err(e) => return Err(e),
    };
    println!("frame {} recorded at {} ns", frame.index, frame.host_ns); // its number in the file, the CSV's time
}
```

- Files: `<name>.avi` (both eyes side by side, left eye in the left half, 8-bit gray, one JPEG per frame),
  `<name>.csv` (`frame,host_ns,driver_ns,driver_clock`) and `<name>.json` (the mode, the camera's layout and
  left/right order, control values at the start and end, crate version, frame count, late / missed frame
  estimates). The field names and their meaning are Python's.
- `capture_info(&mut device)` gives the top of that JSON for any capture — mode, camera, control values, crate
  version, time — and `write_capture_info(path, &mut device, extra)` writes it with fields of your own, as the
  Python package's `capture_info` and `write_capture_info` do (the same field names; a test compares them with the
  Python function's). The `save_stereo_pairs` and `capture_dataset` examples write it as `capture_info.json`. Run
  without a camera only (generated frames); not run with the camera.
- Rust writes and plays MJPG `.avi` only. Lossless FFV1 (`.mkv`) and logging control changes (`note_control`)
  are Python only. The calibration file list in the JSON is `null` here.
- `write()` encodes the two decoded eyes to JPEG again, as Python does: pixel values change a little.
  `Recorder::create` encodes before `write()` returns; `Recorder::create_with_queue(path, &mut cam, n)` hands
  each frame to a writing thread through a queue of `n` frames, as Python's `Recorder(queue_frames=n)`: a frame
  that finds the queue full is dropped and counted (`dropped_frames()`, the JSON's `dropped_frames`; the
  estimated missed frames include them), an error on the writing thread comes back from the next `write()` or
  from `close()`, and `close()` writes the frames still queued first. `pause()` / `resume()` leave frames out;
  the JSON lists each break, and the late / missed estimates do not count a pause. The recorder does not
  replace a recording of the same name (an error), and one `.avi` stops at 2 GiB (`write()` returns an error;
  the file plays; record on to a new one).
- Playback: a frame carries the `host_ns` and driver time recorded in the CSV and its number in the file as
  `index`; after the last frame `read()` gives `SdkError::EndOfRecording`. `Device::playback()` seeks (`seek`,
  `seek_seconds`), loops (`set_loop`), paces at the recorded intervals (`set_real_time`, `set_speed`), pauses
  (`set_paused`: the same frame again) and gives `status` (playing, paused, ended), `frame_count`, `duration_s`,
  `position` and the JSON (`info`). A recording has no controls. A recording that was
  not closed, or a cut-off file, plays the frames it holds whole.
- Rust recording and playback have been run with the camera once (one PC, one camera, 2026-11-25): `record_video`
  10 s, 881 frames, estimated missed 5, dropped by the write queue 0; Rust and Python played the same file back as
  881 frames. Otherwise generated frames, and files the Python package wrote from generated frames with
  opencv-python 4.14 on Windows.

## Calibration file

`calib::resolve()` and `Device::calibration()` read the calibration file installed for your camera by the rules of
the Python package's `calib.resolve()` and `Device.calibration`: the same folder (`calib::store_dir()`:
`%APPDATA%\opticmix\calib` on Windows, `$XDG_CONFIG_HOME/opticmix/calib` or `~/.config/opticmix/calib` elsewhere),
the file used only when exactly one is installed, the same checks of the file and the same messages.
`Device::calibration()` also checks the file's eye size against the opened mode (half the frame width × the frame
height). Values: per eye the model (KB4, as OpenCV's `cv::fisheye`), K, D (k1..k4) and the eye size; R and t (mm)
from the left eye to the right, the baseline; the file's `device` block (`serial()`) and `meta`;
`calib::installed_file()` gives the file's path. Available with or without `capture` (`Device::calibration()` needs
`capture`).

```rust
let c = opticmix_vision::calib::resolve()?;
println!("K {:?} D {:?} baseline {:.2} mm", c.left.k, c.left.d, c.baseline_mm());
let px = c.left.project([100.0, -20.0, 400.0]); // left-eye coordinates (mm) -> Some([u, v])
let ray = c.left.unproject([320.0, 240.0]); // left-eye pixel -> Some(unit ray)
```

- It reads files only: install the file for your camera with the Python package,
  `python -m opticmix_vision.calib add <file.json>`. To read a file without Python: `calib::load(path)`.
- `meta` is a `serde_json::Value` (the crate re-exports `serde_json`).
- Points: `Intrinsics::project` (a point in the eye's camera coordinates, X right, Y down, Z forward, to its
  pixel) and `Intrinsics::unproject` (a pixel to the unit ray), by the KB4 model with the file's K and D, up to
  92.5° from the optical axis (`None` beyond, and for a point at the origin or not finite). The pixel is not
  checked against the eye size. A point behind the eye (Z < 0) within 92.5° gets its own pixel, where OpenCV's
  `cv::fisheye::projectPoints`, which divides by Z, gives the pixel of -p. Compared with OpenCV's
  `cv2.fisheye.projectPoints` and `cv2.fisheye.undistortPoints` on made-up values, without a camera.
- Not in this crate: rectification, depth. The values follow OpenCV's `cv::fisheye` convention.
- Installed file, not matched to this camera (no USB serial): the camera reports no serial number, so another
  unit's file loads without an error. `python -m opticmix_vision.calib check` measures whether a file fits.
- Run without a camera only (made-up calibration files); not run with the camera.

## Notes

Camera writes: set controls once when you open the camera. Every write goes through a guard
(minimum gap between commands, a running count with warnings).

The camera reports no serial number: with two cameras of the same name connected, opening by name fails with
`MultipleMatches`, and on Windows so does `controls()` of a camera opened by index (controls find the camera by
name). Open one by `DeviceSelector::UsbLocation` instead: its controls find the camera at the same USB location. Picking a camera by USB location has been run with the camera once (one PC, one camera, 2026-11-25): with one camera connected, the list showed its USB location, and given that location it opened at 1600x800 MJPG and read one frame; a location no device has was refused (Python DirectShow, Rust Media Foundation); not run with two cameras.

## Getting help

Open an issue at https://github.com/Opticmix/opticmix-vision/issues. To describe your system, run
`pip install opticmix-vision` and paste the output of `python -m opticmix_vision.doctor` (version 0.0.5
or newer; it does not open the camera), or write "Rust only".

## License

Apache-2.0.

With the `capture` feature (on by default) the crate depends on `jpeg-encoder`, which encodes the frames
`Recorder` writes. Its license is `(MIT OR Apache-2.0) AND IJG`: its forward DCT is based on the Independent
JPEG Group's code, whose terms ask that the documentation of a program distributed as executable code say "this
software is based in part on the work of the Independent JPEG Group". Without `capture`
(`default-features = false`, as the Python package's extension is built) `jpeg-encoder` is not a dependency.
