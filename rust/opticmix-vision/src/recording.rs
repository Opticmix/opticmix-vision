//! Recording frames to a file and playing a recording back, in the file format of the Python package's
//! `opticmix_vision.recording` — MJPG `.avi` only.
//!
//! A recording is three files with the same name:
//!
//! ```text
//! <name>.avi   both eyes side by side, the left eye in the left half (lr_order "first_is_left"), 8-bit gray,
//!              one JPEG per frame (MJPG; YCbCr 4:2:0 with neutral color, as FFmpeg writes MJPG)
//! <name>.csv   frame,host_ns,driver_ns,driver_clock — when each frame arrived (StereoFrame::host_ns, the
//!              host's monotonic clock, not a camera clock) and the capture driver's time stamp and its clock
//!              name ("media_foundation", "v4l2_monotonic"), empty where the frame had none
//! <name>.json  what was recording: the negotiated mode, the camera's layout and its own left/right order,
//!              the device name, the control values when recording started and ended, the crate version, codec,
//!              frame count, the late / missed frame estimates
//! ```
//!
//! The field names and their meaning are the Python package's, so each reads the other's recordings: a
//! recording made here plays in Python (`Device(DeviceConfig.recording(path))`), and a Python MJPG `.avi`
//! plays here (`Device::open_recording`). Checked on files written from generated frames, with opencv-python
//! 4.14 on Windows on the Python side. Run with the camera once (one PC, one camera, 2026-11-25): a 10 s
//! recording of 881 frames, which Rust and Python played back as 881 frames.
//!
//! What this crate does not do that the Python package does: lossless recording (FFV1 `.mkv` — neither written
//! nor played here), logging control changes (`note_control`), listing the installed calibration files (the JSON
//! has `null` there), replacing a recording of the same name (refused here). Pausing a recording
//! (`Recorder::pause`, the JSON's "breaks"), a write queue (`Recorder::create_with_queue`, the JSON's
//! `write_queue` and `dropped_frames`) and pausing playback (`Playback::set_paused`, `Playback::status`) follow the
//! Python package's rules.
//!
//! Pixels: `Recorder` encodes each frame (the two decoded eyes side by side) to JPEG again — as the Python
//! package does — so the values differ a little from those `Device::read` gave. The camera's own MJPG bytes are
//! not stored: they hold the right eye first, and the file format has the left eye first.
//!
//! Times: a frame played back has the `host_ns` and driver time recorded in the CSV (the Python package gives
//! them with `Playback.recorded_time`), and `index` is its number in the file. A driver clock this crate does
//! not name (Python's `"v4l2"`, whose clock OpenCV does not report) leaves `driver_ns` and `driver_clock`
//! `None`; `Playback::recorded_driver` has the recorded values. `driver_sequence` and `driver_time_source` are
//! not recorded (the CSV has no such columns). The CSV names a V4L2 time this crate checked to be on
//! `CLOCK_MONOTONIC` "v4l2_monotonic", not Python's "v4l2" (a V4L2 time whose clock is not checked), so the two
//! stay apart when either plays the other's recording: the Python package plays it back with
//! `driver_clock == "v4l2_monotonic"` (`Playback.recorded_time`).

use std::collections::BTreeSet;
use std::fs::{File, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::mpsc::{sync_channel, Receiver, SyncSender, TrySendError};
use std::sync::{Arc, Mutex, MutexGuard};
use std::thread::JoinHandle;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use serde_json::{json, Map, Value};

use crate::backend::Backend;
use crate::capture::{Capture, DriverClock, SampleInfo};
use crate::config::{DeviceSelector, NegotiatedMode};
use crate::controls::Controls;
use crate::convert::UserValue;
use crate::device::{Device, FrameStats, StereoFrame};
use crate::error::{Result, SdkError};
use crate::stereo::{GrayImage, Layout, LrOrder};

/// JPEG quality of the frames `Recorder` writes (OpenCV's own MJPG writer uses 95 unless told otherwise).
const QUALITY: u8 = 95;
// ponytail: one RIFF with an idx1 index (AVI 1.0), kept under 2 GiB so readers that take sizes as signed 32-bit
// numbers open it. Longer recordings need the OpenDML extension (AVIX segments, as FFmpeg writes them).
const MAX_AVI_BYTES: u64 = i32::MAX as u64;
/// The header `Recorder` writes, up to the first frame; the `movi` list's type is at `MOVI`.
const HEADER_LEN: u64 = 224;
const MOVI: u64 = 220;

fn rec_err(m: impl Into<String>) -> SdkError {
    SdkError::Recording(m.into())
}

fn io_err(what: &str, path: &Path, e: std::io::Error) -> SdkError {
    rec_err(format!("{what} {}: {e}", path.display()))
}

/// The Python package's names for the two eyes' layout and order (its `LAYOUTS`, `LR_ORDERS`).
fn layout_name(l: Layout) -> &'static str {
    match l {
        Layout::Sbs => "sbs",
        Layout::Tb => "tb",
        Layout::PlanePack => "plane_pack",
    }
}

fn lr_order_name(o: LrOrder) -> &'static str {
    match o {
        LrOrder::FirstIsLeft => "first_is_left",
        LrOrder::FirstIsRight => "first_is_right",
    }
}

/// A driver clock's name in the CSV. "media_foundation" is the Python package's name for the same clock; Python
/// writes "v4l2" for a V4L2 time whose clock OpenCV does not report, so this crate's checked one is named apart.
fn clock_name(c: DriverClock) -> &'static str {
    match c {
        DriverClock::MediaFoundation => "media_foundation",
        DriverClock::V4l2Monotonic => "v4l2_monotonic",
    }
}

fn clock_from_name(s: &str) -> Option<DriverClock> {
    match s {
        "media_foundation" => Some(DriverClock::MediaFoundation),
        "v4l2_monotonic" => Some(DriverClock::V4l2Monotonic),
        _ => None,
    }
}

/// Seconds since 1970 (and milliseconds) as ISO 8601 in UTC. The Python package writes local time with its offset;
/// the standard library has no time zones.
fn iso_utc(secs: u64, millis: Option<u32>) -> String {
    // civil date from days since 1970 (H. Hinnant's days_from_civil, inverted)
    let z = (secs / 86_400) as i64 + 719_468;
    let (era, doe) = (z.div_euclid(146_097), z.rem_euclid(146_097));
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let (d, m) = (doy - (153 * mp + 2) / 5 + 1, if mp < 10 { mp + 3 } else { mp - 9 });
    let y = yoe + era * 400 + i64::from(m <= 2);
    let t = secs % 86_400;
    let ms = millis.map(|v| format!(".{v:03}")).unwrap_or_default();
    format!("{y:04}-{m:02}-{d:02}T{:02}:{:02}:{:02}{ms}+00:00", t / 3600, t % 3600 / 60, t % 60)
}

fn now(millis: bool) -> String {
    let d = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default();
    iso_utc(d.as_secs(), millis.then(|| d.subsec_millis()))
}

fn control_values(snap: Vec<(String, UserValue)>) -> Value {
    let map: Map<String, Value> = snap
        .into_iter()
        .map(|(k, v)| {
            let v = match v {
                UserValue::Int(i) => json!(i),
                UserValue::Bool(b) => json!(b),
                UserValue::Float(f) => json!(f),
                UserValue::Choice(c) => json!(c),
            };
            (k, v)
        })
        .collect();
    Value::Object(map)
}

/// "simulated" for the crate's stand-in controls (`MockBackend`), as the Python package says for its synthetic source.
fn controls_source(c: &Controls) -> &'static str {
    if c.backend_name() == "sim" {
        "simulated"
    } else {
        "camera"
    }
}

fn u32_at(b: &[u8], at: usize) -> u32 {
    u32::from_le_bytes([b[at], b[at + 1], b[at + 2], b[at + 3]])
}

fn read_at(f: &mut File, pos: u64, buf: &mut [u8]) -> std::io::Result<()> {
    f.seek(SeekFrom::Start(pos))?;
    f.read_exact(buf)
}

fn write_u32_at(f: &mut File, pos: u64, v: u32) -> std::io::Result<()> {
    f.seek(SeekFrom::Start(pos))?;
    f.write_all(&v.to_le_bytes())
}

/// The AVI header up to the first frame: one MJPG video stream of `w`x`h` at `fps`. The frame count, sizes and
/// the largest frame are 0 until `Open::finish` fills them in.
fn avi_header(w: u32, h: u32, fps: f64) -> Vec<u8> {
    let mut b = Vec::with_capacity(HEADER_LEN as usize);
    let mut put = |parts: &[&[u8]]| parts.iter().for_each(|p| b.extend_from_slice(p));
    let u = |v: u32| v.to_le_bytes();
    let (rate, scale) = ((fps * 1000.0).round() as u32, 1000u32);
    put(&[b"RIFF", &u(0), b"AVI ", b"LIST", &u(192), b"hdrl"]);
    // avih: µs per frame, max bytes/s, padding, flags (AVIF_HASINDEX), frames, initial frames, streams,
    // suggested buffer, width, height, 4 reserved
    put(&[b"avih", &u(56), &u((1e6 / fps).round() as u32), &u(0), &u(0), &u(0x10), &u(0), &u(0), &u(1)]);
    put(&[&u(0), &u(w), &u(h), &[0; 16]]);
    // strh: type, handler, flags, priority + language, initial frames, scale, rate, start, length,
    // suggested buffer, quality (-1: default), sample size, frame rectangle
    put(&[b"LIST", &u(116), b"strl", b"strh", &u(56), b"vids", b"MJPG", &u(0), &u(0), &u(0), &u(scale), &u(rate)]);
    put(&[&u(0), &u(0), &u(0), &u(u32::MAX), &u(0), &[0; 4], &(w as u16).to_le_bytes(), &(h as u16).to_le_bytes()]);
    // strf: BITMAPINFOHEADER
    put(&[b"strf", &u(40), &u(40), &u(w), &u(h), &1u16.to_le_bytes(), &24u16.to_le_bytes(), b"MJPG", &u(w * h * 3)]);
    put(&[&[0; 16], b"LIST", &u(0), b"movi"]);
    debug_assert_eq!(b.len() as u64, HEADER_LEN);
    b
}

/// The files of a recording once the first frame has come.
struct Open {
    avi: File,
    csv: File,
    /// Each frame's chunk: offset from the `movi` type, data size.
    index: Vec<(u32, u32)>,
    /// Bytes in the .avi.
    len: u64,
    biggest: u32,
}

impl Open {
    /// Appends one frame's JPEG as a `00dc` chunk. `max` is the largest .avi this may leave, its index included.
    fn chunk(&mut self, jpeg: &[u8], max: u64) -> std::io::Result<bool> {
        let size = jpeg.len() as u64;
        let padded = size + (size & 1);
        if self.len + 8 + padded + 8 + 16 * (self.index.len() as u64 + 1) > max {
            return Ok(false);
        }
        let mut b = Vec::with_capacity(8 + padded as usize);
        b.extend_from_slice(b"00dc");
        b.extend_from_slice(&(size as u32).to_le_bytes());
        b.extend_from_slice(jpeg);
        b.resize(8 + padded as usize, 0);
        self.avi.write_all(&b)?;
        self.index.push(((self.len - MOVI) as u32, size as u32));
        self.biggest = self.biggest.max(size as u32);
        self.len += 8 + padded;
        Ok(true)
    }

