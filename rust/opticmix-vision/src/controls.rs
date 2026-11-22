//! 카메라 하나의 제어 전부(Python `controls.Controls`). 읽기는 바로 하고, 쓰기는 워커 하나가 가드를 지키며 순서대로 한다.
//! 백엔드(COM 등)는 워커 스레드 한 곳에서만 불린다 — 시작·닫기 포함.
//!
//! **쓰기는 위험하다**(guard.rs 머리말). 안전한 사용법은 '시작할 때 한 번만 쓰기'다: `set_many` 한 번 → `wait`.

use std::collections::{HashMap, VecDeque};
use std::sync::mpsc;
use std::sync::{Arc, Condvar, Mutex, MutexGuard};
use std::thread::{self, ThreadId};
use std::time::{Duration, Instant};

use crate::backend::{Backend, RawRange};
use crate::convert::{from_device, py_eq, same, to_device, DevValue, UserValue};
use crate::error::{Result, SdkError};
use crate::guard::{self, LedgerEntry};
use crate::spec::{self, auto_name, ControlSpec, Effect, Kind, Role, CONTROLS};

/// describe() 한 줄. 모르는 값은 None — 지어내지 않는다.
#[derive(Debug, Clone, PartialEq)]
pub struct ControlInfo {
    pub name: &'static str,
    pub kind: Kind,
    pub current: Option<UserValue>,
    pub minimum: Option<i64>,
    pub maximum: Option<i64>,
    pub step: Option<i64>,
    pub default: Option<UserValue>,
    pub choices: Option<Vec<&'static str>>,
    /// "raw" | "log2_s_nominal" | "100us"
    pub units: String,
    pub effect: Effect,
    pub ledger: Option<&'static str>,
    pub group: &'static str,
    pub note: &'static str,
}

/// 요청 하나의 상태(Python `Status`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Status {
    Written,
    SkippedSame,
    Pending,
    Superseded,
    Cancelled,
    Failed,
}

impl Status {
    pub fn as_str(self) -> &'static str {
        match self {
            Status::Written => "written",
            Status::SkippedSame => "skipped_same",
            Status::Pending => "pending",
            Status::Superseded => "superseded",
            Status::Cancelled => "cancelled",
            Status::Failed => "failed",
        }
    }
}

/// 요청 하나의 결과. ticket 으로 나중에 최종 결과를 찾는다(wait/result).
/// written 은 드라이버가 받았다는 뜻일 뿐이다 — 카메라가 값을 지켰는지는 readback/kept 를 본다
/// (2026-09-24: 게인 8 을 드라이버가 받았는데 다시 읽으니 0 이었다).
#[derive(Debug, Clone, PartialEq)]
pub struct WriteResult {
    pub name: String,
    pub requested: UserValue,
    pub status: Status,
    pub due_in_s: Option<f64>,
    pub error: Option<String>,
    pub ticket: u64,
    /// written 일 때 쓴 직후 다시 읽은 값(못 읽었으면 None).
    pub readback: Option<UserValue>,
}

impl WriteResult {
    fn new(name: &str, requested: &UserValue, status: Status, ticket: u64) -> WriteResult {
        WriteResult {
            name: name.to_string(),
            requested: requested.clone(),
            status,
            due_in_s: None,
            error: None,
            ticket,
            readback: None,
        }
    }

    /// 카메라가 요청한 값을 지켰는가. written 이 아니거나 다시 읽지 못했으면 None.
    pub fn kept(&self) -> Option<bool> {
        if self.status != Status::Written {
            return None;
        }
        self.readback.as_ref().map(|rb| py_eq(rb, &self.requested))
    }
}

/// 누적 쓰기 경고 문안(Python warnings.warn 원문).
pub fn warn_total_message(total: u64) -> String {
    format!(
        "{total} camera control writes in this process. On the units we have tested, a control \
         write has occasionally been followed by one eye's stream stopping until the USB cable \
         was re-inserted. Write once at startup where you can."
    )
}

/// 워커가 알리는 일. Python 래퍼는 이것을 `logging`("opticmix_vision.controls") 과 `warnings.warn` 으로 바꾼다.
#[derive(Debug, Clone, PartialEq)]
pub enum Event {
    /// 실제로 쓴 쓰기(가드가 켜졌을 때만). gap = 직전 쓰기로부터 초(첫 쓰기면 None).
    Wrote {
        group: String,
        value: Option<i64>,
        auto: Option<bool>,
        gap: Option<f64>,
        total: u64,
    },
    /// 드라이버가 거부한 쓰기.
    WriteFailed {
        group: String,
        value: Option<i64>,
        auto: Option<bool>,
        error: String,
    },
    /// 드라이버는 받았는데 다시 읽으니 다른 값.
    NotKept {
        name: String,
        requested: UserValue,
        readback: UserValue,
    },
    /// 이 프로세스의 쓰기 합계가 경고 주기에 닿았다(Python 은 RuntimeWarning).
    WarnTotal { total: u64 },
    /// 닫으며 보내지 않은 쓰기.
    Cancelled { name: String, requested: UserValue },
    /// 쓰기 원장에 덧붙이지 못했다(쓰기 자체는 끝났다).
    LedgerFailed { error: String },
}

fn opt_repr<T: ToString>(v: Option<T>) -> String {
    v.map_or_else(|| "None".to_string(), |x| x.to_string())
}

fn bool_repr(v: Option<bool>) -> String {
    match v {
        None => "None".into(),
        Some(true) => "True".into(),
        Some(false) => "False".into(),
    }
}

impl Event {
    /// 경고 수준인가(아니면 info).
    pub fn is_warning(&self) -> bool {
        !matches!(self, Event::Wrote { .. } | Event::Cancelled { .. })
    }

    /// Python 로그 문안과 같은 한 줄.
    pub fn message(&self) -> String {
        match self {
            Event::Wrote {
                group,
                value,
                auto,
                gap,
                total,
            } => format!(
                "write {group} value={} auto={} gap_since_previous={} total_this_process={total}",
                opt_repr(*value),
                bool_repr(*auto),
                gap.map_or_else(|| "first".to_string(), |g| format!("{g:.3}s"))
            ),
            Event::WriteFailed {
                group,
                value,
                auto,
                error,
            } => {
                format!(
                    "write {group} value={} auto={} failed: {error}",
                    opt_repr(*value),
                    bool_repr(*auto)
                )
            }
            Event::NotKept {
                name,
                requested,
                readback,
            } => {
                format!(
                    "{name}: wrote {}, but the camera reads back {}",
                    requested.repr(),
                    readback.repr()
                )
            }
            Event::WarnTotal { total } => warn_total_message(*total),
            Event::Cancelled { name, requested } => {
                format!(
                    "cancelled pending write {name}={} (controls closed)",
                    requested.repr()
                )
            }
            Event::LedgerFailed { error } => format!("could not append to the write log: {error}"),
        }
    }
}

/// 이벤트 콜백. 워커 스레드(또는 close 를 부른 스레드)에서, 내부 잠금 밖에서 불린다.
pub type EventHook = Box<dyn Fn(&Event) + Send + Sync>;

type Job = Box<dyn FnOnce(&mut dyn Backend) + Send>;

/// 결과 보관 상한(Python `_MAX_KEPT`).
const MAX_KEPT: usize = 4096;

struct Req {
    role: Role,
    ticket: u64,
    name: &'static str,
    requested: UserValue,
}

struct Pending {
    group: &'static str,
    seq: u64,
    value: Option<i64>,
    auto: Option<bool>,
    /// 역할별 요청 하나씩, 처음 들어온 순서(Python dict 처럼 바꿔도 자리는 그대로).
    reqs: Vec<Req>,
}

struct Inner {
    calls: VecDeque<Job>,
    pending: HashMap<&'static str, Pending>,
    busy: bool,
    closed: bool,
    finals: HashMap<u64, WriteResult>,
    final_order: VecDeque<u64>,
    reqs: HashMap<u64, (String, UserValue)>,
    unflushed: Vec<WriteResult>,
    next_ticket: u64,
    next_seq: u64,
}

impl Inner {
    fn ticket(&mut self) -> u64 {
        let t = self.next_ticket;
        self.next_ticket += 1;
        t
    }

    fn finish(&mut self, r: WriteResult) {
        self.reqs.remove(&r.ticket);
        if self.finals.insert(r.ticket, r.clone()).is_none() {
            self.final_order.push_back(r.ticket);
        }
        while self.final_order.len() > MAX_KEPT {
            if let Some(old) = self.final_order.pop_front() {
                self.finals.remove(&old);
            }
        }
        self.unflushed.push(r);
        if self.unflushed.len() > MAX_KEPT {
            let extra = self.unflushed.len() - MAX_KEPT;
            self.unflushed.drain(..extra);
        }
    }
}

