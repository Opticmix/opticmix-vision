//! 캘리브레이션 파일(JSON, `opticmix_vision.stereo_calibration` schema_version 1) — 눈별 KB4 내부 + 좌→우 외부.
//! Python `calibration.py` 의 검증을 같은 순서·같은 문안으로 옮긴다(모르는 키는 에러, NaN/inf 는 쓰지도 읽지도 않음,
//! 화각 안에서 접히는 KB4 거부, 회전행렬 아닌 R·베이스라인 0 거부). 외부 규약: p_right = R·p_left + t, 단위 mm.

use serde_json::Value;

use crate::error::{GeomError, Result};
use crate::kb4::kb4_is_injective;
use crate::pyrepr::{py_fixed, py_float, py_g, py_json, py_list_str, py_str, py_tuple};

pub const SCHEMA_NAME: &str = "opticmix_vision.stereo_calibration";
pub const SCHEMA_VERSION: i64 = 1;
/// Half of the 185° (vendor spec) lens field; the field of view has not been measured. Same value as Python.
pub const DEFAULT_MAX_THETA_DEG: f64 = 92.5;
pub const CONVENTION: &str = "p_right = R @ p_left + t";

const TOP_KEYS: [&str; 8] = [
    "schema",
    "schema_version",
    "units",
    "device",
    "left",
    "right",
    "extrinsics",
    "meta",
];
const INTR_KEYS: [&str; 4] = ["model", "K", "D", "size"];
const EXT_KEYS: [&str; 3] = ["R", "t", "convention"];

fn cal(m: impl Into<String>) -> GeomError {
    GeomError::Calibration(m.into())
}

/// 한 눈의 KB4 내부 파라미터.
#[derive(Debug, Clone, PartialEq)]
pub struct Intrinsics {
    pub k: [[f64; 3]; 3],
    pub d: [f64; 4],
    pub size: (i64, i64),
    pub model: String,
}

/// 왼쪽 → 오른쪽 변환(p_right = R·p_left + t, t 는 mm).
#[derive(Debug, Clone, PartialEq)]
pub struct Extrinsics {
    pub r: [[f64; 3]; 3],
    pub t: [f64; 3],
}

#[derive(Debug, Clone, PartialEq)]
pub struct StereoCalibration {
    pub left: Intrinsics,
    pub right: Intrinsics,
    pub extrinsics: Extrinsics,
    /// 문자열 → 문자열(파일 순서).
    pub device: Vec<(String, String)>,
    pub meta: Value,
    pub units: String,
}

impl StereoCalibration {
    pub fn baseline_mm(&self) -> f64 {
        self.extrinsics.baseline_mm()
    }
}

/// `CameraIntrinsics.__post_init__` 의 모양 검사(K 는 그대로, D 는 numpy `reshape(-1)` 뒤 길이).
pub fn check_intrinsics_shapes(k_shape: &[usize], d_len: usize) -> Result<()> {
    if k_shape != [3, 3] {
        return Err(cal(format!("K must be (3,3): {}", py_tuple(k_shape))));
    }
    if d_len != 4 {
        return Err(cal(format!(
            "D must have the 4 KB4 coefficients (k1..k4): {}",
            py_tuple(&[d_len])
        )));
    }
    Ok(())
}

/// `CameraIntrinsics.validate(max_theta_deg)` — 문제를 Python 과 같은 순서로 모은다(없으면 빈 목록).
pub fn intrinsics_problems(i: &Intrinsics, max_theta_deg: f64) -> Vec<String> {
    let mut errs = Vec::new();
    if i.model != "KB4" {
        errs.push(format!("model {}: only KB4 is supported", py_str(&i.model)));
    }
    if !i.k.iter().flatten().all(|v| v.is_finite()) {
        errs.push("K has NaN/inf".into());
    } else if i.k[0][0] <= 0.0 || i.k[1][1] <= 0.0 {
        errs.push(format!(
            "fx/fy must be positive: fx={}, fy={}",
            py_float(i.k[0][0]),
            py_float(i.k[1][1])
        ));
    }
    if !i.d.iter().all(|v| v.is_finite()) {
        errs.push("D has NaN/inf".into());
    } else if let Some(fold) = kb4_is_injective(&i.d, max_theta_deg.to_radians(), 1024) {
        errs.push(format!(
            "KB4 coefficients fold inside the field of view (not monotonic): at about {}° (checked up to {}°)",
            py_fixed(fold.to_degrees(), 2),
            py_g(max_theta_deg)
        ));
    }
    if i.size.0 <= 0 || i.size.1 <= 0 {
        errs.push(format!("size must be positive: ({}, {})", i.size.0, i.size.1));
    }
    errs
}

