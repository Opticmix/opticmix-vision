//! Windows capture — Media Foundation source reader, compressed MJPG samples as is. The format is chosen once (the native MJPG
//! format matching the requested width, height and fps).
//!
//! Initializes COM on the opening thread as multithreaded (MTA) and leaves it so — open from a non-UI thread.

use std::sync::OnceLock;

use windows::core::{GUID, PWSTR};
use windows::Win32::Media::MediaFoundation::*;
use windows::Win32::System::Com::{CoInitializeEx, CoTaskMemFree, COINIT_MULTITHREADED};

use crate::backend::Backend;
use crate::capture::Capture;
use crate::config::{DeviceConfig, DeviceSelector, NegotiatedMode};
use crate::devices::{pick_by_name, DeviceInfo};
use crate::dshow::{name_matches, DShowBackend};
use crate::error::{Result, SdkError};
use crate::spec::EXPECTED_NAMES;

const STREAM: u32 = MF_SOURCE_READER_FIRST_VIDEO_STREAM.0 as u32;

fn dev_err(what: &str, e: windows::core::Error) -> SdkError {
    SdkError::Device(format!("{what} failed: HRESULT 0x{:08X}", e.code().0 as u32))
}

pub struct MfCapture {
    reader: IMFSourceReader,
    mode: NegotiatedMode,
    name: String,
}

// SAFETY: 소스 리더는 MTA(COINIT_MULTITHREADED)로 초기화한 스레드에서 만든다 — MTA 객체는 다른 MTA 스레드에서
// 불러도 된다. 한 번에 한 스레드만 쓴다(&mut self). 다른 스레드 읽기는 카메라 1대(Windows 11)에서 확인했다.
// 연 스레드가 이미 STA 였으면(RPC_E_CHANGED_MODE) 이 전제가 깨진다.
unsafe impl Send for MfCapture {}

/// The MFStartup result, once per process. A failure is remembered too, so every open gives the same error.
static MF_STARTED: OnceLock<Result<()>> = OnceLock::new();

// MFShutdown 은 부르지 않는다 — 프로세스 끝까지 MF 를 유지한다.
fn mf_startup() -> Result<()> {
    MF_STARTED
        .get_or_init(|| unsafe { MFStartup(MF_VERSION, MFSTARTUP_FULL) }.map_err(|e| dev_err("MFStartup", e)))
        .clone()
}

impl MfCapture {
    /// Windows: initializes COM on the calling thread as multithreaded (MTA) and leaves it so — open from a
    /// non-UI thread.
    pub fn open(cfg: &DeviceConfig) -> Result<MfCapture> {
        unsafe {
            // 이미 다른 모드로 초기화된 스레드(RPC_E_CHANGED_MODE)여도 MF 는 쓸 수 있다 — 결과를 보지 않는다.
            // 짝이 되는 CoUninitialize 는 일부러 부르지 않는다: 소스 리더가 이 스레드의 MTA 에 살아 있어야 하고,
            // 언제 놓일지(다른 스레드로 옮겨 간 Device 의 drop) 여기서 알 수 없다. MFStartup 처럼 끝까지 둔다.
            let _ = CoInitializeEx(None, COINIT_MULTITHREADED);
            mf_startup()?;
            let (act, name) = pick(cfg)?;
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
            Ok(MfCapture { reader, mode, name })
        }
    }
}

