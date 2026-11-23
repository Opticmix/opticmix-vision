//! Windows native backend — DirectShow IAMVideoProcAmp / IAMCameraControl (Python `_ctrl_dshow.py`).
//!
//! Called directly because OpenCV cannot do power-line frequency (ProcAmp 13), low-light compensation (CameraControl 19), ranges, defaults or reading auto exposure.
//! Every COM call happens on the Controls worker thread only (start/close included). The device is the index-th moniker counted in
//! the same order as OpenCV, and it is refused unless its name is Weevia S1 — nothing is written to another camera.
//!
//! **Only the standard Get/GetRange/Set.** There is no path that calls a vendor DLL, a UVC XU control or a KS property directly.

use crate::error::{Result, SdkError};
pub use crate::spec::EXPECTED_NAMES;

/// Same values as Python `_ctrl_dshow.py`. In `windows` 0.62.2 these two CLSIDs live in the MediaFoundation module, so they are defined here.
pub const CLSID_SYSTEM_DEVICE_ENUM: u128 = 0x62be5d10_60eb_11d0_bd3b_00a0c911ce86;
pub const CLSID_VIDEO_INPUT_DEVICE_CATEGORY: u128 = 0x860bb310_5d01_11d0_bd3b_00a0c911ce86;

/// Whether the device name contains one of the expected names (case-insensitive).
pub fn name_matches(name: &str, expected: &[&str]) -> bool {
    let low = name.to_lowercase();
    expected.iter().any(|e| low.contains(&e.to_lowercase()))
}

fn repr_name(name: &Option<String>) -> String {
    match name {
        None => "None".into(),
        Some(s) => format!("'{}'", s.replace('\\', "\\\\").replace('\'', "\\'")),
    }
}

/// Checks that the index-th device is a Weevia S1. Otherwise Unavailable — nothing is written to another camera.
pub fn choose(names: &[Option<String>], index: usize, expected: &[&str]) -> Result<()> {
    if index >= names.len() {
        return Err(SdkError::Unavailable(format!(
            "no DirectShow video device at index {index} (found {})",
            names.len()
        )));
    }
    let name = &names[index];
    if !name.as_deref().is_some_and(|n| name_matches(n, expected)) {
        return Err(SdkError::Unavailable(format!(
            "device {index} is {}, not a Weevia S1 ({})",
            repr_name(name),
            expected.join(", ")
        )));
    }
    Ok(())
}

/// DirectShow video input device names, in the same order as OpenCV (devices that fail to bind are counted too). Binds to no device (read-only).
/// Empty list on non-Windows.
pub fn enumerate_video_devices() -> Result<Vec<Option<String>>> {
    #[cfg(windows)]
    {
        let _com = com::ComInit::mta()?;
        // 꼬리식의 임시값(모니커 Vec)은 지역 변수 `_com` 보다 늦게 놓인다(2021 판) — 그대로 반환하면
        // CoUninitialize 뒤에 모니커 Release 가 가서 접근 위반이 난다(가상 카메라가 있는 PC 에서 재현, 2026-11-12).
        // 이름을 지역 변수로 받아 모니커를 이 줄 끝에서 먼저 놓는다.
        let names = com::monikers()?.iter().map(com::friendly_name).collect();
        Ok(names)
    }
    #[cfg(not(windows))]
    {
        Ok(Vec::new())
    }
}

/// (name, DevicePath) for each DirectShow video input device, same order as `enumerate_video_devices`.
/// Binds to no device (read-only). Empty list on non-Windows.
pub fn enumerate_video_device_paths() -> Result<Vec<(Option<String>, Option<String>)>> {
    #[cfg(windows)]
    {
        let _com = com::ComInit::mta()?;
        let out = com::monikers()?
            .iter()
            .map(|m| (com::friendly_name(m), com::device_path(m)))
            .collect();
        Ok(out)
    }
    #[cfg(not(windows))]
    {
        Ok(Vec::new())
    }
}