    /// Writes the index and the sizes and counts the header left at 0.
    fn finish(mut self) -> std::io::Result<()> {
        let n = self.index.len() as u32;
        let mut idx = Vec::with_capacity(8 + 16 * n as usize);
        idx.extend_from_slice(b"idx1");
        idx.extend_from_slice(&(16 * n).to_le_bytes());
        for (off, size) in &self.index {
            // AVIIF_KEYFRAME: every MJPG frame stands alone
            for v in [u32::from_le_bytes(*b"00dc"), 0x10, *off, *size] {
                idx.extend_from_slice(&v.to_le_bytes());
            }
        }
        self.avi.write_all(&idx)?;
        let movi_end = self.len;
        let total = movi_end + idx.len() as u64;
        for (at, v) in [(4, total - 8), (MOVI - 4, movi_end - MOVI), (48, n.into()), (140, n.into())] {
            write_u32_at(&mut self.avi, at, v as u32)?;
        }
        for at in [60, 144] {
            write_u32_at(&mut self.avi, at, self.biggest + 8)?;
        }
        Ok(())
    }
}

/// The two eyes side by side as JPEG's Y, with Cb = Cr = 128 in 4:2:0 — what FFmpeg's MJPG files (the Python
/// package's) hold; the encoder reads the rows from the frame, without a copy. Windows' own decoder (Media
/// Foundation) gave zeros for one-component gray JPEG frames.
struct SideBySide<'a>(&'a GrayImage, &'a GrayImage);

impl jpeg_encoder::ImageBuffer for SideBySide<'_> {
    fn get_jpeg_color_type(&self) -> jpeg_encoder::JpegColorType {
        jpeg_encoder::JpegColorType::Ycbcr
    }

    fn width(&self) -> u16 {
        (2 * self.0.width) as u16
    }

    fn height(&self) -> u16 {
        self.0.height as u16
    }

    fn fill_buffers(&self, y: u16, b: &mut [Vec<u8>; 4]) {
        let w = self.0.width as usize;
        let row = usize::from(y) * w..(usize::from(y) + 1) * w;
        b[0].extend_from_slice(&self.0.data[row.clone()]);
        b[0].extend_from_slice(&self.1.data[row]);
        for c in &mut b[1..3] {
            c.resize(c.len() + 2 * w, 128);
        }
    }
}

/// The frame's two eyes side by side, as one JPEG.
fn encode(frame: &StereoFrame, path: &Path) -> Result<Vec<u8>> {
    let mut jpeg = Vec::new();
    let mut enc = jpeg_encoder::Encoder::new(&mut jpeg, QUALITY);
    enc.set_sampling_factor(jpeg_encoder::SamplingFactor::F_2_2);
    enc.encode_image(SideBySide(&frame.left, &frame.right))
        .map_err(|e| rec_err(format!("encoding frame {} for {}: {e}", frame.index, path.display())))?;
    Ok(jpeg)
}

/// A recording's files and what goes into them; the writing thread appends under its lock.
struct Files {
    path: PathBuf,
    csv_path: PathBuf,
    info_path: PathBuf,
    open: Option<Open>,
    info: Value,
    fps: f64,
    stats: FrameStats,
    frames: u64,
    first_last: Option<(u64, u64)>,
    max_bytes: u64,
}

impl Files {
    /// Makes the three files for frames whose eyes are `w`x`h`.
    fn start(&mut self, w: u32, h: u32) -> Result<()> {
        let new = |p: &Path| OpenOptions::new().write(true).create_new(true).open(p).map_err(|e| io_err("creating", p, e));
        let mut avi = new(&self.path)?;
        avi.write_all(&avi_header(2 * w, h, self.fps)).map_err(|e| io_err("writing", &self.path, e))?;
        let mut csv = new(&self.csv_path)?;
        csv.write_all(b"frame,host_ns,driver_ns,driver_clock\r\n").map_err(|e| io_err("writing", &self.csv_path, e))?;
        self.open = Some(Open { avi, csv, index: Vec::new(), len: HEADER_LEN, biggest: 0 });
        self.info["recording"]["eye_size"] = json!([w, h]);
        self.write_info() // there is a JSON even if the process stops while recording
    }

    /// Appends one encoded frame and its CSV row. `brk`: the first frame after a pause (the JSON's "breaks").
    fn append(&mut self, frame: &StereoFrame, jpeg: &[u8], brk: bool) -> Result<()> {
        if self.open.is_none() {
            self.start(frame.left.width, frame.left.height)?;
        }
        let o = self.open.as_mut().expect("opened above");
        if !o.chunk(jpeg, self.max_bytes).map_err(|e| io_err("writing", &self.path, e))? {
            return Err(rec_err(format!(
                "{} has reached 2 GiB, the most this crate writes to one .avi (AVI 1.0): close this recorder (its \
                 {} frames play) and record on to a new file",
                self.path.display(),
                self.frames
            )));
        }
        let row = format!(
            "{},{},{},{}\r\n",
            self.frames,
            frame.host_ns,
            frame.driver_ns.map(|v| v.to_string()).unwrap_or_default(),
            frame.driver_ns.and(frame.driver_clock).map(clock_name).unwrap_or_default()
        );
        // one write per row: if the process stops, the CSV has the frames written so far
        o.csv.write_all(row.as_bytes()).map_err(|e| io_err("writing", &self.csv_path, e))?;
        if brk {
            if let Some(b) = self.info["recording"]["breaks"].as_array_mut() {
                b.push(json!(self.frames));
            }
            self.stats.restart();
        }
        self.stats.add(frame.host_ns);
        self.frames += 1;
        let first = self.first_last.map_or(frame.host_ns, |(f, _)| f);
        self.first_last = Some((first, frame.host_ns));
        Ok(())
    }

    fn write_info(&mut self) -> Result<()> {
        let r = &mut self.info["recording"];
        r["frames"] = json!(self.frames);
        let (first, last) = self.first_last.unzip();
        r["first_host_ns"] = json!(first);
        r["last_host_ns"] = json!(last);
        r["duration_s"] = json!(self.first_last.map_or(0.0, |(f, l)| l.saturating_sub(f) as f64 / 1e9));
        r["late_frames"] = json!(self.stats.late);
        r["estimated_missed_frames"] = json!(self.stats.missed);
        let text = serde_json::to_string_pretty(&self.info).expect("a JSON value") + "\n";
        std::fs::write(&self.info_path, text).map_err(|e| io_err("writing", &self.info_path, e))
    }
}

/// What the recorder and its writing thread share.
struct Shared {
    files: Mutex<Files>,
    /// The writing thread's first error; every later `write()` returns it.
    error: Mutex<Option<SdkError>>,
    /// Frames the writing thread took off the queue and did not write (the one that failed, and those after it).
    failed: AtomicU64,
    /// Frames the writing thread has taken off the queue (the tests wait for it).
    #[cfg(test)]
    taken: AtomicU64,
}

/// A lock whose holder panicked still gives the files, as far as they were written, so they can be closed.
fn lock<T>(m: &Mutex<T>) -> MutexGuard<'_, T> {
    m.lock().unwrap_or_else(|e| e.into_inner())
}

/// The writing thread: writes the queued frames in order until the recorder drops its end of the queue.
fn drain(rx: Receiver<(StereoFrame, bool)>, shared: Arc<Shared>, path: PathBuf) {
    for (frame, brk) in rx {
        #[cfg(test)]
        shared.taken.fetch_add(1, Ordering::SeqCst);
        if lock(&shared.error).is_none() {
            match encode(&frame, &path).and_then(|jpeg| lock(&shared.files).append(&frame, &jpeg, brk)) {
                Ok(()) => continue,
                Err(e) => *lock(&shared.error) = Some(e),
            }
        }
        shared.failed.fetch_add(1, Ordering::SeqCst);
    }
}

struct Queue {
    tx: SyncSender<(StereoFrame, bool)>,
    thread: JoinHandle<()>,
}

/// Writes frames to a recording: `<name>.avi` with `<name>.csv` and `<name>.json` next to it (see the module
/// documentation). The files are made when the first frame is written; a recorder closed before that leaves none.
///
/// ```no_run
/// # fn main() -> opticmix_vision::Result<()> {
/// use opticmix_vision::{Device, DeviceConfig, Recorder};
/// let mut cam = Device::open(DeviceConfig::weevia_s1())?;
/// let mut rec = Recorder::create_with_queue("recording.avi", &mut cam, 64)?; // encodes on its own thread
/// for _ in 0..450 {
///     let frame = cam.read()?;
///     rec.write(&frame)?;
/// }
/// rec.close()?;
/// println!("{} frames, {} dropped by the write queue", rec.frames(), rec.dropped_frames());
/// # Ok(())
/// # }
/// ```
///
/// `create`: `write` encodes the frame to JPEG and appends it before it returns. `create_with_queue(.., n)`:
/// `write` hands a copy of the frame to a writing thread through a queue of `n` frames and returns at once, so
/// encoding does not hold up the read loop. A frame that finds the queue full is not recorded: it is counted in
/// `dropped_frames` (and the JSON's), and the frame numbers stay those of the file. An error on the writing thread
/// is returned by the next `write` (every later one too; nothing more is recorded) or, if none comes, by `close`;
/// the frames queued after it are counted as dropped. `close` writes the frames still queued first. A queue does
/// not make an encoder that is slower than the camera keep up: the frames it cannot take are dropped and counted.
/// These are the rules and JSON fields (`write_queue`, `dropped_frames`) of the Python package's
/// `Recorder(queue_frames=)`.
///
/// `late_frames` / `estimated_missed_frames` are over the frames written, from their `host_ns` intervals; a pause
/// is not a gap, a dropped frame is (it leaves a gap in the recorded times): `estimated_missed_frames` includes
/// `dropped_frames` — do not add them. Not run with the camera.
pub struct Recorder {
    path: PathBuf,
    shared: Arc<Shared>,
    queue: Option<Queue>,
    controls: Option<Arc<Controls>>,
    /// One eye's size, from the first frame taken.
    eye: Option<(u32, u32)>,
    paused: bool,
    /// Resumed since the last frame taken: the next one taken starts after a break.
    resumed: bool,
    /// Frames taken: written, or queued to be.
    accepted: u64,
    /// Frames that found the queue full.
    full: u64,
    /// The writing thread's error has been returned (by `write` or `close`).
    told: bool,
    closed: bool,
}

