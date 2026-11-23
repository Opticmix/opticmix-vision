# opticmix-vision (Rust)

Rust API for the Weevia S1 stereo-IR camera — **preview**. The API is 0.x and may change.

Status: on Windows 11 this crate has been run on one camera — 1600×800 MJPG at 90 fps through Media
Foundation, 90–92 frames per second in 300-frame runs, fourteen controls read while streaming, no
control written. Not run: Linux, a second unit. Status: partial — not yet verified for release; the
Python package `opticmix_vision` ([repository](https://github.com/Opticmix/opticmix-vision)) is the
measured path.

## Install

```sh
cargo add opticmix-vision
```

MSRV 1.88.

## Example

```rust
use opticmix_vision::{Device, DeviceConfig, UserValue};

fn main() -> opticmix_vision::Result<()> {
    let mut cam = Device::open(DeviceConfig::weevia_s1())?;
    println!("{:?}", cam.mode()); // what the camera actually gave — check it
    // Once, at start: mode and value in one write.
    // Exposure is in the driver's units, not converted to time — Windows (DirectShow): -5. Linux (V4L2): the V4L2 standard's 100 µs unit, not run on the camera.
    // Writing through this crate's Device has not yet been run on the camera.
    cam.controls()?.set_many(&[
        ("auto_exposure".into(), UserValue::Choice("manual".into())),
        ("exposure".into(), UserValue::Int(-5)),
    ])?;
    let frame = cam.read()?; // frame.left, frame.right: 8-bit gray
    println!("{}x{}", frame.left.width, frame.left.height);
    Ok(())
}
```

## Platforms

| | Windows | Linux |
|---|---|---|
| Capture | Media Foundation | V4L2 — implemented, not yet run on the camera |
| Controls | DirectShow | V4L2 — implemented, not yet run on the camera |

Windows: run on one camera (above). Linux: not run.

Requirements:

- Windows: capture needs Media Foundation. It may be absent on Windows N/KN editions unless the Media
  Feature Pack is installed.
- Linux: building needs libclang and kernel headers (Ubuntu: `sudo apt install libclang-dev linux-libc-dev`)
  because the `v4l` crate generates its bindings at build time.

## Features

- `capture` (default): frame capture (`Device`), and on Linux the V4L2 controls.
- Controls only, without capture: `default-features = false`. This keeps the Windows DirectShow controls,
  `split_stereo`, the stall check and the LED.

## Notes

Camera writes: set controls once when you open the camera. Every write goes through a guard
(minimum gap between commands, a running count with warnings).

The camera reports no serial number: with two cameras of the same name connected, `controls()` fails
with `MultipleMatches` on Windows (controls find the camera by name).

## License

Apache-2.0.
