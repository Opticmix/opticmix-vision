"""_cvprops — 옛 OpenCV 이름을 제어 이름으로. set_prop/get_prop 호환."""
from __future__ import annotations

from opticmix_vision._cvprops import ae_is_auto, ae_number, from_controls, prop_name, to_controls


def test_prop_names_resolve_from_ids():
    import cv2
    assert prop_name(cv2.CAP_PROP_GAIN) == "CAP_PROP_GAIN"
    assert prop_name("CAP_PROP_BACKLIGHT") == "CAP_PROP_BACKLIGHT"


def test_exposure_keeps_the_old_implicit_manual_switch():
    assert to_controls("CAP_PROP_EXPOSURE", -5.0, "dshow") == {"auto_exposure": "manual", "exposure": -5.0}
    assert to_controls("CAP_PROP_WB_TEMPERATURE", 4600.0, "dshow") == {"auto_white_balance": False,
                                                                     "white_balance": 4600.0}


def test_auto_exposure_numbers_follow_each_backend():
    assert to_controls("CAP_PROP_AUTO_EXPOSURE", 0.75, "dshow") == {"auto_exposure": "auto"}
    assert to_controls("CAP_PROP_AUTO_EXPOSURE", 0.25, "dshow") == {"auto_exposure": "manual"}
    assert to_controls("CAP_PROP_AUTO_EXPOSURE", 3, "v4l2") == {"auto_exposure": "auto"}
    assert to_controls("CAP_PROP_AUTO_EXPOSURE", 1, "v4l2") == {"auto_exposure": "manual"}
    assert (ae_number(True, "dshow"), ae_number(False, "dshow"), ae_number(None, "dshow")) == (0.75, 0.25, -1.0)
    assert ae_is_auto(3, "v4l2") and not ae_is_auto(1, "v4l2")


def test_non_controls_are_not_mapped():
    assert to_controls("CAP_PROP_FPS", 30.0, "dshow") is None


def test_readback_through_controls():
    class Fake:
        def get(self, name):
            return {"auto_exposure": "auto", "gain": 7, "auto_white_balance": None}[name]
    assert from_controls("CAP_PROP_AUTO_EXPOSURE", Fake(), "dshow") == 0.75
    assert from_controls("CAP_PROP_GAIN", Fake(), "dshow") == 7.0
    assert from_controls("CAP_PROP_AUTO_WB", Fake(), "dshow") == -1.0
    assert from_controls("CAP_PROP_FPS", Fake(), "dshow") is None