struct Shared {
    m: Mutex<Inner>,
    cv: Condvar,
    guard: bool,
    hook: Option<EventHook>,
    backend_name: String,
    device_name: String,
}

impl Shared {
    fn lock(&self) -> MutexGuard<'_, Inner> {
        self.m.lock().unwrap_or_else(|e| e.into_inner())
    }

    fn wait_for<'a>(&self, g: MutexGuard<'a, Inner>, left: Option<f64>) -> MutexGuard<'a, Inner> {
        match left {
            Some(s) => {
                self.cv
                    .wait_timeout(g, Duration::from_secs_f64(s.max(0.0)))
                    .unwrap_or_else(|e| e.into_inner())
                    .0
            }
            None => self.cv.wait(g).unwrap_or_else(|e| e.into_inner()),
        }
    }

    fn emit(&self, e: Event) {
        if let Some(h) = &self.hook {
            h(&e);
        }
    }

    fn due_at(&self, group: &str) -> f64 {
        if self.guard {
            guard::not_before(group)
        } else {
            0.0
        }
    }

    fn next_due_in(&self, g: &Inner) -> Option<f64> {
        let now = guard::now_s();
        g.pending
            .keys()
            .map(|k| self.due_at(k))
            .reduce(f64::min)
            .map(|t| t - now)
    }

    fn pop_due(&self, g: &mut Inner) -> Pending {
        let group = g
            .pending
            .values()
            .map(|p| (self.due_at(p.group), p.seq, p.group))
            .min_by(|a, b| a.0.total_cmp(&b.0).then(a.1.cmp(&b.1)))
            .map(|x| x.2)
            .expect("pending is not empty");
        g.pending.remove(group).expect("present")
    }
}

/// 대기 중인 쓰기 하나를 실행한다(워커 스레드).
fn execute(sh: &Shared, backend: &mut dyn Backend, pend: Pending) {
    let group = pend.group;
    let (cur_v, cur_a) = match backend.read(group) {
        Ok((v, a)) => (Some(v), a),
        Err(_) => (None, None), // 못 읽으면 비교 없이 쓴다
    };
    let mut error = None;
    let mut readback = None;
    let status = if same(pend.value, pend.auto, cur_v, cur_a) {
        Status::SkippedSame
    } else {
        let now = guard::now_s();
        let value = if pend.auto == Some(true) {
            None
        } else {
            pend.value
        };
        match backend.write(group, value, pend.auto) {
            Err(e) => {
                let msg = format!("{}: {e}", e.python_type());
                sh.emit(Event::WriteFailed {
                    group: group.into(),
                    value: pend.value,
                    auto: pend.auto,
                    error: msg.clone(),
                });
                error = Some(msg);
                Status::Failed
            }
            Ok(()) => {
                readback = backend.read(group).ok(); // 드라이버가 받았다고 카메라가 지킨 것은 아니다
                if sh.guard {
                    let (gap, total) = guard::record(group, now);
                    sh.emit(Event::Wrote {
                        group: group.into(),
                        value: pend.value,
                        auto: pend.auto,
                        gap,
                        total,
                    });
                    if guard::should_warn(total, guard::config().warn_after_total) {
                        sh.emit(Event::WarnTotal { total });
                    }
                    let entry = LedgerEntry {
                        backend: &sh.backend_name,
                        device_name: &sh.device_name,
                        group,
                        value,
                        auto: pend.auto,
                        gap_s: gap,
                        total_this_process: total,
                        readback_value: readback.map(|r| r.0),
                        readback_auto: readback.and_then(|r| r.1),
                        kept: readback.map(|(v, a)| same(value, pend.auto, Some(v), a)),
                    };
                    if let Err(e) = guard::ledger_record(&entry) {
                        sh.emit(Event::LedgerFailed {
                            error: e.to_string(),
                        });
                    }
                }
                Status::Written
            }
        }
    };
    let mut results = Vec::with_capacity(pend.reqs.len());
    for q in &pend.reqs {
        let rb = match (status, readback) {
            (Status::Written, Some((v, a))) => {
                from_device(spec::spec(q.name).expect("in table"), Some(v), a)
            }
            _ => None,
        };
        let r = WriteResult {
            error: error.clone(),
            readback: rb,
            ..WriteResult::new(q.name, &q.requested, status, q.ticket)
        };
        if r.kept() == Some(false) {
            if let Some(rb) = &r.readback {
                sh.emit(Event::NotKept {
                    name: q.name.into(),
                    requested: q.requested.clone(),
                    readback: rb.clone(),
                });
            }
        }
        results.push(r);
    }
    let mut g = sh.lock();
    for r in results {
        g.finish(r);
    }
    g.busy = false;
    sh.cv.notify_all();
}

fn worker_loop(sh: &Shared, backend: &mut dyn Backend) {
    loop {
        let mut job = None;
        let mut pend = None;
        {
            let mut g = sh.lock();
            loop {
                if let Some(j) = g.calls.pop_front() {
                    job = Some(j);
                    break;
                }
                if g.closed {
                    break;
                }
                let wait = sh.next_due_in(&g);
                if let Some(w) = wait {
                    if w <= 0.0 {
                        pend = Some(sh.pop_due(&mut g));
                        g.busy = true;
                        break;
                    }
                }
                g = sh.wait_for(g, wait);
            }
        }
        if let Some(j) = job {
            j(backend);
        } else if let Some(p) = pend {
            execute(sh, backend, p);
        } else {
            break;
        }
    }
}

