//! Capture backends. Real devices = mf (Windows) and v4l2 (Linux); tests = fake.

use crate::backend::Backend;
#[cfg(any(windows, target_os = "linux"))]
use crate::config::CaptureApi;
use crate::config::{DeviceConfig, NegotiatedMode};
use crate::error::{Result, SdkError};

/// The clock a driver time stamp (`StereoFrame::driver_ns`) is on. Both are time stamps the operating system's
/// capture driver puts on a sample — the camera sends no clock this crate reads, so neither is a camera (device)
/// time. Whether a driver time marks the exposure or the arrival of the frame is not known: not measured.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DriverClock {
    /// Windows: the sample time Media Foundation's source reader gives with each sample
    /// (`IMFSourceReader::ReadSample`, in 100 ns units, converted to ns here). On one PC (Windows 11) with one
    /// camera (2026-11-24, once) the first Media Foundation time was about 364,400 s — not the start of
    /// streaming; what the clock's zero is has not been established. Its zero is not `host_ns`'s. Use differences
    /// between frames of one stream, not the absolute value.
    MediaFoundation,
    /// Linux: the V4L2 buffer time stamp (`v4l2_buffer.timestamp`, microsecond resolution) of a buffer the driver
    /// flags `V4L2_BUF_FLAG_TIMESTAMP_MONOTONIC` — the system's `CLOCK_MONOTONIC`.
    V4l2Monotonic,
}

/// Where in a frame's capture a V4L2 driver says it took the time stamp (`V4L2_BUF_FLAG_TSTAMP_SRC_*`). This is the
/// value the driver reports — a driver that sets no source reports end of frame — and it has not been checked on
/// the camera.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DriverTimeSource {
    /// `V4L2_BUF_FLAG_TSTAMP_SRC_EOF`: when the last pixel of the frame was received.
    EndOfFrame,
    /// `V4L2_BUF_FLAG_TSTAMP_SRC_SOE`: when the exposure of the frame began.
    StartOfExposure,
}

/// What the capture driver reported with one sample. A field is `None` when the backend does not report it.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct SampleInfo {
    /// Driver time stamp in ns, on `driver_clock`. `Some` exactly when `driver_clock` is.
    pub driver_ns: Option<i64>,
    pub driver_clock: Option<DriverClock>,
    /// The driver's frame sequence number (V4L2 `v4l2_buffer.sequence`). Media Foundation has none.
    pub driver_sequence: Option<u32>,
    /// Where the V4L2 driver says it took `driver_ns`; `None` without a V4L2 time or for a source value it does not
    /// name. Media Foundation gives none.
    pub driver_time_source: Option<DriverTimeSource>,
}

/// `V4L2_BUF_FLAG_TIMESTAMP_MASK` and `V4L2_BUF_FLAG_TIMESTAMP_MONOTONIC` (linux/videodev2.h).
const V4L2_TIMESTAMP_MASK: u32 = 0x0000_e000;
const V4L2_TIMESTAMP_MONOTONIC: u32 = 0x0000_2000;
/// `V4L2_BUF_FLAG_TSTAMP_SRC_MASK`, `_EOF` and `_SOE`.
const V4L2_TSTAMP_SRC_MASK: u32 = 0x0007_0000;
const V4L2_TSTAMP_SRC_EOF: u32 = 0x0000_0000;
const V4L2_TSTAMP_SRC_SOE: u32 = 0x0001_0000;

impl SampleInfo {
    /// From Media Foundation's sample time, in 100 ns units.
    pub fn from_media_foundation(time_100ns: i64) -> SampleInfo {
        SampleInfo {
            driver_ns: Some(time_100ns.saturating_mul(100)),
            driver_clock: Some(DriverClock::MediaFoundation),
            driver_sequence: None,
            driver_time_source: None,
        }
    }

    /// From a V4L2 buffer: time stamp (seconds, microseconds), buffer flags and sequence number. The time (and the
    /// source the flags give for it) is kept only when the flags mark it `CLOCK_MONOTONIC`; the sequence number is
    /// kept either way.
    pub fn from_v4l2(sec: i64, usec: i64, flags: u32, sequence: u32) -> SampleInfo {
        let monotonic = flags & V4L2_TIMESTAMP_MASK == V4L2_TIMESTAMP_MONOTONIC;
        SampleInfo {
            driver_ns: monotonic.then(|| {
                sec.saturating_mul(1_000_000_000)
                    .saturating_add(usec.saturating_mul(1_000))
            }),
            driver_clock: monotonic.then_some(DriverClock::V4l2Monotonic),
            driver_sequence: Some(sequence),
            driver_time_source: match flags & V4L2_TSTAMP_SRC_MASK {
                _ if !monotonic => None,
                V4L2_TSTAMP_SRC_EOF => Some(DriverTimeSource::EndOfFrame),
                V4L2_TSTAMP_SRC_SOE => Some(DriverTimeSource::StartOfExposure),
                _ => None,
            },
        }
    }
}

