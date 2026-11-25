//! Windows capture — Media Foundation source reader, compressed MJPG samples as is. The format is chosen once (the native MJPG
//! format matching the requested width, height and fps).
//!
//! Initializes COM on the opening thread as multithreaded (MTA) and leaves it so — open from a non-UI thread.

use windows::core::GUID;
use windows::Win32::Media::MediaFoundation::*;
use windows::Win32::System::Com::{CoInitializeEx, COINIT_MULTITHREADED};

use crate::backend::Backend;
use crate::capture::{Capture, SampleInfo};
use crate::config::{DeviceConfig, DeviceSelector, NegotiatedMode};
use crate::devices::mf_enum::{dev_err, sources};
use crate::devices::{pick_by_name, pick_by_usb_location, repr, DeviceInfo};
use crate::dshow::{name_matches, DShowBackend};
use crate::error::{Result, SdkError};
use crate::spec::EXPECTED_NAMES;

const STREAM: u32 = MF_SOURCE_READER_FIRST_VIDEO_STREAM.0 as u32;

pub struct MfCapture {
    reader: IMFSourceReader,
    mode: NegotiatedMode,
    name: String,
    /// Set when opened by `DeviceSelector::UsbLocation`: the controls find the camera at the same location.
    usb_location: Option<String>,
}

// SAFETY: 소스 리더는 MTA(COINIT_MULTITHREADED)로 초기화한 스레드에서 만든다 — MTA 객체는 다른 MTA 스레드에서
// 불러도 된다. 한 번에 한 스레드만 쓴다(&mut self). 다른 스레드 읽기는 카메라 1대(Windows 11)에서 확인했다.
// 연 스레드가 이미 STA 였으면(RPC_E_CHANGED_MODE) 이 전제가 깨진다.
unsafe impl Send for MfCapture {}

impl MfCapture {
    /// Windows: initializes COM on the calling thread as multithreaded (MTA) and leaves it so — open from a
    /// non-UI thread.
    pub fn open(cfg: &DeviceConfig) -> Result<MfCapture> {
        unsafe {
            // 이미 다른 모드로 초기화된 스레드(RPC_E_CHANGED_MODE)여도 MF 는 쓸 수 있다 — 결과를 보지 않는다.
            // 짝이 되는 CoUninitialize 는 일부러 부르지 않는다: 소스 리더가 이 스레드의 MTA 에 살아 있어야 하고,
            // 언제 놓일지(다른 스레드로 옮겨 간 Device 의 drop) 여기서 알 수 없다. MFStartup 처럼 끝까지 둔다.
            let _ = CoInitializeEx(None, COINIT_MULTITHREADED);
            let (act, name, usb_location) = pick(cfg)?; // also runs MFStartup (once per process)
            let source: IMFMediaSource = act.ActivateObject().map_err(|e| dev_err("ActivateObject", e))?;
            let reader = match MFCreateSourceReaderFromMediaSource(&source, None) {
                Ok(r) => r,
                Err(e) => {
                    // 리더가 소스를 넘겨받지 못했다 — 장치를 쥔 채로 두지 않게 직접 닫는다.
                    let _ = source.Shutdown();
                    return Err(dev_err("MFCreateSourceReaderFromMediaSource", e));
                }
            };
            let mode = choose_type(&reader, cfg)?;
            Ok(MfCapture { reader, mode, name, usb_location })
        }
    }
}

/// Picks one from the video device list (`devices::enumerate_mf_video_devices` order) by `choose`.
unsafe fn pick(cfg: &DeviceConfig) -> Result<(IMFActivate, String, Option<String>)> {
    let named = sources()?;
    let list: Vec<DeviceInfo> =
        named.iter().enumerate().map(|(i, (_, n, l))| DeviceInfo::new(i, n.clone(), l.clone())).collect();
    let (i, usb_location) = choose(&cfg.device, &list)?;
    Ok((named[i].0.clone(), named[i].1.clone(), usb_location))
}

/// The entry of the Media Foundation list `sel` names, and the USB location the controls look for (only with
/// `UsbLocation`). Name match is a case-insensitive substring; more than one is an error.
fn choose(sel: &DeviceSelector, list: &[DeviceInfo]) -> Result<(usize, Option<String>)> {
    let have = || list.iter().map(|d| d.name.as_str()).collect::<Vec<_>>().join(", ");
    match sel {
        DeviceSelector::Index(i) if *i < list.len() => Ok((*i, None)),
        DeviceSelector::Index(i) => {
            Err(SdkError::NoMatch(format!("no video device at index {i} — have: {}", have())))
        }
        DeviceSelector::Name(needle) => {
            let hits: Vec<_> = list.iter().filter(|d| name_matches(&d.name, &[needle.as_str()])).collect();
            match hits.len() {
                1 => Ok((hits[0].index, None)),
                0 => Err(SdkError::NoMatch(format!("no video device named {needle:?} — have: {}", have()))),
                n => Err(SdkError::MultipleMatches(format!(
                    "{n} video devices match {needle:?} — unplug the others, pass DeviceSelector::Index, or pick one \
                     by USB location with DeviceSelector::UsbLocation (the port it is plugged into, not a serial \
                     number): [{}]",
                    hits.iter()
                        .map(|d| d.usb_location.as_deref().map_or("None".into(), repr))
                        .collect::<Vec<_>>()
                        .join(", ")
                ))),
            }
        }
        DeviceSelector::UsbLocation { name, usb_location } => {
            let d = pick_by_usb_location(name, usb_location, list)?;
            Ok((d.index, d.usb_location))
        }
        DeviceSelector::Path(p) => Err(SdkError::InvalidValue(format!(
            "DeviceSelector::Path({p:?}) is for Linux; use Name or Index on Windows"
        ))),
    }
}