/// 한 그룹(키)에 대한 이번 호출의 요청들(역할별 하나, 들어온 순서).
type Roles = Vec<(Role, &'static str, UserValue, DevValue)>;

/// 카메라 하나의 제어. `guard=false` 는 합성 카메라용이다 — 간격·카운터를 쓰지 않는다(실제 장치에는 쓰지 말 것).
pub struct Controls {
    sh: Arc<Shared>,
    worker: Mutex<Option<(thread::JoinHandle<()>, mpsc::Receiver<()>)>>,
    worker_id: ThreadId,
    backend_note: String,
    exposure_units: String,
    supported: Mutex<HashMap<String, bool>>,
    ranges: Mutex<HashMap<String, Option<RawRange>>>,
}

impl Controls {
    /// 백엔드를 워커 스레드에서 시작한다. 시작 실패는 `Unavailable("<백엔드 이름>: <원인>")`.
    pub fn new(
        mut backend: Box<dyn Backend>,
        guard: bool,
        note: &str,
        on_event: Option<EventHook>,
    ) -> Result<Controls> {
        let backend_name = backend.name().to_string();
        let exposure_units = backend.exposure_units().to_string();
        let backend_note = [backend.note(), note]
            .iter()
            .filter(|x| !x.is_empty())
            .cloned()
            .collect::<Vec<_>>()
            .join("; ");
        let sh = Arc::new(Shared {
            m: Mutex::new(Inner {
                calls: VecDeque::new(),
                pending: HashMap::new(),
                busy: false,
                closed: false,
                finals: HashMap::new(),
                final_order: VecDeque::new(),
                reqs: HashMap::new(),
                unflushed: Vec::new(),
                next_ticket: 1,
                next_seq: 0,
            }),
            cv: Condvar::new(),
            guard,
            hook: on_event,
            backend_name: backend_name.clone(),
            device_name: backend.device_name().to_string(),
        });
        let (start_tx, start_rx) = mpsc::channel::<Result<()>>();
        let (done_tx, done_rx) = mpsc::channel::<()>();
        let sh2 = sh.clone();
        let handle = thread::Builder::new()
            .name(format!("opticmix-controls-{backend_name}"))
            .spawn(move || {
                match backend.start() {
                    Err(e) => {
                        let _ = start_tx.send(Err(e));
                    }
                    Ok(()) => {
                        let _ = start_tx.send(Ok(()));
                        worker_loop(&sh2, backend.as_mut());
                        let _ = backend.close();
                    }
                }
                let _ = done_tx.send(());
            })
            .map_err(|e| SdkError::Unavailable(format!("{backend_name}: {e}")))?;
        let worker_id = handle.thread().id();
        match start_rx.recv() {
            Ok(Ok(())) => {}
            Ok(Err(e)) => {
                let _ = done_rx.recv_timeout(Duration::from_secs(5));
                return Err(SdkError::Unavailable(format!("{backend_name}: {e}")));
            }
            Err(_) => {
                return Err(SdkError::Unavailable(format!(
                    "{backend_name}: the controls worker stopped"
                )))
            }
        }
        Ok(Controls {
            sh,
            worker: Mutex::new(Some((handle, done_rx))),
            worker_id,
            backend_note,
            exposure_units,
            supported: Mutex::new(HashMap::new()),
            ranges: Mutex::new(HashMap::new()),
        })
    }

    pub fn backend_name(&self) -> &str {
        &self.sh.backend_name
    }

    pub fn backend_note(&self) -> &str {
        &self.backend_note
    }

    pub fn closed(&self) -> bool {
        self.sh.lock().closed
    }

    // ---- 백엔드 호출 ---------------------------------------------------------------------------

    /// 백엔드 호출은 워커에서만 — COM 같은 스레드 규칙을 지키려고. 결과가 올 때까지 기다린다.
    fn call<R: Send + 'static>(
        &self,
        f: impl FnOnce(&mut dyn Backend) -> R + Send + 'static,
    ) -> Result<R> {
        if thread::current().id() == self.worker_id {
            // 워커 안에서 다시 부르면 자기 자신을 기다리게 된다 — 막는다.
            return Err(SdkError::Unavailable(
                "controls called from their own worker thread".into(),
            ));
        }
        let (tx, rx) = mpsc::channel();
        {
            let mut g = self.sh.lock();
            if g.closed {
                return Err(SdkError::Closed);
            }
            g.calls.push_back(Box::new(move |b: &mut dyn Backend| {
                let _ = tx.send(f(b));
            }));
            self.sh.cv.notify_all();
        }
        rx.recv()
            .map_err(|_| SdkError::Unavailable("the controls worker stopped".into()))
    }

    fn supports(&self, key: &str) -> Result<bool> {
        if let Some(v) = self
            .supported
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .get(key)
        {
            return Ok(*v);
        }
        let k = key.to_string();
        let v = self.call(move |b| b.supports(&k))?;
        self.supported
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .insert(key.to_string(), v);
        Ok(v)
    }

    /// 장치 범위. 모르면 None(지어내지 않는다). 닫힘·연결 실패는 그대로 올린다.
    pub(crate) fn range(&self, key: &str) -> Result<Option<RawRange>> {
        if let Some(v) = self
            .ranges
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .get(key)
        {
            return Ok(*v);
        }
        let k = key.to_string();
        let v = match self.call(move |b| b.query(&k))? {
            Ok(r) => r,
            Err(e @ (SdkError::Unavailable(_) | SdkError::Closed)) => return Err(e),
            Err(_) => None,
        };
        self.ranges
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .insert(key.to_string(), v);
        Ok(v)
    }

    pub(crate) fn read(&self, key: &str) -> Result<(i64, Option<bool>)> {
        let k = key.to_string();
        self.call(move |b| b.read(&k))?
    }

    pub(crate) fn spec(&self, name: &str) -> Result<&'static ControlSpec> {
        match spec::spec(name) {
            Some(s) if self.supports(s.key)? => Ok(s),
            _ => Err(SdkError::UnknownControl {
                name: name.to_string(),
                have: self.names()?.into_iter().map(String::from).collect(),
            }),
        }
    }

    /// 이 카메라에서 쓸 수 있는 제어 이름 — 이름 표 순서, 백엔드가 지원하는 것만.
    pub fn names(&self) -> Result<Vec<&'static str>> {
        let mut out = Vec::new();
        for c in CONTROLS.iter() {
            if self.supports(c.key)? {
                out.push(c.name);
            }
        }
        Ok(out)
    }

    // ---- 쓰기 ----------------------------------------------------------------------------------

    /// 기다리지 않는다. 결과는 pending / skipped_same. 최종 결과는 wait()·result()·flush().
    pub fn set(&self, name: &str, value: UserValue) -> Result<WriteResult> {
        Ok(self.set_many(&[(name.to_string(), value)])?.remove(0))
    }

    /// 같은 그룹(예: auto_exposure + exposure)은 한 번의 쓰기로 합친다. 검증이 하나라도 실패하면 아무것도 넣지 않는다.
    pub fn set_many(&self, values: &[(String, UserValue)]) -> Result<Vec<WriteResult>> {
        if self.closed() {
            return Err(SdkError::Closed);
        }
        let mut plan: Vec<(&'static str, Roles)> = Vec::new();
        for (name, requested) in values {
            let s = self.spec(name)?;
            let dv = to_device(s, requested)?;
            if let (Role::Value, Kind::Int | Kind::Menu, DevValue::Value(v)) = (s.role, s.kind, dv)
            {
                if let Some(r) = self.range(s.key)? {
                    if !(r.minimum <= v && v <= r.maximum) {
                        return Err(SdkError::InvalidValue(if s.kind == Kind::Int {
                            format!(
                                "{name} must be within {}..{}: {}",
                                r.minimum,
                                r.maximum,
                                requested.repr()
                            )
                        } else {
                            format!("{name}: {} is not offered by this camera", requested.repr())
                        }));
                    }
                }
            }
            let i = match plan.iter().position(|(g, _)| *g == s.key) {
                Some(i) => i,
                None => {
                    plan.push((s.key, Vec::new()));
                    plan.len() - 1
                }
            };
            let roles = &mut plan[i].1;
            let entry = (s.role, s.name, requested.clone(), dv);
            match roles.iter().position(|r| r.0 == s.role) {
                Some(j) => roles[j] = entry,
                None => roles.push(entry),
            }
        }
        for (group, roles) in &plan {
            if roles.iter().any(|r| r.0 == Role::Value) && auto_name(group).is_some() {
                self.check_mode(group, roles)?;
            }
        }
        let mut current: HashMap<&str, (i64, Option<bool>)> = HashMap::new();
        for (group, _) in &plan {
            // 대기 중인 요청이 없으면 장치를 읽어 이미 같은 값인지 본다
            if !self.sh.lock().pending.contains_key(group) {
                match self.read(group) {
                    Ok(x) => {
                        current.insert(group, x);
                    }
                    Err(e @ (SdkError::Unavailable(_) | SdkError::Closed)) => return Err(e),
                    Err(_) => {} // 못 읽으면 대기열로(워커가 다시 본다)
                }
            }
        }
        let mut g = self.sh.lock();
        if g.closed {
            return Err(SdkError::Closed);
        }
        let mut results = Vec::new();
        for (group, roles) in plan {
            let value = roles.iter().find_map(|r| {
                if let DevValue::Value(v) = r.3 {
                    Some(v)
                } else {
                    None
                }
            });
            let auto = roles.iter().find_map(|r| {
                if let DevValue::Auto(a) = r.3 {
                    Some(a)
                } else {
                    None
                }
            });
            if let Some(&(cv, ca)) = current.get(group) {
                if !g.pending.contains_key(group) && same(value, auto, Some(cv), ca) {
                    for (_, name, requested, _) in &roles {
                        let t = g.ticket();
                        let r = WriteResult::new(name, requested, Status::SkippedSame, t);
                        g.finish(r.clone());
                        results.push(r);
                    }
                    continue;
                }
            }
            let mut pend = match g.pending.remove(group) {
                Some(p) => p,
                None => {
                    let seq = g.next_seq;
                    g.next_seq += 1;
                    Pending {
                        group,
                        seq,
                        value: None,
                        auto: None,
                        reqs: Vec::new(),
                    }
                }
            };
            for (role, name, requested, dv) in &roles {
                let ticket = g.ticket();
                let new = Req {
                    role: *role,
                    ticket,
                    name,
                    requested: requested.clone(),
                };
                match pend.reqs.iter().position(|q| q.role == *role) {
                    Some(j) => {
                        let old = std::mem::replace(&mut pend.reqs[j], new);
                        g.finish(WriteResult::new(
                            old.name,
                            &old.requested,
                            Status::Superseded,
                            old.ticket,
                        ));
                    }
                    None => pend.reqs.push(new),
                }
                g.reqs.insert(ticket, (name.to_string(), requested.clone()));
                match dv {
                    DevValue::Value(v) => pend.value = Some(*v),
                    DevValue::Auto(a) => pend.auto = Some(*a),
                }
            }
            if pend.auto == Some(true) {
                // 자동으로 가면 대기 중이던 값은 의미가 없다
                if let Some(j) = pend.reqs.iter().position(|q| q.role == Role::Value) {
                    let old = pend.reqs.remove(j);
                    g.finish(WriteResult::new(
                        old.name,
                        &old.requested,
                        Status::Superseded,
                        old.ticket,
                    ));
                    pend.value = None;
                }
            }
            let due = (self.sh.due_at(group) - guard::now_s()).max(0.0);
            for (role, ..) in &roles {
                if let Some(q) = pend.reqs.iter().find(|q| q.role == *role) {
                    results.push(WriteResult {
                        due_in_s: Some(due),
                        ..WriteResult::new(q.name, &q.requested, Status::Pending, q.ticket)
                    });
                }
            }
            g.pending.insert(group, pend);
        }
        self.sh.cv.notify_all();
        Ok(results)
    }

    /// 자동 상태의 값은 모드와 같이 줄 때만 받는다.
    fn check_mode(&self, group: &str, roles: &Roles) -> Result<()> {
        let auto_n = auto_name(group).expect("checked by caller");
        let value_n = roles
            .iter()
            .find(|r| r.0 == Role::Value)
            .map(|r| r.1)
            .expect("checked by caller");
        if let Some(r) = roles.iter().find(|r| r.0 == Role::Auto) {
            if r.3 == DevValue::Auto(true) {
                return Err(SdkError::State(format!(
                    "{value_n} cannot be set in the same call that turns {auto_n} on"
                )));
            }
            return Ok(());
        }
        let mut auto = self.sh.lock().pending.get(group).and_then(|p| p.auto);
        if auto.is_none() {
            auto = match self.read(group) {
                Ok((_, a)) => a,
                Err(e @ (SdkError::Unavailable(_) | SdkError::Closed)) => return Err(e),
                Err(_) => None, // 모르면 막지 않는다
            };
        }
        if auto == Some(true) {
            let off = if spec::spec(auto_n).map(|s| s.kind) == Some(Kind::Menu) {
                "'manual'"
            } else {
                "False"
            };
            return Err(SdkError::State(format!(
                "{auto_n} is on — set it to {off} in the same call: set_many({{'{auto_n}': {off}, '{value_n}': ...}})"
            )));
        }
        Ok(())
    }

    fn deadline(timeout: Option<f64>) -> Option<Instant> {
        timeout.map(|t| Instant::now() + Duration::from_secs_f64(t.max(0.0)))
    }

    fn left(end: Option<Instant>) -> Option<f64> {
        end.map(|e| e.saturating_duration_since(Instant::now()).as_secs_f64())
    }

    /// 대기 중인 쓰기가 끝날 때까지 기다린 뒤, 지난 flush 이후 끝난 결과를 돌려준다.
    pub fn flush(&self, timeout: Option<f64>) -> Vec<WriteResult> {
        let end = Self::deadline(timeout);
        let mut g = self.sh.lock();
        while !g.pending.is_empty() || g.busy {
            let left = Self::left(end);
            if left.is_some_and(|l| l <= 0.0) {
                break;
            }
            g = self.sh.wait_for(g, left);
        }
        std::mem::take(&mut g.unflushed)
    }

    /// 주어진 요청들이 끝날 때까지 기다려 최종 결과를 돌려준다(시간 안에 안 끝난 것은 pending).
    pub fn wait(&self, tickets: &[u64], timeout: Option<f64>) -> Vec<WriteResult> {
        let end = Self::deadline(timeout);
        let mut g = self.sh.lock();
        while !tickets.iter().all(|t| g.finals.contains_key(t)) {
            let left = Self::left(end);
            if left.is_some_and(|l| l <= 0.0) {
                break;
            }
            g = self.sh.wait_for(g, left);
        }
        tickets
            .iter()
            .map(|t| match g.finals.get(t) {
                Some(r) => r.clone(),
                None => {
                    let (name, requested) = g
                        .reqs
                        .get(t)
                        .cloned()
                        .unwrap_or(("?".into(), UserValue::Choice(String::new())));
                    WriteResult::new(&name, &requested, Status::Pending, *t)
                }
            })
            .collect()
    }

    /// 끝났으면 최종 결과, 아직이면 None.
    pub fn result(&self, ticket: u64) -> Option<WriteResult> {
        self.sh.lock().finals.get(&ticket).cloned()
    }

    /// 대기 중인 요청 이름 → 쓰기까지 남은 초.
    pub fn pending(&self) -> Vec<(String, f64)> {
        let now = guard::now_s();
        let g = self.sh.lock();
        let mut out = Vec::new();
        for p in g.pending.values() {
            let left = (self.sh.due_at(p.group) - now).max(0.0);
            for q in &p.reqs {
                out.push((q.name.to_string(), left));
            }
        }
        out
    }

    // ---- 닫기 ----------------------------------------------------------------------------------

    /// 대기 중인 쓰기는 보내지 않고 cancelled 로 끝낸다 — 닫히는 장치에 쓰지 않는다. 두 번 불러도 된다.
    pub fn close(&self) {
        self.close_inner(true);
    }

    /// close() 와 같되 워커가 끝나기를 기다리지 않는다(워커는 백엔드를 닫고 혼자 끝난다).
    /// Python 바인딩이 GC 중(GIL 을 쥔 채) 닫을 때 쓴다 — 기다리면 GIL 이 필요한 Python 백엔드의 close 와 교착한다.
    pub fn close_nowait(&self) {
        self.close_inner(false);
    }

    fn close_inner(&self, join: bool) {
        let mut events = Vec::new();
        {
            let mut g = self.sh.lock();
            if !g.closed {
                g.closed = true;
                let pend: Vec<Pending> = g.pending.drain().map(|(_, p)| p).collect();
                for p in pend {
                    for q in p.reqs {
                        events.push(Event::Cancelled {
                            name: q.name.into(),
                            requested: q.requested.clone(),
                        });
                        g.finish(WriteResult::new(
                            q.name,
                            &q.requested,
                            Status::Cancelled,
                            q.ticket,
                        ));
                    }
                }
                self.sh.cv.notify_all();
            }
        }
        for e in events {
            self.sh.emit(e); // 잠금 밖에서(콜백이 다른 잠금을 잡아도 교착하지 않게)
        }
        if thread::current().id() == self.worker_id {
            return;
        }
        let w = self.worker.lock().unwrap_or_else(|e| e.into_inner()).take();
        if !join {
            return; // JoinHandle 을 놓으면 스레드는 떨어져 나가 혼자 끝난다
        }
        if let Some((h, done)) = w {
            // Python join(timeout=5) 와 같다: 백엔드 호출이 멈춰 있으면 기다리지 않고 놓아 둔다.
            if done.recv_timeout(Duration::from_secs(5)).is_ok() {
                let _ = h.join();
            }
        }
    }

    // ---- 읽기 ----------------------------------------------------------------------------------

    /// 장치의 지금 값. 모드를 읽을 수 없는 백엔드면 None.
    pub fn get(&self, name: &str) -> Result<Option<UserValue>> {
        let s = self.spec(name)?;
        let (v, a) = self.read(s.key)?;
        Ok(from_device(s, Some(v), a))
    }

    /// 읽기만 한다.
    pub fn describe(&self, name: &str) -> Result<ControlInfo> {
        let s = self.spec(name)?;
        let rng = self.range(s.key)?;
        let (value, auto) = self.read(s.key)?;
        let (mut minimum, mut maximum, mut step, mut default, mut choices) =
            (None, None, None, None, None);
        if s.role == Role::Value {
            if let Some(r) = rng {
                if s.kind == Kind::Int {
                    (minimum, maximum, step) = (Some(r.minimum), Some(r.maximum), Some(r.step));
                }
                if r.default.is_some() {
                    default = from_device(s, r.default, None);
                }
            }
            if s.kind == Kind::Menu {
                choices = Some(
                    s.choices
                        .unwrap_or(&[])
                        .iter()
                        .enumerate()
                        .filter(|(i, _)| {
                            rng.is_none_or(|r| r.minimum <= *i as i64 && (*i as i64) <= r.maximum)
                        })
                        .map(|(_, c)| *c)
                        .collect(),
                );
            }
        } else if s.kind == Kind::Menu {
            choices = s.choices.map(|c| c.to_vec());
        }
        let units = if s.name == "exposure" {
            self.exposure_units.clone()
        } else {
            "raw".to_string()
        };
        Ok(ControlInfo {
            name: s.name,
            kind: s.kind,
            current: from_device(s, Some(value), auto),
            minimum,
            maximum,
            step,
            default,
            choices,
            units,
            effect: s.effect,
            ledger: s.ledger,
            group: s.key,
            note: s.note,
        })
    }

    /// describe() 전부(이름 표 순서).
    pub fn describe_all(&self) -> Result<Vec<ControlInfo>> {
        self.names()?
            .into_iter()
            .map(|n| self.describe(n))
            .collect()
    }

    /// 지금 값 전부(읽을 수 없는 것은 뺀다). restore() 로 되돌린다.
    pub fn snapshot(&self) -> Result<Vec<(String, UserValue)>> {
        let mut out = Vec::new();
        for n in self.names()? {
            if let Some(v) = self.get(n)? {
                out.push((n.to_string(), v));
            }
        }
        Ok(out)
    }

    /// 스냅샷 값으로 되돌린다 — 다른 것만 쓴다. 자동으로 돌아가는 그룹의 값은 뺀다. 기다리지 않는다.
    pub fn restore(&self, snap: &[(String, UserValue)]) -> Result<Vec<WriteResult>> {
        let have = self.names()?;
        let mut target: Vec<(String, UserValue)> = snap
            .iter()
            .filter(|(n, _)| have.contains(&n.as_str()))
            .cloned()
            .collect();
        let find =
            |t: &[(String, UserValue)], n: &str| t.iter().find(|x| x.0 == n).map(|x| x.1.clone());
        for group in spec::auto_groups() {
            let (an, vn) = (
                auto_name(group).expect("auto group"),
                spec::value_name(group).expect("auto group"),
            );
            if let Some(v) = find(&target, an) {
                if py_eq(&v, &UserValue::Choice("auto".into())) || py_eq(&v, &UserValue::Bool(true))
                {
                    target.retain(|x| x.0 != vn);
                }
            }
        }
        let mut diff = Vec::new();
        for (n, v) in &target {
            if !self.get(n)?.is_some_and(|cur| py_eq(&cur, v)) {
                diff.push((n.clone(), v.clone()));
            }
        }
        for group in spec::auto_groups() {
            // 값을 되돌리면 모드도 같이 준다(자동 상태의 값은 모드와 같이 줄 때만 받는다)
            let (an, vn) = (
                auto_name(group).expect("auto group"),
                spec::value_name(group).expect("auto group"),
            );
            if find(&diff, vn).is_some() && find(&diff, an).is_none() {
                if let Some(v) = find(&target, an) {
                    diff.push((an.to_string(), v));
                }
            }
        }
        if diff.is_empty() {
            return Ok(Vec::new());
        }
        self.set_many(&diff)
    }

    /// 기본값 복원으로 바뀔 것 (이름, 지금, 기본값) — 쓰지 않는다.
    /// 모드(자동/수동)는 건드리지 않고, 자동 상태인 값도 뺀다(AMCap 기본값 버튼과 같다).
    /// 흑백 무효 제어는 include_mono 일 때만. 장치가 기본값을 주지 않은 제어는 뺀다.
    pub fn plan_reset(
        &self,
        include_mono: bool,
    ) -> Result<Vec<(&'static str, UserValue, UserValue)>> {
        let mut plan = Vec::new();
        for name in self.names()? {
            let s = spec::spec(name).expect("in table");
            if s.role == Role::Auto || (s.mono && !include_mono) {
                continue;
            }
            if auto_name(s.key).is_some() && self.read(s.key)?.1 == Some(true) {
                continue;
            }
            let info = self.describe(name)?;
            let (Some(cur), Some(def)) = (info.current, info.default) else {
                continue;
            };
            if !py_eq(&cur, &def) {
                plan.push((name, cur, def));
            }
        }
        Ok(plan)
    }

    /// plan_reset() 을 쓴다. 기다리지 않는다.
    pub fn reset_to_defaults(&self, include_mono: bool) -> Result<Vec<WriteResult>> {
        let plan: Vec<(String, UserValue)> = self
            .plan_reset(include_mono)?
            .into_iter()
            .map(|(n, _, d)| (n.to_string(), d))
            .collect();
        if plan.is_empty() {
            return Ok(Vec::new());
        }
        self.set_many(&plan)
    }
}

