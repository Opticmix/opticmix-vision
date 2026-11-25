//! Opticmix Vision geometry: the calibration file (schema v1) and the Kannala-Brandt fisheye model.
//! Same checks, numbers and messages as the Python package `opticmix_vision.calibration`. No device or OS code.

pub mod calib;
#[cfg(feature = "stereo")]
pub mod depth;
pub mod error;
pub mod kb4;
#[cfg(feature = "stereo")]
pub mod map;
// Python notation for the messages; one source with the published crate (opticmix-vision).
#[path = "../../opticmix-vision/src/pyrepr.rs"]
pub mod pyrepr;
#[cfg(feature = "stereo")]
pub mod rectify;

pub use error::{GeomError, Result};
