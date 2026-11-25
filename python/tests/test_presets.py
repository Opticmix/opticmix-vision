"""Presets on the simulated backend: save -> load round trip, one set_many, whole-file checks before any write."""
from __future__ import annotations

import json

import pytest

pytest.importorskip("opticmix_vision._core")
from opticmix_vision import controls as controls_mod  # noqa: E402
from opticmix_vision import presets  # noqa: E402
from opticmix_vision._ctrl_sim import SimBackend  # noqa: E402
from opticmix_vision.controls import Controls  # noqa: E402


@pytest.fixture
def store(monkeypatch, tmp_path):
    """Never the user's %APPDATA%\\opticmix: the preset folder goes to a temporary folder."""
    monkeypatch.setattr(presets, "store_dir", lambda: tmp_path / "presets")
    return tmp_path / "presets"


@pytest.fixture
def cam():
    be = SimBackend()
    c = Controls(be)
    yield c, be
    c.close()


def _edit(path, fn):
    doc = json.loads(path.read_text(encoding="utf-8"))
    fn(doc)
    path.write_text(json.dumps(doc), encoding="utf-8")


def test_save_writes_model_version_time_and_every_readable_control_with_units(cam, tmp_path):
    c, be = cam
    p = c.save_preset(tmp_path / "a.json", name="bench", note="lab")
    doc = json.loads(p.read_text(encoding="utf-8"))
    assert doc["format"] == "opticmix-controls-preset" and doc["version"] == 1
    assert doc["name"] == "bench" and doc["note"] == "lab"
    assert doc["device"] == {"model": "synthetic", "product": None, "backend": "sim"}
    from opticmix_vision import __version__
    assert doc["sdk_version"] == __version__
    assert doc["saved_at"].endswith("+00:00")
    assert list(doc["controls"]) == list(c.snapshot())           # every readable control, table order
    assert doc["controls"]["exposure"] == {"value": -5, "units": "log2_s_nominal"}
    assert doc["controls"]["auto_exposure"]["value"] == "manual"   # the mode is saved too
    assert be.writes == [], "saving reads only"


def test_round_trip_restores_values_in_one_set_many_and_reports_kept(cam, tmp_path, monkeypatch):
    c, be = cam
    p = c.save_preset(tmp_path / "a.json")
    c.set_many({"gain": 100, "backlight_compensation": 10, "auto_exposure": "auto"})
    c.flush(timeout=5)
    calls = []
    real = c.set_many
    monkeypatch.setattr(c, "set_many", lambda v: calls.append(dict(v)) or real(v))
    n0, w0 = len(be.writes), controls_mod.write_counts()["_total"]

    r = c.load_preset(p)

    assert calls == [{"gain": 32, "backlight_compensation": 32, "auto_exposure": "manual"}]
    assert {x.name: (x.status, x.kept) for x in r.results} == {
        "gain": ("written", True), "backlight_compensation": ("written", True), "auto_exposure": ("written", True)}
    assert r.ok
    assert "exposure" in r.unchanged and "brightness" in r.unchanged and r.left_out == ()
    assert len(be.writes) - n0 == 3 == controls_mod.write_counts()["_total"] - w0   # three groups, three writes
    assert c.snapshot() == {n: e["value"] for n, e in json.loads(p.read_text())["controls"].items()}


def test_loading_the_values_the_camera_already_has_writes_nothing(cam, tmp_path):
    c, be = cam
    p = c.save_preset(tmp_path / "a.json")
    r = c.load_preset(p)
    assert r.results == () and r.ok and len(r.unchanged) == len(c.snapshot())
    assert be.writes == []


def test_plan_lists_what_would_change_and_writes_nothing(cam, tmp_path):
    c, be = cam
    p = c.save_preset(tmp_path / "a.json")
    c.set("gain", 100)
    c.flush(timeout=5)
    n0 = len(be.writes)
    assert c.plan_preset(p) == {"gain": (100, 32)}
    assert len(be.writes) == n0


def test_a_preset_from_another_model_is_refused_unless_forced(cam, tmp_path):
    c, be = cam
    p = c.save_preset(tmp_path / "a.json")
    _edit(p, lambda d: d["device"].update(model="WN Dual Camera"))
    c.set("gain", 100)
    c.flush(timeout=5)
    n0 = len(be.writes)
    with pytest.raises(presets.PresetModelError, match="saved from WN Dual Camera; this is synthetic"):
        c.load_preset(p)
    assert len(be.writes) == n0
    assert c.load_preset(p, force=True).ok
    assert c.get("gain") == 32


