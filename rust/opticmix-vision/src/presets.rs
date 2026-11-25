//! Control presets, by the rules of the Python package's `opticmix_vision.presets`: every readable control saved
//! to a JSON file (`Controls::save_preset`), a preset read and checked (`read_preset`, `list_presets`), and what
//! loading it would change on this camera (`Controls::plan_preset`) — the same file format, the same folder
//! (`store_dir()`) and the same messages, so the Rust crate and the Python package read each other's files.
//!
//! ```no_run
//! # #[cfg(feature = "capture")]
//! # fn main() -> opticmix_vision::Result<()> {
//! use opticmix_vision::{presets, Device, DeviceConfig};
//!
//! let mut cam = Device::open(DeviceConfig::weevia_s1())?;
//! let c = cam.controls()?;
//! let path = presets::preset_path("bench")?; // store_dir()/bench.json
//! c.save_preset(&path, None, Some("lab bench"))?; // reads only
//! let plan = c.plan_preset(&path, false, false)?; // the whole preset is checked; writes nothing
//! for (name, current, preset) in &plan.changes {
//!     println!("{name}: {current:?} -> {preset:?}");
//! }
//! # Ok(())
//! # }
//! # #[cfg(not(feature = "capture"))]
//! # fn main() {}
//! ```
//!
//! Applying a preset to the camera is not in the Rust crate yet (Python: `presets load`).
//!
//! A preset is checked as a whole: a different camera model (`SdkError::PresetModel`; `force` accepts it), a
//! control this camera does not have, other units or a value out of the camera's range (`SdkError::Preset`).
//! Controls with no effect on the monochrome sensor (hue, saturation, white balance) are saved, and left out of
//! the plan unless `include_mono` — as the Python package leaves them unwritten.
//!
//! Run without a camera only (simulated controls, preset files written by the Python package); not run with
//! the camera.

use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::Value;

use crate::controls::{ControlInfo, Controls};
use crate::convert::{py_eq, tuple_repr, UserValue};
use crate::error::{Result, SdkError};
use crate::pyrepr::{py_json, py_list_str, py_str};
use crate::spec::{self, Kind, WEEVIA_S1_NAME};

/// The `format` of every preset file (Python `presets.FORMAT`).
pub const FORMAT: &str = "opticmix-controls-preset";
/// The file version this crate writes and reads (Python `presets.VERSION`).
pub const VERSION: i64 = 1;

/// One file in `store_dir()` (Python `presets.PresetInfo`). `name` is the file name without `.json`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PresetInfo {
    pub name: String,
    pub path: PathBuf,
    pub model: Option<String>,
    pub saved_at: String,
    pub note: String,
}

/// What loading a preset would do (`Controls::plan_preset`). `changes` is what Python's `Controls.plan_preset`
/// returns, `(name, current, preset)`; the other three are the fields of the same name in Python's `PresetLoad`.
/// Every list is in the camera's control order.
#[derive(Debug, Clone, PartialEq)]
pub struct PresetPlan {
    /// Controls whose value differs and would be written. `current` is `None` when it cannot be read.
    pub changes: Vec<(String, Option<UserValue>, UserValue)>,
    /// Controls already at the preset's value — would not be written.
    pub unchanged: Vec<String>,
    /// Values of a group the preset puts in auto (e.g. exposure with auto_exposure "auto") — would not be written.
    pub left_out: Vec<String>,
    /// Controls with no effect on the mono sensor whose value differs — would not be written (`include_mono`
    /// puts them in `changes`).
    pub skipped_mono: Vec<String>,
}

fn err(m: impl Into<String>) -> SdkError {
    SdkError::Preset(m.into())
}

/// The preset folder (Python `presets.store_dir()`), beside the calibration folder: `%APPDATA%\opticmix\presets`
/// on Windows; `$XDG_CONFIG_HOME/opticmix/presets`, or `$HOME/.config/opticmix/presets`, elsewhere.
pub fn store_dir() -> Result<PathBuf> {
    Ok(crate::calib::store_dir()?.with_file_name("presets"))
}