impl Intrinsics {
    /// 모양 검사 → 값 검사(기본 상한 92.5°). 문제가 있으면 "CameraIntrinsics: a; b".
    pub fn new(
        k_shape: &[usize],
        k: &[f64],
        d: &[f64],
        size: (i64, i64),
        model: &str,
    ) -> Result<Intrinsics> {
        check_intrinsics_shapes(k_shape, d.len())?;
        let i = Intrinsics {
            k: [[k[0], k[1], k[2]], [k[3], k[4], k[5]], [k[6], k[7], k[8]]],
            d: [d[0], d[1], d[2], d[3]],
            size,
            model: model.to_string(),
        };
        let p = intrinsics_problems(&i, DEFAULT_MAX_THETA_DEG);
        if p.is_empty() {
            Ok(i)
        } else {
            Err(cal(format!("CameraIntrinsics: {}", p.join("; "))))
        }
    }

    pub fn fx(&self) -> f64 {
        self.k[0][0]
    }
    pub fn fy(&self) -> f64 {
        self.k[1][1]
    }
    pub fn cx(&self) -> f64 {
        self.k[0][2]
    }
    pub fn cy(&self) -> f64 {
        self.k[1][2]
    }
}

fn matmul_t(r: &[[f64; 3]; 3]) -> [[f64; 3]; 3] {
    let mut m = [[0.0; 3]; 3];
    for (i, row) in m.iter_mut().enumerate() {
        for (j, v) in row.iter_mut().enumerate() {
            *v = (0..3).map(|k| r[k][i] * r[k][j]).sum();
        }
    }
    m
}

/// `np.allclose(M, I, atol=1e-6)`(rtol 기본 1e-5 포함): |a-b| ≤ 1e-6 + 1e-5·|b|.
pub(crate) fn allclose_eye(m: &[[f64; 3]; 3]) -> bool {
    (0..3).all(|i| {
        (0..3).all(|j| {
            let b = if i == j { 1.0 } else { 0.0 };
            (m[i][j] - b).abs() <= 1e-6 + 1e-5 * f64::abs(b)
        })
    })
}

pub(crate) fn det3(r: &[[f64; 3]; 3]) -> f64 {
    r[0][0] * (r[1][1] * r[2][2] - r[1][2] * r[2][1])
        - r[0][1] * (r[1][0] * r[2][2] - r[1][2] * r[2][0])
        + r[0][2] * (r[1][0] * r[2][1] - r[1][1] * r[2][0])
}

impl Extrinsics {
    /// `StereoExtrinsics.__post_init__` 와 같은 순서: 모양 → NaN/inf → 회전행렬 → 베이스라인.
    pub fn new(r_shape: &[usize], r: &[f64], t: &[f64]) -> Result<Extrinsics> {
        if r_shape != [3, 3] {
            return Err(cal(format!("R must be (3,3): {}", py_tuple(r_shape))));
        }
        if t.len() != 3 {
            return Err(cal(format!("t must be (3,): {}", py_tuple(&[t.len()]))));
        }
        if !r.iter().all(|v| v.is_finite()) {
            return Err(cal("R: has NaN/inf"));
        }
        if !t.iter().all(|v| v.is_finite()) {
            return Err(cal("t: has NaN/inf"));
        }
        let rm = [[r[0], r[1], r[2]], [r[3], r[4], r[5]], [r[6], r[7], r[8]]];
        let det = det3(&rm);
        // math.isclose(det, 1.0, abs_tol=1e-6): |a-b| ≤ max(1e-9·max(|a|,|b|), 1e-6)
        let det_ok = (det - 1.0).abs() <= (1e-9 * det.abs().max(1.0)).max(1e-6);
        if !allclose_eye(&matmul_t(&rm)) || !det_ok {
            return Err(cal("R is not a rotation matrix (RᵀR≠I or det≠+1)"));
        }
        let e = Extrinsics {
            r: rm,
            t: [t[0], t[1], t[2]],
        };
        if e.baseline_mm() <= 0.0 {
            return Err(cal("t is the zero vector (baseline 0)"));
        }
        Ok(e)
    }

