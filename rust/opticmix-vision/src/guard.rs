//! Write guard — process-wide (it carries over when the device is reopened and across several Controls). Ported from Python `controls`:
//! `_guard`·`_last_group`·`_last_any`·`_counts`·`write_counts`·`reset_write_counts`·`_not_before`·`_record`.
//!
//! **This guard does not guarantee safety.** After writes, one eye's stream has occasionally stopped,
//! and it has not been shown whether the count or the rate is the cause (2026-09-24). The safe pattern is 'write once at startup'.
//! The write log (`OPTICMIX_WRITE_LOG`) is there to keep evidence of the cumulative count across processes — off by default.

use std::collections::{BTreeMap, HashMap};
use std::io::Write as _;
use std::path::{Path, PathBuf};
use std::sync::{Mutex, MutexGuard, OnceLock};
use std::time::{Instant, SystemTime, UNIX_EPOCH};

use crate::spec::{VENDOR_MIN_COMMAND_GAP_S, WARN_AFTER_TOTAL};

/// Guard settings. The Python wrapper passes them with `set_config` before every write request, because tests may
/// monkeypatch the module globals (`GROUP_MIN_INTERVAL_S`·`GLOBAL_MIN_GAP_S`·`WARN_AFTER_TOTAL`).
#[derive(Debug, Clone, PartialEq)]
pub struct GuardConfig {
    /// Minimum seconds between consecutive writes to the same group (= the same UVC property).
    pub group_intervals: HashMap<String, f64>,
    /// Minimum seconds between any two writes, whatever the control.
    pub global_gap_s: f64,
    /// Warn when the write total reaches this number and every this-many writes after (0 = off).
    pub warn_after_total: u64,
    /// Write log path. None = no log.
    pub ledger: Option<PathBuf>,
}

/// Write log environment variable — logging is on only when it is set (off by default).
pub const WRITE_LOG_ENV: &str = "OPTICMIX_WRITE_LOG";

impl Default for GuardConfig {
    fn default() -> Self {
        GuardConfig {
            group_intervals: HashMap::from([("exposure".to_string(), VENDOR_MIN_COMMAND_GAP_S)]),
            global_gap_s: VENDOR_MIN_COMMAND_GAP_S,
            warn_after_total: WARN_AFTER_TOTAL,
            ledger: std::env::var_os(WRITE_LOG_ENV)
                .filter(|v| !v.is_empty())
                .map(PathBuf::from),
        }
    }
}

#[derive(Default)]
struct State {
    last_group: HashMap<String, f64>,
    last_any: Option<f64>,
    counts: BTreeMap<String, u64>,
    config: Option<GuardConfig>,
}

fn state() -> MutexGuard<'static, State> {
    static S: OnceLock<Mutex<State>> = OnceLock::new();
    // 독이 든 잠금(다른 스레드 패닉)이어도 카운터는 계속 쓴다 — 쓰기 횟수 기록을 잃지 않는 쪽.
    S.get_or_init(|| Mutex::new(State::default()))
        .lock()
        .unwrap_or_else(|e| e.into_inner())
}

/// Process monotonic clock (seconds). The guard and Controls share this clock.
pub fn now_s() -> f64 {
    static T0: OnceLock<Instant> = OnceLock::new();
    T0.get_or_init(Instant::now).elapsed().as_secs_f64()
}

pub fn set_config(cfg: GuardConfig) {
    state().config = Some(cfg);
}

pub fn config() -> GuardConfig {
    let mut s = state();
    s.config.get_or_insert_with(GuardConfig::default).clone()
}

/// Writes this process actually made — per group plus `_total`. Writes skipped because the value was the same are not counted.
pub fn write_counts() -> BTreeMap<String, u64> {
    let mut out = state().counts.clone();
    let total = out.values().sum();
    out.insert("_total".into(), total);
    out
}

/// Call only when the USB was re-plugged and the device state is back to its initial state.
pub fn reset_write_counts() {
    let mut s = state();
    s.counts.clear();
    s.last_group.clear();
    s.last_any = None;
}