/// `store_dir()/<name>.json` (Python `presets.preset_path`). `SdkError::Preset` when `name` is a path rather than
/// a file name.
pub fn preset_path(name: &str) -> Result<PathBuf> {
    if Path::new(name).file_name().and_then(|n| n.to_str()) != Some(name) {
        return Err(err(format!("A preset name is a file name, not a path: {}", py_str(name))));
    }
    Ok(store_dir()?.join(format!("{name}.json")))
}

/// The preset as JSON, after checking `format`, `version`, `device` and `controls` (Python `presets.read_preset`).
/// `SdkError::Preset` for a file that is not a preset or cannot be read (Python: `OSError` for the latter). Does
/// not compare it with a camera.
pub fn read_preset(path: &Path) -> Result<Value> {
    let p = path.display();
    let bytes = std::fs::read(path).map_err(|e| err(format!("{p}: {e}")))?;
    let doc: Value = std::str::from_utf8(&bytes)
        .map_err(|e| e.to_string())
        .and_then(|t| serde_json::from_str(t).map_err(|e| e.to_string()))
        .map_err(|e| err(format!("{p} is not a preset: {e}")))?;
    if doc.get("format").and_then(Value::as_str) != Some(FORMAT) {
        return Err(err(format!("{p} is not a controls preset (format must be {})", py_str(FORMAT))));
    }
    let v = doc.get("version").unwrap_or(&Value::Null);
    // Python compares with ==, where 1.0 and True equal 1
    if !(v.as_f64() == Some(VERSION as f64) || v.as_bool() == Some(true)) {
        return Err(err(format!("{p}: preset version {}; this SDK reads version {VERSION}", py_json(v))));
    }
    let controls_ok = doc
        .get("controls")
        .and_then(Value::as_object)
        .is_some_and(|c| c.values().all(|e| e.get("value").is_some()));
    if !doc.get("device").is_some_and(Value::is_object) || !controls_ok {
        return Err(err(format!(
            "{p}: needs 'device' and 'controls' as {{name: {{'value': ..., 'units': ...}}}}"
        )));
    }
    Ok(doc)
}

/// The presets in `store_dir()`, by name (Python `presets.list_presets`). With `model`, only those saved from that
/// model (`Controls::model()`). Files that are not presets are left out.
pub fn list_presets(model: Option<&str>) -> Result<Vec<PresetInfo>> {
    Ok(list_in(&store_dir()?, model))
}

fn list_in(dir: &Path, model: Option<&str>) -> Vec<PresetInfo> {
    // Python's glob `*.json` and its sort of paths ignore case on Windows
    let fold = |s: &str| if cfg!(windows) { s.to_lowercase() } else { s.to_string() };
    let mut files: Vec<PathBuf> =
        std::fs::read_dir(dir).map(|rd| rd.flatten().map(|e| e.path()).collect()).unwrap_or_default();
    files.retain(|f| f.file_name().is_some_and(|n| fold(&n.to_string_lossy()).ends_with(".json")));
    files.sort_by_key(|f| fold(&f.to_string_lossy()));
    let mut out = Vec::new();
    for f in files {
        let Ok(doc) = read_preset(&f) else {
            continue; // Python logs a warning and skips it
        };
        let m = doc["device"].get("model").and_then(Value::as_str).map(String::from);
        if model.is_none() || m.as_deref() == model {
            out.push(PresetInfo {
                name: f.file_stem().map(|s| s.to_string_lossy().into_owned()).unwrap_or_default(),
                model: m,
                saved_at: text(doc.get("saved_at")),
                note: text(doc.get("note").filter(|v| !v.is_null())),
                path: f,
            });
        }
    }
    out
}