    pub fn baseline_mm(&self) -> f64 {
        (self.t[0] * self.t[0] + self.t[1] * self.t[1] + self.t[2] * self.t[2]).sqrt()
    }
}

// ---- JSON 읽기 ---------------------------------------------------------------------------------

/// 비표준 숫자 토큰을 문자열 밖에서 찾는다 → (위치, 토큰).
fn nonstandard_tokens(text: &str) -> Vec<(usize, &'static str)> {
    let b = text.as_bytes();
    let mut out = Vec::new();
    let (mut i, mut in_str) = (0, false);
    while i < b.len() {
        let c = b[i];
        if in_str {
            if c == b'\\' {
                i += 2;
                continue;
            }
            if c == b'"' {
                in_str = false;
            }
            i += 1;
            continue;
        }
        if c == b'"' {
            in_str = true;
            i += 1;
            continue;
        }
        let rest = &text[i..];
        if let Some(tok) = ["-Infinity", "Infinity", "NaN"]
            .into_iter()
            .find(|t| rest.starts_with(t))
        {
            out.push((i, tok));
            i += tok.len();
            continue;
        }
        i += 1;
    }
    out
}

const NAN_MARK: &str = "\u{0}opticmix-nonstandard:";

/// JSON 텍스트 읽기. `allow_nan=false`(파일 읽기) 는 Python `parse_constant` 처럼 첫 비표준 숫자에서 거부,
/// `allow_nan=true`(dict 를 다시 직렬화한 것) 는 NaN·±Infinity 를 값으로 들인다 — 그러면 K·R 검사가 잡는다.
fn load_json(text: &str, allow_nan: bool) -> Result<Value> {
    let toks = nonstandard_tokens(text);
    if let (false, Some((_, tok))) = (allow_nan, toks.first()) {
        return Err(cal(format!(
            "non-standard number in JSON: {} (NaN/Infinity are not allowed)",
            py_str(tok)
        )));
    }
    let mut owned = String::with_capacity(text.len());
    let mut last = 0;
    for (pos, tok) in &toks {
        owned.push_str(&text[last..*pos]);
        // JSON 이스케이프 \u0000 — 읽으면 NAN_MARK 로 시작하는 문자열이 된다
        owned.push_str(&format!("\"\\u0000opticmix-nonstandard:{tok}\""));
        last = pos + tok.len();
    }
    owned.push_str(&text[last..]);
    serde_json::from_str(&owned).map_err(|e| cal(format!("cannot read JSON: {e}")))
}

fn as_f64(v: &Value, at: &str) -> Result<f64> {
    match v {
        Value::Number(n) => n
            .as_f64()
            .ok_or_else(|| cal(format!("{at}: not a number: {}", py_json(v)))),
        Value::Bool(b) => Ok(if *b { 1.0 } else { 0.0 }), // numpy 는 bool 을 0/1 로 받는다
        Value::String(s) if s.starts_with(NAN_MARK) => Ok(match &s[NAN_MARK.len()..] {
            "NaN" => f64::NAN,
            "Infinity" => f64::INFINITY,
            _ => f64::NEG_INFINITY,
        }),
        _ => Err(cal(format!("{at}: not a number: {}", py_json(v)))),
    }
}

/// numpy `np.asarray(v)` 의 모양과 값(행 우선). 고르지 않은 중첩은 거부.
fn ndarray(v: &Value, at: &str) -> Result<(Vec<usize>, Vec<f64>)> {
    match v {
        Value::Array(items) => {
            let mut shape: Option<Vec<usize>> = None;
            let mut flat = Vec::new();
            for it in items {
                let (s, f) = ndarray(it, at)?;
                if shape.as_ref().is_some_and(|x| *x != s) {
                    return Err(cal(format!("{at}: ragged array")));
                }
                shape = Some(s);
                flat.extend(f);
            }
            let mut full = vec![items.len()];
            full.extend(shape.unwrap_or_default());
            Ok((full, flat))
        }
        _ => Ok((Vec::new(), vec![as_f64(v, at)?])),
    }
}

