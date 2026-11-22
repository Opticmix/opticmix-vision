"""카메라 제어 — Weevia S1 이 광고하는 UVC 제어 14개를 읽고, 범위·기본값을 알려 주고, 쓴다.

AMCap 의 제어 창은 Windows UVC 드라이버의 표준 속성 페이지이고, 장치 디스크립터가 광고하는
제어를 그대로 보여 준다. 이 모듈은 그 전부를 코드로 연다.

**쓰기는 위험하다.** 시험한 유닛에서 제어를 쓴 직후 가끔 한쪽 눈 스트림이 멈췄고, USB 재삽입으로 돌아왔다.
횟수·속도 어느 쪽이 원인인지 증명되지 않았다(2026-09-24). 그래서 모든 쓰기는 워커 하나가 순서대로 하고, 같은 값은 쓰지 않고, 간격 안에
들어온 요청은 최신 값 하나로 합친다. **이 가드는 안전을 보장하지 않는다.** 안전한 사용법은 '시작할 때 한 번만 쓰기'다.

    with Device(cfg) as cam:
        c = cam.controls
        c.describe("gain")                     # 현재값·범위·기본값·검증 상태 (읽기만)
        c.set("gain", 28)                      # 기다리지 않는다 → WriteResult(status="pending")
        c.set_many({"auto_exposure": "manual", "exposure": -5})   # 모드 전환과 값은 한 번에
        c.flush()                              # 대기 중인 쓰기가 끝날 때까지

구현(2026-10-29): 제어 표·값 변환·쓰기 가드·워커 상태 기계는 Rust 코어(`rust/opticmix-vision`) 한 벌이고,
이 모듈은 그것을 `opticmix_vision._core` 로 부르는 얇은 래퍼다. 공개 이름·예외·결과 타입은 그대로다.
환경변수 OPTICMIX_WRITE_LOG=<경로> 를 주면 실제 쓰기마다 JSON 한 줄(되읽은 값 포함)을 그 파일에 덧붙인다(기본 꺼짐).
"""
from __future__ import annotations

import atexit
import logging
import warnings
import weakref
from dataclasses import dataclass
from typing import Any, Iterable, Literal, Mapping, NamedTuple, Protocol

from opticmix_vision import _native  # 절대 import — 시험이 이 파일을 패키지 밖 이름으로 다시 읽는다

__all__ = [
    "Controls", "ControlInfo", "WriteResult", "ControlSpec", "CONTROLS", "RawRange", "Backend",
    "ControlsUnavailable", "ControlStateError",
    "VENDOR_MIN_COMMAND_GAP_S", "GROUP_MIN_INTERVAL_S", "GLOBAL_MIN_GAP_S", "WARN_AFTER_TOTAL",
    "write_counts", "reset_write_counts",
]

log = logging.getLogger("opticmix_vision.controls")

# --- 가드 상수 ------------------------------------------------------------------------------
# 벤더 권고(2026-09-28): 노출 명령 사이 최소 12 ms — 90 fps 한 프레임(11.1 ms)보다 조금 길다.
# 다른 제어(게인·LED 등)에는 벤더 수치가 없어 같은 간격을 쓴다. 사망 기록에는 노출·게인·LED 연속 쓰기가 섞여 있었다.
# 값은 Rust 코어가 쓴다 — 쓰기 요청 직전마다 이 모듈 전역을 코어로 넘긴다(바꾸면 다음 요청부터 적용).
VENDOR_MIN_COMMAND_GAP_S = 0.012
GROUP_MIN_INTERVAL_S: dict[str, float] = {"exposure": VENDOR_MIN_COMMAND_GAP_S}   # 같은 그룹(= 같은 UVC 속성) 연속 쓰기 사이
GLOBAL_MIN_GAP_S = VENDOR_MIN_COMMAND_GAP_S     # 어느 제어든 두 쓰기 사이
WARN_AFTER_TOTAL = 3       # 이 프로세스의 쓰기 합계가 이 수에 닿을 때와 그 뒤 이 수마다 경고

