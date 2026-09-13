"""led — 기본 컨트롤은 CAP_PROP_BACKLIGHT(제조사 확인 2026-08-31), prop=None 명시 시에만 Unavailable,
value_range 없으면 raw set/get 만, 있으면 정규화. Device+가짜 VideoCapture 로 cv2.CAP_PROP_BACKLIGHT 경로."""

from __future__ import annotations

import pytest

from opticmix_vision.device import Device, DeviceConfig
from opticmix_vision.led import (
    DEFAULT_LED_PROP, RANGE_UNKNOWN_REASON, UNAVAILABLE_REASON, LedControl, LedControlUnavailable,
)
from tests.test_device import FakeCapture


class FakeProps:
    def __init__(self) -> None:
        self.values: dict[str, float] = {"CAP_PROP_BACKLIGHT": 3.0, "CAP_PROP_GAIN": 4.0}
        self.writes: list[tuple[str, float]] = []

    def get_prop(self, prop):
        if str(prop) not in self.values:
            raise AttributeError(prop)
        return self.values[str(prop)]

    def set_prop(self, prop, value):
        self.writes.append((str(prop), float(value)))
        self.values[str(prop)] = float(value)
        return True


def test_default_prop_is_backlight_compensation():
    import cv2
    assert DEFAULT_LED_PROP == "CAP_PROP_BACKLIGHT"
    assert hasattr(cv2, DEFAULT_LED_PROP)                    # Device 가 getattr(cv2, name) 으로 푼다
    p = FakeProps()
    led = LedControl(p)
    assert led.available and led.prop == "CAP_PROP_BACKLIGHT"
    assert led.get() == 3.0
    assert led.set(7) == 7.0
    assert p.writes == [("CAP_PROP_BACKLIGHT", 7.0)]


def test_unavailable_only_when_prop_is_explicit_none():
    led = LedControl(FakeProps(), prop=None)
    assert led.available is False
    with pytest.raises(LedControlUnavailable) as ei:
        led.get()
    assert str(ei.value) == UNAVAILABLE_REASON and "Backlight" in str(ei.value)
    with pytest.raises(NotImplementedError):                 # 상위 타입으로도 잡힌다
        led.set(1)


def test_prop_override_by_name():
    p = FakeProps()
    led = LedControl(p, prop="CAP_PROP_GAIN")
    assert led.get() == 4.0 and led.set(9) == 9.0
    assert p.writes == [("CAP_PROP_GAIN", 9.0)]


def test_normalized_api_requires_range_raw_always_works():
    p = FakeProps()
    led = LedControl(p)
    with pytest.raises(LedControlUnavailable) as ei:
        led.set_level(0.5)
    assert str(ei.value) == RANGE_UNKNOWN_REASON
    with pytest.raises(LedControlUnavailable):
        led.get_level()
    assert led.set(2) == 2.0                                 # raw 는 범위 없이도 된다

    led = LedControl(p, value_range=(0, 10))
    assert led.set_level(0.5) == pytest.approx(0.5)
    assert p.values["CAP_PROP_BACKLIGHT"] == pytest.approx(5.0)
    assert led.get_level() == pytest.approx(0.5)
    with pytest.raises(ValueError):
        led.set_level(1.5)
    with pytest.raises(ValueError):
        LedControl(p, value_range=(10, 10))


def test_probe_reads_only_and_marks_missing():
    p = FakeProps()
    out = LedControl(p).probe(["CAP_PROP_BACKLIGHT", "CAP_PROP_GAIN", "CAP_PROP_NOPE"])
    assert out == {"CAP_PROP_BACKLIGHT": 3.0, "CAP_PROP_GAIN": 4.0, "CAP_PROP_NOPE": None}
    assert p.writes == []


def test_device_path_uses_cv2_CAP_PROP_BACKLIGHT_id():
    """Device.get_prop/set_prop 이 'CAP_PROP_BACKLIGHT' 를 cv2 정수 ID 로 풀어 VideoCapture.set/get 에 보내는지."""
    import cv2
    cap = FakeCapture([])
    cap.props[cv2.CAP_PROP_BACKLIGHT] = 1.0
    with Device(DeviceConfig(layout="sbs", lr_order="first_is_left"), capture=cap) as cam:
        led = LedControl(cam)                                # 기본 prop
        assert led.get() == 1.0
        assert led.set(5) == 5.0
        assert (cv2.CAP_PROP_BACKLIGHT, 5.0) in cap.set_calls
        assert cap.props[cv2.CAP_PROP_BACKLIGHT] == 5.0
        led_r = LedControl(cam, value_range=(0, 8))
        assert led_r.set_level(0.25) == pytest.approx(0.25)
        assert cap.props[cv2.CAP_PROP_BACKLIGHT] == pytest.approx(2.0)
