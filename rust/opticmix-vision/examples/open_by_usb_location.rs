//! open_by_usb_location — open one Weevia S1 by the USB port it is plugged into, and read one frame.
//!
//! ```text
//! cargo run --release --example open_by_usb_location                         # list each Weevia S1 and its USB location
//! cargo run --release --example open_by_usb_location -- --usb-location "PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(1)"
//! cargo run --release --features test-support --example open_by_usb_location -- --synthetic
//! ```
//!
//! Two Weevia S1 have the same name (the camera reports no USB serial number), so opening by name stops with
//! `MultipleMatches` when both are connected. `DeviceSelector::UsbLocation` opens the one with the name at a USB
//! location — Windows: the USB device's first location path, as `cargo run --example list_devices` and this example
//! print it (Media Foundation's list, the one capture opens from); Linux: the USB device's sysfs name, e.g. `1-4.2`
//! (`cargo run --example list_devices` and this example print it from the V4L2 list, `devices::list_v4l2_devices()`;
//! `python -m opticmix_vision.doctor` prints it).
//! The controls then find the camera at the same location in DirectShow's list.
//!
//! A USB location is not a serial number: it names the port the camera is plugged into, and it changes when the
//! camera is plugged into another port. Using two cameras at the same time is not supported. Picking a camera by USB
//! location has been run with the camera once (one PC, one camera, 2026-11-25): with one camera connected, the list
//! showed its USB location, and given that location it opened at 1600x800 MJPG and read one frame; a location no device
//! has was refused (Python DirectShow, Rust Media Foundation); not run with two cameras.
//!
//! `--synthetic` picks from a made-up list of two Weevia S1 (made-up USB locations; the second one unless
//! `--usb-location` names another) and reads a generated frame, not measured data.
//!
//! Output with `--synthetic` (a made-up list, generated frames):
//!
//! ```text
//! Weevia S1 in the made-up list:
//!   [0] USB location: PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(1)
//!   [2] USB location: PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(4)#USB(2)
//! picked [2] at USB location PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(4)#USB(2)
//! negotiated: 1600x800 MJPG 90.0 fps (synthetic)
//! one frame: left 800x800, right 800x800
//! ```
//!
//! Camera: run with one camera once (one PC, 2026-11-25), as said above; not run with two cameras. Writes to the
//! camera: none.

mod common;

#[cfg(not(target_os = "linux"))]
use opticmix_vision::devices::list_mf_devices;
use opticmix_vision::devices::{pick_by_usb_location, DeviceInfo};
use opticmix_vision::spec::WEEVIA_S1_NAME;
use opticmix_vision::{DeviceConfig, DeviceSelector};

const USAGE: &str = "usage: open_by_usb_location [--usb-location LOCATION] [--synthetic]";

fn main() -> std::process::ExitCode {
    let a = common::Args::parse(USAGE, &["--usb-location", "--synthetic"]);
    // Given but empty is not "not given": the library refuses it (InvalidValue).
    let location = a.flag("--usb-location").then(|| a.value("--usb-location", String::new()));
    common::report(run(a.flag("--synthetic"), location))
}

fn made_up() -> Vec<DeviceInfo> {
    [
        (WEEVIA_S1_NAME, "PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(1)"),
        (
            "Example Laptop Webcam",
            "PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(5)",
        ),
        (
            WEEVIA_S1_NAME,
            "PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(4)#USB(2)",
        ),
    ]
    .iter()
    .enumerate()
    .map(|(index, (name, l))| DeviceInfo::new(index, *name, Some(l.to_string())))
    .collect()
}

fn run(synthetic: bool, location: Option<String>) -> Result<(), Box<dyn std::error::Error>> {
    let listed = if synthetic {
        made_up()
    } else {
        capture_list()?
    };
    let weevia: Vec<&DeviceInfo> = listed
        .iter()
        .filter(|d| {
            d.name
                .to_lowercase()
                .contains(&WEEVIA_S1_NAME.to_lowercase())
        })
        .collect();
    if cfg!(windows) || cfg!(target_os = "linux") || synthetic {
        println!(
            "Weevia S1 in the {} list:",
            if synthetic {
                "made-up"
            } else if cfg!(windows) {
                "Media Foundation"
            } else {
                "V4L2"
            }
        );
        for d in &weevia {
            println!(
                "  [{}] USB location: {}",
                d.index,
                d.usb_location.as_deref().unwrap_or("not known")
            );
        }
    }
    let location = match location {
        Some(l) => l,
        None if !synthetic => {
            println!(
                "Pass --usb-location with one of them{}.",
                if cfg!(windows) {
                    ""
                } else {
                    " — on Linux, the USB device's sysfs name (cargo run --example list_devices)"
                }
            );
            return Ok(());
        }
        None => weevia
            .last()
            .and_then(|d| d.usb_location.clone())
            .unwrap_or_default(),
    };
    if synthetic {
        // The pick a real open makes, on the made-up list.
        let d = pick_by_usb_location(WEEVIA_S1_NAME, &location, &listed)?;
        println!("picked [{}] at USB location {location}", d.index);
    }
    let cfg = DeviceConfig {
        device: DeviceSelector::UsbLocation {
            name: WEEVIA_S1_NAME.to_string(),
            usb_location: location,
        },
        ..DeviceConfig::weevia_s1()
    };
    let mut cam = common::open_camera_with(cfg, synthetic, None)?;
    println!("negotiated: {}", common::mode_line(&cam.mode()));
    let f = cam.read()?;
    println!(
        "one frame: left {}x{}, right {}x{}",
        f.left.width, f.left.height, f.right.width, f.right.height
    );
    Ok(())
}

/// The list capture opens from: Media Foundation's on Windows, the V4L2 capture nodes on Linux.
#[cfg(target_os = "linux")]
fn capture_list() -> opticmix_vision::Result<Vec<DeviceInfo>> {
    Ok(opticmix_vision::devices::list_v4l2_devices())
}

#[cfg(not(target_os = "linux"))]
fn capture_list() -> opticmix_vision::Result<Vec<DeviceInfo>> {
    list_mf_devices()
}