impl Recorder {
    /// A recorder for frames from `device` (a camera or a recording) that writes each frame before `write`
    /// returns. Reads the mode, the camera's settings and every control value (`Controls::snapshot`, which opens
    /// the controls; reads only) for the JSON, and again the control values when it closes.
    ///
    /// Errors: `SdkError::InvalidValue` when `path` does not end in `.avi`; `SdkError::Recording` when the .avi,
    /// .csv or .json of that name exists, or another video (.mkv, .mp4, .mov) or a .npz of that name whose .csv
    /// and .json these would be — this crate does not replace a recording.
    pub fn create(path: impl AsRef<Path>, device: &mut Device) -> Result<Recorder> {
        let path = path.as_ref().to_path_buf();
        if !path.extension().is_some_and(|e| e.eq_ignore_ascii_case("avi")) {
            return Err(SdkError::InvalidValue(format!(
                "this crate records MJPG .avi only (FFV1 .mkv: the Python package): {}",
                path.display()
            )));
        }
        let (csv_path, info_path) = (path.with_extension("csv"), path.with_extension("json"));
        let others = ["mkv", "mp4", "mov", "npz"].map(|e| path.with_extension(e));
        if let Some(p) = [&path, &csv_path, &info_path].into_iter().chain(&others).find(|p| p.exists()) {
            let name = |p: &Path| p.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
            let why = if *p == path {
                format!("{} exists: record to another name (this crate does not replace a recording)", p.display())
            } else if *p == csv_path || *p == info_path {
                format!(
                    "{} exists: recording to {} would overwrite it — record to another name (this crate does not \
                     replace a recording's files)",
                    p.display(),
                    name(&path)
                )
            } else {
                format!(
                    "{} has the same name: recording to {} would overwrite {} and {}, the files next to it — record \
                     to another name",
                    p.display(),
                    name(&path),
                    name(&csv_path),
                    name(&info_path)
                )
            };
            return Err(rec_err(why));
        }
        let mode = device.mode();
        let fps = if mode.fps > 0.0 { mode.fps } else { 30.0 };
        let mut info = json!({
            "opticmix_vision": env!("CARGO_PKG_VERSION"),
            "writer": "Rust crate opticmix-vision",
            "created": now(false),
            "host_clock": "host_ns is the recording process's monotonic clock (Rust std::time::Instant) when the \
                           frame was read, not a camera clock",
            "mode": {"width": mode.width, "height": mode.height, "fps": mode.fps, "fourcc": mode.fourcc,
                     "backend_name": mode.backend_name},
        });
        if let Some(p) = device.playback() {
            info["source"] = json!("recording");
            info["recorded_from"] = p.info().cloned().unwrap_or(Value::Null);
        } else {
            let cfg = device.config();
            let selector = match &cfg.device {
                DeviceSelector::Name(s) | DeviceSelector::Path(s) => json!(s),
                DeviceSelector::Index(i) => json!(i),
                // the camera was picked by name and USB location; the name goes here like `Name`
                DeviceSelector::UsbLocation { name, .. } => json!(name),
            };
            info["source"] = json!(if mode.backend_name == "synthetic" { "synthetic" } else { "camera" });
            info["camera"] = json!({"device": selector, "device_name": device.device_name(),
                                    "backend": mode.backend_name, "layout": layout_name(cfg.layout),
                                    "lr_order": lr_order_name(cfg.lr_order)});
            info["calibration_files"] = Value::Null;
            info["calibration_note"] = json!("not listed by the Rust crate");
        }
        let controls = match device.controls().and_then(|c| c.snapshot().map(|s| (c, s))) {
            Ok((c, snap)) => {
                info["controls"] = control_values(snap);
                info["controls_source"] = json!(controls_source(&c));
                info["controls_read_at"] = json!(now(true));
                Some(c)
            }
            Err(e) => {
                for k in ["controls", "controls_source", "controls_read_at"] {
                    info[k] = Value::Null;
                }
                info["controls_note"] = json!(format!("{}: {e}", e.python_type()));
                None
            }
        };
        let csv_name = csv_path.file_name().map(|n| n.to_string_lossy().into_owned());
        info["recording"] = json!({
            "codec": "MJPG", "compressed": true, "layout": "sbs", "lr_order": "first_is_left", "fps_label": fps,
            "frames": 0, "csv": csv_name, "closed": false, "write_queue": 0, "dropped_frames": 0, "breaks": [],
            "control_changes": [],
        });
        let files = Files {
            path: path.clone(),
            csv_path,
            info_path,
            open: None,
            info,
            fps,
            stats: FrameStats::new(mode.fps),
            frames: 0,
            first_last: None,
            max_bytes: MAX_AVI_BYTES,
        };
        let shared = Shared {
            files: Mutex::new(files),
            error: Mutex::new(None),
            failed: AtomicU64::new(0),
            #[cfg(test)]
            taken: AtomicU64::new(0),
        };
        Ok(Recorder {
            path,
            shared: Arc::new(shared),
            queue: None,
            controls,
            eye: None,
            paused: false,
            resumed: false,
            accepted: 0,
            full: 0,
            told: false,
            closed: false,
        })
    }

    /// `create`, and the frames are encoded and written on a writing thread through a queue of `queue_frames`
    /// frames (see the type's documentation); 0 is `create`. The JSON's `write_queue` is `queue_frames`.
    /// Errors: those of `create`, and `SdkError::Recording` when the thread cannot be started.
    pub fn create_with_queue(path: impl AsRef<Path>, device: &mut Device, queue_frames: usize) -> Result<Recorder> {
        let mut r = Recorder::create(path, device)?;
        if queue_frames == 0 {
            return Ok(r);
        }
        lock(&r.shared.files).info["recording"]["write_queue"] = json!(queue_frames);
        let (tx, rx) = sync_channel(queue_frames);
        let (shared, path) = (r.shared.clone(), r.path.clone());
        let name = format!("Recorder {}", path.file_name().map(|n| n.to_string_lossy()).unwrap_or_default());
        let thread = std::thread::Builder::new()
            .name(name)
            .spawn(move || drain(rx, shared, path))
            .map_err(|e| rec_err(format!("starting the writing thread: {e}")))?;
        r.queue = Some(Queue { tx, thread });
        Ok(r)
    }

    /// The frame's eye size; `InvalidValue` for eyes of two sizes, a size a JPEG cannot hold, or another size than
    /// the first frame's.
    fn check(&self, frame: &StereoFrame) -> Result<(u32, u32)> {
        let (l, r) = (&frame.left, &frame.right);
        let (w, h) = (l.width, l.height);
        let px = (w as usize) * (h as usize);
        if (r.width, r.height) != (w, h) || l.data.len() != px || r.data.len() != px || w == 0 || h == 0 {
            return Err(SdkError::InvalidValue(format!(
                "frame {}: the eyes are {w}x{h} and {}x{} ({} and {} bytes)",
                frame.index, r.width, r.height, l.data.len(), r.data.len()
            )));
        }
        if 2 * w > u16::MAX as u32 || h > u16::MAX as u32 {
            return Err(SdkError::InvalidValue(format!("frame {}: {}x{h} is larger than a JPEG holds", frame.index, 2 * w)));
        }
        match self.eye {
            Some(e) if e != (w, h) => Err(SdkError::InvalidValue(format!(
                "frame {}: the eyes are {w}x{h}, this recording's {}x{}",
                frame.index, e.0, e.1
            ))),
            _ => Ok((w, h)),
        }
    }

    /// Appends one frame: the two eyes side by side, encoded to JPEG, and its times to the CSV — before returning,
    /// or with a queue (`create_with_queue`) on the writing thread. Does nothing while paused.
    ///
    /// Errors: `SdkError::InvalidValue` for eyes of two sizes or a size other than the first frame's (the frame is
    /// not taken; recording goes on), and after `close` ("recorder is closed"); `SdkError::Recording` when a file
    /// cannot be written, or when the .avi would pass 2 GiB (AVI 1.0 without the OpenDML extension) — then close
    /// this recorder (what it holds plays) and record on to a new one. With a queue those come from the writing
    /// thread: the next `write` returns the first, and every later one too.
    pub fn write(&mut self, frame: &StereoFrame) -> Result<()> {
        if self.closed {
            return Err(SdkError::InvalidValue(format!("recorder is closed: {}", self.path.display())));
        }
        if self.paused {
            return Ok(());
        }
        if let Some(e) = lock(&self.shared.error).clone() {
            self.told = true;
            return Err(e);
        }
        let size = self.check(frame)?;
        let brk = self.resumed && self.accepted > 0;
        match &self.queue {
            None => {
                let jpeg = encode(frame, &self.path)?;
                lock(&self.shared.files).append(frame, &jpeg, brk)?;
            }
            Some(q) => match q.tx.try_send((frame.clone(), brk)) {
                Ok(()) => {}
                Err(TrySendError::Full(_)) => {
                    self.full += 1; // a break still waiting goes with the next frame taken
                    return Ok(());
                }
                Err(TrySendError::Disconnected(_)) => {
                    return Err(rec_err(format!("the writing thread of {} has stopped", self.path.display())))
                }
            },
        }
        self.eye = Some(size);
        self.resumed = false;
        self.accepted += 1;
        Ok(())
    }

    /// Stops recording until `resume()`: the frames given to `write` in between are not recorded (nor counted as
    /// dropped). The JSON lists the file's frame number after each pause in "breaks", and the late / missed
    /// estimates — here and in playback — do not count a pause as missed frames (Python `Recorder.pause`).
    pub fn pause(&mut self) {
        self.paused = true;
    }

    pub fn resume(&mut self) {
        if self.paused {
            self.resumed = true;
        }
        self.paused = false;
    }

    /// Frames written (with a queue: not those still queued).
    pub fn frames(&self) -> u64 {
        lock(&self.shared.files).frames
    }

    /// Over the frames written, from their `host_ns` intervals at the source's negotiated rate — the rule of
    /// `Device::late_frames` (and of the Python package's `Recorder.late_frames`).
    pub fn late_frames(&self) -> u64 {
        lock(&self.shared.files).stats.late
    }

    /// Frames estimated missing between the frames written (the rule of `Device::estimated_missed_frames`); the
    /// dropped frames are among them.
    pub fn estimated_missed_frames(&self) -> u64 {
        lock(&self.shared.files).stats.missed
    }

    /// Frames `write` took (not paused) that are not in the file: the write queue was full, or writing had failed
    /// on the writing thread. Always 0 without a queue (`write` returns the error instead).
    pub fn dropped_frames(&self) -> u64 {
        self.full + self.shared.failed.load(Ordering::SeqCst)
    }

    /// Writes the frames still queued, then the .avi's index, and the final frame count, estimates, dropped frames
    /// and the control values at the end into the JSON. With no frame written there are no files. Then returns the
    /// writing thread's error if no `write` returned it. Safe to call more than once; `write` after it returns
    /// `SdkError::InvalidValue`. Dropping a recorder closes it too, without the error.
    pub fn close(&mut self) -> Result<()> {
        if std::mem::replace(&mut self.closed, true) {
            return Ok(());
        }
        if let Some(q) = self.queue.take() {
            drop(q.tx); // the thread writes what is still queued, then ends
            if q.thread.join().is_err() {
                let mut e = lock(&self.shared.error);
                e.get_or_insert_with(|| rec_err(format!("the writing thread of {} panicked", self.path.display())));
            }
        }
        self.close_files()?;
        let e = lock(&self.shared.error).clone();
        match e {
            Some(e) if !self.told => {
                self.told = true;
                Err(e)
            }
            _ => Ok(()),
        }
    }

    fn close_files(&mut self) -> Result<()> {
        let dropped = self.dropped_frames();
        let mut f = lock(&self.shared.files);
        let Some(o) = f.open.take() else { return Ok(()) };
        if f.frames == 0 {
            // the first frame failed after the files were made: an AVI without a frame does not play
            drop(o);
            for p in [&f.path, &f.csv_path, &f.info_path] {
                let _ = std::fs::remove_file(p);
            }
            return Ok(());
        }
        o.finish().map_err(|e| io_err("finishing", &f.path, e))?;
        let end = self.controls.as_ref().map(|c| {
            c.flush(Some(1.0)); // let writes still queued land before reading the end values
            c.snapshot().map(|s| (control_values(s), controls_source(c)))
        });
        let r = &mut f.info["recording"];
        match end {
            Some(Ok((values, source))) => {
                r["controls_at_end"] = values;
                r["controls_at_end_source"] = json!(source);
            }
            _ => {
                r["controls_at_end"] = Value::Null;
                r["controls_at_end_source"] = Value::Null;
            }
        }
        r["dropped_frames"] = json!(dropped);
        r["closed"] = json!(true);
        f.write_info()
    }
}