Kind = Literal["int", "bool", "menu"]
Effect = Literal["verified", "partial", "unverified", "none_expected_mono"]
Status = Literal["written", "skipped_same", "pending", "superseded", "cancelled", "failed"]


class ControlsUnavailable(RuntimeError):
    """제어 통로를 열 수 없다 — 백엔드 연결 실패, 다른 장치, 닫힌 뒤의 호출."""


class ControlStateError(RuntimeError):
    """지금 모드에서 받을 수 없는 쓰기 — 예: 자동 노출 중에 노출값만 쓰기."""


class RawRange(NamedTuple):
    """백엔드가 준 장치 값 범위. default 는 장치가 주지 않으면 None."""
    minimum: int
    maximum: int
    step: int
    default: int | None
    can_auto: bool


class Backend(Protocol):
    """제어층이 부르는 백엔드. 모든 메서드는 Controls 의 워커 스레드 한 곳에서만 불린다."""
    name: str
    exposure_units: str
    note: str

    def start(self) -> None: ...
    def supports(self, key: str) -> bool: ...
    def query(self, key: str) -> RawRange | None: ...
    def read(self, key: str) -> tuple[int, bool | None]: ...
    def write(self, key: str, value: int | None, auto: bool | None) -> None: ...
    def close(self) -> None: ...


@dataclass(frozen=True)
class ControlSpec:
    name: str
    key: str                                  # 백엔드 키 = 가드 그룹 (Windows: KS 속성 하나)
    role: Literal["value", "auto"]            # auto = 그 키의 자동/수동 flags
    kind: Kind
    effect: Effect
    ledger: str | None = None                 # 내부 검증 기록 ID
    choices: tuple[str, ...] | None = None    # menu: 장치 값 0,1,2… 순서의 이름 (auto 역할은 manual/auto)
    mono: bool = False                        # 흑백 센서라 영상에 효과가 없을 것
    note: str = ""


# 표의 원본은 Rust 코어(spec.rs) — 두 벌이 되지 않게 거기서 만든다.
# _core 가 없으면(editable 설치) 빈 표 — 제어를 실제로 쓸 때 _native.core() 가 설치 안내로 실패한다.
CONTROLS: tuple[ControlSpec, ...] = (tuple(ControlSpec(**row) for row in _native.core().controls_table())
                                     if _native.available() else ())
_BY_NAME: dict[str, ControlSpec] = {c.name: c for c in CONTROLS}
_AUTO_NAME: dict[str, str] = {c.key: c.name for c in CONTROLS if c.role == "auto"}   # 그룹 → 모드 이름
_VALUE_NAME: dict[str, str] = {c.key: c.name for c in CONTROLS if c.role == "value" and c.key in _AUTO_NAME}


def _sync_guard() -> None:
    """이 모듈의 가드 상수를 코어로 넘긴다(시험·사용자가 바꿨을 수 있다)."""
    _native.core().set_guard_config({str(k): float(v) for k, v in GROUP_MIN_INTERVAL_S.items()},
                           float(GLOBAL_MIN_GAP_S), int(WARN_AFTER_TOTAL))


# --- 값 변환 (코어 위임) -----------------------------------------------------------------------

def _to_device(spec: ControlSpec, value: Any) -> int | bool:
    """사용자 값 → 백엔드 값. 틀린 값은 여기서 막는다(장치에 보내지 않는다)."""
    return _native.core().to_device(spec.name, value)


def _from_device(spec: ControlSpec, raw: int | None, auto: bool | None) -> Any:
    """백엔드 값 → 사용자 값. 모르는 것은 None."""
    return _native.core().from_device(spec.name, raw, auto)


def _same(value: int | None, auto: bool | None, cur_value: int | None, cur_auto: bool | None) -> bool:
    """요청 (value, auto) 가 장치 상태와 같으면 True. 모르는 것(None)은 같다고 보지 않는다."""
    return _native.core().same(value, auto, cur_value, cur_auto)


