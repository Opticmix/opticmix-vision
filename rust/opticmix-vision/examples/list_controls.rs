//! list_controls — print every control the camera advertises: current value, range, default. Reads only.
//!
//! ```text
//! cargo run --release --example list_controls
//! cargo run --release --features test-support --example list_controls -- --synthetic   # no camera: made-up ranges
//! ```
//!
//! Ranges and defaults come from the device, not from this crate. The "effect" column is how far the
//! control's effect on the image has been verified: verified / partial / unverified / none_expected_mono.
//! Exposure is in the driver's units (the "units" column), not converted to time.
//!
//! Output with `--synthetic` (simulated controls with made-up ranges, generated frames):
//!
//! ```text
//! controls backend: sim (simulated controls — ranges and defaults are made up, not measured)
//! name                    current  range               default  units           effect
//! exposure                -5       -11..-3 step 1      -5       log2_s_nominal  verified
//! ...
//! control writes by this process: 0
//! ```
//!
//! Camera: the same calls (open, then read every control while streaming) were run on Windows on one
//! camera in an internal check; on Windows this file has not been run with the camera yet; on Linux it read the
//! controls once (one PC, one camera, 2026-11-25, crate 0.0.6).
//! Writes to the camera: none — the last line prints this process's control-write count to show it.

mod common;

use opticmix_vision::{guard, UserValue};

const USAGE: &str = "usage: list_controls [--synthetic]";

fn main() -> std::process::ExitCode {
    let a = common::Args::parse(USAGE, &["--synthetic"]);
    common::report(run(a.flag("--synthetic")))
}

fn show(v: &Option<UserValue>) -> String {
    match v {
        None => "-".into(),
        Some(UserValue::Int(i)) => i.to_string(),
        Some(UserValue::Bool(b)) => b.to_string(),
        Some(UserValue::Float(x)) => x.to_string(),
        Some(UserValue::Choice(s)) => s.clone(),
    }
}

fn run(synthetic: bool) -> Result<(), Box<dyn std::error::Error>> {
    let mut cam = common::open_camera(synthetic)?;
    let c = cam.controls()?;
    let note = c.backend_note();
    println!(
        "controls backend: {}{}",
        c.backend_name(),
        if note.is_empty() {
            String::new()
        } else {
            format!(" ({note})")
        }
    );

    let mut rows =
        vec![["name", "current", "range", "default", "units", "effect"].map(String::from)];
    for i in c.describe_all()? {
        let range = match (&i.choices, i.minimum, i.maximum) {
            (Some(ch), _, _) => ch.join("/"),
            (None, Some(lo), Some(hi)) => format!(
                "{lo}..{hi} step {}",
                i.step.map_or("-".into(), |s| s.to_string())
            ),
            _ => "-".into(),
        };
        rows.push([
            i.name.into(),
            show(&i.current),
            range,
            show(&i.default),
            i.units,
            i.effect.as_str().into(),
        ]);
    }
    let widths: Vec<usize> = (0..6)
        .map(|k| rows.iter().map(|r| r[k].chars().count()).max().unwrap_or(0))
        .collect();
    for r in &rows {
        let line: Vec<String> = r
            .iter()
            .zip(&widths)
            .map(|(cell, w)| format!("{cell:w$}"))
            .collect();
        println!("{}", line.join("  ").trim_end());
    }
    drop(cam); // closes the controls
    println!(
        "control writes by this process: {}",
        guard::write_counts().get("_total").copied().unwrap_or(0)
    );
    Ok(())
}
