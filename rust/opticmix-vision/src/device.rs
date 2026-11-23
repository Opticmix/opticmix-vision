//! One camera: capture → decode → left/right split → stall check (same order as Python `Device`).

use std::sync::{Arc, OnceLock};
use std::time::Instant;

use crate::capture::{open_platform, Capture};
use crate::config::{DeviceConfig, NegotiatedMode};
use crate::controls::Controls;
use crate::decode::decode_mjpg_gray;
use crate::error::{Result, SdkError};
use crate::led::LedControl;
use crate::stall::StallCheck;
use crate::stereo::{split_stereo, GrayImage};

#[derive(Debug, Clone, PartialEq)]
pub struct StereoFrame {
    pub left: GrayImage,
    pub right: GrayImage,
    /// Monotonic time in this process (ns). Not comparable across processes.
    pub host_ns: u64,
    /// Frame number from this Device, starting at 0 (not a device number). Counts delivered frames only:
    /// a skipped sample (see `Device::read`) takes no number, so a gap does not show here — see
    /// `Device::dropped_frames`.
    pub index: u64,
}

impl StereoFrame {
    /// (width, height) — one eye.
    pub fn size(&self) -> (u32, u32) {
        (self.left.width, self.left.height)
    }
}

/// `Device::read` skips an MJPG sample it cannot decode and reads the next one; this many undecodable samples
/// in a row end the call with `SdkError::Device` instead, so a stream that stays broken is not swallowed.
pub const MAX_UNDECODABLE_IN_A_ROW: u32 = 5;

fn host_ns() -> u64 {
    static T0: OnceLock<Instant> = OnceLock::new();
    T0.get_or_init(Instant::now).elapsed().as_nanos() as u64
}

/// One open camera — `read()` gives a pair that went through capture → MJPG decode → left/right split → stall check. Dropping it also closes the controls.
pub struct Device {
    cfg: DeviceConfig,
    cap: Box<dyn Capture>,
    stall: StallCheck,
    index: u64,
    dropped: u64,
    controls: Option<Arc<Controls>>,
}

impl Device {
    /// Opens with this platform's capture backend. Check the negotiated mode with `mode()` — it can differ from the request.
    ///
    /// Windows: initializes COM on the calling thread as multithreaded (MTA) and leaves it so — open from a
    /// non-UI thread.
    pub fn open(cfg: DeviceConfig) -> Result<Device> {
        cfg.validate()?;
        let cap = open_platform(&cfg)?;
        Ok(Device::wrap(cfg, cap))
    }

    /// Opens with your own capture backend (for tests and other capture paths).
    pub fn open_with(cfg: DeviceConfig, cap: Box<dyn Capture>) -> Result<Device> {
        cfg.validate()?;
        Ok(Device::wrap(cfg, cap))
    }

    fn wrap(cfg: DeviceConfig, cap: Box<dyn Capture>) -> Device {
        let stall = StallCheck::new(cfg.stall_frames);
        Device { cfg, cap, stall, index: 0, dropped: 0, controls: None }
    }

    pub fn config(&self) -> &DeviceConfig {
        &self.cfg
    }

    pub fn mode(&self) -> NegotiatedMode {
        self.cap.mode()
    }

    /// Name the opened device reports — confirms what was opened when opening by index (`DeviceSelector::Index`).
    pub fn device_name(&self) -> &str {
        self.cap.device_name()
    }

    /// MJPG bytes before the split, and the capture time. The bytes are as the capture backend gave them —
    /// nothing is checked or skipped here, and they do not count in `dropped_frames`.
    pub fn read_raw(&mut self) -> Result<(Vec<u8>, u64)> {
        let b = self.cap.read_jpeg()?;
        Ok((b, host_ns()))
    }