impl Drop for Controls {
    fn drop(&mut self) {
        self.close();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::backend::mock::MockBackend;
    use crate::convert::UserValue::{self, *};
    use crate::guard::{self, GuardConfig};
    use crate::spec::CONTROLS;

    /// conftest 의 _fast_control_guard 와 같다: 간격 0, 경고 사실상 끔, 카운터 0. 반환한 잠금을 시험 끝까지 쥔다.
    pub(crate) fn fast() -> std::sync::MutexGuard<'static, ()> {
        let l = guard::test_lock();
        guard::set_config(GuardConfig {
            group_intervals: Default::default(),
            global_gap_s: 0.0,
            warn_after_total: 1_000_000_000,
            ledger: None,
        });
        guard::reset_write_counts();
        l
    }

    pub(crate) fn controls() -> (Controls, MockBackend) {
        let be = MockBackend::new();
        (
            Controls::new(Box::new(be.clone()), true, "", None).unwrap(),
            be,
        )
    }

    fn s(x: &str) -> UserValue {
        Choice(x.into())
    }

    #[test]
    fn names_follow_the_table_and_the_backend() {
        let _l = fast();
        let (c, _) = controls();
        let want: Vec<_> = CONTROLS.iter().map(|x| x.name).collect();
        assert_eq!(c.names().unwrap(), want);
        c.close();
    }

