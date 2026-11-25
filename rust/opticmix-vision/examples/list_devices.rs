//! list_devices — list the video devices Windows reports, and how many of them are a Weevia S1.
//!
//! ```text
//! cargo run --example list_devices                                  # DirectShow's list (the default)
//! cargo run --example list_devices -- --backend msmf                # Media Foundation's list
//! cargo run --example list_devices -- --synthetic                   # no camera: a made-up list with two Weevia S1
//! cargo run --example list_devices -- --synthetic --backend msmf    # no camera: the same made-up devices, another order
//! ```
//!
//! Prints every video device in the order of one capture API's list — laptop webcams included — and marks the
//! ones whose name contains the Weevia S1's USB name. Windows has two such lists. DirectShow's (the default) is
//! where this crate's controls find the camera by name. Media Foundation's (`--backend msmf`,
//! `devices::list_mf_devices()`) is the one capture opens from: `DeviceSelector::Index(n)` is entry n
//! of that list. The two lists can be in a different order — seen once on one PC, with Python's
//! list_devices(backend="msmf") — not with this example. So an index from the DirectShow list does not go with
//! `DeviceSelector::Index`. An index also shifts when another camera is plugged in or unplugged; that is why
//! `DeviceConfig::weevia_s1()` finds the camera by name. Without ffmpeg on the PATH the DirectShow list comes
//! from DirectShow directly.
//!
//! The camera reports no USB serial number, so two Weevia S1 have the same name and cannot be told apart by name:
//! with two connected, opening by name stops with `MultipleMatches`. Each line ends with the device's USB location
//! when Windows reports one (`DeviceInfo::usb_location`); open one of two cameras by it with
//! `DeviceSelector::UsbLocation` (`cargo run --example open_by_usb_location`). A USB location is not a serial
//! number: it names the port the camera is plugged into, and it changes when the camera is plugged into another
//! port. Using two cameras at the same time is not supported. Picking a camera by USB location has been run
//! without a camera only (made-up device lists); it has not been run with a camera, and not run with two cameras.
//!
//! Linux and other systems: this listing is for Windows — the example says so and exits; list the cameras
//! with `v4l2-ctl --list-devices`. On Linux `DeviceConfig::weevia_s1()` finds the camera by its V4L2 card name.
//! `--synthetic` works everywhere.
//!
//! Output with `--synthetic` (a made-up list, made-up USB locations):
//!
//! ```text
//! 3 video device(s) in the DirectShow list:
//!   [0] WN Dual Camera   <- Weevia S1   USB location: PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(1)
//!   [1] Example Laptop Webcam   USB location: PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(5)
//!   [2] WN Dual Camera   <- Weevia S1   USB location: PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(4)#USB(2)
//! Weevia S1 ("WN Dual Camera") found: 2
//! ```
//!
//! Camera: the DirectShow list was run with the camera once (one PC, one camera, 2026-11-24) — before it showed USB
//! locations. `--backend msmf` and the USB locations: run without a camera only (a made-up list); not run with the
//! camera. Does not open the camera. Writes to the camera: none.

use opticmix_vision::devices::{list_devices, list_mf_devices, DeviceInfo};
use opticmix_vision::spec::WEEVIA_S1_NAME;

const USAGE: &str = "usage: list_devices [--synthetic] [--backend dshow|msmf]";
const USB_LOCATION_NOTE: &str = "A USB location is not a serial number: it names the port the camera is plugged \
    into, and it changes when the camera is plugged into another port. Using two cameras at the same time is not \
    supported. Picking a camera by USB location has been run without a camera only (made-up device lists); it has \
    not been run with a camera, and not run with two cameras.";

fn main() {
    let (mut synthetic, mut msmf) = (false, false);
    let args: Vec<String> = std::env::args().skip(1).collect();
    let mut it = args.iter().map(String::as_str);
    while let Some(arg) = it.next() {
        match arg {
            "--synthetic" => synthetic = true,
            "--backend" => match it.next() {
                Some("msmf") => msmf = true,
                Some("dshow") => msmf = false,
                other => usage_error(&format!("--backend {}", other.unwrap_or(""))),
            },
            other => usage_error(other),
        }
    }
    if !synthetic && !cfg!(windows) {
        println!(
            "This listing is for Windows. On Linux: v4l2-ctl --list-devices (package v4l-utils). \
             DeviceConfig::weevia_s1() finds the camera by its V4L2 card name, {WEEVIA_S1_NAME:?}."
        );
        return;
    }
    let devices: Vec<DeviceInfo> = if synthetic {
        // A made-up device list with made-up USB locations — not read from any computer. Media Foundation might
        // list the same devices in another order.
        let made_up = [
            (WEEVIA_S1_NAME, "PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(1)"),
            (
                "Example Laptop Webcam",
                "PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(5)",
            ),
            (
                WEEVIA_S1_NAME,
                "PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(4)#USB(2)",
            ),
        ];
        let order = if msmf { [1, 0, 2] } else { [0, 1, 2] };
        order
            .iter()
            .enumerate()
            .map(|(index, &i)| DeviceInfo::new(index, made_up[i].0, Some(made_up[i].1.to_string())))
            .collect()
    } else if msmf {
        list_mf_devices().unwrap_or_else(|e| {
            eprintln!("error: the Media Foundation device list could not be read: {e}");
            std::process::exit(1);
        })
    } else {
        list_devices(None)
    };
    let is_weevia = |name: &str| name.to_lowercase().contains(&WEEVIA_S1_NAME.to_lowercase());
    let (this, other, other_flag) = if msmf {
        ("Media Foundation", "DirectShow", "dshow")
    } else {
        ("DirectShow", "Media Foundation", "msmf")
    };

    println!("{} video device(s) in the {this} list:", devices.len());
    for d in &devices {
        let mark = if is_weevia(&d.name) {
            "   <- Weevia S1"
        } else {
            ""
        };
        let at = d
            .usb_location
            .as_deref()
            .map(|l| format!("   USB location: {l}"))
            .unwrap_or_default();
        println!("  [{}] {}{mark}{at}", d.index, d.name);
    }
    let found = devices.iter().filter(|d| is_weevia(&d.name)).count();
    println!("Weevia S1 ({WEEVIA_S1_NAME:?}) found: {found}");
    if found == 0 {
        println!("Re-insert the cable; if the camera is missing from the list, Windows does not see it either.");
    } else if found > 1 {
        println!(
            "More than one Weevia S1: the camera reports no USB serial number, so they cannot be told apart \
             by name, and opening by name stops with MultipleMatches instead of guessing. Open one by the USB \
             location shown above: cargo run --example open_by_usb_location -- --usb-location \"<USB location>\" \
             (DeviceSelector::UsbLocation)."
        );
        println!("{USB_LOCATION_NOTE}");
    }
    println!(
        "The {other} list (--backend {other_flag}) can be in a different order: an index goes only with the \
         backend whose list it came from."
    );
    println!(
        "Capture opens from the Media Foundation list: DeviceSelector::Index(n) is entry n of it{}.",
        if msmf { "" } else { " (--backend msmf)" }
    );
}

fn usage_error(arg: &str) -> ! {
    eprintln!("unknown option {arg}\n{USAGE}");
    std::process::exit(2);
}
