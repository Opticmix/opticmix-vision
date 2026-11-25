//! The calibration file (JSON, `opticmix_vision.stereo_calibration` schema_version 1): its types and checks.
//!
//! One source for both Rust crates that read the file: this crate (`calib`) and the geometry crate of the
//! repository, which the Python package `opticmix_vision.calibration` calls through its extension — so Rust and
//! Python apply the same checks in the same order with the same messages. Each crate brings its own error type
//! (`super::cal`, `super::Result`) and `crate::pyrepr`.
//!
//! Checks: an unknown key is an error, NaN/inf are neither read nor written, KB4 coefficients that fold inside
//! the field of view are refused, R must be a rotation and the baseline non-zero. Extrinsics convention:
//! p_right = R·p_left + t, mm.

use serde_json::Value;

use super::{cal, Result};
use crate::pyrepr::{py_fixed, py_float, py_g, py_json, py_list_str, py_str, py_tuple};

/// The `schema` value of the file.
pub const SCHEMA_NAME: &str = "opticmix_vision.stereo_calibration";
/// The `schema_version` this crate reads.
pub const SCHEMA_VERSION: i64 = 1;
/// Half of the 185° (vendor spec) lens field; the field of view has not been measured. Same value as Python.
pub const DEFAULT_MAX_THETA_DEG: f64 = 92.5;
/// The extrinsics convention the file states: R and t take left-eye coordinates to right-eye coordinates.
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

/// One eye's intrinsics (Kannala-Brandt 4th order, as OpenCV's `cv::fisheye`).
#[derive(Debug, Clone, PartialEq)]
pub struct Intrinsics {
    /// Camera matrix [[fx, 0, cx], [0, fy, cy], [0, 0, 1]], pixels.
    pub k: [[f64; 3]; 3],
    /// Distortion k1, k2, k3, k4.
    pub d: [f64; 4],
    /// Eye image (width, height) the values are for.
    pub size: (i64, i64),
    /// `"KB4"`.
    pub model: String,
}

/// Left eye → right eye: p_right = R·p_left + t. Camera axes X right, Y down, Z forward; t in mm.
#[derive(Debug, Clone, PartialEq)]
pub struct Extrinsics {
    /// Rotation, 3×3.
    pub r: [[f64; 3]; 3],
    /// Translation, mm.
    pub t: [f64; 3],
}

/// A whole calibration file.
#[derive(Debug, Clone, PartialEq)]
pub struct StereoCalibration {
    pub left: Intrinsics,
    pub right: Intrinsics,
    pub extrinsics: Extrinsics,
    /// The file's `device` block (strings only, such as `model` and `serial`), sorted by key.
    pub device: Vec<(String, String)>,
    /// The file's free-form `meta` block (an empty object when the file has none).
    pub meta: Value,
    /// `"mm"`.
    pub units: String,
}

impl StereoCalibration {
    /// |t|, mm.
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
        errs.push(format!(
            "size must be positive: ({}, {})",
            i.size.0, i.size.1
        ));
    }
    errs
}

impl Intrinsics {
    /// `k_shape`: the shape of `k` (must be `[3, 3]`); `k`: its 9 values, row by row; `d`: k1..k4. Checks the
    /// shapes and lengths, then the values (field limit `DEFAULT_MAX_THETA_DEG`); the error lists every problem
    /// ("CameraIntrinsics: a; b").
    pub fn new(
        k_shape: &[usize],
        k: &[f64],
        d: &[f64],
        size: (i64, i64),
        model: &str,
    ) -> Result<Intrinsics> {
        check_intrinsics_shapes(k_shape, d.len())?;
        if k.len() != 9 {
            return Err(cal(format!("K must have 9 values (3x3), got {}", k.len())));
        }
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
    /// `r_shape`: the shape of `r` (must be `[3, 3]`); `r`: its 9 values, row by row; `t`: 3 values, mm. Checks, in
    /// this order: shapes and lengths, NaN/inf, that R is a rotation, a non-zero baseline (Python `StereoExtrinsics`).
    pub fn new(r_shape: &[usize], r: &[f64], t: &[f64]) -> Result<Extrinsics> {
        if r_shape != [3, 3] {
            return Err(cal(format!("R must be (3,3): {}", py_tuple(r_shape))));
        }
        if r.len() != 9 {
            return Err(cal(format!("R must have 9 values (3x3), got {}", r.len())));
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

pub(crate) const NAN_MARK: &str = "\u{0}opticmix-nonstandard:";

/// JSON 텍스트 읽기. `allow_nan=false`(파일 읽기) 는 Python `parse_constant` 처럼 첫 비표준 숫자에서 거부,
/// `allow_nan=true`(dict 를 다시 직렬화한 것) 는 NaN·±Infinity 를 값으로 들인다 — 그러면 K·R 검사가 잡는다.
pub(crate) fn load_json(text: &str, allow_nan: bool) -> Result<Value> {
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

pub(crate) fn intrinsics_from(v: &Value, at: &str) -> Result<Intrinsics> {
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

pub(crate) fn extrinsics_from(v: &Value) -> Result<Extrinsics> {
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
        return Err(cal(format!("units must be 'mm': {}", py_str(&units))));
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

/// Python `kb4_is_injective(D, max_theta_rad, n)`: θ_d(θ) 의 도함수 1+3k1θ²+5k2θ⁴+7k3θ⁶+9k4θ⁸ 가 `np.linspace(0, max, n)`
/// 의 모든 점에서 양수면 None, 아니면 처음 접힌 각(rad). 격자 사이의 접힘은 보지 않는다(원래 설계).
/// linspace 는 numpy 와 같은 식: i·step(step=(max-0)/(n-1)), 마지막 점은 max 그대로. `**3`·`**4` 는 npy_pow(=powf).
pub fn kb4_is_injective(d: &[f64; 4], max_theta_rad: f64, n: usize) -> Option<f64> {
    let step = if n > 1 {
        max_theta_rad / (n - 1) as f64
    } else {
        0.0
    };
    for i in 0..n {
        let th = if n > 1 && i == n - 1 {
            max_theta_rad
        } else {
            i as f64 * step
        };
        let t2 = th * th;
        let dd = 1.0
            + 3.0 * d[0] * t2
            + 5.0 * d[1] * (t2 * t2)
            + 7.0 * d[2] * t2.powf(3.0)
            + 9.0 * d[3] * t2.powf(4.0);
        if dd <= 0.0 {
            return Some(th);
        }
    }
    None
}
