//! quickstart — open the Weevia S1, read frames, save one left/right pair, close it.
//!
//! ```text
//! cargo run --release --example quickstart                                      # Weevia S1, found by name
//! cargo run --release --features test-support --example quickstart -- --synthetic   # no camera
//! ```
//!
//! The steps of the Python `quickstart.py`, without its window (a window needs a GUI crate, which this crate does
//! not depend on): find the camera by name and open it (`Device::open(DeviceConfig::weevia_s1())`), print the mode
//! it negotiated (read it rather than assuming the one you asked for), read 100 frames and print the rate they came
//! at (as `quickstart.py --no-window` does), save the last frame's two eyes as `left_000099.pgm` and
//! `right_000099.pgm` (what the `s` key saves in the Python example, as 8-bit gray PGM rather than PNG, so no image
//! crate is needed), and close the camera (dropping the `Device` closes it). `frame.left` and `frame.right` are the
//! two eyes, already split and in the camera's left/right order. The rate is measured on this process's clock,
//! not the camera's. `--synthetic` gives generated frames, not measured data.
//!
//! Output with `--synthetic` (generated frames; with the camera the numbers differ):
//!
//! ```text
//! negotiated: 1600x800 MJPG 90.0 fps (synthetic)
//! 100 frames in 1.103 s -> 90.7 fps (host-measured)
//! saved left/right_000099.pgm
//! ```
//!
//! Camera: run with the camera once (one PC, one camera, 2026-11-26, from the 0.0.8 source; the example has not
//! changed since). Writes to the camera: none.

mod common;

const USAGE: &str = "usage: quickstart [--synthetic]";
const FRAMES: usize = 100;

fn main() -> std::process::ExitCode {
    let a = common::Args::parse(USAGE, &["--synthetic"]);
    common::report(run(a.flag("--synthetic")))
}

fn run(synthetic: bool) -> Result<(), Box<dyn std::error::Error>> {
    let mut cam = common::open_camera(synthetic)?;
    println!("negotiated: {}", common::mode_line(&cam.mode()));
    let t0 = std::time::Instant::now();
    let mut f = cam.read()?;
    for _ in 1..FRAMES {
        f = cam.read()?;
    }
    let dt = t0.elapsed().as_secs_f64();
    println!("{FRAMES} frames in {dt:.3} s -> {:.1} fps (host-measured)", FRAMES as f64 / dt);
    for (eye, img) in [("left", &f.left), ("right", &f.right)] {
        common::write_pgm(std::path::Path::new(&format!("{eye}_{:06}.pgm", f.index)), img)?;
    }
    println!("saved left/right_{:06}.pgm", f.index);
    drop(cam); // closes the camera; the end of `run` would too
    Ok(())
}