def test_a_camera_whose_model_is_unknown_needs_force(tmp_path):
    be = SimBackend()
    be.model = None                     # like the OpenCV fallback, which cannot tell what camera it is
    with Controls(be) as c:
        p = c.save_preset(tmp_path / "a.json")
        with pytest.raises(presets.PresetModelError, match="cannot tell"):
            c.load_preset(p)
        assert c.load_preset(p, force=True).ok


@pytest.mark.parametrize("edit, match", [
    (lambda d: d["controls"]["gain"].update(value=999), r"gain must be a whole number within 0\.\.255"),
    (lambda d: d["controls"]["gain"].update(value=True), "gain must be a whole number"),
    (lambda d: d["controls"]["gain"].update(value=28.5), "gain must be a whole number"),
    (lambda d: d["controls"]["power_line_frequency"].update(value="70hz"), "power_line_frequency must be one of"),
    (lambda d: d["controls"]["auto_white_balance"].update(value=1), "auto_white_balance must be true or false"),
    (lambda d: d["controls"].update(focus={"value": 3}), r"Not controls of this camera: \['focus'\]"),
    (lambda d: d["controls"]["exposure"].update(units="100us"), "exposure: the preset is in '100us'"),
])
def test_a_bad_value_anywhere_writes_nothing_at_all(cam, tmp_path, edit, match):
    c, be = cam
    p = c.save_preset(tmp_path / "a.json")
    _edit(p, lambda d: d["controls"]["brightness"].update(value=80))   # a valid change that must not go out alone
    _edit(p, edit)
    w0 = controls_mod.write_counts()["_total"]
    with pytest.raises(presets.PresetError, match=match):
        c.load_preset(p)
    with pytest.raises(presets.PresetError, match=match):
        c.plan_preset(p)
    c.flush(timeout=5)
    assert be.writes == [] and controls_mod.write_counts()["_total"] == w0
    assert c.get("brightness") == 50


def test_an_exposure_value_is_left_out_when_the_preset_puts_exposure_in_auto(cam, tmp_path):
    c, be = cam
    p = c.save_preset(tmp_path / "a.json")
    _edit(p, lambda d: d["controls"]["auto_exposure"].update(value="auto"))
    _edit(p, lambda d: d["controls"]["exposure"].update(value=-8))
    r = c.load_preset(p)
    assert r.left_out == ("exposure",)
    assert [(x.name, x.requested) for x in r.results] == [("auto_exposure", "auto")]
    assert be.writes == [("exposure", None, True)]


def test_a_manual_preset_switches_mode_and_value_in_one_write(tmp_path):
    be = SimBackend(autos={"exposure": True})
    with Controls(be) as c:
        p = c.save_preset(tmp_path / "a.json")
        _edit(p, lambda d: d["controls"]["auto_exposure"].update(value="manual"))
        _edit(p, lambda d: d["controls"]["exposure"].update(value=-7))
        r = c.load_preset(p)
        assert r.ok and be.writes == [("exposure", -7, False)]


def test_a_value_the_camera_did_not_keep_is_reported(tmp_path):
    be = SimBackend(ignore_writes=["gain"])
    with Controls(be) as c:
        p = c.save_preset(tmp_path / "a.json")
        _edit(p, lambda d: d["controls"]["gain"].update(value=8))
        r = c.load_preset(p)
        (g,) = r.results
        assert (g.status, g.readback, g.kept) == ("written", 32, False)
        assert not r.ok


def test_a_driver_refusal_is_reported_as_failed(tmp_path):
    be = SimBackend(fail_keys=["gain"])
    with Controls(be) as c:
        p = c.save_preset(tmp_path / "a.json")
        _edit(p, lambda d: d["controls"]["gain"].update(value=40))
        (g,) = c.load_preset(p).results
        assert g.status == "failed" and "rejected" in g.error


def test_timeout_zero_returns_without_waiting(cam, tmp_path):
    c, _ = cam
    p = c.save_preset(tmp_path / "a.json")
    _edit(p, lambda d: d["controls"]["gain"].update(value=40))
    r = c.load_preset(p, timeout=0)
    assert [x.name for x in r.results] == ["gain"] and r.results[0].status in ("pending", "written")
    c.flush(timeout=5)
    assert c.get("gain") == 40


