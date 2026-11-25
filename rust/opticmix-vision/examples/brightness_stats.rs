//! brightness_stats — per-eye brightness over N frames: mean, min, max and the share of saturated pixels.
//!
//! ```text
//! cargo run --release --example brightness_stats                          # 90 frames into brightness.csv
//! cargo run --release --example brightness_stats -- --frames 900 --out run.csv
//! cargo run --release --features test-support --example brightness_stats -- --synthetic   # no camera
//! ```
//!
//! One CSV row per frame — the columns of the Python `brightness_stats.py`: `index`, `host_ns`, and for each eye the
//! mean, min and max pixel value and the fraction of pixels at 255 (saturated, 8-bit): `left_mean`, `left_min`,
//! `left_max`, `left_saturated`, then the same for `right`. A summary over all frames is printed at the end, in the
//! Python example's form. Useful to compare lighting or exposure settings; this example only reads — it does not
//! change a setting. `--synthetic` gives generated frames, not measured data: their brightness says nothing about
//! the camera.
//!
//! Output with `--synthetic` (generated frames; with the camera the numbers differ):
//!
//! ```text
//! negotiated: 1600x800 MJPG 90.0 fps (synthetic)
//! 90 frames -> brightness.csv
//! left : mean 173.8 (frames 172.9..174.7)  min 0  max 255  saturated 0.326 % of pixels
//! right: mean 103.4 (frames 99.4..107.4)  min 0  max 207  saturated 0.000 % of pixels
//! ```
//!
//! Camera: run without a camera only (generated frames); not run with the camera. Writes to the camera: none.

mod common;

use std::io::Write;

use opticmix_vision::GrayImage;

const USAGE: &str = "usage: brightness_stats [--frames N] [--out FILE.csv] [--synthetic]";

fn main() -> std::process::ExitCode {
    let a = common::Args::parse(USAGE, &["--frames", "--out", "--synthetic"]);
    let frames: usize = a.value("--frames", 90);
    if frames == 0 {
        a.fail("--frames needs at least 1");
    }
    let out: String = a.value("--out", "brightness.csv".to_string());
    common::report(run(a.flag("--synthetic"), frames, &out))
}

/// mean, min, max, fraction of pixels at 255 — as `eye_stats` in the Python example.
fn eye_stats(g: &GrayImage) -> [f64; 4] {
    let n = g.data.len() as f64;
    let sum: u64 = g.data.iter().map(|&p| u64::from(p)).sum();
    let (min, max) = g.data.iter().fold((255u8, 0u8), |(lo, hi), &p| (lo.min(p), hi.max(p)));
    let saturated = g.data.iter().filter(|&&p| p == 255).count() as f64;
    [sum as f64 / n, f64::from(min), f64::from(max), saturated / n]
}

fn run(synthetic: bool, frames: usize, out: &str) -> Result<(), Box<dyn std::error::Error>> {
    let mut rows = Vec::with_capacity(frames);
    let mut cam = common::open_camera(synthetic)?;
    println!("negotiated: {}", common::mode_line(&cam.mode()));
    for _ in 0..frames {
        let f = cam.read()?;
        rows.push((f.index, f.host_ns, eye_stats(&f.left), eye_stats(&f.right)));
    }
    drop(cam);

    let mut csv = std::io::BufWriter::new(std::fs::File::create(out)?);
    writeln!(csv, "index,host_ns,left_mean,left_min,left_max,left_saturated,right_mean,right_min,right_max,right_saturated")?;
    for (index, host_ns, l, r) in &rows {
        // min and max are whole numbers, as the Python example writes them
        writeln!(csv, "{index},{host_ns},{},{},{},{},{},{},{},{}", l[0], l[1], l[2], l[3], r[0], r[1], r[2], r[3])?;
    }
    csv.flush()?;

    println!("{} frames -> {out}", rows.len());
    for (eye, pick) in [("left", 0), ("right", 1)] {
        let v: Vec<[f64; 4]> = rows.iter().map(|r| if pick == 0 { r.2 } else { r.3 }).collect();
        let means: Vec<f64> = v.iter().map(|s| s[0]).collect();
        let mean = means.iter().sum::<f64>() / means.len() as f64;
        let (lo, hi) = means.iter().fold((f64::MAX, f64::MIN), |(lo, hi), &m| (lo.min(m), hi.max(m)));
        let min = v.iter().map(|s| s[1]).fold(f64::MAX, f64::min);
        let max = v.iter().map(|s| s[2]).fold(f64::MIN, f64::max);
        let sat = v.iter().map(|s| s[3]).sum::<f64>() / v.len() as f64;
        println!(
            "{eye:5}: mean {mean:.1} (frames {lo:.1}..{hi:.1})  min {min}  max {max}  saturated {:.3} % of pixels",
            100.0 * sat
        );
    }
    Ok(())
}