/// Python's `str()` of a JSON value: a string as it is, anything else as its repr; nothing is "".
fn text(v: Option<&Value>) -> String {
    match v {
        None => String::new(),
        Some(Value::String(s)) => s.clone(),
        Some(v) => py_json(v),
    }
}

/// `json.dumps(indent=2)` of an object whose values are JSON text already indented for `depth`, with the keys in
/// the order given (serde_json's map would sort them).
fn obj(items: &[(&str, String)], depth: usize) -> String {
    if items.is_empty() {
        return "{}".into();
    }
    let pad = "  ".repeat(depth + 1);
    let body: Vec<String> = items.iter().map(|(k, v)| format!("{pad}{}: {v}", Value::from(*k))).collect();
    format!("{{\n{}\n{}}}", body.join(",\n"), "  ".repeat(depth))
}

fn user_json(v: &UserValue) -> Value {
    match v {
        UserValue::Int(i) => Value::from(*i),
        UserValue::Bool(b) => Value::from(*b),
        UserValue::Float(f) => Value::from(*f),
        UserValue::Choice(s) => Value::from(s.as_str()),
    }
}

/// Python `presets._check`: the preset's value for one control as `set()` takes it, or why it does not fit.
fn check(i: &ControlInfo, value: &Value, units: Option<&Value>) -> Result<UserValue> {
    let n = i.name;
    if let Some(u) = units.filter(|u| !u.is_null() && u.as_str() != Some(i.units.as_str())) {
        return Err(err(format!(
            "{n}: the preset is in {}, this camera reports {} (the preset was saved on another platform or backend). \
             Save the preset again on this computer, or delete {} from the file — a control left out of a preset is \
             not touched. force does not override this. Nothing was written.",
            py_json(u),
            py_str(&i.units),
            py_str(n)
        )));
    }
    let (got, want) = match i.kind {
        Kind::Int => {
            let range = i.minimum.zip(i.maximum);
            let v = value.as_i64().filter(|v| range.is_none_or(|(lo, hi)| lo <= *v && *v <= hi));
            let within = range.map(|(lo, hi)| format!(" within {lo}..{hi}")).unwrap_or_default();
            (v.map(UserValue::Int), format!("a whole number{within}"))
        }
        Kind::Bool => (value.as_bool().map(UserValue::Bool), "true or false".into()),
        Kind::Menu => {
            let choices = i.choices.as_deref().unwrap_or(&[]);
            let v = value.as_str().filter(|s| choices.contains(s));
            (v.map(|s| UserValue::Choice(s.into())), format!("one of {}", tuple_repr(choices)))
        }
    };
    got.ok_or_else(|| err(format!("{n} must be {want} on this camera: {}. Nothing was written.", py_json(value))))
}

