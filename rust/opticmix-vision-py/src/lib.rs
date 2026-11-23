//! `opticmix_vision._core` — the Python extension. The bindings are in `bindings.rs`.

use pyo3::prelude::*;

mod bindings;
mod calib;

#[pymodule]
fn _core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    bindings::register(m)?;
    calib::register(m)
}
