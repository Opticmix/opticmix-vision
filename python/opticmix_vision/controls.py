"""Camera controls: read the 14 UVC controls the Weevia S1 advertises, report their ranges and defaults, and write them.

The AMCap control window is the standard property page of the Windows UVC driver and shows the
controls the device descriptor advertises, as they are. This module opens all of them from code.

**Writing is risky.** On the units we have tested, a control write has occasionally been followed by one
eye's stream stopping; re-inserting the USB cable brought it back. Whether the count or the rate of writes is the cause has not been
proven (2026-09-24). So all writes go through one worker in order, identical values are not written, and
requests that arrive within the minimum interval are merged into the latest value.
**This guard does not guarantee safety.** The safe way to use it is "write once, at start-up".

    with Device(cfg) as cam:
        c = cam.controls
        c.describe("gain")                     # current value, range, default, verification state (read only)
        c.set("gain", 28)                      # does not wait -> WriteResult(status="pending")
        c.set_many({"auto_exposure": "manual", "exposure": -5})   # mode switch and value in one go
        c.flush()                              # until pending writes are done

Implementation (2026-10-29): the control table, value conversion, write guard and worker state machine
live once, in the Rust core (`rust/opticmix-vision`); this module is a thin wrapper that calls it as
`opticmix_vision._core`. Public names, exceptions and result types are unchanged.
Set the environment variable OPTICMIX_WRITE_LOG=<path> to append one JSON line per actual write
(including the read-back value) to that file (off by default).
"""
from __future__ import annotations

import atexit
import logging
import warnings
import weakref
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Literal, Mapping, NamedTuple, Protocol

from opticmix_vision import _native  # absolute import: tests re-load this file under a name outside the package

if TYPE_CHECKING:
    from opticmix_vision.presets import PresetLoad

__all__ = [
    "Controls", "ControlInfo", "WriteResult", "ControlSpec", "CONTROLS", "RawRange", "Backend",
    "ControlsUnavailable", "ControlStateError",
    "VENDOR_MIN_COMMAND_GAP_S", "GROUP_MIN_INTERVAL_S", "GLOBAL_MIN_GAP_S", "WARN_AFTER_TOTAL",
    "write_counts", "reset_write_counts",
]

log = logging.getLogger("opticmix_vision.controls")

# --- Guard constants ---------------------------------------------------------------------------
# Vendor recommendation (2026-09-28): at least 12 ms between exposure commands, slightly longer than one
# frame at 90 fps (11.1 ms). Other controls (gain, LED, ...) have no vendor figure, so the same gap is used.
# The recorded stream stalls involved back-to-back exposure, gain and LED writes.
# The Rust core applies these values: this module's globals are passed to the core right before every
# write request (a change takes effect from the next request).
VENDOR_MIN_COMMAND_GAP_S = 0.012
GROUP_MIN_INTERVAL_S: dict[str, float] = {"exposure": VENDOR_MIN_COMMAND_GAP_S}   # between consecutive writes to the same group (= same UVC property)
GLOBAL_MIN_GAP_S = VENDOR_MIN_COMMAND_GAP_S     # between any two writes, whatever the control
WARN_AFTER_TOTAL = 3       # warn when this process's total write count reaches this number, then every this many after

Kind = Literal["int", "bool", "menu"]
Effect = Literal["verified", "partial", "unverified", "none_expected_mono"]
Status = Literal["written", "skipped_same", "pending", "superseded", "cancelled", "failed"]


class ControlsUnavailable(RuntimeError):
    """The control channel cannot be opened: backend connection failed, a different device, or a call after close."""


class ControlStateError(RuntimeError):
    """A write that the current mode cannot accept, e.g. writing only the exposure value while auto exposure is on."""


class RawRange(NamedTuple):
    """Device value range reported by the backend. default is None if the device does not report one."""
    minimum: int
    maximum: int
    step: int
    default: int | None
    can_auto: bool


class Backend(Protocol):
    """The backend the control layer calls. Every method is called only from the one Controls worker thread."""
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
    key: str                                  # backend key = guard group (Windows: one KS property)
    role: Literal["value", "auto"]            # auto = the auto/manual flags of that key
    kind: Kind
    effect: Effect
    ledger: str | None = None                 # verification record ID
    choices: tuple[str, ...] | None = None    # menu: names in device-value order 0,1,2... (manual/auto for the auto role)
    mono: bool = False                        # expected to have no effect on the image because the sensor is monochrome
    note: str = ""


