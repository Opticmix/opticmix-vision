//! presets — save every camera control to a JSON file, and show what loading a preset would change. Reads only:
//! applying a preset to the camera is not in the Rust crate yet (Python: `python -m opticmix_vision.presets load`).
//!
//! ```text
//! cargo run --release --example presets                              # every control into my_preset.json
//! cargo run --release --example presets -- --plan my_preset.json     # what loading it would change; writes nothing
//! cargo run --release --example presets -- --list                    # the presets in the preset folder; no camera
//! cargo run --release --features test-support --example presets -- --synthetic   # no camera: simulated controls
//! ```
//!
//! The steps of `presets.py` that do not write: saving (`Controls::save_preset`) and its `--load FILE --dry-run`
//! (`Controls::plan_preset`). The files are the Python package's: this example reads presets the Python package
//! saved (`python -m opticmix_vision.presets save NAME` puts them in the folder `--list` shows), and the Python
//! package reads the ones it saves. The whole preset is checked first: another camera model, a control this camera
//! lacks, other units or a value out of range stop it with the Python package's message.
//!
//! Output with `--synthetic` (simulated controls with made-up ranges, generated frames):
//!
//! ```text
//! $ presets --synthetic
//! saved 14 controls (synthetic) to my_preset.json
//! control writes by this process: 0
//! $ presets --synthetic --plan my_preset.json
//! the camera already has every value in the preset
//! control writes by this process: 0
//! ```
//!
//! Each `--synthetic` run starts from the same simulated values, so the preset just saved changes nothing. A preset
//! that differs prints one line per control (`name  current -> preset`), then what loading would leave alone.
//!
//! Camera: run without a camera only (simulated controls, preset files written by the Python package); not run
//! with the camera. Writes to the camera: none — the last line prints this process's control-write count.

mod common;

use std::path::Path;

use opticmix_vision::{guard, presets, UserValue};

const USAGE: &str = "usage: presets [--synthetic] [--file FILE | --plan FILE | --list]";

fn main() -> std::process::ExitCode {
    let a = common::Args::parse(USAGE, &["--synthetic", "--file", "--plan", "--list"]);
    if a.flag("--list") {
        return common::report(list());
    }
    let plan: String = a.value("--plan", String::new());
    let file: String = a.value("--file", "my_preset.json".into());
    common::report(run(a.flag("--synthetic"), &file, (!plan.is_empty()).then_some(plan.as_str())))
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

/// The preset folder and what is in it, as `python -m opticmix_vision.presets list --all` prints it. Opens nothing.
fn list() -> Result<(), Box<dyn std::error::Error>> {
    println!("folder: {}", presets::store_dir()?.display());
    let found = presets::list_presets(None)?;
    if found.is_empty() {
        println!("  (no presets)");
    }
    for i in found {
        println!("  {:20} {}  {}  {}", i.name, i.model.as_deref().unwrap_or("unknown model"), i.saved_at, i.note);
    }
    Ok(())
}

fn run(synthetic: bool, file: &str, plan: Option<&str>) -> Result<(), Box<dyn std::error::Error>> {
    let mut cam = common::open_camera(synthetic)?;
    let c = cam.controls()?;
    match plan {
        None => {
            let path = c.save_preset(Path::new(file), None, Some("saved by examples/presets.rs"))?; // reads only
            let model = c.model().unwrap_or("model unknown");
            println!("saved {} controls ({model}) to {}", c.snapshot()?.len(), path.display());
        }
        Some(p) => {
            let plan = c.plan_preset(Path::new(p), false, false)?; // the whole preset is checked; writes nothing
            for (name, current, preset) in &plan.changes {
                println!("{name:24} {:>8} -> {}", show(current), show(&Some(preset.clone())));
            }
            if plan.changes.is_empty() {
                println!("the camera already has every value in the preset");
            }
            if !plan.left_out.is_empty() {
                println!("left to auto: {}", plan.left_out.join(", "));
            }
            if !plan.skipped_mono.is_empty() {
                println!("no effect on the mono sensor, would not be written: {}", plan.skipped_mono.join(", "));
            }
        }
    }
    drop(cam); // closes the controls
    println!(
        "control writes by this process: {}",
        guard::write_counts().get("_total").copied().unwrap_or(0)
    );
    Ok(())
}
