//! led_level — set the IR illuminator level once, at startup, and compare the image before and after.
//!
//! ```text
//! cargo run --release --example led_level                    # full level (1.0 = 96)
//! cargo run --release --example led_level -- --level 0       # off
//! cargo run --release --features test-support --example led_level -- --synthetic   # no camera
//! ```
//!
//! The illuminator is the UVC backlight-compensation control (`LedControl`): off up to 16, light from 24,
//! ceiling 96 — measured with the earlier Python implementation. `--level` 0.0–1.0 maps onto 0–96, rounded
//! to a whole number.
//!
//! The readback is only what the driver accepted — it is not evidence that the light changed. The image
//! is. Frame mean is a weak check here: the illuminator lights part of the field of view, so a lit patch
//! can brighten a lot while the mean barely moves. This example prints both the mean and the share of
//! pixels that changed by more than 10 (of 255). The image also changes without any write (the scene can
//! move), so when the level is already the requested one the example writes nothing and says so — the
//! changes it prints then are not the illuminator. With `--synthetic` the generated image does not respond
//! to the level at all.
//!
//! Output with `--synthetic` (simulated control, generated frames):
//!
//! ```text
//! illuminator: 32 -> requested 96, written, readback 96
//! left  eye: mean  173.8 ->  173.8   pixels changed by >10:   0.0 %
//! right eye: mean  103.4 ->  103.4   pixels changed by >10:   0.0 %
//! ```
//!
//! Camera: not run with the camera yet — writing through this crate has not been run on the camera.
//! Writes to the camera: one write (none if the illuminator is already at that level).

mod common;

use opticmix_vision::Device;

const USAGE: &str = "usage: led_level [--level 0.0-1.0] [--frames N] [--settle N] [--synthetic]";

fn main() -> std::process::ExitCode {
    let a = common::Args::parse(USAGE, &["--level", "--frames", "--settle", "--synthetic"]);
    let level: f64 = a.value("--level", 1.0);
    let frames: usize = a.value("--frames", 10);
    // How long the light takes to change has not been measured — raise this if "after" looks unchanged.
    let settle: usize = a.value("--settle", 10);
    if !(0.0..=1.0).contains(&level) || frames == 0 {
        a.fail("--level is 0.0-1.0, --frames at least 1");
    }
    common::report(run(a.flag("--synthetic"), level, frames, settle))
}

/// Mean image of each eye over `n` frames (averaging takes out sensor noise).
fn average(cam: &mut Device, n: usize) -> opticmix_vision::Result<[Vec<f32>; 2]> {
    let mut sum: [Vec<f32>; 2] = [Vec::new(), Vec::new()];
    for _ in 0..n {
        let f = cam.read()?;
        for (s, eye) in sum.iter_mut().zip([&f.left.data, &f.right.data]) {
            s.resize(eye.len(), 0.0);
            s.iter_mut().zip(eye).for_each(|(s, &v)| *s += f32::from(v));
        }
    }
    Ok(sum.map(|s| s.into_iter().map(|v| v / n as f32).collect()))
}

fn mean(d: &[f32]) -> f32 {
    d.iter().sum::<f32>() / d.len() as f32
}

fn run(
    synthetic: bool,
    level: f64,
    frames: usize,
    settle: usize,
) -> Result<(), Box<dyn std::error::Error>> {
    let mut cam = common::open_camera(synthetic)?;
    let led = cam.led()?;
    let was = led.get()?;
    for _ in 0..settle {
        cam.read()?; // frames right after opening are still settling
    }
    let before = average(&mut cam, frames)?;

    // Write once at startup: one write, and set_level waits for it (rounded to a whole number).
    let r = led.set_level(level)?;
    let now = led.get()?;
    for _ in 0..settle {
        cam.read()?;
    }
    let after = average(&mut cam, frames)?;

    let requested = (96.0 * level).round_ties_even() as i64;
    println!(
        "illuminator: {was} -> requested {requested}, {}, readback {now}",
        r.status.as_str()
    );
    if was == requested {
        println!("no write (already {was}) — the changes below are not the illuminator");
    }
    for (eye, (b, f)) in ["left", "right"].iter().zip(before.iter().zip(&after)) {
        let changed = b
            .iter()
            .zip(f)
            .filter(|(b, f)| (*f - *b).abs() > 10.0)
            .count();
        println!(
            "{eye:5} eye: mean {:6.1} -> {:6.1}   pixels changed by >10: {:5.1} %",
            mean(b),
            mean(f),
            100.0 * changed as f32 / b.len() as f32
        );
    }
    Ok(())
}
