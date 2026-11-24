//! set_controls_once — set manual exposure and gain once, at startup, and check what the camera kept.
//!
//! ```text
//! cargo run --release --example set_controls_once                              # exposure -5, gain 28
//! cargo run --release --example set_controls_once -- --exposure -6 --gain 40
//! cargo run --release --features test-support --example set_controls_once -- --synthetic   # no camera: simulated controls
//! ```
//!
//! Writing is risky on this camera. On the units we have tested, a control write has occasionally been
//! followed by one eye's stream stopping until the USB cable was re-inserted. Every write goes through one
//! guard in this crate — equal values are skipped, requests close together collapse to the latest value,
//! writes are spaced at least 12 ms apart — but the guard does not make writes safe. So: write once, at
//! startup, and not in a loop. This example makes one `set_many` call and does not restore the old values
//! afterwards (that would be another write).
//!
//! Exposure is in the driver's units, not converted to time. Windows (DirectShow): log2 seconds, nominal —
//! the default -5 is for Windows. Linux (V4L2): the V4L2 standard's 100 µs unit, so pass `--exposure`;
//! `list_controls` shows the range. A value outside the device's range is refused before anything is sent.
//!
//! Output with `--synthetic` (simulated controls with made-up ranges, generated frames):
//!
//! ```text
//! before: auto_exposure='manual', exposure=-5, gain=32
//! auto_exposure  requested 'manual'  skipped_same readback -         kept -
//! exposure       requested -5        skipped_same readback -         kept -
//! gain           requested 28        written      readback 28        kept yes
//! first frame after the write: mean left 174.7, right 99.4
//! ```
//!
//! Camera: not run with the camera yet — writing through this crate has not been run on the camera.
//! Writes to the camera: one `set_many` call — one write for auto-exposure mode + exposure (they share a
//! UVC property) and one for gain; a value the camera already has is skipped.

mod common;

use std::time::{Duration, Instant};

use opticmix_vision::controls::Status;
use opticmix_vision::UserValue;

const USAGE: &str = "usage: set_controls_once [--exposure N] [--gain N] [--synthetic]";
const NAMES: [&str; 3] = ["auto_exposure", "exposure", "gain"];

fn main() -> std::process::ExitCode {
    let a = common::Args::parse(USAGE, &["--exposure", "--gain", "--synthetic"]);
    let exposure: i64 = a.value("--exposure", -5);
    let gain: i64 = a.value("--gain", 28);
    common::report(run(a.flag("--synthetic"), exposure, gain))
}

fn mean(d: &[u8]) -> f64 {
    d.iter().map(|&v| f64::from(v)).sum::<f64>() / d.len() as f64
}

fn run(synthetic: bool, exposure: i64, gain: i64) -> Result<(), Box<dyn std::error::Error>> {
    let mut cam = common::open_camera(synthetic)?;
    let c = cam.controls()?;
    let mut before = Vec::new();
    for n in NAMES {
        before.push(format!(
            "{n}={}",
            c.get(n)?.map_or("-".into(), |v| v.repr())
        ));
    }
    println!("before: {}", before.join(", "));

    // Write once at startup: one call. auto_exposure goes with exposure — writing exposure while auto
    // exposure is on is refused. set_many does not wait; wait() returns the final results.
    let results = c
        .set_many(&[
            ("auto_exposure".into(), UserValue::Choice("manual".into())),
            ("exposure".into(), UserValue::Int(exposure)),
            ("gain".into(), UserValue::Int(gain)),
        ])
        .map_err(|e| format!("not written: {e}"))?;
    let pending: Vec<u64> = results
        .iter()
        .filter(|r| r.status == Status::Pending)
        .map(|r| r.ticket)
        .collect();
    let mut done: Vec<_> = results
        .into_iter()
        .filter(|r| r.status != Status::Pending)
        .collect();
    done.extend(c.wait(&pending, Some(5.0)));
    let written_at = Instant::now();

    // "written" only means the driver accepted the value. readback is what the camera reports afterwards
    // and kept() says whether it matches — check it before relying on the setting.
    for r in &done {
        println!(
            "{:14} requested {:<9} {:12} readback {:<9} kept {}{}",
            r.name,
            r.requested.repr(),
            r.status.as_str(),
            r.readback.as_ref().map_or("-".into(), |v| v.repr()),
            match r.kept() {
                Some(true) => "yes",
                Some(false) => "no",
                None => "-",
            },
            r.error
                .as_ref()
                .map_or(String::new(), |e| format!("  error: {e}"))
        );
    }

    // Right after an exposure change the two eyes can disagree in brightness for a short time (measured
    // with the earlier Python implementation, not with this crate). Drop those frames before using any.
    while written_at.elapsed() < Duration::from_millis(100) {
        cam.read()?;
    }
    let f = cam.read()?;
    println!(
        "first frame after the write: mean left {:.1}, right {:.1}",
        mean(&f.left.data),
        mean(&f.right.data)
    );
    Ok(())
}
