"""Rust 코어 바인딩(_core) — 기존 시험이 보지 않는 것만: GIL 교착 없음, GC 로 닫혀도 멈추지 않음, 표가 한 벌."""
from __future__ import annotations

import gc
import threading
import time

from opticmix_vision import _core
from opticmix_vision import controls as C
from opticmix_vision._ctrl_sim import SimBackend


def test_a_python_backend_write_finishes_while_the_caller_waits():
    """워커가 Python 백엔드를 부르려면 GIL 이 필요하다 — 기다리는 쪽이 GIL 을 놓지 않으면 여기서 멈춘다."""
    c = C.Controls(SimBackend())
    r = c.set("gain", 40)
    t0 = time.monotonic()
    (final,) = c.wait([r.ticket], timeout=5)
    assert final.status == "written" and time.monotonic() - t0 < 5
    c.close()


def test_reads_from_two_python_threads_do_not_deadlock():
    c = C.Controls(SimBackend())
    seen: list = []

    def reader():
        for _ in range(50):
            seen.append(c.get("gain"))

    ts = [threading.Thread(target=reader) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=10)
    assert not any(t.is_alive() for t in ts)
    assert seen == [32] * 100
    c.close()


def test_garbage_collecting_open_controls_does_not_hang_and_closes_the_backend():
    be = SimBackend()
    c = C.Controls(be)
    C._live.discard(c)
    t0 = time.monotonic()
    del c
    gc.collect()
    assert time.monotonic() - t0 < 2.0
    end = time.monotonic() + 5
    while not be.closed and time.monotonic() < end:
        time.sleep(0.01)
    assert be.closed


def test_the_control_table_comes_from_the_core():
    assert [c.name for c in C.CONTROLS] == [r["name"] for r in _core.controls_table()]
    assert _core.WEEVIA_S1_NAME == "WN Dual Camera"


def test_a_backend_exception_type_survives_the_round_trip():
    class Odd(SimBackend):
        def read(self, key):
            raise LookupError("no such register")
    c = C.Controls(Odd())
    try:
        c.get("gain")
    except LookupError as exc:
        assert "no such register" in str(exc)
    else:
        raise AssertionError("expected LookupError")
    c.close()


def test_a_dshow_backend_is_built_without_touching_com_and_carries_the_rust_backend():
    import sys

    import pytest

    if sys.platform != "win32":
        pytest.skip("DirectShow is Windows-only")
    from opticmix_vision import _ctrl_dshow as D
    be = D.DShowBackend(7)                       # 만들기만 — 열거·바인드는 start() 에서(이 시험은 부르지 않는다)
    assert isinstance(be._core_backend, _core.DShowBackend)
    assert be.raw_get(D.CAMERA, 4) == {"error": "no camera interface"}   # 시작 전: COM 객체 없음
    be.close()


def test_device_name_constants_agree_with_the_core():
    from opticmix_vision import _ctrl_dshow as D
    from opticmix_vision import device
    assert device.WEEVIA_S1_NAME == D.EXPECTED_NAMES[0] == _core.WEEVIA_S1_NAME == "WN Dual Camera"
