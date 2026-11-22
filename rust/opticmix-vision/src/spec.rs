//! 제어 표 14개와 상수. Python `controls.CONTROLS`·`_ctrl_dshow.KEYS`·`device.WEEVIA_S1*` 의 원본은 이제 여기다
//! (Python 래퍼는 `controls_table()` 에서 CONTROLS 를 만든다 — 표가 두 벌이 되지 않게).

/// 제어 값의 종류(Python `Kind`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Int,
    Bool,
    Menu,
}

/// value = 그 키의 값, auto = 그 키의 자동/수동 flags.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Role {
    Value,
    Auto,
}

/// 영상 효과 검증 상태(Python `Effect`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Effect {
    Verified,
    Partial,
    Unverified,
    NoneExpectedMono,
}

impl Kind {
    pub fn as_str(self) -> &'static str {
        match self {
            Kind::Int => "int",
            Kind::Bool => "bool",
            Kind::Menu => "menu",
        }
    }
}

impl Role {
    pub fn as_str(self) -> &'static str {
        match self {
            Role::Value => "value",
            Role::Auto => "auto",
        }
    }
}

impl Effect {
    pub fn as_str(self) -> &'static str {
        match self {
            Effect::Verified => "verified",
            Effect::Partial => "partial",
            Effect::Unverified => "unverified",
            Effect::NoneExpectedMono => "none_expected_mono",
        }
    }
}

/// 제어 하나. `key` 는 백엔드 키 = 가드 그룹(Windows: KS 속성 하나).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ControlSpec {
    pub name: &'static str,
    pub key: &'static str,
    pub role: Role,
    pub kind: Kind,
    pub effect: Effect,
    /// 내부 검증 기록 ID(지금은 전부 없음).
    pub ledger: Option<&'static str>,
    /// menu: 장치 값 0,1,2… 순서의 이름(auto 역할은 manual/auto).
    pub choices: Option<&'static [&'static str]>,
    /// 흑백 센서라 영상에 효과가 없을 것.
    pub mono: bool,
    pub note: &'static str,
}

const fn c(
    name: &'static str,
    key: &'static str,
    role: Role,
    kind: Kind,
    effect: Effect,
) -> ControlSpec {
    ControlSpec {
        name,
        key,
        role,
        kind,
        effect,
        ledger: None,
        choices: None,
        mono: false,
        note: "",
    }
}

const fn mono(s: ControlSpec) -> ControlSpec {
    ControlSpec { mono: true, ..s }
}

/// Python `controls.CONTROLS` 그대로(순서·문안 포함).
pub const CONTROLS: [ControlSpec; 14] = {
    use Effect::*;
    use Kind::*;
    use Role::*;
    [
        c("brightness", "brightness", Value, Int, Unverified),
        c("contrast", "contrast", Value, Int, Unverified),
        mono(c("hue", "hue", Value, Int, NoneExpectedMono)),
        mono(c("saturation", "saturation", Value, Int, NoneExpectedMono)),
        c("sharpness", "sharpness", Value, Int, Unverified),
        c("gamma", "gamma", Value, Int, Unverified),
        mono(c("white_balance", "white_balance", Value, Int, NoneExpectedMono)),
        mono(c("auto_white_balance", "white_balance", Auto, Bool, NoneExpectedMono)),
        ControlSpec {
            note: "IR illuminator: off at 0-16, light from 24, no change above 96 (measured on two units)",
            ..c("backlight_compensation", "backlight_compensation", Value, Int, Verified)
        },
        c("gain", "gain", Value, Int, Verified),
        ControlSpec {
            choices: Some(&["disabled", "50hz", "60hz"]),
            note: "device default is 'disabled'; the 60hz seen at start appears to come from the Windows driver",
            ..c("power_line_frequency", "power_line_frequency", Value, Menu, Unverified)
        },
        ControlSpec {
            note: "units are what the driver reports; not converted to time",
            ..c("exposure", "exposure", Value, Int, Verified)
        },
        ControlSpec {
            choices: Some(&["manual", "auto"]),
            note: "switching back to auto once froze one eye for about 1 s; it recovered by itself",
            ..c("auto_exposure", "exposure", Auto, Menu, Verified)
        },
        ControlSpec {
            note: "only matters while auto_exposure is 'auto'; the camera may then lower the frame rate. \
                   This camera reports 5 (range 5-5), not 0/1 — writes untested",
            ..c("auto_exposure_priority", "auto_exposure_priority", Value, Bool, Unverified)
        },
    ]
};

/// 이름으로 제어를 찾는다(Python `_BY_NAME`).
pub fn spec(name: &str) -> Option<&'static ControlSpec> {
    CONTROLS.iter().find(|c| c.name == name)
}

