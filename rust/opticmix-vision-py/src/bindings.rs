//! `opticmix_vision._core` — Rust 코어를 Python 에서 부르는 얇은 층. 제어 로직은 코어 한 벌뿐이고, 여기서는
//! 값·예외·결과를 Python 모양으로 바꾸기만 한다(Python 래퍼 `opticmix_vision.controls` 등이 기존 공개 API 를 지킨다).
//!
//! 🔴 GIL: 워커 결과를 기다리는 모든 호출은 GIL 을 놓고 기다린다(`py.detach`). 놓지 않으면 Python 백엔드
//! (`PyBackend`, 예: SimBackend·OpenCVBackend)를 부르려는 워커와 교착한다.
//! 예외는 Python 모듈에 정의된 클래스(ControlsUnavailable·ControlStateError·NoMatch·MultipleMatches)를 import 해 던진다.

use std::collections::HashMap;
use std::sync::Mutex;

use opticmix_vision::backend::{Backend, RawRange, ReadOnly};
use opticmix_vision::controls::{ControlInfo, Controls, Event, EventHook, WriteResult};
use opticmix_vision::convert::{self, DevValue, UserValue};
use opticmix_vision::error::SdkError;
use opticmix_vision::spec::{self, Iface, CONTROLS};
use opticmix_vision::{devices, dshow, guard};
use pyo3::exceptions::{PyKeyError, PyOSError, PyRuntimeError, PyTypeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyDict, PyList, PyString, PyTuple};

// ---- 예외 ------------------------------------------------------------------------------------

fn py_class<'py>(py: Python<'py>, module: &str, name: &str) -> Option<Bound<'py, PyAny>> {
    py.import(module).ok()?.getattr(name).ok()
}

fn raise_as(py: Python<'_>, module: &str, name: &str, msg: String) -> PyErr {
    match py_class(py, module, name).and_then(|c| c.call1((msg.clone(),)).ok()) {
        Some(inst) => PyErr::from_value(inst),
        None => PyRuntimeError::new_err(msg),
    }
}

/// 코어 오류 → Python 예외.
fn to_py(py: Python<'_>, e: SdkError) -> PyErr {
    let msg = e.to_string();
    match e {
        SdkError::Unavailable(_) | SdkError::Closed => {
            raise_as(py, "opticmix_vision.controls", "ControlsUnavailable", msg)
        }
        SdkError::State(_) => raise_as(py, "opticmix_vision.controls", "ControlStateError", msg),
        SdkError::UnknownControl { .. } => PyKeyError::new_err(msg),
        SdkError::InvalidValue(_) => PyValueError::new_err(msg),
        SdkError::WrongType(_) => PyTypeError::new_err(msg),
        SdkError::Backend { .. } => PyOSError::new_err(msg),
        SdkError::NoMatch(_) => raise_as(py, "opticmix_vision.devices", "NoMatch", msg),
        SdkError::MultipleMatches(_) => {
            raise_as(py, "opticmix_vision.devices", "MultipleMatches", msg)
        }
        SdkError::Foreign { type_name, message } => {
            // 바깥 백엔드가 던진 내장 예외는 같은 타입으로 되살린다
            let builtin = py
                .import("builtins")
                .ok()
                .and_then(|b| b.getattr(type_name.as_str()).ok())
                .filter(|t| {
                    t.cast::<pyo3::types::PyType>().is_ok_and(|t| {
                        t.is_subclass_of::<pyo3::exceptions::PyBaseException>()
                            .unwrap_or(false)
                    })
                });
            match builtin.and_then(|t| t.call1((message.clone(),)).ok()) {
                Some(inst) => PyErr::from_value(inst),
                None => PyRuntimeError::new_err(format!("{type_name}: {message}")),
            }
        }
    }
}

/// Python 예외 → 코어 오류. ControlsUnavailable 는 그대로 Unavailable(Controls 가 올린다).
fn from_py(py: Python<'_>, e: PyErr) -> SdkError {
    let message = e.value(py).str().map(|s| s.to_string()).unwrap_or_default();
    if let Some(cu) = py_class(py, "opticmix_vision.controls", "ControlsUnavailable") {
        if e.value(py).is_instance(&cu).unwrap_or(false) {
            return SdkError::Unavailable(message);
        }
    }
    let type_name = e
        .get_type(py)
        .name()
        .map(|s| s.to_string())
        .unwrap_or_else(|_| "Exception".into());
    SdkError::Foreign { type_name, message }
}

