//! export_recording — write the frames of a recording out as left/right image pairs, with the recorded frame times.
//!
//! ```text
//! cargo run --release --example export_recording -- recording.avi                         # every frame, to recording_frames/
//! cargo run --release --example export_recording -- run1.avi --out run1_pairs --every 10  # every 10th frame
//! cargo run --release --example export_recording -- run1.avi --start 300 --count 50       # 50 frames from frame 300
//! cargo run --release --features test-support --example export_recording -- --synthetic   # no camera
//! ```
//!
//! It reads an MJPG `.avi` recording (the `record_video` example of this crate or of the Python package) through
//! `Device::open_recording` and writes into `--out` (default: the video's name + `_frames`), a folder that must be
//! new or empty — nothing is overwritten:
//!
//! ```text
//! left_000123.pgm, right_000123.pgm   one pair per exported frame (123 = the frame's number in the recording)
//! pairs.csv                            index,host_ns,driver_ns,driver_clock,left,right — the times recorded in the
//!                                      recording's .csv
//! capture_info.json                    a copy of the recording's .json
//! ```
//!
//! The images are 8-bit gray PGM, as in the `save_stereo_pairs` example (no image crate); the Python example
//! writes PNG and, with `--npz`, an `.npz`. They hold what the MJPG decodes to, not the camera's pixel values.
//! The Python package and this crate decode the same MJPG recording with different JPEG decoders, so their images
//! can differ by up to 1 in a pixel value (the largest difference seen on two recordings of generated frames, one
//! written by each; 1 to 2 % of the pixels differed). A recording without its `.csv` still plays, but has no
//! recorded times: `host_ns` in `pairs.csv` is then frame number / the file's frame rate and the driver columns are
//! empty; the example prints a line saying so.
//! The frames to export are counted from the recording (one per row of its .csv, from `--start`, every
//! `--every`-th, at most `--count`); the last line gives the number exported and the recording's frame count, and
//! the example ends with an error when it wrote another number. `--synthetic` first records 30 generated frames
//! (not measured data) to `synthetic.avi`, or the file named first, then exports them.
//!
//! Output with `--synthetic` (generated frames; with the camera the numbers differ):
//!
//! ```text
//! recorded 30 generated frames to synthetic.avi
//! synthetic.avi: 30 frames, 1600x800 MJPG 90.0 fps (synthetic)
//! exported 30 of 30 frames from synthetic.avi to synthetic_frames
//! ```
//!
//! Camera: does not open one. Run with generated recordings only; not run with a camera recording.

mod common;

use std::io::Write;
use std::path::{Path, PathBuf};

use opticmix_vision::{Device, Recorder, SdkError};

const USAGE: &str =
    "usage: export_recording FILE.avi [--out DIR] [--every N] [--start N] [--count N] [--synthetic]";
const SYNTHETIC_FRAMES: usize = 30;

fn main() -> std::process::ExitCode {
    let a = common::Args::parse(USAGE, &["--out", "--every", "--start", "--count", "--synthetic"]);
    let synthetic = a.flag("--synthetic");
    let video = match std::env::args().nth(1).filter(|p| !p.starts_with("--")) {
        Some(p) => PathBuf::from(p),
        None if synthetic => PathBuf::from("synthetic.avi"),
        None => a.fail("give the recording first: export_recording FILE.avi"),
    };
    let every: usize = a.value("--every", 1);
    if every == 0 {
        a.fail("--every needs at least 1");
    }
    let mut name = video.file_stem().unwrap_or_default().to_os_string();
    name.push("_frames");
    let out: PathBuf = a.value("--out", video.with_file_name(name));
    if out.is_file() || std::fs::read_dir(&out).is_ok_and(|mut d| d.next().is_some()) {
        a.fail(&format!("{} is not empty: give a new folder with --out (nothing is overwritten)", out.display()));
    }
    let pick = (a.value("--start", 0), every, a.value("--count", 0));
    common::report(run(&video, &out, synthetic, pick))
}

fn run(
    video: &Path,
    out: &Path,
    synthetic: bool,
    (start, every, count): (usize, usize, usize),
) -> Result<(), Box<dyn std::error::Error>> {
    if synthetic {
        let mut cam = common::open_camera(true)?;
        let mut rec = Recorder::create(video, &mut cam)?; // refuses a recording that is there
        for _ in 0..SYNTHETIC_FRAMES {
            let f = cam.read()?;
            rec.write(&f)?;
        }
        rec.close()?;
        println!("recorded {} generated frames to {}", rec.frames(), video.display());
    }
    let mut cam = Device::open_recording(video)?;
    let mode = common::mode_line(&cam.mode());
    let pb = cam.playback().expect("a recording");
    let n = pb.frame_count();
    println!("{}: {n} frames, {mode}", video.display());
    if !video.with_extension("csv").is_file() {
        println!(
            "no .csv next to the file: host_ns in pairs.csv is frame number / frame rate, not recorded times; \
             driver_ns and driver_clock are empty"
        );
    }
    if start >= n {
        return Err(format!("--start {start}: the recording has {n} frames (0..{})", n - 1).into());
    }
    let mut want = (start..n).step_by(every).count();
    if count > 0 {
        want = want.min(count);
    }
    pb.seek(start);
    std::fs::create_dir_all(out)?;
    let json = video.with_extension("json");
    if json.is_file() {
        std::fs::copy(&json, out.join("capture_info.json"))?;
    }
    let mut csv = std::io::BufWriter::new(std::fs::File::create(out.join("pairs.csv"))?);
    writeln!(csv, "index,host_ns,driver_ns,driver_clock,left,right")?;
    let mut saved = 0;
    while saved < want {
        let f = match cam.read() {
            Ok(f) => f,
            Err(SdkError::EndOfRecording(_)) => break,
            Err(e) => return Err(e.into()),
        };
        let i = f.index as usize;
        if !(i - start).is_multiple_of(every) {
            continue;
        }
        let (l, r) = (format!("left_{i:06}.pgm"), format!("right_{i:06}.pgm"));
        common::write_pgm(&out.join(&l), &f.left)?;
        common::write_pgm(&out.join(&r), &f.right)?;
        // the driver time as the CSV has it, also for a clock this crate does not name
        let (d, c) = cam.playback().expect("a recording").recorded_driver(i);
        let d = d.map_or(String::new(), |d| d.to_string());
        writeln!(csv, "{i},{},{d},{},{l},{r}", f.host_ns, c.unwrap_or(""))?;
        saved += 1;
    }
    csv.flush()?;
    println!("exported {saved} of {n} frames from {} to {}", video.display(), out.display());
    if saved != want {
        return Err(format!("counted {want} frames to export from the recording, wrote {saved}").into());
    }
    Ok(())
}
