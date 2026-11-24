//! list_devices — list the video devices Windows reports, and how many of them are a Weevia S1.
//!
//! ```text
//! cargo run --example list_devices                    # the devices connected now (Windows)
//! cargo run --example list_devices -- --synthetic     # no camera: a made-up list with two Weevia S1
//! ```
//!
//! Prints every video device in enumeration order — laptop webcams included — and marks the ones whose
//! name contains the Weevia S1's USB name. The index is the order Windows reports, and it shifts when
//! another camera is plugged in or unplugged; that is why `DeviceConfig::weevia_s1()` finds the camera by
//! name. Without ffmpeg on the PATH the list comes from DirectShow directly.
//!
//! The camera reports no USB serial number, so two Weevia S1 have the same name and cannot be told apart:
//! with two connected, opening by name stops with `MultipleMatches`. Using two units on one computer has
//! not been tested — connect one at a time.
//!
//! Linux and other systems: this listing is for Windows — the example says so and exits; list the cameras
//! with `v4l2-ctl --list-devices`. On Linux `DeviceConfig::weevia_s1()` finds the camera by its V4L2 card name.
//! `--synthetic` works everywhere.
//!
//! Output with `--synthetic` (a made-up list):
//!
//! ```text
//! 3 video device(s):
//!   [0] WN Dual Camera   <- Weevia S1
//!   [1] Example Laptop Webcam
//!   [2] WN Dual Camera   <- Weevia S1
//! Weevia S1 ("WN Dual Camera") found: 2
//! ```
//!
//! Camera: run with the camera once (one PC, one camera, 2026-11-24). Does not open the camera.
//! Writes to the camera: none.

use opticmix_vision::devices::list_devices;
use opticmix_vision::spec::WEEVIA_S1_NAME;

fn main() {
    let synthetic = match std::env::args().nth(1).as_deref() {
        None => false,
        Some("--synthetic") => true,
        Some(other) => {
            eprintln!("unknown option {other}\nusage: list_devices [--synthetic]");
            std::process::exit(2);
        }
    };
    if !synthetic && !cfg!(windows) {
        println!(
            "This listing is for Windows. On Linux: v4l2-ctl --list-devices (package v4l-utils). \
             DeviceConfig::weevia_s1() finds the camera by its V4L2 card name, {WEEVIA_S1_NAME:?}."
        );
        return;
    }
    // A made-up device list in ffmpeg's DirectShow format — not read from any computer.
    let made_up = format!(
        "[dshow @ 0] \"{WEEVIA_S1_NAME}\" (video)\n\
         [dshow @ 0] \"Example Laptop Webcam\" (video)\n\
         [dshow @ 0] \"{WEEVIA_S1_NAME}\" (video)\n\
         [dshow @ 0] \"Microphone (Example Audio)\" (audio)\n"
    );
    let devices = list_devices(synthetic.then_some(made_up.as_str()));
    let is_weevia = |name: &str| name.to_lowercase().contains(&WEEVIA_S1_NAME.to_lowercase());

    println!("{} video device(s):", devices.len());
    for d in &devices {
        let mark = if is_weevia(&d.name) {
            "   <- Weevia S1"
        } else {
            ""
        };
        println!("  [{}] {}{mark}", d.index, d.name);
    }
    let found = devices.iter().filter(|d| is_weevia(&d.name)).count();
    println!("Weevia S1 ({WEEVIA_S1_NAME:?}) found: {found}");
    if found == 0 {
        println!("Re-insert the cable; if the camera is missing from the list, Windows does not see it either.");
    } else if found > 1 {
        println!(
            "More than one Weevia S1: the camera reports no USB serial number, so they cannot be told apart \
             by name, and opening by name stops with MultipleMatches instead of guessing. \
             Using two units on one computer has not been tested — connect one at a time."
        );
    }
}