// ---- 값 --------------------------------------------------------------------------------------

/// Python 값 → UserValue. bool 먼저(bool 은 int 의 하위형), 정수(numpy 정수는 __index__), 실수, 문자열.
fn user_value(v: &Bound<'_, PyAny>) -> PyResult<UserValue> {
    if v.is_instance_of::<PyBool>() {
        return Ok(UserValue::Bool(v.extract::<bool>()?));
    }
    if v.is_instance_of::<PyString>() {
        return Ok(UserValue::Choice(v.extract::<String>()?));
    }
    if let Ok(i) = v.extract::<i64>() {
        return Ok(UserValue::Int(i));
    }
    if let Ok(f) = v.extract::<f64>() {
        return Ok(UserValue::Float(f));
    }
    Err(PyValueError::new_err(format!(
        "unsupported control value: {}",
        v.repr()?
    )))
}

fn py_value<'py>(py: Python<'py>, v: &UserValue) -> PyResult<Bound<'py, PyAny>> {
    Ok(match v {
        UserValue::Int(i) => i.into_pyobject(py)?.into_any(),
        UserValue::Bool(b) => PyBool::new(py, *b).to_owned().into_any(),
        UserValue::Float(f) => f.into_pyobject(py)?.into_any(),
        UserValue::Choice(s) => PyString::new(py, s).into_any(),
    })
}

fn opt_value<'py>(py: Python<'py>, v: &Option<UserValue>) -> PyResult<Bound<'py, PyAny>> {
    match v {
        Some(v) => py_value(py, v),
        None => Ok(py.None().into_bound(py)),
    }
}

fn spec_of(name: &str) -> PyResult<&'static spec::ControlSpec> {
    spec::spec(name)
        .ok_or_else(|| PyKeyError::new_err(format!("{name:?} is not in the control table")))
}

fn iface_of(s: &str) -> PyResult<Iface> {
    match s {
        "procamp" => Ok(Iface::Procamp),
        "camera" => Ok(Iface::Camera),
        _ => Err(PyValueError::new_err(format!(
            "iface must be 'procamp' or 'camera': {s:?}"
        ))),
    }
}

fn write_result<'py>(py: Python<'py>, r: &WriteResult) -> PyResult<Bound<'py, PyTuple>> {
    PyTuple::new(
        py,
        [
            PyString::new(py, &r.name).into_any(),
            py_value(py, &r.requested)?,
            PyString::new(py, r.status.as_str()).into_any(),
            r.due_in_s.into_pyobject(py)?.into_any(),
            r.error.clone().into_pyobject(py)?.into_any(),
            r.ticket.into_pyobject(py)?.into_any(),
            opt_value(py, &r.readback)?,
        ],
    )
}

fn write_results<'py>(py: Python<'py>, rs: &[WriteResult]) -> PyResult<Bound<'py, PyList>> {
    let items: PyResult<Vec<_>> = rs.iter().map(|r| write_result(py, r)).collect();
    PyList::new(py, items?)
}

fn control_info<'py>(py: Python<'py>, i: &ControlInfo) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    d.set_item("name", i.name)?;
    d.set_item("kind", i.kind.as_str())?;
    d.set_item("current", opt_value(py, &i.current)?)?;
    d.set_item("minimum", i.minimum)?;
    d.set_item("maximum", i.maximum)?;
    d.set_item("step", i.step)?;
    d.set_item("default", opt_value(py, &i.default)?)?;
    d.set_item(
        "choices",
        i.choices
            .as_ref()
            .map(|c| PyTuple::new(py, c))
            .transpose()?,
    )?;
    d.set_item("units", &i.units)?;
    d.set_item("effect", i.effect.as_str())?;
    d.set_item("ledger", i.ledger)?;
    d.set_item("group", i.group)?;
    d.set_item("note", i.note)?;
    Ok(d)
}

