//! One error type. Messages are the Python originals — the Python wrapper re-raises them as the same exception classes,
//! and the existing Python tests check the text with `match=`.

use std::fmt;

/// Exceptions as seen from Python: Unavailable·Closed → ControlsUnavailable, State → ControlStateError,
/// UnknownControl → KeyError, InvalidValue → ValueError, WrongType → TypeError, Backend → OSError,
/// NoMatch / MultipleMatches → devices.NoMatch / MultipleMatches, Calibration → calibration.CalibrationError,
/// CalibLookup → calib.CalibLookupError, Recording → OSError, EndOfRecording → device.EndOfRecording.
///
/// `#[non_exhaustive]`: variants may be added — match with a wildcard arm.
#[derive(Debug, Clone, PartialEq, Eq)]
#[non_exhaustive]
pub enum SdkError {
    /// The control path cannot be opened — backend connection failure, or a different device.
    Unavailable(String),
    /// Call after close.
    Closed,
    /// A write the current mode cannot take (e.g. an exposure value alone while auto exposure is on).
    State(String),
    /// A control name this camera does not have.
    UnknownControl {
        name: String,
        have: Vec<String>,
    },
    InvalidValue(String),
    WrongType(String),
    /// Backend (driver) call failed. hresult only for COM calls.
    Backend {
        what: String,
        hresult: Option<i32>,
    },
    NoMatch(String),
    MultipleMatches(String),
    /// Exception raised by an outside (Python) backend — carries its type name as is.
    Foreign {
        type_name: String,
        message: String,
    },
    /// Camera open, read or decode failure (Python `DeviceError`). Never returns an empty frame silently.
    Device(String),
    /// A calibration file that breaks the schema or the geometry checks, or cannot be read (Python `CalibrationError`).
    Calibration(String),
    /// No usable calibration — none installed, several installed, or the eye size does not match the mode; the
    /// message says what to do (Python `CalibLookupError`).
    CalibLookup(String),
    /// A recording's files (`recording`) cannot be written or read: a file that exists already, an AVI this crate
    /// cannot play, a CSV row that is not a frame time, a write that failed (Python `OSError`).
    Recording(String),
    /// `Device::read` reached the end of a recording (`Device::open_recording`) (Python `EndOfRecording`).
    EndOfRecording(String),
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
            | SdkError::Foreign { message: m, .. }
            | SdkError::Device(m)
            | SdkError::Calibration(m)
            | SdkError::CalibLookup(m)
            | SdkError::Recording(m)
            | SdkError::EndOfRecording(m) => f.write_str(m),
        }
    }
}

impl std::error::Error for SdkError {}

impl SdkError {
    /// Exception type name as seen from Python. Used in a failed write's `error` string (`"<type>: <message>"`).
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
            SdkError::Device(_) => "DeviceError",
            SdkError::Calibration(_) => "CalibrationError",
            SdkError::CalibLookup(_) => "CalibLookupError",
            SdkError::Recording(_) => "OSError",
            SdkError::EndOfRecording(_) => "EndOfRecording",
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
            SdkError::Device("d".into()),
            SdkError::Calibration("c".into()),
            SdkError::CalibLookup("l".into()),
            SdkError::Recording("r".into()),
            SdkError::EndOfRecording("e".into()),
        ] {
            assert_eq!(e.to_string().len(), 1, "{e:?}");
        }
    }
}