    #[test]
    fn names_leave_out_what_the_backend_does_not_support() {
        let _l = fast();
        let be = MockBackend::new();
        be.st().ranges.remove("power_line_frequency");
        let c = Controls::new(Box::new(be.clone()), true, "", None).unwrap();
        assert!(!c.names().unwrap().contains(&"power_line_frequency"));
        assert!(matches!(
            c.get("power_line_frequency"),
            Err(SdkError::UnknownControl { .. })
        ));
        assert!(matches!(
            c.get("iris"),
            Err(SdkError::UnknownControl { .. })
        ));
        c.close();
    }

    #[test]
    fn describe_reports_range_default_units_and_verification() {
        let _l = fast();
        let (c, _) = controls();
        let g = c.describe("gain").unwrap();
        assert_eq!(
            (g.minimum, g.maximum, g.step, g.default.clone()),
            (Some(0), Some(255), Some(1), Some(Int(32)))
        );
        assert_eq!(g.current, Some(Int(32)));
        assert_eq!((g.effect, g.ledger), (Effect::Verified, None));
        let e = c.describe("exposure").unwrap();
        assert_eq!((e.units.as_str(), e.group), ("log2_s_nominal", "exposure"));
        let ae = c.describe("auto_exposure").unwrap();
        assert_eq!(ae.kind, Kind::Menu);
        assert_eq!(ae.choices, Some(vec!["manual", "auto"]));
        assert_eq!((ae.current, ae.default), (Some(s("manual")), None));
        let pl = c.describe("power_line_frequency").unwrap();
        assert_eq!(pl.choices, Some(vec!["disabled", "50hz", "60hz"]));
        assert_eq!((pl.current, pl.minimum), (Some(s("60hz")), None));
        assert_eq!(c.describe("hue").unwrap().effect, Effect::NoneExpectedMono);
        assert_eq!(c.describe("gain").unwrap().units, "raw");
        let all: Vec<_> = c
            .describe_all()
            .unwrap()
            .into_iter()
            .map(|i| i.name)
            .collect();
        assert_eq!(all, c.names().unwrap());
        c.close();
    }

    #[test]
    fn menu_choices_are_limited_to_the_device_range() {
        let _l = fast();
        let be = MockBackend::new();
        be.st()
            .ranges
            .get_mut("power_line_frequency")
            .unwrap()
            .maximum = 1;
        let c = Controls::new(Box::new(be.clone()), true, "", None).unwrap();
        assert_eq!(
            c.describe("power_line_frequency").unwrap().choices,
            Some(vec!["disabled", "50hz"])
        );
        c.close();
    }

    #[test]
    fn describe_without_a_range_invents_nothing() {
        let _l = fast();
        let be = MockBackend::new();
        be.st().unranged.insert("sharpness".into());
        let c = Controls::new(Box::new(be.clone()), true, "", None).unwrap();
        let i = c.describe("sharpness").unwrap();
        assert_eq!(
            (i.minimum, i.maximum, i.default, i.current),
            (None, None, None, Some(Int(28)))
        );
        c.close();
    }

    #[test]
    fn snapshot_reads_every_control() {
        let _l = fast();
        let (c, _) = controls();
        let snap = c.snapshot().unwrap();
        assert_eq!(snap.len(), 14);
        assert!(snap.contains(&("auto_exposure".to_string(), s("manual"))));
        assert!(snap.contains(&("gain".to_string(), Int(32))));
        c.close();
    }

    #[test]
    fn a_backend_that_fails_to_start_is_unavailable() {
        let _l = fast();
        let be = MockBackend::new();
        be.st().fail_start = Some("no such camera".into());
        match Controls::new(Box::new(be), true, "", None) {
            Err(SdkError::Unavailable(m)) => assert_eq!(m, "sim: no such camera"),
            Err(e) => panic!("{e:?}"),
            Ok(_) => panic!("started"),
        }
    }

    #[test]
    fn backend_calls_happen_on_one_worker_thread_and_close_reaches_the_backend() {
        let _l = fast();
        let (c, be) = controls();
        c.describe_all().unwrap();
        c.close();
        let st = be.st();
        assert_eq!(st.threads.len(), 1);
        assert!(!st.threads.contains(&std::thread::current().id()));
        assert!(st.started && st.closed);
    }

    #[test]
    fn calls_after_close_are_unavailable() {
        let _l = fast();
        let (c, _) = controls();
        c.close();
        assert!(c.closed());
        assert_eq!(c.get("gain"), Err(SdkError::Closed));
        c.close(); // 두 번 닫아도 된다
    }