// ---- 표·변환·가드 -------------------------------------------------------------------------------

/// Python `controls.CONTROLS` 를 만들 행들(ControlSpec 필드 이름 그대로).
#[pyfunction]
fn controls_table(py: Python<'_>) -> PyResult<Bound<'_, PyList>> {
    let rows: PyResult<Vec<_>> = CONTROLS
        .iter()
        .map(|c| {
            let d = PyDict::new(py);
            d.set_item("name", c.name)?;
            d.set_item("key", c.key)?;
            d.set_item("role", c.role.as_str())?;
            d.set_item("kind", c.kind.as_str())?;
            d.set_item("effect", c.effect.as_str())?;
            d.set_item("ledger", c.ledger)?;
            d.set_item(
                "choices",
                c.choices.map(|ch| PyTuple::new(py, ch)).transpose()?,
            )?;
            d.set_item("mono", c.mono)?;
            d.set_item("note", c.note)?;
            Ok(d)
        })
        .collect();
    PyList::new(py, rows?)
}

#[pyfunction]
fn to_device<'py>(
    py: Python<'py>,
    name: &str,
    value: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    let s = spec_of(name)?;
    match convert::to_device(s, &user_value(value)?).map_err(|e| to_py(py, e))? {
        DevValue::Value(v) => Ok(v.into_pyobject(py)?.into_any()),
        DevValue::Auto(a) => Ok(PyBool::new(py, a).to_owned().into_any()),
    }
}

#[pyfunction]
#[pyo3(signature = (name, raw, auto))]
fn from_device<'py>(
    py: Python<'py>,
    name: &str,
    raw: Option<i64>,
    auto: Option<bool>,
) -> PyResult<Bound<'py, PyAny>> {
    opt_value(py, &convert::from_device(spec_of(name)?, raw, auto))
}

#[pyfunction]
#[pyo3(signature = (value, auto, cur_value, cur_auto))]
fn same(
    value: Option<i64>,
    auto: Option<bool>,
    cur_value: Option<i64>,
    cur_auto: Option<bool>,
) -> bool {
    convert::same(value, auto, cur_value, cur_auto)
}

/// Python 모듈 전역(GROUP_MIN_INTERVAL_S·GLOBAL_MIN_GAP_S·WARN_AFTER_TOTAL)을 넘긴다. 원장 경로는 그대로 둔다.
#[pyfunction]
fn set_guard_config(
    group_intervals: HashMap<String, f64>,
    global_gap_s: f64,
    warn_after_total: i64,
) {
    let ledger = guard::config().ledger;
    guard::set_config(guard::GuardConfig {
        group_intervals,
        global_gap_s,
        warn_after_total: warn_after_total.max(0) as u64,
        ledger,
    });
}

#[pyfunction]
fn write_log_path() -> Option<String> {
    guard::config().ledger.map(|p| p.display().to_string())
}

#[pyfunction]
fn not_before(group: &str) -> f64 {
    guard::not_before(group)
}

#[pyfunction]
fn record(group: &str, now: f64) -> (Option<f64>, u64) {
    guard::record(group, now)
}

