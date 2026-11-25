"""_ctrl_opencv — OpenCV 가 되는 제어만. 옛 SDK 와 같은 쓰기 호출을 낸다."""
from __future__ import annotations

import errno
import os
import struct
import sys

import pytest

from opticmix_vision import _ctrl_opencv, _native
from opticmix_vision._ctrl_opencv import OpenCVBackend
from opticmix_vision.controls import Controls, RawRange


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


# ---- Linux: ranges and defaults by VIDIOC_QUERYCTRL (read only) -----------------------------------------

def _queryctrl(table):
    """A made-up driver: {cid: (min, max, step, default, flags)}; any other control id is EINVAL."""
    seen = []

    def ioctl(fd, request, buf):
        assert request == _native.core().V4L2_VIDIOC_QUERYCTRL
        cid = struct.unpack_from("=I", buf)[0]
        seen.append(cid)
        if cid not in table:
            raise OSError(errno.EINVAL, "Invalid argument")
        _ctrl_opencv.QUERYCTRL.pack_into(buf, 0, cid, 1, b"made up", *table[cid], 0, 0)
    return ioctl, seen


def test_the_queryctrl_layout_matches_the_ioctl_number_and_every_key_has_an_id():
    core = _native.core()
    assert _ctrl_opencv.QUERYCTRL.size == (core.V4L2_VIDIOC_QUERYCTRL >> 16) & 0x3FFF == 68
    assert set(core.V4L2_CID) == set(core.KEYS)


def test_v4l2_ranges_reads_what_the_driver_reports_and_leaves_out_the_rest(tmp_path):
    cid = _native.core().V4L2_CID
    node = tmp_path / "video0"
    node.write_bytes(b"")
    ioctl, seen = _queryctrl({cid["gain"]: (0, 100, 1, 32, 0), cid["exposure"]: (1, 5000, 1, 156, 0),
                              cid["hue"]: (-40, 40, 1, 0, _ctrl_opencv.CTRL_FLAG_DISABLED)})
    got = _ctrl_opencv.v4l2_ranges(str(node), ["gain", "exposure", "hue", "gamma"], _ioctl=ioctl)
    assert got == {"gain": RawRange(0, 100, 1, 32, False), "exposure": RawRange(1, 5000, 1, 156, True)}
    assert seen == [cid["gain"], cid["exposure"], cid["hue"], cid["gamma"]]


def test_v4l2_ranges_passes_other_driver_errors_up(tmp_path):
    node = tmp_path / "video0"
    node.write_bytes(b"")

    def ioctl(fd, request, buf):
        raise OSError(errno.ENOTTY, "Inappropriate ioctl for device")
    with pytest.raises(OSError):
        _ctrl_opencv.v4l2_ranges(str(node), ["gain"], _ioctl=ioctl)


def test_with_a_node_describe_has_the_driver_range_and_default(monkeypatch):
    ranges = {"gain": RawRange(0, 100, 1, 32, False), "exposure": RawRange(1, 5000, 1, 156, True)}
    monkeypatch.setattr(_ctrl_opencv, "v4l2_ranges", lambda node, keys: ranges)
    cap = Cap()
    cap.vals[cap.cv2.CAP_PROP_GAIN] = 40.0
    be = OpenCVBackend(cap, "v4l2", node="/dev/video0")
    assert be.query("gain") == ranges["gain"] and be.query("hue") is None
    assert "ranges and defaults from the V4L2 driver" in be.note
    with Controls(be, guard=False) as c:
        g = c.describe("gain")
        assert (g.current, g.minimum, g.maximum, g.step, g.default) == (40, 0, 100, 1, 32)
        assert c.describe("exposure").units == "100us"
        h = c.describe("hue")                                       # not reported: nothing made up
        assert (h.minimum, h.maximum, h.default) == (None, None, None)
        with pytest.raises(ValueError):
            c.set("gain", 101)                                      # outside the range the driver reports
    assert cap.sets == []                                            # reading wrote nothing


