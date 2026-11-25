//! Rust API for the Weevia S1 stereo-IR camera — **preview**. The API is 0.x and may change.
//!
//! ```no_run
//! # #[cfg(feature = "capture")]
//! # fn main() -> opticmix_vision::Result<()> {
//! use opticmix_vision::{Device, DeviceConfig, UserValue};
//!
//! let mut cam = Device::open(DeviceConfig::weevia_s1())?;
//! println!("{:?}", cam.mode()); // what the camera actually gave — check it
//! // Once, at start: mode and value in one write.
//! // Exposure is in the driver's units, not converted to time — Windows (DirectShow): -5. Linux (V4L2): the V4L2 standard's 100 µs unit, not run on the camera.
//! // Writing through this crate's Device has not yet been run on the camera.
//! cam.controls()?.set_many(&[
//!     ("auto_exposure".into(), UserValue::Choice("manual".into())),
//!     ("exposure".into(), UserValue::Int(-5)),
//! ])?;
//! let frame = cam.read()?; // frame.left, frame.right: 8-bit gray
//! # Ok(())
//! # }
//! # #[cfg(not(feature = "capture"))]
//! # fn main() {}
//! ```
//!
//! The default feature `capture` brings frame capture (`Device`). With `default-features = false`
//! the crate keeps the camera controls (Windows DirectShow), `split_stereo`, the stall check and the LED.
//!
//! `Recorder` and `Device::open_recording` write and play recordings in the Python package's file format (MJPG
//! `.avi` with its CSV and JSON; see `recording`).
//!
//! `calib` reads the calibration file installed for your camera (`calib::resolve()`, `Device::calibration()`), by
//! the rules of the Python package's `opticmix_vision.calib`.
//!
//! `presets` saves every control to a preset file, reads and checks presets and gives what loading one would change
//! on this camera, in the Python package's format and folder; applying a preset to the camera is not in the crate yet.
//!
//! The Python package `opticmix_vision` calls these controls through its PyO3 extension
//! (`opticmix_vision._core`) — the control logic lives here only.

pub mod backend;
pub mod calib;
#[cfg(feature = "capture")]
pub mod capture;
pub mod config;
pub mod controls;
pub mod convert;
#[cfg(feature = "capture")]
pub mod decode;
#[cfg(feature = "capture")]
pub mod device;
pub mod devices;
pub mod dshow;
pub mod error;
pub mod guard;
pub mod led;
pub mod presets;
#[cfg(feature = "capture")]
pub mod recording;
#[cfg(all(feature = "capture", windows))]
pub mod mf;
// Python notation for the calibration messages (shared with the repository's geometry crate).
mod pyrepr;
pub mod spec;
pub mod stall;
pub mod stereo;
#[cfg(all(feature = "capture", target_os = "linux"))]
pub mod v4l2;
#[cfg(all(feature = "capture", target_os = "linux"))]
pub mod v4l2_ctrl;

#[cfg(feature = "capture")]
pub use capture::{DriverClock, DriverTimeSource, SampleInfo};
pub use config::{CaptureApi, DeviceConfig, DeviceSelector, NegotiatedMode};
pub use convert::UserValue;
#[cfg(feature = "capture")]
pub use device::{host_now_ns, Device, StereoFrame, MAX_UNDECODABLE_IN_A_ROW};
pub use error::{Result, SdkError};
pub use led::LedControl;
#[cfg(feature = "capture")]
pub use recording::{capture_info, write_capture_info, Playback, PlaybackStatus, Recorder};
pub use stereo::{split_stereo, GrayImage, Layout, LrOrder};
/// The JSON crate of `calib::StereoCalibration::meta` (a `serde_json::Value`), re-exported so you need not add it.
pub use serde_json;