#[pyfunction]
fn write_counts(py: Python<'_>) -> PyResult<Bound<'_, PyDict>> {
    let d = PyDict::new(py);
    for (k, v) in guard::write_counts() {
        d.set_item(k, v)?;
    }
    Ok(d)
}

#[pyfunction]
fn reset_write_counts() {
    guard::reset_write_counts();
}

// ---- 장치 --------------------------------------------------------------------------------------

#[pyfunction]
fn enumerate_video_devices(py: Python<'_>) -> PyResult<Vec<Option<String>>> {
    py.detach(dshow::enumerate_video_devices)
        .map_err(|e| to_py(py, e))
}

#[pyfunction]
#[pyo3(signature = (names, index, expected))]
fn choose(
    py: Python<'_>,
    names: Vec<Option<String>>,
    index: i64,
    expected: Vec<String>,
) -> PyResult<()> {
    let exp: Vec<&str> = expected.iter().map(String::as_str).collect();
    // 음수 순번도 "그 순번에 장치가 없다"로(Python 0 <= index < len 검사와 같은 문안)
    if index < 0 {
        return Err(to_py(
            py,
            SdkError::Unavailable(format!(
                "no DirectShow video device at index {index} (found {})",
                names.len()
            )),
        ));
    }
    dshow::choose(&names, index as usize, &exp).map_err(|e| to_py(py, e))
}

#[pyfunction]
#[pyo3(signature = (raw=None))]
fn list_devices(py: Python<'_>, raw: Option<String>) -> Vec<(usize, String)> {
    py.detach(|| devices::list_devices(raw.as_deref()))
        .into_iter()
        .map(|d| (d.index, d.name))
        .collect()
}

#[pyfunction]
#[pyo3(signature = (name, raw=None))]
fn list_modes(py: Python<'_>, name: String, raw: Option<String>) -> Vec<(String, u32, u32, f64)> {
    py.detach(|| devices::list_modes(&name, raw.as_deref()))
        .into_iter()
        .map(|m| (m.fourcc, m.width, m.height, m.max_fps))
        .collect()
}

#[pyfunction]
#[pyo3(signature = (needle, raw=None))]
fn resolve_by_name(
    py: Python<'_>,
    needle: String,
    raw: Option<String>,
) -> PyResult<(usize, String)> {
    let d = py
        .detach(|| devices::resolve_by_name(&needle, raw.as_deref()))
        .map_err(|e| to_py(py, e))?;
    Ok((d.index, d.name))
}

/// query() 반환: (minimum, maximum, step, default, can_auto).
type RangeTuple = (i64, i64, i64, Option<i64>, bool);
/// plan_reset() 한 줄: (이름, 지금, 기본값).
type PlanRow<'py> = (String, Bound<'py, PyAny>, Bound<'py, PyAny>);

/// 이미 가진 (순번, 이름) 목록에서 이름으로 하나 고른다 → (순번, 이름).
#[pyfunction]
fn pick_by_name(
    py: Python<'_>,
    needle: &str,
    devices: Vec<(usize, String)>,
) -> PyResult<(usize, String)> {
    let list: Vec<devices::DeviceInfo> = devices
        .into_iter()
        .map(|(index, name)| devices::DeviceInfo { index, name })
        .collect();
    let d = devices::pick_by_name(needle, &list).map_err(|e| to_py(py, e))?;
    Ok((d.index, d.name))
}

// ---- DirectShow 백엔드 -------------------------------------------------------------------------

/// Rust DShowBackend. Controls 에 넘기면 안의 백엔드를 꺼내 워커 스레드로 옮긴다(그 뒤 이 객체는 빈 껍데기).
/// 단독 사용(진단 스크립트: start → query/read/raw_get → close)도 된다 — 한 스레드에서 부를 것.
#[pyclass(name = "DShowBackend", module = "opticmix_vision._core")]
struct PyDShow {
    inner: Mutex<Option<dshow::DShowBackend>>,
    started: Mutex<bool>,
    /// 되읽기 동치 도구용: 쓰기를 백엔드에 닿기 전에 거부한다(Controls 에 넘기면 ReadOnly 로 감싼다).
    read_only: bool,
}

impl PyDShow {
    fn with<R>(
        &self,
        py: Python<'_>,
        f: impl FnOnce(&mut dshow::DShowBackend) -> opticmix_vision::Result<R>,
    ) -> PyResult<R> {
        let mut g = self.inner.lock().unwrap_or_else(|e| e.into_inner());
        let b = g.as_mut().ok_or_else(|| {
            to_py(
                py,
                SdkError::Unavailable("this backend was handed to Controls".into()),
            )
        })?;
        f(b).map_err(|e| to_py(py, e))
    }
}

#[pymethods]
impl PyDShow {
    #[new]
    #[pyo3(signature = (index, expected, read_only=false))]
    fn new(py: Python<'_>, index: usize, expected: Vec<String>, read_only: bool) -> PyResult<Self> {
        let exp: Vec<&str> = expected.iter().map(String::as_str).collect();
        let b = dshow::DShowBackend::new(index, &exp).map_err(|e| to_py(py, e))?;
        Ok(PyDShow {
            inner: Mutex::new(Some(b)),
            started: Mutex::new(false),
            read_only,
        })
    }

    #[getter]
    fn name(&self) -> &'static str {
        "dshow"
    }

    #[getter]
    fn read_only(&self) -> bool {
        self.read_only
    }

    #[getter]
    fn exposure_units(&self) -> &'static str {
        "log2_s_nominal"
    }

    #[getter]
    fn note(&self) -> &'static str {
        ""
    }

    fn start(&self, py: Python<'_>) -> PyResult<()> {
        self.with(py, |b| b.start())?;
        *self.started.lock().unwrap_or_else(|e| e.into_inner()) = true;
        Ok(())
    }

    fn supports(&self, py: Python<'_>, key: &str) -> PyResult<bool> {
        self.with(py, |b| Ok(b.supports(key)))
    }

    /// (minimum, maximum, step, default, can_auto) 또는 None.
    fn query(&self, py: Python<'_>, key: &str) -> PyResult<Option<RangeTuple>> {
        Ok(self
            .with(py, |b| b.query(key))?
            .map(|r| (r.minimum, r.maximum, r.step, r.default, r.can_auto)))
    }

    fn read(&self, py: Python<'_>, key: &str) -> PyResult<(i64, Option<bool>)> {
        self.with(py, |b| b.read(key))
    }

    #[pyo3(signature = (key, value, auto))]
    fn write(
        &self,
        py: Python<'_>,
        key: &str,
        value: Option<i64>,
        auto: Option<bool>,
    ) -> PyResult<()> {
        if self.read_only {
            return Err(to_py(
                py,
                SdkError::Backend {
                    what: format!("read-only backend: write to {key} refused"),
                    hresult: None,
                },
            ));
        }
        self.with(py, |b| b.write(key, value, auto))
    }

    /// 진단(읽기 전용). Python 과 같은 dict: {"hr","value","flags"} 또는 {"error"}.
    fn raw_get<'py>(&self, py: Python<'py>, iface: &str, pid: i32) -> PyResult<Bound<'py, PyDict>> {
        let i = iface_of(iface)?;
        let d = PyDict::new(py);
        match self.with(py, |b| Ok(b.raw_get(i, pid)))? {
            Some(g) => {
                d.set_item("hr", format!("0x{:08X}", g.hr as u32))?;
                d.set_item("value", g.value)?;
                d.set_item("flags", g.flags)?;
            }
            None => d.set_item("error", format!("no {iface} interface"))?,
        }
        Ok(d)
    }

    /// 진단(읽기 전용). {"hr","min","max","step","default","caps"} 또는 {"error"}.
    fn raw_range<'py>(
        &self,
        py: Python<'py>,
        iface: &str,
        pid: i32,
    ) -> PyResult<Bound<'py, PyDict>> {
        let i = iface_of(iface)?;
        let d = PyDict::new(py);
        match self.with(py, |b| Ok(b.raw_range(i, pid)))? {
            Some(r) => {
                d.set_item("hr", format!("0x{:08X}", r.hr as u32))?;
                d.set_item("min", r.min)?;
                d.set_item("max", r.max)?;
                d.set_item("step", r.step)?;
                d.set_item("default", r.default)?;
                d.set_item("caps", r.caps)?;
            }
            None => d.set_item("error", format!("no {iface} interface"))?,
        }
        Ok(d)
    }

    fn close(&self, py: Python<'_>) -> PyResult<()> {
        let mut g = self.inner.lock().unwrap_or_else(|e| e.into_inner());
        match g.as_mut() {
            Some(b) => b.close().map_err(|e| to_py(py, e)),
            None => Ok(()),
        }
    }
}