def test_what_the_driver_does_not_report_works_as_in_0_0_6(monkeypatch, tmp_path):
    # A driver that reports 3 controls: names(), get, set and presets stay those of 0.0.6 (OpenCV); the query only adds.
    ranges = {"gain": RawRange(0, 100, 1, 32, False), "exposure": RawRange(1, 5000, 1, 156, True),
              "brightness": RawRange(-64, 64, 1, 0, False)}
    old_cap = Cap()
    old_cap.vals[old_cap.cv2.CAP_PROP_EXPOSURE] = 156.0              # inside the driver's range below
    with Controls(OpenCVBackend(old_cap, "v4l2"), guard=False) as old:
        want = old.names()
        preset = old.save_preset(tmp_path / "old.json")
        assert old.driver_names is None
    monkeypatch.setattr(_ctrl_opencv, "v4l2_ranges", lambda node, keys: ranges)
    cap = Cap()
    cap.vals[cap.cv2.CAP_PROP_HUE] = 3.0
    with Controls(OpenCVBackend(cap, "v4l2", node="/dev/video0"), guard=False) as c:
        assert c.names() == want and len(want) == 12
        assert c.driver_names == {"gain", "exposure", "auto_exposure", "brightness"}
        assert c.get("hue") == 3
        c.set("hue", 1)
        c.flush(timeout=1)
        assert cap.sets == [(cap.cv2.CAP_PROP_HUE, 1.0)]
        c.plan_preset(preset, force=True)          # force: both models unknown; not "Not controls of this camera"


def test_the_second_handle_is_opened_read_only(tmp_path, monkeypatch):
    node = tmp_path / "video0"
    node.write_bytes(b"")
    flags, real = [], os.open
    monkeypatch.setattr(os, "open", lambda path, f, *a: flags.append(f) or real(path, f, *a))
    _ctrl_opencv.v4l2_ranges(str(node), ["gain"], _ioctl=_queryctrl({})[0])
    assert flags == [os.O_RDONLY]


def test_without_a_node_nothing_changes():
    be = OpenCVBackend(Cap(), "v4l2")
    assert be.query("gain") is None and be.supports("hue")
    assert "no ranges or defaults" in be.note
    with Controls(be, guard=False) as c:
        assert c.driver_names is None and c.writes_defaults is False
    with Controls(OpenCVBackend(Cap(), "dshow"), guard=False) as c:
        assert c.driver_names is None and c.writes_defaults is True


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only: a real ioctl on a non-V4L2 node")
def test_a_node_that_is_not_v4l2_falls_back_to_no_ranges():
    # /dev/null takes the real request number (no OverflowError) and answers ENOTTY
    with pytest.raises(OSError) as e:
        _ctrl_opencv.v4l2_ranges("/dev/null", ["gain"])
    assert e.value.errno == errno.ENOTTY
    be = OpenCVBackend(Cap(), "v4l2", node="/dev/null")
    assert be.query("gain") is None and be.supports("gain")
    assert "V4L2 ranges not readable from /dev/null" in be.note


def test_on_linux_reset_to_defaults_still_writes_nothing(monkeypatch):
    # The driver's defaults are shown (describe) but not written: Linux control writes are not verified (as 0.0.6).
    ranges = {"gain": RawRange(0, 100, 1, 32, False), "exposure": RawRange(1, 5000, 1, 156, False)}
    monkeypatch.setattr(_ctrl_opencv, "v4l2_ranges", lambda node, keys: ranges)
    cap = Cap()
    cap.vals[cap.cv2.CAP_PROP_GAIN] = 40.0
    with Controls(OpenCVBackend(cap, "v4l2", node="/dev/video0"), guard=False) as c:
        assert c.describe("gain").default == 32 and c.writes_defaults is False
        assert c.plan_reset() == {} and c.plan_reset(include_mono=True) == {}
        assert c.reset_to_defaults() == [] and c.reset_to_defaults(include_mono=True) == []
        c.flush(timeout=1)
    assert cap.sets == []


def test_a_preset_value_outside_the_driver_range_is_refused_before_writing(monkeypatch, tmp_path):
    # Changed from 0.0.6 (CHANGELOG): 0.0.6 passed it to OpenCV.
    with Controls(OpenCVBackend(Cap(), "v4l2"), guard=False) as old:
        preset = old.save_preset(tmp_path / "old.json")              # exposure 0
    monkeypatch.setattr(_ctrl_opencv, "v4l2_ranges", lambda node, keys: {"exposure": RawRange(1, 5000, 1, 156, True)})
    cap = Cap()
    with Controls(OpenCVBackend(cap, "v4l2", node="/dev/video0"), guard=False) as c:
        with pytest.raises(ValueError, match=r"exposure must be a whole number within 1\.\.5000"):
            c.load_preset(preset, force=True)
    assert cap.sets == []
