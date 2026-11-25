//! The backend the control layer calls (1:1 with the Python `controls.Backend` Protocol). Every method is called from one place only: the Controls worker thread.

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

use crate::error::{Result, SdkError};

/// Device value range from the backend. `default` is None when the device does not report one — it is never made up.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RawRange {
    pub minimum: i64,
    pub maximum: i64,
    pub step: i64,
    pub default: Option<i64>,
    pub can_auto: bool,
}

/// Same methods as the Python `Backend` Protocol. `Send` because Controls moves it to its worker thread.
pub trait Backend: Send {
    fn name(&self) -> &str;
    /// Name of the exposure unit ("log2_s_nominal" | "100us" | "raw").
    fn exposure_units(&self) -> &str;
    fn note(&self) -> &str;
    /// Device name for the write log. Empty string if unknown.
    fn device_name(&self) -> &str {
        ""
    }
    fn start(&mut self) -> Result<()>;
    fn supports(&mut self, key: &str) -> bool;
    fn query(&mut self, key: &str) -> Result<Option<RawRange>>;
    /// (value, auto). auto is None for keys where it is unknown or has no meaning.
    fn read(&mut self, key: &str) -> Result<(i64, Option<bool>)>;
    /// value None = keep the value (a write that switches to auto); auto None = leave the mode alone.
    fn write(&mut self, key: &str, value: Option<i64>, auto: Option<bool>) -> Result<()>;
    fn close(&mut self) -> Result<()>;
    /// False when `Controls::reset_to_defaults` must write nothing (the defaults are shown, not written): the
    /// V4L2 controls on Linux. The Python package's backend attribute `writes_defaults`.
    fn writes_defaults(&self) -> bool {
        true
    }
}

/// Read-only wrapper — rejects writes before they reach the inner backend and only counts the attempts. It is the first
/// safeguard that guarantees zero writes in a read-only check.
pub struct ReadOnly<B: Backend> {
    inner: B,
    attempts: Arc<AtomicU64>,
}

impl<B: Backend> ReadOnly<B> {
    pub fn new(inner: B) -> Self {
        ReadOnly {
            inner,
            attempts: Arc::new(AtomicU64::new(0)),
        }
    }

    /// Number of rejected write attempts (shared, so it stays visible after the wrapper is handed to Controls).
    pub fn attempts(&self) -> Arc<AtomicU64> {
        self.attempts.clone()
    }
}

impl<B: Backend> Backend for ReadOnly<B> {
    fn name(&self) -> &str {
        self.inner.name()
    }
    fn exposure_units(&self) -> &str {
        self.inner.exposure_units()
    }
    fn note(&self) -> &str {
        self.inner.note()
    }
    fn device_name(&self) -> &str {
        self.inner.device_name()
    }
    fn start(&mut self) -> Result<()> {
        self.inner.start()
    }
    fn supports(&mut self, key: &str) -> bool {
        self.inner.supports(key)
    }
    fn query(&mut self, key: &str) -> Result<Option<RawRange>> {
        self.inner.query(key)
    }
    fn read(&mut self, key: &str) -> Result<(i64, Option<bool>)> {
        self.inner.read(key)
    }
    fn write(&mut self, key: &str, _value: Option<i64>, _auto: Option<bool>) -> Result<()> {
        self.attempts.fetch_add(1, Ordering::SeqCst);
        Err(SdkError::Backend {
            what: format!("read-only backend: write to {key} refused"),
            hresult: None,
        })
    }
    fn close(&mut self) -> Result<()> {
        self.inner.close()
    }
    fn writes_defaults(&self) -> bool {
        self.inner.writes_defaults()
    }
}

/// Test double — same as Python `_ctrl_sim.SimBackend`. **Ranges and defaults are made-up values** (Python SIM_RANGES as is).
#[cfg(any(test, feature = "test-support"))]
pub mod mock {
    use super::*;
    use std::collections::{HashMap, HashSet};
    use std::sync::{Arc, Mutex, MutexGuard};
    use std::thread::ThreadId;

