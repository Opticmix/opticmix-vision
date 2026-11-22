"""controls — 쓰기 가드: 같은 값 건너뛰기, 최신 값 합치기, 간격, 자동 상태 값 단독 쓰기 거부, 닫기."""
from __future__ import annotations

import threading
import time

import pytest

from opticmix_vision import controls as C
from opticmix_vision._ctrl_sim import SIM_RANGES, SimBackend


def _controls(**kw) -> tuple[C.Controls, SimBackend]:
    be = SimBackend(**kw)
    return C.Controls(be), be


def test_a_write_goes_out_once_and_is_reported():
    c, be = _controls()
    r = c.set("gain", 28)
    assert r.status == "pending" and r.name == "gain" and r.requested == 28
    done = c.flush(timeout=5)
    assert [x.status for x in done] == ["written"]
    assert be.writes == [("gain", 28, None)]
    assert C.write_counts()["gain"] == 1
    c.close()


def test_the_same_value_is_not_written_at_all():
    c, be = _controls()
    r = c.set("gain", be.values["gain"])
    assert r.status == "skipped_same"
    assert be.writes == [] and C.write_counts()["_total"] == 0
    c.close()


def test_all_backend_calls_happen_on_one_worker_thread():
    c, be = _controls()
    c.set("gain", 30)
    c.flush(timeout=5)
    c.close()
    assert len(be.threads) == 1 and threading.get_ident() not in be.threads


def test_requests_inside_the_interval_collapse_to_the_latest(monkeypatch):
    monkeypatch.setattr(C, "GROUP_MIN_INTERVAL_S", {"gain": 0.3})
    c, be = _controls()
    c.set("gain", 40)
    c.flush(timeout=5)                                     # 첫 쓰기 → 간격 시작
    results = [c.set("gain", v) for v in range(41, 60)]    # 드래그처럼 19번
    assert all(r.status == "pending" for r in results)
    done = c.flush(timeout=5)
    assert be.writes == [("gain", 40, None), ("gain", 59, None)]
    status = {r.requested: r.status for r in done}
    assert status[59] == "written"
    assert all(status[v] == "superseded" for v in range(41, 59))
    c.close()


def test_group_interval_is_kept(monkeypatch):
    monkeypatch.setattr(C, "GROUP_MIN_INTERVAL_S", {"gain": 0.3})
    c, _ = _controls()
    c.set("gain", 40)
    c.flush(timeout=5)
    t0 = time.monotonic()
    c.set("gain", 41)
    c.flush(timeout=5)
    assert time.monotonic() - t0 >= 0.25
    c.close()


def test_global_gap_spaces_writes_of_different_controls(monkeypatch):
    monkeypatch.setattr(C, "GLOBAL_MIN_GAP_S", 0.3)
    c, _ = _controls()
    c.set("gain", 40)
    c.flush(timeout=5)
    t0 = time.monotonic()
    c.set("brightness", 60)
    c.flush(timeout=5)
    assert time.monotonic() - t0 >= 0.25
    c.close()


def test_pending_reports_time_until_the_write(monkeypatch):
    monkeypatch.setattr(C, "GROUP_MIN_INTERVAL_S", {"gain": 2.0})
    c, _ = _controls()
    c.set("gain", 40)
    c.flush(timeout=5)
    r = c.set("gain", 41)
    assert 1.0 < r.due_in_s <= 2.0
    assert 1.0 < c.pending()["gain"] <= 2.0
    c.close()


def test_exposure_value_is_refused_while_auto_exposure_is_on():
    c, be = _controls(autos={"exposure": True})
    with pytest.raises(C.ControlStateError, match="auto_exposure"):
        c.set("exposure", -6)
    assert be.writes == []
    c.close()


def test_mode_and_value_together_are_one_write():
    c, be = _controls(autos={"exposure": True})
    res = c.set_many({"auto_exposure": "manual", "exposure": -6})
    assert {r.name for r in res} == {"auto_exposure", "exposure"}
    c.flush(timeout=5)
    assert be.writes == [("exposure", -6, False)]
    c.close()


def test_a_value_cannot_ride_along_with_switching_auto_on():
    c, _ = _controls()
    with pytest.raises(C.ControlStateError):
        c.set_many({"auto_exposure": "auto", "exposure": -6})
    c.close()


def test_switching_auto_on_drops_a_pending_value(monkeypatch):
    monkeypatch.setattr(C, "GROUP_MIN_INTERVAL_S", {"exposure": 0.3})
    c, be = _controls()
    c.set("exposure", -6)
    c.flush(timeout=5)
    v = c.set("exposure", -7)
    a = c.set("auto_exposure", "auto")
    done = {r.ticket: r.status for r in c.flush(timeout=5)}
    assert done[v.ticket] == "superseded" and done[a.ticket] == "written"
    assert be.writes[-1] == ("exposure", None, True)
    c.close()