/// 그룹(키) → 자동/수동 제어 이름(Python `_AUTO_NAME`).
pub fn auto_name(key: &str) -> Option<&'static str> {
    CONTROLS
        .iter()
        .find(|c| c.key == key && c.role == Role::Auto)
        .map(|c| c.name)
}

/// 자동 제어가 있는 그룹 → 값 제어 이름(Python `_VALUE_NAME`).
pub fn value_name(key: &str) -> Option<&'static str> {
    auto_name(key)?;
    CONTROLS
        .iter()
        .find(|c| c.key == key && c.role == Role::Value)
        .map(|c| c.name)
}

/// 자동 제어가 있는 그룹들(표 순서).
pub fn auto_groups() -> impl Iterator<Item = &'static str> {
    CONTROLS
        .iter()
        .filter(|c| c.role == Role::Auto)
        .map(|c| c.key)
}

// --- DirectShow KS id -----------------------------------------------------------------------

/// DirectShow 인터페이스: IAMVideoProcAmp / IAMCameraControl.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Iface {
    Procamp,
    Camera,
}

impl Iface {
    pub fn as_str(self) -> &'static str {
        match self {
            Iface::Procamp => "procamp",
            Iface::Camera => "camera",
        }
    }
}

/// Python `_ctrl_dshow.KEYS`. id 13·19 는 DirectShow 열거형 밖이지만 kswdmcap.ax 10.0.26100.1 이 KS 속성으로 넘긴다
/// (2026-09-24 확인). 26100 이전 빌드는 미검증.
pub const KEYS: [(&str, Iface, i32); 12] = [
    ("brightness", Iface::Procamp, 0),
    ("contrast", Iface::Procamp, 1),
    ("hue", Iface::Procamp, 2),
    ("saturation", Iface::Procamp, 3),
    ("sharpness", Iface::Procamp, 4),
    ("gamma", Iface::Procamp, 5),
    ("white_balance", Iface::Procamp, 7),
    ("backlight_compensation", Iface::Procamp, 8),
    ("gain", Iface::Procamp, 9),
    ("power_line_frequency", Iface::Procamp, 13),
    ("exposure", Iface::Camera, 4),
    ("auto_exposure_priority", Iface::Camera, 19),
];

pub fn ks_id(key: &str) -> Option<(Iface, i32)> {
    KEYS.iter().find(|k| k.0 == key).map(|k| (k.1, k.2))
}

/// 자동/수동 flags 가 뜻을 가지는 키.
pub const AUTO_CAPABLE: [&str; 2] = ["exposure", "white_balance"];

pub fn is_auto_capable(key: &str) -> bool {
    AUTO_CAPABLE.contains(&key)
}

pub const FLAG_AUTO: i32 = 1;
pub const FLAG_MANUAL: i32 = 2;

// --- 가드 기본값 -------------------------------------------------------------------------------

/// 벤더 권고(2026-09-28): 노출 명령 사이 최소 12 ms. 다른 제어에는 벤더 수치가 없어 같은 간격을 쓴다.
pub const VENDOR_MIN_COMMAND_GAP_S: f64 = 0.012;
/// 이 프로세스의 쓰기 합계가 이 수에 닿을 때와 그 뒤 이 수마다 경고.
pub const WARN_AFTER_TOTAL: u64 = 3;

// --- 장치 ----------------------------------------------------------------------------------

/// 🔴 펌웨어 USB 장치 이름. 제품명이 바뀌어도 이 값은 바꾸지 않는다.
pub const WEEVIA_S1_NAME: &str = "WN Dual Camera";
/// DirectShow 백엔드가 받아들이는 이름(대소문자 무시 부분일치) — 다른 카메라에 쓰지 않는다.
pub const EXPECTED_NAMES: [&str; 1] = [WEEVIA_S1_NAME];

pub const LAYOUTS: [&str; 3] = ["sbs", "tb", "plane_pack"];
pub const LR_ORDERS: [&str; 2] = ["first_is_left", "first_is_right"];
pub const BACKENDS: [&str; 5] = ["any", "dshow", "msmf", "v4l2", "avfoundation"];

/// 실측 설정(Python `device.WEEVIA_S1`): SBS 의 첫 반쪽이 오른쪽 눈(2026-09-16), MJPG 를 요청해야 90 fps.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct StreamConfig {
    pub layout: &'static str,
    pub lr_order: &'static str,
    pub backend: &'static str,
    pub width: u32,
    pub height: u32,
    pub fps: f64,
    pub fourcc: &'static str,
}

pub const WEEVIA_S1: StreamConfig = StreamConfig {
    layout: "sbs",
    lr_order: "first_is_right",
    backend: "dshow",
    width: 1600,
    height: 800,
    fps: 90.0,
    fourcc: "MJPG",
};

