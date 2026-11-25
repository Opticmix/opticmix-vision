//! calibration_info — print the installed calibration: K, D, eye size and the optical axis's pixel per eye, R, t and
//! the baseline (mm); then one point projected into both eyes and unprojected back.
//!
//! ```text
//! cargo run --example calibration_info                    # the file installed with `python -m opticmix_vision.calib add`
//! cargo run --example calibration_info -- --synthetic     # no calibration needed: a made-up one in a temporary folder
//! ```
//!
//! Reads the calibration with `calib::resolve()` — the same file `Device::calibration()` returns, which also checks
//! that the eye size matches the mode the camera opened in. It does not open the camera. With no file installed, or
//! more than one, it prints the crate's message as is and exits with code 1. The Rust version of the Python example
//! `calibration_info.py`; files are installed with the Python package (`python -m opticmix_vision.calib add`).
//!
//! The crate cannot check that a file belongs to the camera you opened — the camera does not send a USB serial
//! number, so another unit's file loads without an error: installed file, not matched to this camera (no USB serial).
//!
//! `--synthetic` writes an ideal fisheye (made-up values, not a measured calibration) into a temporary folder and
//! points this process's calibration folder there (`APPDATA` on Windows, `XDG_CONFIG_HOME` elsewhere); your
//! calibration folder is not touched.
//!
//! Run without a camera only (made-up calibration files); not run with the camera.
//! Writes to the camera: none.

use std::path::Path;
use std::process::ExitCode;

use opticmix_vision::calib;

const USAGE: &str = "usage: calibration_info [--synthetic]";

/// Installs a made-up calibration under `folder` and makes `calib::store_dir()` point there.
fn install_synthetic(folder: &Path) -> std::io::Result<()> {
    let var = if cfg!(windows) {
        "APPDATA"
    } else {
        "XDG_CONFIG_HOME"
    };
    std::env::set_var(var, folder); // every calib function now uses the temporary folder
    let f = 400.0 / 90f64.to_radians(); // ideal equidistant fisheye, 180° over 800 px: made-up, not this camera
    let eye = serde_json::json!({"model": "KB4", "K": [[f, 0.0, 399.5], [0.0, f, 399.5], [0.0, 0.0, 1.0]],
                                 "D": [0.0, 0.0, 0.0, 0.0], "size": [800, 800]});
    let c = serde_json::json!({
        "schema": calib::SCHEMA_NAME, "schema_version": calib::SCHEMA_VERSION, "units": "mm",
        "device": {"model": "synthetic", "serial": "S1-0000"}, "left": eye, "right": eye,
        "extrinsics": {"R": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]], "t": [-50.0, 0.0, 0.0], // made-up
                       "convention": calib::CONVENTION},
        "meta": {}});
    let store = folder.join("opticmix").join("calib");
    std::fs::create_dir_all(&store)?;
    std::fs::write(store.join("S1-0000.json"), c.to_string())
}

fn matrix(m: &[[f64; 3]; 3]) -> String {
    m.iter()
        .map(|r| format!("  [{:9.4} {:9.4} {:9.4}]", r[0], r[1], r[2]))
        .collect::<Vec<_>>()
        .join("\n")
}

fn row(v: &[f64]) -> String {
    format!(
        "[{}]",
        v.iter()
            .map(|x| format!("{x:.4}"))
            .collect::<Vec<_>>()
            .join(" ")
    )
}

fn main() -> ExitCode {
    let mut synthetic = false;
    for a in std::env::args().skip(1) {
        match a.as_str() {
            "--synthetic" => synthetic = true,
            "--help" | "-h" => {
                println!("{USAGE}");
                return ExitCode::SUCCESS;
            }
            other => {
                eprintln!("unknown option {other}\n{USAGE}");
                return ExitCode::from(2);
            }
        }
    }
    let tmp =
        std::env::temp_dir().join(format!("opticmix_calibration_info_{}", std::process::id()));
    if synthetic {
        if let Err(e) = install_synthetic(&tmp) {
            eprintln!(
                "could not write the made-up calibration under {}: {e}",
                tmp.display()
            );
            return ExitCode::FAILURE;
        }
        println!("synthetic: made-up values, not a camera");
    }
    let read = || -> opticmix_vision::Result<_> {
        println!("calibration folder: {}", calib::store_dir()?.display());
        let path = calib::installed_file()?;
        Ok((calib::load(&path)?, path))
    };
    let got = read();
    let _ = std::fs::remove_dir_all(&tmp);
    let (c, path) = match got {
        Ok(v) => v,
        Err(e) => {
            eprintln!("{e}");
            return ExitCode::FAILURE;
        }
    };

    println!("file: {}", path.display());
    println!("      installed file, not matched to this camera (no USB serial)");
    let device: Vec<String> = c
        .device
        .iter()
        .map(|(k, v)| format!("'{k}': '{v}'"))
        .collect();
    println!("device: {{{}}}", device.join(", "));
    for (name, eye) in [("left", &c.left), ("right", &c.right)] {
        println!(
            "{name}: model {}, eye size {}x{}",
            eye.model, eye.size.0, eye.size.1
        );
        println!("  K =\n{}", matrix(&eye.k));
        println!("  D (k1..k4) = {}", row(&eye.d));
        if let Some(uv) = eye.project([0.0, 0.0, 1.0]) {
            println!("  optical axis -> pixel {}", row(&uv)); // Intrinsics::project; unproject goes back
        }
    }
    println!("R (left -> right) =\n{}", matrix(&c.extrinsics.r));
    println!("t (mm) = {}", row(&c.extrinsics.t));
    println!("baseline: {:.2} mm", c.baseline_mm());

    // A point off the optical axis, in the left eye's coordinates (mm), and the same point in the right eye's by the
    // file's convention: its pixel in each eye (Intrinsics::project), and that pixel back to a ray (unproject), which
    // is the direction of the point.
    let p = [100.0, -20.0, 400.0];
    let (r, t) = (&c.extrinsics.r, &c.extrinsics.t);
    let q: [f64; 3] =
        std::array::from_fn(|i| r[i][0] * p[0] + r[i][1] * p[1] + r[i][2] * p[2] + t[i]);
    println!(
        "a point in the left eye, and in the right eye by {}:",
        calib::CONVENTION
    );
    for (name, eye, pt) in [("left", &c.left, p), ("right", &c.right, q)] {
        let n = pt.iter().map(|v| v * v).sum::<f64>().sqrt();
        match eye.project(pt) {
            Some(uv) => println!(
                "  {name:<5} {} mm -> pixel {} -> unproject {} (direction {})",
                row(&pt),
                row(&uv),
                eye.unproject(uv).map_or("none".into(), |ray| row(&ray)),
                row(&pt.map(|v| v / n))
            ),
            None => println!(
                "  {name:<5} {} mm: more than 92.5 degrees from the optical axis",
                row(&pt)
            ),
        }
    }
    ExitCode::SUCCESS
}
