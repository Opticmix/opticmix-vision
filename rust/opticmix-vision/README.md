# opticmix-vision (Rust)

Rust API for the Weevia S1 stereo-IR camera — **preview**. The API is 0.x and may change.

Status: on Windows 11 this crate has been run on one camera — 1600×800 MJPG at 90 fps through Media
Foundation, 90–92 frames per second in 300-frame runs, fourteen controls read while streaming, no
control written. Not run: Linux, a second unit. Status: partial — not yet verified for release; the
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

Eight programs in `examples/`, each the Rust version of the Python example of the same name. In a checkout
of the repository, run them from `rust/opticmix-vision`:

```sh
cargo run --example list_devices
cargo run --release --example measure_fps
cargo run --release --features test-support --example measure_fps -- --synthetic   # no camera
```

Use `--release` for anything that reads frames: in a debug build, decoding the frames is much slower than
the camera sends them.

| Example | What it does | Writes to the camera | Without a camera | With the camera |
|---|---|---|---|---|
| `list_devices` | Lists the video devices Windows reports and how many are a Weevia S1 | none, does not open it | `-- --synthetic` (a made-up list) | run with the camera once (one PC, one camera, 2026-11-24) |
| `measure_fps` | Opens the camera, prints the negotiated mode, times N frames: interval mean/min/max, late intervals, repeated images, skipped MJPG samples, the `Device` statistics (late, estimated missed, actual fps); where the driver gives them, the same for the driver time stamps, how far each frame trails the earliest (host minus driver time) and frames missing between driver sequence numbers | none | `--features test-support -- --synthetic` | run with the camera once (one PC, one camera, 2026-11-24) |
| `save_stereo_pairs` | Saves every Nth left/right pair as 8-bit PGM, with `pairs.csv` (frame index, host time, file names) | none | `--features test-support -- --synthetic` | not run yet |
| `list_controls` | Prints every control: current value, range, step, default, units, how far its effect is verified | none — prints the write count | `--features test-support -- --synthetic` | same calls run on Windows on one camera¹ |
| `set_controls_once` | Sets manual exposure and gain in one `set_many` at startup, waits, prints the status, the read-back value and whether the camera kept it | one `set_many` call | `--features test-support -- --synthetic` | not run yet |
| `threaded_capture` | Moves the `Device` to a reader thread; the main thread processes the latest frame and counts the skipped ones; the reader's late / estimated missed frames and actual fps come back when its thread is joined | none | `--features test-support -- --synthetic` | same calls run on Windows on one camera¹ (not the statistics line) |
| `led_level` | Sets the IR illuminator level once at startup and compares the image before and after (mean, share of pixels changed) | one write (none if already at that level) | `--features test-support -- --synthetic` | not run yet |
| `handle_stall` | When `read()` fails or an eye stalls: closes the camera, asks for the USB cable to be re-inserted, reopens, gives up after N tries | none | `--features test-support -- --synthetic --wait 0` | not run yet |

¹ Opening the camera, reading and timing 300 frames, reading from a thread other than the one that
opened the camera, and reading every control while streaming were run on Windows 11 on one camera in an
internal check of this crate. Of the example files, `list_devices` and `measure_fps` have been run with the camera once (one PC, one camera, 2026-11-24); the others not yet.
Writing through this crate has not been run on the camera. Linux: not run.

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

Counts and rates on `Device`, each for a different thing:

| Method | Counts |
|---|---|
| `undecodable_frames()` (also `dropped_frames()`, its earlier name) | MJPG samples that arrived but did not decode, which `read()` skipped. Not in `estimated_missed_frames()`: these samples did arrive. For frames that never arrived use `estimated_missed_frames()` (Windows and Linux) or `driver_missed_frames()` (Linux) |
| `driver_missed_frames()` | Frames missing between the driver's sequence numbers since the first sample read (0, 1, 2, 5, 6 gives 2). V4L2 calls these dropped frames: received but not stored for lack of a free buffer; its documentation says frames dropped on the camera side of the USB link may not be counted. `None` on Windows |
| `late_frames()` | Samples since open received by `read()` more than 1.5 frame periods (at `mode().fps`) after the one before, from `host_ns`. Windows and Linux. A late frame can be host delivery jitter rather than a lost frame, and a `read()` loop slower than the frame period counts here (and in `estimated_missed_frames()`) the same as a frame lost on the camera side — the two cannot be told apart. If it is not 0: build with `--release`, connect the camera directly to a USB 3 port, and move processing to another thread (`threaded_capture`) |
| `estimated_missed_frames()` | For each late interval, round(interval / period) - 1 (half to even), from `host_ns`. Windows and Linux. An estimate from host times, where `driver_missed_frames()` (Linux) counts the driver's own numbers — the two can differ |
| `actual_fps()` | Samples per second over the last 30 intervals, from `host_ns`; 0.0 until two samples |
| `index` on each frame | Frames delivered by this `Device`, from 0 — no gaps |

`late_frames()`, `estimated_missed_frames()` and `actual_fps()` have the names and the rule of the Python
package's `Device.late_frames`, `estimated_missed_frames` and `actual_fps`; a test runs the Python definition on
the same host times and compares the numbers. Every sample counts, including those `read_raw()` returns and
those `read()` skips as undecodable (Python counts the frames its `read()` returns). They are not read from a
device frame counter. `measure_fps` and `threaded_capture` print them, and `measure_fps` says what to try when frames were missed. `measure_fps`: run with the camera once (one PC, one camera, 2026-11-24); `threaded_capture`: run without a camera only
(made-up host times and generated frames).

The Python package's `StereoFrame` has the same `driver_ns`, `driver_clock` and `driver_sequence` fields
(through OpenCV; see its API reference). They are not the same times on Windows: OpenCV asks Media Foundation
to convert the MJPG to RGB and passes on the converted sample's time. Windows driver times: run with the camera once (one PC, one camera, 2026-11-24): this crate's `measure_fps` gave driver-time intervals from 0.01 to 16.90 ms (mean 11.23) and 0 of 450 repeated images; the Python package through OpenCV's Media Foundation backend gave intervals of 11.11 ms (mean, min and max) with 276 of 900 frames per eye repeating the previous image — there `driver_ns` does not show whether a new image arrived; the cause has not been established. Linux driver times, sequence numbers and `driver_missed_frames`: run without a camera only (made-up driver
reports); not run with the camera.

## Platforms

| | Windows | Linux |
|---|---|---|
| Capture | Media Foundation | V4L2 — implemented, not yet run on the camera |
| Controls | DirectShow | V4L2 — implemented, not yet run on the camera |

Windows: run on one camera (above). Linux: not run.

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
  in the order OpenCV's `CAP_MSMF` counts device indexes; activates no device; empty off Windows. Not run on
  Windows yet.
- Presets (every control saved to a file and loaded back): Python only for now — `opticmix_vision.presets`.

## Notes

Camera writes: set controls once when you open the camera. Every write goes through a guard
(minimum gap between commands, a running count with warnings).

The camera reports no serial number: with two cameras of the same name connected, `controls()` fails
with `MultipleMatches` on Windows (controls find the camera by name).

## Getting help

Open an issue at https://github.com/Opticmix/opticmix-vision/issues. To describe your system, run
`pip install opticmix-vision` and paste the output of `python -m opticmix_vision.doctor` (version 0.0.5
or newer; it does not open the camera), or write "Rust only".

## License

Apache-2.0.
