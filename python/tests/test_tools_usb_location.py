"""--usb-location in the viewer, the explorer, the doctor, presets and calib check: two made-up Weevia S1 that share
a name.

Nothing is enumerated and no camera is opened: the DirectShow list is made up, and OpenCV's VideoCapture and the
native DirectShow control backend are stand-ins that record the device index they are given. The stream and the
controls must both be the unit at the USB location asked for — a preset must never be written to the other one.
No window is opened: the explorer's two methods are called without Tk.
"""
from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("opticmix_vision._core")
from opticmix_vision import _ctrl_dshow, calib, devices, doctor, presets, viewer  # noqa: E402
from opticmix_vision._ctrl_sim import SimBackend  # noqa: E402
from opticmix_vision.controls import Controls  # noqa: E402
from opticmix_vision.device import WEEVIA_S1_NAME, DeviceError  # noqa: E402
from opticmix_vision.devices import DeviceInfo  # noqa: E402

LOC_A = "PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(1)"
LOC_B = "PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(4)#USB(2)"
LOC_WEBCAM = "PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(5)"
# DirectShow order (made up): unit A at index 0, a webcam at 1, unit B at 2
LISTED = [(WEEVIA_S1_NAME, LOC_A), ("Example Webcam", LOC_WEBCAM), (WEEVIA_S1_NAME, LOC_B)]
A, B = 0, 2


class _Capture:
    """What cv2.VideoCapture(device, api) gives here: a new grey level each frame, through the backend asked for."""

    def __init__(self, backend="DSHOW"):
        self.props, self.n, self.backend = {}, 0, backend

    def isOpened(self):  # noqa: N802
        return True

    def get(self, pid):
        return float(self.props.get(pid, 0.0))

    def set(self, pid, value):  # noqa: A003
        self.props[pid] = value
        return True

    def read(self):
        self.n += 1
        return True, np.full((16, 64), self.n % 256, np.uint8)

    def release(self):
        pass

    def getBackendName(self):  # noqa: N802
        return self.backend


class _Unit(SimBackend):
    """The native DirectShow controls of one unit, simulated; `index` is the device index it was made for."""
    model = WEEVIA_S1_NAME
    index: int | None = None


@pytest.fixture
def two_units(monkeypatch):
    """Two Weevia S1 on 'Windows'. Returns (streams, units): the device index of each VideoCapture opened, and each
    control backend made (its `index` and `writes`), in order."""
    import cv2
    streams, units = [], []

    def no_ffmpeg():
        raise FileNotFoundError("ffmpeg")

    def capture(index, api):
        backend = {cv2.CAP_DSHOW: "DSHOW", cv2.CAP_MSMF: "MSMF", cv2.CAP_V4L2: "V4L2"}[api]
        streams.append(index if backend == "DSHOW" else (index, backend))
        return _Capture(backend)

    def control_backend(index, **_):
        u = _Unit()
        u.index = index
        units.append(u)
        return u
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(devices, "_ffmpeg_devices", no_ffmpeg)
    monkeypatch.setattr(devices, "_native_devices", lambda: list(LISTED))
    monkeypatch.setattr(cv2, "VideoCapture", capture)
    monkeypatch.setattr(_ctrl_dshow, "DShowBackend", control_backend)
    return streams, units


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(presets, "store_dir", lambda: tmp_path / "presets")
    return tmp_path / "presets"


def _preset(path, gain=100):
    """A preset of this model that differs from a unit's values in gain only."""
    c = Controls(_Unit())
    try:
        c.save_preset(path)
    finally:
        c.close()
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["controls"]["gain"]["value"] = gain
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def _code(main, argv):
    """The exit code, returned or raised (argparse errors raise SystemExit)."""
    try:
        return main(argv)
    except SystemExit as e:
        return e.code


def _lists_the_locations(err):
    return f"('WN Dual Camera', '{LOC_A}')" in err and f"('WN Dual Camera', '{LOC_B}')" in err


# The refusals every tool shares: nothing at that location (the message lists what is where), an empty location,
# and another way of choosing what to open given with it. Each is exit code 2 and opens nothing.
WRONG, BLANK = ["--usb-location", LOC_WEBCAM], ["--usb-location", " "]
COMBINED = "--usb-location cannot be combined with"