    #[test]
    fn backend_name_and_note_are_kept() {
        let _l = fast();
        let be = MockBackend::new();
        let c = Controls::new(Box::new(be), true, "extra", None).unwrap();
        assert_eq!(c.backend_name(), "sim");
        assert_eq!(
            c.backend_note(),
            "simulated controls — ranges and defaults are made up, not measured; extra"
        );
        c.close();
    }
}

/// Python test_controls_writes.py 이식.
#[cfg(test)]
mod write_tests {
    use super::tests::{controls, fast};
    use super::*;
    use crate::backend::mock::MockBackend;
    use crate::convert::UserValue::{self, *};
    use crate::guard::{self, GuardConfig};
    use std::time::Instant;

    pub(crate) fn intervals(groups: &[(&str, f64)], global: f64) {
        guard::set_config(GuardConfig {
            group_intervals: groups.iter().map(|(g, s)| (g.to_string(), *s)).collect(),
            global_gap_s: global,
            warn_after_total: 1_000_000_000,
            ledger: None,
        });
    }

    pub(crate) fn with(f: impl FnOnce(&MockBackend)) -> (Controls, MockBackend) {
        let be = MockBackend::new();
        f(&be);
        (
            Controls::new(Box::new(be.clone()), true, "", None).unwrap(),
            be,
        )
    }

    fn s(x: &str) -> UserValue {
        Choice(x.into())
    }

    const T: Option<f64> = Some(5.0);

    #[test]
    fn a_write_goes_out_once_and_is_reported() {
        let _l = fast();
        let (c, be) = controls();
        let r = c.set("gain", Int(28)).unwrap();
        assert_eq!(
            (r.status, r.name.as_str(), r.requested.clone()),
            (Status::Pending, "gain", Int(28))
        );
        let done = c.flush(T);
        assert_eq!(
            done.iter().map(|x| x.status).collect::<Vec<_>>(),
            [Status::Written]
        );
        assert_eq!(be.writes(), [("gain".to_string(), Some(28), None)]);
        assert_eq!(guard::write_counts()["gain"], 1);
        c.close();
    }

    #[test]
    fn the_same_value_is_not_written_at_all() {
        let _l = fast();
        let (c, be) = controls();
        let r = c.set("gain", Int(32)).unwrap();
        assert_eq!(r.status, Status::SkippedSame);
        assert!(be.writes().is_empty());
        assert_eq!(guard::write_counts()["_total"], 0);
        c.close();
    }

    #[test]
    fn all_backend_calls_happen_on_one_worker_thread() {
        let _l = fast();
        let (c, be) = controls();
        c.set("gain", Int(30)).unwrap();
        c.flush(T);
        c.close();
        let st = be.st();
        assert_eq!(st.threads.len(), 1);
        assert!(!st.threads.contains(&std::thread::current().id()));
    }

    #[test]
    fn requests_inside_the_interval_collapse_to_the_latest() {
        let _l = fast();
        intervals(&[("gain", 0.3)], 0.0);
        let (c, be) = controls();
        c.set("gain", Int(40)).unwrap();
        c.flush(T);
        let results: Vec<_> = (41..60).map(|v| c.set("gain", Int(v)).unwrap()).collect();
        assert!(results.iter().all(|r| r.status == Status::Pending));
        let done = c.flush(T);
        assert_eq!(
            be.writes(),
            [
                ("gain".to_string(), Some(40), None),
                ("gain".to_string(), Some(59), None)
            ]
        );
        let status: HashMap<i64, Status> = done
            .iter()
            .map(|r| {
                (
                    if let Int(v) = r.requested {
                        v
                    } else {
                        panic!()
                    },
                    r.status,
                )
            })
            .collect();
        assert_eq!(status[&59], Status::Written);
        assert!((41..59).all(|v| status[&v] == Status::Superseded));
        c.close();
    }

    #[test]
    fn group_interval_is_kept() {
        let _l = fast();
        intervals(&[("gain", 0.3)], 0.0);
        let (c, _) = controls();
        c.set("gain", Int(40)).unwrap();
        c.flush(T);
        let t0 = Instant::now();
        c.set("gain", Int(41)).unwrap();
        c.flush(T);
        assert!(t0.elapsed().as_secs_f64() >= 0.25);
        c.close();
    }

    #[test]
    fn global_gap_spaces_writes_of_different_controls() {
        let _l = fast();
        intervals(&[], 0.3);
        let (c, _) = controls();
        c.set("gain", Int(40)).unwrap();
        c.flush(T);
        let t0 = Instant::now();
        c.set("brightness", Int(60)).unwrap();
        c.flush(T);
        assert!(t0.elapsed().as_secs_f64() >= 0.25);
        c.close();
    }

    #[test]
    fn pending_reports_time_until_the_write() {
        let _l = fast();
        intervals(&[("gain", 2.0)], 0.0);
        let (c, _) = controls();
        c.set("gain", Int(40)).unwrap();
        c.flush(T);
        let r = c.set("gain", Int(41)).unwrap();
        let d = r.due_in_s.unwrap();
        assert!(1.0 < d && d <= 2.0, "{d}");
        let p = c.pending();
        assert_eq!(p.len(), 1);
        assert_eq!(p[0].0, "gain");
        assert!(1.0 < p[0].1 && p[0].1 <= 2.0);
        c.close();
    }

    #[test]
    fn exposure_value_is_refused_while_auto_exposure_is_on() {
        let _l = fast();
        let (c, be) = with(|b| {
            b.st().autos.insert("exposure".into(), true);
        });
        let want = "auto_exposure is on — set it to 'manual' in the same call: \
                    set_many({'auto_exposure': 'manual', 'exposure': ...})";
        assert_eq!(
            c.set("exposure", Int(-6)),
            Err(SdkError::State(want.into()))
        );
        assert!(be.writes().is_empty());
        c.close();
    }

    #[test]
    fn mode_and_value_together_are_one_write() {
        let _l = fast();
        let (c, be) = with(|b| {
            b.st().autos.insert("exposure".into(), true);
        });
        let res = c
            .set_many(&[
                ("auto_exposure".into(), s("manual")),
                ("exposure".into(), Int(-6)),
            ])
            .unwrap();
        let mut names: Vec<_> = res.iter().map(|r| r.name.clone()).collect();
        names.sort();
        assert_eq!(names, ["auto_exposure", "exposure"]);
        c.flush(T);
        assert_eq!(
            be.writes(),
            [("exposure".to_string(), Some(-6), Some(false))]
        );
        c.close();
    }

    #[test]
    fn a_value_cannot_ride_along_with_switching_auto_on() {
        let _l = fast();
        let (c, _) = controls();
        assert_eq!(
            c.set_many(&[
                ("auto_exposure".into(), s("auto")),
                ("exposure".into(), Int(-6))
            ]),
            Err(SdkError::State(
                "exposure cannot be set in the same call that turns auto_exposure on".into()
            ))
        );
        c.close();
    }

    #[test]
    fn switching_auto_on_drops_a_pending_value() {
        let _l = fast();
        intervals(&[("exposure", 0.3)], 0.0);
        let (c, be) = controls();
        c.set("exposure", Int(-6)).unwrap();
        c.flush(T);
        let v = c.set("exposure", Int(-7)).unwrap();
        let a = c.set("auto_exposure", s("auto")).unwrap();
        let done: HashMap<u64, Status> = c.flush(T).iter().map(|r| (r.ticket, r.status)).collect();
        assert_eq!(
            (done[&v.ticket], done[&a.ticket]),
            (Status::Superseded, Status::Written)
        );
        assert_eq!(
            be.writes().last().unwrap(),
            &("exposure".to_string(), None, Some(true))
        );
        c.close();
    }

    #[test]
    fn white_balance_follows_the_same_rule() {
        let _l = fast();
        let (c, _) = with(|b| {
            b.st().autos.insert("white_balance".into(), true);
        });
        match c.set("white_balance", Int(100)) {
            Err(SdkError::State(m)) => assert!(
                m.contains("auto_white_balance") && m.contains("set it to False"),
                "{m}"
            ),
            other => panic!("{other:?}"),
        }
        c.close();
    }

    #[test]
    fn out_of_range_and_unknown_choices_are_refused() {
        let _l = fast();
        let (c, be) = controls();
        assert_eq!(
            c.set("gain", Int(999)),
            Err(SdkError::InvalidValue(
                "gain must be within 0..255: 999".into()
            ))
        );
        assert!(matches!(
            c.set("power_line_frequency", s("auto")),
            Err(SdkError::InvalidValue(_))
        ));
        be.st()
            .ranges
            .get_mut("power_line_frequency")
            .unwrap()
            .maximum = 1;
        // 범위는 Controls 마다 캐시된다 — 새 Controls 로 본다
        let c2 = Controls::new(Box::new(be.clone()), true, "", None).unwrap();
        assert_eq!(
            c2.set("power_line_frequency", s("60hz")),
            Err(SdkError::InvalidValue(
                "power_line_frequency: '60hz' is not offered by this camera".into()
            ))
        );
        assert!(be.writes().is_empty());
        c.close();
        c2.close();
    }