@pytest.mark.parametrize("text, match", [
    ("not json", "is not a preset"),
    ('{"format": "other", "version": 1}', "is not a controls preset"),
    ('{"format": "opticmix-controls-preset", "version": 2, "device": {}, "controls": {}}', "preset version 2"),
    ('{"format": "opticmix-controls-preset", "version": 1, "device": {}, "controls": {"gain": 3}}', "needs"),
])
def test_read_preset_refuses_what_is_not_a_preset(tmp_path, text, match):
    p = tmp_path / "x.json"
    p.write_text(text, encoding="utf-8")
    with pytest.raises(presets.PresetError, match=match):
        presets.read_preset(p)


def test_store_dir_sits_beside_the_calibration_folder():
    from opticmix_vision import calib
    assert presets.store_dir() == calib.store_dir().parent / "presets"


@pytest.mark.parametrize("bad", ["", ".", "..", "a/b", "../x"])
def test_preset_path_takes_a_name_not_a_path(store, bad):
    with pytest.raises(presets.PresetError, match="file name, not a path"):
        presets.preset_path(bad)


def test_list_presets_by_model_skips_files_that_are_not_presets(cam, store):
    c, _ = cam
    c.save_preset(presets.preset_path("b"), note="second")
    c.save_preset(presets.preset_path("a"))
    _edit(presets.preset_path("b"), lambda d: d["device"].update(model="WN Dual Camera"))
    (store / "junk.json").write_text("{}", encoding="utf-8")
    assert [(i.name, i.model, i.note) for i in presets.list_presets()] == [
        ("a", "synthetic", ""), ("b", "WN Dual Camera", "second")]
    assert [i.name for i in presets.list_presets(model="synthetic")] == ["a"]


# --- command line ------------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _no_device_list(monkeypatch):
    """`list` filters by the plugged-in model; a test must not depend on what is plugged into this PC."""
    monkeypatch.setattr(presets, "_connected_model", lambda: None)


def test_cli_save_list_show_load(store, capsys, monkeypatch):
    assert presets.main(["save", "bench", "--synthetic", "--note", "lab"]) == 0
    assert (store / "bench.json").is_file()
    assert presets.main(["save", "bench", "--synthetic"]) == 1
    assert "--overwrite" in capsys.readouterr().err
    assert presets.main(["save", "defaults", "--synthetic"]) == 1

    _edit(store / "bench.json", lambda d: d["controls"]["gain"].update(value=40))
    real_open = presets._open
    monkeypatch.setattr(presets, "_open", lambda s: (_ for _ in ()).throw(AssertionError("opened a camera")))
    capsys.readouterr()
    assert presets.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "defaults" in out and "bench" in out and "synthetic" in out and "lab" in out
    assert presets.main(["show", "bench"]) == 0
    assert "gain" in capsys.readouterr().out
    monkeypatch.setattr(presets, "_open", real_open)

    assert presets.main(["load", "bench", "--synthetic", "--dry-run"]) == 0
    assert "gain" in capsys.readouterr().out
    assert presets.main(["load", "bench", "--synthetic"]) == 0
    out = capsys.readouterr().out
    assert "gain" in out and "written" in out and "kept" in out and "control writes by this process: 0 (simulated" in out


def test_cli_load_refuses_a_bad_preset_and_writes_nothing(store, capsys):
    assert presets.main(["save", "bench", "--synthetic"]) == 0
    _edit(store / "bench.json", lambda d: d["controls"]["gain"].update(value=999))
    w0 = controls_mod.write_counts()["_total"]
    assert presets.main(["load", "bench", "--synthetic"]) == 1
    assert "gain must be" in capsys.readouterr().err
    assert controls_mod.write_counts()["_total"] == w0
    assert presets.main(["load", "nothing-here", "--synthetic"]) == 1


def test_cli_load_defaults_is_reset_to_defaults(store, capsys):
    assert presets.main(["load", "defaults", "--synthetic", "--dry-run"]) == 0
    assert "nothing differs" in capsys.readouterr().out
    assert presets.main(["load", "defaults", "--synthetic"]) == 0


def test_units_from_another_platform_say_what_to_do_and_force_does_not_help(cam, tmp_path):
    c, be = cam
    p = c.save_preset(tmp_path / "a.json")
    _edit(p, lambda d: d["controls"]["exposure"].update(units="100us"))
    for force in (False, True):
        with pytest.raises(presets.PresetError, match="Save the preset again on this computer, or delete 'exposure'"):
            c.load_preset(p, force=force)
    assert be.writes == []


