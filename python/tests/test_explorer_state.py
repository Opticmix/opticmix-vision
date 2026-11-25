"""화면 없이 도는 상태 객체. Tk 를 import 하지 않는다."""
from __future__ import annotations

import pytest

from opticmix_vision.devices import DeviceInfo, Mode
from opticmix_vision.explorer.state import KNOWN_MODE, ExplorerState

MODES = [
    Mode(fourcc="MJPG", width=1600, height=800, max_fps=90.0),
    Mode(fourcc="YUY2", width=1600, height=800, max_fps=45.0),
]


def test_starts_with_nothing_selected():
    s = ExplorerState()
    assert s.device is None and s.mode is None and s.can_stream is False


def test_selecting_a_device_and_mode_makes_it_streamable():
    s = ExplorerState()
    s.select_device(DeviceInfo(index=0, name="WN Dual Camera"))
    s.set_modes(MODES)
    s.select_mode(MODES[0])
    assert s.can_stream is True


def test_choosing_a_new_device_clears_the_old_mode():
    s = ExplorerState()
    s.select_device(DeviceInfo(index=0, name="WN Dual Camera"))
    s.set_modes(MODES); s.select_mode(MODES[0])
    s.select_device(DeviceInfo(index=1, name="Example Rear Webcam"))
    assert s.mode is None and s.can_stream is False


def test_falls_back_to_the_known_mode_when_enumeration_found_none():
    s = ExplorerState()
    s.select_device(DeviceInfo(index=0, name="WN Dual Camera"))
    s.set_modes([])
    assert s.modes == [KNOWN_MODE]
    assert "Could not read the mode list" in s.mode_note


def test_illuminator_below_onset_is_reported_as_off():
    assert ExplorerState.illuminator_label(0) == "off"
    assert ExplorerState.illuminator_label(16) == "off"
    assert ExplorerState.illuminator_label(24) == "24"


from opticmix_vision.controls import ControlInfo


def _info(name, kind="int", current=0, minimum=0, maximum=10, choices=None, effect="verified"):
    return ControlInfo(name=name, kind=kind, current=current, minimum=minimum, maximum=maximum, step=1,
                       default=None, choices=choices, units="raw", effect=effect, ledger=None, group=name)


def test_rows_follow_the_table_with_the_exposure_mode_right_above_exposure():
    from opticmix_vision.explorer.state import build_rows
    infos = {
        "gain": _info("gain"),
        "exposure": _info("exposure", minimum=-11, maximum=-3),
        "auto_exposure": _info("auto_exposure", kind="menu", current="manual", minimum=None, maximum=None,
                               choices=("manual", "auto")),
        "hue": _info("hue", effect="none_expected_mono"),
    }
    main, mono = build_rows(infos)
    assert [r.name for r in main] == ["gain", "auto_exposure", "exposure"]
    assert [r.name for r in mono] == ["hue"]
    assert {r.name: r.widget for r in main} == {"gain": "slider", "auto_exposure": "menu", "exposure": "slider"}


def test_a_control_without_a_reported_range_gets_no_slider():
    from opticmix_vision.explorer.state import build_rows
    main, _ = build_rows({"sharpness": _info("sharpness", minimum=None, maximum=None)})
    assert main[0].widget == "readonly"


def test_exposure_is_locked_while_auto_exposure_is_on():
    from opticmix_vision.explorer.state import locked
    assert locked({"auto_exposure": "auto"}) == {"exposure"}
    assert locked({"auto_exposure": "manual", "auto_white_balance": True}) == {"white_balance"}
    assert locked({"auto_exposure": None}) == set()


def test_banner_names_the_limitation():
    from opticmix_vision.explorer.state import banner_text
    assert banner_text("dshow", "") == ""
    assert "made up" in banner_text("sim", "")
    assert banner_text("opencv-dshow", "no ranges").startswith("Limited controls (OpenCV fallback)")


def test_the_banner_on_linux_says_the_controls_are_shown_not_written():
    from opticmix_vision.controls import DEFAULTS_NOT_WRITTEN
    from opticmix_vision.explorer.state import READ_ONLY_NOTE, banner_text
    assert READ_ONLY_NOTE == "Camera controls on Linux are not verified and not supported — shown, not written"
    assert banner_text("opencv-v4l2", "no ranges", read_only=True).splitlines() == [
        "Limited controls (OpenCV fallback): no ranges", READ_ONLY_NOTE, DEFAULTS_NOT_WRITTEN]
    assert banner_text("dshow", "", read_only=True).splitlines() == [READ_ONLY_NOTE, DEFAULTS_NOT_WRITTEN]


def test_reset_lines_count_the_writes():
    from opticmix_vision.explorer.state import reset_lines
    assert reset_lines({}) == ["Everything is already at the device defaults."]
    lines = reset_lines({"gain": (28, 0), "backlight_compensation": (96, 50)})
    assert lines[:2] == ["gain  28 → 0", "backlight_compensation  96 → 50"]
    assert lines[-1] == "This will write to the camera 2 times."


def test_a_mode_and_its_value_count_as_one_write():
    """The Load preset dialog: auto_exposure + exposure share one device property, so set_many writes them once."""
    pytest.importorskip("opticmix_vision._core")      # the control table comes from the extension
    from opticmix_vision.explorer.state import reset_lines
    lines = reset_lines({"auto_exposure": ("auto", "manual"), "exposure": (-5, -7), "gain": (28, 40)})
    assert lines[-1] == "This will write to the camera 2 times."
    assert reset_lines({}, nothing="Everything is already at the preset's values.") == [
        "Everything is already at the preset's values."]