impl Drop for Recorder {
    fn drop(&mut self) {
        let _ = self.close();
    }
}

/// What an AVI file holds: the video stream's size, rate and codec, and where each whole frame is.
struct Avi {
    width: u32,
    height: u32,
    fps: f64,
    fourcc: String,
    /// (offset, size) of each frame's data.
    frames: Vec<(u64, u32)>,
}

/// The end of a chunk whose data starts at `start`: `start + size`, or `limit` when the size is 0 (a recording
/// that was not closed) or runs past `limit` (a cut-off file).
fn chunk_end(start: u64, size: u64, limit: u64) -> u64 {
    if size == 0 || start + size > limit {
        limit
    } else {
        start + size
    }
}

/// The chunks of an in-memory list: (id, data).
fn chunks(mut b: &[u8]) -> Vec<(&[u8], &[u8])> {
    let mut out = Vec::new();
    while b.len() >= 8 {
        let size = u32_at(b, 4) as usize;
        let data = &b[8..(8 + size).min(b.len())];
        out.push((&b[..4], data));
        b = &b[(8 + size + (size & 1)).min(b.len())..];
    }
    out
}

const NOT_AVI: &str = "not an AVI file (no RIFF AVI header) — this crate plays MJPG .avi recordings only; an FFV1 \
                       .mkv plays in the Python package";

fn scan_avi(f: &mut File) -> std::result::Result<Avi, String> {
    let len = f.metadata().map_err(|e| e.to_string())?.len();
    let mut head = [0u8; 12];
    if len < 12 || read_at(f, 0, &mut head).is_err() || &head[..4] != b"RIFF" || &head[8..] != b"AVI " {
        return Err(NOT_AVI.into());
    }
    let mut avi = Avi { width: 0, height: 0, fps: 0.0, fourcc: String::new(), frames: Vec::new() };
    let mut stream = *b"00";
    let mut riff = 0;
    // the first RIFF ("AVI "), then the OpenDML ones ("AVIX") of a large file the Python package wrote
    while riff + 12 <= len && read_at(f, riff, &mut head).is_ok() && &head[..4] == b"RIFF" {
        let end = chunk_end(riff + 8, u32_at(&head, 4).into(), len);
        let mut p = riff + 12;
        while p + 12 <= end {
            let mut ck = [0u8; 12];
            read_at(f, p, &mut ck).map_err(|e| e.to_string())?;
            let size = u64::from(u32_at(&ck, 4));
            if &ck[..4] == b"LIST" && &ck[8..] == b"hdrl" {
                let mut b = vec![0u8; (chunk_end(p + 12, size.saturating_sub(4), end) - p - 12).min(1 << 20) as usize];
                read_at(f, p + 12, &mut b).map_err(|e| e.to_string())?;
                stream = read_hdrl(&b, &mut avi);
            } else if &ck[..4] == b"LIST" && &ck[8..] == b"movi" {
                scan_movi(f, p + 12, chunk_end(p + 8, size, end), stream, &mut avi.frames)?;
                if size == 0 {
                    break; // a recording that was not closed: its frames run to the end of the file
                }
            }
            p += 8 + size + (size & 1);
        }
        riff = end + (end & 1);
    }
    Ok(avi)
}

/// Size, rate and codec of the first video stream; returns its number as the two digits of its chunk ids.
fn read_hdrl(b: &[u8], avi: &mut Avi) -> [u8; 2] {
    let (mut us, mut stream, mut n) = (0, None, 0u8);
    for (id, d) in chunks(b) {
        match id {
            b"avih" if d.len() >= 40 => (us, avi.width, avi.height) = (u32_at(d, 0), u32_at(d, 32), u32_at(d, 36)),
            b"LIST" if d.starts_with(b"strl") => {
                let mut vids = false;
                for (id, d) in chunks(&d[4..]) {
                    if id == b"strh" && d.len() >= 28 && &d[..4] == b"vids" && stream.is_none() {
                        vids = true;
                        let (scale, rate) = (u32_at(d, 20), u32_at(d, 24));
                        avi.fps = if scale > 0 && rate > 0 { f64::from(rate) / f64::from(scale) } else { 0.0 };
                        avi.fourcc = String::from_utf8_lossy(&d[4..8]).into_owned();
                    }
                    if id == b"strf" && vids && d.len() >= 20 && d[16..20] != [0; 4] {
                        avi.fourcc = String::from_utf8_lossy(&d[16..20]).into_owned();
                    }
                }
                if vids {
                    stream = Some(n);
                }
                n += 1;
            }
            _ => {}
        }
    }
    if avi.fps == 0.0 && us > 0 {
        avi.fps = 1e6 / f64::from(us);
    }
    let s = stream.unwrap_or(0);
    [b'0' + s / 10 % 10, b'0' + s % 10]
}

/// The whole frames of a `movi` list (`##dc` chunks of the video stream); stops at a frame the file cuts off.
fn scan_movi(f: &mut File, start: u64, end: u64, stream: [u8; 2], out: &mut Vec<(u64, u32)>) -> std::result::Result<(), String> {
    let mut p = start;
    while p + 8 <= end {
        let mut ck = [0u8; 8];
        read_at(f, p, &mut ck).map_err(|e| e.to_string())?;
        let size = u64::from(u32_at(&ck, 4));
        if ck[..2] == stream && &ck[2..4] == b"dc" {
            if p + 8 + size > end {
                break;
            }
            if size > 0 {
                out.push((p + 8, size as u32));
            }
        }
        p += 8 + size + (size & 1);
    }
    Ok(())
}

type DriverCell = (Option<i64>, Option<String>);

/// The CSV's host_ns and driver cells; `None` without a CSV.
fn read_csv(path: &Path) -> Result<Option<(Vec<u64>, Vec<DriverCell>)>> {
    let text = match std::fs::read_to_string(path) {
        Ok(t) => t,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(io_err("reading", path, e)),
    };
    let mut lines = text.lines();
    let head: Vec<&str> = lines.next().unwrap_or("").split(',').collect();
    let col = |name: &str| head.iter().position(|h| *h == name);
    let host = col("host_ns").ok_or_else(|| rec_err(format!("{}: no host_ns column", path.display())))?;
    let (dn, dc) = (col("driver_ns"), col("driver_clock"));
    let (mut ns, mut driver) = (Vec::new(), Vec::new());
    for (i, line) in lines.enumerate().filter(|(_, l)| !l.is_empty()) {
        let cells: Vec<&str> = line.split(',').collect();
        let cell = |c: Option<usize>| c.and_then(|c| cells.get(c)).copied().unwrap_or("");
        let bad = || {
            rec_err(format!(
                "{} line {}: not a frame time row (host_ns {:?}, driver_ns {:?})",
                path.display(),
                i + 2,
                cell(Some(host)),
                cell(dn)
            ))
        };
        ns.push(cell(Some(host)).parse::<u64>().map_err(|_| bad())?);
        // an earlier CSV has no driver columns, and an empty cell is a frame without a driver time: both None
        let d = match cell(dn) {
            "" => None,
            s => Some(s.parse::<i64>().map_err(|_| bad())?),
        };
        let c = d.and(Some(cell(dc))).filter(|s| !s.is_empty()).map(str::to_string);
        driver.push((d, c));
    }
    Ok(Some((ns, driver)))
}

/// A recording read back frame by frame — a `Capture`, so `Device::open_recording` reads it as it reads a
/// camera; `Device::playback` gives this for the calls below.
///
/// - `position`: the file frame number of the frame read last, or the one a `seek` moved to (read next).
/// - `set_loop(true)`: after the last frame, playback starts again at frame 0 instead of ending with
///   `SdkError::EndOfRecording`.
/// - `set_real_time(true)`: each read waits so frames come at the recorded intervals (the CSV's host_ns), divided
///   by `set_speed`. Frames are not dropped when the caller is slower.
/// - `set_paused(true)`: each read returns the frame read last again (at once, without waiting); `seek` still
///   moves, and the read after it returns the frame sought, then that one again.
/// - `status()`: playing, paused, or ended (after the last frame, without loop, until a `seek`).
///
/// These are the Python package's `Playback` controls (`pause`/`resume`, `status`), under Rust names.
/// Without the CSV, frame times are frame number / the file's frame rate. Without the JSON, `info()` is `None`
/// and the mode is the AVI header's. When the CSV and the video hold different numbers of frames (a recording
/// that was not closed, a cut-off file), the smaller number is played. Played a recording made with the camera once
/// (one PC, one camera, 2026-11-25: 881 frames, as Python read them); seeking, looping, pacing and pausing: generated
/// recordings only.
pub struct Playback {
    path: PathBuf,
    name: String,
    file: File,
    frames: Vec<(u64, u32)>,
    ns: Vec<u64>,
    driver: Vec<DriverCell>,
    info: Option<Value>,
    mode: NegotiatedMode,
    breaks: BTreeSet<usize>,
    next: usize,
    pos: usize,
    last: Option<usize>,
    starts_over: bool,
    looping: bool,
    real_time: bool,
    speed: f64,
    anchor: Option<(Instant, u64)>,
    paused: bool,
    /// The frame read last is still the current one (no seek since): a paused read returns it again.
    held: bool,
    ended: bool,
}

/// Where a `Playback` is (Python `Playback.status`: "playing", "paused", "ended").
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PlaybackStatus {
    Playing,
    Paused,
    /// `read` gave `SdkError::EndOfRecording` (no loop); a `seek` plays again.
    Ended,
}

impl PlaybackStatus {
    pub fn as_str(self) -> &'static str {
        match self {
            PlaybackStatus::Playing => "playing",
            PlaybackStatus::Paused => "paused",
            PlaybackStatus::Ended => "ended",
        }
    }
}

