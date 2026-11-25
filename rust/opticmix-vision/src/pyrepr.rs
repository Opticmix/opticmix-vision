//! Python 표기 — 오류 문안이 Python 원문과 같도록(`repr()`·`f"{x:g}"`·`f"{x:.2f}"`·튜플·리스트·JSON 값).

use serde_json::Value;

/// (부호, 유효숫자 문자열, 첫 자리의 십진 지수) — Rust `{:e}` 의 최단 왕복 표기에서.
fn digits(x: f64) -> (bool, String, i32) {
    let s = format!("{x:e}");
    let (m, e) = s.split_once('e').expect("{:e} has an exponent");
    let neg = m.starts_with('-');
    let d: String = m.chars().filter(|c| c.is_ascii_digit()).collect();
    (neg, d, e.parse().expect("exponent"))
}

fn exp_suffix(e: i32) -> String {
    format!("e{}{:02}", if e < 0 { '-' } else { '+' }, e.abs())
}

/// Python `repr(float)`: 최단 왕복 숫자, 지수 -4 ≤ e < 16 이면 고정 소수(정수면 ".0"), 아니면 `1e-05` 꼴.
pub fn py_float(x: f64) -> String {
    if x.is_nan() {
        return "nan".into();
    }
    if x.is_infinite() {
        return if x > 0.0 { "inf".into() } else { "-inf".into() };
    }
    let (neg, d, e) = digits(x);
    let sign = if neg { "-" } else { "" };
    if (-4..16).contains(&e) {
        let body = if e >= 0 {
            let int_len = (e + 1) as usize;
            if d.len() <= int_len {
                format!("{}{}.0", d, "0".repeat(int_len - d.len()))
            } else {
                format!("{}.{}", &d[..int_len], &d[int_len..])
            }
        } else {
            format!("0.{}{}", "0".repeat((-e - 1) as usize), d)
        };
        format!("{sign}{body}")
    } else {
        let mant = if d.len() == 1 {
            d.clone()
        } else {
            format!("{}.{}", &d[..1], &d[1..])
        };
        format!("{sign}{mant}{}", exp_suffix(e))
    }
}

fn strip_zeros(s: &str) -> String {
    if s.contains('.') {
        s.trim_end_matches('0').trim_end_matches('.').to_string()
    } else {
        s.to_string()
    }
}

/// Python `f"{x:g}"`(유효숫자 6).
pub fn py_g(x: f64) -> String {
    if !x.is_finite() {
        return py_float(x);
    }
    let r = format!("{x:.5e}");
    let (m, e) = r.split_once('e').expect("exponent");
    let e: i32 = e.parse().expect("exponent");
    if (-4..6).contains(&e) {
        strip_zeros(&format!("{:.*}", (5 - e) as usize, x))
    } else {
        format!("{}{}", strip_zeros(m), exp_suffix(e))
    }
}

/// Python `f"{x:.{n}f}"`.
pub fn py_fixed(x: f64, n: usize) -> String {
    format!("{x:.n$}")
}

/// Python `repr(str)`.
pub fn py_str(s: &str) -> String {
    let q = if s.contains('\'') && !s.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut o = String::with_capacity(s.len() + 2);
    o.push(q);
    for c in s.chars() {
        match c {
            '\\' => o.push_str("\\\\"),
            '\n' => o.push_str("\\n"),
            '\r' => o.push_str("\\r"),
            '\t' => o.push_str("\\t"),
            c if c == q => {
                o.push('\\');
                o.push(c);
            }
            c => o.push(c),
        }
    }
    o.push(q);
    o
}

/// Python 튜플 repr(정수) — numpy `shape` 표기.
pub fn py_tuple(v: &[usize]) -> String {
    match v {
        [] => "()".into(),
        [one] => format!("({one},)"),
        _ => format!(
            "({})",
            v.iter()
                .map(|x| x.to_string())
                .collect::<Vec<_>>()
                .join(", ")
        ),
    }
}

/// Python 리스트 repr(문자열).
pub fn py_list_str(v: &[&str]) -> String {
    format!(
        "[{}]",
        v.iter().map(|s| py_str(s)).collect::<Vec<_>>().join(", ")
    )
}

/// JSON 값을 Python `repr` 로(json.loads 결과의 repr 과 같다).
pub fn py_json(v: &Value) -> String {
    match v {
        Value::Null => "None".into(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::Number(n) => match (n.as_i64(), n.as_u64()) {
            (Some(i), _) => i.to_string(),
            (None, Some(u)) => u.to_string(),
            _ => py_float(n.as_f64().unwrap_or(f64::NAN)),
        },
        Value::String(s) => py_str(s),
        Value::Array(a) => format!("[{}]", a.iter().map(py_json).collect::<Vec<_>>().join(", ")),
        Value::Object(o) => format!(
            "{{{}}}",
            o.iter()
                .map(|(k, v)| format!("{}: {}", py_str(k), py_json(v)))
                .collect::<Vec<_>>()
                .join(", ")
        ),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn floats_read_like_python_repr() {
        // Python: repr(x)
        let cases = [
            (92.5, "92.5"),
            (1e-05, "1e-05"),
            (0.0001, "0.0001"),
            (100.0, "100.0"),
            (-1.0, "-1.0"),
            (0.1, "0.1"),
            (1e16, "1e+16"),
            (1234567890123456.0, "1234567890123456.0"),
            (123.45678901234567, "123.45678901234567"),
            (-0.12345678901234568, "-0.12345678901234568"),
            (0.0, "0.0"),
            (-0.0, "-0.0"),
            (2.5e-310, "2.5e-310"),
            (f64::NAN, "nan"),
            (f64::INFINITY, "inf"),
            (f64::NEG_INFINITY, "-inf"),
        ];
        for (x, want) in cases {
            assert_eq!(py_float(x), want, "{x:e}");
        }
    }

    #[test]
    fn g_and_fixed_formats_read_like_python() {
        // Python: f"{x:g}", f"{x:.2f}"
        for (x, want) in [
            (92.5, "92.5"),
            (90.0, "90"),
            (1e-05, "1e-05"),
            (123456789.0, "1.23457e+08"),
            (0.0001, "0.0001"),
            (1.0, "1"),
        ] {
            assert_eq!(py_g(x), want, "{x}");
        }
        assert_eq!(py_fixed(91.234, 2), "91.23");
        assert_eq!(py_fixed(0.125, 2), "0.12"); // 정확한 이진값 0.125 → 짝수 쪽(Python 과 같음)
    }

    #[test]
    fn tuples_lists_strings_and_json_values() {
        assert_eq!(py_tuple(&[3, 3]), "(3, 3)");
        assert_eq!(py_tuple(&[3]), "(3,)");
        assert_eq!(py_tuple(&[]), "()");
        assert_eq!(py_str("left"), "'left'");
        assert_eq!(py_str("it's"), "\"it's\"");
        assert_eq!(py_list_str(&["K", "D"]), "['K', 'D']");
        assert_eq!(
            py_json(&json!({"a": [1, 2.5, null, true, "x"]})),
            "{'a': [1, 2.5, None, True, 'x']}"
        );
    }
}