/// For tests and diagnostics: the 16 in-memory bytes of a GUID this module uses (same layout as a Python ctypes GUID).
#[cfg(windows)]
pub fn guid_bytes(name: &str) -> Option<[u8; 16]> {
    use windows::core::{Interface, GUID};
    use windows::Win32::Media::DirectShow::{
        IAMCameraControl, IAMVideoProcAmp, IBaseFilter, ICreateDevEnum,
    };
    use windows::Win32::System::Com::StructuredStorage::IPropertyBag;
    let g = match name {
        "CLSID_SystemDeviceEnum" => GUID::from_u128(CLSID_SYSTEM_DEVICE_ENUM),
        "CLSID_VideoInputDeviceCategory" => GUID::from_u128(CLSID_VIDEO_INPUT_DEVICE_CATEGORY),
        "IID_ICreateDevEnum" => ICreateDevEnum::IID,
        "IID_IPropertyBag" => IPropertyBag::IID,
        "IID_IBaseFilter" => IBaseFilter::IID,
        "IID_IAMVideoProcAmp" => IAMVideoProcAmp::IID,
        "IID_IAMCameraControl" => IAMCameraControl::IID,
        _ => return None,
    };
    let mut o = [0u8; 16];
    o[..4].copy_from_slice(&g.data1.to_le_bytes());
    o[4..6].copy_from_slice(&g.data2.to_le_bytes());
    o[6..8].copy_from_slice(&g.data3.to_le_bytes());
    o[8..].copy_from_slice(&g.data4);
    Some(o)
}

#[cfg(windows)]
pub(crate) mod com {
    use super::*;
    use windows::core::{w, GUID, HRESULT};
    use windows::Win32::Foundation::RPC_E_CHANGED_MODE;
    use windows::Win32::Media::DirectShow::ICreateDevEnum;
    use windows::Win32::System::Com::StructuredStorage::IPropertyBag;
    use windows::Win32::System::Com::{
        CoCreateInstance, CoInitializeEx, CoUninitialize, IEnumMoniker, IMoniker,
        CLSCTX_INPROC_SERVER, COINIT_MULTITHREADED,
    };
    use windows::Win32::System::Variant::{VariantClear, VARIANT, VT_BSTR};

    pub(crate) fn com_err(what: &str, e: windows::core::Error) -> SdkError {
        SdkError::Backend {
            what: what.to_string(),
            hresult: Some(e.code().0),
        }
    }

    /// The bound device. Field order = drop order: control interfaces → filter → CoUninitialize.
    pub(crate) struct Bound {
        pub(crate) procamp: Option<windows::Win32::Media::DirectShow::IAMVideoProcAmp>,
        pub(crate) camera: Option<windows::Win32::Media::DirectShow::IAMCameraControl>,
        pub(crate) _filter: windows::Win32::Media::DirectShow::IBaseFilter,
        pub(crate) _init: ComInit,
    }

    /// COM on this thread (MTA). If it is already in another mode (RPC_E_CHANGED_MODE), it is used as is and not released — same as Python.
    pub(crate) struct ComInit {
        owned: bool,
    }

    impl ComInit {
        pub(crate) fn mta() -> Result<ComInit> {
            let hr: HRESULT = unsafe { CoInitializeEx(None, COINIT_MULTITHREADED) };
            if hr.is_ok() {
                return Ok(ComInit { owned: true }); // S_OK / S_FALSE 면 짝을 맞춰 해제한다
            }
            if hr == RPC_E_CHANGED_MODE {
                return Ok(ComInit { owned: false });
            }
            Err(SdkError::Backend {
                what: "CoInitializeEx(MTA)".into(),
                hresult: Some(hr.0),
            })
        }
    }

    impl Drop for ComInit {
        fn drop(&mut self) {
            if self.owned {
                unsafe { CoUninitialize() };
            }
        }
    }

    /// All video input device monikers, in the same order as OpenCV. Empty if there is no video device.
    pub(crate) fn monikers() -> Result<Vec<IMoniker>> {
        let devenum: ICreateDevEnum = unsafe {
            CoCreateInstance(
                &GUID::from_u128(CLSID_SYSTEM_DEVICE_ENUM),
                None,
                CLSCTX_INPROC_SERVER,
            )
        }
        .map_err(|e| com_err("CoCreateInstance(SystemDeviceEnum)", e))?;
        let mut en: Option<IEnumMoniker> = None;
        unsafe {
            devenum.CreateClassEnumerator(
                &GUID::from_u128(CLSID_VIDEO_INPUT_DEVICE_CATEGORY),
                &mut en,
                0,
            )
        }
        .map_err(|e| com_err("CreateClassEnumerator(VideoInputDeviceCategory)", e))?;
        let Some(en) = en else { return Ok(Vec::new()) }; // S_FALSE: 영상 장치가 없다
        let mut out = Vec::new();
        loop {
            let mut one = [None];
            let mut fetched = 0u32;
            let hr = unsafe { en.Next(&mut one, Some(&mut fetched)) };
            if hr != HRESULT(0) || fetched == 0 {
                break;
            }
            match one[0].take() {
                Some(m) => out.push(m),
                None => break,
            }
        }
        Ok(out)
    }