impl Playback {
    /// Opens `path` (an MJPG `.avi`) with the `.csv` and `.json` of the same name when they are there.
    /// Errors: `SdkError::Recording` — the file is missing or not an MJPG AVI (an FFV1 `.mkv` plays in the Python
    /// package only), it holds no whole frame, or its CSV or JSON cannot be read.
    pub fn open(path: impl AsRef<Path>) -> Result<Playback> {
        let path = path.as_ref().to_path_buf();
        let mut file = File::open(&path).map_err(|e| io_err("opening the recording", &path, e))?;
        let info_path = path.with_extension("json");
        let info: Option<Value> = match std::fs::read_to_string(&info_path) {
            Ok(t) => Some(serde_json::from_str(&t).map_err(|e| rec_err(format!("{}: {e}", info_path.display())))?),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => None,
            Err(e) => return Err(io_err("reading", &info_path, e)),
        };
        let cannot = |why: &str| rec_err(format!("{} cannot be played: {why}", path.display()));
        let avi = scan_avi(&mut file).map_err(|why| cannot(&why))?;
        if !avi.fourcc.eq_ignore_ascii_case("MJPG") {
            return Err(cannot(&format!(
                "its video is {:?}, not MJPG — this crate plays MJPG .avi recordings only; an FFV1 recording plays in \
                 the Python package",
                avi.fourcc
            )));
        }
        let fps = if avi.fps > 0.0 { avi.fps } else { 30.0 };
        let csv = read_csv(&path.with_extension("csv"))?;
        let n = avi.frames.len().min(csv.as_ref().map_or(usize::MAX, |(ns, _)| ns.len()));
        if n == 0 {
            return Err(cannot("it holds no whole frame"));
        }
        let (mut ns, mut driver) = csv.unwrap_or_else(|| ((0..n).map(|i| (i as f64 * 1e9 / fps).round() as u64).collect(), Vec::new()));
        ns.truncate(n);
        driver.truncate(n);
        let rec = info.as_ref().map(|i| &i["recording"]);
        let breaks = rec
            .and_then(|r| r["breaks"].as_array())
            .map(|b| b.iter().filter_map(|v| v.as_u64()).map(|v| v as usize).collect())
            .unwrap_or_default();
        let m = info.as_ref().map(|i| &i["mode"]).filter(|m| m["width"].is_u64());
        let mode = NegotiatedMode {
            width: m.and_then(|m| m["width"].as_u64()).unwrap_or(avi.width.into()) as u32,
            height: m.and_then(|m| m["height"].as_u64()).unwrap_or(avi.height.into()) as u32,
            fps: m.and_then(|m| m["fps"].as_f64()).unwrap_or(fps),
            fourcc: m.and_then(|m| m["fourcc"].as_str()).unwrap_or(&avi.fourcc).to_string(),
            backend_name: m.and_then(|m| m["backend_name"].as_str()).unwrap_or("recording").to_string(),
        };
        let mut frames = avi.frames;
        frames.truncate(n);
        Ok(Playback {
            name: path.display().to_string(),
            path,
            file,
            frames,
            ns,
            driver,
            info,
            mode,
            breaks,
            next: 0,
            pos: 0,
            last: None,
            starts_over: true,
            looping: false,
            real_time: false,
            speed: 1.0,
            anchor: None,
            paused: false,
            held: false,
            ended: false,
        })
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    /// The recording's JSON (the Python package's `Device.recording_info`); `None` without one.
    pub fn info(&self) -> Option<&Value> {
        self.info.as_ref()
    }

    /// The JSON's `recording.closed`: `Some(false)` for a recording that was not closed (the process stopped
    /// while recording); `None` without a JSON.
    pub fn closed(&self) -> Option<bool> {
        self.info.as_ref().and_then(|i| i["recording"]["closed"].as_bool())
    }

    /// Frames played (see the type's documentation for a CSV and video that disagree).
    pub fn frame_count(&self) -> usize {
        self.frames.len()
    }

    /// Recorded time from the first frame to the last, in seconds.
    pub fn duration_s(&self) -> f64 {
        self.ns[self.ns.len() - 1].saturating_sub(self.ns[0]) as f64 / 1e9
    }

    pub fn position(&self) -> usize {
        self.pos
    }

    /// The host_ns recorded for frame `index` (clamped to the recording).
    pub fn recorded_ns(&self, index: usize) -> u64 {
        self.ns[index.min(self.ns.len() - 1)]
    }

    /// (driver_ns, driver_clock) as the CSV has them for frame `index` (clamped), also for a clock this crate does
    /// not name; (None, None) for an empty cell, a CSV without driver columns, or no CSV.
    pub fn recorded_driver(&self, index: usize) -> (Option<i64>, Option<&str>) {
        match self.driver.get(index.min(self.ns.len() - 1)) {
            Some((d, c)) => (*d, c.as_deref()),
            None => (None, None),
        }
    }

    /// Moves to a frame number (clamped to the recording); the next read returns it. Returns the frame number.
    pub fn seek(&mut self, frame: usize) -> usize {
        let i = frame.min(self.frames.len() - 1);
        (self.next, self.pos, self.anchor, self.held, self.ended) = (i, i, None, false, false);
        i
    }

    /// `true`: each read returns the frame read last again, until `set_paused(false)` (see the type's
    /// documentation). Real-time pacing starts counting again on resuming.
    pub fn set_paused(&mut self, on: bool) {
        self.paused = on;
        if !on {
            self.anchor = None;
        }
    }

    pub fn status(&self) -> PlaybackStatus {
        if self.ended {
            PlaybackStatus::Ended
        } else if self.paused {
            PlaybackStatus::Paused
        } else {
            PlaybackStatus::Playing
        }
    }

    /// Moves to the first frame recorded at or after `seconds` from the first frame (clamped).
    pub fn seek_seconds(&mut self, seconds: f64) -> usize {
        let at = self.ns[0] as f64 + seconds * 1e9;
        let i = self.ns.partition_point(|&t| (t as f64) < at);
        self.seek(i)
    }

    pub fn set_loop(&mut self, on: bool) {
        self.looping = on;
    }

    pub fn set_real_time(&mut self, on: bool) {
        self.real_time = on;
        self.anchor = None;
    }

    /// Playback speed for real time: 2.0 is twice as fast as recorded, 0.5 half. `SdkError::InvalidValue` unless
    /// positive.
    pub fn set_speed(&mut self, speed: f64) -> Result<()> {
        if !(speed > 0.0 && speed.is_finite()) {
            return Err(SdkError::InvalidValue(format!("speed must be positive: {speed}")));
        }
        self.speed = speed;
        self.anchor = None;
        Ok(())
    }

    /// The frame read last does not follow the one before it: the first read, after a seek or loop, or a pause in
    /// recording (the JSON's "breaks"). `Device` then starts its interval statistics over.
    pub(crate) fn starts_over(&self) -> bool {
        self.starts_over
    }

    fn wait_for(&mut self, rec: u64) {
        let now = Instant::now();
        match self.anchor {
            // a time that goes back in the CSV starts the count again
            Some((t0, r0)) if rec >= r0 => {
                let due = t0 + Duration::from_secs_f64((rec - r0) as f64 / 1e9 / self.speed);
                if due > now {
                    std::thread::sleep(due - now);
                }
            }
            _ => self.anchor = Some((now, rec)),
        }
    }
}

impl Capture for Playback {
    /// The recorded mode (the JSON's), or the AVI header's without a JSON.
    fn mode(&self) -> NegotiatedMode {
        self.mode.clone()
    }

    /// The recording's path.
    fn device_name(&self) -> &str {
        &self.name
    }

    fn read_jpeg(&mut self) -> Result<Vec<u8>> {
        self.read_sample().map(|(b, _)| b)
    }

    /// The next frame's JPEG and its recorded driver time; `SdkError::EndOfRecording` after the last frame (unless
    /// looping).
    fn read_sample(&mut self) -> Result<(Vec<u8>, SampleInfo)> {
        let again = self.last.filter(|_| self.paused && self.held);
        let i = match again {
            Some(i) => i, // paused: the frame read last
            None => {
                if self.next >= self.frames.len() {
                    if !self.looping {
                        self.ended = true;
                        return Err(SdkError::EndOfRecording(format!("end of the recording: {}", self.path.display())));
                    }
                    self.seek(0);
                }
                self.next
            }
        };
        let (off, size) = self.frames[i];
        let mut b = vec![0u8; size as usize];
        read_at(&mut self.file, off, &mut b).map_err(|e| io_err(&format!("reading frame {i} of"), &self.path, e))?;
        self.starts_over = self.last.is_none_or(|l| l + 1 != i) || self.breaks.contains(&i);
        (self.last, self.pos, self.next, self.held) = (Some(i), i, i + 1, true);
        if self.real_time && again.is_none() {
            if self.breaks.contains(&i) {
                self.anchor = None; // a pause in recording is not waited out
            }
            self.wait_for(self.ns[i]);
        }
        let (d, c) = self.recorded_driver(i);
        let clock = c.and_then(clock_from_name);
        Ok((b, SampleInfo { driver_ns: clock.and(d), driver_clock: clock, ..SampleInfo::default() }))
    }

    fn controls_backend(&self) -> Result<Box<dyn Backend>> {
        Err(SdkError::Unavailable(format!(
            "a recording has no controls: {} (the values recorded with it are in Playback::info())",
            self.path.display()
        )))
    }