fn reject_unknown(v: &Value, allowed: &[&str], at: &str) -> Result<()> {
    let obj = v
        .as_object()
        .ok_or_else(|| cal(format!("{at}: not an object: {}", py_json(v))))?;
    let mut unknown: Vec<&str> = obj
        .keys()
        .map(String::as_str)
        .filter(|k| !allowed.contains(k))
        .collect();
    unknown.sort_unstable();
    if unknown.is_empty() {
        Ok(())
    } else {
        Err(cal(format!(
            "{at}: unknown key {} (allowed: {})",
            py_list_str(&unknown),
            py_list_str(allowed)
        )))
    }
}

fn require<'a>(v: &'a Value, key: &str, at: &str) -> Result<&'a Value> {
    v.get(key)
        .ok_or_else(|| cal(format!("{at}: missing required key {}", py_str(key))))
}

/// Python `int(x)`: 실수는 0 쪽으로 자르고, 문자열은 정수로 읽는다.
fn py_int(v: &Value) -> Option<i64> {
    match v {
        Value::Number(n) => n.as_i64().or_else(|| {
            n.as_f64()
                .filter(|f| f.is_finite())
                .map(|f| f.trunc() as i64)
        }),
        Value::Bool(b) => Some(*b as i64),
        Value::String(s) => s.trim().parse().ok(),
        _ => None,
    }
}

fn parse_size(v: &Value) -> Result<(i64, i64)> {
    let items = match v {
        Value::Array(a) => a.clone(),
        other => vec![other.clone()],
    };
    let got = (
        items.first().and_then(py_int),
        items.get(1).and_then(py_int),
    );
    match got {
        (Some(w), Some(h)) => Ok((w, h)),
        _ => {
            let tuple = match items.len() {
                1 => format!("({},)", py_json(&items[0])),
                _ => format!(
                    "({})",
                    items.iter().map(py_json).collect::<Vec<_>>().join(", ")
                ),
            };
            Err(cal(format!("size must be [width, height]: {tuple}")))
        }
    }
}

fn intrinsics_from(v: &Value, at: &str) -> Result<Intrinsics> {
    reject_unknown(v, &INTR_KEYS, at)?;
    let (k_shape, k) = ndarray(require(v, "K", at)?, at)?;
    let (_, d) = ndarray(require(v, "D", at)?, at)?; // reshape(-1)
    let size_v = require(v, "size", at)?;
    let model = match require(v, "model", at)? {
        Value::String(s) => s.clone(),
        other => py_json(other),
    };
    check_intrinsics_shapes(&k_shape, d.len())?;
    let size = parse_size(size_v)?;
    Intrinsics::new(&k_shape, &k, &d, size, &model)
}

fn extrinsics_from(v: &Value) -> Result<Extrinsics> {
    reject_unknown(v, &EXT_KEYS, "extrinsics")?;
    if let Some(conv) = v.get("convention") {
        if conv.as_str() != Some(CONVENTION) {
            return Err(cal(format!(
                "extrinsics.convention {} ≠ {}",
                py_json(conv),
                py_str(CONVENTION)
            )));
        }
    }
    let (r_shape, r) = ndarray(require(v, "R", "extrinsics")?, "extrinsics")?;
    let (_, t) = ndarray(require(v, "t", "extrinsics")?, "extrinsics")?;
    Extrinsics::new(&r_shape, &r, &t)
}

/// 한 눈 블록만(Python `CameraIntrinsics.from_dict(d, where)`).
pub fn parse_intrinsics(text: &str, at: &str, allow_nan: bool) -> Result<Intrinsics> {
    intrinsics_from(&load_json(text, allow_nan)?, at)
}

/// 외부 파라미터 블록만(Python `StereoExtrinsics.from_dict(d)`).
pub fn parse_extrinsics(text: &str, allow_nan: bool) -> Result<Extrinsics> {
    extrinsics_from(&load_json(text, allow_nan)?)
}