def test_mono_only_controls_are_not_written_unless_asked(cam, tmp_path):
    """Every write is a risk on this camera; hue has no effect on the mono sensor, so it is left alone by default."""
    c, be = cam
    p = c.save_preset(tmp_path / "a.json")
    _edit(p, lambda d: d["controls"]["hue"].update(value=10))
    assert c.plan_preset(p) == {}
    r = c.load_preset(p)
    assert r.results == () and r.skipped_mono == ("hue",) and "hue" not in r.unchanged
    assert be.writes == []
    assert c.plan_preset(p, include_mono=True) == {"hue": (64, 10)}
    r = c.load_preset(p, include_mono=True)
    assert [(x.name, x.status) for x in r.results] == [("hue", "written")] and r.skipped_mono == ()
    assert be.writes == [("hue", 10, None)]


# --- Controls.model through Device (the OpenCV fallback cannot ask the device) ---------------------

class _FakeCap:
    def isOpened(self):  # noqa: N802
        return True

    def get(self, pid):
        return 0.0

    def set(self, pid, v):  # noqa: A003
        return True

    def read(self):
        raise AssertionError("no frames in this test")

    def release(self):
        pass


def _model_of(device):
    from opticmix_vision.device import Device, DeviceConfig
    with Device(DeviceConfig(layout="sbs", lr_order="first_is_right", backend="dshow", device=device),
                capture=_FakeCap()) as d:
        return d.controls.backend_name, d.controls.model


def test_device_tells_the_opencv_backend_the_model_on_windows(monkeypatch):
    from opticmix_vision import devices
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr(devices, "_native_devices", lambda: ["USB2.0 HD UVC WebCam", "WN Dual Camera"])
    assert _model_of(1) == ("opencv-dshow", "WN Dual Camera")
    assert _model_of(0) == ("opencv-dshow", None)
    assert _model_of(5) == ("opencv-dshow", None)            # index past the list: unknown, not a guess


def test_device_tells_the_opencv_backend_the_model_on_linux(monkeypatch, tmp_path):
    from opticmix_vision import devices
    for node, name in (("video0", "WN Dual Camera: WN Dual Camera"), ("video2", "Integrated Camera")):
        (tmp_path / node).mkdir()
        (tmp_path / node / "name").write_text(f"{name}\n")
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr(devices, "SYSFS_V4L", str(tmp_path))
    assert _model_of("/dev/video0")[1] == "WN Dual Camera"
    assert _model_of(0)[1] == "WN Dual Camera"                # OpenCV index N is /dev/videoN
    assert _model_of("/dev/video2")[1] is None
    assert _model_of("/dev/video9")[1] is None


def test_cli_names_the_folder_when_a_preset_is_missing(store, capsys):
    assert presets.main(["show", "nothing-here"]) == 1
    err = capsys.readouterr().err
    assert "No preset named 'nothing-here' in" in err and str(store) in err and "presets list" in err
    assert presets.main(["show", "missing.json"]) == 1
    assert "No such file: missing.json" in capsys.readouterr().err


def test_cli_list_shows_the_connected_model_by_default(cam, store, capsys, monkeypatch):
    c, _ = cam
    c.save_preset(presets.preset_path("mine"))
    c.save_preset(presets.preset_path("cam"))
    _edit(presets.preset_path("cam"), lambda d: d["device"].update(model="WN Dual Camera"))
    monkeypatch.setattr(presets, "_open", lambda s: (_ for _ in ()).throw(AssertionError("opened a camera")))

    def names():
        out = capsys.readouterr().out
        return [ln.split()[0] for ln in out.splitlines()[3:]]

    monkeypatch.setattr(presets, "_connected_model", lambda: "WN Dual Camera")
    assert presets.main(["list"]) == 0 and names() == ["cam"]
    assert presets.main(["list", "--all"]) == 0 and names() == ["cam", "mine"]
    assert presets.main(["list", "--model", "synthetic"]) == 0 and names() == ["mine"]
    monkeypatch.setattr(presets, "_connected_model", lambda: None)
    assert presets.main(["list"]) == 0 and names() == ["cam", "mine"]


def test_cli_load_defaults_on_linux_says_the_defaults_are_not_written(store, capsys, monkeypatch):
    import contextlib
    from opticmix_vision.controls import DEFAULTS_NOT_WRITTEN
    real = presets._open

    @contextlib.contextmanager
    def linux_like(synthetic, cfg=None):
        with real(synthetic, cfg) as cam:
            cam.controls._writes_defaults = False                    # what the OpenCV path on Linux (V4L2) sets
            yield cam
    monkeypatch.setattr(presets, "_open", linux_like)
    for args in (["load", "defaults", "--synthetic", "--dry-run"], ["load", "defaults", "--synthetic"]):
        assert presets.main(args) == 1
        captured = capsys.readouterr()
        assert DEFAULTS_NOT_WRITTEN in captured.err and "nothing differs" not in captured.out