    /// The moniker's FriendlyName (opens the property bag only — does not bind to the device).
    pub(crate) fn friendly_name(mon: &IMoniker) -> Option<String> {
        bag_string(mon, w!("FriendlyName"))
    }

    /// 모니커의 DevicePath(USB 포트를 찾는 데 쓴다). 가상 카메라는 없을 수 있다.
    pub(crate) fn device_path(mon: &IMoniker) -> Option<String> {
        bag_string(mon, w!("DevicePath"))
    }

    fn bag_string(mon: &IMoniker, key: windows::core::PCWSTR) -> Option<String> {
        let bag: IPropertyBag = unsafe { mon.BindToStorage(None, None) }.ok()?;
        let mut var = VARIANT::default();
        unsafe { bag.Read(key, &mut var, None) }.ok()?;
        let name = unsafe {
            let inner = &var.Anonymous.Anonymous;
            if inner.vt == VT_BSTR {
                let b = &inner.Anonymous.bstrVal;
                (!b.is_empty()).then(|| b.to_string()) // BSTR 은 빈 포인터와 "" 를 구분하지 않는다 — 둘 다 None(Python 은 "" 를 돌려줌, 열거 결과에서는 같은 뜻)
            } else {
                None
            }
        };
        let _ = unsafe { VariantClear(&mut var) };
        name
    }
}

// ---- DShowBackend ----------------------------------------------------------------------------

use crate::backend::{Backend, RawRange};
pub use crate::spec::Iface;
use crate::spec::{is_auto_capable, ks_id, FLAG_AUTO, FLAG_MANUAL};

/// Set flags (Python `DShowBackend.write`): for auto-capable keys auto → 1, otherwise (manual or None) → 2. Other keys get 0 (what OpenCV used).
pub fn set_flags(key: &str, auto: Option<bool>) -> i32 {
    if !is_auto_capable(key) {
        return 0;
    }
    if auto == Some(true) {
        FLAG_AUTO
    } else {
        FLAG_MANUAL
    }
}

/// Diagnostic (read-only) Get result — for looking at ids outside the name table.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RawGet {
    pub hr: i32,
    pub value: i32,
    pub flags: i32,
}

/// Diagnostic (read-only) GetRange result.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RawRangeOut {
    pub hr: i32,
    pub min: i32,
    pub max: i32,
    pub step: i32,
    pub default: i32,
    pub caps: i32,
}

/// DirectShow backend. Creating it opens nothing — `start()` (on the worker thread) enumerates, checks the name and binds.
pub struct DShowBackend {
    index: usize,
    #[cfg_attr(not(windows), allow(dead_code))] // start() 의 Windows 블록에서만 읽는다
    expected: Vec<String>,
    device_name: String,
    #[cfg(windows)]
    bound: Option<com::Bound>,
}

// SAFETY: COM 객체(`bound`)는 start() 에서 만들어 close()/drop 에서 놓는다. Controls 는 백엔드를 워커 스레드로 옮긴 뒤
// start·호출·close·drop 을 전부 그 스레드에서 하므로, COM 객체가 스레드를 건너지 않는다. 시작 전(bound=None)에는
// 옮겨도 COM 상태가 없다.
unsafe impl Send for DShowBackend {}

impl DShowBackend {
    /// Unavailable on non-Windows.
    pub fn new(index: usize, expected: &[&str]) -> Result<DShowBackend> {
        if cfg!(not(windows)) {
            return Err(SdkError::Unavailable(
                "DirectShow controls are Windows-only".into(),
            ));
        }
        Ok(DShowBackend {
            index,
            expected: expected.iter().map(|s| s.to_string()).collect(),
            device_name: String::new(),
            #[cfg(windows)]
            bound: None,
        })
    }

    pub fn index(&self) -> usize {
        self.index
    }

    fn not_available(iface: Iface) -> SdkError {
        SdkError::Backend {
            what: format!(
                "the {} interface is not available on this device",
                iface.as_str()
            ),
            hresult: None,
        }
    }

    fn id(key: &str) -> Result<(Iface, i32)> {
        ks_id(key).ok_or_else(|| SdkError::Backend {
            what: format!("{key} is not a DirectShow control"),
            hresult: None,
        })
    }

