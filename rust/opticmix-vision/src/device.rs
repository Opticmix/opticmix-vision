//! One camera: capture → decode → left/right split → stall check (same order as Python `Device`).

use std::collections::VecDeque;
use std::sync::{Arc, OnceLock};
use std::time::Instant;

use crate::capture::{open_platform, Capture, DriverClock, DriverTimeSource, SampleInfo};
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
    /// Monotonic time in this process (ns), taken right after the capture backend handed over the sample.
    /// Not comparable across processes.
    pub host_ns: u64,
    /// Frame number from this Device, starting at 0 (not a device number). Counts delivered frames only:
    /// a skipped sample (see `Device::read`) takes no number, so a gap does not show here — see
    /// `Device::undecodable_frames` and `Device::driver_missed_frames`. From a recording
    /// (`Device::open_recording`): the frame's number in the file, and `host_ns` and the driver fields are the
    /// recorded ones.
    pub index: u64,
    /// The capture driver's time stamp for this frame in ns, on the clock `driver_clock` names: Windows = Media
    /// Foundation sample time (not counted from the start of streaming — about 364,400 s at the first frame on
    /// one PC with one camera, once; zero not established; use differences), Linux = V4L2 buffer time on
    /// `CLOCK_MONOTONIC`.
    /// `None` when the capture backend gives none (a Linux buffer not flagged monotonic, your own `Capture`).
    /// An operating-system driver time, not a camera clock — the camera sends none this crate reads. Whether it
    /// marks the exposure or the arrival of the frame is not known (not measured).
    pub driver_ns: Option<i64>,
    /// Which clock `driver_ns` is on; `Some` exactly when `driver_ns` is.
    pub driver_clock: Option<DriverClock>,
    /// The driver's frame sequence number (Linux: V4L2 `v4l2_buffer.sequence`). `None` on Windows — Media
    /// Foundation gives none. A gap between frames is what `Device::driver_missed_frames` counts.
    pub driver_sequence: Option<u32>,
    /// Linux: where the V4L2 driver says it took `driver_ns` (start of exposure or end of frame), from the buffer
    /// flags. The value the driver reports, not checked on the camera. `None` on Windows and without `driver_ns`.
    pub driver_time_source: Option<DriverTimeSource>,
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
    #[cfg(test)]
    if let Some(t) = tests::FAKE_HOST_NS.with(|q| q.borrow_mut().pop_front()) {
        return t;
    }
    static T0: OnceLock<Instant> = OnceLock::new();
    T0.get_or_init(Instant::now).elapsed().as_nanos() as u64
}

/// Late and missed frames estimated from the intervals between `host_ns` times, and the recent frame rate —
/// the rule of the Python package's `Device.late_frames`, `estimated_missed_frames` and `actual_fps`: an
/// interval longer than 1.5 frame periods (at the negotiated fps) is one late frame, and
/// round(interval / period) - 1 frames (rounded half to even, as Python's `round`) are counted as missed.
pub(crate) struct FrameStats {
    fps: f64,
    pub(crate) late: u64,
    pub(crate) missed: u64,
    /// The last `FPS_WINDOW + 1` host times.
    recent: VecDeque<u64>,
}

/// `actual_fps` is over this many intervals (Python `_FrameStats.WINDOW`).
const FPS_WINDOW: usize = 30;

impl FrameStats {
    pub(crate) fn new(fps: f64) -> FrameStats {
        FrameStats { fps, late: 0, missed: 0, recent: VecDeque::with_capacity(FPS_WINDOW + 1) }
    }

    /// The next time starts a new run: no interval to the time before it (a seek or loop in a recording, a pause
    /// in recording) — Python `_FrameStats.add(..., restart=True)`.
    pub(crate) fn restart(&mut self) {
        self.recent.clear();
    }

    pub(crate) fn add(&mut self, ns: u64) {
        if let Some(&last) = self.recent.back().filter(|_| self.fps > 0.0) {
            let period = 1e9 / self.fps;
            let dt = ns.saturating_sub(last) as f64;
            if dt > 1.5 * period {
                self.late += 1;
                self.missed += (dt / period).round_ties_even() as u64 - 1;
            }
        }
        if self.recent.len() > FPS_WINDOW {
            self.recent.pop_front();
        }
        self.recent.push_back(ns);
    }

