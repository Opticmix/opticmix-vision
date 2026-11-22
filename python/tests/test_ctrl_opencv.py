"""_ctrl_opencv — OpenCV 가 되는 제어만. 옛 SDK 와 같은 쓰기 호출을 낸다."""
from __future__ import annotations

import pytest

from opticmix_vision._ctrl_opencv import OpenCVBackend


class Cap:
    def __init__(self):
        import cv2
        self.cv2 = cv2
        self.vals: dict[int, float] = {}
        self.sets: list[tuple[int, float]] = []

    def get(self, pid):
        return self.vals.get(pid, 0.0)

    def set(self, pid, v):
        self.sets.append((pid, v))
        self.vals[pid] = v
        return True


def test_supports_what_opencv_maps_and_nothing_else():
    be = OpenCVBackend(Cap())
    assert be.supports("gain") and be.supports("exposure") and be.supports("white_balance")
    assert not be.supports("power_line_frequency") and not be.supports("auto_exposure_priority")
    assert be.query("gain") is None


def test_dshow_manual_value_is_one_write_like_the_old_sdk():
    cap = Cap()
    be = OpenCVBackend(cap, "dshow")
    be.write("exposure", -5, False)
    assert cap.sets == [(cap.cv2.CAP_PROP_EXPOSURE, -5.0)]
    assert be.read("exposure") == (-5, False)


def test_dshow_auto_on_writes_the_auto_number():
    cap = Cap()
    OpenCVBackend(cap, "dshow").write("exposure", None, True)
    assert cap.sets == [(cap.cv2.CAP_PROP_AUTO_EXPOSURE, 0.75)]


def test_dshow_manual_without_a_value_writes_the_manual_number():
    cap = Cap()
    OpenCVBackend(cap, "dshow").write("exposure", None, False)
    assert cap.sets == [(cap.cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)]


def test_v4l2_writes_the_mode_before_the_value():
    cap = Cap()
    be = OpenCVBackend(cap, "v4l2")
    be.write("exposure", 100, False)
    assert cap.sets == [(cap.cv2.CAP_PROP_AUTO_EXPOSURE, 1.0), (cap.cv2.CAP_PROP_EXPOSURE, 100.0)]
    assert be.exposure_units == "100us" and be.read("exposure") == (100, False)


def test_dshow_auto_exposure_is_unknown_until_written():
    be = OpenCVBackend(Cap(), "dshow")
    assert be.read("exposure")[1] is None
    assert "not readable" in be.note


def test_a_rejected_set_raises():
    cap = Cap()
    cap.set = lambda pid, v: False
    with pytest.raises(OSError, match="rejected"):
        OpenCVBackend(cap).write("gain", 5, None)
