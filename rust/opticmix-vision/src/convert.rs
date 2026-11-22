//! 사용자 값 ↔ 장치 값(Python `_as_int`·`_to_device`·`_from_device`·`_same`). 틀린 값은 여기서 막는다 — 장치에 보내지 않는다.

use crate::error::{Result, SdkError};
use crate::spec::{ControlSpec, Kind, Role};

/// 사용자가 준 값. Python 바인딩은 bool → Bool, 정수(numpy 포함) → Int, float → Float, str → Choice 로 바꾼다.
#[derive(Debug, Clone, PartialEq)]
pub enum UserValue {
    Int(i64),
    Bool(bool),
    Float(f64),
    Choice(String),
}

impl UserValue {
    /// Python `repr()` 과 같은 표기 — 오류 문안이 Python 원문과 같도록.
    pub fn repr(&self) -> String {
        match self {
            UserValue::Int(v) => v.to_string(),
            UserValue::Bool(true) => "True".into(),
            UserValue::Bool(false) => "False".into(),
            UserValue::Float(v) => format!("{v:?}"),
            UserValue::Choice(s) => format!("'{s}'"),
        }
    }
}

/// 백엔드로 갈 값: value 역할은 정수, auto 역할은 자동 여부.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DevValue {
    Value(i64),
    Auto(bool),
}

fn tuple_repr(choices: &[&str]) -> String {
    let inner: Vec<String> = choices.iter().map(|c| format!("'{c}'")).collect();
    if inner.len() == 1 {
        format!("({},)", inner[0])
    } else {
        format!("({})", inner.join(", "))
    }
}

/// Python `_as_int`: bool 은 TypeError, 정수·정수값 float 는 받고, 나머지는 ValueError.
fn as_int(name: &str, value: &UserValue) -> Result<i64> {
    match value {
        UserValue::Bool(_) => Err(SdkError::WrongType(format!(
            "{name} takes a number, not {}",
            value.repr()
        ))),
        UserValue::Int(v) => Ok(*v),
        UserValue::Float(f) if f.is_finite() && f.fract() == 0.0 => Ok(*f as i64),
        _ => Err(SdkError::InvalidValue(format!(
            "{name} takes a whole number: {}",
            value.repr()
        ))),
    }
}

/// 사용자 값 → 백엔드 값. 범위 검사는 여기서 하지 않는다(장치 범위가 필요해 Controls 가 한다).
pub fn to_device(spec: &ControlSpec, value: &UserValue) -> Result<DevValue> {
    match spec.kind {
        Kind::Menu => {
            let choices = spec.choices.unwrap_or(&[]);
            let idx = match value {
                UserValue::Choice(s) => choices.iter().position(|c| c == s),
                _ => None,
            };
            let Some(idx) = idx else {
                return Err(SdkError::InvalidValue(format!(
                    "{} must be one of {}: {}",
                    spec.name,
                    tuple_repr(choices),
                    value.repr()
                )));
            };
            Ok(match spec.role {
                Role::Auto => DevValue::Auto(choices[idx] == "auto"),
                Role::Value => DevValue::Value(idx as i64),
            })
        }
        Kind::Bool => {
            // Python: isinstance(value, str) or value not in (True, False, 0, 1) → 거부
            let b = match value {
                UserValue::Bool(b) => Some(*b),
                UserValue::Int(0) => Some(false),
                UserValue::Int(1) => Some(true),
                UserValue::Float(f) if *f == 0.0 => Some(false),
                UserValue::Float(f) if *f == 1.0 => Some(true),
                _ => None,
            };
            let Some(b) = b else {
                return Err(SdkError::InvalidValue(format!(
                    "{} takes True/False: {}",
                    spec.name,
                    value.repr()
                )));
            };
            Ok(match spec.role {
                Role::Auto => DevValue::Auto(b),
                Role::Value => DevValue::Value(b as i64),
            })
        }
        Kind::Int => Ok(DevValue::Value(as_int(spec.name, value)?)),
    }
}

/// 백엔드 값 → 사용자 값. 모르는 것은 None — 지어내지 않는다.
pub fn from_device(spec: &ControlSpec, raw: Option<i64>, auto: Option<bool>) -> Option<UserValue> {
    if spec.role == Role::Auto {
        let a = auto?;
        return Some(if spec.kind == Kind::Menu {
            UserValue::Choice(if a { "auto" } else { "manual" }.into())
        } else {
            UserValue::Bool(a)
        });
    }
    let raw = raw?;
    Some(match spec.kind {
        Kind::Menu => match spec.choices {
            Some(ch) if raw >= 0 && (raw as usize) < ch.len() => {
                UserValue::Choice(ch[raw as usize].into())
            }
            _ => UserValue::Int(raw),
        },
        Kind::Bool => UserValue::Bool(raw != 0),
        Kind::Int => UserValue::Int(raw),
    })
}

/// Python `==` 과 같은 비교: 숫자끼리는 값으로(40 == 40.0, True == 1), 문자열은 문자열끼리.
pub fn py_eq(a: &UserValue, b: &UserValue) -> bool {
    fn num(v: &UserValue) -> Option<f64> {
        match v {
            UserValue::Int(i) => Some(*i as f64),
            UserValue::Bool(b) => Some(*b as i64 as f64),
            UserValue::Float(f) => Some(*f),
            UserValue::Choice(_) => None,
        }
    }
    match (a, b) {
        (UserValue::Choice(x), UserValue::Choice(y)) => x == y,
        _ => matches!((num(a), num(b)), (Some(x), Some(y)) if x == y),
    }
}

