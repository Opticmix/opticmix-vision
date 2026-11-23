//! 오류 둘: 캘리브 스키마/기하 위반(Python CalibrationError)과 그 밖의 값 오류(Python ValueError). 문안은 Python 원문.

use std::fmt;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum GeomError {
    /// Python `calibration.CalibrationError`.
    Calibration(String),
    /// Python `ValueError`(rectify·depth).
    Value(String),
}

pub type Result<T> = std::result::Result<T, GeomError>;

impl fmt::Display for GeomError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            GeomError::Calibration(m) | GeomError::Value(m) => f.write_str(m),
        }
    }
}

impl std::error::Error for GeomError {}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn messages_pass_through() {
        assert_eq!(GeomError::Calibration("a".into()).to_string(), "a");
        assert_eq!(GeomError::Value("b".into()).to_string(), "b");
    }
}
