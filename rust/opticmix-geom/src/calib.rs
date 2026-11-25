//! 캘리브레이션 파일(JSON, `opticmix_vision.stereo_calibration` schema_version 1) — 눈별 KB4 내부 + 좌→우 외부.
//! 타입·검사·읽기(`parse`)는 공개 크레이트 opticmix-vision 의 `src/calib/schema.rs` 한 벌을 같이 컴파일한다(Rust `calib` 과
//! Python `calibration.py` 가 같은 검사·같은 문안). 여기에는 블록 단위 검사·쓰기·합성만 남는다.

use serde_json::Value;

use crate::error::{GeomError, Result};
use crate::pyrepr::py_float;

#[path = "../../opticmix-vision/src/calib/schema.rs"]
mod schema;
pub use schema::*;
use schema::{extrinsics_from, intrinsics_from, load_json, NAN_MARK};

fn cal(m: impl Into<String>) -> GeomError {
    GeomError::Calibration(m.into())
}

/// 한 눈 블록만(Python `CameraIntrinsics.from_dict(d, where)`).
pub fn parse_intrinsics(text: &str, at: &str, allow_nan: bool) -> Result<Intrinsics> {
    intrinsics_from(&load_json(text, allow_nan)?, at)
}

/// 외부 파라미터 블록만(Python `StereoExtrinsics.from_dict(d)`).
pub fn parse_extrinsics(text: &str, allow_nan: bool) -> Result<Extrinsics> {
    extrinsics_from(&load_json(text, allow_nan)?)
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