def _refuses(two_units, capsys, main, argv, says):
    assert _code(main, argv) == 2
    err = capsys.readouterr().err
    assert says in err, err
    if says == COMBINED:
        assert f"error: {COMBINED}" in err, err         # argparse's form, the same in every tool
    elif "need --open" not in says:
        assert "] could not open the camera: " in err, err
    assert two_units == ([], []), "nothing may be opened"
    return err


# ---------------------------------------------------------------- viewer

def test_viewer_streams_and_controls_the_unit_at_the_usb_location(two_units, tmp_path):
    streams, units = two_units
    p = _preset(tmp_path / "p.json")
    assert viewer.main(["--usb-location", LOC_B, "--preset", str(p), "--headless", "--frames", "2",
                        "--out", str(tmp_path / "out")]) == 0
    assert streams == [B] and [u.index for u in units] == [B]
    assert [w[:2] for w in units[0].writes] == [("gain", 100)]          # the preset, written to unit B


@pytest.mark.parametrize("argv, says", [
    (WRONG, "No camera matches 'WN Dual Camera' at USB location"),
    (BLANK, "usb_location is empty"),
    (["--usb-location", LOC_B, "--device", "2", "--layout", "sbs", "--lr-order", "first_is_right"], COMBINED),
    (["--usb-location", LOC_B, "--synthetic"], COMBINED),
])
def test_viewer_refuses(two_units, capsys, argv, says):
    err = _refuses(two_units, capsys, viewer.main, argv + ["--headless", "--frames", "1"], says)
    assert argv is not WRONG or _lists_the_locations(err)


def _lists_in_two_orders(monkeypatch):
    """Media Foundation lists the units in another order than DirectShow: B at 0, the webcam at 1, A at 2."""
    monkeypatch.setattr(devices, "_mf_devices", lambda: [LISTED[2], LISTED[1], LISTED[0]])


def test_viewer_msmf_opens_the_media_foundation_index_and_makes_no_directshow_controls(two_units, tmp_path,
                                                                                         monkeypatch):
    streams, units = two_units
    _lists_in_two_orders(monkeypatch)
    assert viewer.main(["--usb-location", LOC_B, "--backend", "msmf", "--headless", "--frames", "2",
                        "--out", str(tmp_path / "out")]) == 0
    assert streams == [(0, "MSMF")] and units == []      # the controls are OpenCV's, on that same capture


def test_viewer_any_refuses_when_the_two_lists_give_different_indexes(two_units, capsys, monkeypatch):
    _lists_in_two_orders(monkeypatch)
    _refuses(two_units, capsys, viewer.main, ["--usb-location", LOC_B, "--backend", "any", "--headless"],
             "index 0 in the Media Foundation list but 2 in the DirectShow list")


def test_viewer_on_linux_opens_the_v4l2_node_at_the_usb_location(two_units, tmp_path, monkeypatch):
    from opticmix_vision import _ctrl_opencv
    from test_devices import TWO_ON_LINUX, _sysfs_usb
    streams, units = two_units
    asked = []
    monkeypatch.setattr(_ctrl_opencv, "v4l2_ranges", lambda node, keys, **kw: asked.append(node) or {})  # opens none
    monkeypatch.setattr(devices, "SYSFS_V4L", _sysfs_usb(tmp_path, TWO_ON_LINUX))     # before 'linux': junctions
    monkeypatch.setattr(sys, "platform", "linux")
    assert viewer.main(["--usb-location", "1-4.2", "--headless", "--frames", "2", "--out", str(tmp_path / "out")]) == 0
    assert streams == [("/dev/video2", "V4L2")] and units == [] and set(asked) <= {"/dev/video2"}


# ---------------------------------------------------------------- explorer (no window)

def _explorer():
    try:   # as test_explorer_app: a Python with _tkinter but no Tk library raises ImportError
        from opticmix_vision.explorer import app
    except ImportError as exc:
        pytest.skip(f"no Tk: {exc}")
    return app


class _Var:
    def __init__(self):
        self.value = ""

    def set(self, v):  # noqa: A003
        self.value = v

    def get(self):
        return self.value


class _Combo(dict):
    """A ttk.Combobox stand-in: its values, and current() — the position of the text shown, as ttk gives it."""

    def __init__(self, var):
        super().__init__(values=[])
        self.var = var

    def current(self):
        values = list(self["values"])
        return values.index(self.var.get()) if self.var.get() in values else -1