/// The DirectShow device the controls use: at the USB location the camera was opened at, otherwise by name.
fn controls_device(name: &str, usb_location: Option<&str>, dshow: &[DeviceInfo]) -> Result<DeviceInfo> {
    match usb_location {
        Some(l) => pick_by_usb_location(name, l, dshow),
        None => pick_by_name(name, dshow),
    }
}

/// An MF video subtype is a FOURCC GUID (Data1 = four characters) — the characters if printable, otherwise Data1 in hex.
fn subtype_name(g: &GUID) -> String {
    let b = g.data1.to_le_bytes();
    if b.iter().all(u8::is_ascii_graphic) {
        String::from_utf8_lossy(&b).into_owned()
    } else {
        format!("{:08x}", g.data1)
    }
}

/// Picks the native format that is MJPG with the requested size and fps (±0.5). If none, the error lists every format there is.
unsafe fn choose_type(reader: &IMFSourceReader, cfg: &DeviceConfig) -> Result<NegotiatedMode> {
    let mut seen = Vec::new();
    for i in 0.. {
        let Ok(mt) = reader.GetNativeMediaType(STREAM, i) else { break };
        // 형식 하나의 속성을 못 읽으면 그 형식만 건너뛴다 — 맞는 형식은 뒤에 있을 수 있다.
        let (Ok(sub), Ok(size), Ok(rate)) =
            (mt.GetGUID(&MF_MT_SUBTYPE), mt.GetUINT64(&MF_MT_FRAME_SIZE), mt.GetUINT64(&MF_MT_FRAME_RATE))
        else {
            continue;
        };
        let (w, h) = ((size >> 32) as u32, size as u32);
        let (num, den) = ((rate >> 32) as u32, (rate as u32).max(1));
        let fps = num as f64 / den as f64;
        seen.push(format!("{} {w}x{h}@{fps:.1}", subtype_name(&sub)));
        if sub == MFVideoFormat_MJPG && w == cfg.width && h == cfg.height && (fps - cfg.fps).abs() <= 0.5 {
            reader
                .SetCurrentMediaType(STREAM, None, &mt)
                .map_err(|e| dev_err("SetCurrentMediaType", e))?;
            return Ok(NegotiatedMode { width: w, height: h, fps, fourcc: "MJPG".into(), backend_name: "msmf".into() });
        }
    }
    Err(SdkError::Device(format!(
        "the camera has no MJPG {}x{}@{} mode — it has: {}",
        cfg.width,
        cfg.height,
        cfg.fps,
        seen.join(", ")
    )))
}

impl Capture for MfCapture {
    fn mode(&self) -> NegotiatedMode {
        self.mode.clone()
    }

    fn device_name(&self) -> &str {
        &self.name
    }

    fn read_jpeg(&mut self) -> Result<Vec<u8>> {
        self.read_sample().map(|(b, _)| b)
    }

    /// The sample time ReadSample gives (100 ns units) goes into `SampleInfo::driver_ns` as ns.
    fn read_sample(&mut self) -> Result<(Vec<u8>, SampleInfo)> {
        // 동기 ReadSample 은 시간 제한이 없다 — 장치가 샘플을 보내지 않으면 여기서 막힌다.
        unsafe {
            loop {
                let mut flags = 0u32;
                let mut time_100ns = 0i64;
                let mut sample: Option<IMFSample> = None;
                self.reader
                    .ReadSample(STREAM, 0, None, Some(&mut flags), Some(&mut time_100ns), Some(&mut sample))
                    .map_err(|e| dev_err("ReadSample", e))?;
                if flags & (MF_SOURCE_READERF_ENDOFSTREAM.0 as u32 | MF_SOURCE_READERF_ERROR.0 as u32) != 0 {
                    return Err(SdkError::Device(format!(
                        "the camera stream ended (flags 0x{flags:X}) — unplugged?"
                    )));
                }
                let Some(sample) = sample else { continue }; // 스트림 틱 — 다음 샘플
                let buf = sample.ConvertToContiguousBuffer().map_err(|e| dev_err("ConvertToContiguousBuffer", e))?;
                let mut p: *mut u8 = std::ptr::null_mut();
                let mut len = 0u32;
                buf.Lock(&mut p, None, Some(&mut len)).map_err(|e| dev_err("Lock", e))?;
                // Lock 과 Unlock 사이에 ? 를 두지 않는다 — 복사 뒤 바로 Unlock.
                let out = if p.is_null() { Vec::new() } else { std::slice::from_raw_parts(p, len as usize).to_vec() };
                buf.Unlock().map_err(|e| dev_err("Unlock", e))?;
                return Ok((out, SampleInfo::from_media_foundation(time_100ns)));
            }
        }
    }