    #[test]
    fn a_failed_check_queues_nothing() {
        let _l = fast();
        let (c, be) = controls();
        assert!(c
            .set_many(&[("gain".into(), Int(40)), ("brightness".into(), Int(999))])
            .is_err());
        assert!(c.pending().is_empty());
        c.flush(T);
        assert!(be.writes().is_empty());
        c.close();
    }

    #[test]
    fn unknown_or_unsupported_names_raise_key_error() {
        let _l = fast();
        let (c, _) = with(|b| {
            b.st().ranges.remove("power_line_frequency");
        });
        assert!(!c.names().unwrap().contains(&"power_line_frequency"));
        assert!(matches!(
            c.set("power_line_frequency", s("60hz")),
            Err(SdkError::UnknownControl { .. })
        ));
        assert!(matches!(
            c.get("iris"),
            Err(SdkError::UnknownControl { .. })
        ));
        c.close();
    }

    #[test]
    fn a_rejected_write_is_reported_as_failed() {
        let _l = fast();
        let (c, _) = with(|b| {
            b.st().fail_keys.insert("gain".into());
        });
        c.set("gain", Int(40)).unwrap();
        let done = c.flush(T);
        assert_eq!(done.len(), 1);
        assert_eq!(done[0].status, Status::Failed);
        assert_eq!(
            done[0].error.as_deref(),
            Some("OSError: simulated driver rejected gain")
        );
        assert_eq!(guard::write_counts()["_total"], 0);
        c.close();
    }

    #[test]
    fn wait_returns_the_final_result_of_given_requests() {
        let _l = fast();
        intervals(&[("gain", 0.2)], 0.0);
        let (c, _) = controls();
        c.set("gain", Int(40)).unwrap();
        c.flush(T);
        let r = c.set("gain", Int(41)).unwrap();
        let fin = c.wait(&[r.ticket], T);
        assert_eq!(fin.len(), 1);
        assert_eq!(fin[0].status, Status::Written);
        assert_eq!(c.result(r.ticket), Some(fin[0].clone()));
        c.close();
    }

    #[test]
    fn wait_gives_up_at_the_timeout_and_says_pending() {
        let _l = fast();
        intervals(&[("gain", 10.0)], 0.0);
        let (c, _) = controls();
        c.set("gain", Int(40)).unwrap();
        c.flush(T);
        let r = c.set("gain", Int(41)).unwrap();
        let fin = c.wait(&[r.ticket], Some(0.1));
        assert_eq!(
            (fin[0].status, fin[0].requested.clone()),
            (Status::Pending, Int(41))
        );
        assert_eq!(c.result(r.ticket), None);
        c.close();
    }

    #[test]
    fn closing_cancels_pending_writes() {
        let _l = fast();
        intervals(&[("gain", 10.0)], 0.0);
        let (c, be) = controls();
        c.set("gain", Int(40)).unwrap();
        c.flush(T);
        let r = c.set("gain", Int(41)).unwrap();
        c.close();
        assert_eq!(c.result(r.ticket).unwrap().status, Status::Cancelled);
        assert_eq!(be.writes(), [("gain".to_string(), Some(40), None)]);
        assert!(be.st().closed);
        assert_eq!(c.set("gain", Int(42)), Err(SdkError::Closed));
    }

    #[test]
    fn unguarded_controls_do_not_touch_the_process_counters() {
        let _l = fast();
        let be = MockBackend::new();
        let c = Controls::new(Box::new(be.clone()), false, "", None).unwrap();
        c.set("gain", Int(40)).unwrap();
        c.flush(T);
        assert_eq!(be.writes(), [("gain".to_string(), Some(40), None)]);
        assert_eq!(guard::write_counts()["_total"], 0);
        c.close();
    }

    #[test]
    fn a_write_reports_what_the_camera_reads_back() {
        let _l = fast();
        let (c, _) = controls();
        c.set("gain", Int(40)).unwrap();
        let done = c.flush(T);
        assert_eq!(
            (done[0].status, done[0].readback.clone(), done[0].kept()),
            (Status::Written, Some(Int(40)), Some(true))
        );
        c.close();
    }

    #[test]
    fn a_write_the_camera_does_not_keep_is_visible() {
        let _l = fast();
        let seen: Arc<Mutex<Vec<Event>>> = Arc::default();
        let s2 = seen.clone();
        let be = MockBackend::new();
        be.st().ignore_writes.insert("gain".into());
        let hook: EventHook = Box::new(move |e| s2.lock().unwrap().push(e.clone()));
        let c = Controls::new(Box::new(be.clone()), true, "", Some(hook)).unwrap();
        c.set("gain", Int(40)).unwrap();
        let done = c.flush(T);
        assert_eq!(done[0].status, Status::Written); // 드라이버는 받았다
        assert_eq!(
            (done[0].readback.clone(), done[0].kept()),
            (Some(Int(32)), Some(false))
        ); // 카메라는 그대로다
        let msgs: Vec<String> = seen
            .lock()
            .unwrap()
            .iter()
            .filter(|e| e.is_warning())
            .map(Event::message)
            .collect();
        assert_eq!(msgs, ["gain: wrote 40, but the camera reads back 32"]);
        c.close();
    }

    #[test]
    fn mode_and_value_are_both_read_back() {
        let _l = fast();
        let (c, _) = with(|b| {
            b.st().autos.insert("exposure".into(), true);
        });
        c.set_many(&[
            ("auto_exposure".into(), s("manual")),
            ("exposure".into(), Int(-6)),
        ])
        .unwrap();
        let done: HashMap<String, WriteResult> = c
            .flush(T)
            .into_iter()
            .map(|r| (r.name.clone(), r))
            .collect();
        assert_eq!(
            (
                done["auto_exposure"].readback.clone(),
                done["auto_exposure"].kept()
            ),
            (Some(s("manual")), Some(true))
        );
        assert_eq!(
            (done["exposure"].readback.clone(), done["exposure"].kept()),
            (Some(Int(-6)), Some(true))
        );
        c.close();
    }

    #[test]
    fn skipped_and_failed_writes_have_no_readback() {
        let _l = fast();
        let (c, _) = with(|b| {
            b.st().fail_keys.insert("gain".into());
        });
        assert_eq!(c.set("brightness", Int(50)).unwrap().kept(), None);
        c.set("gain", Int(40)).unwrap();
        let done = c.flush(T); // flush 는 건너뛴 결과도 같이 준다
        let r = done.iter().find(|x| x.name == "gain").unwrap();
        assert_eq!((r.status, r.kept()), (Status::Failed, None));
        c.close();
    }

    #[test]
    fn a_float_request_that_equals_the_readback_is_kept() {
        let _l = fast();
        let (c, _) = controls();
        c.set("gain", Float(40.0)).unwrap();
        assert_eq!(c.flush(T)[0].kept(), Some(true)); // Python: 40 == 40.0
        c.close();
    }