def test_white_balance_follows_the_same_rule():
    c, _ = _controls(autos={"white_balance": True})
    with pytest.raises(C.ControlStateError, match="auto_white_balance"):
        c.set("white_balance", 100)
    c.close()


def test_out_of_range_and_unknown_choices_are_refused():
    c, be = _controls()
    with pytest.raises(ValueError, match="0..255"):
        c.set("gain", 999)
    with pytest.raises(ValueError):
        c.set("power_line_frequency", "auto")
    assert be.writes == []
    c.close()


def test_unknown_or_unsupported_names_raise_key_error():
    c, _ = _controls(ranges={k: r for k, r in SIM_RANGES.items() if k != "power_line_frequency"})
    assert "power_line_frequency" not in c.names()
    with pytest.raises(KeyError):
        c.set("power_line_frequency", "60hz")
    with pytest.raises(KeyError):
        c.get("iris")
    c.close()


def test_a_rejected_write_is_reported_as_failed():
    c, _ = _controls(fail_keys={"gain"})
    c.set("gain", 40)
    (r,) = c.flush(timeout=5)
    assert r.status == "failed" and "rejected" in r.error
    assert C.write_counts()["_total"] == 0
    c.close()


def test_wait_returns_the_final_result_of_given_requests(monkeypatch):
    monkeypatch.setattr(C, "GROUP_MIN_INTERVAL_S", {"gain": 0.2})
    c, _ = _controls()
    c.set("gain", 40)
    c.flush(timeout=5)
    r = c.set("gain", 41)
    (final,) = c.wait([r.ticket], timeout=5)
    assert final.status == "written" and c.result(r.ticket) == final
    c.close()


def test_closing_cancels_pending_writes(monkeypatch):
    monkeypatch.setattr(C, "GROUP_MIN_INTERVAL_S", {"gain": 10.0})
    c, be = _controls()
    c.set("gain", 40)
    c.flush(timeout=5)
    r = c.set("gain", 41)
    c.close()
    assert c.result(r.ticket).status == "cancelled"
    assert be.writes == [("gain", 40, None)] and be.closed
    with pytest.raises(C.ControlsUnavailable):
        c.set("gain", 42)


def test_a_backend_that_fails_to_start_is_unavailable():
    class Broken(SimBackend):
        def start(self):
            raise OSError("no such camera")
    with pytest.raises(C.ControlsUnavailable, match="no such camera"):
        C.Controls(Broken())


def test_warning_names_the_evidence(monkeypatch):
    monkeypatch.setattr(C, "WARN_AFTER_TOTAL", 2)
    c, _ = _controls()
    c.set("gain", 40)
    c.flush(timeout=5)
    with pytest.warns(RuntimeWarning, match="re-inserted"):
        c.set("brightness", 60)
        c.flush(timeout=5)
    c.close()


def test_unguarded_controls_do_not_touch_the_process_counters():
    be = SimBackend()
    c = C.Controls(be, guard=False)
    c.set("gain", 40)
    c.flush(timeout=5)
    assert be.writes == [("gain", 40, None)] and C.write_counts()["_total"] == 0
    c.close()


# --- 쓰기 뒤 다시 읽기 (2026-09-24: 게인 8 을 드라이버가 받았는데 0 으로 읽혔다) ---------------

def test_a_write_reports_what_the_camera_reads_back():
    c, _ = _controls()
    c.set("gain", 40)
    (r,) = c.flush(timeout=5)
    assert r.status == "written" and r.readback == 40 and r.kept is True
    c.close()


def test_a_write_the_camera_does_not_keep_is_visible(caplog):
    c, be = _controls(ignore_writes={"gain"})
    c.set("gain", 40)
    with caplog.at_level("WARNING", logger="opticmix_vision.controls"):
        (r,) = c.flush(timeout=5)
    assert r.status == "written"                  # 드라이버는 받았다
    assert r.readback == 32 and r.kept is False   # 카메라는 그대로다
    assert "gain" in caplog.text and "40" in caplog.text and "32" in caplog.text
    c.close()


def test_mode_and_value_are_both_read_back():
    c, _ = _controls(autos={"exposure": True})
    c.set_many({"auto_exposure": "manual", "exposure": -6})
    done = {r.name: r for r in c.flush(timeout=5)}
    assert done["auto_exposure"].readback == "manual" and done["auto_exposure"].kept is True
    assert done["exposure"].readback == -6 and done["exposure"].kept is True
    c.close()


def test_skipped_and_failed_writes_have_no_readback():
    c, be = _controls(fail_keys={"gain"})
    assert c.set("brightness", be.values["brightness"]).kept is None
    c.set("gain", 40)
    (r,) = [x for x in c.flush(timeout=5) if x.name == "gain"]   # flush 는 건너뛴 결과도 같이 준다
    assert r.status == "failed" and r.kept is None
    c.close()