// ---- Python 백엔드 어댑터 ----------------------------------------------------------------------

/// Python 객체(SimBackend·OpenCVBackend·사용자 백엔드)를 코어 Backend 로. 워커 스레드에서 GIL 을 잡고 부른다.
struct PyBackend {
    obj: Py<PyAny>,
    name: String,
    exposure_units: String,
    note: String,
}

impl PyBackend {
    fn new(obj: &Bound<'_, PyAny>) -> PyResult<Self> {
        let attr = |n: &str, default: &str| -> PyResult<String> {
            match obj.getattr(n) {
                Ok(v) => Ok(v.str()?.to_string()),
                Err(_) => Ok(default.to_string()),
            }
        };
        Ok(PyBackend {
            obj: obj.clone().unbind(),
            name: attr("name", "python")?,
            exposure_units: attr("exposure_units", "raw")?,
            note: attr("note", "")?,
        })
    }

    fn call<R>(
        &self,
        f: impl FnOnce(Python<'_>, &Bound<'_, PyAny>) -> PyResult<R>,
    ) -> opticmix_vision::Result<R> {
        Python::attach(|py| f(py, self.obj.bind(py)).map_err(|e| from_py(py, e)))
    }
}

impl Backend for PyBackend {
    fn name(&self) -> &str {
        &self.name
    }