# --- 가드 상태 (프로세스 전역, 코어에 있다) ---------------------------------------------------------

def write_counts() -> dict[str, int]:
    """이 프로세스가 실제로 쓴 횟수 — 그룹별 + '_total'. 같은 값이라 건너뛴 것은 세지 않는다."""
    return dict(_native.core().write_counts())


def reset_write_counts() -> None:
    """USB 를 다시 꽂아 장치 상태가 처음으로 돌아간 경우에만 부른다."""
    _native.core().reset_write_counts()


def _not_before(group: str) -> float:
    """이 그룹에 다음으로 쓸 수 있는 가장 이른 단조 시각(코어 시계)."""
    _sync_guard()
    return _native.core().not_before(group)


def _record(group: str, now: float) -> tuple[float | None, int]:
    """쓰기 한 번을 남긴다 → (직전 쓰기로부터 간격, 합계)."""
    _sync_guard()
    return _native.core().record(group, now)


# --- 결과·정보 타입 ------------------------------------------------------------------------------

@dataclass(frozen=True)
class ControlInfo:
    """describe() 한 줄. 모르는 값은 None — 지어내지 않는다."""
    name: str
    kind: Kind
    current: Any
    minimum: int | None
    maximum: int | None
    step: int | None
    default: Any
    choices: tuple[str, ...] | None
    units: str                 # "raw" | "log2_s_nominal" | "100us"
    effect: Effect
    ledger: str | None
    group: str
    note: str = ""


@dataclass(frozen=True)
class WriteResult:
    """요청 하나의 결과. ticket 으로 나중에 최종 결과를 찾는다(wait/result).

    written 은 드라이버가 받았다는 뜻일 뿐이다 — 카메라가 값을 지켰는지는 readback/kept 를 본다
    (2026-09-24: 게인 8 을 드라이버가 받았는데 다시 읽으니 0 이었다).
    """
    name: str
    requested: Any
    status: Status
    due_in_s: float | None = None
    error: str | None = None
    ticket: int = 0
    readback: Any = None          # written 일 때 쓴 직후 다시 읽은 값 (못 읽었으면 None)

    @property
    def kept(self) -> bool | None:
        """카메라가 요청한 값을 지켰는가. written 이 아니거나 다시 읽지 못했으면 None."""
        if self.status != "written" or self.readback is None:
            return None
        return self.readback == self.requested


def _result(t: tuple) -> WriteResult:
    return WriteResult(*t)


def _on_event(kind: str, message: str, is_warning: bool) -> None:
    """코어 워커의 알림 → 예전과 같은 로그 줄·RuntimeWarning."""
    if kind == "warn_total":
        warnings.warn(message, RuntimeWarning, stacklevel=2)
    elif is_warning:
        log.warning("%s", message)
    else:
        log.info("%s", message)


_live: "weakref.WeakSet[Controls]" = weakref.WeakSet()


@atexit.register
def _close_all() -> None:
    for c in list(_live):
        try:
            c.close()
        except Exception:  # noqa: BLE001 — 종료 중
            pass