    /// Controls use the existing DirectShow backend — the MF list order can differ from DirectShow's, so the camera is found again by name
    /// (and by USB location when it was opened by one). Enumerates DirectShow directly (no ffmpeg child process). Two or more with the
    /// same name, opened by name, give `MultipleMatches`.
    fn controls_backend(&self) -> Result<Box<dyn Backend>> {
        let list = crate::devices::list_dshow_devices()?;
        let info = controls_device(&self.name, self.usb_location.as_deref(), &list)?;
        Ok(Box::new(DShowBackend::new(info.index, &EXPECTED_NAMES)?))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn subtypes_read_as_their_fourcc_or_a_short_guid() {
        assert_eq!(subtype_name(&MFVideoFormat_MJPG), "MJPG");
        assert_eq!(subtype_name(&MFVideoFormat_YUY2), "YUY2");
        assert_eq!(subtype_name(&MFVideoFormat_RGB24), "00000014"); // D3DFMT_R8G8B8 = 20, 글자가 아니다
    }

    // ---- made-up device lists only: nothing is enumerated or opened ----------------------------------------
    const LOC1: &str = "PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(1)";
    const LOC2: &str = "PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(4)#USB(2)";

    fn listed(v: &[(&str, Option<&str>)]) -> Vec<DeviceInfo> {
        v.iter().enumerate().map(|(i, (n, l))| DeviceInfo::new(i, *n, l.map(str::to_string))).collect()
    }

    fn at(usb_location: &str) -> DeviceSelector {
        DeviceSelector::UsbLocation { name: "WN Dual Camera".into(), usb_location: usb_location.into() }
    }

    #[test]
    fn opening_by_usb_location_picks_that_entry_and_keeps_its_location() {
        let mf = listed(&[("WN Dual Camera", Some(LOC2)), ("Example Webcam", None), ("WN Dual Camera", Some(LOC1))]);
        assert_eq!(choose(&at(LOC1), &mf), Ok((2, Some(LOC1.to_string()))));
        assert_eq!(choose(&at(&LOC2.to_lowercase()), &mf), Ok((0, Some(LOC2.to_string())))); // case does not matter
        assert!(matches!(choose(&at("PCIROOT(0)#USB(9)"), &mf), Err(SdkError::NoMatch(_))));
        assert!(matches!(choose(&at(" "), &mf), Err(SdkError::InvalidValue(_))));
        // by name or index no location is kept: the controls look the camera up by name, as before
        assert_eq!(choose(&DeviceSelector::Name("webcam".into()), &mf), Ok((1, None)));
        assert_eq!(choose(&DeviceSelector::Index(2), &mf), Ok((2, None)));
        assert!(matches!(choose(&DeviceSelector::Index(3), &mf), Err(SdkError::NoMatch(_))));
        let Err(SdkError::MultipleMatches(m)) = choose(&DeviceSelector::Name("wn dual".into()), &mf) else { panic!() };
        assert!(m.ends_with(&format!("number): ['{LOC2}', '{LOC1}']")), "{m}");
    }

    /// Media Foundation and DirectShow list the two cameras in a different order: the controls find, in DirectShow's
    /// list, the camera at the location capture opened.
    #[test]
    fn the_controls_find_the_camera_at_the_location_it_was_opened_at() {
        let mf = listed(&[("WN Dual Camera", Some(LOC2)), ("Example Webcam", None), ("WN Dual Camera", Some(LOC1))]);
        let dshow = listed(&[("WN Dual Camera", Some(LOC1)), ("WN Dual Camera", Some(LOC2))]);
        for (loc, mf_index, dshow_index) in [(LOC1, 2, 0), (LOC2, 0, 1)] {
            let (i, kept) = choose(&at(loc), &mf).unwrap();
            assert_eq!(i, mf_index);
            assert_eq!(controls_device("WN Dual Camera", kept.as_deref(), &dshow).unwrap().index, dshow_index);
        }
        // opened by name or index: two of one name in DirectShow's list are refused, not guessed
        assert!(matches!(controls_device("WN Dual Camera", None, &dshow), Err(SdkError::MultipleMatches(_))));
        // the location is not in DirectShow's list (read differently there): NoMatch, never the other camera
        let one = listed(&[("WN Dual Camera", Some(LOC1)), ("WN Dual Camera", None)]);
        assert!(matches!(controls_device("WN Dual Camera", Some(LOC2), &one), Err(SdkError::NoMatch(_))));
    }
}