    fn exposure_units(&self) -> &str {
        &self.exposure_units
    }

    fn note(&self) -> &str {
        &self.note
    }

    fn start(&mut self) -> opticmix_vision::Result<()> {
        self.call(|_, o| o.call_method0("start").map(|_| ()))
    }

    fn supports(&mut self, key: &str) -> bool {
        self.call(|_, o| o.call_method1("supports", (key,))?.is_truthy())
            .unwrap_or(false)
    }

    fn query(&mut self, key: &str) -> opticmix_vision::Result<Option<RawRange>> {
        self.call(|_, o| {
            let r = o.call_method1("query", (key,))?;
            if r.is_none() {
                return Ok(None);
            }
            Ok(Some(RawRange {
                minimum: r.getattr("minimum")?.extract()?,
                maximum: r.getattr("maximum")?.extract()?,
                step: r.getattr("step")?.extract()?,
                default: r.getattr("default")?.extract()?,
                can_auto: r.getattr("can_auto")?.is_truthy()?,
            }))
        })
    }

    fn read(&mut self, key: &str) -> opticmix_vision::Result<(i64, Option<bool>)> {
        self.call(|_, o| {
            let t = o.call_method1("read", (key,))?;
            let v: i64 = t.get_item(0)?.extract()?;
            let a = t.get_item(1)?;
            let a = if a.is_none() {
                None
            } else {
                Some(a.is_truthy()?)
            };
            Ok((v, a))
        })
    }

    fn write(
        &mut self,
        key: &str,
        value: Option<i64>,
        auto: Option<bool>,
    ) -> opticmix_vision::Result<()> {
        self.call(|_, o| o.call_method1("write", (key, value, auto)).map(|_| ()))
    }

    fn close(&mut self) -> opticmix_vision::Result<()> {
        self.call(|_, o| o.call_method0("close").map(|_| ()))
    }
}

// ---- Controls ----------------------------------------------------------------------------------

fn event_kind(e: &Event) -> &'static str {
    match e {
        Event::Wrote { .. } => "wrote",
        Event::WriteFailed { .. } => "write_failed",
        Event::NotKept { .. } => "not_kept",
        Event::WarnTotal { .. } => "warn_total",
        Event::Cancelled { .. } => "cancelled",
        Event::LedgerFailed { .. } => "ledger_failed",
    }
}

/// 코어 Controls. Python 래퍼 `controls.Controls` 가 감싼다.
#[pyclass(name = "CoreControls", module = "opticmix_vision._core")]
struct CoreControls {
    inner: Controls,
}

fn values_arg(items: &Bound<'_, PyAny>) -> PyResult<Vec<(String, UserValue)>> {
    let mut out = Vec::new();
    for it in items.try_iter()? {
        let it = it?;
        let name: String = it.get_item(0)?.extract()?;
        out.push((name, user_value(&it.get_item(1)?)?));
    }
    Ok(out)
}