    pub const SIM_RANGES: [(&str, RawRange); 12] = {
        const fn r(minimum: i64, maximum: i64, default: i64, can_auto: bool) -> RawRange {
            RawRange {
                minimum,
                maximum,
                step: 1,
                default: Some(default),
                can_auto,
            }
        }
        [
            ("brightness", r(0, 100, 50, false)),
            ("contrast", r(0, 100, 50, false)),
            ("hue", r(0, 128, 64, false)),
            ("saturation", r(0, 128, 36, false)),
            ("sharpness", r(0, 100, 28, false)),
            ("gamma", r(1, 500, 50, false)),
            ("white_balance", r(0, 255, 128, true)),
            ("backlight_compensation", r(0, 96, 32, false)),
            ("gain", r(0, 255, 32, false)),
            ("power_line_frequency", r(0, 2, 2, false)),
            ("exposure", r(-11, -3, -5, true)),
            ("auto_exposure_priority", r(0, 1, 0, false)),
        ]
    };

    pub type Write = (String, Option<i64>, Option<bool>);

    #[derive(Debug, Default)]
    pub struct MockState {
        pub ranges: HashMap<String, RawRange>,
        pub values: HashMap<String, i64>,
        pub autos: HashMap<String, bool>,
        pub fail_keys: HashSet<String>,
        pub unranged: HashSet<String>,
        pub ignore_writes: HashSet<String>,
        pub fail_start: Option<String>,
        /// true: `writes_defaults()` is false, as on Linux (V4L2).
        pub defaults_not_written: bool,
        pub writes: Vec<Write>,
        pub threads: HashSet<ThreadId>,
        pub started: bool,
        pub closed: bool,
    }

    /// Clones see the same state — hand one to Controls and inspect it through the other in tests.
    #[derive(Clone)]
    pub struct MockBackend(Arc<Mutex<MockState>>);

    impl Default for MockBackend {
        fn default() -> Self {
            Self::new()
        }
    }

    impl MockBackend {
        pub fn new() -> Self {
            Self::with_ranges(
                SIM_RANGES
                    .iter()
                    .map(|(k, r)| (k.to_string(), *r))
                    .collect(),
            )
        }

        pub fn with_ranges(ranges: HashMap<String, RawRange>) -> Self {
            let values = ranges
                .iter()
                .map(|(k, r)| (k.clone(), r.default.unwrap_or(r.minimum)))
                .collect();
            let autos = ranges
                .iter()
                .filter(|(_, r)| r.can_auto)
                .map(|(k, _)| (k.clone(), false))
                .collect();
            MockBackend(Arc::new(Mutex::new(MockState {
                ranges,
                values,
                autos,
                ..Default::default()
            })))
        }

        pub fn st(&self) -> MutexGuard<'_, MockState> {
            self.0.lock().unwrap_or_else(|e| e.into_inner())
        }

        pub fn writes(&self) -> Vec<Write> {
            self.st().writes.clone()
        }

        fn touch(&self) -> MutexGuard<'_, MockState> {
            let mut s = self.st();
            s.threads.insert(std::thread::current().id());
            s
        }
    }

    impl Backend for MockBackend {
        fn name(&self) -> &str {
            "sim"
        }
        fn exposure_units(&self) -> &str {
            "log2_s_nominal"
        }
        fn note(&self) -> &str {
            "simulated controls — ranges and defaults are made up, not measured"
        }
        fn start(&mut self) -> Result<()> {
            let mut s = self.touch();
            if let Some(why) = s.fail_start.clone() {
                return Err(SdkError::Unavailable(why));
            }
            s.started = true;
            Ok(())
        }
        fn supports(&mut self, key: &str) -> bool {
            self.touch().ranges.contains_key(key)
        }
        fn query(&mut self, key: &str) -> Result<Option<RawRange>> {
            let s = self.touch();
            Ok(if s.unranged.contains(key) {
                None
            } else {
                s.ranges.get(key).copied()
            })
        }
        fn read(&mut self, key: &str) -> Result<(i64, Option<bool>)> {
            let s = self.touch();
            let v = *s.values.get(key).ok_or_else(|| SdkError::Backend {
                what: format!("no {key}"),
                hresult: None,
            })?;
            Ok((v, s.autos.get(key).copied()))
        }
        fn write(&mut self, key: &str, value: Option<i64>, auto: Option<bool>) -> Result<()> {
            let mut s = self.touch();
            if s.fail_keys.contains(key) {
                return Err(SdkError::Backend {
                    what: format!("simulated driver rejected {key}"),
                    hresult: None,
                });
            }
            s.writes.push((key.to_string(), value, auto));
            if s.ignore_writes.contains(key) {
                return Ok(());
            }
            if let Some(a) = auto {
                s.autos.insert(key.to_string(), a);
            }
            if let Some(v) = value {
                s.values.insert(key.to_string(), v);
            }
            Ok(())
        }
        fn close(&mut self) -> Result<()> {
            self.touch().closed = true;
            Ok(())
        }
        fn writes_defaults(&self) -> bool {
            !self.st().defaults_not_written
        }
    }
}