class Controls:
    """카메라 하나의 제어 전부. 읽기는 바로 하고, 쓰기는 워커 하나가 가드를 지키며 순서대로 한다.

    guard=False 는 합성 카메라용이다 — 간격·카운터를 쓰지 않는다(실제 장치에는 쓰지 말 것).
    """

    def __init__(self, backend: Backend, *, guard: bool = True, note: str = "") -> None:
        _sync_guard()
        # _ctrl_dshow.DShowBackend 는 안의 Rust 백엔드를 그대로 넘긴다(Python 을 거치지 않는다)
        inner = getattr(backend, "_core_backend", None)
        self._core = _native.core().CoreControls(inner if inner is not None else backend, guard, note, _on_event)
        self.backend_name: str = self._core.backend_name
        self.backend_note: str = self._core.backend_note
        _live.add(self)

    def names(self) -> tuple[str, ...]:
        """이 카메라에서 쓸 수 있는 제어 이름 — 이름 표 순서, 백엔드가 지원하는 것만."""
        return tuple(self._core.names())

    # ---- 쓰기 ----------------------------------------------------------------------------------
    def set(self, name: str, value: Any) -> WriteResult:
        """기다리지 않는다. 결과는 pending / skipped_same. 최종 결과는 wait()·result()·flush()."""
        return self.set_many({name: value})[0]

    def set_many(self, values: Mapping[str, Any]) -> list[WriteResult]:
        """같은 그룹(예: auto_exposure + exposure)은 한 번의 쓰기로 합친다. 검증이 하나라도 실패하면 아무것도 넣지 않는다."""
        _sync_guard()
        return [_result(t) for t in self._core.set_many(list(values.items()))]

    def flush(self, timeout: float | None = None) -> list[WriteResult]:
        """대기 중인 쓰기가 끝날 때까지 기다린 뒤, 지난 flush 이후 끝난 결과를 돌려준다."""
        return [_result(t) for t in self._core.flush(timeout)]

    def wait(self, tickets: Iterable[int], timeout: float | None = None) -> list[WriteResult]:
        """주어진 요청들이 끝날 때까지 기다려 최종 결과를 돌려준다(시간 안에 안 끝난 것은 pending)."""
        return [_result(t) for t in self._core.wait([int(t) for t in tickets], timeout)]

    def result(self, ticket: int) -> WriteResult | None:
        """끝났으면 최종 결과, 아직이면 None."""
        t = self._core.result(int(ticket))
        return None if t is None else _result(t)

    def pending(self) -> dict[str, float]:
        """대기 중인 요청 이름 → 쓰기까지 남은 초."""
        return dict(self._core.pending())

    # ---- 닫기 ----------------------------------------------------------------------------------
    @property
    def closed(self) -> bool:
        return self._core.closed

    def close(self) -> None:
        """대기 중인 쓰기는 보내지 않고 cancelled 로 끝낸다 — 닫히는 장치에 쓰지 않는다."""
        self._core.close()
        _live.discard(self)

    def __enter__(self) -> "Controls":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ---- 읽기 ----------------------------------------------------------------------------------
    def get(self, name: str) -> Any:
        """장치의 지금 값. 모드를 읽을 수 없는 백엔드면 None."""
        return self._core.get(name)

    def describe(self, name: str | None = None) -> Any:
        """이름 하나면 ControlInfo, 없으면 {이름: ControlInfo} 전부. 읽기만 한다."""
        if name is None:
            return {n: self.describe(n) for n in self.names()}
        return ControlInfo(**self._core.describe(name))

    def snapshot(self) -> dict[str, Any]:
        """지금 값 전부(읽을 수 없는 것은 뺀다). restore() 로 되돌린다."""
        return dict(self._core.snapshot())

    def restore(self, snap: Mapping[str, Any]) -> list[WriteResult]:
        """스냅샷 값으로 되돌린다 — 다른 것만 쓴다. 자동으로 돌아가는 그룹의 값은 뺀다."""
        _sync_guard()
        return [_result(t) for t in self._core.restore([(n, v) for n, v in snap.items() if v is not None])]

    def plan_reset(self, *, include_mono: bool = False) -> dict[str, tuple[Any, Any]]:
        """기본값 복원으로 바뀔 것 {이름: (지금, 기본값)} — 쓰지 않는다.

        모드(자동/수동)는 건드리지 않고, 자동 상태인 값도 뺀다(AMCap 기본값 버튼과 같다).
        흑백 무효 제어는 include_mono=True 일 때만. 장치가 기본값을 주지 않은 제어는 뺀다.
        """
        return {n: (c, d) for n, c, d in self._core.plan_reset(include_mono)}

    def reset_to_defaults(self, *, include_mono: bool = False) -> list[WriteResult]:
        """plan_reset() 을 쓴다. 기다리지 않는다."""
        _sync_guard()
        return [_result(t) for t in self._core.reset_to_defaults(include_mono)]