/// 캘리브 JSON 읽기·검증(Python `StereoCalibration.from_json` / `from_dict` 와 같은 순서·문안).
pub fn parse(text: &str, allow_nan: bool) -> Result<StereoCalibration> {
    let v = load_json(text, allow_nan)?;
    reject_unknown(&v, &TOP_KEYS, "calibration")?;
    let schema = require(&v, "schema", "calibration")?;
    if schema.as_str() != Some(SCHEMA_NAME) {
        return Err(cal(format!(
            "schema {} ≠ {}",
            py_json(schema),
            py_str(SCHEMA_NAME)
        )));
    }
    let ver = require(&v, "schema_version", "calibration")?;
    // Python `ver != 1`: 1.0 과 True 도 같다고 본다
    let ver_ok = match ver {
        Value::Number(n) => n.as_f64() == Some(SCHEMA_VERSION as f64),
        Value::Bool(b) => *b,
        _ => false,
    };
    if !ver_ok {
        return Err(cal(format!(
            "schema_version {} is not supported (this SDK: {SCHEMA_VERSION})",
            py_json(ver)
        )));
    }
    let left = intrinsics_from(require(&v, "left", "calibration")?, "left")?;
    let right = intrinsics_from(require(&v, "right", "calibration")?, "right")?;
    let extrinsics = extrinsics_from(require(&v, "extrinsics", "calibration")?)?;
    let units = match v.get("units") {
        None => "mm".to_string(),
        Some(Value::String(s)) => s.clone(),
        Some(other) => py_json(other),
    };
    if units != "mm" {
        return Err(cal(format!(
            "units must be 'mm': {}",
            py_str(&units)
        )));
    }
    let mut device = Vec::new();
    if let Some(dv) = v.get("device") {
        let obj = dv
            .as_object()
            .ok_or_else(|| cal("the device block allows string→string entries only"))?;
        for (k, val) in obj {
            match val {
                Value::String(s) => device.push((k.clone(), s.clone())),
                _ => return Err(cal("the device block allows string→string entries only")),
            }
        }
    }
    let meta = v
        .get("meta")
        .cloned()
        .unwrap_or(Value::Object(Default::default()));
    Ok(StereoCalibration {
        left,
        right,
        extrinsics,
        device,
        meta,
        units,
    })
}

/// 파일 읽기.
pub fn load_calibration(path: &std::path::Path) -> Result<StereoCalibration> {
    let text =
        std::fs::read_to_string(path).map_err(|e| cal(format!("{}: {e}", path.display())))?;
    parse(&text, false)
}

// ---- 쓰기·합성 -----------------------------------------------------------------------------------

fn num_json(x: f64) -> Result<String> {
    if !x.is_finite() {
        return Err(cal("cannot write NaN/inf to JSON (NaN/Infinity are not allowed)"));
    }
    Ok(serde_json::Number::from_f64(x)
        .map(|n| n.to_string())
        .unwrap_or_default())
}

fn mat_json(m: &[[f64; 3]; 3]) -> Result<String> {
    let rows: Result<Vec<String>> = m
        .iter()
        .map(|r| {
            Ok(format!(
                "[{}, {}, {}]",
                num_json(r[0])?,
                num_json(r[1])?,
                num_json(r[2])?
            ))
        })
        .collect();
    Ok(format!("[{}]", rows?.join(", ")))
}

fn intr_json(i: &Intrinsics) -> Result<String> {
    let d: Result<Vec<String>> = i.d.iter().map(|x| num_json(*x)).collect();
    Ok(format!(
        "{{\"model\": {}, \"K\": {}, \"D\": [{}], \"size\": [{}, {}]}}",
        serde_json::to_string(&i.model).unwrap_or_default(),
        mat_json(&i.k)?,
        d?.join(", "),
        i.size.0,
        i.size.1
    ))
}

fn has_nonstandard(v: &Value) -> bool {
    match v {
        Value::String(s) => s.starts_with(NAN_MARK),
        Value::Array(a) => a.iter().any(has_nonstandard),
        Value::Object(o) => o.values().any(has_nonstandard),
        _ => false,
    }
}

