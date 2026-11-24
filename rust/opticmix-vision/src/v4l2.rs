//! Linux capture — V4L2 mmap. Sets FOURCC (with size) first and FPS after — the order the Python package uses on V4L2.
//! This module has not yet been run on the camera. A read fails if no frame arrives for 2 seconds.

use std::path::{Path, PathBuf};
use std::time::Duration;

use v4l::buffer::Type;
use v4l::io::mmap::Stream;
use v4l::io::traits::CaptureStream;
use v4l::video::Capture as _;
use v4l::{Device as VDev, FourCC, Fraction};

use crate::backend::Backend;
use crate::capture::{Capture, SampleInfo};
use crate::config::{DeviceConfig, DeviceSelector, NegotiatedMode};
use crate::dshow::name_matches;
use crate::error::{Result, SdkError};

fn io(what: &str, e: std::io::Error) -> SdkError {
    SdkError::Device(format!("{what} failed: {e}"))
}

/// If no frame arrives for this long, `read()` fails — it does not block.
const READ_TIMEOUT: Duration = Duration::from_secs(2);

/// Read error. A timeout (`v4l` reports poll 0 as `TimedOut`) is a stalled stream — the message says to reopen.
fn read_err(e: std::io::Error) -> SdkError {
    if e.kind() == std::io::ErrorKind::TimedOut {
        SdkError::Device(format!(
            "no frame for {} s — the stream has stopped; reopen the device",
            READ_TIMEOUT.as_secs()
        ))
    } else {
        io("VIDIOC_DQBUF", e)
    }
}

/// `Stream` holds only the device handle (`Arc`) and the mmap buffer (`&mut [u8]`), so it is `Send` automatically — no `unsafe impl` needed.
/// Drops in field order: the stream (STREAMOFF, munmap) first, the device after.
pub struct V4l2Capture {
    stream: Stream<'static>,
    _dev: VDev,
    mode: NegotiatedMode,
    path: PathBuf,
    name: String,
}

impl V4l2Capture {
    pub fn open(cfg: &DeviceConfig) -> Result<V4l2Capture> {
        let (path, name) = pick(cfg)?;
        let dev = VDev::with_path(&path).map_err(|e| io("open", e))?;
        // 경로·번호로 열었으면 이름을 모른다 — 드라이버가 보고하는 카드 이름으로 채운다.
        let name = if name.is_empty() { dev.query_caps().map(|c| c.card).unwrap_or_default() } else { name };
        let mut fmt = dev.format().map_err(|e| io("VIDIOC_G_FMT", e))?;
        fmt.width = cfg.width;
        fmt.height = cfg.height;
        fmt.fourcc = FourCC::new(b"MJPG");
        let fmt = dev.set_format(&fmt).map_err(|e| io("VIDIOC_S_FMT", e))?; // FOURCC 먼저
        let fourcc = fmt.fourcc.str().unwrap_or("????").to_string();
        if fmt.fourcc != FourCC::new(b"MJPG") {
            // 드라이버는 S_FMT 에서 오류 대신 다른 형식을 돌려줄 수 있다 — 복호기는 MJPG 만 읽는다.
            return Err(SdkError::Device(format!(
                "the camera gave {fourcc} instead of MJPG at {}x{}",
                cfg.width, cfg.height
            )));
        }
        let mut p = dev.params().map_err(|e| io("VIDIOC_G_PARM", e))?;
        p.interval = Fraction::new(1, cfg.fps.round() as u32);
        let p = dev.set_params(&p).map_err(|e| io("VIDIOC_S_PARM", e))?; // 그다음 FPS
        let fps = p.interval.denominator as f64 / p.interval.numerator.max(1) as f64;
        let mut stream = Stream::with_buffers(&dev, Type::VideoCapture, 4).map_err(|e| io("VIDIOC_REQBUFS", e))?;
        stream.set_timeout(READ_TIMEOUT);
        let mode = NegotiatedMode {
            width: fmt.width,
            height: fmt.height,
            fps,
            fourcc,
            backend_name: "v4l2".into(),
        };
        Ok(V4l2Capture { stream, _dev: dev, mode, path, name })
    }
}

fn pick(cfg: &DeviceConfig) -> Result<(PathBuf, String)> {
    match &cfg.device {
        DeviceSelector::Path(p) => Ok((PathBuf::from(p), String::new())),
        DeviceSelector::Index(i) => Ok((PathBuf::from(format!("/dev/video{i}")), String::new())),
        DeviceSelector::Name(needle) => {
            let nodes = v4l::context::enum_devices()
                .iter()
                .map(|n| (n.path().to_path_buf(), n.name().unwrap_or_default()))
                .collect();
            let capture_node = |p: &Path| -> bool {
                VDev::with_path(p)
                    .and_then(|d| d.query_caps())
                    .map(|c| c.capabilities.contains(v4l::capability::Flags::VIDEO_CAPTURE))
                    .unwrap_or(false)
            };
            choose_named(needle, nodes, capture_node)
        }
    }
}

