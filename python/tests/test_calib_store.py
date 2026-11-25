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


# --- calib show ----------------------------------------------------------------------------------------------
import hashlib  # noqa: E402

RVEC = np.array([0.004, -0.017, 0.002])      # rad; R is built from it below without OpenCV


def _axis_angle(r):
    """Rodrigues' formula in numpy: R = I + sin(θ) K + (1 - cos(θ)) K², K the cross-product matrix of the axis."""
    th = np.linalg.norm(r)
    k = r / th
    K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + np.sin(th) * K + (1 - np.cos(th)) * K @ K


def _distinct_json(serial="S1-0042"):
    """Left and right differ in every value, so a swap or a mix-up shows."""
    left = calibration.CameraIntrinsics(K=[[370.5, 0, 401.2], [0, 370.1, 398.7], [0, 0, 1]],
                                        D=[-0.2577, 0.0437, -0.001, 0.0002], size=(800, 800))
    right = calibration.CameraIntrinsics(K=[[368.9, 0, 389.4], [0, 368.6, 402.3], [0, 0, 1]],
                                         D=[-0.2551, 0.0421, 0.0, 0.0], size=(800, 800))
    c = calibration.StereoCalibration(left=left, right=right,
                                      extrinsics=calibration.StereoExtrinsics(R=_axis_angle(RVEC), t=[-66.56, 0.31, -0.42]),
                                      device={"model": "Weevia S1", "serial": serial},
                                      meta={"rms_stereo_px": 0.25, "tool": "made up — test"})
    return c.to_json()


def _install_distinct(store, *serials):
    store.mkdir(parents=True, exist_ok=True)
    for s in serials:
        (store / f"{s}.json").write_text(_distinct_json(s), encoding="utf-8")


def test_show_installed_file_every_value(store):
    _install_distinct(store, "S1-0042")
    f = store / "S1-0042.json"
    d = calib.show()
    assert (d["source"], d["path"], d["matched_to_camera"]) == ("installed", str(f), False)
    assert d["note"] == "installed file, not matched to this camera (no USB serial)"
    assert d["sha256"] == hashlib.sha256(f.read_bytes()).hexdigest()
    assert (d["used_by_device_calibration"], d["use"]) == (True, "yes: the only installed file")
    assert d["device"] == {"model": "Weevia S1", "serial": "S1-0042"}
    assert (d["schema"], d["schema_version"], d["units"]) == ("opticmix_vision.stereo_calibration", 1, "mm")
    L, R_ = d["left"], d["right"]
    assert (L["fx"], L["fy"], L["cx"], L["cy"]) == (370.5, 370.1, 401.2, 398.7)
    assert (R_["fx"], R_["fy"], R_["cx"], R_["cy"]) == (368.9, 368.6, 389.4, 402.3)     # not swapped
    assert L["D"] == [-0.2577, 0.0437, -0.001, 0.0002] and R_["D"] == [-0.2551, 0.0421, 0.0, 0.0]
    assert L["K"] == [[370.5, 0, 401.2], [0, 370.1, 398.7], [0, 0, 1]]
    assert (L["model"], L["size"], L["mode"]) == ("KB4", [800, 800], [1600, 800])
    x = d["extrinsics"]
    assert x["convention"] == "p_right = R @ p_left + t"
    assert np.array_equal(x["R"], _axis_angle(RVEC)) and x["t"] == [-66.56, 0.31, -0.42]
    assert x["baseline_mm"] == pytest.approx(np.linalg.norm([-66.56, 0.31, -0.42]), abs=1e-12)
    assert np.allclose(x["rvec_rad"], RVEC, rtol=0, atol=1e-9)
    assert np.allclose(x["rvec_deg"], np.degrees(RVEC), rtol=0, atol=1e-7)
    assert x["rotation_deg"] == pytest.approx(np.degrees(np.linalg.norm(RVEC)), abs=1e-7)
    assert np.allclose(x["rotation_axis"], RVEC / np.linalg.norm(RVEC), rtol=0, atol=1e-7)
    assert d["meta"] == {"rms_stereo_px": 0.25, "tool": "made up — test"}


