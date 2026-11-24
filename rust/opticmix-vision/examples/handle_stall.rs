//! handle_stall — when `read()` fails: close the camera, ask for the USB cable to be re-inserted, wait,
//! open it again; give up after N tries.
//!
//! ```text
//! cargo run --release --example handle_stall                       # read 900 frames, up to 3 reopens
//! cargo run --release --example handle_stall -- --retries 5 --wait 10
//! cargo run --release --features test-support --example handle_stall -- --synthetic --wait 0
//! ```
//!
//! `read()` returns `SdkError::Device` when a frame cannot be read (camera unplugged; on Windows a read has no timeout — if the camera stops sending, `read()` does not return) and when
//! one eye has sent the byte-identical image `stall_frames` times in a row (default 90, `--stall-frames`)
//! — on the units we have tested one eye's stream has occasionally stopped, and re-inserting the USB cable
//! brought it back. Opening fails with `SdkError::NoMatch` while the cable is out.
//!
//! On each of those errors this example prints the message, closes the camera (drops the `Device`), asks
//! you to re-insert the USB cable, waits `--wait` seconds and opens a new `Device`. After `--retries`
//! failed reopens it stops with exit code 2. Use a new `Device` for every attempt: one `Device` keeps the
//! stall count of the stream it was reading.
//!
//! `--synthetic` uses generated frames (not a camera) where one eye repeats one image after 10 frames on
//! the first open only, so the error and the reopen run without hardware.
//!
//! Output with `--synthetic` (generated frames):
//!
//! ```text
//! Camera error after 99 frames: The right eye has sent the same image for 90 frames in a row: its stream has stopped. ...
//! Camera closed. If an eye stopped or the camera is gone, re-insert the USB cable now: ... (reopen 1 of 3).
//! read 900 frames, 1 reopen(s)
//! ```
//!
//! Camera: not run with the camera yet. Writes to the camera: none.

mod common;

use std::time::Duration;

use opticmix_vision::{DeviceConfig, SdkError};

const USAGE: &str =
    "usage: handle_stall [--frames N] [--retries N] [--wait SECONDS] [--stall-frames N] [--synthetic]";

fn main() -> std::process::ExitCode {
    let a = common::Args::parse(
        USAGE,
        &[
            "--frames",
            "--retries",
            "--wait",
            "--stall-frames",
            "--synthetic",
        ],
    );
    let frames: u64 = a.value("--frames", 900);
    let retries: u32 = a.value("--retries", 3);
    let wait: f64 = a.value("--wait", 10.0);
    let stall_frames: u32 = a.value("--stall-frames", 90);
    if frames == 0 || !(0.0..=3600.0).contains(&wait) || stall_frames == 1 {
        a.fail("--frames at least 1, --wait 0-3600, --stall-frames 0 (off) or 2 or more");
    }
    let cfg = DeviceConfig {
        stall_frames,
        ..DeviceConfig::weevia_s1()
    };
    common::report(run(a.flag("--synthetic"), cfg, frames, retries, wait))
}

fn run(
    synthetic: bool,
    cfg: DeviceConfig,
    frames: u64,
    retries: u32,
    wait: f64,
) -> Result<(), Box<dyn std::error::Error>> {
    let (mut got, mut reopens) = (0u64, 0u32);
    while got < frames {
        // A new Device each attempt. --synthetic: one eye freezes after 10 frames, on the first open only.
        let freeze_after = (reopens == 0).then_some(10);
        let attempt =
            common::open_camera_with(cfg.clone(), synthetic, freeze_after).and_then(|mut cam| {
                while got < frames {
                    cam.read()?; // your processing goes here
                    got += 1;
                }
                Ok(())
            }); // the camera is closed here, whatever happened
        match attempt {
            Ok(()) => {}
            Err(e @ (SdkError::Device(_) | SdkError::NoMatch(_))) => {
                eprintln!("Camera error after {got} frames: {e}");
                if reopens >= retries {
                    return Err(format!(
                        "gave up after {reopens} reopen(s). Re-insert the USB cable and run again."
                    )
                    .into());
                }
                reopens += 1;
                println!(
                    "Camera closed. If an eye stopped or the camera is gone, re-insert the USB cable now: \
                     unplug it and plug it back in. Reopening in {wait} s (reopen {reopens} of {retries})."
                );
                std::thread::sleep(Duration::from_secs_f64(wait));
            }
            Err(e) => return Err(e.into()), // anything else is not fixed by re-inserting the cable
        }
    }
    println!("read {got} frames, {reopens} reopen(s)");
    Ok(())
}