/// JSON 텍스트(키 순서는 Python `to_dict` 와 같다). 실수 표기는 serde_json(최단 왕복) — Python 과 바이트가 같을 필요는
/// 없고, 서로 읽었을 때 숫자가 같다.
pub fn to_json(c: &StereoCalibration) -> Result<String> {
    if has_nonstandard(&c.meta) {
        return Err(cal("cannot write NaN/inf to JSON (NaN/Infinity are not allowed)"));
    }
    let device: Vec<String> = c
        .device
        .iter()
        .map(|(k, v)| {
            format!(
                "{}: {}",
                serde_json::to_string(k).unwrap_or_default(),
                serde_json::to_string(v).unwrap_or_default()
            )
        })
        .collect();
    let t: Result<Vec<String>> = c.extrinsics.t.iter().map(|x| num_json(*x)).collect();
    Ok(format!(
        "{{
  \"schema\": \"{SCHEMA_NAME}\",
  \"schema_version\": {SCHEMA_VERSION},
  \"units\": {},
  \"device\": {{{}}},
  \"left\": {},
  \"right\": {},
  \"extrinsics\": {{\"R\": {}, \"t\": [{}], \"convention\": \"{CONVENTION}\"}},
  \"meta\": {}
}}
",
        serde_json::to_string(&c.units).unwrap_or_default(),
        device.join(", "),
        intr_json(&c.left)?,
        intr_json(&c.right)?,
        mat_json(&c.extrinsics.r)?,
        t?.join(", "),
        serde_json::to_string(&c.meta).unwrap_or_default()
    ))
}

/// 이상적 등거리(D=0) 어안 K — **테스트·초기 추정용**, 실측이 아니다. f = (min(w,h)/2) / rad(fov/2), 주점 = 영상 중심.
pub fn synthetic_equidistant(size: (i64, i64), fov_deg: f64) -> Result<Intrinsics> {
    let (w, h) = size;
    if w <= 0 || h <= 0 || !(fov_deg > 0.0 && fov_deg < 360.0) {
        return Err(cal(format!(
            "size=({w}, {h}) fov_deg={}",
            py_float(fov_deg)
        )));
    }
    let f = (w.min(h) as f64 / 2.0) / (fov_deg / 2.0).to_radians();
    let k = [
        f,
        0.0,
        (w - 1) as f64 / 2.0,
        0.0,
        f,
        (h - 1) as f64 / 2.0,
        0.0,
        0.0,
        1.0,
    ];
    Intrinsics::new(&[3, 3], &k, &[0.0; 4], size, "KB4")
}

