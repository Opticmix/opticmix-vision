//! Opticmix Vision geometry: the calibration file (schema v1) and the Kannala-Brandt fisheye model.
//! Same checks, numbers and messages as the Python package `opticmix_vision.calibration`. No device or OS code.

pub mod calib;
#[cfg(feature = "stereo")]
pub mod depth;
pub mod error;
pub mod kb4;
#[cfg(feature = "stereo")]
pub mod map;
pub mod pyrepr;
#[cfg(feature = "stereo")]
pub mod rectify;

pub use error::{GeomError, Result};
