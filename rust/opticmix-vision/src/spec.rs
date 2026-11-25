//! The 14-control table and constants. The source of Python `controls.CONTROLS`·`_ctrl_dshow.KEYS`·`device.WEEVIA_S1*` now lives here
//! (the Python wrapper builds CONTROLS from `controls_table()` — so there are not two copies of the table).

/// Kind of control value (Python `Kind`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Int,
    Bool,
    Menu,
}

/// value = the key's value, auto = the key's auto/manual flags.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Role {
    Value,
    Auto,
}

/// Image-effect verification state (Python `Effect`).
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

/// One control. `key` is the backend key = guard group (Windows: one KS property).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ControlSpec {
    pub name: &'static str,
    pub key: &'static str,
    pub role: Role,
    pub kind: Kind,
    pub effect: Effect,
    /// Internal verification record ID (none at present).
    pub ledger: Option<&'static str>,
    /// menu: names in device value order 0,1,2… (for the auto role: manual/auto).
    pub choices: Option<&'static [&'static str]>,
    /// Mono sensor, so no effect on the image is expected.
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

/// Python `controls.CONTROLS` as is (order and text included).
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
            note: "IR illuminator: no change above 96 (two units); off at 0-16, light from 24 (one unit)",
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

/// Finds a control by name (Python `_BY_NAME`).
pub fn spec(name: &str) -> Option<&'static ControlSpec> {
    CONTROLS.iter().find(|c| c.name == name)
}

/// Group (key) → auto/manual control name (Python `_AUTO_NAME`).
pub fn auto_name(key: &str) -> Option<&'static str> {
    CONTROLS
        .iter()
        .find(|c| c.key == key && c.role == Role::Auto)
        .map(|c| c.name)
}

/// Group with an auto control → value control name (Python `_VALUE_NAME`).
pub fn value_name(key: &str) -> Option<&'static str> {
    auto_name(key)?;
    CONTROLS
        .iter()
        .find(|c| c.key == key && c.role == Role::Value)
        .map(|c| c.name)
}

/// Groups that have an auto control (table order).
pub fn auto_groups() -> impl Iterator<Item = &'static str> {
    CONTROLS
        .iter()
        .filter(|c| c.role == Role::Auto)
        .map(|c| c.key)
}

// --- DirectShow KS id -----------------------------------------------------------------------

/// DirectShow interface: IAMVideoProcAmp / IAMCameraControl.
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

/// Python `_ctrl_dshow.KEYS`. ids 13 and 19 are outside the DirectShow enumerations, but kswdmcap.ax 10.0.26100.1 passes them on as KS properties
/// (checked 2026-09-24). Builds before 26100 are not verified.
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

/// Linux: the standard V4L2 control ID of each key (linux/videodev2.h). The Rust V4L2 controls (`v4l2_ctrl`) use them, and so
/// does the Python package's range read on Linux (`_core.V4L2_CID`). Checked against the kernel header in `v4l2_ctrl`'s tests.
pub const V4L2_CID: [(&str, u32); 12] = [
    ("brightness", 0x0098_0900),
    ("contrast", 0x0098_0901),
    ("saturation", 0x0098_0902),
    ("hue", 0x0098_0903),
    ("gamma", 0x0098_0910),
    ("gain", 0x0098_0913),
    ("power_line_frequency", 0x0098_0918),
    ("white_balance", 0x0098_091a), // WHITE_BALANCE_TEMPERATURE
    ("sharpness", 0x0098_091b),
    ("backlight_compensation", 0x0098_091c),
    ("exposure", 0x009a_0902), // EXPOSURE_ABSOLUTE (100 µs)
    ("auto_exposure_priority", 0x009a_0903),
];

/// `VIDIOC_QUERYCTRL` = `_IOWR('V', 36, struct v4l2_queryctrl)`, a 68-byte struct — the Python package's range read on Linux
/// (`_core.V4L2_VIDIOC_QUERYCTRL`). Checked against the `v4l` crate's value in `v4l2_ctrl`'s tests.
pub const V4L2_VIDIOC_QUERYCTRL: u32 = 0xC044_5624;

/// Keys where the auto/manual flags have a meaning.
pub const AUTO_CAPABLE: [&str; 2] = ["exposure", "white_balance"];

pub fn is_auto_capable(key: &str) -> bool {
    AUTO_CAPABLE.contains(&key)
}

pub const FLAG_AUTO: i32 = 1;
pub const FLAG_MANUAL: i32 = 2;

// --- 가드 기본값 -------------------------------------------------------------------------------

/// Vendor advice (2026-09-28): at least 12 ms between exposure commands. There is no vendor figure for other controls, so the same gap is used.
pub const VENDOR_MIN_COMMAND_GAP_S: f64 = 0.012;
/// Warn when this process's write total reaches this number and every this-many writes after.
pub const WARN_AFTER_TOTAL: u64 = 3;

// --- 장치 ----------------------------------------------------------------------------------

/// Firmware USB device name. **Do not change this value** when the product name changes.
pub const WEEVIA_S1_NAME: &str = "WN Dual Camera";
/// Names the DirectShow backend accepts (case-insensitive partial match) — nothing is written to another camera.
pub const EXPECTED_NAMES: [&str; 1] = [WEEVIA_S1_NAME];

pub const LAYOUTS: [&str; 3] = ["sbs", "tb", "plane_pack"];
pub const LR_ORDERS: [&str; 2] = ["first_is_left", "first_is_right"];
pub const BACKENDS: [&str; 5] = ["any", "dshow", "msmf", "v4l2", "avfoundation"];

/// Measured settings (Python `device.WEEVIA_S1`): the first half of SBS is the right eye (2026-09-16); MJPG must be requested to get 90 fps.
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
            .starts_with("IR illuminator: no change above 96"));
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