#[pymethods]
impl CoreControls {
    /// backend: `_core.DShowBackend`(안의 Rust 백엔드를 꺼내 쓴다) 또는 Backend 프로토콜을 따르는 Python 객체.
    /// on_event(kind, message, is_warning): 워커 스레드에서 불린다.
    #[new]
    #[pyo3(signature = (backend, guard=true, note="", on_event=None))]
    fn new(
        py: Python<'_>,
        backend: &Bound<'_, PyAny>,
        guard: bool,
        note: &str,
        on_event: Option<Py<PyAny>>,
    ) -> PyResult<Self> {
        let b: Box<dyn Backend> = match backend.cast::<PyDShow>() {
            Ok(d) => {
                let d = d.borrow();
                if *d.started.lock().unwrap_or_else(|e| e.into_inner()) {
                    return Err(PyValueError::new_err(
                        "hand an unstarted DShowBackend to Controls",
                    ));
                }
                let inner = d.inner.lock().unwrap_or_else(|e| e.into_inner()).take();
                let inner = inner.ok_or_else(|| {
                    PyValueError::new_err("this DShowBackend was already handed to Controls")
                })?;
                if d.read_only {
                    Box::new(ReadOnly::new(inner))
                } else {
                    Box::new(inner)
                }
            }
            Err(_) => Box::new(PyBackend::new(backend)?),
        };
        let hook: Option<EventHook> = on_event.map(|cb| {
            Box::new(move |e: &Event| {
                Python::attach(|py| {
                    if let Err(err) = cb.call1(py, (event_kind(e), e.message(), e.is_warning())) {
                        err.print(py); // 콜백 실패가 쓰기 결과를 바꾸지 않게 — 보이게만 한다
                    }
                });
            }) as EventHook
        });
        let note = note.to_string();
        let inner = py
            .detach(move || Controls::new(b, guard, &note, hook))
            .map_err(|e| to_py(py, e))?;
        Ok(CoreControls { inner })
    }

    #[getter]
    fn backend_name(&self) -> String {
        self.inner.backend_name().to_string()
    }

    #[getter]
    fn backend_note(&self) -> String {
        self.inner.backend_note().to_string()
    }

    #[getter]
    fn closed(&self) -> bool {
        self.inner.closed()
    }

    fn names(&self, py: Python<'_>) -> PyResult<Vec<&'static str>> {
        py.detach(|| self.inner.names()).map_err(|e| to_py(py, e))
    }

    fn set_many<'py>(
        &self,
        py: Python<'py>,
        items: &Bound<'py, PyAny>,
    ) -> PyResult<Bound<'py, PyList>> {
        let values = values_arg(items)?;
        let rs = py
            .detach(|| self.inner.set_many(&values))
            .map_err(|e| to_py(py, e))?;
        write_results(py, &rs)
    }

    #[pyo3(signature = (timeout=None))]
    fn flush<'py>(&self, py: Python<'py>, timeout: Option<f64>) -> PyResult<Bound<'py, PyList>> {
        let rs = py.detach(|| self.inner.flush(timeout));
        write_results(py, &rs)
    }

    #[pyo3(signature = (tickets, timeout=None))]
    fn wait<'py>(
        &self,
        py: Python<'py>,
        tickets: Vec<u64>,
        timeout: Option<f64>,
    ) -> PyResult<Bound<'py, PyList>> {
        let rs = py.detach(|| self.inner.wait(&tickets, timeout));
        write_results(py, &rs)
    }

    fn result<'py>(&self, py: Python<'py>, ticket: u64) -> PyResult<Option<Bound<'py, PyTuple>>> {
        let r = py.detach(|| self.inner.result(ticket));
        r.as_ref().map(|r| write_result(py, r)).transpose()
    }

    fn pending(&self, py: Python<'_>) -> Vec<(String, f64)> {
        py.detach(|| self.inner.pending())
    }

    fn close(&self, py: Python<'_>) {
        py.detach(|| self.inner.close());
    }

    fn get<'py>(&self, py: Python<'py>, name: &str) -> PyResult<Bound<'py, PyAny>> {
        let v = py
            .detach(|| self.inner.get(name))
            .map_err(|e| to_py(py, e))?;
        opt_value(py, &v)
    }

    fn describe<'py>(&self, py: Python<'py>, name: &str) -> PyResult<Bound<'py, PyDict>> {
        let i = py
            .detach(|| self.inner.describe(name))
            .map_err(|e| to_py(py, e))?;
        control_info(py, &i)
    }

    fn snapshot<'py>(&self, py: Python<'py>) -> PyResult<Vec<(String, Bound<'py, PyAny>)>> {
        let s = py
            .detach(|| self.inner.snapshot())
            .map_err(|e| to_py(py, e))?;
        s.iter()
            .map(|(n, v)| Ok((n.clone(), py_value(py, v)?)))
            .collect()
    }

    fn restore<'py>(
        &self,
        py: Python<'py>,
        items: &Bound<'py, PyAny>,
    ) -> PyResult<Bound<'py, PyList>> {
        let values = values_arg(items)?;
        let rs = py
            .detach(|| self.inner.restore(&values))
            .map_err(|e| to_py(py, e))?;
        write_results(py, &rs)
    }

    #[pyo3(signature = (include_mono=false))]
    fn plan_reset<'py>(&self, py: Python<'py>, include_mono: bool) -> PyResult<Vec<PlanRow<'py>>> {
        let p = py
            .detach(|| self.inner.plan_reset(include_mono))
            .map_err(|e| to_py(py, e))?;
        p.iter()
            .map(|(n, c, d)| Ok((n.to_string(), py_value(py, c)?, py_value(py, d)?)))
            .collect()
    }

    #[pyo3(signature = (include_mono=false))]
    fn reset_to_defaults<'py>(
        &self,
        py: Python<'py>,
        include_mono: bool,
    ) -> PyResult<Bound<'py, PyList>> {
        let rs = py
            .detach(|| self.inner.reset_to_defaults(include_mono))
            .map_err(|e| to_py(py, e))?;
        write_results(py, &rs)
    }
}