/// 요청 (value, auto) 가 장치 상태와 같으면 true. 모르는 것(None)은 같다고 보지 않는다.
pub fn same(
    value: Option<i64>,
    auto: Option<bool>,
    cur_value: Option<i64>,
    cur_auto: Option<bool>,
) -> bool {
    if auto == Some(true) {
        return cur_auto == Some(true); // 자동으로 가는 쓰기에 값은 의미가 없다
    }
    if auto.is_some() && cur_auto != auto {
        return false;
    }
    match value {
        None => true,
        Some(v) => cur_value == Some(v),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::spec::spec;
    use UserValue::*;

    fn to(name: &str, v: UserValue) -> Result<DevValue> {
        to_device(spec(name).unwrap(), &v)
    }

    fn s(x: &str) -> UserValue {
        Choice(x.into())
    }

    #[test]
    fn user_values_become_device_values() {
        // Python test_user_values_become_device_values 표 그대로
        let cases = [
            ("gain", Int(28), DevValue::Value(28)),
            ("gain", Float(28.0), DevValue::Value(28)),
            ("exposure", Int(-5), DevValue::Value(-5)),
            ("auto_exposure", s("manual"), DevValue::Auto(false)),
            ("auto_exposure", s("auto"), DevValue::Auto(true)),
            ("auto_white_balance", Bool(true), DevValue::Auto(true)),
            ("auto_exposure_priority", Bool(false), DevValue::Value(0)),
            ("power_line_frequency", s("60hz"), DevValue::Value(2)),
            ("power_line_frequency", s("disabled"), DevValue::Value(0)),
        ];
        for (name, v, want) in cases {
            assert_eq!(to(name, v.clone()), Ok(want), "{name} {v:?}");
        }
        // bool 제어는 0/1 숫자도 받는다(Python: value in (True, False, 0, 1))
        assert_eq!(to("auto_white_balance", Int(1)), Ok(DevValue::Auto(true)));
        assert_eq!(
            to("auto_exposure_priority", Float(1.0)),
            Ok(DevValue::Value(1))
        );
    }

    #[test]
    fn wrong_values_are_refused_before_the_camera_sees_them() {
        // Python test_wrong_values_are_refused_before_the_camera_sees_them 표 그대로
        assert_eq!(
            to("gain", Float(28.5)),
            Err(SdkError::InvalidValue(
                "gain takes a whole number: 28.5".into()
            ))
        );
        assert_eq!(
            to("gain", Bool(true)),
            Err(SdkError::WrongType("gain takes a number, not True".into()))
        );
        assert_eq!(
            to("gain", s("28")),
            Err(SdkError::InvalidValue(
                "gain takes a whole number: '28'".into()
            ))
        );
        assert_eq!(
            to("auto_exposure", s("on")),
            Err(SdkError::InvalidValue(
                "auto_exposure must be one of ('manual', 'auto'): 'on'".into()
            ))
        );
        assert!(matches!(
            to("power_line_frequency", s("55hz")),
            Err(SdkError::InvalidValue(_))
        ));
        assert_eq!(
            to("auto_exposure_priority", Int(2)),
            Err(SdkError::InvalidValue(
                "auto_exposure_priority takes True/False: 2".into()
            ))
        );
        assert!(matches!(
            to("auto_white_balance", s("True")),
            Err(SdkError::InvalidValue(_))
        ));
        assert!(matches!(
            to("power_line_frequency", Int(2)),
            Err(SdkError::InvalidValue(_))
        ));
    }

    #[test]
    fn device_values_become_user_values() {
        // Python test_device_values_become_user_values 표 그대로
        let f = |name: &str, raw: Option<i64>, auto: Option<bool>| {
            from_device(spec(name).unwrap(), raw, auto)
        };
        assert_eq!(f("gain", Some(28), None), Some(Int(28)));
        assert_eq!(f("auto_exposure", Some(-5), Some(true)), Some(s("auto")));
        assert_eq!(f("auto_exposure", Some(-5), Some(false)), Some(s("manual")));
        assert_eq!(f("auto_exposure", Some(-5), None), None);
        assert_eq!(f("power_line_frequency", Some(1), None), Some(s("50hz")));
        assert_eq!(f("power_line_frequency", Some(3), None), Some(Int(3)));
        assert_eq!(f("auto_exposure_priority", Some(1), None), Some(Bool(true)));
        assert_eq!(
            f("auto_white_balance", Some(128), Some(true)),
            Some(Bool(true))
        );
        assert_eq!(f("gain", None, None), None);
    }

    #[test]
    fn same_value_and_mode_is_same() {
        // Python test_same_value_and_mode_is_same 그대로
        assert!(same(Some(5), None, Some(5), None));
        assert!(!same(Some(5), None, Some(6), None));
        assert!(same(Some(5), Some(false), Some(5), Some(false)));
        assert!(!same(Some(5), Some(false), Some(5), None)); // 모르는 모드는 같다고 보지 않는다
        assert!(same(None, Some(true), Some(1), Some(true))); // 자동으로 가는 쓰기에 값은 상관없다
        assert!(!same(None, Some(true), Some(1), Some(false)));
        assert!(!same(Some(5), None, None, None)); // 모르는 값도 같지 않다
    }

    #[test]
    fn python_equality() {
        assert!(
            py_eq(&Int(40), &Float(40.0)) && py_eq(&Bool(true), &Int(1)) && py_eq(&s("a"), &s("a"))
        );
        assert!(!py_eq(&Int(1), &s("1")) && !py_eq(&Int(40), &Float(40.5)));
    }

    #[test]
    fn python_repr_of_values() {
        assert_eq!(Int(-5).repr(), "-5");
        assert_eq!(Bool(false).repr(), "False");
        assert_eq!(s("a").repr(), "'a'");
        assert_eq!(Float(28.0).repr(), "28.0");
    }
}
