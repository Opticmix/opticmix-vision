//! record_video — record the two eyes to an MJPG `.avi` with the CSV of frame times and the JSON of what was
//! recording, in the Python package's format (`python examples/play_recording.py recording.avi` plays it).
//!
//! ```text
//! cargo run --release --example record_video                                  # 5 s to recording.avi
//! cargo run --release --example record_video -- --seconds 30 --out run1.avi
//! cargo run --release --example record_video -- --queue-frames 0              # encode before the next read
//! cargo run --release --features test-support --example record_video -- --synthetic --seconds 3
//! ```
//!
//! Run it with `--release`: a debug build encodes and decodes each frame many times slower. The frames are
//! encoded on a writing thread (`Recorder::create_with_queue`, a queue of `--queue-frames` frames, default 64;
//! the Python example's default is 0), so a read does not wait for the encoder. A frame that finds the queue full
//! is dropped and counted. When reading or encoding is too slow, the late, estimated missed and dropped frames on
//! the last line go up (from host times; the estimated missed frames include the dropped ones). The recorder does
//! not replace a recording: give a new `--out` each run. `--synthetic` gives generated frames, not measured data.
//!
//! Output of `cargo run --release --features test-support --example record_video -- --synthetic --seconds 3
//! --out run1.avi` on one PC (Windows 11, 2026-11-25; generated frames, not the camera — other PCs and the
//! camera give other numbers):
//!
//! ```text
//! negotiated: 1600x800 MJPG 90.0 fps (synthetic)
//! run1.avi: 271 frames, with run1.csv and run1.json
//! late 0, estimated missed 0 (of which dropped by the write queue 0)
//! ```
//!
//! Camera: not run with the camera yet. Writes to the camera: none (it reads every control for the JSON).

mod common;

use std::path::PathBuf;
use std::time::{Duration, Instant};

use opticmix_vision::Recorder;

const USAGE: &str = "usage: record_video [--seconds S] [--out FILE.avi] [--queue-frames N] [--synthetic]";

fn main() -> std::process::ExitCode {
    let a = common::Args::parse(USAGE, &["--seconds", "--out", "--queue-frames", "--synthetic"]);
    let seconds: f64 = a.value("--seconds", 5.0);
    if !seconds.is_finite() || seconds <= 0.0 {
        a.fail("--seconds needs a positive number");
    }
    let queue: usize = a.value("--queue-frames", 64);
    let out = PathBuf::from(a.value("--out", "recording.avi".to_string()));
    common::report(run(a.flag("--synthetic"), seconds, queue, out))
}

fn run(synthetic: bool, seconds: f64, queue: usize, out: PathBuf) -> Result<(), Box<dyn std::error::Error>> {
    let mut cam = common::open_camera(synthetic)?;
    println!("negotiated: {}", common::mode_line(&cam.mode()));
    let mut rec = Recorder::create_with_queue(&out, &mut cam, queue)?;
    let end = Instant::now() + Duration::from_secs_f64(seconds);
    while Instant::now() < end {
        let frame = cam.read()?;
        rec.write(&frame)?; // with a queue: hands the frame to the writing thread and returns
    }
    rec.close()?; // writes the frames still queued
    println!(
        "{}: {} frames, with {} and {}",
        out.display(),
        rec.frames(),
        out.with_extension("csv").display(),
        out.with_extension("json").display()
    );
    println!(
        "late {}, estimated missed {} (of which dropped by the write queue {})",
        rec.late_frames(),
        rec.estimated_missed_frames(),
        rec.dropped_frames()
    );
    Ok(())
}
