//! play_recording — read a recording back through `Device`, as if it were the camera.
//!
//! ```text
//! cargo run --release --example play_recording -- recording.avi                 # as fast as it decodes
//! cargo run --release --example play_recording -- recording.avi --real-time     # at the recorded pace
//! cargo run --release --example play_recording -- recording.avi --speed 0.5     # half speed (implies --real-time)
//! cargo run --release --example play_recording -- recording.avi --start 120     # from frame 120
//! cargo run --release --example play_recording -- recording.avi --loop --frames 1000
//! ```
//!
//! It plays an MJPG `.avi` this crate's `Recorder` wrote or the Python package's `Recorder` wrote (the
//! `record_video` example of either); an FFV1 `.mkv` plays in the Python package only. The same loop you write for
//! the camera works on the file: `Device::open_recording` instead of `Device::open`, `cam.read()` gives `left`
//! and `right`, and after the last frame `read()` gives `SdkError::EndOfRecording`. A frame's `index` is its
//! number in the file; `host_ns` and the driver time are those recorded in the CSV. It prints the first frames
//! and, at the end, the late / estimated missed frames over the frames read (from the recorded times).
//!
//! Output of `cargo run --release --features test-support --example record_video -- --synthetic --seconds 1
//! --out run1.avi` and then `cargo run --release --features test-support --example play_recording -- run1.avi`
//! on one PC (Windows 11, 2026-11-25; generated frames, not the camera — other PCs and the camera give other
//! numbers):
//!
//! ```text
//! run1.avi: 1600x800 MJPG 90.0 fps (synthetic), 91 frames, 1.00 s, closed
//! frame 0: host_ns 100 driver_ns - left mean 174.7 right mean 99.4
//! frame 1: host_ns 12197900 driver_ns - left mean 172.9 right mean 107.4
//! frame 2: host_ns 22711100 driver_ns - left mean 174.7 right mean 99.4
//! read 91 frames; late 0, estimated missed 0
//! ```
//!
//! Camera: does not open one. Run on recordings of generated frames only; not run on a recording made with the
//! camera.

mod common;

use opticmix_vision::{Device, GrayImage, SdkError};

const USAGE: &str =
    "usage: play_recording FILE.avi [--real-time] [--speed X] [--start N] [--loop] [--frames N]";

fn main() -> std::process::ExitCode {
    let a = common::Args::parse(
        USAGE,
        &["--real-time", "--speed", "--start", "--loop", "--frames"],
    );
    let Some(path) = std::env::args().nth(1).filter(|p| !p.starts_with("--")) else {
        a.fail("give the recording first: play_recording FILE.avi")
    };
    let speed: f64 = a.value("--speed", 0.0);
    let start: usize = a.value("--start", 0);
    let frames: u64 = a.value("--frames", u64::MAX);
    let opts = (a.flag("--real-time") || speed > 0.0, speed, start, a.flag("--loop"), frames);
    common::report(run(&path, opts))
}

fn mean(g: &GrayImage) -> f64 {
    g.data.iter().map(|&v| v as f64).sum::<f64>() / g.data.len().max(1) as f64
}

fn run(
    path: &str,
    (real_time, speed, start, looping, frames): (bool, f64, usize, bool, u64),
) -> Result<(), Box<dyn std::error::Error>> {
    let mut cam = Device::open_recording(path)?;
    let mode = common::mode_line(&cam.mode());
    let pb = cam.playback().expect("a recording");
    let closed = match pb.closed() {
        Some(true) => "closed",
        Some(false) => "not closed (the process stopped while recording)",
        None => "no JSON",
    };
    println!("{path}: {mode}, {} frames, {:.2} s, {closed}", pb.frame_count(), pb.duration_s());
    pb.set_real_time(real_time);
    if speed > 0.0 {
        pb.set_speed(speed)?;
    }
    pb.set_loop(looping);
    pb.seek(start);
    let mut n = 0;
    while n < frames {
        let f = match cam.read() {
            Ok(f) => f,
            Err(SdkError::EndOfRecording(_)) => break,
            Err(e) => return Err(e.into()),
        };
        if n < 3 {
            let driver = f.driver_ns.map_or("-".to_string(), |d| d.to_string());
            println!(
                "frame {}: host_ns {} driver_ns {driver} left mean {:.1} right mean {:.1}",
                f.index,
                f.host_ns,
                mean(&f.left),
                mean(&f.right)
            );
        }
        n += 1;
    }
    println!(
        "read {n} frames; late {}, estimated missed {}",
        cam.late_frames(),
        cam.estimated_missed_frames()
    );
    Ok(())
}