    fn fps(&self) -> f64 {
        match (self.recent.front(), self.recent.back()) {
            (Some(&a), Some(&b)) if b > a => (self.recent.len() - 1) as f64 * 1e9 / (b - a) as f64,
            _ => 0.0,
        }
    }
}

/// One open camera — `read()` gives a pair that went through capture → MJPG decode → left/right split → stall check. Dropping it also closes the controls.
pub struct Device {
    cfg: DeviceConfig,
    cap: Box<dyn Capture>,
    stall: StallCheck,
    index: u64,
    dropped: u64,
    /// Last driver sequence number seen, and the frames missing between the numbers seen so far.
    last_sequence: Option<u32>,
    driver_missed: u64,
    stats: FrameStats,
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

    /// Plays a recording — one `Recorder` wrote, or an MJPG `.avi` the Python package's `Recorder` wrote — as if it
    /// were the camera: the same `read()` gives `StereoFrame`s, and after the last frame `SdkError::EndOfRecording`.
    /// A frame's `index` is its number in the file, `host_ns` and the driver fields are the times recorded in the
    /// CSV (see `recording`). The config is the recording's: side by side, left eye first, the recorded mode,
    /// no stall check. `playback()` seeks, loops and paces. A recording has no controls (`controls()` gives
    /// `SdkError::Unavailable`). Errors: `SdkError::Recording` (see `Playback::open`). Run with a camera recording
    /// once (one PC, one camera, 2026-11-25: 881 frames, as Python read them).
    pub fn open_recording(path: impl AsRef<std::path::Path>) -> Result<Device> {
        let p = crate::recording::Playback::open(path)?;
        let m = p.mode();
        let cfg = DeviceConfig {
            layout: crate::stereo::Layout::Sbs,
            lr_order: crate::stereo::LrOrder::FirstIsLeft,
            device: crate::config::DeviceSelector::Path(p.path().display().to_string()),
            api: crate::config::CaptureApi::Auto,
            width: m.width,
            height: m.height,
            fps: if m.fps > 0.0 { m.fps } else { 30.0 },
            stall_frames: 0,
        };
        Device::open_with(cfg, Box::new(p))
    }

    /// The recording this Device plays (`open_recording`): seek, loop, real-time pacing, frame count; `None` for a
    /// camera.
    pub fn playback(&mut self) -> Option<&mut crate::recording::Playback> {
        self.cap.playback()
    }

    /// Opens with your own capture backend (for tests and other capture paths).
    pub fn open_with(cfg: DeviceConfig, cap: Box<dyn Capture>) -> Result<Device> {
        cfg.validate()?;
        Ok(Device::wrap(cfg, cap))
    }

    fn wrap(cfg: DeviceConfig, cap: Box<dyn Capture>) -> Device {
        let stall = StallCheck::new(cfg.stall_frames);
        let stats = FrameStats::new(cap.mode().fps);
        Device { cfg, cap, stall, index: 0, dropped: 0, last_sequence: None, driver_missed: 0, stats, controls: None }
    }

    pub fn config(&self) -> &DeviceConfig {
        &self.cfg
    }

    pub fn mode(&self) -> NegotiatedMode {
        self.cap.mode()
    }

    /// This camera's calibration: the file installed with `python -m opticmix_vision.calib add <file.json>`, when
    /// exactly one is installed (`calib::resolve()`), after checking its eye size against `mode()`
    /// (`StereoCalibration::check_eye_size`) — the rules of the Python package's `Device.calibration`. The crate
    /// cannot check that the file belongs to this camera (no USB serial). Errors: `SdkError::CalibLookup` (none or
    /// several installed, another mode), `SdkError::Calibration` (a broken file). Reads the file on each call.
    /// Capture does not need a calibration.
    pub fn calibration(&self) -> Result<crate::calib::StereoCalibration> {
        let c = crate::calib::resolve()?;
        let m = self.mode();
        c.check_eye_size(m.width, m.height)?;
        Ok(c)
    }

    /// Name the opened device reports — confirms what was opened when opening by index (`DeviceSelector::Index`).
    pub fn device_name(&self) -> &str {
        self.cap.device_name()
    }