    /// Diagnostic: Get on any id (read-only). None if the interface is missing or before start.
    pub fn raw_get(&self, iface: Iface, pid: i32) -> Option<RawGet> {
        #[cfg(windows)]
        {
            self.bound.as_ref()?.get(iface, pid)
        }
        #[cfg(not(windows))]
        {
            let _ = (iface, pid);
            None
        }
    }

    /// Diagnostic: GetRange on any id (read-only). None if the interface is missing or before start.
    pub fn raw_range(&self, iface: Iface, pid: i32) -> Option<RawRangeOut> {
        #[cfg(windows)]
        {
            self.bound.as_ref()?.range(iface, pid)
        }
        #[cfg(not(windows))]
        {
            let _ = (iface, pid);
            None
        }
    }
}

impl Backend for DShowBackend {
    fn name(&self) -> &str {
        "dshow"
    }

    fn exposure_units(&self) -> &str {
        "log2_s_nominal"
    }

    fn note(&self) -> &str {
        ""
    }

    fn device_name(&self) -> &str {
        &self.device_name
    }

    fn start(&mut self) -> Result<()> {
        #[cfg(windows)]
        {
            let expected: Vec<&str> = self.expected.iter().map(String::as_str).collect();
            let (bound, name) = com::Bound::open(self.index, &expected)?;
            self.bound = Some(bound);
            self.device_name = name;
            Ok(())
        }
        #[cfg(not(windows))]
        {
            Err(SdkError::Unavailable(
                "DirectShow controls are Windows-only".into(),
            ))
        }
    }

    fn supports(&mut self, key: &str) -> bool {
        ks_id(key).is_some() && self.read(key).is_ok()
    }

    fn query(&mut self, key: &str) -> Result<Option<RawRange>> {
        let (iface, pid) = Self::id(key)?;
        let r = self
            .raw_range(iface, pid)
            .ok_or_else(|| Self::not_available(iface))?;
        if r.hr < 0 {
            return Ok(None);
        }
        Ok(Some(RawRange {
            minimum: r.min as i64,
            maximum: r.max as i64,
            step: r.step as i64,
            default: Some(r.default as i64),
            can_auto: is_auto_capable(key) && r.caps & FLAG_AUTO != 0,
        }))
    }

    fn read(&mut self, key: &str) -> Result<(i64, Option<bool>)> {
        let (iface, pid) = Self::id(key)?;
        let g = self
            .raw_get(iface, pid)
            .ok_or_else(|| Self::not_available(iface))?;
        if g.hr < 0 {
            return Err(SdkError::Backend {
                what: format!("Get({key})"),
                hresult: Some(g.hr),
            });
        }
        Ok((
            g.value as i64,
            is_auto_capable(key).then_some(g.flags & FLAG_AUTO != 0),
        ))
    }

    fn write(&mut self, key: &str, value: Option<i64>, auto: Option<bool>) -> Result<()> {
        let (iface, pid) = Self::id(key)?;
        let value = match value {
            Some(v) => v,
            None => self.read(key)?.0,
        };
        let flags = set_flags(key, auto);
        let what = format!("Set({key}={value}, flags={flags})");
        let v = i32::try_from(value)
            .map_err(|_| SdkError::InvalidValue(format!("{what}: value out of range")))?;
        #[cfg(windows)]
        {
            let b = self
                .bound
                .as_ref()
                .filter(|b| b.has(iface))
                .ok_or_else(|| Self::not_available(iface))?;
            b.set(iface, pid, v, flags).map_err(|hr| SdkError::Backend {
                what,
                hresult: Some(hr),
            })
        }
        #[cfg(not(windows))]
        {
            let _ = (pid, v, what);
            Err(Self::not_available(iface))
        }
    }

    fn close(&mut self) -> Result<()> {
        #[cfg(windows)]
        {
            self.bound = None; // 인터페이스 → 필터 → CoUninitialize 순으로 놓는다(Bound 의 필드 순서)
        }
        Ok(())
    }
}

