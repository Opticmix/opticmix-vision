//! list_devices — list the video devices Windows (or Linux) reports, and how many of them are a Weevia S1.
//!
//! ```text
//! cargo run --example list_devices                                  # DirectShow's list (the default; Linux: V4L2's)
//! cargo run --example list_devices -- --backend msmf                # Media Foundation's list
//! cargo run --example list_devices -- --backend v4l2                # Linux: the V4L2 capture nodes (the default there)
//! cargo run --example list_devices -- --synthetic                   # no camera: a made-up list with two Weevia S1
//! cargo run --example list_devices -- --synthetic --backend msmf    # no camera: the same made-up devices, another order
//! cargo run --example list_devices -- --synthetic --backend v4l2    # no camera: the same made-up devices as V4L2 lists them
//! cargo run --example list_devices -- --verbose                     # also each device's USB IDs and link speed
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
//! port. Using two cameras at the same time is not supported. Picking a camera by USB location has been run with the
//! camera once (one PC, one camera, 2026-11-25): with one camera connected, the list showed its USB location, and given
//! that location it opened at 1600x800 MJPG and read one frame; a location no device has was refused (Python
//! DirectShow, Rust Media Foundation); not run with two cameras.
//!
//! With `--verbose`, each device also gets one line for each of `DeviceInfo::vid`, `pid`, `bcd_device` (the USB
//! vendor ID, product ID and device revision) and `link_speed` (the USB link as the hub reports it: Low, Full, High,
//! Super or Super+), read as the doctor reads them, without opening the device; "not read" where Windows did not say.
//! Not reported by the camera: a USB serial number, a firmware version (`bcd_device` is the USB device revision; how
//! it relates to the firmware version has not been verified), a temperature.
//!
//! Linux (`--backend v4l2`, the default there; with the default `capture` feature): the video capture nodes in
//! /sys/class/video4linux, from `devices::list_v4l2_devices()` (a UVC camera's metadata node is left out); the index
//! is the N of /dev/videoN, which `DeviceSelector::Index(n)` opens, the USB location the USB device's sysfs name
//! (e.g. 2-1), and `--verbose` reads the IDs and link speed from sysfs, as the doctor does. Run without a camera only
//! (made-up sysfs trees, in the function's tests); not run with the camera on Linux. A list another system does not
//! have (DirectShow and Media Foundation off Windows; V4L2 off Linux, or on Linux without `capture`): the example
//! says where the lists are and exits. `--synthetic` works everywhere; with `--backend v4l2` it gives the same
//! made-up devices as V4L2 lists them (made-up /dev/videoN numbers and sysfs names).
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
//! locations. `--backend msmf`, the USB locations and `--verbose`: run without a camera only (a made-up list); not run
//! with the camera. Does not open the camera. Writes to the camera: none.

use opticmix_vision::devices::{list_devices, list_mf_devices, DeviceInfo};
use opticmix_vision::spec::WEEVIA_S1_NAME;

const USAGE: &str = "usage: list_devices [--synthetic] [--backend dshow|msmf|v4l2] [--verbose]";
const USB_LOCATION_NOTE: &str = "A USB location is not a serial number: it names the port the camera is plugged \
    into, and it changes when the camera is plugged into another port. Using two cameras at the same time is not \
    supported. Picking a camera by USB location has been run with the camera once (one PC, one camera, 2026-11-25): \
    with one camera connected, the list showed its USB location, and given that location it opened at 1600x800 MJPG \
    and read one frame; a location no device has was refused (Python DirectShow, Rust Media Foundation); not run \
    with two cameras.";
const NOT_REPORTED: &str = "Not reported by the camera: a USB serial number, a firmware version (bcd_device is the USB \
    device revision; how it relates to the firmware version has not been verified), a temperature.";

