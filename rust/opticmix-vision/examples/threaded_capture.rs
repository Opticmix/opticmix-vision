//! threaded_capture — read frames on one thread, process them on another, always the latest frame.
//!
//! ```text
//! cargo run --release --example threaded_capture                       # 300 processed frames, 20 ms of work each
//! cargo run --release --example threaded_capture -- --work-ms 5        # lighter processing
//! cargo run --release --features test-support --example threaded_capture -- --synthetic   # no camera
//! ```
//!
//! `Device` is `Send`: it moves into a reader thread, which calls `read()` in a loop and puts each frame
//! into a slot that holds one frame; a frame the processing thread has not taken yet is replaced by the
//! newer one. Processing therefore never falls behind the camera — it skips frames instead. At the end the
//! example prints how many frames were processed and how many were skipped (gaps in `frame.index`), and
//! beside them the reader's `Device` statistics, returned when its thread is joined: `late_frames`,
//! `estimated_missed_frames` and `actual_fps`. Those come from host times and count frames the reader itself
//! got late or not at all — not the frames processing skipped. Not run with the camera.
//! `--work-ms` stands in for your own processing (here: a sleep plus the mean of each eye).
//!
//! Output with `--synthetic` (generated frames; with the camera the numbers differ):
//!
//! ```text
//! negotiated: 1600x800 MJPG 90.0 fps (synthetic)
//! processed 300 frames, skipped 255 (frames read while the previous one was still being processed)
//! reader thread: late_frames 0, estimated_missed_frames 0, actual_fps 90.2 (frames the reader itself got late or not at all)
//! ```
//!
//! Camera: the same calls (open, then `read()` from a thread other than the one that opened the camera)
//! were run on Windows on one camera in an internal check; this file has not been run with the camera yet.
//! Writes to the camera: none.

mod common;

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::time::Duration;

use opticmix_vision::StereoFrame;

const USAGE: &str = "usage: threaded_capture [--frames N] [--work-ms MS] [--synthetic]";

fn main() -> std::process::ExitCode {
    let a = common::Args::parse(USAGE, &["--frames", "--work-ms", "--synthetic"]);
    let frames: u64 = a.value("--frames", 300);
    let work_ms: f64 = a.value("--work-ms", 20.0);
    if frames < 1 || !(0.0..=10_000.0).contains(&work_ms) {
        a.fail("--frames needs at least 1, --work-ms 0..10000");
    }
    common::report(run(a.flag("--synthetic"), frames, work_ms))
}

fn mean(d: &[u8]) -> f64 {
    d.iter().map(|&v| f64::from(v)).sum::<f64>() / d.len() as f64
}

fn run(synthetic: bool, frames: u64, work_ms: f64) -> Result<(), Box<dyn std::error::Error>> {
    let mut cam = common::open_camera(synthetic)?;
    println!("negotiated: {}", common::mode_line(&cam.mode()));

    let latest = Arc::new((Mutex::new(None::<StereoFrame>), Condvar::new()));
    let stop = Arc::new(AtomicBool::new(false));
    let reader = {
        let (latest, stop) = (latest.clone(), stop.clone());
        std::thread::spawn(move || -> opticmix_vision::Result<(u64, u64, f64)> {
            while !stop.load(Ordering::Relaxed) {
                let f = cam.read()?; // the camera now belongs to this thread
                *latest.0.lock().unwrap() = Some(f); // replaces a frame nobody has taken yet
                latest.1.notify_one();
            }
            // the reader's own statistics; the camera closes here, on the reader thread
            Ok((cam.late_frames(), cam.estimated_missed_frames(), cam.actual_fps()))
        })
    };

    let (mut processed, mut skipped, mut last_index) = (0u64, 0u64, None);
    while processed < frames {
        let (slot, ready) = &*latest;
        let taken = ready
            .wait_timeout_while(slot.lock().unwrap(), Duration::from_secs(1), |f| {
                f.is_none()
            })
            .unwrap()
            .0
            .take();
        let Some(f) = taken else {
            if reader.is_finished() {
                break; // the reader stopped on an error; join() below returns it
            }
            continue;
        };
        if let Some(last) = last_index {
            skipped += f.index - last - 1;
        }
        last_index = Some(f.index);
        std::thread::sleep(Duration::from_secs_f64(work_ms / 1000.0)); // your processing goes here
        let _ = (mean(&f.left.data), mean(&f.right.data));
        processed += 1;
    }
    stop.store(true, Ordering::Relaxed);
    let (late, missed, fps) = reader.join().map_err(|_| "the reader thread panicked")??;

    println!("processed {processed} frames, skipped {skipped} (frames read while the previous one was still being processed)");
    println!("reader thread: late_frames {late}, estimated_missed_frames {missed}, actual_fps {fps:.1} (frames the reader itself got late or not at all)");
    Ok(())
}