/// Picks one from the video device list. Name match is a case-insensitive substring; more than one is an error.
unsafe fn pick(cfg: &DeviceConfig) -> Result<(IMFActivate, String)> {
    let mut attrs: Option<IMFAttributes> = None;
    MFCreateAttributes(&mut attrs, 1).map_err(|e| dev_err("MFCreateAttributes", e))?;
    let attrs = attrs.ok_or_else(|| SdkError::Device("MFCreateAttributes gave nothing".into()))?;
    attrs
        .SetGUID(&MF_DEVSOURCE_ATTRIBUTE_SOURCE_TYPE, &MF_DEVSOURCE_ATTRIBUTE_SOURCE_TYPE_VIDCAP_GUID)
        .map_err(|e| dev_err("SetGUID", e))?;
    let mut pp: *mut Option<IMFActivate> = std::ptr::null_mut();
    let mut count = 0u32;
    MFEnumDeviceSources(&attrs, &mut pp, &mut count).map_err(|e| dev_err("MFEnumDeviceSources", e))?;
    // 배열 원소가 쥔 참조를 take 로 넘겨받는다(clone 이면 원소 쪽 참조가 해제되지 않는다). 그 뒤 배열만 해제.
    let list: Vec<IMFActivate> = if pp.is_null() || count == 0 {
        Vec::new()
    } else {
        std::slice::from_raw_parts_mut(pp, count as usize)
            .iter_mut()
            .filter_map(Option::take)
            .collect()
    };
    CoTaskMemFree(Some(pp as _));
    let named: Vec<(IMFActivate, String)> = list
        .into_iter()
        .map(|a| {
            let n = friendly_name(&a).unwrap_or_default();
            (a, n)
        })
        .collect();
    let have = || named.iter().map(|(_, n)| n.clone()).collect::<Vec<_>>().join(", ");
    match &cfg.device {
        DeviceSelector::Index(i) => named
            .get(*i)
            .cloned()
            .ok_or_else(|| SdkError::NoMatch(format!("no video device at index {i} — have: {}", have()))),
        DeviceSelector::Name(needle) => {
            let hits: Vec<_> = named.iter().filter(|(_, n)| name_matches(n, &[needle.as_str()])).cloned().collect();
            match hits.len() {
                1 => Ok(hits.into_iter().next().unwrap()),
                0 => Err(SdkError::NoMatch(format!("no video device named {needle:?} — have: {}", have()))),
                _ => Err(SdkError::MultipleMatches(format!(
                    "{} video devices match {needle:?} — unplug the others or pass DeviceSelector::Index",
                    hits.len()
                ))),
            }
        }
        DeviceSelector::Path(p) => Err(SdkError::InvalidValue(format!(
            "DeviceSelector::Path({p:?}) is for Linux; use Name or Index on Windows"
        ))),
    }
}

unsafe fn friendly_name(a: &IMFActivate) -> Result<String> {
    let mut s = PWSTR::null();
    let mut len = 0u32;
    a.GetAllocatedString(&MF_DEVSOURCE_ATTRIBUTE_FRIENDLY_NAME, &mut s, &mut len)
        .map_err(|e| dev_err("GetAllocatedString", e))?;
    let out = s.to_string().unwrap_or_default();
    CoTaskMemFree(Some(s.0 as _));
    Ok(out)
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
        // 동기 ReadSample 은 시간 제한이 없다 — 장치가 샘플을 보내지 않으면 여기서 막힌다.
        unsafe {
            loop {
                let mut flags = 0u32;
                let mut sample: Option<IMFSample> = None;
                self.reader
                    .ReadSample(STREAM, 0, None, Some(&mut flags), None, Some(&mut sample))
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
                return Ok(out);
            }
        }
    }

    /// Controls use the existing DirectShow backend — the MF list order can differ from DirectShow's, so the camera is found again by name.
    /// Enumerates DirectShow directly (no ffmpeg child process). Two or more with the same name give `MultipleMatches`.
    fn controls_backend(&self) -> Result<Box<dyn Backend>> {
        let list: Vec<DeviceInfo> = crate::dshow::enumerate_video_devices()?
            .into_iter()
            .enumerate()
            .map(|(index, name)| DeviceInfo { index, name: name.unwrap_or_default() })
            .collect();
        let info = pick_by_name(&self.name, &list)?;
        Ok(Box::new(DShowBackend::new(info.index, &EXPECTED_NAMES)?))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// MFStartup does not open a camera (it only initializes the MF platform). A second call returns the remembered result.
    #[test]
    fn mf_starts_once_per_process() {
        assert_eq!(mf_startup(), Ok(()));
        assert!(MF_STARTED.get().is_some());
        assert_eq!(mf_startup(), Ok(()));
    }

    #[test]
    fn subtypes_read_as_their_fourcc_or_a_short_guid() {
        assert_eq!(subtype_name(&MFVideoFormat_MJPG), "MJPG");
        assert_eq!(subtype_name(&MFVideoFormat_YUY2), "YUY2");
        assert_eq!(subtype_name(&MFVideoFormat_RGB24), "00000014"); // D3DFMT_R8G8B8 = 20, 글자가 아니다
    }
}
