//! measure_fps — open the camera, read the mode it negotiated, and measure the frame rate you actually get.
//!
//! ```text
//! cargo run --release --example measure_fps                        # 450 frames
//! cargo run --release --example measure_fps -- --frames 2700       # a longer run
//! cargo run --release --example measure_fps -- --csv frames.csv    # also one row per frame read
//! cargo run --release --features test-support --example measure_fps -- --synthetic   # no camera
//! ```
//!
//! Prints the mode the camera negotiated (read it rather than assuming the one you asked for), then the
//! intervals between frames: mean, min, max, the rate they add up to, how many came late, how many frames
//! repeated the previous image byte for byte (per eye, and `Device::repeated_frames`: both eyes, since open,
//! not printed when `stall_frames` is 0), and how many MJPG samples `read()` skipped because they did
//! not decode (`undecodable_frames`). Then the `Device` statistics since open, which use the negotiated rate
//! rather than the median: `late_frames` (intervals over 1.5 frame periods), `estimated_missed_frames`
//! (round(interval / period) - 1 for each) and `actual_fps` (over the last 30 intervals) — the same names and
//! rule as the Python package's `Device` statistics; they include the first frame, which the figures above
//! leave out. They come from host times, so a read loop slower than the frame period counts as late and missed
//! frames too; when frames are missed the example prints what to try. `--synthetic` gives generated frames
//! paced in software, not measured data.
//! Build with `--release`: in a debug build, decoding the frames is much slower than the camera sends them.
//!
//! `--csv FILE` (off by default) also writes one row per frame `read()` returned, the first one too
//! (`index,host_ns,left_same,right_same,left_max,right_max`: each eye byte-identical to the frame before, 0 on the
//! first row, and its largest pixel value), and prints the negotiated fps and `actual_fps` in full. When no sample
//! was skipped as undecodable (the example prints how many did not decode), the `Device` statistics can be counted
//! again from the file: the `Device` also counts the times of skipped samples, which have no row. Run with the
//! camera in two USB re-insert cycles (one PC, one camera, 2026-11-26), from this release's source before release.
//!
//! Times are `host_ns`: this process's monotonic clock right after each frame was read, not a camera
//! timestamp, so they include the host's scheduling jitter. Where the capture driver puts its own time stamp on
//! each frame (`driver_ns`: Windows Media Foundation sample time, Linux V4L2 buffer time), the same interval
//! figures are printed for it, the first timed frame's driver time (not the start of streaming: about
//! 364,400 s on one PC with one camera, 2026-11-24, once; zero not established — use differences), and
//! how much later than the earliest one each frame reached this process (`host_ns` - `driver_ns`, less its
//! smallest value — the two clocks have different zero points). Linux also
//! prints the driver's sequence numbers, the frames missing between them (`driver_missed_frames`) and where the
//! driver says it takes its time stamp (`driver_time_source`: start of exposure or end of frame). These are
//! operating-system driver times, not a camera clock; whether they mark the exposure or the arrival of a frame
//! is not known. This example has been run with the camera once on Windows (one PC, one camera,
//! 2026-11-24); on Linux once (one PC, one camera, 2026-11-25, crate 0.0.6).
//!
//! Stall check: if one eye sends a byte-identical image `stall_frames` times in a row (default 90),
//! `read()` returns an error naming the eye — on the units we have tested one eye's stream has
//! occasionally stopped, and re-inserting the USB cable brought it back. This example prints it and exits
//! with code 2. `DeviceConfig { stall_frames: 0, ..DeviceConfig::weevia_s1() }` turns the check off.
//!
//! Output with `--synthetic` (generated frames; with the camera the numbers differ):
//!
//! ```text
//! negotiated: 1600x800 MJPG 90.0 fps (synthetic)
//! 450 frames, 449 intervals (host-measured)
//! interval ms: mean 11.11  min 9.09  max 13.10  -> 90.0 frames per second
//! intervals over 1.5x the median: 0
//! frames identical to the previous one: left 0, right 0
//! frames identical in both eyes (Device::repeated_frames): 0
//! MJPG samples skipped because they did not decode: 0
//! since open, at the negotiated rate: late_frames 0, estimated_missed_frames 0, actual_fps 89.9
//! driver time stamps: none from this capture backend (synthetic)
//! driver sequence numbers: none from this capture backend
//! ```
//!
//! Camera: the same calls (open, mode, reading and timing 300 frames) were run on Windows on one camera in
//! an internal check; this file has been run with the camera once (one PC, one camera, 2026-11-24). The
//! `Device::repeated_frames` line came later: run with the camera once (one PC, one camera, 2026-11-25, Media
//! Foundation, 450 frames): 0, the same as the `measure_fps` example's own per-eye counts (left 0, right 0). A
//! count above 0: generated frames only.
//! Writes to the camera: none.

mod common;

const USAGE: &str = "usage: measure_fps [--frames N] [--csv FILE] [--synthetic]";

fn main() -> std::process::ExitCode {
    let a = common::Args::parse(USAGE, &["--frames", "--csv", "--synthetic"]);
    let frames: usize = a.value("--frames", 450);
    if frames < 2 {
        a.fail("--frames needs at least 2");
    }
    let csv = a.flag("--csv").then(|| std::path::PathBuf::from(a.value("--csv", String::new())));
    common::report(run(a.flag("--synthetic"), frames, csv))
}

/// One `--csv` row: index, host_ns, left / right byte-identical to the frame before (0 on the first), left / right
/// largest pixel value.
type Row = (u64, u64, bool, bool, u8, u8);

fn row(f: &opticmix_vision::StereoFrame, same: (bool, bool)) -> Row {
    let max = |g: &opticmix_vision::GrayImage| g.data.iter().copied().max().unwrap_or(0);
    (f.index, f.host_ns, same.0, same.1, max(&f.left), max(&f.right))
}