/// Earliest monotonic time at which this group may be written next.
pub fn not_before(group: &str) -> f64 {
    let mut s = state();
    let cfg = s.config.get_or_insert_with(GuardConfig::default).clone();
    let mut t: f64 = 0.0;
    if let Some(last) = s.last_group.get(group) {
        t = t.max(last + cfg.group_intervals.get(group).copied().unwrap_or(0.0));
    }
    if let Some(last) = s.last_any {
        t = t.max(last + cfg.global_gap_s);
    }
    t
}

/// Records one write → (gap since the previous write, total).
pub fn record(group: &str, now: f64) -> (Option<f64>, u64) {
    let mut s = state();
    let gap = s.last_any.map(|last| now - last);
    s.last_any = Some(now);
    s.last_group.insert(group.to_string(), now);
    *s.counts.entry(group.to_string()).or_insert(0) += 1;
    (gap, s.counts.values().sum())
}

/// Whether the total hit a warning step (Python: total >= W and (total - W) % W == 0).
pub fn should_warn(total: u64, warn_after_total: u64) -> bool {
    warn_after_total > 0
        && total >= warn_after_total
        && (total - warn_after_total).is_multiple_of(warn_after_total)
}

/// One write log line — for every write that actually went out (skipped and failed writes are not logged).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct LedgerEntry<'a> {
    pub backend: &'a str,
    pub device_name: &'a str,
    pub group: &'a str,
    pub value: Option<i64>,
    pub auto: Option<bool>,
    pub gap_s: Option<f64>,
    pub total_this_process: u64,
    pub readback_value: Option<i64>,
    pub readback_auto: Option<bool>,
    pub kept: Option<bool>,
}

fn js(s: &str) -> String {
    let mut o = String::with_capacity(s.len() + 2);
    o.push('"');
    for ch in s.chars() {
        match ch {
            '"' => o.push_str("\\\""),
            '\\' => o.push_str("\\\\"),
            c if (c as u32) < 0x20 => o.push_str(&format!("\\u{:04x}", c as u32)),
            c => o.push(c),
        }
    }
    o.push('"');
    o
}

fn opt<T: ToString>(v: Option<T>) -> String {
    v.map_or_else(|| "null".to_string(), |x| x.to_string())
}

/// Appends one line to the log file.
pub fn append_ledger(path: &Path, e: &LedgerEntry) -> std::io::Result<()> {
    let ts = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0);
    let line = format!(
        "{{\"ts\":{ts:.6},\"pid\":{},\"backend\":{},\"device_name\":{},\"group\":{},\"value\":{},\"auto\":{},\"gap_s\":{},\
         \"total_this_process\":{},\"readback_value\":{},\"readback_auto\":{},\"kept\":{}}}\n",
        std::process::id(),
        js(e.backend),
        js(e.device_name),
        js(e.group),
        opt(e.value),
        opt(e.auto),
        opt(e.gap_s),
        e.total_this_process,
        opt(e.readback_value),
        opt(e.readback_auto),
        opt(e.kept),
    );
    let mut f = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)?;
    f.write_all(line.as_bytes())
}

/// Writes one line if the settings have a log path; otherwise does nothing.
pub fn ledger_record(e: &LedgerEntry) -> std::io::Result<()> {
    match config().ledger {
        Some(p) => append_ledger(&p, e),
        None => Ok(()),
    }
}

