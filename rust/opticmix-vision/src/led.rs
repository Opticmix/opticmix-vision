//! IR LED (flood illumination) — the real path is UVC Backlight Compensation (`backlight_compensation`); the write guard applies.
//! Measured 2026-09-13: above 96 reads back as 96 (two units); 0–16 off and light from 24 (one unit).
//! readback is the value the driver accepted, not evidence of light output — check that it is on from frame brightness.

use std::sync::Arc;

use crate::controls::{Controls, WriteResult};
use crate::convert::UserValue;
use crate::error::{Result, SdkError};

pub const LED_CONTROL: &str = "backlight_compensation";
/// (min, max) — measured 2026-09-13.
pub const LED_RANGE: (i64, i64) = (0, 96);

/// IR LED level — reads and writes `Controls`' `backlight_compensation` as a raw value (0–96) or a 0.0–1.0 level.
pub struct LedControl {
    c: Arc<Controls>,
}

impl LedControl {
    pub fn new(c: Arc<Controls>) -> LedControl {
        LedControl { c }
    }

    pub fn get(&self) -> Result<i64> {
        match self.c.get(LED_CONTROL)? {
            Some(UserValue::Int(v)) => Ok(v),
            other => Err(SdkError::Unavailable(format!("{LED_CONTROL} gave no value: {other:?}"))),
        }
    }

    /// Writes a raw value and returns the final result (readback included).
    pub fn set(&self, raw: i64) -> Result<WriteResult> {
        let r = self.c.set(LED_CONTROL, UserValue::Int(raw))?;
        Ok(self.c.wait(&[r.ticket], Some(2.0)).pop().unwrap_or(r))
    }

    /// Maps 0.0–1.0 onto `LED_RANGE` and writes it (rounded to an integer — halves go to even, like Python `round`).
    pub fn set_level(&self, level: f64) -> Result<WriteResult> {
        if !(0.0..=1.0).contains(&level) {
            return Err(SdkError::InvalidValue(format!("level is 0.0 to 1.0: {level}")));
        }
        let (lo, hi) = LED_RANGE;
        self.set((lo as f64 + (hi - lo) as f64 * level).round_ties_even() as i64)
    }

    pub fn get_level(&self) -> Result<f64> {
        let (lo, hi) = LED_RANGE;
        Ok((self.get()? - lo) as f64 / (hi - lo) as f64)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::backend::mock::MockBackend;
    use crate::controls::Status;

    fn led() -> LedControl {
        let c = Controls::new(Box::new(MockBackend::new()), false, "", None).unwrap();
        LedControl::new(Arc::new(c))
    }

    #[test]
    fn set_then_get_reads_the_raw_value_back() {
        let l = led();
        assert_eq!(l.set(48).unwrap().status, Status::Written);
        assert_eq!(l.get().unwrap(), 48);
    }

    #[test]
    fn level_maps_onto_the_measured_range() {
        let l = led();
        l.set_level(0.5).unwrap();
        assert_eq!(l.get().unwrap(), 48);
        assert!((l.get_level().unwrap() - 0.5).abs() < 1e-9);
    }

    #[test]
    fn level_rounds_like_python_round() {
        // python -c "print([round(96 * l) for l in (0.3, 3/64, 7/64, 11/64)])" → [29, 4, 10, 16](반값은 짝수 쪽).
        let l = led();
        for (level, raw) in [(0.3, 29), (3.0 / 64.0, 4), (7.0 / 64.0, 10), (11.0 / 64.0, 16)] {
            l.set_level(level).unwrap();
            assert_eq!(l.get().unwrap(), raw, "{level}");
        }
    }

    #[test]
    fn level_outside_zero_to_one_is_refused_before_any_write() {
        let l = led();
        for bad in [-0.1, 1.5, f64::NAN] {
            assert!(matches!(l.set_level(bad), Err(SdkError::InvalidValue(_))), "{bad}");
        }
    }
}
