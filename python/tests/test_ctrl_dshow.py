"""_ctrl_dshow — 상수·장치 고르기는 어디서나, COM 열거는 Windows 에서만(카메라에 바인드하지 않는다)."""
from __future__ import annotations

import os
import sys

import pytest

from opticmix_vision import _ctrl_dshow as D
from opticmix_vision.controls import CONTROLS, ControlsUnavailable


def test_guid_parse_round_trips():
    g = D.GUID.parse("C6E13360-30AC-11D0-A18C-00A0C9118956")
    assert str(g).upper() == "C6E13360-30AC-11D0-A18C-00A0C9118956"


def test_every_named_control_has_a_ks_id():
    assert {c.key for c in CONTROLS} <= set(D.KEYS)
    assert D.KEYS["power_line_frequency"] == (D.PROCAMP, 13)
    assert D.KEYS["auto_exposure_priority"] == (D.CAMERA, 19)
    assert D.KEYS["exposure"] == (D.CAMERA, 4)


def test_choose_refuses_a_different_camera():
    D.choose(["Example Front Webcam", "WN Dual Camera"], 1)
    with pytest.raises(ControlsUnavailable, match="not a Weevia S1"):
        D.choose(["Example Front Webcam", "WN Dual Camera"], 0)
    with pytest.raises(ControlsUnavailable, match="no DirectShow video device"):
        D.choose(["WN Dual Camera"], 3)
    with pytest.raises(ControlsUnavailable):
        D.choose([None], 0)


@pytest.mark.skipif(sys.platform != "win32", reason="DirectShow is Windows-only")
@pytest.mark.skipif(not os.environ.get("OPTICMIX_HW"), reason="실제 COM 열거(장치 이름 읽기): OPTICMIX_HW=1 일 때만")
def test_enumeration_runs_through_com_without_binding_anything():
    names = D.enumerate_video_devices()
    assert isinstance(names, list)
    assert all(n is None or isinstance(n, str) for n in names)


@pytest.mark.skipif(sys.platform == "win32", reason="checks the refusal off Windows")
def test_the_backend_refuses_off_windows():
    with pytest.raises(ControlsUnavailable):
        D.DShowBackend(0)