    fn playback(&mut self) -> Option<&mut Playback> {
        Some(self)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::capture::fake::FakeCapture;
    use crate::capture::DriverClock;
    use crate::config::DeviceConfig;
    use crate::stereo::GrayImage;

    const N: u64 = 6;

    fn tmp(tag: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("ov_rec_{tag}_{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    /// A 32x16 eye of one value; `marker` puts a 250 block in its top-left 8x8 (shows a mirrored or swapped eye).
    fn eye(value: u8, marker: bool) -> GrayImage {
        let data = (0..16 * 32)
            .map(|i| if marker && i / 32 < 8 && i % 32 < 8 { 250 } else { value })
            .collect();
        GrayImage::new(32, 16, data).unwrap()
    }

    /// The frames of the fixtures too (python/tests/data/recordings/make_python_mjpg.py makes the same): left
    /// 40 + 20 i with the marker, right 200 - 20 i, 90 fps host times, Media Foundation driver times except the last.
    fn frame(i: u64) -> StereoFrame {
        let driver = (i < 5).then_some(5_000_000_000 + i as i64 * 11_111_100);
        StereoFrame {
            left: eye(40 + 20 * i as u8, true),
            right: eye(200 - 20 * i as u8, false),
            host_ns: 1_000_000_000 + i * 11_111_111,
            index: i,
            driver_ns: driver,
            driver_clock: driver.map(|_| DriverClock::MediaFoundation),
            driver_sequence: None,
            driver_time_source: None,
        }
    }

    fn fake_device() -> Device {
        let cfg = DeviceConfig { width: 64, height: 16, stall_frames: 0, ..DeviceConfig::weevia_s1() };
        Device::open_with(cfg, Box::new(FakeCapture::new(vec![], 64, 16))).unwrap()
    }

    fn record(dir: &Path, name: &str, frames: impl IntoIterator<Item = StereoFrame>) -> PathBuf {
        let p = dir.join(name);
        let mut d = fake_device();
        let mut r = Recorder::create(&p, &mut d).unwrap();
        for f in frames {
            r.write(&f).unwrap();
        }
        r.close().unwrap();
        p
    }

    /// Every pixel of the 8x8 block at (x, y) within `tol` of `want` (MJPG changes values a little).
    fn block_near(g: &GrayImage, x: u32, y: u32, want: u8, tol: u8) -> bool {
        (y..y + 8).all(|r| (x..x + 8).all(|c| g.data[(r * g.width + c) as usize].abs_diff(want) <= tol))
    }

    /// The frame is `frame(i)`'s pixels (within MJPG's change), with the eyes on the right side.
    fn assert_pixels(f: &StereoFrame, i: u64) {
        assert_eq!(f.size(), (32, 16));
        assert!(block_near(&f.left, 0, 0, 250, 6), "frame {i}: the left eye's marker");
        assert!(block_near(&f.left, 24, 8, 40 + 20 * i as u8, 3), "frame {i}: left {:?}", &f.left.data[..8]);
        assert!(block_near(&f.right, 0, 0, 200 - 20 * i as u8, 3), "frame {i}: right {:?}", &f.right.data[..8]);
    }

    /// The frame's index, times and driver fields are `frame(i)`'s.
    fn assert_times(f: &StereoFrame, i: u64) {
        let want = frame(i);
        assert_eq!(
            (f.index, f.host_ns, f.driver_ns, f.driver_clock),
            (i, want.host_ns, want.driver_ns, want.driver_clock),
            "frame {i}"
        );
    }

    fn end_of_recording(r: Result<StereoFrame>) -> bool {
        matches!(r, Err(SdkError::EndOfRecording(_)))
    }

    #[test]
    fn a_recording_plays_back_its_pixels_and_recorded_times() {
        let p = record(&tmp("roundtrip"), "rec.avi", (0..N).map(frame));
        let mut cam = Device::open_recording(&p).unwrap();
        for i in 0..N {
            let f = cam.read().unwrap();
            assert_times(&f, i);
            assert_pixels(&f, i);
        }
        assert!(end_of_recording(cam.read()));
        let m = cam.mode();
        assert_eq!((m.width, m.height, m.fps, m.fourcc.as_str(), m.backend_name.as_str()), (64, 16, 90.0, "MJPG", "fake"));
        let pb = cam.playback().unwrap();
        assert_eq!((pb.frame_count(), pb.closed()), (N as usize, Some(true)));
        assert!((pb.duration_s() - 5.0 * 0.011_111_111).abs() < 1e-12, "{}", pb.duration_s());
    }

    #[test]
    fn the_csv_and_json_are_the_python_recorders() {
        let p = record(&tmp("format"), "rec.avi", (0..N).map(frame));
        let csv = std::fs::read_to_string(p.with_extension("csv")).unwrap();
        let rows: Vec<&str> = csv.split("\r\n").collect();
        assert_eq!(rows[0], "frame,host_ns,driver_ns,driver_clock");
        assert_eq!(rows[1], "0,1000000000,5000000000,media_foundation");
        assert_eq!(rows[6], "5,1055555555,,", "no driver time: empty cells");
        assert_eq!(rows.len(), 8, "a line per frame and the last line's end: {csv:?}");

        let info: Value = serde_json::from_str(&std::fs::read_to_string(p.with_extension("json")).unwrap()).unwrap();
        assert_eq!(info["opticmix_vision"], env!("CARGO_PKG_VERSION"));
        assert_eq!(info["mode"], serde_json::json!({"width": 64, "height": 16, "fps": 90.0, "fourcc": "MJPG", "backend_name": "fake"}));
        assert!(info["created"].as_str().unwrap().ends_with("+00:00"), "{}", info["created"]);
        assert_eq!(info["source"], "camera");
        assert_eq!(info["camera"]["lr_order"], "first_is_right", "the camera's own order");
        assert_eq!(info["controls_source"], "simulated");
        assert!(info["controls"]["gain"].is_number(), "{}", info["controls"]);
        let r = &info["recording"];
        assert_eq!((r["codec"].as_str(), r["compressed"].as_bool()), (Some("MJPG"), Some(true)));
        assert_eq!((r["layout"].as_str(), r["lr_order"].as_str()), (Some("sbs"), Some("first_is_left")));
        assert_eq!((r["frames"].as_u64(), r["closed"].as_bool(), r["csv"].as_str()), (Some(N), Some(true), Some("rec.csv")));
        assert_eq!(r["eye_size"], serde_json::json!([32, 16]));
        assert_eq!((r["first_host_ns"].as_u64(), r["last_host_ns"].as_u64()), (Some(1_000_000_000), Some(1_055_555_555)));
        assert_eq!((r["breaks"].clone(), r["control_changes"].clone()), (serde_json::json!([]), serde_json::json!([])));
        assert_eq!((r["late_frames"].as_u64(), r["estimated_missed_frames"].as_u64()), (Some(0), Some(0)));
        assert_eq!(r["controls_at_end_source"], "simulated");
    }

    /// Windows' own decoder (Media Foundation, also OpenCV's MSMF) decoded one-component (gray) JPEG frames to
    /// zeros; FFmpeg's MJPG files are three-component 4:2:0, and those it shows.
    #[test]
    fn frames_are_three_component_4_2_0_jpeg_as_ffmpeg_writes_them() {
        let p = record(&tmp("ycc"), "rec.avi", (0..1).map(frame));
        let b = std::fs::read(&p).unwrap();
        let sof = b.windows(2).position(|w| w == [0xFF, 0xC0]).expect("a baseline JPEG frame");
        assert_eq!(b[sof + 9], 3, "components");
        assert_eq!((b[sof + 11], b[sof + 14], b[sof + 17]), (0x22, 0x11, 0x11), "Y 2x2, Cb and Cr 1x1: 4:2:0");
    }

    #[test]
    fn late_and_missed_frames_use_the_recorded_times_and_a_jump_starts_over() {
        // 100 fps: 10 ms apart, then a 30 ms gap (1 late, 2 missed)
        let ms = [0u64, 10, 20, 50, 60];
        let frames = ms.iter().enumerate().map(|(i, t)| StereoFrame { host_ns: t * 1_000_000, ..frame(i as u64) });
        let dir = tmp("stats");
        let p = dir.join("rec.avi");
        let cfg = DeviceConfig { width: 64, height: 16, fps: 100.0, stall_frames: 0, ..DeviceConfig::weevia_s1() };
        let mut cap = FakeCapture::new(vec![], 64, 16);
        cap.mode.fps = 100.0;
        let mut d = Device::open_with(cfg, Box::new(cap)).unwrap();
        let mut r = Recorder::create(&p, &mut d).unwrap();
        frames.for_each(|f| r.write(&f).unwrap());
        assert_eq!((r.frames(), r.late_frames(), r.estimated_missed_frames()), (5, 1, 2));
        r.close().unwrap();

        let mut cam = Device::open_recording(&p).unwrap();
        (0..5).for_each(|_| drop(cam.read().unwrap()));
        assert_eq!((cam.late_frames(), cam.estimated_missed_frames()), (1, 2));
        cam.playback().unwrap().seek(0);
        assert_eq!(cam.read().unwrap().index, 0);
        assert_eq!((cam.late_frames(), cam.estimated_missed_frames()), (1, 2), "the jump back is not a gap");
    }

    #[test]
    fn seek_loop_and_the_end() {
        let p = record(&tmp("seek"), "rec.avi", (0..N).map(frame));
        let mut cam = Device::open_recording(&p).unwrap();
        let pb = cam.playback().unwrap();
        assert_eq!((pb.seek(3), pb.position()), (3, 3));
        assert_eq!(cam.read().unwrap().index, 3);
        let pb = cam.playback().unwrap();
        assert_eq!(pb.seek(99), N as usize - 1, "clamped to the last frame");
        assert_eq!(pb.seek_seconds(0.02), 2, "the first frame at or after 20 ms (frame 2 at 22.2 ms)");
        assert_eq!(pb.seek_seconds(-1.0), 0);
        pb.seek(N as usize - 1);
        pb.set_loop(true);
        let got: Vec<u64> = (0..3).map(|_| cam.read().unwrap().index).collect();
        assert_eq!(got, [5, 0, 1]);
        let pb = cam.playback().unwrap();
        pb.set_loop(false);
        pb.seek(N as usize - 1);
        cam.read().unwrap();
        assert!(end_of_recording(cam.read()));
        assert!(end_of_recording(cam.read()), "still at the end");
        cam.playback().unwrap().seek(0);
        let f = cam.read().unwrap();
        assert_times(&f, 0);
        assert_pixels(&f, 0);
    }

    #[test]
    fn real_time_waits_for_the_recorded_intervals_divided_by_speed() {
        let frames = (0..3).map(|i| StereoFrame { host_ns: i * 200_000_000, ..frame(i) });
        let p = record(&tmp("realtime"), "rec.avi", frames);
        let mut cam = Device::open_recording(&p).unwrap();
        let pb = cam.playback().unwrap();
        assert!(matches!(pb.set_speed(0.0), Err(SdkError::InvalidValue(_))));
        assert!(matches!(pb.set_speed(f64::NAN), Err(SdkError::InvalidValue(_))));
        pb.set_speed(4.0).unwrap();
        pb.set_real_time(true);
        let t0 = std::time::Instant::now();
        (0..3).for_each(|_| drop(cam.read().unwrap()));
        // 400 ms recorded at 4x: at least 100 ms (a sleep is never shorter)
        assert!(t0.elapsed() >= std::time::Duration::from_millis(100), "{:?}", t0.elapsed());
    }

    #[test]
    fn an_empty_file_is_an_error_not_a_panic() {
        let p = tmp("empty").join("rec.avi");
        std::fs::write(&p, b"").unwrap();
        let e = Device::open_recording(&p).err().unwrap();
        assert!(matches!(&e, SdkError::Recording(m) if m.contains("not an AVI")), "{e:?}");
    }

    #[test]
    fn a_file_that_is_not_an_mjpg_avi_says_what_rust_plays() {
        let dir = tmp("notavi");
        let mkv = dir.join("rec.mkv");
        std::fs::write(&mkv, [0x1A, 0x45, 0xDF, 0xA3, 0, 0, 0, 0, 0, 0, 0, 0]).unwrap(); // a Matroska start
        let e = Device::open_recording(&mkv).err().unwrap();
        assert!(matches!(&e, SdkError::Recording(m) if m.contains("MJPG .avi")), "{e:?}");
        // an AVI whose video is not MJPG
        let p = record(&dir, "other.avi", (0..2).map(frame));
        let mut b = std::fs::read(&p).unwrap();
        for at in [112, 188] {
            b[at..at + 4].copy_from_slice(b"FFV1");
        }
        std::fs::write(&p, b).unwrap();
        let e = Device::open_recording(&p).err().unwrap();
        assert!(matches!(&e, SdkError::Recording(m) if m.contains("FFV1") && m.contains("MJPG .avi")), "{e:?}");
        assert!(matches!(Device::open_recording(dir.join("none.avi")), Err(SdkError::Recording(_))), "no file");
    }

    #[test]
    fn a_cut_off_file_plays_its_whole_frames() {
        let p = record(&tmp("cut"), "rec.avi", (0..N).map(frame));
        let b = std::fs::read(&p).unwrap();
        let idx1 = b.windows(4).rposition(|w| w == b"idx1").unwrap();
        std::fs::write(&p, &b[..idx1 - 20]).unwrap(); // the index and the end of the last frame are gone
        let mut cam = Device::open_recording(&p).unwrap();
        assert_eq!(cam.playback().unwrap().frame_count(), N as usize - 1, "the CSV's rows beyond the video are not played");
        for i in 0..N - 1 {
            let f = cam.read().unwrap();
            assert_times(&f, i);
            assert_pixels(&f, i);
        }
        assert!(end_of_recording(cam.read()));
        std::fs::write(&p, &b[..230]).unwrap(); // the header and part of the first frame
        let e = Device::open_recording(&p).err().unwrap();
        assert!(matches!(&e, SdkError::Recording(m) if m.contains("no whole frame")), "{e:?}");
    }

    #[test]
    fn a_recording_that_was_not_closed_plays_what_was_written() {
        let dir = tmp("unclosed");
        let p = dir.join("rec.avi");
        let mut d = fake_device();
        let mut r = Recorder::create(&p, &mut d).unwrap();
        (0..3).for_each(|i| r.write(&frame(i)).unwrap());
        std::mem::forget(r); // the process stopped while recording: no close, no drop
        let mut cam = Device::open_recording(&p).unwrap();
        let pb = cam.playback().unwrap();
        assert_eq!((pb.frame_count(), pb.closed()), (3, Some(false)));
        for i in 0..3 {
            assert_times(&cam.read().unwrap(), i);
        }
        assert!(end_of_recording(cam.read()));
    }

    #[test]
    fn a_recorder_dropped_without_close_closes_the_files() {
        let dir = tmp("dropped");
        let p = dir.join("rec.avi");
        let mut d = fake_device();
        let mut r = Recorder::create(&p, &mut d).unwrap();
        (0..2).for_each(|i| r.write(&frame(i)).unwrap());
        drop(r);
        let mut cam = Device::open_recording(&p).unwrap();
        assert_eq!(cam.playback().unwrap().closed(), Some(true));
        assert_times(&cam.read().unwrap(), 0);
    }

    #[test]
    fn without_csv_and_json_the_avi_alone_plays() {
        let p = record(&tmp("bare"), "rec.avi", (0..3).map(frame));
        std::fs::remove_file(p.with_extension("csv")).unwrap();
        std::fs::remove_file(p.with_extension("json")).unwrap();
        let mut cam = Device::open_recording(&p).unwrap();
        let m = cam.mode();
        assert_eq!((m.width, m.height, m.fps, m.fourcc.as_str()), (64, 16, 90.0, "MJPG"), "the AVI header's");
        assert_eq!(cam.playback().unwrap().info(), None);
        let f: Vec<StereoFrame> = (0..3).map(|_| cam.read().unwrap()).collect();
        assert_eq!(f.iter().map(|f| f.host_ns).collect::<Vec<_>>(), [0, 11_111_111, 22_222_222], "frame number / fps");
        assert_eq!(f[0].driver_ns, None);
        assert_pixels(&f[2], 2);
    }

    #[test]
    fn a_csv_row_that_is_not_a_frame_time_names_the_file_and_line() {
        let p = record(&tmp("badcsv"), "rec.avi", (0..3).map(frame));
        let csv = std::fs::read_to_string(p.with_extension("csv")).unwrap();
        std::fs::write(p.with_extension("csv"), csv.replace("1011111111", "x")).unwrap();
        let e = Device::open_recording(&p).err().unwrap();
        assert!(matches!(&e, SdkError::Recording(m) if m.contains("rec.csv line 3") && m.contains("\"x\"")), "{e:?}");
    }

    #[test]
    fn a_csv_of_only_frame_and_host_ns_plays_without_driver_times() {
        let p = record(&tmp("oldcsv"), "rec.avi", (0..2).map(frame));
        std::fs::write(p.with_extension("csv"), "frame,host_ns\r\n0,7\r\n1,9\r\n").unwrap();
        let mut cam = Device::open_recording(&p).unwrap();
        let f = cam.read().unwrap();
        assert_eq!((f.host_ns, f.driver_ns, f.driver_clock), (7, None, None));
    }

    #[test]
    fn a_driver_clock_this_crate_does_not_know_stays_in_recorded_driver() {
        let p = record(&tmp("clock"), "rec.avi", (0..1).map(frame));
        std::fs::write(p.with_extension("csv"), "frame,host_ns,driver_ns,driver_clock\r\n0,7,123,v4l2\r\n").unwrap();
        let mut cam = Device::open_recording(&p).unwrap();
        let f = cam.read().unwrap();
        assert_eq!((f.driver_ns, f.driver_clock), (None, None), "Python's \"v4l2\" is not checked monotonic");
        assert_eq!(cam.playback().unwrap().recorded_driver(0), (Some(123), Some("v4l2")));
    }

    #[test]
    fn a_recording_has_no_controls() {
        let p = record(&tmp("noctl"), "rec.avi", (0..1).map(frame));
        let mut cam = Device::open_recording(&p).unwrap();
        assert!(matches!(cam.controls(), Err(SdkError::Unavailable(_))));
    }

    #[test]
    fn recording_a_recording_says_where_it_came_from() {
        let dir = tmp("rerecord");
        let p = record(&dir, "a.avi", (0..2).map(frame));
        let mut cam = Device::open_recording(&p).unwrap();
        let q = dir.join("b.avi");
        let mut r = Recorder::create(&q, &mut cam).unwrap();
        r.write(&cam.read().unwrap()).unwrap();
        r.close().unwrap();
        let info: Value = serde_json::from_str(&std::fs::read_to_string(q.with_extension("json")).unwrap()).unwrap();
        assert_eq!(info["source"], "recording");
        assert_eq!(info["recorded_from"]["recording"]["frames"], 2);
        assert_eq!(info["controls"], Value::Null);
    }

    #[test]
    fn the_recorder_does_not_overwrite_another_recording() {
        let dir = tmp("exists");
        let p = record(&dir, "rec.avi", (0..1).map(frame));
        let mut d = fake_device();
        let e = Recorder::create(&p, &mut d).err().unwrap();
        // the .avi itself: named once, without the sentence about the files next to it
        assert!(
            matches!(&e, SdkError::Recording(m) if m.matches("rec.avi").count() == 1 && !m.contains("overwrite")),
            "{e:?}"
        );
        std::fs::write(dir.join("solo.json"), b"{}").unwrap();
        let e = Recorder::create(dir.join("solo.avi"), &mut d).err().unwrap();
        assert!(
            matches!(&e, SdkError::Recording(m) if m.contains("solo.json exists") && m.contains("solo.avi would overwrite it")),
            "{e:?}"
        );
        std::fs::write(dir.join("other.mkv"), b"x").unwrap();
        let e = Recorder::create(dir.join("other.avi"), &mut d).err().unwrap();
        assert!(
            matches!(&e, SdkError::Recording(m) if m.contains("other.mkv has the same name")
                && m.contains("other.csv and other.json")),
            "{e:?}"
        );
        let e = Recorder::create(dir.join("x.mkv"), &mut d).err().unwrap();
        assert!(matches!(&e, SdkError::InvalidValue(m) if m.contains("MJPG .avi")), "{e:?}");
    }

    #[test]
    fn a_recorder_closed_before_any_frame_leaves_no_files() {
        let dir = tmp("noframe");
        let mut d = fake_device();
        Recorder::create(dir.join("rec.avi"), &mut d).unwrap().close().unwrap();
        assert_eq!(std::fs::read_dir(&dir).unwrap().count(), 0);
    }

    #[test]
    fn a_frame_of_another_size_is_refused() {
        let dir = tmp("size");
        let mut d = fake_device();
        let mut r = Recorder::create(dir.join("rec.avi"), &mut d).unwrap();
        r.write(&frame(0)).unwrap();
        let small = StereoFrame { left: GrayImage::new(8, 8, vec![0; 64]).unwrap(), right: GrayImage::new(8, 8, vec![0; 64]).unwrap(), ..frame(1) };
        assert!(matches!(r.write(&small), Err(SdkError::InvalidValue(_))));
        let odd = StereoFrame { right: GrayImage::new(8, 8, vec![0; 64]).unwrap(), ..frame(1) };
        assert!(matches!(r.write(&odd), Err(SdkError::InvalidValue(_))), "eyes of two sizes");
        r.close().unwrap();
        assert_eq!(Device::open_recording(dir.join("rec.avi")).unwrap().playback().unwrap().frame_count(), 1);
    }

    #[test]
    fn the_avi_size_limit_ends_writing_with_an_error_and_keeps_the_file_playable() {
        let dir = tmp("limit");
        let p = dir.join("rec.avi");
        let mut d = fake_device();
        let mut r = Recorder::create(&p, &mut d).unwrap();
        r.write(&frame(0)).unwrap();
        let mut f = lock(&r.shared.files);
        f.max_bytes = f.open.as_ref().unwrap().len + 100; // room for the index, not for another frame
        drop(f);
        let e = r.write(&frame(1)).unwrap_err();
        assert!(matches!(&e, SdkError::Recording(m) if m.contains("2 GiB")), "{e:?}");
        r.close().unwrap();
        let mut cam = Device::open_recording(&p).unwrap();
        assert_eq!(cam.playback().unwrap().frame_count(), 1);
        assert_times(&cam.read().unwrap(), 0);
    }

    // ---------------------------------------------------------------- writing on a thread (create_with_queue)

    fn until(what: &str, cond: impl Fn() -> bool) {
        let end = Instant::now() + Duration::from_secs(10);
        while !cond() {
            assert!(Instant::now() < end, "timed out waiting for {what}");
            std::thread::sleep(Duration::from_millis(2));
        }
    }

    fn csv_hosts(p: &Path) -> Vec<(u64, u64)> {
        let csv = std::fs::read_to_string(p.with_extension("csv")).unwrap();
        csv.lines()
            .skip(1)
            .map(|l| {
                let c: Vec<&str> = l.split(',').collect();
                (c[0].parse().unwrap(), c[1].parse().unwrap())
            })
            .collect()
    }

    fn json_recording(p: &Path) -> Value {
        let info: Value = serde_json::from_str(&std::fs::read_to_string(p.with_extension("json")).unwrap()).unwrap();
        info["recording"].clone()
    }

    /// A recorder with a queue of `n` frames whose writing thread is held: the test keeps the lock it appends
    /// under, so a frame it takes off the queue waits there (Python's test: a slow encoder behind a gate).
    fn held_queue(p: &Path, n: usize) -> (Recorder, Arc<Shared>) {
        let mut d = fake_device();
        let r = Recorder::create_with_queue(p, &mut d, n).unwrap();
        let shared = r.shared.clone();
        (r, shared)
    }

    #[test]
    fn a_write_queue_that_fills_up_drops_nothing_and_close_writes_what_is_queued() {
        let p = tmp("queue_fill").join("q.avi");
        let (mut r, shared) = held_queue(&p, 3);
        let gate = lock(&shared.files);
        r.write(&frame(0)).unwrap();
        until("the writing thread to take frame 0", || shared.taken.load(Ordering::SeqCst) == 1);
        for i in 1..4 {
            r.write(&frame(i)).unwrap(); // three more fill the queue: nothing dropped
        }
        assert_eq!(r.dropped_frames(), 0, "write() did not wait for the writing thread");
        drop(gate);
        r.close().unwrap(); // writes the frames still queued
        assert_eq!((r.frames(), r.dropped_frames()), (4, 0));
        assert_eq!(csv_hosts(&p), (0..4).map(|i| (i, frame(i).host_ns)).collect::<Vec<_>>());
        let j = json_recording(&p);
        assert_eq!((j["frames"].as_u64(), j["dropped_frames"].as_u64(), j["write_queue"].as_u64()), (Some(4), Some(0), Some(3)));
        assert_eq!((j["closed"].as_bool(), j["late_frames"].as_u64(), j["estimated_missed_frames"].as_u64()), (Some(true), Some(0), Some(0)));
        let mut cam = Device::open_recording(&p).unwrap();
        (0..4).for_each(|i| assert_pixels(&cam.read().unwrap(), i));
    }

    #[test]
    fn frames_that_find_the_queue_full_are_dropped_and_counted_and_frame_numbers_are_the_files() {
        let p = tmp("queue_drop").join("q.avi");
        let (mut r, shared) = held_queue(&p, 3);
        let gate = lock(&shared.files);
        r.write(&frame(0)).unwrap();
        until("the writing thread to take frame 0", || shared.taken.load(Ordering::SeqCst) == 1);
        for i in 1..6 {
            r.write(&frame(i)).unwrap(); // 1..3 fill the queue; 4 and 5 are dropped
        }
        assert_eq!(r.dropped_frames(), 2);
        drop(gate);
        until("four frames written", || r.frames() == 4);
        let late = StereoFrame { host_ns: frame(0).host_ns + 6 * 11_111_111, ..frame(1) };
        r.write(&late).unwrap();
        r.close().unwrap();
        let want: Vec<(u64, u64)> = [0, 1, 2, 3].map(|i| frame(i).host_ns).into_iter().chain([late.host_ns]).enumerate().map(|(n, t)| (n as u64, t)).collect();
        assert_eq!(csv_hosts(&p), want, "the file's frame numbers, the given frames' times");
        let j = json_recording(&p);
        assert_eq!((j["frames"].as_u64(), j["dropped_frames"].as_u64(), j["write_queue"].as_u64()), (Some(5), Some(2), Some(3)));
        // the dropped frames leave a gap in the recorded times, so the missed estimate counts them as well
        assert_eq!((j["late_frames"].as_u64(), j["estimated_missed_frames"].as_u64()), (Some(1), Some(2)));
        assert_eq!((r.late_frames(), r.estimated_missed_frames()), (1, 2));
    }

    #[test]
    fn a_break_still_goes_with_the_next_frame_recorded_when_the_first_one_after_it_is_dropped() {
        let p = tmp("queue_break").join("q.avi");
        let (mut r, shared) = held_queue(&p, 1);
        let gate = lock(&shared.files);
        r.write(&frame(0)).unwrap();
        until("the writing thread to take frame 0", || shared.taken.load(Ordering::SeqCst) == 1);
        r.write(&frame(1)).unwrap(); // the queue is full
        r.pause();
        r.write(&frame(2)).unwrap(); // paused: not recorded, not dropped
        r.resume();
        r.write(&frame(3)).unwrap(); // dropped
        assert_eq!(r.dropped_frames(), 1);
        drop(gate);
        until("two frames written", || r.frames() == 2);
        r.write(&frame(4)).unwrap();
        drop(r); // dropping closes too, after the frames still queued
        let j = json_recording(&p);
        assert_eq!((j["breaks"].clone(), j["dropped_frames"].as_u64(), j["frames"].as_u64()), (json!([2]), Some(1), Some(3)));
        let hosts: Vec<u64> = csv_hosts(&p).into_iter().map(|(_, t)| t).collect();
        assert_eq!(hosts, [0, 1, 4].map(|i| frame(i).host_ns));
    }

    #[test]
    fn an_error_on_the_writing_thread_is_returned_by_every_later_write_and_the_files_still_close() {
        let p = tmp("queue_error").join("q.avi");
        let (mut r, shared) = held_queue(&p, 4);
        r.write(&frame(0)).unwrap();
        until("frame 0 written", || r.frames() == 1);
        let mut f = lock(&shared.files);
        f.max_bytes = f.open.as_ref().unwrap().len + 100; // the next frame does not fit
        drop(f);
        r.write(&frame(1)).unwrap();
        until("frame 1 to fail", || r.dropped_frames() == 1);
        for i in 2..4 {
            let e = r.write(&frame(i)).unwrap_err(); // each later write returns it and records nothing
            assert!(matches!(&e, SdkError::Recording(m) if m.contains("2 GiB")), "{e:?}");
        }
        r.close().unwrap(); // returned already: close() only closes
        assert_eq!(r.dropped_frames(), 1);
        let j = json_recording(&p);
        assert_eq!((j["frames"].as_u64(), j["dropped_frames"].as_u64(), j["closed"].as_bool()), (Some(1), Some(1), Some(true)));
        assert_eq!(csv_hosts(&p), [(0, frame(0).host_ns)]);
    }

    #[test]
    fn close_returns_the_error_of_the_writing_thread_when_no_write_did() {
        let dir = tmp("queue_close_error");
        let (mut r, shared) = held_queue(&dir.join("q.avi"), 4);
        lock(&shared.files).max_bytes = HEADER_LEN; // not even the first frame fits
        r.write(&frame(0)).unwrap();
        let e = r.close().unwrap_err();
        assert!(matches!(&e, SdkError::Recording(m) if m.contains("2 GiB")), "{e:?}");
        r.close().unwrap(); // once only
        assert_eq!(r.dropped_frames(), 1);
        assert_eq!(std::fs::read_dir(&dir).unwrap().count(), 0, "no frame written: no files");
        let e = r.write(&frame(1)).unwrap_err();
        assert!(matches!(&e, SdkError::InvalidValue(m) if m.contains("recorder is closed")), "{e:?}");
    }

    #[test]
    fn without_a_queue_the_json_says_so_and_a_closed_recorder_takes_no_frame() {
        let dir = tmp("noqueue");
        let mut d = fake_device();
        let mut r = Recorder::create(dir.join("rec.avi"), &mut d).unwrap();
        r.write(&frame(0)).unwrap();
        assert_eq!(r.frames(), 1, "written before write() returned");
        r.close().unwrap();
        assert!(matches!(r.write(&frame(1)), Err(SdkError::InvalidValue(_))));
        assert_eq!(r.frames(), 1);
        let j = json_recording(&dir.join("rec.avi"));
        assert_eq!((j["write_queue"].as_u64(), j["dropped_frames"].as_u64()), (Some(0), Some(0)));
    }

    // ---------------------------------------------------------------- pausing

    #[test]
    fn a_pause_in_recording_leaves_frames_out_and_is_a_break_not_missed_frames() {
        let dir = tmp("rec_pause");
        let p = dir.join("rec.avi");
        let mut d = fake_device();
        let mut r = Recorder::create(&p, &mut d).unwrap();
        for i in 0..6 {
            if i == 2 {
                r.pause();
            }
            if i == 4 {
                r.resume();
            }
            r.write(&frame(i)).unwrap();
        }
        r.resume(); // not paused: no second break
        r.write(&frame(6)).unwrap();
        r.close().unwrap();
        assert_eq!((r.frames(), r.late_frames(), r.estimated_missed_frames(), r.dropped_frames()), (5, 0, 0, 0));
        let hosts: Vec<u64> = csv_hosts(&p).into_iter().map(|(_, t)| t).collect();
        assert_eq!(hosts, [0, 1, 4, 5, 6].map(|i| frame(i).host_ns));
        assert_eq!(json_recording(&p)["breaks"], json!([2]), "the file's frame number after the pause");
        let mut cam = Device::open_recording(&p).unwrap();
        (0..5).for_each(|_| drop(cam.read().unwrap()));
        assert_eq!((cam.late_frames(), cam.estimated_missed_frames()), (0, 0), "playback does not count the pause");
    }

    #[test]
    fn a_paused_playback_repeats_the_frame_seek_still_moves_and_status_says_where_it_is() {
        let p = record(&tmp("pb_pause"), "rec.avi", (0..N).map(frame));
        let mut cam = Device::open_recording(&p).unwrap();
        let status = |cam: &mut Device| cam.playback().unwrap().status();
        assert_eq!(status(&mut cam), PlaybackStatus::Playing);
        cam.read().unwrap();
        cam.read().unwrap();
        cam.playback().unwrap().set_paused(true); // holds the frame read last (1)
        assert_eq!((status(&mut cam), status(&mut cam).as_str()), (PlaybackStatus::Paused, "paused"));
        let idx = |cam: &mut Device, n: usize| (0..n).map(|_| cam.read().unwrap().index).collect::<Vec<_>>();
        assert_eq!(idx(&mut cam, 3), [1, 1, 1]);
        let pb = cam.playback().unwrap();
        pb.seek(pb.position() + 1); // one frame forward while paused
        assert_eq!(idx(&mut cam, 2), [2, 2]);
        let pb = cam.playback().unwrap();
        pb.seek(pb.position() - 2); // two back
        let f = cam.read().unwrap();
        assert_times(&f, 0);
        assert_pixels(&f, 0);
        cam.playback().unwrap().set_paused(false);
        assert_eq!(status(&mut cam), PlaybackStatus::Playing);
        assert_eq!(idx(&mut cam, 5), [1, 2, 3, 4, 5]);
        assert_eq!((cam.late_frames(), cam.estimated_missed_frames()), (0, 0), "a repeated frame is not a gap");
        assert!(end_of_recording(cam.read()));
        assert_eq!((status(&mut cam), status(&mut cam).as_str()), (PlaybackStatus::Ended, "ended"));
        cam.playback().unwrap().seek(0);
        assert_eq!(status(&mut cam).as_str(), "playing");
        assert_eq!(cam.read().unwrap().index, 0);
    }

    #[test]
    fn iso_utc_is_iso_8601_in_utc() {
        assert_eq!(iso_utc(0, None), "1970-01-01T00:00:00+00:00");
        assert_eq!(iso_utc(951_825_600, Some(7)), "2000-02-29T12:00:00.007+00:00");
        assert_eq!(iso_utc(1_795_995_045, None), "2026-11-29T23:30:45+00:00"); // Python datetime.fromtimestamp(.., UTC)
    }

    /// The fixture folder in the repository's Python package; None outside the repository (the packaged crate).
    fn fixtures() -> Option<PathBuf> {
        let d = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../python/tests/data/recordings");
        d.is_dir().then_some(d)
    }

    #[test]
    fn a_recording_the_python_package_wrote_plays_here() {
        let Some(d) = fixtures() else { return };
        // written by make_python_mjpg.py: Python Recorder, opencv-python's MJPG writer, the frames of frame(i)
        let mut cam = Device::open_recording(d.join("python_mjpg.avi")).unwrap();
        assert_eq!(cam.playback().unwrap().closed(), Some(true));
        let m = cam.mode();
        assert_eq!((m.width, m.height, m.fps, m.backend_name.as_str()), (64, 16, 90.0, "synthetic"), "the recorded mode");
        for i in 0..N {
            let f = cam.read().unwrap();
            assert_times(&f, i);
            assert_pixels(&f, i);
        }
        assert!(end_of_recording(cam.read()));
    }

    /// The Python tests read rust_mjpg.* (test_recording.py) — it must be what this Recorder writes. To write it
    /// again: `OV_WRITE_RECORDING_FIXTURE=1 cargo test -p opticmix-vision rust_fixture`.
    #[test]
    fn the_rust_fixture_the_python_tests_read_is_what_the_recorder_writes() {
        let Some(d) = fixtures() else { return };
        let p = record(&tmp("fixture"), "rust_mjpg.avi", (0..N).map(frame));
        if std::env::var_os("OV_WRITE_RECORDING_FIXTURE").is_some() {
            for ext in ["avi", "csv", "json"] {
                std::fs::copy(p.with_extension(ext), d.join("rust_mjpg").with_extension(ext)).unwrap();
            }
        }
        for ext in ["avi", "csv"] {
            let (now, kept) = (std::fs::read(p.with_extension(ext)).unwrap(), std::fs::read(d.join("rust_mjpg").with_extension(ext)));
            assert!(kept.as_ref().is_ok_and(|k| *k == now), "rust_mjpg.{ext} is not what the Recorder writes now: OV_WRITE_RECORDING_FIXTURE=1 cargo test -p opticmix-vision rust_fixture");
        }
    }
}