# The table's source is the Rust core (spec.rs); it is built from there so there are not two copies.
# Without _core (editable install) the table is empty; actually using a control then fails in
# _native.core() with install instructions.
CONTROLS: tuple[ControlSpec, ...] = (tuple(ControlSpec(**row) for row in _native.core().controls_table())
                                     if _native.available() else ())
_BY_NAME: dict[str, ControlSpec] = {c.name: c for c in CONTROLS}
_AUTO_NAME: dict[str, str] = {c.key: c.name for c in CONTROLS if c.role == "auto"}   # group -> mode name
_VALUE_NAME: dict[str, str] = {c.key: c.name for c in CONTROLS if c.role == "value" and c.key in _AUTO_NAME}


def _sync_guard() -> None:
    """Pass this module's guard constants to the core (tests or users may have changed them)."""
    _native.core().set_guard_config({str(k): float(v) for k, v in GROUP_MIN_INTERVAL_S.items()},
                           float(GLOBAL_MIN_GAP_S), int(WARN_AFTER_TOTAL))


# --- Value conversion (delegated to the core) --------------------------------------------------

def _to_device(spec: ControlSpec, value: Any) -> int | bool:
    """User value -> backend value. Invalid values are stopped here (never sent to the device)."""
    return _native.core().to_device(spec.name, value)


def _from_device(spec: ControlSpec, raw: int | None, auto: bool | None) -> Any:
    """Backend value -> user value. Unknown is None."""
    return _native.core().from_device(spec.name, raw, auto)


def _same(value: int | None, auto: bool | None, cur_value: int | None, cur_auto: bool | None) -> bool:
    """True if the requested (value, auto) equals the device state. Unknown (None) is never treated as equal."""
    return _native.core().same(value, auto, cur_value, cur_auto)


# --- Guard state (process-wide, held in the core) ----------------------------------------------

def write_counts() -> dict[str, int]:
    """Number of actual writes by this process, per group plus '_total'. Writes skipped as identical are not counted."""
    return dict(_native.core().write_counts())


def reset_write_counts() -> None:
    """Call only when USB was re-plugged and the device state is back to its initial state."""
    _native.core().reset_write_counts()


def _not_before(group: str) -> float:
    """Earliest monotonic time (core clock) at which this group may be written next."""
    _sync_guard()
    return _native.core().not_before(group)


def _record(group: str, now: float) -> tuple[float | None, int]:
    """Record one write -> (gap since the previous write, total)."""
    _sync_guard()
    return _native.core().record(group, now)


# --- Result and info types ---------------------------------------------------------------------

@dataclass(frozen=True)
class ControlInfo:
    """One row of describe(). Unknown values are None, never made up."""
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
    """Result of one request. Use ticket to look up the final result later (wait/result).

    written only means the driver accepted it. Whether the camera kept the value is shown by readback/kept
    (2026-09-24: the driver accepted gain 8, but reading it back gave 0).
    """
    name: str
    requested: Any
    status: Status
    due_in_s: float | None = None
    error: str | None = None
    ticket: int = 0
    readback: Any = None          # when written, the value read back right after the write (None if it could not be read)

    @property
    def kept(self) -> bool | None:
        """Whether the camera kept the requested value. None if not written or if it could not be read back."""
        if self.status != "written" or self.readback is None:
            return None
        return self.readback == self.requested


def _result(t: tuple) -> WriteResult:
    return WriteResult(*t)


def _on_event(kind: str, message: str, is_warning: bool) -> None:
    """Core worker notification -> the same log line / RuntimeWarning as before."""
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
        except Exception:  # noqa: BLE001 — shutting down
            pass


