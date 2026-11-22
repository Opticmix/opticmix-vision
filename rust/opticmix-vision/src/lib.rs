//! Opticmix Vision SDK 코어 — 카메라 제어(Weevia S1).
//!
//! Python `opticmix_vision` 의 controls / _ctrl_dshow / devices 를 1:1 로 옮긴다. Python 패키지는 이 코어를
//! PyO3 확장(`opticmix_vision._core`)으로 부르는 얇은 래퍼다 — 제어 로직은 여기 한 벌뿐이다.

pub mod backend;
pub mod controls;
pub mod convert;
pub mod devices;
pub mod dshow;
pub mod error;
pub mod guard;
pub mod spec;

pub use error::{Result, SdkError};