impl Drop for CoreControls {
    fn drop(&mut self) {
        // GC 는 GIL 을 쥔 채 온다 — 기다리지 않고 닫는다(워커가 Python 백엔드의 close 를 부르려면 GIL 이 필요하다).
        self.inner.close_nowait();
    }
}

// ---- 모듈 --------------------------------------------------------------------------------------

/// 모듈에 바인딩 전부를 단다. `#[pymodule]` 은 lib.rs 에 있다.
pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("WEEVIA_S1_NAME", spec::WEEVIA_S1_NAME)?;
    m.add(
        "EXPECTED_NAMES",
        PyTuple::new(m.py(), spec::EXPECTED_NAMES)?,
    )?;
    m.add("KEYS", {
        let d = PyDict::new(m.py());
        for (k, i, id) in spec::KEYS {
            d.set_item(k, (i.as_str(), id))?;
        }
        d
    })?;
    m.add("WRITE_LOG_ENV", guard::WRITE_LOG_ENV)?;
    m.add_function(wrap_pyfunction!(controls_table, m)?)?;
    m.add_function(wrap_pyfunction!(to_device, m)?)?;
    m.add_function(wrap_pyfunction!(from_device, m)?)?;
    m.add_function(wrap_pyfunction!(same, m)?)?;
    m.add_function(wrap_pyfunction!(set_guard_config, m)?)?;
    m.add_function(wrap_pyfunction!(write_log_path, m)?)?;
    m.add_function(wrap_pyfunction!(not_before, m)?)?;
    m.add_function(wrap_pyfunction!(record, m)?)?;
    m.add_function(wrap_pyfunction!(write_counts, m)?)?;
    m.add_function(wrap_pyfunction!(reset_write_counts, m)?)?;
    m.add_function(wrap_pyfunction!(enumerate_video_devices, m)?)?;
    m.add_function(wrap_pyfunction!(choose, m)?)?;
    m.add_function(wrap_pyfunction!(list_devices, m)?)?;
    m.add_function(wrap_pyfunction!(list_modes, m)?)?;
    m.add_function(wrap_pyfunction!(resolve_by_name, m)?)?;
    m.add_function(wrap_pyfunction!(pick_by_name, m)?)?;
    m.add_class::<PyDShow>()?;
    m.add_class::<CoreControls>()?;
    Ok(())
}
