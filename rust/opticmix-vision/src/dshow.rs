//! Windows 네이티브 백엔드 — DirectShow IAMVideoProcAmp / IAMCameraControl(Python `_ctrl_dshow.py`).
//!
//! 전원 주파수(ProcAmp 13)·낮은 빛 보상(CameraControl 19)·범위·기본값·자동 노출 읽기가 OpenCV 로는 안 돼서 직접 부른다.
//! 모든 COM 호출은 Controls 워커 스레드 한 곳에서만 한다(start/close 포함). 장치는 OpenCV 와 같은 순서로 센 index 번째
//! 모니커이고, 이름이 Weevia S1 이 아니면 거부한다 — 다른 카메라에 쓰지 않는다.
//!
//! 🔴 표준 Get/GetRange/Set 만 쓴다. 제조사 DLL·확장 유닛·KS 속성 직접 호출 경로는 만들지 않는다.

use crate::error::{Result, SdkError};
pub use crate::spec::EXPECTED_NAMES;

/// Python `_ctrl_dshow.py` 와 같은 값. 0.62.2 에서 이 두 CLSID 는 MediaFoundation 모듈에 있어 직접 둔다.
pub const CLSID_SYSTEM_DEVICE_ENUM: u128 = 0x62be5d10_60eb_11d0_bd3b_00a0c911ce86;
pub const CLSID_VIDEO_INPUT_DEVICE_CATEGORY: u128 = 0x860bb310_5d01_11d0_bd3b_00a0c911ce86;

/// 장치 이름이 기대 이름 중 하나를 (대소문자 무시) 포함하는가.
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

/// index 번째 장치가 Weevia S1 인지 확인한다. 아니면 Unavailable — 다른 카메라에 쓰지 않는다.
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

/// DirectShow 영상 입력 장치 이름을 OpenCV 와 같은 순서로(바인드 실패도 센다). 아무 장치에도 바인드하지 않는다(읽기만).
/// Windows 가 아니면 빈 목록.
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

/// 시험·진단용: 이 모듈이 쓰는 GUID 의 메모리 16바이트(Python ctypes GUID 와 같은 배치).
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

    /// 바인드된 장치. 필드 순서 = 놓는 순서: 제어 인터페이스 → 필터 → CoUninitialize.
    pub(crate) struct Bound {
        pub(crate) procamp: Option<windows::Win32::Media::DirectShow::IAMVideoProcAmp>,
        pub(crate) camera: Option<windows::Win32::Media::DirectShow::IAMCameraControl>,
        pub(crate) _filter: windows::Win32::Media::DirectShow::IBaseFilter,
        pub(crate) _init: ComInit,
    }

    /// 이 스레드의 COM(MTA). 이미 다른 모드면(RPC_E_CHANGED_MODE) 그대로 쓰고 해제하지 않는다 — Python 과 같다.
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

    /// 영상 입력 장치 모니커 전부, OpenCV 와 같은 순서. 영상 장치가 하나도 없으면 빈 목록.
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

    /// 모니커의 FriendlyName(속성 가방만 연다 — 장치에는 바인드하지 않는다).
    pub(crate) fn friendly_name(mon: &IMoniker) -> Option<String> {
        let bag: IPropertyBag = unsafe { mon.BindToStorage(None, None) }.ok()?;
        let mut var = VARIANT::default();
        unsafe { bag.Read(w!("FriendlyName"), &mut var, None) }.ok()?;
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

/// Set 의 flags(Python `DShowBackend.write`): 자동 가능 키는 auto→1, 그 외(수동·None)→2. 나머지 키는 0(OpenCV 가 쓰던 값).
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

/// 진단(읽기 전용) Get 결과 — 이름 표 밖 id 를 볼 때.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RawGet {
    pub hr: i32,
    pub value: i32,
    pub flags: i32,
}

/// 진단(읽기 전용) GetRange 결과.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RawRangeOut {
    pub hr: i32,
    pub min: i32,
    pub max: i32,
    pub step: i32,
    pub default: i32,
    pub caps: i32,
}

/// DirectShow 백엔드. 만들 때는 아무것도 열지 않는다 — `start()` 가 (워커 스레드에서) 열거·이름 확인·바인드를 한다.
pub struct DShowBackend {
    index: usize,
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
    /// Windows 가 아니면 Unavailable.
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

    /// 진단: 아무 id 나 Get(읽기만). 인터페이스가 없거나 시작 전이면 None.
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

    /// 진단: 아무 id 나 GetRange(읽기만). 인터페이스가 없거나 시작 전이면 None.
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
    /// index 번째 모니커가 기대 이름이면 IBaseFilter 로 바인드하고 두 제어 인터페이스를 얻는다.
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

    /// 실패하면 HRESULT. 인터페이스가 있는지는 부르는 쪽이 먼저 본다(`has`).
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

    /// Python `_ctrl_dshow.py` 의 GUID 문자열(그 파일에 그대로 있어야 한다 — 아래 시험이 확인).
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

    /// uuid.UUID(text).bytes_le — Python ctypes GUID 구조체에 들어가는 16바이트.
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
                "{name} 가 Python 파일과 다르다"
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

    /// 🔴 COM 으로 실제 장치를 열거한다(바인드 없음, 이름만). 기본 실행에서 빠진다 — 명시적으로만:
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