/// Tests that touch the global guard queue up on this lock.
#[cfg(test)]
pub(crate) fn test_lock() -> MutexGuard<'static, ()> {
    static L: Mutex<()> = Mutex::new(());
    L.lock().unwrap_or_else(|e| e.into_inner())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fresh(groups: &[(&str, f64)], global: f64) {
        set_config(GuardConfig {
            group_intervals: groups.iter().map(|(g, s)| (g.to_string(), *s)).collect(),
            global_gap_s: global,
            warn_after_total: 3,
            ledger: None,
        });
        reset_write_counts();
    }

    #[test]
    fn guard_state_counts_and_spacing() {
        let _l = test_lock();
        fresh(&[("exposure", 5.0)], 1.0);
        assert_eq!(not_before("exposure"), 0.0);
        let (gap, total) = record("exposure", 100.0);
        assert_eq!((gap, total), (None, 1));
        assert_eq!(not_before("exposure"), 105.0);
        assert_eq!(not_before("gain"), 101.0);
        let (gap, total) = record("gain", 101.5);
        assert!((gap.unwrap() - 1.5).abs() < 1e-9);
        assert_eq!(total, 2);
        let c = write_counts();
        assert_eq!((c["exposure"], c["gain"], c["_total"]), (1, 1, 2));
        reset_write_counts();
        assert_eq!(
            write_counts().into_iter().collect::<Vec<_>>(),
            [("_total".to_string(), 0)]
        );
        set_config(GuardConfig::default());
    }

    #[test]
    fn default_spacing_is_the_vendor_minimum_between_exposure_commands() {
        let d = GuardConfig::default();
        assert_eq!(d.group_intervals.len(), 1);
        assert_eq!(d.group_intervals["exposure"], 0.012);
        assert_eq!(d.global_gap_s, 0.012);
        assert_eq!(d.warn_after_total, 3);
    }

    #[test]
    fn warn_on_third_and_every_third_after() {
        let hits: Vec<u64> = (1..=10).filter(|t| should_warn(*t, 3)).collect();
        assert_eq!(hits, [3, 6, 9]);
        assert!(!should_warn(5, 0)); // 0 = 경고 끔(0 으로 나누지 않는다)
    }

    #[test]
    fn write_ledger_appends_one_json_line_per_real_write() {
        let dir = std::env::temp_dir().join(format!("ov_ledger_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("w.jsonl");
        let _ = std::fs::remove_file(&path);
        let e = LedgerEntry {
            backend: "sim",
            device_name: "WN \"Dual\" Camera",
            group: "gain",
            value: Some(28),
            auto: None,
            gap_s: None,
            total_this_process: 1,
            readback_value: Some(28),
            readback_auto: None,
            kept: Some(true),
        };
        append_ledger(&path, &e).unwrap();
        append_ledger(
            &path,
            &LedgerEntry {
                gap_s: Some(0.5),
                total_this_process: 2,
                kept: Some(false),
                ..e
            },
        )
        .unwrap();
        let text = std::fs::read_to_string(&path).unwrap();
        let lines: Vec<_> = text.lines().collect();
        assert_eq!(lines.len(), 2);
        for k in [
            "\"ts\":",
            "\"pid\":",
            "\"backend\":\"sim\"",
            "\"device_name\":\"WN \\\"Dual\\\" Camera\"",
            "\"group\":\"gain\"",
            "\"value\":28",
            "\"auto\":null",
            "\"gap_s\":null",
            "\"total_this_process\":1",
            "\"readback_value\":28",
            "\"readback_auto\":null",
            "\"kept\":true",
        ] {
            assert!(lines[0].contains(k), "{k} in {}", lines[0]);
        }
        assert!(lines[1].contains("\"gap_s\":0.5") && lines[1].contains("\"kept\":false"));
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn no_ledger_path_means_no_file() {
        let _l = test_lock();
        set_config(GuardConfig {
            ledger: None,
            ..GuardConfig::default()
        });
        assert_eq!(config().ledger, None);
        assert!(ledger_record(&LedgerEntry {
            backend: "sim",
            device_name: "",
            group: "gain",
            value: Some(1),
            auto: None,
            gap_s: None,
            total_this_process: 1,
            readback_value: None,
            readback_auto: None,
            kept: None,
        })
        .is_ok());
        set_config(GuardConfig::default());
    }

    #[test]
    fn monotonic_clock_moves_forward() {
        let a = now_s();
        let b = now_s();
        assert!(b >= a && a >= 0.0);
    }
}