def _window(cls, usb_location=None):
    """An ExplorerApp with stand-in widgets and no Tk: its own _rescan, _on_device, _on_mode and _open_source run;
    _restart does nothing (no capture thread) — the test calls _open_source as the capture thread would."""
    from opticmix_vision.explorer.state import ExplorerState
    w = cls.__new__(cls)
    dv, mv = _Var(), _Var()
    w.__dict__.update(synthetic=False, usb_location=usb_location, state=ExplorerState(), device=None, source=None,
                      device_var=dv, device_combo=_Combo(dv), mode_var=mv, mode_combo=_Combo(mv), message_var=_Var(),
                      _restart=lambda: None)
    return w


def _write_gain(w):
    """Open as the capture thread does, write gain as the Settings window does, close."""
    dev = w._open_source()
    try:
        dev.controls.set("gain", 100)
        dev.controls.flush(timeout=5)
    finally:
        dev.__exit__(None, None, None)


def test_explorer_lists_streams_and_controls_only_the_unit_at_the_usb_location(two_units, monkeypatch):
    app = _explorer()
    from opticmix_vision.explorer.state import KNOWN_MODE
    streams, units = two_units
    made, real = [], app.ExplorerApp
    monkeypatch.setattr(app, "ExplorerApp", lambda **kw: made.append(kw) or SimpleNamespace(run=lambda: 0))
    assert app.main(["--usb-location", LOC_B]) == 0 and made == [{"synthetic": False, "usb_location": LOC_B}]
    monkeypatch.setattr(app, "list_modes", lambda name: pytest.fail("the mode list was asked of ffmpeg by name"))
    w = _window(real, LOC_B)
    w._rescan()                                        # the device list holds unit B only
    assert w._devices == [DeviceInfo(B, WEEVIA_S1_NAME, LOC_B)] and w.device_combo["values"] == [WEEVIA_S1_NAME]
    assert w.state.device == w._devices[0] and w.state.modes == [KNOWN_MODE]
    assert "not read: ffmpeg asks by name" in w.message_var.get()
    _write_gain(w)
    assert streams == [B] and [u.index for u in units] == [B] and [x[:2] for x in units[0].writes] == [("gain", 100)]
    w.usb_location = LOC_WEBCAM                        # refreshed after the camera moved: no device, the error shown
    w._rescan()
    assert w._devices == [] and "No camera matches 'WN Dual Camera' at USB location" in w.message_var.get()


def test_explorer_finds_the_camera_again_by_usb_location_at_every_open(two_units, monkeypatch):
    # started on unit A (index 0); then the list order changes: unit B is index 0 and A is 2
    app = _explorer()
    streams, units = two_units
    monkeypatch.setattr(app, "list_modes", lambda name: pytest.fail("the mode list was asked of ffmpeg by name"))
    w = _window(app.ExplorerApp, LOC_A)
    w._rescan()
    assert w.state.device.index == A
    monkeypatch.setattr(devices, "_native_devices", lambda: [LISTED[2], LISTED[1], LISTED[0]])
    _write_gain(w)
    assert streams == [2] and [u.index for u in units] == [2] and [x[:2] for x in units[0].writes] == [("gain", 100)]
    monkeypatch.setattr(devices, "_native_devices", lambda: [LISTED[1], LISTED[2]])          # unit A unplugged
    with pytest.raises(LookupError, match="No camera matches 'WN Dual Camera' at USB location"):
        w._open_source()
    assert streams == [2] and len(units) == 1, "nothing may be opened"


def test_explorer_without_a_location_tells_two_cameras_of_one_name_apart(two_units, monkeypatch):
    app = _explorer()
    streams, units = two_units
    monkeypatch.setattr(app, "list_modes", lambda name: pytest.fail("the mode list was asked of ffmpeg by name"))
    w = _window(app.ExplorerApp)
    w._rescan()
    labels = list(w.device_combo["values"])
    assert labels == [f"{WEEVIA_S1_NAME} · {LOC_A}", "Example Webcam", f"{WEEVIA_S1_NAME} · {LOC_B}"]
    assert w.state.device.index == A and "not read: ffmpeg asks by name" in w.message_var.get()
    w.device_var.set(labels[2])                        # unit B chosen in the list
    w._on_device()
    assert w.state.device == DeviceInfo(B, WEEVIA_S1_NAME, LOC_B)
    monkeypatch.setattr(devices, "_native_devices", lambda: [LISTED[2], LISTED[0]])          # B is now index 0
    _write_gain(w)
    assert streams == [0] and [u.index for u in units] == [0] and [x[:2] for x in units[0].writes] == [("gain", 100)]
    monkeypatch.setattr(devices, "_native_devices", lambda: [WEEVIA_S1_NAME, WEEVIA_S1_NAME])  # locations unread
    w._rescan()
    with pytest.raises(DeviceError, match="cannot be told apart"):
        w._open_source()
    assert streams == [0] and len(units) == 1, "nothing may be opened"


