//! 오류 하나로 모은다. 문안은 Python 원문 그대로 — Python 래퍼가 같은 예외 클래스로 다시 던지고,
//! 기존 Python 시험이 `match=` 로 문안을 본다.

use std::fmt;

/// Python 에서 보이는 예외: Unavailable·Closed → ControlsUnavailable, State → ControlStateError,
/// UnknownControl → KeyError, InvalidValue → ValueError, WrongType → TypeError, Backend → OSError,
/// NoMatch / MultipleMatches → devices.NoMatch / MultipleMatches.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SdkError {
    /// 제어 통로를 열 수 없다 — 백엔드 연결 실패, 다른 장치.
    Unavailable(String),
    /// 닫힌 뒤의 호출.
    Closed,
    /// 지금 모드에서 받을 수 없는 쓰기(예: 자동 노출 중 노출값만).
    State(String),
    /// 이 카메라에 없는 제어 이름.
    UnknownControl {
        name: String,
        have: Vec<String>,
    },
    InvalidValue(String),
    WrongType(String),
    /// 백엔드(드라이버) 호출 실패. hresult 는 COM 호출일 때만.
    Backend {
        what: String,
        hresult: Option<i32>,
    },
    NoMatch(String),
    MultipleMatches(String),
    /// 바깥(Python) 백엔드가 던진 예외 — 타입 이름을 그대로 들고 다닌다.
    Foreign {
        type_name: String,
        message: String,
    },
}

pub type Result<T> = std::result::Result<T, SdkError>;

impl fmt::Display for SdkError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            SdkError::Closed => f.write_str("these controls are closed"),
            SdkError::UnknownControl { name, have } => {
                write!(
                    f,
                    "'{name}' is not a control of this camera — have: {}",
                    have.join(", ")
                )
            }
            SdkError::Backend {
                what,
                hresult: Some(hr),
            } => write!(f, "{what} failed: HRESULT 0x{:08X}", *hr as u32),
            SdkError::Backend {
                what,
                hresult: None,
            } => f.write_str(what),
            SdkError::Unavailable(m)
            | SdkError::State(m)
            | SdkError::InvalidValue(m)
            | SdkError::WrongType(m)
            | SdkError::NoMatch(m)
            | SdkError::MultipleMatches(m)
            | SdkError::Foreign { message: m, .. } => f.write_str(m),
        }
    }
}

impl std::error::Error for SdkError {}

impl SdkError {
    /// Python 에서 보일 예외 타입 이름. 실패한 쓰기의 `error` 문자열("<타입>: <문안>")에 쓴다.
    pub fn python_type(&self) -> &str {
        match self {
            SdkError::Unavailable(_) | SdkError::Closed => "ControlsUnavailable",
            SdkError::State(_) => "ControlStateError",
            SdkError::UnknownControl { .. } => "KeyError",
            SdkError::InvalidValue(_) => "ValueError",
            SdkError::WrongType(_) => "TypeError",
            SdkError::Backend { .. } => "OSError",
            SdkError::NoMatch(_) => "NoMatch",
            SdkError::MultipleMatches(_) => "MultipleMatches",
            SdkError::Foreign { type_name, .. } => type_name,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn closed_reads_like_python() {
        assert_eq!(SdkError::Closed.to_string(), "these controls are closed");
    }

    #[test]
    fn unknown_control_lists_what_the_camera_has() {
        let e = SdkError::UnknownControl {
            name: "iris".into(),
            have: vec!["gain".into(), "exposure".into()],
        };
        assert_eq!(
            e.to_string(),
            "'iris' is not a control of this camera — have: gain, exposure"
        );
    }

    #[test]
    fn backend_error_shows_the_hresult_as_unsigned_hex() {
        let e = SdkError::Backend {
            what: "Get(gain)".into(),
            hresult: Some(0x8007_0057u32 as i32),
        };
        assert_eq!(e.to_string(), "Get(gain) failed: HRESULT 0x80070057");
        let e = SdkError::Backend {
            what: "the driver rejected gain".into(),
            hresult: None,
        };
        assert_eq!(e.to_string(), "the driver rejected gain");
    }

    #[test]
    fn python_type_names_the_exception_python_sees() {
        assert_eq!(
            SdkError::Backend {
                what: "x".into(),
                hresult: None
            }
            .python_type(),
            "OSError"
        );
        assert_eq!(SdkError::Closed.python_type(), "ControlsUnavailable");
        let f = SdkError::Foreign {
            type_name: "RuntimeError".into(),
            message: "boom".into(),
        };
        assert_eq!(
            (f.python_type(), f.to_string().as_str()),
            ("RuntimeError", "boom")
        );
    }

    #[test]
    fn message_variants_pass_their_text_through() {
        for e in [
            SdkError::Unavailable("u".into()),
            SdkError::State("s".into()),
            SdkError::InvalidValue("v".into()),
            SdkError::WrongType("t".into()),
            SdkError::NoMatch("n".into()),
            SdkError::MultipleMatches("m".into()),
        ] {
            assert_eq!(e.to_string().len(), 1, "{e:?}");
        }
    }
}
