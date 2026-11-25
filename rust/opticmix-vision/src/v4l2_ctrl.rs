//! Linux controls (reading run with the camera once, 2026-11-25; writing not run) — standard V4L2 controls only (`v4l`'s `control`/`set_control` = VIDIOC_QUERY_EXT_CTRL·
//! VIDIOC_G_EXT_CTRLS·VIDIOC_S_EXT_CTRLS, standard control IDs). The exposure unit is "100us", the same as Python OpenCV V4L2.

use std::collections::HashMap;
use std::path::PathBuf;

use v4l::control::{Control, Description, Type, Value};
use v4l::Device as VDev;

use crate::backend::{Backend, RawRange};
use crate::error::{Result, SdkError};
use crate::spec::{is_auto_capable, V4L2_CID as CID};

const CID_AUTO_WHITE_BALANCE: u32 = 0x0098_090c;
const CID_EXPOSURE_AUTO: u32 = 0x009a_0901;
const EXPOSURE_MANUAL: i64 = 1;
const EXPOSURE_APERTURE_PRIORITY: i64 = 3;

fn cid(key: &str) -> Option<u32> {
    CID.iter().find(|c| c.0 == key).map(|c| c.1)
}

/// (control ID, value) pairs one write sends, in order — auto/manual mode first, then the value. Exposure mode: auto = aperture priority (3),
/// manual = 1; white balance auto is 0/1 (if the driver says boolean, `value_for` sends a Boolean).
fn write_plan(key: &str, value: Option<i64>, auto: Option<bool>) -> Result<Vec<(u32, i64)>> {
    let id = cid(key).ok_or_else(|| SdkError::Backend { what: format!("no V4L2 control for {key}"), hresult: None })?;
    let mut plan = Vec::new();
    match (key, auto) {
        ("exposure", Some(a)) => {
            plan.push((CID_EXPOSURE_AUTO, if a { EXPOSURE_APERTURE_PRIORITY } else { EXPOSURE_MANUAL }))
        }
        ("white_balance", Some(a)) => plan.push((CID_AUTO_WHITE_BALANCE, a as i64)),
        _ => {}
    }
    plan.extend(value.map(|v| (id, v)));
    Ok(plan)
}

/// Boolean for a control the driver says is boolean, Integer for the rest.
fn value_for(typ: Option<Type>, v: i64) -> Value {
    match typ {
        Some(Type::Boolean) => Value::Boolean(v != 0),
        _ => Value::Integer(v),
    }
}

fn be(what: String, e: std::io::Error) -> SdkError {
    SdkError::Backend { what: format!("{what}: {e}"), hresult: None }
}

pub struct V4l2Controls {
    path: PathBuf,
    name: String,
    dev: Option<VDev>,
    descs: HashMap<u32, Description>,
}

impl V4l2Controls {
    pub fn new(path: PathBuf, name: String) -> V4l2Controls {
        V4l2Controls { path, name, dev: None, descs: HashMap::new() }
    }

    fn dev(&self) -> Result<&VDev> {
        self.dev.as_ref().ok_or(SdkError::Closed)
    }

    fn get_int(&self, id: u32) -> Result<i64> {
        match self.dev()?.control(id).map_err(|e| be(format!("VIDIOC_G_EXT_CTRLS 0x{id:08x}"), e))?.value {
            Value::Integer(v) => Ok(v),
            Value::Boolean(b) => Ok(b as i64),
            other => Err(SdkError::Backend { what: format!("control 0x{id:08x} gave {other:?}"), hresult: None }),
        }
    }

    fn set_int(&self, id: u32, v: i64) -> Result<()> {
        let value = value_for(self.descs.get(&id).map(|d| d.typ), v);
        self.dev()?
            .set_control(Control { id, value })
            .map_err(|e| be(format!("VIDIOC_S_EXT_CTRLS 0x{id:08x}={v}"), e))
    }
}

