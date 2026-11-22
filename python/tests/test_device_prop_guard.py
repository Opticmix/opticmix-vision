"""Device.set_prop 호환 통로 — 옛 OpenCV 이름으로 부르지만 쓰기는 controls 가드를 거친다."""
from __future__ import annotations

import time

import pytest

from opticmix_vision import controls as C
from opticmix_vision.device import Device, DeviceConfig, DeviceError, prop_write_counts


class FakeCap:
    def __init__(self):
        import cv2
        self.cv2 = cv2
        self.vals: dict[int, float] = {}
        self.sets: list[tuple[int, float]] = []

    def isOpened(self):  # noqa: N802
        return True

    def get(self, pid):
        return self.vals.get(pid, 0.0)

    def set(self, pid, v):  # noqa: A003
        self.sets.append((pid, v))
        self.vals[pid] = v
        return True

    def read(self):
        raise AssertionError("no frames in this test")

    def release(self):
        pass


def _dev(**cfg):
    cap = FakeCap()
    d = Device(DeviceConfig(layout="sbs", lr_order="first_is_right", backend="dshow", **cfg), capture=cap).open()
    return d, cap


def test_injected_captures_use_the_opencv_backend():
    d, _ = _dev()
    assert d.controls.backend_name == "opencv-dshow"
    d.close()


def test_same_value_is_not_written_at_all():
    d, cap = _dev()
    assert d.set_prop("CAP_PROP_EXPOSURE", -6.0) is True
    n = len(cap.sets)
    assert d.set_prop("CAP_PROP_EXPOSURE", -6.0) is True
    assert len(cap.sets) == n, "같은 값인데 드라이버를 또 건드렸다"
    assert prop_write_counts()["exposure"] == 1
    d.close()


def test_exposure_by_opencv_name_still_switches_to_manual():
    d, _ = _dev()
    d.set_prop("CAP_PROP_EXPOSURE", -6.0)
    assert d.controls.get("auto_exposure") == "manual"
    assert d.get_prop("CAP_PROP_AUTO_EXPOSURE") == 0.25        # 이제 읽힌다 (DShow 관례)
    d.close()


def test_consecutive_exposure_writes_are_spaced(monkeypatch):
    monkeypatch.setattr(C, "GROUP_MIN_INTERVAL_S", {"exposure": 0.3})
    d, _ = _dev()
    d.set_prop("CAP_PROP_EXPOSURE", -6.0)
    t0 = time.monotonic()
    d.set_prop("CAP_PROP_EXPOSURE", -5.0)                      # 기다리는 쓰기
    assert time.monotonic() - t0 >= 0.25
    d.close()


def test_not_waiting_returns_at_once(monkeypatch):
    monkeypatch.setattr(C, "GROUP_MIN_INTERVAL_S", {"exposure": 5.0})
    d, _ = _dev()
    d.set_prop("CAP_PROP_EXPOSURE", -6.0)
    t0 = time.monotonic()
    assert d.set_prop("CAP_PROP_EXPOSURE", -5.0, wait=False) is True
    assert time.monotonic() - t0 < 1.0
    d.close()                                                  # 대기 중이던 쓰기는 취소된다


def test_integer_ids_share_the_guard_with_names():
    d, cap = _dev()
    d.set_prop(cap.cv2.CAP_PROP_GAIN, 7)
    d.set_prop("CAP_PROP_GAIN", 7)
    assert prop_write_counts()["gain"] == 1
    d.close()


def test_non_controls_are_refused():
    d, _ = _dev()
    with pytest.raises(DeviceError, match="DeviceConfig"):
        d.set_prop("CAP_PROP_FPS", 30)
    d.close()


def test_extra_props_go_through_the_guard():
    d, cap = _dev(extra_props={"CAP_PROP_EXPOSURE": -6, "CAP_PROP_GAIN": 28})
    assert prop_write_counts()["_total"] == 2
    assert (cap.cv2.CAP_PROP_EXPOSURE, -6.0) in cap.sets and (cap.cv2.CAP_PROP_GAIN, 28.0) in cap.sets
    d.close()


def test_closing_the_device_closes_its_controls():
    d, _ = _dev()
    c = d.controls
    d.close()
    assert c.closed