def test_explorer_refuses_a_usb_location_on_linux(two_units, capsys, monkeypatch):
    app = _explorer()
    monkeypatch.setattr(app, "ExplorerApp", lambda **kw: pytest.fail("the window was opened"))
    monkeypatch.setattr(sys, "platform", "linux")
    _refuses(two_units, capsys, app.main, ["--usb-location", "1-4.2"], "lists cameras on Windows only")


def test_explorer_lists_the_directshow_list_it_opens_with_also_on_linux(monkeypatch, tmp_path):
    # the explorer opens with backend="dshow" and finds a camera again in that list: a V4L2 index or sysfs location
    # names nothing there. So on Linux it lists no cameras, as in 0.0.6, although list_devices() now lists them there
    app = _explorer()
    from test_devices import ONE_CAMERA, WEEVIA_USB, _sysfs_tree

    def no_ffmpeg():
        raise FileNotFoundError("ffmpeg")
    monkeypatch.setattr(devices, "SYSFS_V4L", _sysfs_tree(tmp_path, {"2-1": WEEVIA_USB}, ONE_CAMERA))
    monkeypatch.setattr(devices, "_ffmpeg_devices", no_ffmpeg)
    monkeypatch.setattr(sys, "platform", "linux")
    assert len(devices.list_devices()) == 1
    w = _window(app.ExplorerApp)
    w._rescan()
    assert w._devices == [] and w.message_var.get().startswith("No camera found")


@pytest.mark.parametrize("argv, says", [
    (WRONG, "No camera matches 'WN Dual Camera' at USB location"),
    (BLANK, "usb_location is empty"),
    (["--usb-location", LOC_B, "--synthetic"], COMBINED),
])
def test_explorer_refuses_before_the_window_opens(two_units, capsys, monkeypatch, argv, says):
    app = _explorer()
    monkeypatch.setattr(app, "ExplorerApp", lambda **kw: pytest.fail("the window was opened"))
    err = _refuses(two_units, capsys, app.main, argv, says)
    assert argv is not WRONG or _lists_the_locations(err)


def test_explorer_usb_line_is_that_of_the_unit_at_the_usb_location(monkeypatch):
    # the line over the device list (VID:PID, revision, link speed): two units at different link speeds
    def record(location):
        return {"name": WEEVIA_S1_NAME, "started": True, "problem": 0, "location_paths": [location],
                "hardware_ids": ["USB\\VID_1D6B&PID_0200&REV_0409"], "location_info": None, "address": None,
                "hub_id": None}
    listed = [DeviceInfo(A, WEEVIA_S1_NAME, LOC_A, 0x1D6B, 0x0200, 0x0409, "High"),
              DeviceInfo(B, WEEVIA_S1_NAME, LOC_B, 0x1D6B, 0x0200, 0x0409, "Super")]
    monkeypatch.setattr(devices, "list_devices", lambda backend="dshow": listed)
    real = doctor._hardware
    monkeypatch.setattr(doctor, "_hardware",
                        lambda *a, **k: real(False, [record(LOC_A), record(LOC_B)], None, None, "win32", **k))
    assert doctor._usb_summary() == "1d6b:0200 · REV 0409 · High · no serial"         # no location: the first one
    assert doctor._usb_summary(usb_location=LOC_B.lower()) == "1d6b:0200 · REV 0409 · Super · no serial"
    assert doctor._usb_summary(usb_location=LOC_WEBCAM) == "USB: — · no serial"


# ---------------------------------------------------------------- doctor --open