    /// MJPG bytes before the split, and the capture time. The bytes are as the capture backend gave them —
    /// nothing is checked or skipped here, and they do not count in `undecodable_frames`. The driver sequence
    /// number, where there is one, is still followed, so `driver_missed_frames` stays right when `read_raw` and
    /// `read` are mixed.
    pub fn read_raw(&mut self) -> Result<(Vec<u8>, u64)> {
        self.sample().map(|(b, t, _)| (b, t))
    }

    /// Every read goes through here, so a sample `read_raw` took or `read` skipped still counts in the sequence
    /// and in the frame statistics.
    fn sample(&mut self) -> Result<(Vec<u8>, u64, SampleInfo)> {
        let (b, info) = self.cap.read_sample()?;
        // a recording's frames carry their recorded time, and its statistics describe the recording
        let t = match self.cap.playback() {
            Some(p) => {
                if p.starts_over() {
                    self.stats.restart();
                }
                p.recorded_ns(p.position())
            }
            None => host_ns(),
        };
        self.stats.add(t);
        if let Some(s) = info.driver_sequence {
            // A number that goes back or repeats (and a u32 wrap, years at 90 fps) restarts the count
            // from there instead of being read as a gap.
            if let Some(p) = self.last_sequence.filter(|&p| s > p) {
                self.driver_missed += u64::from(s - p - 1);
            }
            self.last_sequence = Some(s);
        }
        Ok((b, t, info))
    }

