import json

import numpy as np
import pytest

pytest.importorskip("opticmix_vision._core")
from opticmix_vision import calib, calibration  # noqa: E402


def _calib_json(serial="S1-0042"):
    intr = calibration.synthetic_equidistant((800, 800), 180.0)
    ext = calibration.StereoExtrinsics(R=np.eye(3), t=np.array([-64.0, 0.0, 0.0]))
    c = calibration.StereoCalibration(left=intr, right=intr, extrinsics=ext,
                                      device={"model": "Weevia S1", "serial": serial})
    return c.to_json()


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(calib, "store_dir", lambda: tmp_path / "store")
    return tmp_path / "store"


def _install(store, *serials):
    store.mkdir(parents=True, exist_ok=True)
    for s in serials:
        (store / f"{s}.json").write_text(_calib_json(s))


def test_parse_label_ok():
    assert calib.parse_label(" s1-0042-k7q2 ") == ("S1-0042", "S1-0042-K7Q2")


@pytest.mark.parametrize("bad", ["S1-42-K7Q2", "S1-0042", "S2-0042-K7Q2", "S1-0042-K7Q", ""])
def test_parse_label_bad(bad):
    with pytest.raises(calib.CalibLookupError, match="Check the 12 characters"):
        calib.parse_label(bad)


def test_store_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(calib.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert calib.store_dir() == tmp_path / "opticmix" / "calib"
    monkeypatch.setattr(calib.sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "x"))
    assert calib.store_dir() == tmp_path / "x" / "opticmix" / "calib"
    monkeypatch.delenv("XDG_CONFIG_HOME")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert calib.store_dir() == tmp_path / ".config" / "opticmix" / "calib"


def test_add_file(store, tmp_path):
    src = tmp_path / "S1-0042.json"
    src.write_text(_calib_json())
    assert calib.add(str(src)) == ("S1-0042", "added")
    assert calib.add(str(src)) == ("S1-0042", "unchanged")
    src.write_text(_calib_json().replace('"Weevia S1"', '"Weevia S1 "'))
    assert calib.add(str(src)) == ("S1-0042", "replaced")


def test_add_label_not_available(store):
    with pytest.raises(calib.CalibLookupError, match="Installing by label is not available yet"):
        calib.add("s1-0042-k7q2")
    with pytest.raises(calib.CalibLookupError, match="No such file"):
        calib.add("D:/nope.json")


def test_add_no_serial(store, tmp_path):
    src = tmp_path / "a.json"
    src.write_text(_calib_json("no-serial"))
    with pytest.raises(calib.CalibLookupError, match="no S1-NNNN serial"):
        calib.add(str(src))


def test_add_broken_file(store, tmp_path):
    src = tmp_path / "x.json"
    src.write_text("{")
    with pytest.raises(ValueError):
        calib.add(str(src))
    assert not store.exists()


def test_resolve_none(store):
    with pytest.raises(calib.CalibLookupError, match="No calibration installed"):
        calib.resolve()


def test_resolve_single(store):
    _install(store, "S1-0042")
    assert calib.resolve().device["serial"] == "S1-0042"


def test_resolve_many_needs_choice(store):
    _install(store, "S1-0042", "S1-0057")
    with pytest.raises(calib.CalibLookupError, match=r"2 calibrations installed \(S1-0042, S1-0057\)"):
        calib.resolve()


def test_resolve_lists_installed(store):
    _install(store, "S1-0057", "S1-0042")
    assert calib.installed() == ["S1-0042", "S1-0057"]
    with pytest.raises(calib.CalibLookupError, match="Keep only the file for the camera you use"):
        calib.resolve()


def test_cli(store, tmp_path, capsys):
    src = tmp_path / "a.json"
    src.write_text(_calib_json())
    assert calib.main(["add", str(src)]) == 0
    assert "S1-0042 calibration added:" in capsys.readouterr().out
    assert calib.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "  S1-0042" in out and str(store) in out
    assert calib.main(["add", "S1-42"]) == 1
    assert "No such file: S1-42" in capsys.readouterr().err


# --- device.py: Device.calibration ------------------------------------------------------------------
from opticmix_vision.device import Device, DeviceConfig  # noqa: E402


def test_device_calibration_single(store):
    _install(store, "S1-0042")
    dev = Device(DeviceConfig.weevia_s1(device=0), capture=object())
    assert dev.calibration.device["serial"] == "S1-0042"
    assert dev.calibration is dev.calibration


def test_device_calibration_many_installed(store):
    _install(store, "S1-0042", "S1-0057")
    with pytest.raises(calib.CalibLookupError, match="2 calibrations installed"):
        Device(DeviceConfig.weevia_s1(device=0), capture=object()).calibration


def test_device_calibration_size_mismatch(store):
    _install(store, "S1-0042")
    dev = Device(DeviceConfig.weevia_s1(device=0, width=1280, height=640), capture=object())
    with pytest.raises(calib.CalibLookupError, match="for 800x800 eyes. Open the 1600x800 mode"):
        dev.calibration


def test_round_trip_keeps_every_number(store, tmp_path):
    """add -> Device.calibration returns the file's numbers unchanged, with left and right not swapped
    (left and right differ here, so a swap would show)."""
    left = calibration.CameraIntrinsics(K=np.array([[370.5, 0, 401.2], [0, 370.1, 398.7], [0, 0, 1]]),
                                        D=np.array([-0.2577, 0.0437, 0.0, 0.0]), size=(800, 800))
    right = calibration.CameraIntrinsics(K=np.array([[368.9, 0, 389.4], [0, 368.6, 402.3], [0, 0, 1]]),
                                         D=np.array([-0.2551, 0.0421, 0.0, 0.0]), size=(800, 800))
    a = 0.02
    R = np.array([[np.cos(a), 0, np.sin(a)], [0, 1, 0], [-np.sin(a), 0, np.cos(a)]])
    src_calib = calibration.StereoCalibration(left=left, right=right,
                                              extrinsics=calibration.StereoExtrinsics(R=R, t=np.array([-66.56, 0.31, -0.42])),
                                              device={"model": "Weevia S1", "serial": "S1-0042"})
    src = tmp_path / "S1-0042.json"
    src.write_text(src_calib.to_json())
    calib.add(str(src))
    got = Device(DeviceConfig.weevia_s1(device=0), capture=object()).calibration
    for eye_got, eye_src in ((got.left, left), (got.right, right)):
        assert np.array_equal(eye_got.K, eye_src.K) and np.array_equal(eye_got.D, eye_src.D)
        assert eye_got.size == eye_src.size
    assert np.array_equal(got.extrinsics.R, R) and np.array_equal(got.extrinsics.t, src_calib.extrinsics.t)