def test_doctor_opens_and_reads_the_controls_of_the_unit_at_the_usb_location(two_units, monkeypatch, tmp_path):
    streams, units = two_units
    real = doctor.collect
    monkeypatch.setattr(doctor, "collect", lambda **kw: real(**kw, _platform="darwin"))   # no OS device tree here
    monkeypatch.setattr(calib, "store_dir", lambda: tmp_path / "calib")   # never the user's folder (nor %APPDATA%)
    out = tmp_path / "r.json"
    assert doctor.main(["--open", "--usb-location", LOC_B, "--seconds", "0.1", "--output", str(out)]) == 0
    r = json.loads(out.read_text(encoding="utf-8"))
    assert r["camera"]["opened"] and r["camera"]["controls"]["count"] > 0, r["camera"]
    assert streams == [B] and [u.index for u in units] == [B] and units[0].writes == []
    assert (r["camera"]["usb_location"], r["camera"]["device"]) == (LOC_B, B)      # which unit the report is about
    text = doctor.format_text(r)
    assert f"USB location:    {LOC_B} (--usb-location; opened as device {B})" in text
    assert "advertised modes: not listed with --usb-location" in text


@pytest.mark.parametrize("argv, says", [
    (["--open"] + WRONG, "No camera matches 'WN Dual Camera' at USB location"),
    (["--open"] + BLANK, "usb_location is empty"),
    (["--usb-location", LOC_B], "--usb-location need --open"),
    (["--open", "--usb-location", LOC_B, "--device", "2"], COMBINED),
    (["--open", "--usb-location", LOC_B, "--synthetic"], COMBINED),
])
def test_doctor_refuses(two_units, capsys, monkeypatch, argv, says):
    monkeypatch.setattr(doctor, "collect", lambda **kw: pytest.fail("the report was collected"))
    err = _refuses(two_units, capsys, doctor.main, argv, says)
    assert argv[1:] != WRONG or _lists_the_locations(err)


# ---------------------------------------------------------------- presets save / load

def test_presets_save_and_load_touch_only_the_unit_at_the_usb_location(two_units, store, tmp_path):
    streams, units = two_units
    assert presets.main(["save", "b", "--usb-location", LOC_B]) == 0
    assert streams == [B] and [u.index for u in units] == [B] and units[0].writes == []
    p = _preset(tmp_path / "p.json")
    for location, unit in ((LOC_B, B), (LOC_A, A)):
        streams.clear()
        units.clear()
        assert presets.main(["load", str(p), "--usb-location", location]) == 0
        assert streams == [unit] and [u.index for u in units] == [unit], location
        assert [w[:2] for w in units[0].writes] == [("gain", 100)], location   # written there and nowhere else


@pytest.mark.parametrize("cmd", [["save", "x"], ["load", "p.json"]])
@pytest.mark.parametrize("argv, says", [
    (WRONG, "No camera matches 'WN Dual Camera' at USB location"),
    (BLANK, "usb_location is empty"),
    (["--usb-location", LOC_B, "--synthetic"], COMBINED),
])
def test_presets_refuse(two_units, store, tmp_path, capsys, monkeypatch, cmd, argv, says):
    monkeypatch.chdir(tmp_path)
    _preset(tmp_path / "p.json")
    err = _refuses(two_units, capsys, presets.main, cmd + argv, says)
    assert argv is not WRONG or _lists_the_locations(err)


# ---------------------------------------------------------------- calib check

def test_calib_check_reads_the_unit_at_the_usb_location(two_units, monkeypatch, capsys):
    streams, units = two_units
    monkeypatch.setattr(calib, "load_calibration", lambda f: SimpleNamespace(device={"serial": "S1-0042"}))
    pairs = []
    monkeypatch.setattr(calib, "compare_pairs", lambda p, cals: pairs.append(len(p)) or
                        {"S1-0042": SimpleNamespace(enough=True, summary=lambda: "fits")})
    assert calib.main(["check", "--usb-location", LOC_B, "--file", "S1-0042.json", "--frames", "5"]) == 0
    assert streams == [B] and units == [] and pairs == [5]           # frames from unit B; check writes no control


@pytest.mark.parametrize("argv, says", [
    (WRONG, "No camera matches 'WN Dual Camera' at USB location"),
    (BLANK, "usb_location is empty"),
    (["--usb-location", LOC_B, "--synthetic"], COMBINED),
    (["--usb-location", LOC_B, "--pairs-dir", "."], COMBINED),
])
def test_calib_check_refuses(two_units, capsys, monkeypatch, argv, says):
    monkeypatch.setattr(calib, "load_calibration", lambda f: SimpleNamespace(device={"serial": "S1-0042"}))
    err = _refuses(two_units, capsys, calib.main, ["check", "--file", "S1-0042.json"] + argv, says)
    assert argv is not WRONG or _lists_the_locations(err)