    /// Next frame as left/right 8-bit gray. Never an empty frame.
    ///
    /// On one camera on Windows, MJPG samples without a JPEG start marker have been seen (through Media
    /// Foundation and through DirectShow); where the start is lost is not established. A sample that does not decode is skipped: the
    /// next one is read and `undecodable_frames` goes up by one. `MAX_UNDECODABLE_IN_A_ROW` such samples in a
    /// row give `SdkError::Device`. A skipped sample takes no `index` and is not seen by the stall check,
    /// which compares delivered frames only. `host_ns` and the driver fields are those of the sample delivered.
    ///
    /// On Windows a read has no timeout: if the camera stops sending samples, `read()` does not return.
    ///
    /// Not skipped: a read failure (timeout, unplugged camera) and a stall (`stall_frames`) give
    /// `SdkError::Device` at once; a decoded frame the layout cannot split gives `SdkError::InvalidValue`.
    pub fn read(&mut self) -> Result<StereoFrame> {
        let mut bad = 0;
        let (g, t, info) = loop {
            let (b, t, info) = self.sample()?;
            match decode_mjpg_gray(&b) {
                Ok(g) => break (g, t, info),
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
        let index = match self.cap.playback() {
            Some(p) => p.position() as u64,
            None => self.index,
        };
        self.index += 1;
        Ok(StereoFrame {
            left,
            right,
            host_ns: t,
            index,
            driver_ns: info.driver_ns,
            driver_clock: info.driver_clock,
            driver_sequence: info.driver_sequence,
            driver_time_source: info.driver_time_source,
        })
    }

    /// MJPG samples `read()` could not decode and skipped since this Device was opened, including those that
    /// ended a call with `SdkError::Device`. These samples did arrive; frames that never arrived are
    /// `driver_missed_frames` (Linux, from the driver's numbers) or `estimated_missed_frames` (from host times).
    /// The Python package has no such count (OpenCV decodes there).
    pub fn undecodable_frames(&self) -> u64 {
        self.dropped
    }

    /// The same count as `undecodable_frames` (this is its earlier name, kept so existing code builds): MJPG
    /// samples that arrived but did not decode — not frames lost on the way. For frames that never arrived use
    /// `estimated_missed_frames()` (Windows and Linux) or `driver_missed_frames()` (Linux).
    pub fn dropped_frames(&self) -> u64 {
        self.dropped
    }

    /// Frames the capture driver numbered but this Device never received, since the first sample it read: the
    /// sum of the gaps in `driver_sequence` (numbers 0, 1, 2, 5, 6 give 2). Counted from the driver's numbers, not
    /// estimated from times; samples `read()` skipped as undecodable are not in it. `None` until a sample with a
    /// sequence number arrives, and always on Windows (Media Foundation gives no sequence number); there, and
    /// on Linux beside it, `estimated_missed_frames` estimates the missing frames from host times.
    ///
    /// The V4L2 documentation calls these dropped frames: received by the driver but not stored for lack of a
    /// free buffer. It also says the count may leave out frames the hardware on the other side of the USB
    /// link dropped — so this is not a count of every frame the camera failed to send.
    pub fn driver_missed_frames(&self) -> Option<u64> {
        self.last_sequence.map(|_| self.driver_missed)
    }

    /// Samples since this Device was opened that were received by `read()` (or `read_raw()`) more than 1.5
    /// frame periods (at `mode().fps`) after the sample before. Estimated from host times (`host_ns`, taken when
    /// the read got the sample), not read from a device frame counter: a late frame can be delivery jitter
    /// rather than a lost frame. Same name and rule as the Python package's `Device.late_frames`, on Windows
    /// and Linux alike. Every sample counts — those `read_raw` returns and those `read()` skips as undecodable
    /// too (Python counts the frames `read()` returns).
    ///
    /// A read loop slower than the frame period makes its own long intervals, which count here as late and
    /// missed frames just as a frame lost on the camera side would; the two cannot be told apart from host
    /// times. If this is not 0: build with `--release`, connect the camera directly to a USB 3 port, and move
    /// your processing to another thread (see the `threaded_capture` example).
    pub fn late_frames(&self) -> u64 {
        self.stats.late
    }

    /// Frames estimated missing since this Device was opened: for each late interval,
    /// `round(interval / period) - 1`, rounded half to even like Python's `round` (Python
    /// `Device.estimated_missed_frames`). Estimated from host times, not a device counter. Not the same count
    /// as `driver_missed_frames` — on Linux that one is counted from the driver's sequence numbers, so the two
    /// can differ — nor `undecodable_frames`: an undecodable sample did arrive and is not missing here. A read
    /// loop slower than the frame period is counted here too (see `late_frames`).
    pub fn estimated_missed_frames(&self) -> u64 {
        self.stats.missed
    }

    /// Samples per second over the last 30 intervals, from host times (`host_ns`); 0.0 until two samples have
    /// been read (Python `Device.actual_fps`). Undecodable samples `read()` skipped are counted: this is the
    /// rate the capture backend hands samples over, not the rate `read()` returns frames when samples are skipped.
    pub fn actual_fps(&self) -> f64 {
        self.stats.fps()
    }

    /// Frames `read()` decoded since this Device was opened whose two eyes were both byte-identical to the frame
    /// before, counted by the comparison the `stall_frames` check already makes, so it costs nothing more. A
    /// frame that ends the call with the stall error counts too. `None` when `stall_frames` is 0 (no comparison
    /// is made). The same name and meaning as the Python package's `Device.repeated_frames`. Unlike
    /// `late_frames()`, which counts every sample, only the frames `read()` decoded are compared: samples it
    /// skipped as undecodable and the bytes `read_raw()` returns are not. A count only, with no warning (the
    /// Python package warns through OpenCV's Media Foundation backend only). Run with the camera once (one PC,
    /// one camera, 2026-11-25, Media Foundation, 450 frames): 0, the same as the `measure_fps` example's own
    /// per-eye counts (left 0, right 0). A count above 0: generated frames only.
    pub fn repeated_frames(&self) -> Option<u64> {
        self.stall.repeated()
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
    use std::cell::RefCell;

    thread_local! {
        /// Host times `host_ns()` hands out on this test thread, in turn, before it falls back to the clock.
        pub(super) static FAKE_HOST_NS: RefCell<VecDeque<u64>> = RefCell::default();
    }

    /// The next reads on this thread happen at these host times (ms).
    fn host_times_ms(ms: &[u64]) {
        FAKE_HOST_NS.with(|q| *q.borrow_mut() = ms.iter().map(|m| m * 1_000_000).collect());
    }

    fn stats_of(fps: f64, ns: &[u64]) -> FrameStats {
        let mut s = FrameStats::new(fps);
        ns.iter().for_each(|&t| s.add(t));
        s
    }

    fn ms(v: &[u64]) -> Vec<u64> {
        v.iter().map(|m| m * 1_000_000).collect()
    }

    #[test]
    fn late_and_missed_frames_follow_the_python_rule() {
        // Python test_late_and_missed_frames_come_from_host_intervals: 100 fps = 10 ms. Gaps 10, 14 (under 1.5
        // periods), 30 (late, 2 missed), 10, 16 (late, 1 missed), 10.
        let s = stats_of(100.0, &ms(&[0, 10, 24, 54, 64, 80, 90]));
        assert_eq!((s.late, s.missed), (2, 3));
        assert!((s.fps() - 6.0 / 0.090).abs() < 1e-9, "{}", s.fps());
        assert_eq!(stats_of(100.0, &ms(&[0])).fps(), 0.0, "0.0 until two samples");
    }

    #[test]
    fn one_and_a_half_periods_is_not_late_and_a_nanosecond_more_is() {
        assert_eq!(stats_of(100.0, &[0, 15_000_000]).late, 0);
        let s = stats_of(100.0, &[0, 15_000_001]);
        assert_eq!((s.late, s.missed), (1, 1));
    }

    #[test]
    fn missed_frames_round_half_to_even_as_python_does() {
        // 25 ms = 2.5 periods: Python round(2.5) = 2, 1 missed (round half away from zero would give 2).
        // 35 ms = 3.5 periods: round(3.5) = 4, 3 missed.
        let s = stats_of(100.0, &ms(&[0, 25, 60]));
        assert_eq!((s.late, s.missed), (2, 4));
    }

    #[test]
    fn actual_fps_is_over_the_last_30_intervals() {
        // 11 intervals of 20 ms, then 29 of 10 ms: the last 30 are one of 20 ms and 29 of 10 ms, 310 ms in all
        // (a window of 29 or 31 would give another rate).
        let mut t = 0;
        let steps = (0..40).map(|i| {
            t += if i < 11 { 20_000_000 } else { 10_000_000 };
            t
        });
        let ns: Vec<u64> = std::iter::once(0).chain(steps).collect();
        assert!((stats_of(100.0, &ns).fps() - 30.0 / 0.310).abs() < 1e-9, "{}", stats_of(100.0, &ns).fps());
        assert!((stats_of(100.0, &ns[..12]).fps() - 50.0).abs() < 1e-9, "fewer than 30 intervals: all of them");
    }

    #[test]
    fn without_a_negotiated_rate_nothing_is_late_but_the_rate_is_measured() {
        let s = stats_of(0.0, &ms(&[0, 10, 50]));
        assert_eq!((s.late, s.missed), (0, 0));
        assert!((s.fps() - 40.0).abs() < 1e-9);
        assert_eq!(stats_of(90.0, &[7, 7]).fps(), 0.0, "no time between them");
    }

    fn at_100_fps(frames: Vec<Vec<u8>>) -> Device {
        let mut cap = FakeCapture::new(frames, 16, 8);
        cap.mode.fps = 100.0;
        Device::open_with(cfg(), Box::new(cap)).unwrap()
    }

    #[test]
    fn the_device_counts_late_and_missed_frames_from_host_times() {
        let mut d = at_100_fps(frames(7));
        assert_eq!((d.late_frames(), d.estimated_missed_frames(), d.actual_fps()), (0, 0, 0.0));
        host_times_ms(&[0, 10, 24, 54, 64, 80, 90]);
        let got: Vec<u64> = (0..7).map(|_| d.read().unwrap().host_ns).collect();
        assert_eq!(got, ms(&[0, 10, 24, 54, 64, 80, 90]), "the frames carry the same host times");
        assert_eq!((d.late_frames(), d.estimated_missed_frames()), (2, 3));
        assert!((d.actual_fps() - 6.0 / 0.090).abs() < 1e-9, "{}", d.actual_fps());
    }

    #[test]
    fn an_undecodable_sample_arrived_so_it_is_not_missed() {
        // every 10 ms at 100 fps, the second sample does not decode: no gap, one undecodable sample
        let mut d = at_100_fps(vec![sbs(10, 20), no_soi(), sbs(30, 40), sbs(50, 60)]);
        host_times_ms(&[0, 10, 20, 30]);
        d.read().unwrap();
        d.read().unwrap();
        d.read().unwrap();
        assert_eq!((d.late_frames(), d.estimated_missed_frames(), d.undecodable_frames()), (0, 0, 1));
        assert!((d.actual_fps() - 100.0).abs() < 1e-9, "{}", d.actual_fps());
    }

    #[test]
    fn a_sample_read_raw_took_is_not_a_gap() {
        let mut d = at_100_fps(frames(3));
        host_times_ms(&[0, 10, 20]);
        d.read().unwrap();
        d.read_raw().unwrap();
        d.read().unwrap();
        assert_eq!((d.late_frames(), d.estimated_missed_frames()), (0, 0), "0 -> 20 ms would be 1 late");
    }

    /// Runs the Python package's own `_FrameStats` class (cut out of device.py, so numpy is not needed) on the
    /// same host times and compares every number, the frame rate bit for bit. Skipped outside the repository
    /// (no ../../python); there, a Python that does not run is a failure, not a pass.
    #[test]
    fn the_statistics_are_those_of_the_python_class() {
        let dir = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../python");
        if !dir.is_dir() {
            return;
        }
        let p = dir.join("opticmix_vision/device.py");
        let src = std::fs::read_to_string(&p).unwrap_or_else(|e| panic!("{}: {e}", p.display()));
        let class: Vec<&str> = src
            .lines()
            .skip_while(|l| !l.starts_with("class _FrameStats"))
            .enumerate()
            .take_while(|(i, l)| *i == 0 || l.is_empty() || l.starts_with(' '))
            .map(|(_, l)| l)
            .collect();
        assert!(class.len() > 10 && class.iter().any(|l| l.contains("def fps")), "_FrameStats not found in {}", p.display());

        // a jittery 90 fps stream with gaps of 2..5 periods (a fixed pseudo-random sequence)
        let (mut x, mut t) = (12345u64, 0u64);
        let jitter: Vec<u64> = (0..400)
            .map(|i| {
                x = x.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
                let r = x >> 33;
                let periods = if i % 37 == 5 { 2 + r % 4 } else { 1 };
                t += periods * 11_111_111 + r % 6_000_000 - 3_000_000;
                t
            })
            .collect();
        let mut window = (0..40u64).map(|i| if i < 10 { 20 * i } else { 200 + 10 * (i - 10) }).collect::<Vec<_>>();
        window.iter_mut().for_each(|v| *v *= 1_000_000);
        let cases: Vec<(f64, Vec<u64>)> = vec![
            (100.0, ms(&[0, 10, 24, 54, 64, 80, 90])),
            (100.0, ms(&[0, 25, 60, 75])),
            (100.0, vec![0, 15_000_000, 30_000_001]),
            (100.0, window),
            (90.0, jitter),
            (0.0, ms(&[0, 10, 50])),
            (90.0, vec![5]),
            (60.0, vec![7, 7]),
        ];
        assert!(stats_of(90.0, &cases[4].1).late >= 8, "the jittery stream has gaps to compare");
        let lists: Vec<String> = cases.iter().map(|(f, ns)| format!("({f:?}, {ns:?})")).collect();
        let script = format!(
            "from __future__ import annotations\nfrom collections import deque\n{}\n\
             for fps, times in [{}]:\n    s = _FrameStats()\n    for i, t in enumerate(times):\n        s.add(i, t, fps)\n    \
             print(s.late, s.missed, repr(s.fps))\n",
            class.join("\n"),
            lists.join(", ")
        );
        let out = ["python3", "python"]
            .iter()
            .find_map(|py| {
                use std::io::Write;
                use std::process::{Command, Stdio};
                let mut c = Command::new(py)
                    .args(["-I", "-"])
                    .stdin(Stdio::piped())
                    .stdout(Stdio::piped())
                    .stderr(Stdio::piped())
                    .spawn()
                    .ok()?;
                c.stdin.take().unwrap().write_all(script.as_bytes()).ok()?;
                let o = c.wait_with_output().ok()?;
                o.status.success().then(|| String::from_utf8(o.stdout).unwrap())
            })
            .unwrap_or_else(|| panic!("neither python3 nor python ran the Python class:\n{script}"));
        let py: Vec<&str> = out.lines().collect();
        assert_eq!(py.len(), cases.len(), "{out}");
        for ((fps, ns), line) in cases.iter().zip(py) {
            let s = stats_of(*fps, ns);
            let want: Vec<&str> = line.split(' ').collect();
            let rust = (s.late.to_string(), s.missed.to_string(), s.fps().to_bits());
            let python = (want[0].to_string(), want[1].to_string(), want[2].parse::<f64>().unwrap().to_bits());
            assert_eq!(rust, python, "fps {fps}, {} times: Rust {} {} {}, Python {line}", ns.len(), s.late, s.missed, s.fps());
        }
    }

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
        assert_eq!(
            d.repeated_frames(),
            Some(2),
            "the frame that ends in the stall error counts too"
        );
        assert_eq!(d.read().unwrap().index, 2);
    }

    #[test]
    fn repeated_frames_counts_frames_whose_two_eyes_both_repeat() {
        let frames = vec![
            sbs(10, 20),
            sbs(10, 20), // both eyes repeat: 1
            sbs(10, 30), // one eye repeats: not counted
            sbs(40, 30), // the other eye repeats: not counted
            sbs(40, 30), // both: 2
            no_soi(),    // skipped, not compared
            sbs(40, 30), // both, against the frame read() delivered before: 3
            sbs(50, 60),
        ];
        let cfg = DeviceConfig { stall_frames: 90, ..cfg() };
        let mut d = Device::open_with(cfg, Box::new(FakeCapture::new(frames, 16, 8))).unwrap();
        assert_eq!(d.repeated_frames(), Some(0), "nothing read yet");
        let counts: Vec<_> = (0..7)
            .map(|_| d.read().map(|_| d.repeated_frames().unwrap()).unwrap())
            .collect();
        assert_eq!(counts, [0, 1, 1, 1, 2, 3, 3]);
        assert_eq!(d.undecodable_frames(), 1);
    }

    #[test]
    fn repeated_frames_is_none_when_the_stall_check_is_off() {
        let cfg = DeviceConfig { stall_frames: 0, ..cfg() };
        let mut d = Device::open_with(cfg, Box::new(FakeCapture::new(vec![sbs(10, 20); 4], 16, 8))).unwrap();
        for _ in 0..4 {
            d.read().unwrap();
        }
        assert_eq!(d.repeated_frames(), None);
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

    fn seq(s: u32) -> SampleInfo {
        SampleInfo::from_v4l2(100, s as i64 * 11_111, 0x2000, s)
    }

    fn fake_with(frames: Vec<Vec<u8>>, infos: Vec<SampleInfo>) -> Device {
        Device::open_with(cfg(), Box::new(FakeCapture::new(frames, 16, 8).with_infos(infos))).unwrap()
    }

    /// Distinct frames, so the stall check stays quiet.
    fn frames(n: u8) -> Vec<Vec<u8>> {
        (0..n).map(|i| sbs(10 * i, 10 * i + 5)).collect()
    }

    #[test]
    fn gaps_in_the_driver_sequence_count_as_missed_frames() {
        let mut d = fake_with(frames(5), [0, 1, 2, 5, 6].map(seq).to_vec());
        assert_eq!(d.driver_missed_frames(), None, "nothing read yet");
        let got: Vec<_> = (0..5).map(|_| d.read().unwrap()).collect();
        assert_eq!(got.iter().map(|f| f.driver_sequence).collect::<Vec<_>>(), [0, 1, 2, 5, 6].map(Some));
        assert_eq!(got.iter().map(|f| f.index).collect::<Vec<_>>(), [0, 1, 2, 3, 4], "index has no gap");
        assert_eq!(d.driver_missed_frames(), Some(2));
        assert_eq!(d.undecodable_frames(), 0);
        assert_eq!(got[4].driver_ns, Some(100_000_000_000 + 6 * 11_111_000));
        assert_eq!(got[4].driver_clock, Some(DriverClock::V4l2Monotonic));
        assert_eq!(got[4].driver_time_source, Some(DriverTimeSource::EndOfFrame), "flags 0x2000: no source bits");
    }

    #[test]
    fn the_count_starts_at_the_first_sample_read() {
        let mut d = fake_with(frames(3), [40, 41, 43].map(seq).to_vec());
        d.read().unwrap();
        assert_eq!(d.driver_missed_frames(), Some(0), "numbers before 40 are not counted");
        d.read().unwrap();
        d.read().unwrap();
        assert_eq!(d.driver_missed_frames(), Some(1));
    }

    #[test]
    fn a_sequence_that_goes_back_or_repeats_is_not_a_gap() {
        let mut d = fake_with(frames(5), [7, 8, 8, 0, 2].map(seq).to_vec());
        for _ in 0..5 {
            d.read().unwrap();
        }
        assert_eq!(d.driver_missed_frames(), Some(1), "only 0 -> 2 is a gap");
    }

    #[test]
    fn an_undecodable_sample_keeps_its_place_in_the_sequence() {
        // 0, 1 (does not decode), 2: no frame is missing, one is undecodable — the two counts stay apart
        let mut d = fake_with(vec![sbs(10, 20), no_soi(), sbs(30, 40)], [0, 1, 2].map(seq).to_vec());
        assert_eq!(d.read().unwrap().driver_sequence, Some(0));
        assert_eq!(d.read().unwrap().driver_sequence, Some(2));
        assert_eq!(d.driver_missed_frames(), Some(0));
        assert_eq!((d.undecodable_frames(), d.dropped_frames()), (1, 1));
    }

    #[test]
    fn read_raw_follows_the_sequence_too() {
        let mut d = fake_with(frames(3), [0, 1, 3].map(seq).to_vec());
        d.read().unwrap();
        d.read_raw().unwrap();
        assert_eq!(d.read().unwrap().driver_sequence, Some(3));
        assert_eq!(d.driver_missed_frames(), Some(1), "1 -> 3, not 0 -> 3");
    }

    #[test]
    fn media_foundation_sample_times_reach_the_frame_in_ns() {
        let infos = [1_000_000, 1_111_111].map(SampleInfo::from_media_foundation).to_vec();
        let mut d = fake_with(frames(2), infos);
        let (a, b) = (d.read().unwrap(), d.read().unwrap());
        assert_eq!((a.driver_ns, b.driver_ns), (Some(100_000_000), Some(111_111_100)));
        assert_eq!(b.driver_ns.unwrap() - a.driver_ns.unwrap(), 11_111_100, "one interval at 90 fps, in ns");
        assert_eq!(a.driver_clock, Some(DriverClock::MediaFoundation));
        assert_eq!((a.driver_sequence, d.driver_missed_frames()), (None, None), "no sequence on Windows");
        assert_eq!(a.driver_time_source, None);
    }

    #[test]
    fn a_backend_that_reports_nothing_leaves_the_driver_fields_empty() {
        let mut d = Device::open_with(cfg(), Box::new(FakeCapture::new(frames(2), 16, 8))).unwrap();
        let f = d.read().unwrap();
        assert_eq!((f.driver_ns, f.driver_clock, f.driver_sequence), (None, None, None));
        assert_eq!(d.driver_missed_frames(), None);
        // a V4L2 buffer not flagged monotonic: no time, but the sequence number still counts
        let mut d = fake_with(frames(2), vec![SampleInfo::from_v4l2(1, 2, 0x4000, 3), seq(5)]);
        let f = d.read().unwrap();
        assert_eq!((f.driver_ns, f.driver_clock, f.driver_sequence), (None, None, Some(3)));
        d.read().unwrap();
        assert_eq!(d.driver_missed_frames(), Some(1));
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
        let a = c.set("gain", Int(40)).unwrap();
        // Wait for the first write to go out. While it is still pending, 41 replaces it and, with no write before
        // it, is due at once — the worker can send it before the drop (without this wait: 61 of 2500 runs).
        assert_eq!(c.wait(&[a.ticket], Some(5.0))[0].status, Status::Written);
        let r = c.set("gain", Int(41)).unwrap();
        assert_eq!(r.status, Status::Pending);
        assert!(r.due_in_s.unwrap() > 1.0, "held back by the 10 s gap: {:?}", r.due_in_s);
        drop(d);
        assert!(c.closed());
        assert_eq!(c.result(r.ticket).unwrap().status, Status::Cancelled);
        assert_eq!(crate::guard::write_counts()["gain"], 1, "the cancelled write never went out");
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