/// Several nodes share a name (video and metadata), so only nodes whose name matches and that can capture video count (in path order).
/// One gives that node, none gives `NoMatch`, more than one gives `MultipleMatches` — it does not open either of two identical cameras.
fn choose_named(
    needle: &str,
    mut nodes: Vec<(PathBuf, String)>,
    is_capture: impl Fn(&Path) -> bool,
) -> Result<(PathBuf, String)> {
    nodes.sort();
    let mut hits: Vec<_> = nodes
        .iter()
        .filter(|(p, n)| name_matches(n, &[needle]) && is_capture(p))
        .cloned()
        .collect();
    let list = |v: &[(PathBuf, String)], with_name: bool| {
        v.iter()
            .map(|(p, n)| if with_name { format!("{} {n}", p.display()) } else { p.display().to_string() })
            .collect::<Vec<_>>()
            .join(", ")
    };
    match hits.len() {
        0 => Err(SdkError::NoMatch(format!(
            "no video capture node named {needle:?} — have: {}",
            list(&nodes, true)
        ))),
        1 => Ok(hits.remove(0)),
        n => Err(SdkError::MultipleMatches(format!(
            "{n} video capture nodes match {needle:?} ({}) — unplug the others or pass DeviceSelector::Path",
            list(&hits, false)
        ))),
    }
}

impl Capture for V4l2Capture {
    fn mode(&self) -> NegotiatedMode {
        self.mode.clone()
    }

    fn device_name(&self) -> &str {
        &self.name
    }

    fn read_jpeg(&mut self) -> Result<Vec<u8>> {
        self.read_sample().map(|(b, _)| b)
    }

    /// The buffer's time stamp (kept only when flagged `CLOCK_MONOTONIC`) and sequence number go into `SampleInfo`.
    fn read_sample(&mut self) -> Result<(Vec<u8>, SampleInfo)> {
        // 시간 초과 뒤 v4l 의 Stream 은 다시 쓸 수 없다(같은 버퍼를 또 QBUF) — 오류 문구가 다시 열라고 한다.
        let (buf, meta) = CaptureStream::next(&mut self.stream).map_err(read_err)?;
        let n = (meta.bytesused as usize).min(buf.len());
        // time_t is i64 on 64-bit Linux and i32 on some 32-bit targets; both widen to i64 without loss.
        #[allow(clippy::unnecessary_cast)]
        let (sec, usec) = (meta.timestamp.sec as i64, meta.timestamp.usec as i64);
        let info = SampleInfo::from_v4l2(sec, usec, meta.flags.bits(), meta.sequence);
        Ok((buf[..n].to_vec(), info))
    }

    fn controls_backend(&self) -> Result<Box<dyn Backend>> {
        Ok(Box::new(crate::v4l2_ctrl::V4l2Controls::new(self.path.clone(), self.name.clone())))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn nodes(list: &[(&str, &str)]) -> Vec<(PathBuf, String)> {
        list.iter().map(|(p, n)| (PathBuf::from(p), n.to_string())).collect()
    }

    /// /dev/video0 and 2 = video, the rest = metadata (same name).
    fn video_0_and_2(p: &Path) -> bool {
        p == Path::new("/dev/video0") || p == Path::new("/dev/video2")
    }

    #[test]
    fn the_one_capture_node_with_the_name_is_chosen_over_its_metadata_node() {
        let have = nodes(&[("/dev/video1", "WN Dual Camera"), ("/dev/video0", "WN Dual Camera")]);
        let got = choose_named("WN Dual Camera", have, video_0_and_2).unwrap();
        assert_eq!(got, (PathBuf::from("/dev/video0"), "WN Dual Camera".to_string()));
    }

    #[test]
    fn two_capture_nodes_with_the_name_are_refused() {
        let have = nodes(&[
            ("/dev/video2", "WN Dual Camera"),
            ("/dev/video1", "WN Dual Camera"),
            ("/dev/video0", "WN Dual Camera"),
        ]);
        let e = choose_named("WN Dual Camera", have, video_0_and_2).unwrap_err();
        let SdkError::MultipleMatches(m) = &e else { panic!("{e:?}") };
        assert!(m.starts_with("2 video capture nodes match"), "{m}");
        assert!(m.contains("/dev/video0, /dev/video2"), "{m}");
        assert!(m.contains("unplug the others or pass DeviceSelector::Path"), "{m}");
    }

    #[test]
    fn a_read_timeout_says_the_stream_stopped() {
        let e = read_err(std::io::Error::new(std::io::ErrorKind::TimedOut, "VIDIOC_DQBUF"));
        assert_eq!(e, SdkError::Device("no frame for 2 s — the stream has stopped; reopen the device".into()));
        let other = read_err(std::io::Error::from_raw_os_error(19));
        assert!(matches!(&other, SdkError::Device(m) if m.starts_with("VIDIOC_DQBUF failed")), "{other:?}");
    }

    #[test]
    fn no_capture_node_with_the_name_lists_what_is_there() {
        let have = nodes(&[("/dev/video0", "USB webcam"), ("/dev/video1", "WN Dual Camera")]);
        let e = choose_named("WN Dual Camera", have, video_0_and_2).unwrap_err();
        let SdkError::NoMatch(m) = &e else { panic!("{e:?}") };
        assert!(m.contains("/dev/video0 USB webcam, /dev/video1 WN Dual Camera"), "{m}");
    }
}