class Controls:
    """All controls of one camera. Reads happen immediately; writes are done in order by one worker that keeps the guard.

    guard=False is for the synthetic camera: no gaps and no counters (do not use it on a real device).
    """

    def __init__(self, backend: Backend, *, guard: bool = True, note: str = "") -> None:
        _sync_guard()
        # _ctrl_dshow.DShowBackend passes its inner Rust backend straight through (no Python in between)
        inner = getattr(backend, "_core_backend", None)
        self._core = _native.core().CoreControls(inner if inner is not None else backend, guard, note, _on_event)
        self.backend_name: str = self._core.backend_name
        self.backend_note: str = self._core.backend_note
        # The camera's USB name when the backend has confirmed it (DirectShow: 'WN Dual Camera'), "synthetic" for the
        # simulated controls, None when the backend cannot tell (OpenCV). Presets record it and check it on load.
        self.model: str | None = getattr(backend, "model", None)
        _live.add(self)

    def names(self) -> tuple[str, ...]:
        """Control names usable on this camera, in table order, only those the backend supports."""
        return tuple(self._core.names())

    # ---- Writing -----------------------------------------------------------------------------
    def set(self, name: str, value: Any) -> WriteResult:
        """Does not wait. The result is pending / skipped_same. Final results come from wait(), result(), flush()."""
        return self.set_many({name: value})[0]

    def set_many(self, values: Mapping[str, Any]) -> list[WriteResult]:
        """Merges the same group (e.g. auto_exposure + exposure) into one write. If any validation fails, nothing is queued."""
        _sync_guard()
        return [_result(t) for t in self._core.set_many(list(values.items()))]

    def flush(self, timeout: float | None = None) -> list[WriteResult]:
        """Waits until pending writes are done, then returns the results finished since the last flush."""
        return [_result(t) for t in self._core.flush(timeout)]

    def wait(self, tickets: Iterable[int], timeout: float | None = None) -> list[WriteResult]:
        """Waits until the given requests are done and returns their final results (those not done in time are pending)."""
        return [_result(t) for t in self._core.wait([int(t) for t in tickets], timeout)]

    def result(self, ticket: int) -> WriteResult | None:
        """The final result if done, None if not yet."""
        t = self._core.result(int(ticket))
        return None if t is None else _result(t)

    def pending(self) -> dict[str, float]:
        """Pending request name -> seconds left until the write."""
        return dict(self._core.pending())

    # ---- Closing -----------------------------------------------------------------------------
    @property
    def closed(self) -> bool:
        return self._core.closed

    def close(self) -> None:
        """Pending writes are not sent; they end as cancelled. Nothing is written to a device that is closing."""
        self._core.close()
        _live.discard(self)

    def __enter__(self) -> "Controls":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ---- Reading -----------------------------------------------------------------------------
    def get(self, name: str) -> Any:
        """The device's current value. None if the backend cannot read the mode."""
        return self._core.get(name)

    def describe(self, name: str | None = None) -> Any:
        """ControlInfo for one name, or {name: ControlInfo} for all if no name is given. Read only."""
        if name is None:
            return {n: self.describe(n) for n in self.names()}
        return ControlInfo(**self._core.describe(name))

    def snapshot(self) -> dict[str, Any]:
        """All current values (those that cannot be read are left out). Restore them with restore()."""
        return dict(self._core.snapshot())

    def restore(self, snap: Mapping[str, Any]) -> list[WriteResult]:
        """Restores snapshot values, writing only the ones that differ. Values of groups that are returned to auto are left out."""
        _sync_guard()
        return [_result(t) for t in self._core.restore([(n, v) for n, v in snap.items() if v is not None])]

    def plan_reset(self, *, include_mono: bool = False) -> dict[str, tuple[Any, Any]]:
        """What restoring defaults would change, as {name: (current, default)}. Does not write.

        The mode (auto/manual) is not touched, and values currently in auto are left out (same as the
        AMCap Default button). Controls with no effect on a monochrome sensor are included only with
        include_mono=True. Controls for which the device reports no default are left out.
        """
        return {n: (c, d) for n, c, d in self._core.plan_reset(include_mono)}

    def reset_to_defaults(self, *, include_mono: bool = False) -> list[WriteResult]:
        """Writes plan_reset(). Does not wait."""
        _sync_guard()
        return [_result(t) for t in self._core.reset_to_defaults(include_mono)]

    # ---- Presets (opticmix_vision.presets) ---------------------------------------------------
    def save_preset(self, path: str | Path, name: str | None = None, note: str | None = None) -> Path:
        """Writes every readable control (value and units), the model and the SDK version to a JSON file. Reads only."""
        from opticmix_vision import presets
        return presets._save(self, path, name, note)

    def plan_preset(self, path: str | Path, *, force: bool = False,
                    include_mono: bool = False) -> dict[str, tuple[Any, Any]]:
        """What load_preset() would change, as {name: (current, preset)}. Checks the whole preset; writes nothing."""
        from opticmix_vision import presets
        return presets._plan(self, path, force, include_mono)[0]

    def load_preset(self, path: str | Path, *, force: bool = False, include_mono: bool = False,
                    timeout: float | None = 10.0) -> "PresetLoad":
        """Writes the preset's values that differ, in one set_many(), and waits up to timeout for the results.

        Raises presets.PresetError, with nothing written, if the file is not a preset, was saved from another model
        (PresetModelError, unless force), names a control this camera lacks, or has other units or a value out of
        range. Controls with no effect on the mono sensor are written only with include_mono=True (as plan_reset).
        """
        from opticmix_vision import presets
        return presets._load(self, path, force, include_mono, timeout)