#[cfg(windows)]
impl com::Bound {
    /// If the index-th moniker has an expected name, binds it as IBaseFilter and gets the two control interfaces.
    fn open(index: usize, expected: &[&str]) -> Result<(com::Bound, String)> {
        use windows::core::Interface;
        use windows::Win32::Media::DirectShow::IBaseFilter;
        let init = com::ComInit::mta()?;
        let mut names = Vec::new();
        let mut filter: Option<IBaseFilter> = None;
        for (i, mon) in com::monikers()?.iter().enumerate() {
            let name = com::friendly_name(mon);
            if i == index && name.as_deref().is_some_and(|n| name_matches(n, expected)) {
                let f: IBaseFilter = unsafe { mon.BindToObject(None, None) }.map_err(|e| {
                    com::com_err(
                        &format!("BindToObject({})", name.as_deref().unwrap_or("")),
                        e,
                    )
                })?;
                filter = Some(f);
            }
            names.push(name);
        }
        choose(&names, index, expected)?;
        let filter = filter.ok_or_else(|| {
            SdkError::Unavailable(format!("could not bind DirectShow device {index}"))
        })?;
        let procamp = filter.cast().ok();
        let camera = filter.cast().ok();
        let name = names[index].clone().unwrap_or_default();
        Ok((
            com::Bound {
                procamp,
                camera,
                _filter: filter,
                _init: init,
            },
            name,
        ))
    }

    fn get(&self, iface: Iface, pid: i32) -> Option<RawGet> {
        let (mut value, mut flags) = (0i32, 0i32);
        let r = unsafe {
            match iface {
                Iface::Procamp => self.procamp.as_ref()?.Get(pid, &mut value, &mut flags),
                Iface::Camera => self.camera.as_ref()?.Get(pid, &mut value, &mut flags),
            }
        };
        Some(RawGet {
            hr: r.map_or_else(|e| e.code().0, |_| 0),
            value,
            flags,
        })
    }

    fn range(&self, iface: Iface, pid: i32) -> Option<RawRangeOut> {
        let (mut min, mut max, mut step, mut default, mut caps) = (0i32, 0i32, 0i32, 0i32, 0i32);
        let r = unsafe {
            match iface {
                Iface::Procamp => self.procamp.as_ref()?.GetRange(
                    pid,
                    &mut min,
                    &mut max,
                    &mut step,
                    &mut default,
                    &mut caps,
                ),
                Iface::Camera => self.camera.as_ref()?.GetRange(
                    pid,
                    &mut min,
                    &mut max,
                    &mut step,
                    &mut default,
                    &mut caps,
                ),
            }
        };
        Some(RawRangeOut {
            hr: r.map_or_else(|e| e.code().0, |_| 0),
            min,
            max,
            step,
            default,
            caps,
        })
    }

    fn has(&self, iface: Iface) -> bool {
        match iface {
            Iface::Procamp => self.procamp.is_some(),
            Iface::Camera => self.camera.is_some(),
        }
    }

