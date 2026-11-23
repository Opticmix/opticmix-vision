//! Capture backends. Real devices = mf (Windows) and v4l2 (Linux); tests = fake.

use crate::backend::Backend;
#[cfg(any(windows, target_os = "linux"))]
use crate::config::CaptureApi;
use crate::config::{DeviceConfig, NegotiatedMode};
use crate::error::{Result, SdkError};

/// Capture backend that yields compressed frames — use your own with `Device::open_with`.
/// `Send` so a `Device` can move to a capture thread.
pub trait Capture: Send {
    /// The mode the device actually gave.
    fn mode(&self) -> NegotiatedMode;
    /// Name the opened device reports (Windows: Media Foundation name, Linux: V4L2 card name).
    fn device_name(&self) -> &str;
    /// One compressed frame (MJPG bytes). A read failure or an unplugged device gives `SdkError::Device`.
    fn read_jpeg(&mut self) -> Result<Vec<u8>>;
    /// Control backend for the same camera. `SdkError::Unavailable` if there is none.
    fn controls_backend(&self) -> Result<Box<dyn Backend>>;
}

/// Opens this platform's capture backend for `cfg.api` (Windows = Media Foundation, Linux = V4L2).
pub fn open_platform(cfg: &DeviceConfig) -> Result<Box<dyn Capture>> {
    #[cfg(windows)]
    {
        if matches!(cfg.api, CaptureApi::Auto | CaptureApi::MediaFoundation) {
            return Ok(Box::new(crate::mf::MfCapture::open(cfg)?));
        }
    }
    #[cfg(target_os = "linux")]
    {
        if matches!(cfg.api, CaptureApi::Auto | CaptureApi::V4l2) {
            return Ok(Box::new(crate::v4l2::V4l2Capture::open(cfg)?));
        }
    }
    Err(SdkError::Device(format!(
        "capture api {:?} is not available on this platform",
        cfg.api
    )))
}

/// For tests — yields prepared MJPG bytes in turn. Controls are a MockBackend with made-up ranges.
#[cfg(any(test, feature = "test-support"))]
pub mod fake {
    use super::*;
    use std::collections::VecDeque;

    pub struct FakeCapture {
        pub frames: VecDeque<Vec<u8>>,
        pub mode: NegotiatedMode,
    }

    impl FakeCapture {
        pub fn new(frames: Vec<Vec<u8>>, width: u32, height: u32) -> FakeCapture {
            FakeCapture {
                frames: frames.into(),
                mode: NegotiatedMode {
                    width,
                    height,
                    fps: 90.0,
                    fourcc: "MJPG".into(),
                    backend_name: "fake".into(),
                },
            }
        }
    }

    impl Capture for FakeCapture {
        fn mode(&self) -> NegotiatedMode {
            self.mode.clone()
        }
        fn device_name(&self) -> &str {
            "fake"
        }
        fn read_jpeg(&mut self) -> Result<Vec<u8>> {
            self.frames
                .pop_front()
                .ok_or_else(|| SdkError::Device("fake: no more frames".into()))
        }
        fn controls_backend(&self) -> Result<Box<dyn Backend>> {
            Ok(Box::new(crate::backend::mock::MockBackend::new()))
        }
    }
}
