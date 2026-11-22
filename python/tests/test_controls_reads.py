"""controls — 읽기: names/describe, 스냅샷, 기본값 복원 계획."""
from __future__ import annotations

from opticmix_vision import controls as C
from opticmix_vision._ctrl_sim import SimBackend


def _controls(**kw):
    be = SimBackend(**kw)
    return C.Controls(be), be


def test_names_follow_the_table_and_the_backend():
    c, _ = _controls()
    assert c.names() == tuple(x.name for x in C.CONTROLS)
    c.close()


def test_describe_reports_range_default_units_and_verification():
    c, be = _controls()
    g = c.describe("gain")
    assert (g.minimum, g.maximum, g.step, g.default) == (0, 255, 1, 32)
    assert g.current == be.values["gain"] and g.effect == "verified" and g.ledger is None
    e = c.describe("exposure")
    assert e.units == "log2_s_nominal" and e.group == "exposure"
    ae = c.describe("auto_exposure")
    assert ae.kind == "menu" and ae.choices == ("manual", "auto") and ae.current == "manual" and ae.default is None
    pl = c.describe("power_line_frequency")
    assert pl.choices == ("disabled", "50hz", "60hz") and pl.current == "60hz" and pl.minimum is None
    assert c.describe("hue").effect == "none_expected_mono"
    assert set(c.describe()) == set(c.names())
    c.close()


def test_describe_without_a_range_invents_nothing():
    c, _ = _controls(unranged={"sharpness"})
    s = c.describe("sharpness")
    assert s.minimum is None and s.maximum is None and s.default is None and s.current == 28
    c.close()


def test_snapshot_and_restore_write_only_what_changed():
    c, be = _controls()
    snap = c.snapshot()
    c.set_many({"gain": 100, "brightness": 10})
    c.flush(timeout=5)
    be.writes.clear()
    c.restore(snap)
    c.flush(timeout=5)
    assert sorted(be.writes) == sorted([("gain", 32, None), ("brightness", 50, None)])
    c.close()


def test_restore_brings_manual_mode_with_the_exposure_value():
    c, be = _controls()                                   # 수동, -5
    snap = c.snapshot()
    c.set("auto_exposure", "auto")
    c.flush(timeout=5)
    be.writes.clear()
    c.restore(snap)
    c.flush(timeout=5)
    assert be.writes == [("exposure", None, False)]      # 값은 그대로라 모드만 보낸다
    assert c.get("auto_exposure") == "manual" and c.get("exposure") == -5
    c.close()


def test_plan_reset_lists_only_values_that_differ_from_device_defaults():
    c, be = _controls()
    assert c.plan_reset() == {}
    be.values.update(gain=100, hue=1, exposure=-8)
    assert c.plan_reset() == {"gain": (100, 32), "exposure": (-8, -5)}      # 흑백 무효 제어는 빠진다
    assert c.plan_reset(include_mono=True)["hue"] == (1, 64)
    c.close()


def test_plan_reset_leaves_modes_and_auto_values_alone():
    c, be = _controls(autos={"exposure": True})
    be.values["exposure"] = -8
    plan = c.plan_reset()
    assert "exposure" not in plan and "auto_exposure" not in plan
    c.close()


def test_plan_reset_skips_controls_without_a_device_default():
    c, be = _controls(unranged={"sharpness"})
    be.values["sharpness"] = 3
    assert "sharpness" not in c.plan_reset()
    c.close()


def test_reset_to_defaults_writes_the_plan():
    c, be = _controls()
    be.values.update(gain=100, backlight_compensation=96)
    c.reset_to_defaults()
    c.flush(timeout=5)
    assert sorted(be.writes) == [("backlight_compensation", 32, None), ("gain", 32, None)]
    c.close()