// Tests against the calibration files kept in this repository (not in the published crate).
#[cfg(all(test, feature = "repo-tests"))]
#[path = "calib_repo_tests.rs"]
mod repo_tests;

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::{json, Value};

    /// Python 시험의 `_calib()` 과 같은 합성 캘리브(1280×800, 180°, t=(-64,0,0)).
    fn synth() -> Value {
        let f = 400.0 / 90f64.to_radians();
        let intr = json!({"model": "KB4", "K": [[f, 0.0, 639.5], [0.0, f, 399.5], [0.0, 0.0, 1.0]], "D": [0.0, 0.0, 0.0, 0.0], "size": [1280, 800]});
        json!({"schema": SCHEMA_NAME, "schema_version": 1, "units": "mm", "device": {"model": "synthetic"},
               "left": intr, "right": intr,
               "extrinsics": {"R": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]], "t": [-64.0, 0.0, 0.0], "convention": CONVENTION},
               "meta": {}})
    }

    fn err(v: Value) -> String {
        match parse(&v.to_string(), false) {
            Err(GeomError::Calibration(m)) => m,
            other => panic!("expected CalibrationError, got {other:?}"),
        }
    }

    #[test]
    fn unknown_keys_are_rejected() {
        let mut v = synth();
        v["baseline"] = json!(64.0);
        assert_eq!(
            err(v),
            "calibration: unknown key ['baseline'] (allowed: ['schema', 'schema_version', 'units', 'device', 'left', 'right', 'extrinsics', 'meta'])"
        );
        let mut v = synth();
        v["left"]["fov_deg"] = json!(185);
        assert_eq!(
            err(v),
            "left: unknown key ['fov_deg'] (allowed: ['model', 'K', 'D', 'size'])"
        );
    }

    #[test]
    fn schema_mismatch_is_rejected() {
        let mut v = synth();
        v["schema_version"] = json!(99);
        assert_eq!(err(v), "schema_version 99 is not supported (this SDK: 1)");
        let mut v = synth();
        v["schema_version"] = json!(1.0); // Python: 1.0 != 1 는 False — 받는다
        parse(&v.to_string(), false).unwrap();
        let mut v = synth();
        v["schema"] = json!("something.else");
        assert_eq!(
            err(v),
            "schema 'something.else' ≠ 'opticmix_vision.stereo_calibration'"
        );
        let mut v = synth();
        v.as_object_mut().unwrap().remove("left");
        assert_eq!(err(v), "calibration: missing required key 'left'");
    }

    #[test]
    fn non_standard_numbers_in_json_are_rejected() {
        match parse(r#"{"schema": "x", "v": NaN}"#, false) {
            Err(GeomError::Calibration(m)) => {
                assert_eq!(m, "non-standard number in JSON: 'NaN' (NaN/Infinity are not allowed)")
            }
            o => panic!("{o:?}"),
        }
        match parse(r#"{"a": "NaN in a string", "b": -Infinity}"#, false) {
            Err(GeomError::Calibration(m)) => {
                assert_eq!(m, "non-standard number in JSON: '-Infinity' (NaN/Infinity are not allowed)")
            }
            o => panic!("{o:?}"),
        }
        // dict 경로(allow_nan): NaN 은 값으로 들어가 K 검사가 잡는다(Python from_dict 와 같은 문안)
        let text = synth().to_string().replacen("639.5", "NaN", 1);
        assert_eq!(
            parse(&text, true).unwrap_err(),
            GeomError::Calibration("CameraIntrinsics: K has NaN/inf".into())
        );
    }

    #[test]
    fn folded_kb4_is_rejected() {
        let mut v = synth();
        v["left"]["D"] = json!([-0.5, 0, 0, 0]);
        let m = err(v);
        assert!(m.starts_with("CameraIntrinsics: KB4 coefficients fold inside the field of view (not monotonic): at about 46.84° (checked up to 92.5°)"), "{m}");
    }

    #[test]
    fn bad_shapes_and_values() {
        let cases: [(&str, Value, &str); 6] = [
            ("D", json!([0, 0, 0]), "D must have the 4 KB4 coefficients (k1..k4): (3,)"),
            (
                "K",
                json!([[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]),
                "K must be (3,3): (4, 4)",
            ),
            (
                "K",
                json!([[-1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0]]),
                "CameraIntrinsics: fx/fy must be positive: fx=-1.0, fy=1.0",
            ),
            (
                "model",
                json!("pinhole"),
                "CameraIntrinsics: model 'pinhole': only KB4 is supported",
            ),
            (
                "size",
                json!([0, 800]),
                "CameraIntrinsics: size must be positive: (0, 800)",
            ),
            ("size", json!([800]), "size must be [width, height]: (800,)"),
        ];
        for (key, val, want) in cases {
            let mut v = synth();
            v["left"][key] = val;
            assert_eq!(err(v), want, "{key}");
        }
        let mut v = synth();
        v["left"]["D"] = json!([[0], [0], [0], [0]]); // numpy reshape(-1) — 받는다
        parse(&v.to_string(), false).unwrap();
        let mut v = synth();
        v["left"]["size"] = json!([800.9, "600"]); // Python int(800.9)=800, int("600")=600
        assert_eq!(parse(&v.to_string(), false).unwrap().left.size, (800, 600));
    }

    #[test]
    fn several_problems_are_joined_in_python_order() {
        let i = Intrinsics {
            k: [[-1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
            d: [-0.5, 0.0, 0.0, 0.0],
            size: (0, 1),
            model: "x".into(),
        };
        assert_eq!(
            intrinsics_problems(&i, DEFAULT_MAX_THETA_DEG),
            [
                "model 'x': only KB4 is supported",
                "fx/fy must be positive: fx=-1.0, fy=1.0",
                "KB4 coefficients fold inside the field of view (not monotonic): at about 46.84° (checked up to 92.5°)",
                "size must be positive: (0, 1)",
            ]
        );
    }

    #[test]
    fn extrinsics_validation() {
        let e = |r: Value, t: Value| -> String {
            let mut v = synth();
            v["extrinsics"]["R"] = r;
            v["extrinsics"]["t"] = t;
            err(v)
        };
        let rot = "R is not a rotation matrix (RᵀR≠I or det≠+1)";
        assert_eq!(
            e(json!([[2, 0, 0], [0, 2, 0], [0, 0, 2]]), json!([1, 0, 0])),
            rot
        );
        assert_eq!(
            e(json!([[1, 0, 0], [0, 1, 0], [0, 0, -1]]), json!([1, 0, 0])),
            rot
        ); // det = -1
        assert_eq!(
            e(json!([[1, 0, 0], [0, 1, 0], [0, 0, 1]]), json!([0, 0, 0])),
            "t is the zero vector (baseline 0)"
        );
        assert_eq!(
            e(json!([[1, 0, 0], [0, 1, 0]]), json!([1, 0, 0])),
            "R must be (3,3): (2, 3)"
        );
        assert_eq!(
            e(json!([[1, 0, 0], [0, 1, 0], [0, 0, 1]]), json!([1, 0])),
            "t must be (3,): (2,)"
        );
        let mut v = synth();
        v["extrinsics"]["convention"] = json!("p_left = ...");
        assert_eq!(
            err(v),
            "extrinsics.convention 'p_left = ...' ≠ 'p_right = R @ p_left + t'"
        );
        let ext = Extrinsics::new(
            &[3, 3],
            &[1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0],
            &[3.0, 4.0, 0.0],
        )
        .unwrap();
        assert_eq!(ext.baseline_mm(), 5.0);
    }

    #[test]
    fn allclose_uses_numpy_tolerances() {
        // np.allclose(a, I, atol=1e-6): |a-b| <= 1e-6 + 1e-5·|b|
        let mut m = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]];
        m[0][0] = 1.0 + 1.05e-5;
        assert!(allclose_eye(&m));
        m[0][0] = 1.0 + 1.2e-5;
        assert!(!allclose_eye(&m));
        let mut m = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]];
        m[0][1] = 1.05e-6;
        assert!(!allclose_eye(&m));
    }

    #[test]
    fn blocks_can_be_checked_on_their_own() {
        let v = synth();
        assert_eq!(
            parse_intrinsics(&v["left"].to_string(), "left", false)
                .unwrap()
                .size,
            (1280, 800)
        );
        let mut e = v["extrinsics"].clone();
        e["convention"] = json!("p_left = ...");
        assert!(
            matches!(parse_extrinsics(&e.to_string(), false), Err(GeomError::Calibration(m)) if m.contains("convention"))
        );
        let mut l = v["left"].clone();
        l["x"] = json!(1);
        assert_eq!(
            parse_intrinsics(&l.to_string(), "right", false).unwrap_err(),
            GeomError::Calibration(
                "right: unknown key ['x'] (allowed: ['model', 'K', 'D', 'size'])".into()
            )
        );
    }

    #[test]
    fn units_and_device_block_are_strict() {
        let mut v = synth();
        v["units"] = json!("m");
        assert_eq!(err(v), "units must be 'mm': 'm'");
        let mut v = synth();
        v["device"] = json!({"serial": 1234});
        assert_eq!(err(v), "the device block allows string→string entries only");
    }

    #[test]
    fn nan_is_refused_on_write() {
        let text = synth()
            .to_string()
            .replacen("\"meta\":{}", "\"meta\":{\"x\":NaN}", 1);
        let c = parse(&text, true).unwrap(); // dict 경로: meta 의 NaN 은 검사하지 않는다(Python 과 같음)
        assert!(matches!(to_json(&c), Err(GeomError::Calibration(m)) if m.contains("NaN")));
    }

    #[test]
    fn synthetic_equidistant_is_valid_and_centered() {
        // Python test_synthetic_equidistant_is_valid_and_centered: (1280, 800), 180°
        let i = synthetic_equidistant((1280, 800), 180.0).unwrap();
        assert!(intrinsics_problems(&i, DEFAULT_MAX_THETA_DEG).is_empty());
        assert_eq!((i.size, i.model.as_str()), ((1280, 800), "KB4"));
        assert_eq!((i.cx(), i.cy()), (639.5, 399.5));
        assert_eq!(i.fx(), 400.0 / 90f64.to_radians());
        assert_eq!(i.d, [0.0; 4]);
        assert_eq!(
            synthetic_equidistant((0, 800), 180.0),
            Err(GeomError::Calibration("size=(0, 800) fov_deg=180.0".into()))
        );
        assert!(synthetic_equidistant((10, 10), 360.0).is_err());
    }
}
