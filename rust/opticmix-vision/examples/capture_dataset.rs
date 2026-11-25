//! capture_dataset — collect N stereo frames in memory, then write them as left/right image pairs with a CSV of
//! their times and the capture info, for training or analysis.
//!
//! ```text
//! cargo run --release --example capture_dataset                                  # 100 frames into stereo_dataset/
//! cargo run --release --example capture_dataset -- --frames 300 --out take1
//! cargo run --release --features test-support --example capture_dataset -- --synthetic --frames 20   # no camera
//! ```
//!
//! The Rust version of the Python `capture_dataset_npz.py`, without the `.npz` (writing one would need a zip crate,
//! which this crate does not depend on). `--out` (default `stereo_dataset`) must be a new or empty folder — nothing
//! is overwritten:
//!
//! ```text
//! left_000123.pgm, right_000123.pgm   one pair per frame (123 = frame index in this run), 8-bit gray PGM, as
//!                                      captured (no calibration or rectification)
//! pairs.csv                            index,host_ns,left,right — the columns of the save_stereo_pairs example
//! capture_info.json                    recording::write_capture_info: the negotiated mode, the camera's layout and
//!                                      left/right order, every control value, the crate version — and under
//!                                      "pgm_pairs" the CSV's name, the frame count, one eye's size and the late /
//!                                      estimated missed frames, as the Python example's .json has them under "npz"
//! ```
//!
//! `host_ns` is this process's monotonic clock right after the frame was read — not a timestamp from the camera.
//! Frames are kept in memory until the end, as in the Python example, so that writing files does not slow the read
//! loop: at 800x800 per eye that is 1.28 MB a frame, so 100 frames take about 128 MB. The capture info is read
//! before the camera is closed; the files are written after. `--synthetic` gives generated frames, not measured
//! data.
//!
//! Output with `--synthetic` (generated frames; with the camera the numbers differ):
//!
//! ```text
//! negotiated: 1600x800 MJPG 90.0 fps (synthetic)
//! saved 100 frames of 800x800 per eye to stereo_dataset (1.10 s from first to last frame, host-measured)
//! ```
//!
//! Camera: run without a camera only (generated frames); not run with the camera. Writes to the camera: none.

mod common;

use std::io::Write;
use std::path::{Path, PathBuf};

use opticmix_vision::serde_json::{json, Map};
use opticmix_vision::write_capture_info;

const USAGE: &str = "usage: capture_dataset [--frames N] [--out DIR] [--synthetic]";

fn main() -> std::process::ExitCode {
    let a = common::Args::parse(USAGE, &["--frames", "--out", "--synthetic"]);
    let frames: usize = a.value("--frames", 100);
    if frames == 0 {
        a.fail("--frames needs at least 1");
    }
    let out: PathBuf = a.value("--out", PathBuf::from("stereo_dataset"));
    if out.is_file() || std::fs::read_dir(&out).is_ok_and(|mut d| d.next().is_some()) {
        a.fail(&format!("{} is not empty: give a new folder with --out (nothing is overwritten)", out.display()));
    }
    common::report(run(a.flag("--synthetic"), frames, &out))
}

fn run(synthetic: bool, frames: usize, out: &Path) -> Result<(), Box<dyn std::error::Error>> {
    std::fs::create_dir_all(out)?;
    let mut cam = common::open_camera(synthetic)?;
    println!("negotiated: {}", common::mode_line(&cam.mode()));
    let mut got = Vec::with_capacity(frames);
    for _ in 0..frames {
        got.push(cam.read()?);
    }
    let (w, h) = got[0].size();
    let mut pairs = Map::new();
    pairs.insert(
        "pgm_pairs".into(),
        json!({"csv": "pairs.csv", "frames": frames, "eye_size": [w, h], "late_frames": cam.late_frames(),
               "estimated_missed_frames": cam.estimated_missed_frames()}),
    );
    write_capture_info(out.join("capture_info.json"), &mut cam, pairs)?; // reads the controls; writes none
    drop(cam);

    let mut csv = std::io::BufWriter::new(std::fs::File::create(out.join("pairs.csv"))?);
    writeln!(csv, "index,host_ns,left,right")?;
    for f in &got {
        let (l, r) = (format!("left_{:06}.pgm", f.index), format!("right_{:06}.pgm", f.index));
        common::write_pgm(&out.join(&l), &f.left)?;
        common::write_pgm(&out.join(&r), &f.right)?;
        writeln!(csv, "{},{},{l},{r}", f.index, f.host_ns)?;
    }
    csv.flush()?;
    let span = (got[frames - 1].host_ns - got[0].host_ns) as f64 / 1e9;
    println!(
        "saved {frames} frames of {w}x{h} per eye to {} ({span:.2} s from first to last frame, host-measured)",
        out.display()
    );
    Ok(())
}