    /// Next frame as left/right 8-bit gray. Never an empty frame.
    ///
    /// On one camera on Windows, MJPG samples without a JPEG start marker have been seen (through Media
    /// Foundation and through DirectShow); where the start is lost is not established. A sample that does not decode is skipped: the
    /// next one is read and `dropped_frames` goes up by one. `MAX_UNDECODABLE_IN_A_ROW` such samples in a
    /// row give `SdkError::Device`. A skipped sample takes no `index` and is not seen by the stall check,
    /// which compares delivered frames only. `host_ns` is the time of the sample delivered.
    ///
    /// On Windows a read has no timeout: if the camera stops sending samples, `read()` does not return.
    ///
    /// Not skipped: a read failure (timeout, unplugged camera) and a stall (`stall_frames`) give
    /// `SdkError::Device` at once; a decoded frame the layout cannot split gives `SdkError::InvalidValue`.
    pub fn read(&mut self) -> Result<StereoFrame> {
        let mut bad = 0;
        let (g, t) = loop {
            let (b, t) = self.read_raw()?;
            match decode_mjpg_gray(&b) {
                Ok(g) => break (g, t),
                Err(e) => {
                    self.dropped += 1;
                    bad += 1;
                    if bad >= MAX_UNDECODABLE_IN_A_ROW {
                        return Err(SdkError::Device(format!(
                            "{bad} MJPG frames in a row could not be decoded (the last: {e})"
                        )));
                    }
                }
            }
        };
        let (left, right) = split_stereo(&g, self.cfg.layout, self.cfg.lr_order)?;
        self.stall.check(&left, &right)?;
        let index = self.index;
        self.index += 1;
        Ok(StereoFrame { left, right, host_ns: t, index })
    }

    /// MJPG samples `read()` could not decode and skipped since this Device was opened, including those that
    /// ended a call with `SdkError::Device`.
    pub fn dropped_frames(&self) -> u64 {
        self.dropped
    }

    /// This camera's controls (opened on first call). For writes, one `set_many` at startup is recommended.
    pub fn controls(&mut self) -> Result<Arc<Controls>> {
        if let Some(c) = &self.controls {
            return Ok(c.clone());
        }
        let c = Arc::new(Controls::new(self.cap.controls_backend()?, true, "", None)?);
        self.controls = Some(c.clone());
        Ok(c)
    }

    /// IR LED. Writes go through the controls (`controls()`), so the write guard applies.
    pub fn led(&mut self) -> Result<LedControl> {
        Ok(LedControl::new(self.controls()?))
    }
}