impl Backend for V4l2Controls {
    fn name(&self) -> &str {
        "v4l2"
    }
    fn exposure_units(&self) -> &str {
        "100us"
    }
    fn note(&self) -> &str {
        "Linux V4L2 controls (experimental)"
    }
    fn device_name(&self) -> &str {
        &self.name
    }
    fn start(&mut self) -> Result<()> {
        let d = VDev::with_path(&self.path).map_err(|e| be(format!("open {}", self.path.display()), e))?;
        self.descs = d
            .query_controls()
            .map_err(|e| be("VIDIOC_QUERY_EXT_CTRL".into(), e))?
            .into_iter()
            .map(|c| (c.id, c))
            .collect();
        self.dev = Some(d);
        Ok(())
    }
    fn supports(&mut self, key: &str) -> bool {
        cid(key).is_some_and(|id| self.descs.contains_key(&id))
    }
    fn query(&mut self, key: &str) -> Result<Option<RawRange>> {
        let Some(d) = cid(key).and_then(|id| self.descs.get(&id)) else { return Ok(None) };
        Ok(Some(RawRange {
            minimum: d.minimum,
            maximum: d.maximum,
            step: d.step as i64,
            default: Some(d.default),
            can_auto: is_auto_capable(key),
        }))
    }
    fn read(&mut self, key: &str) -> Result<(i64, Option<bool>)> {
        let id = cid(key).ok_or_else(|| SdkError::Backend { what: format!("no V4L2 control for {key}"), hresult: None })?;
        let value = self.get_int(id)?;
        let auto = match key {
            "exposure" => Some(self.get_int(CID_EXPOSURE_AUTO)? != EXPOSURE_MANUAL),
            "white_balance" => Some(self.get_int(CID_AUTO_WHITE_BALANCE)? != 0),
            _ => None,
        };
        Ok((value, auto))
    }
    fn write(&mut self, key: &str, value: Option<i64>, auto: Option<bool>) -> Result<()> {
        for (id, v) in write_plan(key, value, auto)? {
            self.set_int(id, v)?;
        }
        Ok(())
    }
    fn close(&mut self) -> Result<()> {
        self.dev = None;
        Ok(())
    }
    /// Control writes on Linux have not been run on the camera: the driver's defaults are shown (`query`), not written.
    fn writes_defaults(&self) -> bool {
        false
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_dshow_key_has_a_standard_v4l2_id() {
        for (key, _, _) in crate::spec::KEYS {
            assert!(cid(key).is_some(), "{key}");
        }
    }

    /// Checks the 14 IDs and the 2 exposure mode values against the kernel header (linux/videodev2.h, which `v4l` reads with bindgen at build time).
    #[test]
    fn ids_are_the_kernel_header_values() {
        use v4l::v4l_sys as k;
        let want = [
            ("brightness", k::V4L2_CID_BRIGHTNESS),
            ("contrast", k::V4L2_CID_CONTRAST),
            ("saturation", k::V4L2_CID_SATURATION),
            ("hue", k::V4L2_CID_HUE),
            ("gamma", k::V4L2_CID_GAMMA),
            ("gain", k::V4L2_CID_GAIN),
            ("power_line_frequency", k::V4L2_CID_POWER_LINE_FREQUENCY),
            ("white_balance", k::V4L2_CID_WHITE_BALANCE_TEMPERATURE),
            ("sharpness", k::V4L2_CID_SHARPNESS),
            ("backlight_compensation", k::V4L2_CID_BACKLIGHT_COMPENSATION),
            ("exposure", k::V4L2_CID_EXPOSURE_ABSOLUTE),
            ("auto_exposure_priority", k::V4L2_CID_EXPOSURE_AUTO_PRIORITY),
        ];
        assert_eq!(CID.len(), want.len());
        for (key, id) in want {
            assert_eq!(cid(key), Some(id), "{key}");
        }
        assert_eq!(CID_AUTO_WHITE_BALANCE, k::V4L2_CID_AUTO_WHITE_BALANCE);
        assert_eq!(CID_EXPOSURE_AUTO, k::V4L2_CID_EXPOSURE_AUTO);
        assert_eq!(EXPOSURE_MANUAL, k::v4l2_exposure_auto_type_V4L2_EXPOSURE_MANUAL as i64);
        assert_eq!(EXPOSURE_APERTURE_PRIORITY, k::v4l2_exposure_auto_type_V4L2_EXPOSURE_APERTURE_PRIORITY as i64);
        use v4l::v4l2::vidioc::{_IOC_TYPE, VIDIOC_QUERYCTRL};
        assert_eq!(VIDIOC_QUERYCTRL, crate::spec::V4L2_VIDIOC_QUERYCTRL as _IOC_TYPE);
        assert_eq!(std::mem::size_of::<k::v4l2_queryctrl>(), 68);
    }

    /// The driver's default is reported (describe), but reset_to_defaults writes nothing on Linux.
    #[test]
    fn the_driver_default_is_shown_not_written() {
        let id = cid("gain").unwrap();
        let mut c = V4l2Controls::new(PathBuf::from("/dev/null"), String::new());
        c.descs.insert(id, Description {
            id,
            typ: Type::Integer,
            name: "Gain".into(),
            minimum: 0,
            maximum: 100,
            step: 1,
            default: 32,
            flags: v4l::control::Flags::empty(),
            items: None,
        });
        assert_eq!(c.query("gain").unwrap().unwrap().default, Some(32));
        assert!(!c.writes_defaults());
    }

    #[test]
    fn a_write_sets_the_mode_first_then_the_value() {
        let (exp, wb, gain) = (cid("exposure").unwrap(), cid("white_balance").unwrap(), cid("gain").unwrap());
        assert_eq!(write_plan("exposure", Some(156), Some(false)).unwrap(), [(CID_EXPOSURE_AUTO, 1), (exp, 156)]);
        assert_eq!(write_plan("exposure", None, Some(true)).unwrap(), [(CID_EXPOSURE_AUTO, 3)]);
        assert_eq!(write_plan("white_balance", Some(4600), Some(false)).unwrap(), [(CID_AUTO_WHITE_BALANCE, 0), (wb, 4600)]);
        assert_eq!(write_plan("white_balance", None, Some(true)).unwrap(), [(CID_AUTO_WHITE_BALANCE, 1)]);
        assert_eq!(write_plan("gain", Some(40), None).unwrap(), [(gain, 40)]);
        assert!(write_plan("focus", Some(1), None).is_err());
        assert_eq!(value_for(Some(Type::Boolean), 1), Value::Boolean(true));
        assert_eq!(value_for(Some(Type::Integer), 1), Value::Integer(1));
        assert_eq!(value_for(None, -5), Value::Integer(-5));
    }
}