/// Capture backend that yields compressed frames — use your own with `Device::open_with`.
/// `Send` so a `Device` can move to a capture thread.
pub trait Capture: Send {
    /// The mode the device actually gave.
    fn mode(&self) -> NegotiatedMode;
    /// Name the opened device reports (Windows: Media Foundation name, Linux: V4L2 card name).
    fn device_name(&self) -> &str;
    /// One compressed frame (MJPG bytes). A read failure or an unplugged device gives `SdkError::Device`.
    fn read_jpeg(&mut self) -> Result<Vec<u8>>;
    /// One compressed frame and what the driver reported with it. `Device` reads through this. The default
    /// calls `read_jpeg` and reports nothing (every `SampleInfo` field `None`).
    fn read_sample(&mut self) -> Result<(Vec<u8>, SampleInfo)> {
        Ok((self.read_jpeg()?, SampleInfo::default()))
    }
    /// Control backend for the same camera. `SdkError::Unavailable` if there is none.
    fn controls_backend(&self) -> Result<Box<dyn Backend>>;
    /// The recording this backend plays, so `Device` can take the recorded times and `Device::playback` can
    /// seek: `Some` only for `recording::Playback` (`Device::open_recording`). Your own backend keeps the
    /// default, `None`.
    fn playback(&mut self) -> Option<&mut crate::recording::Playback> {
        None
    }
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
        /// What `read_sample` reports with each frame, in turn; once empty, nothing (`SampleInfo::default()`).
        pub infos: VecDeque<SampleInfo>,
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
                infos: VecDeque::new(),
            }
        }

        /// The same, with made-up driver reports for the frames (one per frame, in order).
        pub fn with_infos(mut self, infos: Vec<SampleInfo>) -> FakeCapture {
            self.infos = infos.into();
            self
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
        fn read_sample(&mut self) -> Result<(Vec<u8>, SampleInfo)> {
            let b = self.read_jpeg()?;
            Ok((b, self.infos.pop_front().unwrap_or_default()))
        }
        fn controls_backend(&self) -> Result<Box<dyn Backend>> {
            Ok(Box::new(crate::backend::mock::MockBackend::new()))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn media_foundation_time_is_converted_from_100_ns_units() {
        let i = SampleInfo::from_media_foundation(123_456_789);
        assert_eq!(i.driver_ns, Some(12_345_678_900));
        assert_eq!(i.driver_clock, Some(DriverClock::MediaFoundation));
        assert_eq!(
            i.driver_sequence, None,
            "Media Foundation has no sequence number"
        );
        assert_eq!(SampleInfo::from_media_foundation(-3).driver_ns, Some(-300));
        assert_eq!(
            SampleInfo::from_media_foundation(i64::MAX).driver_ns,
            Some(i64::MAX),
            "saturates"
        );
    }

    #[test]
    fn a_monotonic_v4l2_time_stamp_is_kept_in_ns() {
        // MONOTONIC | TSTAMP_SRC_SOE (0x10000) | DONE (0x4) — only the time stamp type bits decide
        let i = SampleInfo::from_v4l2(12, 345_678, 0x0001_2004, 7);
        assert_eq!(i.driver_ns, Some(12_345_678_000));
        assert_eq!(i.driver_clock, Some(DriverClock::V4l2Monotonic));
        assert_eq!(i.driver_sequence, Some(7));
        assert_eq!(
            i.driver_time_source,
            Some(DriverTimeSource::StartOfExposure)
        );
    }

    #[test]
    fn the_v4l2_time_stamp_source_is_what_the_flags_say() {
        let src = |flags| SampleInfo::from_v4l2(1, 0, flags, 0).driver_time_source;
        assert_eq!(
            src(0x0000_2000),
            Some(DriverTimeSource::EndOfFrame),
            "no source bits = end of frame"
        );
        assert_eq!(src(0x0001_2000), Some(DriverTimeSource::StartOfExposure));
        assert_eq!(src(0x0002_2000), None, "a source value V4L2 does not name");
    }

    #[test]
    fn no_time_stamp_source_without_a_monotonic_v4l2_time() {
        assert_eq!(
            SampleInfo::from_v4l2(1, 0, 0x0001_4000, 0).driver_time_source,
            None,
            "COPY clock"
        );
        assert_eq!(
            SampleInfo::from_v4l2(1, 0, 0x0001_0000, 0).driver_time_source,
            None,
            "UNKNOWN clock"
        );
        assert_eq!(
            SampleInfo::from_media_foundation(5).driver_time_source,
            None
        );
    }

    #[test]
    fn a_v4l2_time_stamp_on_another_clock_is_dropped_but_the_sequence_is_kept() {
        for flags in [0x0000_0000, 0x0000_4000, 0x0000_6000] {
            // TIMESTAMP_UNKNOWN, TIMESTAMP_COPY, and MONOTONIC|COPY (not a valid type)
            let i = SampleInfo::from_v4l2(12, 345_678, flags, 9);
            assert_eq!(
                (i.driver_ns, i.driver_clock),
                (None, None),
                "flags {flags:#x}"
            );
            assert_eq!(i.driver_sequence, Some(9));
        }
    }

    /// A backend that only implements `read_jpeg` reports nothing.
    struct OnlyJpeg;
    impl Capture for OnlyJpeg {
        fn mode(&self) -> NegotiatedMode {
            fake::FakeCapture::new(vec![], 1, 1).mode
        }
        fn device_name(&self) -> &str {
            "only-jpeg"
        }
        fn read_jpeg(&mut self) -> Result<Vec<u8>> {
            Ok(vec![1, 2, 3])
        }
        fn controls_backend(&self) -> Result<Box<dyn Backend>> {
            Err(SdkError::Unavailable("none".into()))
        }
    }

    #[test]
    fn the_default_read_sample_gives_the_bytes_and_no_driver_report() {
        let (b, i) = OnlyJpeg.read_sample().unwrap();
        assert_eq!(b, [1, 2, 3]);
        assert_eq!(
            i,
            SampleInfo {
                driver_ns: None,
                driver_clock: None,
                driver_sequence: None,
                driver_time_source: None
            }
        );
    }
}