fn main() {
    let (mut synthetic, mut backend, mut verbose) = (false, None, false);
    let args: Vec<String> = std::env::args().skip(1).collect();
    let mut it = args.iter().map(String::as_str);
    while let Some(arg) = it.next() {
        match arg {
            "--synthetic" => synthetic = true,
            "--verbose" => verbose = true,
            "--backend" => match it.next() {
                Some(b @ ("dshow" | "msmf" | "v4l2")) => backend = Some(b),
                other => usage_error(&format!("--backend {}", other.unwrap_or(""))),
            },
            other => usage_error(other),
        }
    }
    // the default: V4L2's list on Linux with `capture`, DirectShow's elsewhere and with --synthetic (as in Python)
    let backend = backend.unwrap_or(if LINUX_LIST && !synthetic { "v4l2" } else { "dshow" });
    let (msmf, linux) = (backend == "msmf", backend == "v4l2");
    let v4l2 = if synthetic || !linux { None } else { v4l2_devices() };
    if !synthetic && linux && v4l2.is_none() {
        println!(
            "The V4L2 list is Linux's. On Windows: the DirectShow list (the default, where this crate's controls find \
             the camera) or --backend msmf (the one capture opens from)."
        );
        return;
    }
    if !synthetic && !linux && !cfg!(windows) {
        println!(
            "The DirectShow and Media Foundation lists are Windows'. On Linux this example lists the V4L2 capture \
             nodes (--backend v4l2, the default there), built with the capture feature (the default); or v4l2-ctl \
             --list-devices (package v4l-utils). DeviceConfig::weevia_s1() finds the camera by its V4L2 card name, \
             {WEEVIA_S1_NAME:?}."
        );
        return;
    }
    let devices: Vec<DeviceInfo> = if let Some(listed) = v4l2 {
        listed
    } else if synthetic {
        // A made-up device list with made-up USB locations, IDs and link speeds — not read from any computer; the
        // webcam's IDs and link speed are left unread. Media Foundation might list the same devices in another order;
        // V4L2 lists them by /dev/videoN (a UVC camera's metadata node, the odd numbers here, left out), named and
        // located as sysfs names them.
        let made_up = [
            (WEEVIA_S1_NAME, "PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(1)", Some("Super")),
            ("Example Laptop Webcam", "PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(5)", None),
            (WEEVIA_S1_NAME, "PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(4)#USB(2)", Some("High")),
        ];
        let made_up_v4l2 = [
            ("Webcam gadget: WN Dual Camera", "2-1"),
            ("Example Laptop Webcam", "1-5"),
            ("Webcam gadget: WN Dual Camera", "1-4.2"),
        ];
        let order = if msmf { [1, 0, 2] } else { [0, 1, 2] };
        order
            .iter()
            .enumerate()
            .map(|(index, &i)| {
                let (name, location, link) = made_up[i];
                let (index, name, location) = if linux {
                    (2 * index, made_up_v4l2[i].0, made_up_v4l2[i].1)
                } else {
                    (index, name, location)
                };
                let mut d = DeviceInfo::new(index, name, Some(location.to_string()));
                if let Some(link) = link {
                    (d.vid, d.pid, d.bcd_device, d.link_speed) =
                        (Some(0x1D6B), Some(0x0200), Some(0x0409), Some(link.to_string()));
                }
                d
            })
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
    let (this, other, other_flag) = if linux {
        ("V4L2", "", "")
    } else if msmf {
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
        if verbose {
            let hex = |v: Option<u16>| v.map_or("not read".to_string(), |v| format!("0x{v:04x}"));
            println!("      vid: {}", hex(d.vid));
            println!("      pid: {}", hex(d.pid));
            println!("      bcd_device: {}", hex(d.bcd_device));
            println!("      link_speed: {}", d.link_speed.as_deref().unwrap_or("not read"));
        }
    }
    if verbose {
        println!("{NOT_REPORTED}");
    }
    let found = devices.iter().filter(|d| is_weevia(&d.name)).count();
    println!("Weevia S1 ({WEEVIA_S1_NAME:?}) found: {found}");
    if found == 0 {
        println!(
            "Re-insert the cable; if the camera is missing from the list, {} does not see it either.",
            if linux { "Linux" } else { "Windows" }
        );
    } else if found > 1 {
        println!(
            "More than one Weevia S1: the camera reports no USB serial number, so they cannot be told apart \
             by name, and opening by name stops with MultipleMatches instead of guessing. Open one by the USB \
             location shown above: cargo run --example open_by_usb_location -- --usb-location \"<USB location>\" \
             (DeviceSelector::UsbLocation)."
        );
        println!("{USB_LOCATION_NOTE}");
    }
    if linux {
        println!("DeviceSelector::Index(n) opens /dev/videoN, the index shown.");
        return;
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

/// Whether this build lists the V4L2 capture nodes (Linux with `capture`).
const LINUX_LIST: bool = cfg!(all(feature = "capture", target_os = "linux"));

/// Linux with `capture`: the V4L2 capture nodes. `None` elsewhere (the listing is not there).
#[cfg(all(feature = "capture", target_os = "linux"))]
fn v4l2_devices() -> Option<Vec<DeviceInfo>> {
    Some(opticmix_vision::devices::list_v4l2_devices())
}

#[cfg(not(all(feature = "capture", target_os = "linux")))]
fn v4l2_devices() -> Option<Vec<DeviceInfo>> {
    None
}

fn usage_error(arg: &str) -> ! {
    eprintln!("unknown option {arg}\n{USAGE}");
    std::process::exit(2);
}