def test_show_cli_prints_every_value(store, capsys):
    _install_distinct(store, "S1-0042")
    assert calib.main(["show"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[:3] == ["calibration S1-0042 (Weevia S1)", f"file:     {store / 'S1-0042.json'}",
                         "          installed file, not matched to this camera (no USB serial)"]
    assert "used by Device.calibration: yes: the only installed file" in lines
    left = lines.index("left:     KB4, eye size 800x800 (the 1600x800 mode)")
    right = lines.index("right:    KB4, eye size 800x800 (the 1600x800 mode)")
    assert lines[left + 1:left + 3] == ["  fx 370.5000  fy 370.1000  cx 401.2000  cy 398.7000",
                                        "  D (k1..k4)  -0.2577  0.0437  -0.001  0.0002"]
    assert lines[right + 1:right + 3] == ["  fx 368.9000  fy 368.6000  cx 389.4000  cy 402.3000",
                                          "  D (k1..k4)  -0.2551  0.0421  0  0"]
    R = _axis_angle(RVEC)
    i = lines.index("left to right (p_right = R @ p_left + t, mm):")
    assert lines[i + 1:i + 8] == [
        f"  baseline  {np.linalg.norm([-66.56, 0.31, -0.42]):.4f} mm",
        "  t         -66.5600  0.3100  -0.4200 mm",
        "  R         " + "  ".join(f"{v:+.6f}" for v in R[0]),
        "            " + "  ".join(f"{v:+.6f}" for v in R[1]),
        "            " + "  ".join(f"{v:+.6f}" for v in R[2]),
        "  rvec      +0.004000  -0.017000  +0.002000 rad = +0.2292  -0.9740  +0.1146 deg",
        "  rotation  1.0072 deg about the axis +0.2276  -0.9671  +0.1138"]
    assert lines[-1] == 'meta:     {"rms_stereo_px": 0.25, "tool": "made up — test"}'


def test_show_json_is_the_dict(store, capsys):
    _install_distinct(store, "S1-0042")
    assert calib.main(["show", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == calib.show()


def test_show_one_of_several_by_serial_or_label(store):
    _install_distinct(store, "S1-0042", "S1-0057")
    d = calib.show("S1-0057")
    assert d["device"]["serial"] == "S1-0057" and d["path"] == str(store / "S1-0057.json")
    assert (d["source"], d["used_by_device_calibration"]) == ("installed", False)
    assert d["use"] == "no: 2 files are installed, and it needs exactly one"
    assert calib.show(" s1-0057-k7q2 ") == d                        # the label on the camera works too


def test_show_a_file_that_is_not_installed(store, tmp_path):
    src = tmp_path / "S1-0042.json"
    src.write_text(_distinct_json(), encoding="utf-8")
    d = calib.show(str(src))
    assert (d["source"], d["path"], d["used_by_device_calibration"]) == ("file", str(src), False)
    assert d["note"] == "file not installed, not matched to this camera (no USB serial)"
    assert d["use"] == f"no: not installed (python -m opticmix_vision.calib add {src} installs it)"
    assert d["sha256"] == hashlib.sha256(src.read_bytes()).hexdigest()
    calib.add(str(src))
    d = calib.show(str(store / "S1-0042.json"))                      # the installed file, named by its path
    assert (d["source"], d["used_by_device_calibration"]) == ("installed", True)


def test_show_says_what_to_do(store, capsys):
    with pytest.raises(calib.CalibLookupError, match=r"^No calibration installed\. Install the file for your camera: "
                       r"python -m opticmix_vision\.calib add <file\.json> — or show a file without installing it: "
                       r"python -m opticmix_vision\.calib show <file\.json>$"):
        calib.show()
    _install_distinct(store, "S1-0042", "S1-0057")
    with pytest.raises(calib.CalibLookupError, match=r"^2 calibrations installed \(S1-0042, S1-0057\)\. Name the one to "
                       r"show: python -m opticmix_vision\.calib show S1-0042\. .*calib check --all"):
        calib.show()
    with pytest.raises(calib.CalibLookupError, match=r"^S1-0099 is not installed \(installed: S1-0042, S1-0057\)\. "
                       r"Install its file: python -m opticmix_vision\.calib add <file\.json>"):
        calib.show("s1-0099")
    with pytest.raises(calib.CalibLookupError, match=r"^No such file: nope\.json\. "):
        calib.show("nope.json")
    assert calib.main(["show", "S1-0099"]) == 1
    assert "S1-0099 is not installed" in capsys.readouterr().err
    (store / "S1-0042.json").write_text("{", encoding="utf-8")
    assert calib.main(["show", "S1-0042"]) == 1                     # a broken file: the reason, exit code 1
    assert capsys.readouterr().err.strip()
    with pytest.raises(SystemExit) as e:
        calib.main(["show", "--synthetic", "S1-0057"])
    assert e.value.code == 2


def test_show_json_failure_is_json_on_stdout(store, capsys):
    assert calib.main(["show", "--json"]) == 1
    cap = capsys.readouterr()
    got = json.loads(cap.out)
    assert list(got) == ["error"] and got["error"].startswith("No calibration installed. Install the file")
    assert got["error"] in cap.err                                   # the message is on stderr as for the text form
    _install_distinct(store, "S1-0042")
    (store / "S1-0042.json").write_text("{", encoding="utf-8")
    assert calib.main(["show", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["error"]               # an invalid file: its reason, as JSON


def test_show_prints_utf8_on_a_cp949_console(store, monkeypatch, tmp_path):
    """A console whose code page cannot encode a character of the file (the em dash here; cp949 has Hangul) must
    not stop the command: it writes UTF-8, as the doctor does."""
    import io
    import sys
    text = "보정 — test"
    d = json.loads(_distinct_json())
    d["meta"]["tool"] = text
    src = tmp_path / "S1-0042.json"
    src.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    out, err = io.BytesIO(), io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(out, encoding="cp949"))
    monkeypatch.setattr(sys, "stderr", io.TextIOWrapper(err, encoding="cp949"))
    rc = calib.main(["show", str(src)])
    sys.stdout.flush()
    sys.stderr.flush()
    assert rc == 0, err.getvalue()
    assert f'"tool": "{text}"' in out.getvalue().decode("utf-8")


def test_show_synthetic_reads_no_file(store, capsys):
    assert calib.main(["show", "--synthetic", "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert (d["source"], d["path"], d["sha256"], d["used_by_device_calibration"]) == ("generated", None, None, False)
    assert d["extrinsics"]["baseline_mm"] == 64.0 and d["extrinsics"]["rotation_deg"] == 0.0
    assert d["extrinsics"]["rotation_axis"] is None
    assert not store.exists()
