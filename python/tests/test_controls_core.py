"""controls — 이름 표, 값 변환, 가드 상태. 워커 없이 순수 함수만."""
from __future__ import annotations

import pytest

from opticmix_vision import controls as C


def test_the_table_has_the_fourteen_advertised_controls():
    assert [c.name for c in C.CONTROLS] == [
        "brightness", "contrast", "hue", "saturation", "sharpness", "gamma",
        "white_balance", "auto_white_balance", "backlight_compensation", "gain",
        "power_line_frequency", "exposure", "auto_exposure", "auto_exposure_priority"]


def test_auto_and_value_of_one_property_share_a_guard_group():
    by = {c.name: c for c in C.CONTROLS}
    assert by["auto_exposure"].key == by["exposure"].key == "exposure"
    assert by["auto_white_balance"].key == by["white_balance"].key == "white_balance"


def test_mono_controls_are_flagged():
    assert {c.name for c in C.CONTROLS if c.mono} == {"hue", "saturation", "white_balance", "auto_white_balance"}
    assert all(c.effect == "none_expected_mono" for c in C.CONTROLS if c.mono)


@pytest.mark.parametrize("name,value,device", [
    ("gain", 28, 28), ("gain", 28.0, 28), ("exposure", -5, -5),
    ("auto_exposure", "manual", False), ("auto_exposure", "auto", True),
    ("auto_white_balance", True, True), ("auto_exposure_priority", False, 0),
    ("power_line_frequency", "60hz", 2), ("power_line_frequency", "disabled", 0),
])
def test_user_values_become_device_values(name, value, device):
    assert C._to_device(C._BY_NAME[name], value) == device


@pytest.mark.parametrize("name,value", [
    ("gain", 28.5), ("gain", True), ("gain", "28"), ("auto_exposure", "on"),
    ("power_line_frequency", "55hz"), ("auto_exposure_priority", 2),
])
def test_wrong_values_are_refused_before_the_camera_sees_them(name, value):
    with pytest.raises((ValueError, TypeError)):
        C._to_device(C._BY_NAME[name], value)


@pytest.mark.parametrize("name,raw,auto,user", [
    ("gain", 28, None, 28), ("auto_exposure", -5, True, "auto"), ("auto_exposure", -5, False, "manual"),
    ("auto_exposure", -5, None, None), ("power_line_frequency", 1, None, "50hz"),
    ("power_line_frequency", 3, None, 3), ("auto_exposure_priority", 1, None, True),
])
def test_device_values_become_user_values(name, raw, auto, user):
    assert C._from_device(C._BY_NAME[name], raw, auto) == user


def test_same_value_and_mode_is_same():
    assert C._same(5, None, 5, None)
    assert not C._same(5, None, 6, None)
    assert C._same(5, False, 5, False)
    assert not C._same(5, False, 5, None)          # 모르는 모드는 같다고 보지 않는다
    assert C._same(None, True, 1, True)            # 자동으로 가는 쓰기에 값은 상관없다
    assert not C._same(None, True, 1, False)


def test_guard_state_counts_and_spacing(monkeypatch):
    monkeypatch.setattr(C, "GROUP_MIN_INTERVAL_S", {"exposure": 5.0})
    monkeypatch.setattr(C, "GLOBAL_MIN_GAP_S", 1.0)
    C.reset_write_counts()
    assert C._not_before("exposure") == 0.0
    gap, total = C._record("exposure", 100.0)
    assert gap is None and total == 1
    assert C._not_before("exposure") == 105.0
    assert C._not_before("gain") == 101.0
    gap, total = C._record("gain", 101.5)
    assert gap == pytest.approx(1.5) and total == 2
    assert C.write_counts() == {"exposure": 1, "gain": 1, "_total": 2}
    C.reset_write_counts()
    assert C.write_counts() == {"_total": 0}


def test_default_spacing_is_the_vendor_minimum_between_exposure_commands():
    """벤더 권고 12 ms(2026-09-28). 바꾸려면 근거와 함께 이 테스트도 바꾼다.
    conftest 가 매 테스트 간격을 덮어쓰므로 파일을 새로 로드해서 실제 기본값을 본다."""
    import importlib.util
    import sys
    spec = importlib.util.spec_from_file_location("_controls_fresh", C.__file__)
    m = sys.modules[spec.name] = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(m)
    finally:
        sys.modules.pop(spec.name, None)
    assert m.VENDOR_MIN_COMMAND_GAP_S == 0.012
    assert m.GROUP_MIN_INTERVAL_S == {"exposure": m.VENDOR_MIN_COMMAND_GAP_S}
    assert m.GLOBAL_MIN_GAP_S == m.VENDOR_MIN_COMMAND_GAP_S