/// Seconds since 1970 (and milliseconds) as ISO 8601 in UTC. The Python package writes local time with its offset
/// in recordings (UTC in presets); the standard library has no time zones.
pub(crate) fn iso_utc(secs: u64, millis: Option<u32>) -> String {
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

pub(crate) fn now(millis: bool) -> String {
    let d = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default();
    iso_utc(d.as_secs(), millis.then(|| d.subsec_millis()))
}

impl Controls {
    /// Writes every readable control (value and units, auto/manual modes included), the model and the crate
    /// version to a JSON file in the Python package's layout (Python `Controls.save_preset`). Reads only.
    /// `name` defaults to the file name without `.json`. Returns `path`.
    pub fn save_preset(&self, path: &Path, name: Option<&str>, note: Option<&str>) -> Result<PathBuf> {
        let js = |v: Value| v.to_string();
        let controls: Vec<(&str, String)> = self
            .describe_all()? // reads only
            .into_iter()
            .filter_map(|i| {
                let v = user_json(i.current.as_ref()?);
                Some((i.name, obj(&[("value", js(v)), ("units", js(i.units.into()))], 2)))
            })
            .collect();
        let model = self.model();
        let product = if model == Some(WEEVIA_S1_NAME) { "Weevia S1".into() } else { Value::Null };
        let device = [("model", js(model.into())), ("product", js(product)), ("backend", js(self.backend_name().into()))];
        let stem = path.file_stem().map(|s| s.to_string_lossy().into_owned()).unwrap_or_default();
        let doc = obj(
            &[
                ("format", js(FORMAT.into())),
                ("version", js(VERSION.into())),
                ("name", js(name.unwrap_or(&stem).into())),
                ("note", js(note.unwrap_or("").into())),
                ("device", obj(&device, 1)),
                ("sdk_version", js(env!("CARGO_PKG_VERSION").into())),
                ("saved_at", js(now(false).into())),
                ("controls", obj(&controls, 1)),
            ],
            0,
        );
        let fail = |e: std::io::Error| err(format!("{}: {e}", path.display())); // Python: OSError
        if let Some(dir) = path.parent().filter(|d| !d.as_os_str().is_empty()) {
            std::fs::create_dir_all(dir).map_err(fail)?;
        }
        std::fs::write(path, doc + "\n").map_err(fail)?;
        Ok(path.to_path_buf())
    }

    /// What loading the preset would change on this camera, and what it would leave alone (Python
    /// `Controls.plan_preset`, and the lists of its `PresetLoad`). Checks the whole preset first; writes nothing.
    /// `SdkError::PresetModel` when the preset was saved from another model or this camera's model is unknown
    /// (`force` accepts it), `SdkError::Preset` for a file that is not a preset, a control this camera does not
    /// have, other units or a value out of the camera's range. Controls with no effect on the mono sensor are in
    /// `skipped_mono` unless `include_mono`.
    pub fn plan_preset(&self, path: &Path, force: bool, include_mono: bool) -> Result<PresetPlan> {
        let doc = read_preset(path)?;
        let saved = doc["device"].get("model").and_then(Value::as_str);
        if !force && (saved.is_none() || saved != self.model()) {
            let here = self.model().map(String::from).unwrap_or_else(|| {
                format!("a camera whose model the {} backend cannot tell", self.backend_name())
            });
            return Err(SdkError::PresetModel(format!(
                "The preset was saved from {}; this is {here}. Nothing was written. Pass force=True (command line: \
                 --force) to load it anyway.",
                saved.filter(|s| !s.is_empty()).unwrap_or("an unknown model")
            )));
        }
        let infos = self.describe_all()?;
        let ctl = doc["controls"].as_object().expect("checked by read_preset");
        // serde_json's map keeps its keys sorted, as Python's sorted() does here
        let unknown: Vec<&str> =
            ctl.keys().map(String::as_str).filter(|n| !infos.iter().any(|i| i.name == *n)).collect();
        if !unknown.is_empty() {
            return Err(err(format!("Not controls of this camera: {}. Nothing was written.", py_list_str(&unknown))));
        }
        // ponytail: the camera's control order, not the file's (serde_json without preserve_order sorts keys). The
        // same for files either SDK writes (table order); with two bad values in a hand-edited file, Python may name
        // the other one first. serde_json's `preserve_order` feature would give the file's order (adds indexmap).
        let mut values = Vec::new();
        for i in &infos {
            if let Some(e) = ctl.get(i.name) {
                values.push((i, check(i, &e["value"], e.get("units"))?));
            }
        }
        let preset = |n: &str| values.iter().find(|(i, _)| i.name == n).map(|(_, v)| v);
        let mut plan = PresetPlan { changes: vec![], unchanged: vec![], left_out: vec![], skipped_mono: vec![] };
        for (i, v) in &values {
            let n = i.name.to_string();
            let mode = spec::auto_name(i.group).filter(|a| *a != i.name).and_then(preset);
            if mode.is_some_and(|m| *m == UserValue::Choice("auto".into()) || *m == UserValue::Bool(true)) {
                plan.left_out.push(n);
            } else if i.current.as_ref().is_some_and(|c| py_eq(c, v)) {
                plan.unchanged.push(n);
            } else if !include_mono && spec::spec(i.name).is_some_and(|s| s.mono) {
                plan.skipped_mono.push(n);
            } else {
                plan.changes.push((n, i.current.clone(), v.clone()));
            }
        }
        Ok(plan)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::backend::mock::MockBackend;
    use serde_json::json;
    use UserValue::{Bool, Choice, Int};

    fn tmp(tag: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("ov_presets_{tag}_{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    fn cam() -> (Controls, MockBackend) {
        let be = MockBackend::new();
        (Controls::new(Box::new(be.clone()), false, "", None).unwrap(), be)
    }

    fn edit(p: &Path, f: impl FnOnce(&mut Value)) {
        let mut doc: Value = serde_json::from_str(&std::fs::read_to_string(p).unwrap()).unwrap();
        f(&mut doc);
        std::fs::write(p, doc.to_string()).unwrap();
    }

    fn names(v: &[&str]) -> Vec<String> {
        v.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn save_writes_the_python_layout_in_table_order_and_reads_only() {
        let (c, be) = cam();
        let p = tmp("save").join("sub").join("a.json");
        assert_eq!(c.save_preset(&p, Some("bench"), Some("lab")).unwrap(), p);
        let text = std::fs::read_to_string(&p).unwrap();
        let doc: Value = serde_json::from_str(&text).unwrap();
        // json.dumps(doc, indent=2, ensure_ascii=False) + "\n", keys in Python's order
        let head = "{\n  \"format\": \"opticmix-controls-preset\",\n  \"version\": 1,\n  \"name\": \"bench\",\n  \
                    \"note\": \"lab\",\n  \"device\": {\n    \"model\": \"synthetic\",\n    \"product\": null,\n    \
                    \"backend\": \"sim\"\n  },\n  \"sdk_version\": \"";
        assert!(text.starts_with(head), "{text}");
        assert!(text.ends_with("      \"units\": \"raw\"\n    }\n  }\n}\n"), "{text}");
        assert_eq!(doc["sdk_version"], env!("CARGO_PKG_VERSION"));
        assert!(doc["saved_at"].as_str().unwrap().ends_with("+00:00"));
        let order: Vec<usize> = c
            .names()
            .unwrap()
            .iter()
            .map(|n| text.find(&format!("\n    \"{n}\": {{")).unwrap())
            .collect();
        assert!(order.windows(2).all(|w| w[0] < w[1]), "table order");
        assert_eq!(doc["controls"].as_object().unwrap().len(), 14);
        assert_eq!(doc["controls"]["exposure"], json!({"value": -5, "units": "log2_s_nominal"}));
        assert_eq!(doc["controls"]["auto_exposure"]["value"], "manual");
        assert_eq!(doc["controls"]["auto_white_balance"]["value"], false);
        assert!(be.writes().is_empty(), "saving reads only");
        // name defaults to the file name; no note is ""
        c.save_preset(&p, None, None).unwrap();
        let doc = read_preset(&p).unwrap();
        assert_eq!((doc["name"].as_str(), doc["note"].as_str()), (Some("a"), Some("")));
    }

    #[test]
    fn a_preset_just_saved_changes_nothing() {
        let (c, be) = cam();
        let p = tmp("same").join("a.json");
        c.save_preset(&p, None, None).unwrap();
        let plan = c.plan_preset(&p, false, false).unwrap();
        assert_eq!(plan.changes, vec![]);
        assert_eq!(plan.unchanged, c.names().unwrap().iter().map(|s| s.to_string()).collect::<Vec<_>>());
        assert!(plan.left_out.is_empty() && plan.skipped_mono.is_empty());
        assert!(be.writes().is_empty());
    }

    #[test]
    fn the_plan_lists_what_would_change_and_writes_nothing() {
        let (c, be) = cam();
        let p = tmp("plan").join("a.json");
        c.save_preset(&p, None, None).unwrap();
        be.st().values.insert("gain".into(), 100);
        let plan = c.plan_preset(&p, false, false).unwrap();
        assert_eq!(plan.changes, vec![("gain".to_string(), Some(Int(100)), Int(32))]);
        assert_eq!(plan.unchanged.len(), 13);
        assert!(be.writes().is_empty());
    }

    #[test]
    fn a_preset_from_another_model_is_refused_unless_forced() {
        let (c, _) = cam();
        let p = tmp("model").join("a.json");
        c.save_preset(&p, None, None).unwrap();
        edit(&p, |d| d["device"]["model"] = json!("WN Dual Camera"));
        assert_eq!(
            c.plan_preset(&p, false, false).unwrap_err(),
            SdkError::PresetModel(
                "The preset was saved from WN Dual Camera; this is synthetic. Nothing was written. Pass force=True \
                 (command line: --force) to load it anyway."
                    .into()
            )
        );
        assert!(c.plan_preset(&p, true, false).unwrap().changes.is_empty());
        edit(&p, |d| d["device"].as_object_mut().unwrap().remove("model").map(|_| ()).unwrap());
        let e = c.plan_preset(&p, false, false).unwrap_err().to_string();
        assert!(e.starts_with("The preset was saved from an unknown model; this is synthetic."), "{e}");
    }

    #[test]
    fn a_camera_whose_model_is_unknown_needs_force() {
        let be = MockBackend::new();
        be.st().no_model = true; // like the OpenCV fallback, which cannot tell what camera it is
        let c = Controls::new(Box::new(be.clone()), false, "", None).unwrap();
        assert_eq!(c.model(), None);
        let p = tmp("nomodel").join("a.json");
        c.save_preset(&p, None, None).unwrap();
        assert_eq!(read_preset(&p).unwrap()["device"]["model"], Value::Null);
        let e = c.plan_preset(&p, false, false).unwrap_err();
        assert_eq!(
            e.to_string(),
            "The preset was saved from an unknown model; this is a camera whose model the sim backend cannot tell. \
             Nothing was written. Pass force=True (command line: --force) to load it anyway."
        );
        assert_eq!(e.python_type(), "PresetModelError");
        assert!(c.plan_preset(&p, true, false).is_ok());
    }

    #[test]
    fn the_model_is_the_backends() {
        assert_eq!(cam().0.model(), Some("synthetic"));
        #[cfg(windows)]
        {
            // DirectShow refuses a device not named so; creating the backend opens nothing
            let be = crate::dshow::DShowBackend::new(0, &spec::EXPECTED_NAMES).unwrap();
            assert_eq!(crate::backend::Backend::model(&be), Some(WEEVIA_S1_NAME));
        }
    }

    #[test]
    fn a_bad_value_anywhere_stops_the_plan_with_the_python_message() {
        type Edit = fn(&mut Value);
        let cases: [(Edit, &str); 8] = [
            (|d| d["controls"]["gain"]["value"] = json!(999),
             "gain must be a whole number within 0..255 on this camera: 999. Nothing was written."),
            (|d| d["controls"]["gain"]["value"] = json!(true),
             "gain must be a whole number within 0..255 on this camera: True. Nothing was written."),
            (|d| d["controls"]["gain"]["value"] = json!(28.5),
             "gain must be a whole number within 0..255 on this camera: 28.5. Nothing was written."),
            (|d| d["controls"]["power_line_frequency"]["value"] = json!("70hz"),
             "power_line_frequency must be one of ('disabled', '50hz', '60hz') on this camera: '70hz'. Nothing was \
              written."),
            (|d| d["controls"]["auto_white_balance"]["value"] = json!(1),
             "auto_white_balance must be true or false on this camera: 1. Nothing was written."),
            (|d| d["controls"]["auto_exposure"]["value"] = json!(null),
             "auto_exposure must be one of ('manual', 'auto') on this camera: None. Nothing was written."),
            (|d| {
                d["controls"]["focus"] = json!({"value": 3});
                d["controls"]["iris"] = json!({"value": 1});
            },
             "Not controls of this camera: ['focus', 'iris']. Nothing was written."),
            (|d| d["controls"]["exposure"]["units"] = json!("100us"),
             "exposure: the preset is in '100us', this camera reports 'log2_s_nominal' (the preset was saved on \
              another platform or backend). Save the preset again on this computer, or delete 'exposure' from the \
              file — a control left out of a preset is not touched. force does not override this. Nothing was \
              written."),
        ];
        let (c, be) = cam();
        let p = tmp("bad").join("a.json");
        for (f, want) in cases {
            c.save_preset(&p, None, None).unwrap();
            edit(&p, |d| d["controls"]["brightness"]["value"] = json!(80)); // a valid change that is not enough
            edit(&p, f);
            for force in [false, true] {
                let e = c.plan_preset(&p, force, false).unwrap_err();
                assert_eq!(e, SdkError::Preset(want.into()));
                assert_eq!(e.python_type(), "PresetError");
            }
        }
        assert!(be.writes().is_empty());
    }

    #[test]
    fn an_exposure_value_is_left_out_when_the_preset_puts_exposure_in_auto() {
        let (c, _) = cam();
        let p = tmp("auto").join("a.json");
        c.save_preset(&p, None, None).unwrap();
        edit(&p, |d| {
            d["controls"]["auto_exposure"]["value"] = json!("auto");
            d["controls"]["exposure"]["value"] = json!(-8);
        });
        let plan = c.plan_preset(&p, false, false).unwrap();
        assert_eq!(plan.left_out, names(&["exposure"]));
        assert_eq!(
            plan.changes,
            vec![("auto_exposure".to_string(), Some(Choice("manual".into())), Choice("auto".into()))]
        );
        assert!(!plan.unchanged.contains(&"exposure".to_string()));
    }

    #[test]
    fn mono_only_controls_are_left_out_unless_asked() {
        let (c, _) = cam();
        let p = tmp("mono").join("a.json");
        c.save_preset(&p, None, None).unwrap();
        edit(&p, |d| {
            d["controls"]["hue"]["value"] = json!(10);
            d["controls"]["auto_white_balance"]["value"] = json!(true);
        });
        let plan = c.plan_preset(&p, false, false).unwrap();
        assert!(plan.changes.is_empty());
        assert_eq!(plan.skipped_mono, names(&["hue", "auto_white_balance"]));
        assert!(!plan.unchanged.contains(&"hue".to_string()));
        let plan = c.plan_preset(&p, false, true).unwrap();
        assert_eq!(
            plan.changes,
            vec![
                ("hue".to_string(), Some(Int(64)), Int(10)),
                ("auto_white_balance".to_string(), Some(Bool(false)), Bool(true)),
            ]
        );
        // a white balance value whose mode the preset puts in auto is left out, not skipped
        assert!(plan.skipped_mono.is_empty() && plan.left_out == names(&["white_balance"]));
    }

    #[test]
    fn a_control_left_out_of_the_file_is_not_touched() {
        let (c, be) = cam();
        let p = tmp("partial").join("a.json");
        std::fs::write(&p, json!({"format": FORMAT, "version": 1, "device": {"model": "synthetic"},
                                  "controls": {"gain": {"value": 40}}}).to_string()).unwrap();
        be.st().values.insert("brightness".into(), 99);
        let plan = c.plan_preset(&p, false, false).unwrap();
        assert_eq!(plan.changes, vec![("gain".to_string(), Some(Int(32)), Int(40))]);
        assert!(plan.unchanged.is_empty());
    }

    #[test]
    fn read_preset_refuses_what_is_not_a_preset() {
        let d = tmp("read");
        let p = d.join("x.json");
        let at = p.display().to_string();
        for (text, want) in [
            ("{\"format\": \"other\", \"version\": 1}",
             format!("{at} is not a controls preset (format must be 'opticmix-controls-preset')")),
            ("[1]", format!("{at} is not a controls preset (format must be 'opticmix-controls-preset')")),
            ("{\"format\": \"opticmix-controls-preset\", \"version\": 2, \"device\": {}, \"controls\": {}}",
             format!("{at}: preset version 2; this SDK reads version 1")),
            ("{\"format\": \"opticmix-controls-preset\", \"device\": {}, \"controls\": {}}",
             format!("{at}: preset version None; this SDK reads version 1")),
            ("{\"format\": \"opticmix-controls-preset\", \"version\": 1, \"device\": {}, \"controls\": {\"gain\": 3}}",
             format!("{at}: needs 'device' and 'controls' as {{name: {{'value': ..., 'units': ...}}}}")),
            ("{\"format\": \"opticmix-controls-preset\", \"version\": 1, \"controls\": {}}",
             format!("{at}: needs 'device' and 'controls' as {{name: {{'value': ..., 'units': ...}}}}")),
        ] {
            std::fs::write(&p, text).unwrap();
            assert_eq!(read_preset(&p).unwrap_err(), SdkError::Preset(want), "{text}");
        }
        // bad JSON: Python's prefix; the parser's own words follow (Python's json module words them differently)
        std::fs::write(&p, "not json").unwrap();
        let e = read_preset(&p).unwrap_err().to_string();
        assert!(e.starts_with(&format!("{at} is not a preset: ")), "{e}");
        // version 1.0 == 1 in Python
        std::fs::write(&p, "{\"format\": \"opticmix-controls-preset\", \"version\": 1.0, \"device\": {}, \"controls\": {}}").unwrap();
        assert!(read_preset(&p).is_ok());
        let missing = d.join("missing.json");
        assert!(matches!(read_preset(&missing), Err(SdkError::Preset(m)) if m.starts_with(&missing.display().to_string())));
    }

    #[test]
    fn the_folder_sits_beside_the_calibration_folder() {
        let (Ok(p), Ok(c)) = (store_dir(), crate::calib::store_dir()) else {
            return; // APPDATA / HOME not set
        };
        assert_eq!(p, c.parent().unwrap().join("presets"));
        assert_eq!(preset_path("bench").unwrap(), p.join("bench.json"));
    }

    #[test]
    fn preset_path_takes_a_name_not_a_path() {
        for bad in ["", ".", "..", "a/b", "../x", "a/"] {
            assert_eq!(
                preset_path(bad).unwrap_err(),
                SdkError::Preset(format!("A preset name is a file name, not a path: {}", py_str(bad))),
            );
        }
    }

    #[test]
    fn list_by_model_skips_files_that_are_not_presets() {
        let (c, _) = cam();
        let d = tmp("list");
        c.save_preset(&d.join("b.json"), None, Some("second")).unwrap();
        c.save_preset(&d.join("a.json"), None, None).unwrap();
        edit(&d.join("b.json"), |v| v["device"]["model"] = json!("WN Dual Camera"));
        std::fs::write(d.join("junk.json"), "{}").unwrap();
        std::fs::write(d.join("notes.txt"), "x").unwrap();
        let got: Vec<(String, Option<String>, String)> =
            list_in(&d, None).into_iter().map(|i| (i.name, i.model, i.note)).collect();
        assert_eq!(
            got,
            vec![
                ("a".into(), Some("synthetic".into()), String::new()),
                ("b".into(), Some(WEEVIA_S1_NAME.into()), "second".into()),
            ]
        );
        let a = &list_in(&d, Some("synthetic"))[0];
        assert_eq!((a.name.as_str(), a.path.clone()), ("a", d.join("a.json")));
        assert!(a.saved_at.ends_with("+00:00"));
        assert!(list_in(&d.join("none"), None).is_empty());
    }
}
