//! save_stereo_pairs — save left/right image pairs and a CSV of when each frame arrived.
//!
//! ```text
//! cargo run --release --example save_stereo_pairs                         # every 30th frame, 10 pairs
//! cargo run --release --example save_stereo_pairs -- --every 5 --count 3 --out captures
//! cargo run --release --features test-support --example save_stereo_pairs -- --synthetic --every 5 --count 3
//! ```
//!
//! Each run writes into a new folder, `<out>/<seconds since 1970>/`:
//!
//! ```text
//! left_000123.pgm, right_000123.pgm   one pair per saved frame (123 = frame index in this run)
//! pairs.csv                            index,host_ns,left,right
//! ```
//!
//! The images are 8-bit gray PGM (binary, "P5") so that this example needs no image crate; GIMP,
//! ImageMagick and OpenCV (`cv2.imread`) open them. For PNG, encode `frame.left.data` with an image crate.
//! `host_ns` is this process's monotonic clock right after the frame was read — not a timestamp from the
//! camera. `--synthetic` gives generated frames, not measured data.
//!
//! Output with `--synthetic` (generated frames; with the camera the numbers differ):
//!
//! ```text
//! negotiated: 1600x800 MJPG 90.0 fps (synthetic)
//! saved 3 pairs in captures95453132
//! ```
//!
//! Camera: not run with the camera yet. Writes to the camera: none.

mod common;

use std::io::Write;
use std::path::Path;

use opticmix_vision::GrayImage;

const USAGE: &str = "usage: save_stereo_pairs [--every N] [--count N] [--out DIR] [--synthetic]";

fn main() -> std::process::ExitCode {
    let a = common::Args::parse(USAGE, &["--every", "--count", "--out", "--synthetic"]);
    let every: u64 = a.value("--every", 30);
    let count: u32 = a.value("--count", 10);
    if every == 0 || count == 0 {
        a.fail("--every and --count need at least 1");
    }
    let out: String = a.value("--out", "captures".to_string());
    common::report(run(a.flag("--synthetic"), every, count, Path::new(&out)))
}

fn write_pgm(path: &Path, g: &GrayImage) -> std::io::Result<()> {
    let mut f = std::io::BufWriter::new(std::fs::File::create(path)?);
    write!(f, "P5\n{} {}\n255\n", g.width, g.height)?;
    f.write_all(&g.data)?;
    f.flush()
}

fn run(
    synthetic: bool,
    every: u64,
    count: u32,
    out: &Path,
) -> Result<(), Box<dyn std::error::Error>> {
    let stamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)?
        .as_secs();
    let dir = out.join(stamp.to_string());
    std::fs::create_dir_all(&dir)?;
    let mut csv = std::io::BufWriter::new(std::fs::File::create(dir.join("pairs.csv"))?);
    writeln!(csv, "index,host_ns,left,right")?;

    let mut cam = common::open_camera(synthetic)?;
    println!("negotiated: {}", common::mode_line(&cam.mode()));
    let mut saved = 0;
    while saved < count {
        let f = cam.read()?;
        if f.index % every == 0 {
            let (l, r) = (
                format!("left_{:06}.pgm", f.index),
                format!("right_{:06}.pgm", f.index),
            );
            write_pgm(&dir.join(&l), &f.left)?;
            write_pgm(&dir.join(&r), &f.right)?;
            writeln!(csv, "{},{},{l},{r}", f.index, f.host_ns)?;
            saved += 1;
        }
    }
    csv.flush()?;
    println!("saved {saved} pairs in {}", dir.display());
    Ok(())
}