#[cfg(test)]
mod tests {
    use super::mock::*;
    use super::*;

    #[test]
    fn mock_starts_at_its_defaults_and_records_writes() {
        let m = MockBackend::new();
        let mut b: Box<dyn Backend> = Box::new(m.clone());
        b.start().unwrap();
        assert!(b.supports("gain") && !b.supports("iris"));
        assert_eq!(b.read("gain").unwrap(), (32, None));
        assert_eq!(b.read("exposure").unwrap(), (-5, Some(false)));
        b.write("gain", Some(28), None).unwrap();
        b.write("exposure", None, Some(true)).unwrap();
        assert_eq!(b.read("gain").unwrap(), (28, None));
        assert_eq!(b.read("exposure").unwrap(), (-5, Some(true)));
        assert_eq!(
            m.writes(),
            [
                ("gain".to_string(), Some(28), None),
                ("exposure".to_string(), None, Some(true))
            ]
        );
        b.close().unwrap();
        assert!(m.st().closed);
    }

    #[test]
    fn mock_can_fail_hide_ranges_ignore_writes_and_refuse_to_start() {
        let m = MockBackend::new();
        {
            let mut s = m.st();
            s.fail_keys.insert("gain".into());
            s.unranged.insert("contrast".into());
            s.ignore_writes.insert("brightness".into());
        }
        let mut b = m.clone();
        assert!(matches!(
            b.write("gain", Some(1), None),
            Err(SdkError::Backend { .. })
        ));
        assert_eq!(b.query("contrast").unwrap(), None);
        assert_eq!(
            b.query("gain").unwrap(),
            Some(RawRange {
                minimum: 0,
                maximum: 255,
                step: 1,
                default: Some(32),
                can_auto: false
            })
        );
        b.write("brightness", Some(99), None).unwrap();
        assert_eq!(b.read("brightness").unwrap().0, 50);
        assert_eq!(m.writes().len(), 1); // 받은 쓰기는 남는다(값만 안 바뀜) — 실패한 것은 남지 않는다
        m.st().fail_start = Some("no device".into());
        assert!(m.clone().start().is_err());
    }

    #[test]
    fn mock_remembers_which_threads_called_it() {
        let m = MockBackend::new();
        let mut b = m.clone();
        b.supports("gain");
        std::thread::spawn(move || {
            b.supports("gain");
        })
        .join()
        .unwrap();
        assert_eq!(m.st().threads.len(), 2);
    }
    #[test]
    fn a_read_only_backend_refuses_every_write_and_counts_the_attempts() {
        let m = MockBackend::new();
        let mut ro = ReadOnly::new(m.clone());
        let attempts = ro.attempts();
        ro.start().unwrap();
        assert_eq!(ro.read("gain").unwrap(), (32, None));
        assert_eq!(ro.query("gain").unwrap().unwrap().maximum, 255);
        assert!(ro.supports("gain"));
        assert_eq!(ro.name(), "sim");
        assert!(matches!(
            ro.write("gain", Some(28), None),
            Err(SdkError::Backend { .. })
        ));
        assert_eq!(attempts.load(std::sync::atomic::Ordering::SeqCst), 1);
        assert!(m.writes().is_empty()); // 안쪽 백엔드에는 닿지 않았다
        ro.close().unwrap();
        assert!(m.st().closed);
    }
}
