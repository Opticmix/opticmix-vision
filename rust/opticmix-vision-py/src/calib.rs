//! 보정 파일 검사(opticmix-geom calib·KB4)의 Python 바인딩 — `opticmix_vision.calibration` 이 쓴다.
//! 오류: Calibration → `opticmix_vision.calibration.CalibrationError`(Python 클래스를 import 해 던짐), Value → ValueError.
//! Rectification and depth bindings are not part of this crate.

use opticmix_geom::{calib, kb4, GeomError};
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;

pub(crate) fn to_py(py: Python<'_>, e: GeomError) -> PyErr {
    match e {
        GeomError::Calibration(m) => {
            let cls = py
                .import("opticmix_vision.calibration")
                .and_then(|m| m.getattr("CalibrationError"));
            match cls.and_then(|c| c.call1((m.clone(),))) {
                Ok(inst) => PyErr::from_value(inst),
                Err(_) => PyRuntimeError::new_err(m),
            }
        }
        GeomError::Value(m) => PyValueError::new_err(m),
    }
}

pub(crate) fn m3(v: &[f64], what: &str) -> PyResult<[[f64; 3]; 3]> {
    if v.len() != 9 {
        return Err(PyValueError::new_err(format!(
            "{what}: 3x3 (9 values) expected, got {}",
            v.len()
        )));
    }
    Ok([[v[0], v[1], v[2]], [v[3], v[4], v[5]], [v[6], v[7], v[8]]])
}

pub(crate) fn d4(v: &[f64]) -> PyResult<[f64; 4]> {
    v.try_into()
        .map_err(|_| PyValueError::new_err(format!("D: 4 values expected, got {}", v.len())))
}

// ---- 캘리브 -----------------------------------------------------------------------------------

/// Validates calibration JSON (`allow_nan`: True when the text is a re-serialized dict). Returns None if it passes.
#[pyfunction]
#[pyo3(signature = (text, allow_nan=false))]
fn calib_validate_json(py: Python<'_>, text: &str, allow_nan: bool) -> PyResult<()> {
    calib::parse(text, allow_nan)
        .map(|_| ())
        .map_err(|e| to_py(py, e))
}

/// Validates one eye's JSON block (`CameraIntrinsics.from_dict`).
#[pyfunction]
#[pyo3(signature = (text, at, allow_nan=true))]
fn intrinsics_validate_json(py: Python<'_>, text: &str, at: &str, allow_nan: bool) -> PyResult<()> {
    calib::parse_intrinsics(text, at, allow_nan)
        .map(|_| ())
        .map_err(|e| to_py(py, e))
}

/// Validates the extrinsics JSON block (`StereoExtrinsics.from_dict`).
#[pyfunction]
#[pyo3(signature = (text, allow_nan=true))]
fn extrinsics_validate_json(py: Python<'_>, text: &str, allow_nan: bool) -> PyResult<()> {
    calib::parse_extrinsics(text, allow_nan)
        .map(|_| ())
        .map_err(|e| to_py(py, e))
}

#[pyfunction]
fn check_intrinsics_shapes(py: Python<'_>, k_shape: Vec<usize>, d_len: usize) -> PyResult<()> {
    calib::check_intrinsics_shapes(&k_shape, d_len).map_err(|e| to_py(py, e))
}

/// Problem list of `CameraIntrinsics.validate`.
#[pyfunction]
fn intrinsics_problems(
    k: Vec<f64>,
    d: Vec<f64>,
    size: (i64, i64),
    model: String,
    max_theta_deg: f64,
) -> PyResult<Vec<String>> {
    let i = calib::Intrinsics {
        k: m3(&k, "K")?,
        d: d4(&d)?,
        size,
        model,
    };
    Ok(calib::intrinsics_problems(&i, max_theta_deg))
}

/// `StereoExtrinsics.__post_init__` checks → baseline (mm).
#[pyfunction]
fn check_extrinsics(
    py: Python<'_>,
    r_shape: Vec<usize>,
    r: Vec<f64>,
    t: Vec<f64>,
) -> PyResult<f64> {
    calib::Extrinsics::new(&r_shape, &r, &t)
        .map(|e| e.baseline_mm())
        .map_err(|e| to_py(py, e))
}

#[pyfunction]
fn kb4_is_injective(d: Vec<f64>, max_theta_rad: f64, n: usize) -> PyResult<(bool, Option<f64>)> {
    let fold = kb4::kb4_is_injective(&d4(&d)?, max_theta_rad, n);
    Ok((fold.is_none(), fold))
}

/// (K 9개, D 4개, (w, h)).
type IntrinsicsTuple = (Vec<f64>, Vec<f64>, (i64, i64));

/// Synthetic equidistant K.
#[pyfunction]
fn synthetic_equidistant(
    py: Python<'_>,
    size: (i64, i64),
    fov_deg: f64,
) -> PyResult<IntrinsicsTuple> {
    let i = calib::synthetic_equidistant(size, fov_deg).map_err(|e| to_py(py, e))?;
    Ok((
        i.k.iter().flatten().copied().collect(),
        i.d.to_vec(),
        i.size,
    ))
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("CALIB_SCHEMA_NAME", calib::SCHEMA_NAME)?;
    m.add("CALIB_DEFAULT_MAX_THETA_DEG", calib::DEFAULT_MAX_THETA_DEG)?;
    for f in [
        wrap_pyfunction!(calib_validate_json, m)?,
        wrap_pyfunction!(intrinsics_validate_json, m)?,
        wrap_pyfunction!(extrinsics_validate_json, m)?,
        wrap_pyfunction!(check_intrinsics_shapes, m)?,
        wrap_pyfunction!(intrinsics_problems, m)?,
        wrap_pyfunction!(check_extrinsics, m)?,
        wrap_pyfunction!(kb4_is_injective, m)?,
        wrap_pyfunction!(synthetic_equidistant, m)?,
    ] {
        m.add_function(f)?;
    }
    Ok(())
}
