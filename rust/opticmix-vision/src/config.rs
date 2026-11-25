//! Device configuration (Python `DeviceConfig`). The first release captures MJPG only — there is no fourcc field.

use crate::error::{Result, SdkError};
use crate::spec::WEEVIA_S1_NAME;
use crate::stereo::{Layout, LrOrder};

/// Which camera to open. An index changes when another camera is plugged in — the name is the default.
///
/// `#[non_exhaustive]`: variants may be added — match with a wildcard arm.
#[derive(Debug, Clone, PartialEq, Eq)]
#[non_exhaustive]
pub enum DeviceSelector {
    /// Part of the name (case-insensitive). `MultipleMatches` if two or more cameras have that name.
    Name(String),
    /// Windows: Media Foundation enumeration order (`cargo run --example list_devices -- --backend msmf`), which
    /// can differ from DirectShow's; Linux: `/dev/videoN`.
    /// Check what was opened with `Device::device_name()`.
    Index(usize),
    /// Linux `/dev/videoN`. A Device from `Device::open_recording` has the recording's path here.
    Path(String),
    /// Part of the name (as `Name`) at one USB location (`DeviceInfo::usb_location`, as `cargo run --example
    /// list_devices` prints it) — how to open one of two cameras that share a name. A USB location is not a serial
    /// number: it names the port the camera is plugged into, and it changes when the camera is plugged into another
    /// port. Using two cameras at the same time is not supported. Picking a camera by USB location has been run with
    /// the camera once (one PC, one camera, 2026-11-25): with one camera connected, the list showed its USB location,
    /// and given that location it opened at 1600x800 MJPG and read one frame; a location no device has was refused
    /// (Python DirectShow, Rust Media Foundation); not run with two cameras.
    UsbLocation { name: String, usb_location: String },
}

/// Capture backend choice. Choosing one this platform does not have makes `Device::open` fail.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CaptureApi {
    /// Windows = MediaFoundation, Linux = V4l2.
    Auto,
    MediaFoundation,
    V4l2,
}

/// What to open and how (requested values). Usually start from `DeviceConfig::weevia_s1()` and change a few fields.
#[derive(Debug, Clone, PartialEq)]
pub struct DeviceConfig {
    pub layout: Layout,
    pub lr_order: LrOrder,
    pub device: DeviceSelector,
    pub api: CaptureApi,
    pub width: u32,
    pub height: u32,
    pub fps: f64,
    /// `read()` fails when one eye yields a byte-identical image this many frames in a row. 0 = off; 1 is not accepted.
    pub stall_frames: u32,
}

impl DeviceConfig {
    /// Weevia S1 measured settings (same values as Python `WEEVIA_S1`). The camera is found by name.
    pub fn weevia_s1() -> DeviceConfig {
        DeviceConfig {
            layout: Layout::Sbs,
            lr_order: LrOrder::FirstIsRight,
            device: DeviceSelector::Name(WEEVIA_S1_NAME.to_string()),
            api: CaptureApi::Auto,
            width: 1600,
            height: 800,
            fps: 90.0,
            stall_frames: 90,
        }
    }

    pub fn validate(&self) -> Result<()> {
        if self.width == 0 || self.height == 0 {
            return Err(SdkError::InvalidValue(format!(
                "width and height must be positive: {}x{}",
                self.width, self.height
            )));
        }
        if self.fps.is_nan() || self.fps <= 0.0 {
            return Err(SdkError::InvalidValue(format!("fps must be positive: {}", self.fps)));
        }
        if self.layout == Layout::PlanePack {
            // MJPG 복호 결과는 한 장의 회색 영상이라 짝/홀 화소 묶음에 뜻이 없다. split_stereo 단독으로는 받는다.
            return Err(SdkError::InvalidValue(
                "plane_pack is not a layout an MJPG camera sends; use split_stereo directly on raw planes".into(),
            ));
        }
        if self.stall_frames == 1 {
            return Err(SdkError::InvalidValue(
                "stall_frames is 0 (off) or 2 or more: 1".into(),
            ));
        }
        Ok(())
    }
}

/// The mode the device actually gave. It can differ from the request — check it after opening.
#[derive(Debug, Clone, PartialEq)]
pub struct NegotiatedMode {
    pub width: u32,
    pub height: u32,
    pub fps: f64,
    pub fourcc: String,
    /// "msmf" | "v4l2" | "fake" (tests)
    pub backend_name: String,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn weevia_s1_is_the_measured_python_setting() {
        let c = DeviceConfig::weevia_s1();
        assert_eq!((c.layout, c.lr_order), (Layout::Sbs, LrOrder::FirstIsRight));
        assert_eq!((c.width, c.height, c.fps), (1600, 800, 90.0));
        assert_eq!(c.device, DeviceSelector::Name("WN Dual Camera".into()));
        assert_eq!((c.api, c.stall_frames), (CaptureApi::Auto, 90));
        assert!(c.validate().is_ok());
    }

    #[test]
    fn validate_refuses_what_python_refuses() {
        let ok = DeviceConfig::weevia_s1();
        for bad in [
            DeviceConfig { width: 0, ..ok.clone() },
            DeviceConfig { height: 0, ..ok.clone() },
            DeviceConfig { fps: 0.0, ..ok.clone() },
            DeviceConfig { fps: f64::NAN, ..ok.clone() },
            DeviceConfig { stall_frames: 1, ..ok.clone() },
        ] {
            assert!(matches!(bad.validate(), Err(SdkError::InvalidValue(_))), "{bad:?}");
        }
        for fine in [0, 2] {
            assert!(DeviceConfig { stall_frames: fine, ..ok.clone() }.validate().is_ok());
        }
    }

    #[test]
    fn plane_pack_is_refused_for_a_camera() {
        let c = DeviceConfig { layout: Layout::PlanePack, ..DeviceConfig::weevia_s1() };
        let e = c.validate().unwrap_err();
        assert!(matches!(e, SdkError::InvalidValue(_)), "{e:?}");
        assert!(e.to_string().contains("split_stereo"), "{e}");
        for fine in [Layout::Sbs, Layout::Tb] {
            assert!(DeviceConfig { layout: fine, ..DeviceConfig::weevia_s1() }.validate().is_ok());
        }
    }
}
