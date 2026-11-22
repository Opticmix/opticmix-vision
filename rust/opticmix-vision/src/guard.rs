//! 쓰기 가드 — 프로세스 전역(장치를 다시 열어도, Controls 가 여럿이어도 이어진다). Python `controls` 의
//! `_guard`·`_last_group`·`_last_any`·`_counts`·`write_counts`·`reset_write_counts`·`_not_before`·`_record` 를 옮겼다.
//!
//! **이 가드는 안전을 보장하지 않는다.** 쓰기 뒤 가끔 한쪽 눈 스트림이 멈췄고,
//! 횟수·속도 어느 쪽이 원인인지 증명되지 않았다(2026-09-24). 안전한 사용법은 '시작할 때 한 번만 쓰기'다.
//! 쓰기 원장(`OPTICMIX_WRITE_LOG`)은 프로세스를 넘는 누적 횟수의 증거를 남기려는 것 — 기본은 꺼짐.

use std::collections::{BTreeMap, HashMap};
use std::io::Write as _;
use std::path::{Path, PathBuf};
use std::sync::{Mutex, MutexGuard, OnceLock};
use std::time::{Instant, SystemTime, UNIX_EPOCH};

use crate::spec::{VENDOR_MIN_COMMAND_GAP_S, WARN_AFTER_TOTAL};

/// 가드 설정. Python 래퍼는 모듈 전역(`GROUP_MIN_INTERVAL_S`·`GLOBAL_MIN_GAP_S`·`WARN_AFTER_TOTAL`)을
/// 시험이 monkeypatch 할 수 있으므로 쓰기 요청 직전마다 `set_config` 로 넘긴다.
#[derive(Debug, Clone, PartialEq)]
pub struct GuardConfig {
    /// 같은 그룹(= 같은 UVC 속성) 연속 쓰기 사이 최소 초.
    pub group_intervals: HashMap<String, f64>,
    /// 어느 제어든 두 쓰기 사이 최소 초.
    pub global_gap_s: f64,
    /// 쓰기 합계가 이 수에 닿을 때와 그 뒤 이 수마다 경고(0 = 끔).
    pub warn_after_total: u64,
    /// 쓰기 원장 경로. None 이면 남기지 않는다.
    pub ledger: Option<PathBuf>,
}

/// 쓰기 원장 환경변수 — 있을 때만 켜진다(기본 꺼짐).
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

/// 프로세스 단조 시계(초). 가드·Controls 가 같은 시계를 쓴다.
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

/// 이 프로세스가 실제로 쓴 횟수 — 그룹별 + `_total`. 같은 값이라 건너뛴 것은 세지 않는다.
pub fn write_counts() -> BTreeMap<String, u64> {
    let mut out = state().counts.clone();
    let total = out.values().sum();
    out.insert("_total".into(), total);
    out
}

/// USB 를 다시 꽂아 장치 상태가 처음으로 돌아간 경우에만 부른다.
pub fn reset_write_counts() {
    let mut s = state();
    s.counts.clear();
    s.last_group.clear();
    s.last_any = None;
}

/// 이 그룹에 다음으로 쓸 수 있는 가장 이른 단조 시각.
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

/// 쓰기 한 번을 남긴다 → (직전 쓰기로부터 간격, 합계).
pub fn record(group: &str, now: f64) -> (Option<f64>, u64) {
    let mut s = state();
    let gap = s.last_any.map(|last| now - last);
    s.last_any = Some(now);
    s.last_group.insert(group.to_string(), now);
    *s.counts.entry(group.to_string()).or_insert(0) += 1;
    (gap, s.counts.values().sum())
}

/// 합계가 경고 주기에 닿았는가(Python: total >= W and (total - W) % W == 0).
pub fn should_warn(total: u64, warn_after_total: u64) -> bool {
    warn_after_total > 0
        && total >= warn_after_total
        && (total - warn_after_total).is_multiple_of(warn_after_total)
}

/// 쓰기 원장 한 줄 — 실제로 쓴 쓰기마다(건너뛴 것·실패한 것은 남기지 않는다).
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

/// 원장 파일에 한 줄 덧붙인다.
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

/// 설정에 원장 경로가 있으면 한 줄 남긴다. 없으면 아무것도 하지 않는다.
pub fn ledger_record(e: &LedgerEntry) -> std::io::Result<()> {
    match config().ledger {
        Some(p) => append_ledger(&p, e),
        None => Ok(()),
    }
}

/// 전역 가드를 건드리는 시험은 이 잠금으로 줄을 세운다.
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