    /// HRESULT on failure. The caller checks first whether the interface exists (`has`).
    fn set(&self, iface: Iface, pid: i32, value: i32, flags: i32) -> std::result::Result<(), i32> {
        let r = unsafe {
            match (iface, &self.procamp, &self.camera) {
                (Iface::Procamp, Some(p), _) => p.Set(pid, value, flags),
                (Iface::Camera, _, Some(c)) => c.Set(pid, value, flags),
                _ => return Err(0x8000_4002u32 as i32), // E_NOINTERFACE — has() 뒤라 오지 않는다
            }
        };
        r.map_err(|e| e.code().0)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// GUID strings from Python `_ctrl_dshow.py` (they must be in that file as is — the test below checks).
    #[cfg(windows)]
    const PY_GUIDS: [(&str, &str); 7] = [
        (
            "CLSID_SystemDeviceEnum",
            "62BE5D10-60EB-11D0-BD3B-00A0C911CE86",
        ),
        ("IID_ICreateDevEnum", "29840822-5B84-11D0-BD3B-00A0C911CE86"),
        (
            "CLSID_VideoInputDeviceCategory",
            "860BB310-5D01-11D0-BD3B-00A0C911CE86",
        ),
        ("IID_IPropertyBag", "55272A00-42CB-11CE-8135-00AA004BB851"),
        ("IID_IBaseFilter", "56A86895-0AD4-11CE-B03A-0020AF0BA770"),
        (
            "IID_IAMVideoProcAmp",
            "C6E13360-30AC-11D0-A18C-00A0C9118956",
        ),
        (
            "IID_IAMCameraControl",
            "C6E13370-30AC-11D0-A18C-00A0C9118956",
        ),
    ];

    /// uuid.UUID(text).bytes_le — the 16 bytes that go into a Python ctypes GUID structure.
    #[cfg(windows)]
    fn bytes_le(text: &str) -> [u8; 16] {
        let hex: String = text.chars().filter(|c| *c != '-').collect();
        let b: Vec<u8> = (0..16)
            .map(|i| u8::from_str_radix(&hex[2 * i..2 * i + 2], 16).unwrap())
            .collect();
        let mut o = [0u8; 16];
        o[..4].copy_from_slice(&[b[3], b[2], b[1], b[0]]);
        o[4..6].copy_from_slice(&[b[5], b[4]]);
        o[6..8].copy_from_slice(&[b[7], b[6]]);
        o[8..].copy_from_slice(&b[8..]);
        o
    }

    #[cfg(windows)]
    #[test]
    fn guids_match_python_byte_for_byte() {
        let src = include_str!("../../../python/opticmix_vision/_ctrl_dshow.py");
        for (name, text) in PY_GUIDS {
            assert!(
                src.contains(&format!("{name} = GUID.parse(\"{text}\")")),
                "{name} differs from the Python file"
            );
            assert_eq!(guid_bytes(name).unwrap(), bytes_le(text), "{name}");
        }
        assert_eq!(guid_bytes("nope"), None);
    }

    #[test]
    fn choose_refuses_a_different_camera() {
        let n = |v: &[Option<&str>]| v.iter().map(|x| x.map(String::from)).collect::<Vec<_>>();
        let two = n(&[Some("Example Front Webcam"), Some("WN Dual Camera")]);
        assert_eq!(choose(&two, 1, &EXPECTED_NAMES), Ok(()));
        assert_eq!(
            choose(&two, 0, &EXPECTED_NAMES),
            Err(SdkError::Unavailable(
                "device 0 is 'Example Front Webcam', not a Weevia S1 (WN Dual Camera)".into()
            ))
        );
        assert_eq!(
            choose(&n(&[Some("WN Dual Camera")]), 3, &EXPECTED_NAMES),
            Err(SdkError::Unavailable(
                "no DirectShow video device at index 3 (found 1)".into()
            ))
        );
        assert_eq!(
            choose(&n(&[None]), 0, &EXPECTED_NAMES),
            Err(SdkError::Unavailable(
                "device 0 is None, not a Weevia S1 (WN Dual Camera)".into()
            ))
        );
        assert_eq!(
            choose(&n(&[Some("usb wn dual camera #2")]), 0, &EXPECTED_NAMES),
            Ok(())
        ); // 대소문자 무시 부분일치
    }

    /// Enumerates real devices through COM (no binding, names only). Left out of the default run — only explicitly:
    /// `cargo test -- --ignored enumeration_runs_through_com_without_binding_anything`
    #[test]
    #[ignore]
    fn enumeration_runs_through_com_without_binding_anything() {
        let names = enumerate_video_devices().unwrap();
        assert!(names
            .iter()
            .all(|n| n.as_ref().is_none_or(|s| !s.is_empty())));
    }
    #[test]
    fn set_flags_follow_python() {
        assert_eq!(set_flags("exposure", Some(true)), FLAG_AUTO);
        assert_eq!(set_flags("exposure", Some(false)), FLAG_MANUAL);
        assert_eq!(set_flags("white_balance", None), FLAG_MANUAL); // auto None = 수동 상태에서 값 쓰기
        assert_eq!(set_flags("gain", None), 0); // OpenCV 가 쓰던 값(게인·LED 검증 경로)
        assert_eq!(set_flags("power_line_frequency", Some(true)), 0);
    }

    #[test]
    #[cfg(not(windows))]
    fn directshow_is_refused_off_windows() {
        assert_eq!(
            DShowBackend::new(0, &EXPECTED_NAMES).err(),
            Some(SdkError::Unavailable("DirectShow controls are Windows-only".into()))
        );
    }

    #[test]
    #[cfg(windows)]
    fn a_backend_that_was_not_started_touches_nothing() {
        let mut b = DShowBackend::new(0, &EXPECTED_NAMES).unwrap();
        assert_eq!(
            (b.name(), b.exposure_units(), b.note()),
            ("dshow", "log2_s_nominal", "")
        );
        assert!(!b.supports("iris")); // 이름 표 밖은 COM 을 부르지 않고 거부
        assert_eq!(
            b.read("gain"),
            Err(SdkError::Backend {
                what: "the procamp interface is not available on this device".into(),
                hresult: None
            })
        );
        assert_eq!(b.raw_get(Iface::Camera, 5), None);
        assert_eq!(b.raw_range(Iface::Procamp, 6), None);
        b.close().unwrap();
    }
}