/// Same as Python `Device.close()` — closes the controls and ends pending writes as cancelled, even if an `Arc<Controls>` you took is still alive.
/// Uses `close()` (waits up to 5 s for the worker), the same meaning as Python `Controls.close()`. `close_nowait()` is only for
/// the Python binding's GC, to avoid a GIL deadlock, and Device is not in the binding.
impl Drop for Device {
    fn drop(&mut self) {
        if let Some(c) = self.controls.take() {
            c.close();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::capture::fake::FakeCapture;
    use crate::decode::encode_gray;
    use crate::error::SdkError;
    use crate::stall::stall_message;

    /// 16×8 SBS MJPG with left half = a, right half = b.
    fn sbs(a: u8, b: u8) -> Vec<u8> {
        let px: Vec<u8> = (0..8).flat_map(|_| [vec![a; 8], vec![b; 8]].concat()).collect();
        encode_gray(16, 8, &px)
    }

    fn mean(g: &GrayImage) -> f64 {
        g.data.iter().map(|&v| v as f64).sum::<f64>() / g.data.len() as f64
    }

    fn cfg() -> DeviceConfig {
        DeviceConfig { width: 16, height: 8, stall_frames: 3, ..DeviceConfig::weevia_s1() }
    }

    #[test]
    fn first_is_right_gives_the_second_half_as_the_left_eye() {
        let mut d = Device::open_with(cfg(), Box::new(FakeCapture::new(vec![sbs(40, 200)], 16, 8))).unwrap();
        let f = d.read().unwrap();
        assert_eq!(f.size(), (8, 8));
        assert!((mean(&f.left) - 200.0).abs() < 3.0, "{}", mean(&f.left));
        assert!((mean(&f.right) - 40.0).abs() < 3.0, "{}", mean(&f.right));
        assert_eq!(f.index, 0);
    }

    #[test]
    fn index_counts_up_and_time_moves_forward() {
        let frames = vec![sbs(10, 20), sbs(30, 40)];
        let mut d = Device::open_with(cfg(), Box::new(FakeCapture::new(frames, 16, 8))).unwrap();
        let (a, b) = (d.read().unwrap(), d.read().unwrap());
        assert_eq!((a.index, b.index), (0, 1));
        assert!(b.host_ns >= a.host_ns);
    }

    #[test]
    fn a_stalled_stream_is_an_error_and_does_not_count() {
        let mut frames = vec![sbs(10, 20); 3];
        frames.push(sbs(30, 40));
        let mut d = Device::open_with(cfg(), Box::new(FakeCapture::new(frames, 16, 8))).unwrap();
        d.read().unwrap();
        d.read().unwrap();
        assert_eq!(d.read().unwrap_err(), SdkError::Device(stall_message("left", 3)));
        assert_eq!(d.read().unwrap().index, 2);
    }

    /// A sample that starts without SOI and ends with FFD9, built here by removing the front of a normal MJPG.
    fn no_soi() -> Vec<u8> {
        let b = sbs(50, 60);
        assert!(b.ends_with(&[0xFF, 0xD9]));
        b[b.len() / 2..].to_vec()
    }

    #[test]
    fn one_undecodable_sample_is_skipped_and_counted() {
        let frames = vec![sbs(10, 20), no_soi(), sbs(30, 40)];
        let mut d = Device::open_with(cfg(), Box::new(FakeCapture::new(frames, 16, 8))).unwrap();
        assert_eq!(d.read().unwrap().index, 0);
        let f = d.read().unwrap();
        assert_eq!(f.index, 1, "a skipped sample takes no index");
        assert!((mean(&f.left) - 40.0).abs() < 3.0, "the frame after the bad one: {}", mean(&f.left));
        assert_eq!(d.dropped_frames(), 1);
    }

    #[test]
    fn max_undecodable_samples_in_a_row_are_an_error() {
        let mut frames = vec![no_soi(); MAX_UNDECODABLE_IN_A_ROW as usize];
        frames.push(sbs(30, 40));
        let mut d = Device::open_with(cfg(), Box::new(FakeCapture::new(frames, 16, 8))).unwrap();
        let e = d.read().unwrap_err();
        assert!(matches!(&e, SdkError::Device(m) if m.contains("in a row could not be decoded")), "{e:?}");
        assert_eq!(d.dropped_frames(), MAX_UNDECODABLE_IN_A_ROW as u64);
        assert_eq!(d.read().unwrap().index, 0, "the stream goes on after the error");
    }

    #[test]
    fn one_less_than_the_limit_still_delivers_the_next_frame() {
        let mut frames = vec![no_soi(); MAX_UNDECODABLE_IN_A_ROW as usize - 1];
        frames.push(sbs(30, 40));
        let mut d = Device::open_with(cfg(), Box::new(FakeCapture::new(frames, 16, 8))).unwrap();
        assert_eq!(d.read().unwrap().index, 0);
        assert_eq!(d.dropped_frames(), MAX_UNDECODABLE_IN_A_ROW as u64 - 1);
    }

    #[test]
    fn a_clean_stream_drops_nothing() {
        let frames = vec![sbs(10, 20), sbs(30, 40), sbs(50, 60)];
        let mut d = Device::open_with(cfg(), Box::new(FakeCapture::new(frames, 16, 8))).unwrap();
        for _ in 0..3 {
            d.read().unwrap();
        }
        assert_eq!(d.dropped_frames(), 0);
    }

    #[test]
    fn a_skipped_sample_does_not_break_the_stall_run() {
        // 같은 그림 둘 사이에 깨진 샘플 — 멈춤 검사는 넘겨준 프레임끼리만 비교한다(stall_frames 3 = 셋째 같은 프레임에서 오류).
        let frames = vec![sbs(10, 20), sbs(10, 20), no_soi(), sbs(10, 20)];
        let mut d = Device::open_with(cfg(), Box::new(FakeCapture::new(frames, 16, 8))).unwrap();
        d.read().unwrap();
        d.read().unwrap();
        assert_eq!(d.read().unwrap_err(), SdkError::Device(stall_message("left", 3)));
        assert_eq!(d.dropped_frames(), 1);
    }

    #[test]
    fn read_raw_hands_back_undecodable_bytes_as_they_are() {
        let bad = no_soi();
        let mut d = Device::open_with(cfg(), Box::new(FakeCapture::new(vec![bad.clone()], 16, 8))).unwrap();
        assert_eq!(d.read_raw().unwrap().0, bad);
        assert_eq!(d.dropped_frames(), 0);
    }

    #[test]
    fn a_read_failure_is_not_skipped() {
        let mut d = Device::open_with(cfg(), Box::new(FakeCapture::new(vec![no_soi()], 16, 8))).unwrap();
        let e = d.read().unwrap_err();
        assert_eq!(e, SdkError::Device("fake: no more frames".into()));
        assert_eq!(d.dropped_frames(), 1);
    }

    #[test]
    fn a_device_can_move_to_another_thread() {
        fn assert_send<T: Send>() {}
        assert_send::<Device>();
    }

    #[test]
    fn dropping_the_device_closes_its_controls_and_cancels_pending_writes() {
        use crate::controls::Status;
        use crate::convert::UserValue::Int;
        let _l = crate::guard::test_lock();
        crate::guard::set_config(crate::guard::GuardConfig {
            group_intervals: Default::default(),
            global_gap_s: 10.0, // 둘째 쓰기가 drop 전에 나가지 않게(controls 의 닫기 시험과 같은 길이)
            warn_after_total: 1_000_000_000,
            ledger: None,
        });
        crate::guard::reset_write_counts();
        let mut d = Device::open_with(cfg(), Box::new(FakeCapture::new(vec![], 16, 8))).unwrap();
        let c = d.controls().unwrap();
        c.set("gain", Int(40)).unwrap();
        let r = c.set("gain", Int(41)).unwrap();
        assert_eq!(r.status, Status::Pending);
        drop(d);
        assert!(c.closed());
        assert_eq!(c.result(r.ticket).unwrap().status, Status::Cancelled);
    }

    #[test]
    fn the_device_name_comes_from_the_capture_backend() {
        let d = Device::open_with(cfg(), Box::new(FakeCapture::new(vec![], 16, 8))).unwrap();
        assert_eq!(d.device_name(), "fake");
    }

    #[test]
    fn running_out_of_frames_is_a_device_error() {
        let mut d = Device::open_with(cfg(), Box::new(FakeCapture::new(vec![], 16, 8))).unwrap();
        assert!(matches!(d.read(), Err(SdkError::Device(_))));
    }

    #[test]
    fn a_bad_config_does_not_open() {
        let bad = DeviceConfig { stall_frames: 1, ..cfg() };
        assert!(Device::open_with(bad, Box::new(FakeCapture::new(vec![], 16, 8))).is_err());
    }

    #[test]
    fn controls_open_once_and_are_shared() {
        let mut d = Device::open_with(cfg(), Box::new(FakeCapture::new(vec![], 16, 8))).unwrap();
        let (a, b) = (d.controls().unwrap(), d.controls().unwrap());
        assert!(Arc::ptr_eq(&a, &b));
    }

    #[test]
    fn led_goes_through_the_same_controls() {
        // 가드 켠 제어로 쓴다 — 전역 쓰기 카운터를 보는 시험들과 줄을 선다(controls::tests::fast 와 같은 설정).
        let _l = crate::guard::test_lock();
        crate::guard::set_config(crate::guard::GuardConfig {
            group_intervals: Default::default(),
            global_gap_s: 0.0,
            warn_after_total: 1_000_000_000,
            ledger: None,
        });
        let mut d = Device::open_with(cfg(), Box::new(FakeCapture::new(vec![], 16, 8))).unwrap();
        d.led().unwrap().set(30).unwrap();
        assert_eq!(d.led().unwrap().get().unwrap(), 30);
    }
}