fn run(synthetic: bool, frames: usize, csv: Option<std::path::PathBuf>) -> Result<(), Box<dyn std::error::Error>> {
    let mut cam = common::open_camera(synthetic)?;
    let mode = cam.mode();
    println!("negotiated: {}", common::mode_line(&mode));
    let mut prev = cam.read()?; // the first frame carries start-up delay; time from the next one
    // --csv keeps every frame read() returned, the first one too: the rows the Device statistics counted
    let mut rows: Vec<Row> = Vec::with_capacity(if csv.is_some() { frames + 1 } else { 0 });
    if csv.is_some() {
        rows.push(row(&prev, (false, false)));
    }
    let mut got = Vec::with_capacity(frames);
    let (mut same_left, mut same_right) = (0, 0);
    for _ in 0..frames {
        let f = cam.read()?;
        let same = (f.left == prev.left, f.right == prev.right);
        if csv.is_some() {
            rows.push(row(&f, same));
        }
        same_left += usize::from(same.0);
        same_right += usize::from(same.1);
        got.push((
            f.host_ns as i64,
            f.driver_ns,
            f.driver_clock,
            f.driver_sequence,
            f.driver_time_source,
        ));
        prev = f;
    }

    let host: Vec<i64> = got.iter().map(|g| g.0).collect();
    println!(
        "{} frames, {} intervals (host-measured)",
        host.len(),
        host.len() - 1
    );
    let (mean, min, max, late) = intervals(&host);
    println!(
        "interval ms: mean {mean:.2}  min {min:.2}  max {max:.2}  -> {:.1} frames per second",
        1000.0 / mean
    );
    println!("intervals over 1.5x the median: {late}");
    println!("frames identical to the previous one: left {same_left}, right {same_right}");
    if let Some(both) = cam.repeated_frames() {
        println!("frames identical in both eyes (Device::repeated_frames): {both}");
    }
    println!(
        "MJPG samples skipped because they did not decode: {}",
        cam.undecodable_frames()
    );
    println!(
        "since open, at the negotiated rate: late_frames {}, estimated_missed_frames {}, actual_fps {:.1}",
        cam.late_frames(),
        cam.estimated_missed_frames(),
        cam.actual_fps()
    );
    if let Some(path) = &csv {
        // the rate and actual_fps as the statistics used them (shortest text that reads back to the same f64)
        println!("negotiated fps (full precision): {}", mode.fps);
        println!("actual_fps since open (full precision): {}", cam.actual_fps());
        let mut text = String::from("index,host_ns,left_same,right_same,left_max,right_max\n");
        for (i, t, l, r, lm, rm) in &rows {
            text += &format!("{i},{t},{},{},{lm},{rm}\n", u8::from(*l), u8::from(*r));
        }
        std::fs::write(path, text)?;
        println!("per-frame CSV: {} ({} rows)", path.display(), rows.len());
    }
    if cam.estimated_missed_frames() > 0 {
        println!(
            "Frames were late or missed. Host times cannot tell a slow read loop from frames lost on the camera \
             side: build with --release, connect the camera directly to a USB 3 port, and do your processing on \
             another thread (examples/threaded_capture.rs)."
        );
    }

    // Driver time stamps, when every frame has one on the same clock.
    let driver: Option<Vec<i64>> = got
        .iter()
        .map(|g| g.1.filter(|_| g.2 == got[0].2))
        .collect();
    match (driver, got[0].2) {
        (Some(driver), Some(clock)) => {
            let (mean, min, max, late) = intervals(&driver);
            println!("driver time stamps ({clock:?}): interval ms: mean {mean:.2}  min {min:.2}  max {max:.2}");
            println!("driver time stamps: intervals over 1.5x the median: {late}");
            println!(
                "driver time stamps: first timed frame at {:.6} s on that clock",
                driver[0] as f64 / 1e9
            );
            if let Some(src) = got[0].4 {
                println!("driver time stamps: taken at {src:?}, as the driver reports it");
            }
            let mut delay: Vec<i64> = host.iter().zip(&driver).map(|(h, d)| h - d).collect();
            delay.sort_unstable();
            let ms = |ns: i64| (ns - delay[0]) as f64 / 1e6;
            println!(
                "host_ns - driver_ns, above its smallest value, ms: median {:.2}  95th percentile {:.2}  max {:.2}",
                ms(delay[delay.len() / 2]),
                ms(delay[delay.len() * 95 / 100]),
                ms(delay[delay.len() - 1])
            );
        }
        _ => println!(
            "driver time stamps: none from this capture backend ({})",
            mode.backend_name
        ),
    }
    match (got.iter().find_map(|g| g.3), got.iter().rev().find_map(|g| g.3)) {
        (Some(first), Some(last)) => println!(
            "driver sequence numbers: {first} to {last}; frames missing since the first frame read: {}",
            cam.driver_missed_frames().unwrap_or(0)
        ),
        _ => println!("driver sequence numbers: none from this capture backend"),
    }
    Ok(())
}

/// Intervals between `stamps` (ns): mean, min and max in ms, and how many are over 1.5x the median.
fn intervals(stamps: &[i64]) -> (f64, f64, f64, usize) {
    let mut dt: Vec<f64> = stamps
        .windows(2)
        .map(|w| (w[1] - w[0]) as f64 / 1e6)
        .collect();
    let mean = dt.iter().sum::<f64>() / dt.len() as f64;
    dt.sort_by(f64::total_cmp);
    let median = dt[dt.len() / 2];
    (
        mean,
        dt[0],
        dt[dt.len() - 1],
        dt.iter().filter(|&&d| d > 1.5 * median).count(),
    )
}