#[cfg(test)]
mod tests {
    use super::*;

    fn by(name: &str) -> &'static ControlSpec {
        spec(name).unwrap()
    }

    #[test]
    fn the_table_has_the_fourteen_advertised_controls() {
        let names: Vec<_> = CONTROLS.iter().map(|c| c.name).collect();
        assert_eq!(
            names,
            [
                "brightness",
                "contrast",
                "hue",
                "saturation",
                "sharpness",
                "gamma",
                "white_balance",
                "auto_white_balance",
                "backlight_compensation",
                "gain",
                "power_line_frequency",
                "exposure",
                "auto_exposure",
                "auto_exposure_priority",
            ]
        );
    }

    #[test]
    fn auto_and_value_of_one_property_share_a_guard_group() {
        assert_eq!(by("auto_exposure").key, "exposure");
        assert_eq!(by("exposure").key, "exposure");
        assert_eq!(by("auto_white_balance").key, "white_balance");
        assert_eq!(by("white_balance").key, "white_balance");
        assert_eq!(auto_name("exposure"), Some("auto_exposure"));
        assert_eq!(value_name("white_balance"), Some("white_balance"));
        assert_eq!(auto_name("gain"), None);
    }

    #[test]
    fn mono_controls_are_flagged() {
        let mono: Vec<_> = CONTROLS.iter().filter(|c| c.mono).map(|c| c.name).collect();
        assert_eq!(
            mono,
            ["hue", "saturation", "white_balance", "auto_white_balance"]
        );
        assert!(CONTROLS
            .iter()
            .filter(|c| c.mono)
            .all(|c| c.effect == Effect::NoneExpectedMono));
    }

    #[test]
    fn menus_and_notes_match_python() {
        assert_eq!(
            by("power_line_frequency").choices,
            Some(&["disabled", "50hz", "60hz"][..])
        );
        assert_eq!(by("auto_exposure").choices, Some(&["manual", "auto"][..]));
        assert_eq!(by("auto_exposure").kind, Kind::Menu);
        assert_eq!(by("auto_white_balance").kind, Kind::Bool);
        assert_eq!(by("auto_exposure_priority").kind, Kind::Bool);
        assert_eq!(by("auto_exposure_priority").role, Role::Value);
        assert_eq!(by("gain").effect, Effect::Verified);
        assert!(by("backlight_compensation")
            .note
            .starts_with("IR illuminator: off at 0-16"));
        assert!(by("auto_exposure_priority")
            .note
            .ends_with("not 0/1 — writes untested"));
        assert!(CONTROLS.iter().all(|c| c.ledger.is_none()));
    }

    #[test]
    fn every_named_control_has_a_ks_id() {
        for c in CONTROLS.iter() {
            assert!(ks_id(c.key).is_some(), "{}", c.key);
        }
        assert_eq!(ks_id("power_line_frequency"), Some((Iface::Procamp, 13)));
        assert_eq!(ks_id("auto_exposure_priority"), Some((Iface::Camera, 19)));
        assert_eq!(ks_id("exposure"), Some((Iface::Camera, 4)));
        assert_eq!(KEYS.len(), 12);
        assert!(
            is_auto_capable("exposure")
                && is_auto_capable("white_balance")
                && !is_auto_capable("gain")
        );
    }

    #[test]
    fn usb_device_name_never_changes() {
        // 🔴 펌웨어 USB 장치 이름이다. 제품명(Weevia)이 바뀌어도 이 값은 바꾸지 않는다 — 바꾸면 카메라를 못 찾는다.
        assert_eq!(WEEVIA_S1_NAME, "WN Dual Camera");
        assert_eq!(EXPECTED_NAMES, ["WN Dual Camera"]);
    }

    #[test]
    fn weevia_s1_config_matches_python() {
        let m = WEEVIA_S1;
        assert_eq!(
            (m.layout, m.lr_order, m.backend),
            ("sbs", "first_is_right", "dshow")
        );
        assert_eq!(
            (m.width, m.height, m.fps, m.fourcc),
            (1600, 800, 90.0, "MJPG")
        );
        assert!(
            LAYOUTS.contains(&m.layout)
                && LR_ORDERS.contains(&m.lr_order)
                && BACKENDS.contains(&m.backend)
        );
    }

    #[test]
    fn vendor_spacing_and_warning_constants() {
        assert_eq!(VENDOR_MIN_COMMAND_GAP_S, 0.012);
        assert_eq!(WARN_AFTER_TOTAL, 3);
        assert_eq!((FLAG_AUTO, FLAG_MANUAL), (1, 2));
    }
}