    #[test]
    fn written_events_carry_the_total_and_real_writes_go_to_the_ledger() {
        let _l = fast();
        let dir = std::env::temp_dir().join(format!("ov_ctl_ledger_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("w.jsonl");
        let _ = std::fs::remove_file(&path);
        guard::set_config(GuardConfig {
            ledger: Some(path.clone()),
            ..guard::config()
        });
        let seen: Arc<Mutex<Vec<Event>>> = Arc::default();
        let s2 = seen.clone();
        let hook: EventHook = Box::new(move |e| s2.lock().unwrap().push(e.clone()));
        let c = Controls::new(Box::new(MockBackend::new()), true, "", Some(hook)).unwrap();
        c.set("gain", Int(40)).unwrap();
        c.flush(T);
        c.set("gain", Int(32)).unwrap(); // 기본값으로 돌아가는 것도 실제 쓰기
        c.flush(T);
        c.set("gain", Int(32)).unwrap(); // 이건 건너뜀 — 원장에 없다
        c.flush(T);
        c.close();
        guard::set_config(GuardConfig {
            ledger: None,
            ..guard::config()
        });
        let wrote: Vec<u64> = seen
            .lock()
            .unwrap()
            .iter()
            .filter_map(|e| {
                if let Event::Wrote { total, .. } = e {
                    Some(*total)
                } else {
                    None
                }
            })
            .collect();
        assert_eq!(wrote, [1, 2]);
        let text = std::fs::read_to_string(&path).unwrap();
        assert_eq!(text.lines().count(), 2);
        assert!(
            text.lines()
                .all(|l| l.contains("\"backend\":\"sim\"") && l.contains("\"kept\":true")),
            "{text}"
        );
        std::fs::remove_dir_all(&dir).unwrap();
    }
}

/// Python test_controls_reads.py 의 restore·plan_reset 과 test_controls_writes.py 의 경고 시험 이식.
#[cfg(test)]
mod reset_tests {
    use super::tests::{controls, fast};
    use super::write_tests::with;
    use super::*;
    use crate::backend::mock::MockBackend;
    use crate::convert::UserValue::{self, *};
    use crate::guard::{self, GuardConfig};

    fn s(x: &str) -> UserValue {
        Choice(x.into())
    }

    fn sorted(
        mut w: Vec<(String, Option<i64>, Option<bool>)>,
    ) -> Vec<(String, Option<i64>, Option<bool>)> {
        w.sort_by(|a, b| a.0.cmp(&b.0));
        w
    }

    const T: Option<f64> = Some(5.0);

    #[test]
    fn snapshot_and_restore_write_only_what_changed() {
        let _l = fast();
        let (c, be) = controls();
        let snap = c.snapshot().unwrap();
        c.set_many(&[("gain".into(), Int(100)), ("brightness".into(), Int(10))])
            .unwrap();
        c.flush(T);
        be.st().writes.clear();
        c.restore(&snap).unwrap();
        c.flush(T);
        assert_eq!(
            sorted(be.writes()),
            [
                ("brightness".to_string(), Some(50), None),
                ("gain".to_string(), Some(32), None)
            ]
        );
        c.close();
    }

    #[test]
    fn restore_brings_manual_mode_with_the_exposure_value() {
        let _l = fast();
        let (c, be) = controls(); // 수동, -5
        let snap = c.snapshot().unwrap();
        c.set("auto_exposure", s("auto")).unwrap();
        c.flush(T);
        be.st().writes.clear();
        c.restore(&snap).unwrap();
        c.flush(T);
        assert_eq!(be.writes(), [("exposure".to_string(), None, Some(false))]); // 값은 그대로라 모드만 보낸다
        assert_eq!(c.get("auto_exposure").unwrap(), Some(s("manual")));
        assert_eq!(c.get("exposure").unwrap(), Some(Int(-5)));
        c.close();
    }

    #[test]
    fn restore_sends_the_mode_along_with_a_changed_value() {
        let _l = fast();
        let (c, be) = controls();
        let snap = c.snapshot().unwrap(); // 수동, -5
        c.set("exposure", Int(-8)).unwrap();
        c.flush(T);
        be.st().writes.clear();
        let res = c.restore(&snap).unwrap();
        let mut names: Vec<_> = res.iter().map(|r| r.name.clone()).collect();
        names.sort();
        assert_eq!(names, ["auto_exposure", "exposure"]); // 값을 되돌리면 모드도 같이
        c.flush(T);
        assert_eq!(c.get("exposure").unwrap(), Some(Int(-5)));
        c.close();
    }

    #[test]
    fn restore_to_auto_leaves_the_value_out() {
        let _l = fast();
        let (c, be) = with(|b| {
            b.st().autos.insert("exposure".into(), true);
        });
        let snap = c.snapshot().unwrap(); // 자동
        c.set_many(&[
            ("auto_exposure".into(), s("manual")),
            ("exposure".into(), Int(-8)),
        ])
        .unwrap();
        c.flush(T);
        be.st().writes.clear();
        c.restore(&snap).unwrap();
        c.flush(T);
        assert_eq!(be.writes(), [("exposure".to_string(), None, Some(true))]);
        c.close();
    }

    #[test]
    fn plan_reset_lists_only_values_that_differ_from_device_defaults() {
        let _l = fast();
        let (c, be) = controls();
        assert!(c.plan_reset(false).unwrap().is_empty());
        {
            let mut st = be.st();
            st.values.insert("gain".into(), 100);
            st.values.insert("hue".into(), 1);
            st.values.insert("exposure".into(), -8);
        }
        assert_eq!(
            c.plan_reset(false).unwrap(),
            [("gain", Int(100), Int(32)), ("exposure", Int(-8), Int(-5))] // 흑백 무효 제어는 빠진다
        );
        assert!(c
            .plan_reset(true)
            .unwrap()
            .contains(&("hue", Int(1), Int(64))));
        c.close();
    }

    #[test]
    fn plan_reset_leaves_modes_and_auto_values_alone() {
        let _l = fast();
        let (c, be) = with(|b| {
            b.st().autos.insert("exposure".into(), true);
        });
        be.st().values.insert("exposure".into(), -8);
        let plan = c.plan_reset(false).unwrap();
        assert!(!plan
            .iter()
            .any(|p| p.0 == "exposure" || p.0 == "auto_exposure"));
        c.close();
    }

    #[test]
    fn plan_reset_skips_controls_without_a_device_default() {
        let _l = fast();
        let (c, be) = with(|b| {
            b.st().unranged.insert("sharpness".into());
        });
        be.st().values.insert("sharpness".into(), 3);
        assert!(!c
            .plan_reset(false)
            .unwrap()
            .iter()
            .any(|p| p.0 == "sharpness"));
        c.close();
    }

    #[test]
    fn reset_to_defaults_writes_the_plan() {
        let _l = fast();
        let (c, be) = controls();
        {
            let mut st = be.st();
            st.values.insert("gain".into(), 100);
            st.values.insert("backlight_compensation".into(), 96);
        }
        c.reset_to_defaults(false).unwrap();
        c.flush(T);
        assert_eq!(
            sorted(be.writes()),
            [
                ("backlight_compensation".to_string(), Some(32), None),
                ("gain".to_string(), Some(32), None)
            ]
        );
        c.close();
    }

    #[test]
    fn warning_names_the_evidence() {
        let _l = fast();
        guard::set_config(GuardConfig {
            warn_after_total: 2,
            ..guard::config()
        });
        let seen: Arc<Mutex<Vec<Event>>> = Arc::default();
        let s2 = seen.clone();
        let hook: EventHook = Box::new(move |e| s2.lock().unwrap().push(e.clone()));
        let c = Controls::new(Box::new(MockBackend::new()), true, "", Some(hook)).unwrap();
        c.set("gain", Int(40)).unwrap();
        c.flush(T);
        assert!(!seen
            .lock()
            .unwrap()
            .iter()
            .any(|e| matches!(e, Event::WarnTotal { .. })));
        c.set("brightness", Int(60)).unwrap();
        c.flush(T);
        let warns: Vec<String> = seen
            .lock()
            .unwrap()
            .iter()
            .filter(|e| matches!(e, Event::WarnTotal { .. }))
            .map(Event::message)
            .collect();
        assert_eq!(warns.len(), 1);
        assert!(
            warns[0].starts_with("2 camera control writes in this process.")
                && warns[0].contains("re-inserted")
        );
        assert!(warns[0].ends_with("Write once at startup where you can."));
        c.close();
    }

    #[test]
    fn close_nowait_cancels_and_the_worker_still_closes_the_backend() {
        let _l = fast();
        guard::set_config(GuardConfig {
            group_intervals: [("gain".to_string(), 10.0)].into(),
            ..guard::config()
        });
        let be = MockBackend::new();
        let c = Controls::new(Box::new(be.clone()), true, "", None).unwrap();
        c.set("gain", Int(40)).unwrap();
        c.flush(T);
        let r = c.set("gain", Int(41)).unwrap();
        c.close_nowait();
        assert_eq!(c.result(r.ticket).unwrap().status, Status::Cancelled);
        let t0 = std::time::Instant::now();
        while !be.st().closed && t0.elapsed().as_secs_f64() < 5.0 {
            std::thread::sleep(std::time::Duration::from_millis(5));
        }
        assert!(be.st().closed);
        drop(c); // 이미 놓았으니 기다리지 않는다
    }

    #[test]
    fn closing_reports_each_cancelled_write() {
        let _l = fast();
        guard::set_config(GuardConfig {
            group_intervals: [("gain".to_string(), 10.0)].into(),
            ..guard::config()
        });
        let seen: Arc<Mutex<Vec<Event>>> = Arc::default();
        let s2 = seen.clone();
        let hook: EventHook = Box::new(move |e| s2.lock().unwrap().push(e.clone()));
        let c = Controls::new(Box::new(MockBackend::new()), true, "", Some(hook)).unwrap();
        c.set("gain", Int(40)).unwrap();
        c.flush(T);
        c.set("gain", Int(41)).unwrap();
        c.close();
        let msgs: Vec<String> = seen
            .lock()
            .unwrap()
            .iter()
            .filter(|e| matches!(e, Event::Cancelled { .. }))
            .map(Event::message)
            .collect();
        assert_eq!(msgs, ["cancelled pending write gain=41 (controls closed)"]);
    }
}
