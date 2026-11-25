"""python -m opticmix_vision.doctor: the report, on fake Windows device-tree records and a fake Linux sysfs,
the --open path on generated and fake frames, the JSON form, and that it writes no control."""
from __future__ import annotations

import csv
import json
import os
import sys
from pathlib import Path

import numpy as np
import pytest

from opticmix_vision import calib, devices, doctor
from opticmix_vision.device import Device, DeviceConfig
from opticmix_vision.devices import DeviceInfo

# What cfgmgr32 returned for a Weevia S1 behind a hub and a laptop webcam (two interfaces) — the shape of
# doctor._win_cameras(). The webcam's USB serial is in usb_id; the report must not carry it.
WIN_RECORDS = [
    {"name": "Laptop webcam", "started": True, "problem": 0,
     "hardware_ids": ["USB\\VID_636E&PID_0BDA&REV_0007", "USB\\VID_636E&PID_0BDA"],
     "location_paths": ["PCIROOT(0)#PCI(0801)#PCI(0004)#USBROOT(0)#USB(1)"],
     "location_info": "Port_#0001.Hub_#0013", "address": 1,
     "usb_id": "USB\\VID_636E&PID_0BDA\\200901010001", "hub_id": "USB\\ROOT_HUB30\\5&287DC944&0&0"},
    {"name": "Laptop IR camera", "started": True, "problem": 0,
     "hardware_ids": ["USB\\VID_636E&PID_0BDA&REV_0007", "USB\\VID_636E&PID_0BDA"],
     "location_paths": ["PCIROOT(0)#PCI(0801)#PCI(0004)#USBROOT(0)#USB(1)"],
     "location_info": "Port_#0001.Hub_#0013", "address": 1,
     "usb_id": "USB\\VID_636E&PID_0BDA\\200901010001", "hub_id": "USB\\ROOT_HUB30\\5&287DC944&0&0"},
    {"name": "WN Dual Camera", "started": True, "problem": 0,
     "hardware_ids": ["USB\\VID_1D6B&PID_0200&REV_0409&MI_00", "USB\\VID_1D6B&PID_0200&REV_0409"],
     "location_paths": ["PCIROOT(0)#PCI(0803)#PCI(0004)#USBROOT(0)#USB(2)#USB(4)#USB(2)"],
     "location_info": "Port_#0002.Hub_#0010", "address": 2,
     "usb_id": "USB\\VID_1D6B&PID_0200\\8&C90EF6C&0&2", "hub_id": "USB\\VID_05E3&PID_0626\\7&18B4FC45&0&4"},
]
# What _hub_link returns: (parsed EX reply, V2 flags). Webcam: SuperSpeed (flags: operating + capable).
# Weevia: High speed on a port, SuperSpeed-capable (flag bit 1 only).
HUBS = {("USB\\ROOT_HUB30\\5&287DC944&0&0", 1): ({"vid": 0x636E, "pid": 0x0BDA, "bcd_device": 7, "speed": 2, "status": 1}, 0b11),
        ("USB\\VID_05E3&PID_0626\\7&18B4FC45&0&4", 2): ({"vid": 0x1D6B, "pid": 0x0200, "bcd_device": 0x409, "speed": 2,
                                                          "status": 1}, 0b10)}


@pytest.fixture(autouse=True)
def _scratch_calib(monkeypatch, tmp_path):
    """Never the user's calibration folder."""
    monkeypatch.setattr(calib, "store_dir", lambda: tmp_path / "calib")
    return tmp_path / "calib"


@pytest.fixture
def no_hardware(monkeypatch):
    """Anything that reads the machine's devices fails the test."""
    def boom(*a, **k):
        raise AssertionError("hardware was read")
    monkeypatch.setattr(doctor, "_win_cameras", boom)
    monkeypatch.setattr(doctor, "_hub_link", boom)
    monkeypatch.setattr(doctor, "_linux", boom)
    monkeypatch.setattr(devices, "list_devices", boom)
    monkeypatch.setattr(devices, "list_modes", boom)
    monkeypatch.setattr(Device, "open", boom)


def _windows(monkeypatch, names=("Laptop webcam", "WN Dual Camera"), records=WIN_RECORDS, link=None, mf_names=None):
    """DirectShow lists `names`; Media Foundation lists `mf_names` (the same as DirectShow when not given)."""
    lists = {"dshow": names, "msmf": names if mf_names is None else mf_names}
    monkeypatch.setattr(devices, "list_devices",
                        lambda backend="dshow": [DeviceInfo(i, n) for i, n in enumerate(lists[backend])])
    return doctor.collect(_platform="win32", _win_records=records, _link=link or (lambda h, p: HUBS[(h, p)]))


# ---------------------------------------------------------------- Windows: device tree

def test_windows_usb_details_come_from_the_device_tree(monkeypatch):
    r = _windows(monkeypatch)
    assert r["video_devices"]["devices"] == [{"index": 0, "name": "Laptop webcam"}, {"index": 1, "name": "WN Dual Camera"}]
    assert r["weevia_s1"] == {"name": "WN Dual Camera", "count": 1}
    w = [d for d in r["usb"]["devices"] if d["name"] == "WN Dual Camera"]
    assert w == [{"name": "WN Dual Camera", "vid": "1d6b", "pid": "0200", "usb_device_revision": "0409",
                  "status": "OK", "location": "PCIROOT(0)#PCI(0803)#PCI(0004)#USBROOT(0)#USB(2)#USB(4)#USB(2)",
                  "port": "Port_#0002.Hub_#0010", "link_speed": "High", "superspeed_capable": True}]
    assert len(r["usb"]["devices"]) == 3 and r["usb"]["devices"][0]["link_speed"] == "Super"
    assert r["camera"] is None
    text = doctor.format_text(r)
    assert "USB device revision (bcdDevice)  0409" in text
    assert "link speed                       High (480 Mb/s), device is SuperSpeed-capable" in text
    assert any("USB link is High" in f and "USB 3 port" in f for f in r["findings"])
    assert "not opened (add --open" in text


def test_the_report_never_calls_the_revision_a_firmware_version(monkeypatch):
    text = doctor.format_text(_windows(monkeypatch))
    lines = [ln for ln in text.splitlines() if "firmware" in ln.lower()]
    assert len(lines) == 1 and "not reported" in lines[0] and "has not been verified" in lines[0]
    assert "temperature      not available" in text
    assert "serial number    not reported" in text


def test_the_report_carries_no_usb_serial_or_instance_id(monkeypatch):
    r = _windows(monkeypatch)
    blob = json.dumps(r) + doctor.format_text(r)
    assert "200901010001" not in blob and "C90EF6C" not in blob.upper() and "ROOT_HUB30" not in blob


def test_windows_problem_code_and_unknown_link(monkeypatch):
    rec = dict(WIN_RECORDS[2], started=False, problem=10)

    def link(h, p):
        raise OSError("hub busy")
    r = _windows(monkeypatch, records=[rec], link=link)
    d = r["usb"]["devices"][0]
    assert d["status"] == "problem code 10" and d["link_speed"] == "unknown" and d["superspeed_capable"] is None
    assert "The operating system reports the Weevia S1 as: problem code 10." in r["findings"]
    assert "link speed                       unknown\n" in doctor.format_text(r)


def test_no_weevia_and_two_weevias_are_findings(monkeypatch):
    assert "No Weevia S1 in the video device list. Re-insert the USB cable." in _windows(monkeypatch, names=("cam",))["findings"]
    r = _windows(monkeypatch, names=("WN Dual Camera", "WN Dual Camera"))
    assert r["weevia_s1"]["count"] == 2
    two = [f for f in r["findings"] if f.startswith("2 Weevia S1 are connected")]
    assert len(two) == 1 and "DeviceConfig.weevia_s1(usb_location=...)" in two[0], two
    assert two[0].endswith("not run with two cameras."), two


def test_a_failing_device_listing_is_reported_not_raised(monkeypatch):
    def fail(backend="dshow"):
        raise RuntimeError(f"no {backend}")
    monkeypatch.setattr(devices, "list_devices", fail)
    r = doctor.collect(_platform="win32", _win_records=[], _link=lambda h, p: None)
    assert r["video_devices"]["error"] == "RuntimeError: no dshow" and r["weevia_s1"]["count"] is None
    assert r["video_devices"]["media_foundation"] == {"devices": [], "error": "RuntimeError: no msmf", "note": None}
    text = doctor.format_text(r)
    assert "error: RuntimeError: no dshow" in text
    assert 'Media Foundation (backend="msmf"; listed, not opened):\n  error: RuntimeError: no msmf\n' in text


# ---------------------------------------------------------------- Windows: the Media Foundation list beside DirectShow

# Two lists holding the same three cameras in a different order, the shape seen once on one PC (2026-11-24). Which
# order goes with which backend is chosen here for the test; the other two names are made up.
DS_TEXT = ('[dshow @ 0] "WN Dual Camera" (video)\n[dshow @ 0] "Laptop AF webcam" (video)\n'
           '[dshow @ 0] "Laptop webcam" (video)\n[dshow @ 0] "Microphone (Example Audio)" (audio)\n')
MF_NAMES = ["Laptop webcam", "WN Dual Camera", "Laptop AF webcam"]
NOTE = ("Weevia S1 index: DirectShow 0, Media Foundation 1. The two lists can be in a different order; an index goes "
        "only with the backend whose list it came from (OpenCV CAP_DSHOW or CAP_MSMF). DeviceConfig.weevia_s1() finds "
        "the camera by name in the list of its backend; with backend='any' it stops when the two indexes differ.")


def test_windows_prints_both_lists_and_a_line_when_the_weevia_index_differs(monkeypatch):
    pytest.importorskip("opticmix_vision._core")
    monkeypatch.setattr(devices, "_ffmpeg_devices", lambda: DS_TEXT)           # the real list_devices(), fake lists
    monkeypatch.setattr(devices, "_mf_devices", lambda: list(MF_NAMES))
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    r = doctor.collect(_platform="win32", _win_records=[WIN_RECORDS[2]], _link=lambda h, p: HUBS[(h, p)])
    v = json.loads(json.dumps(r))["video_devices"]
    assert v == {"source": "DirectShow", "error": None,
                 "devices": [{"index": 0, "name": "WN Dual Camera"}, {"index": 1, "name": "Laptop AF webcam"},
                             {"index": 2, "name": "Laptop webcam"}],
                 "media_foundation": {"devices": [{"index": 0, "name": "Laptop webcam"},
                                                  {"index": 1, "name": "WN Dual Camera"},
                                                  {"index": 2, "name": "Laptop AF webcam"}],
                                      "error": None, "note": NOTE}}
    assert r["weevia_s1"] == {"name": "WN Dual Camera", "count": 1}
    assert not any("Media Foundation" in f or "index" in f for f in r["findings"])   # information, not a finding
    text = doctor.format_text(r)
    assert text[text.index("== Video devices"):text.index("== USB")].splitlines() == [
        "== Video devices (DirectShow) ==",
        "  0  WN Dual Camera",
        "  1  Laptop AF webcam",
        "  2  Laptop webcam",
        'Media Foundation (backend="msmf"; listed, not opened):',
        "  0  Laptop webcam",
        "  1  WN Dual Camera",
        "  2  Laptop AF webcam",
        "Weevia S1 ('WN Dual Camera') connected: 1",
        NOTE]
    monkeypatch.setattr(doctor.shutil, "which", lambda name: "C:/ffmpeg/bin/ffmpeg.exe")    # the list through ffmpeg
    assert doctor.collect(_platform="win32", _win_records=[])["video_devices"]["source"] == "DirectShow, through ffmpeg"
    assert "== Findings ==\n- none\n" not in text or r["findings"] == []


def test_the_same_index_in_both_lists_prints_no_line(monkeypatch):
    r = _windows(monkeypatch, mf_names=("Laptop webcam", "WN Dual Camera", "Laptop IR camera"))
    m = r["video_devices"]["media_foundation"]
    assert m["note"] is None and [d["name"] for d in m["devices"]][2] == "Laptop IR camera"
    text = doctor.format_text(r)
    assert "  2  Laptop IR camera\nWeevia S1 ('WN Dual Camera') connected: 1\n== USB" in text
    assert "Weevia S1 index" not in text


@pytest.mark.parametrize("ds,mf,want", [
    (("WN Dual Camera",), (), "DirectShow 0, Media Foundation not listed."),
    (("cam",), ("WN Dual Camera",), "DirectShow not listed, Media Foundation 0."),
    (("WN Dual Camera", "cam", "WN Dual Camera"), ("cam", "WN Dual Camera", "WN Dual Camera"),
     "DirectShow 0, 2, Media Foundation 1, 2."),
])
def test_a_weevia_in_one_list_only_or_at_other_places_is_a_line(monkeypatch, ds, mf, want):
    r = _windows(monkeypatch, names=ds, mf_names=mf)
    note = r["video_devices"]["media_foundation"]["note"]
    assert note.startswith(f"Weevia S1 index: {want} The two lists can be in a different order;")
    text = doctor.format_text(r)
    assert note in text and not any(note in f for f in r["findings"])
    if not mf:
        assert 'Media Foundation (backend="msmf"; listed, not opened):\n  (none)\n' in text


def test_a_failing_media_foundation_listing_keeps_the_directshow_part(monkeypatch):
    def lists(backend="dshow"):
        if backend == "msmf":
            raise RuntimeError("MFStartup failed: HRESULT 0x80004005")
        return [DeviceInfo(0, "WN Dual Camera")]
    monkeypatch.setattr(devices, "list_devices", lists)
    r = doctor.collect(_platform="win32", _win_records=[], _link=lambda h, p: None)
    assert r["video_devices"]["devices"] == [{"index": 0, "name": "WN Dual Camera"}] and r["weevia_s1"]["count"] == 1
    assert r["video_devices"]["media_foundation"] == {
        "devices": [], "error": "RuntimeError: MFStartup failed: HRESULT 0x80004005", "note": None}
    assert "  error: RuntimeError: MFStartup failed: HRESULT 0x80004005\nWeevia S1 ('WN Dual Camera') connected: 1\n" \
        in doctor.format_text(r)


def test_only_windows_has_a_media_foundation_part(tmp_path):
    for r in (doctor.collect(_platform="linux", _sysfs=str(_fake_sysfs(tmp_path))), doctor.collect(_platform="darwin"),
              doctor.collect(synthetic=True, _platform="win32")):
        assert "media_foundation" not in r["video_devices"]
        assert "Media Foundation" not in doctor.format_text(r)


# ---------------------------------------------------------------- Linux: sysfs

def _link(target: Path, link: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        if sys.platform != "win32":
            raise
        import _winapi                                     # a junction needs no privilege on Windows
        _winapi.CreateJunction(str(target), str(link))


def _fake_sysfs(tmp_path: Path) -> Path:
    """sysfs as Linux lays it out: /sys/class/video4linux/videoN/device -> the USB interface, whose parent
    directory is the USB device (idVendor, idProduct, bcdDevice, speed). (Interface directories are named
    1-2:1.0 on Linux; ':' is not allowed in a Windows file name, so 1-2_1.0 here.)"""
    usb = tmp_path / "devices" / "usb1"
    for dev, vid, pid, rev, speed in (("1-2", "1d6b", "0200", "0409", "480"), ("1-4", "636e", "0bda", "0007", "10000")):
        (usb / dev / f"{dev}_1.0").mkdir(parents=True)
        for f, v in (("idVendor", vid), ("idProduct", pid), ("bcdDevice", rev), ("speed", speed)):
            (usb / dev / f).write_text(v + "\n")
    cls = tmp_path / "class"
    for node, name, index, dev in (("video0", "WN Dual Camera", "0", "1-2"), ("video1", "WN Dual Camera", "1", "1-2"),
                                   ("video2", "Laptop webcam", "0", "1-4"), ("video10", "Dummy video device", "0", None)):
        (cls / node).mkdir(parents=True)
        (cls / node / "name").write_text(name + "\n")
        (cls / node / "index").write_text(index + "\n")
        if dev:
            _link(usb / dev / f"{dev}_1.0", cls / node / "device")
    return cls


def test_linux_reads_video_nodes_and_usb_details_from_sysfs(tmp_path):
    r = doctor.collect(_platform="linux", _sysfs=str(_fake_sysfs(tmp_path)))
    nodes = r["video_devices"]["devices"]
    assert [(n["path"], n["name"], n["index"], n.get("usb")) for n in nodes] == [
        ("/dev/video0", "WN Dual Camera", 0, "1-2"), ("/dev/video1", "WN Dual Camera", 1, "1-2"),
        ("/dev/video2", "Laptop webcam", 0, "1-4"), ("/dev/video10", "Dummy video device", 0, None)]
    assert r["weevia_s1"]["count"] == 1                    # the capture node only, as find_v4l2_node counts
    assert r["usb"]["devices"] == [
        {"name": "WN Dual Camera", "vid": "1d6b", "pid": "0200", "usb_device_revision": "0409", "status": "OK",
         "location": "1-2", "port": None, "link_speed": "High", "superspeed_capable": None},
        {"name": "Laptop webcam", "vid": "636e", "pid": "0bda", "usb_device_revision": "0007", "status": "OK",
         "location": "1-4", "port": None, "link_speed": "Super+", "superspeed_capable": None}]
    text = doctor.format_text(r)
    assert "/dev/video1  WN Dual Camera  (index 1)" in text and "link speed                       High (480 Mb/s)\n" in text
    assert any("USB link is High" in f for f in r["findings"])


def test_linux_with_no_video_nodes(tmp_path):
    (tmp_path / "empty").mkdir()
    r = doctor.collect(_platform="linux", _sysfs=str(tmp_path / "empty"))
    assert r["video_devices"]["devices"] == [] and r["usb"]["devices"] == [] and r["weevia_s1"]["count"] == 0


# ---------------------------------------------------------------- --open

def test_synthetic_open_runs_everything_without_hardware(no_hardware, capsys, tmp_path):
    pytest.importorskip("opticmix_vision._core")
    assert doctor.main(["--synthetic", "--open", "--seconds", "0.3", "--timestamps", str(tmp_path / "t.csv")]) == 0
    out = capsys.readouterr().out
    for section in ("== Findings ==\n- none", "== System ==", "== SDK ==", "== Video devices (skipped (--synthetic))",
                    "== USB (skipped (--synthetic)", "Not reported by this camera:", "== Calibration ==", "== Camera ==",
                    "== Log (opticmix_vision warnings during this run) ==\n  (none)"):
        assert section in out
    assert "negotiated mode: 640x200 SYNT 90 fps (synthetic)" in out
    assert "no eye stalled (threshold 90)" in out
    assert "controls (14, backend sim; read only)" in out
    assert "  exposure                -5       -11..-3 step 1      -5       log2_s_nominal  verified" in out
    assert "control writes by this run: 0 (SDK write counter before 0, after 0)" in out
    rows = list(csv.reader((tmp_path / "t.csv").open(encoding="utf-8")))
    assert rows[0] == ["frame", "host_ns", "interval_ms", "left_identical", "right_identical"]
    n = int(next(ln for ln in out.splitlines() if ln.startswith("frames:")).split()[1])
    assert len(rows) - 1 == n > 10 and rows[1][2] == "" and float(rows[2][2]) > 0


def test_json_report(no_hardware, capsys):
    pytest.importorskip("opticmix_vision._core")
    assert doctor.main(["--synthetic", "--open", "--json", "--seconds", "0.2"]) == 0
    r = json.loads(capsys.readouterr().out)
    assert list(r) == ["report", "format", "time", "synthetic", "system", "sdk", "video_devices", "usb", "weevia_s1",
                       "not_available", "calibration", "camera", "log", "findings"]
    assert r["report"] == "opticmix_vision.doctor" and r["format"] == 1 and r["synthetic"] is True
    assert set(r["not_available"]) == {"serial_number", "firmware_version", "temperature"}
    cam = r["camera"]
    assert cam["opened"] and cam["mode"]["fourcc"] == "SYNT" and cam["control_writes"]["by_this_run"] == 0
    assert cam["controls"]["count"] == 14 and cam["controls"]["controls"]["gain"]["maximum"] == 255
    assert cam["measure"]["fps"] > 0 and cam["measure"]["stalled"] == []


def test_open_reads_the_controls_and_writes_none(no_hardware):
    pytest.importorskip("opticmix_vision._core")
    from opticmix_vision.synthetic import SyntheticStereoSource
    src = SyntheticStereoSource()
    r = doctor.collect(open_camera=True, synthetic=True, seconds=0.1, _open=lambda: src)
    assert src._sim.writes == []                           # nothing reached the (simulated) device
    assert r["camera"]["control_writes"]["by_this_run"] == 0 and r["findings"] == []


class _StuckCapture:
    """A VideoCapture that returns the same side-by-side frame forever (a stopped stream)."""

    def __init__(self):
        self.frame = np.zeros((8, 16), np.uint8)
        self.sets: list[tuple[int, float]] = []

    def isOpened(self):  # noqa: N802
        return True

    def get(self, pid):
        return 0.0

    def set(self, pid, v):
        self.sets.append((pid, v))
        return True

    def read(self):
        return True, self.frame

    def release(self):
        pass


def test_open_on_a_guarded_device_writes_no_control_and_finds_the_stall():
    pytest.importorskip("opticmix_vision._core")
    import cv2
    from opticmix_vision import write_counts
    cap = _StuckCapture()
    cfg = DeviceConfig(layout="sbs", lr_order="first_is_right", backend="dshow", stall_frames=0)
    r = doctor.collect(open_camera=True, seconds=0.2, _platform="darwin", _open=lambda: Device(cfg, capture=cap).open())
    assert {pid for pid, _ in cap.sets} == {cv2.CAP_PROP_BUFFERSIZE}        # format only, set by open()
    assert write_counts()["_total"] == 0 and r["camera"]["control_writes"]["by_this_run"] == 0
    m = r["camera"]["measure"]
    assert m["stalled"] == ["left", "right"] and m["left_longest_identical_run"] == m["frames"] + 1
    assert any("left and right eye sent the same image 90+ frames in a row" in f for f in r["findings"])
    assert r["camera"]["controls"]["backend"] == "opencv-dshow"


def test_a_camera_that_does_not_open_is_a_finding():
    def fail():
        raise RuntimeError("failed to open the camera")
    r = doctor.collect(open_camera=True, synthetic=True, _open=fail)
    assert r["camera"]["opened"] is False and r["camera"]["error"] == "RuntimeError: failed to open the camera"
    assert "open failed: RuntimeError: failed to open the camera" in doctor.format_text(r)


def test_without_open_the_camera_is_not_opened(no_hardware):
    r = doctor.collect(synthetic=True)
    assert r["camera"] is None


def test_the_write_log_setting_is_reported(no_hardware, monkeypatch, tmp_path):
    monkeypatch.delenv("OPTICMIX_WRITE_LOG", raising=False)
    assert "write log:       off" in doctor.format_text(doctor.collect(synthetic=True))
    monkeypatch.setenv("OPTICMIX_WRITE_LOG", str(tmp_path / "w.jsonl"))
    assert doctor.collect(synthetic=True)["sdk"]["write_log"].endswith("w.jsonl")


@pytest.mark.parametrize("argv", [["--timestamps", "x.csv"], ["--device", "0"], ["--backend", "msmf"],
                                  ["--open", "--seconds", "0"]])
def test_cli_refuses_options_that_need_open(argv):
    with pytest.raises(SystemExit) as e:
        doctor.main(argv)
    assert e.value.code == 2


# ---------------------------------------------------------------- calibration folder

def test_calibration_section_lists_the_folder_and_checks_the_file(_scratch_calib, no_hardware):
    pytest.importorskip("opticmix_vision._core")
    from opticmix_vision import calibration
    intr = calibration.synthetic_equidistant((800, 800), 180.0)
    c = calibration.StereoCalibration(left=intr, right=intr, extrinsics=calibration.StereoExtrinsics(
        R=np.eye(3), t=np.array([-64.0, 0.0, 0.0])), device={"model": "Weevia S1", "serial": "S1-0042"})
    _scratch_calib.mkdir()
    (_scratch_calib / "S1-0042.json").write_text(c.to_json())
    cal = doctor.collect(synthetic=True)["calibration"]
    assert cal["files"] == ["S1-0042.json"] and cal["installed"] == ["S1-0042"]
    assert cal["check"] == "ok — S1-0042, 800x800 eyes"
    text = doctor.format_text(doctor.collect(synthetic=True)).splitlines()
    i = text.index("== Calibration ==")
    assert text[i + 3:i + 5] == ["check:           ok — S1-0042, 800x800 eyes",
                                 "details:         python -m opticmix_vision.calib show"]
    (_scratch_calib / "S1-0043.json").write_text(c.to_json())
    assert doctor.collect(synthetic=True)["calibration"]["check"].startswith("2 calibrations installed")


def test_a_read_that_fails_midway_keeps_the_frames_before_it():
    from opticmix_vision.synthetic import SyntheticStereoSource

    class Breaks(SyntheticStereoSource):
        def read(self):
            if self._i >= 5:
                raise RuntimeError("driver gone")
            return super().read()
    r = doctor.collect(open_camera=True, synthetic=True, seconds=5.0, _open=lambda: Breaks())
    m = r["camera"]["measure"]
    assert m["frames"] == 4 and m["read_error"] == "RuntimeError: driver gone" and r["camera"]["error"] is None
    assert "Reading frames failed: RuntimeError: driver gone" in r["findings"]
    assert "read error:      RuntimeError: driver gone" in doctor.format_text(r)


# ---------------------------------------------------------------- the hub's reply, byte by byte

def _ex_reply(*, port=2, vid=0x1D6B, pid=0x0200, bcd_device=0x0409, speed=2, status=1) -> bytes:
    """A USB_NODE_CONNECTION_INFORMATION_EX reply laid out by hand from usbioctl.h (packed, little-endian):
    ConnectionIndex @0, device descriptor @4 (bcdUSB @6, idVendor @12, idProduct @14, bcdDevice @16),
    CurrentConfigurationValue @22, Speed @23, DeviceIsHub @24, DeviceAddress @25, NumberOfOpenPipes @27,
    ConnectionStatus @31; the pipe list follows."""
    b = bytearray(512)
    b[0:4] = port.to_bytes(4, "little")
    b[4], b[5] = 18, 1                                     # bLength, bDescriptorType (device)
    b[6:8] = (0x0320).to_bytes(2, "little")
    b[12:14] = vid.to_bytes(2, "little")
    b[14:16] = pid.to_bytes(2, "little")
    b[16:18] = bcd_device.to_bytes(2, "little")
    b[22], b[23], b[24] = 1, speed, 0
    b[25:27] = (7).to_bytes(2, "little")
    b[27:31] = (3).to_bytes(4, "little")
    b[31:35] = status.to_bytes(4, "little")
    return bytes(b)


def test_parse_ex_reads_the_fields_at_their_offsets():
    assert doctor._parse_ex(_ex_reply()) == {"vid": 0x1D6B, "pid": 0x0200, "bcd_device": 0x0409, "speed": 2, "status": 1}
    assert doctor._parse_ex(_ex_reply(speed=3, status=0))["speed"] == 3


def test_parse_v2_reads_the_flags():
    b = (2).to_bytes(4, "little") + (16).to_bytes(4, "little") + (7).to_bytes(4, "little") + (0b1011).to_bytes(4, "little")
    assert doctor._parse_v2(b) == 0b1011


@pytest.mark.parametrize("speed,flags,want", [
    (0, None, ("Low", None)), (1, None, ("Full", None)), (2, None, ("High", None)), (3, None, ("Super", None)),
    (2, 0b0000, ("High", False)),            # a USB 2 device on any port
    (2, 0b0010, ("High", True)),             # SuperSpeed-capable, running at High (USB 2 port or cable)
    (2, 0b0011, ("Super", True)),            # EX said High, V2 says SuperSpeed: V2 wins
    (3, 0b1111, ("Super+", True)),
    (9, None, ("unknown", None)),
])
def test_link_name(speed, flags, want):
    assert doctor._link_name(speed, flags) == want


def test_a_hub_reply_for_another_device_or_an_empty_port_is_not_used(monkeypatch):
    def reply(**kw):
        return lambda h, p: (doctor._parse_ex(_ex_reply(**kw)), 0b10)
    rec = [WIN_RECORDS[2]]
    assert _windows(monkeypatch, records=rec, link=reply())["usb"]["devices"][0]["link_speed"] == "High"
    assert _windows(monkeypatch, records=rec, link=reply(vid=0x05E3))["usb"]["devices"][0]["link_speed"] == "unknown"
    assert _windows(monkeypatch, records=rec, link=reply(status=0))["usb"]["devices"][0]["link_speed"] == "unknown"


# ---------------------------------------------------------------- findings on the negotiated mode and frame rate

class _SlowYuy2:
    """Says YUY2 at 90 fps, delivers about 30 changing frames a second."""

    def __init__(self):
        import cv2
        self.cv2, self.i = cv2, 0

    def isOpened(self):  # noqa: N802
        return True

    def get(self, pid):
        c = self.cv2
        return {c.CAP_PROP_FRAME_WIDTH: 32, c.CAP_PROP_FRAME_HEIGHT: 8, c.CAP_PROP_FPS: 90.0,
                c.CAP_PROP_FOURCC: float(c.VideoWriter_fourcc(*"YUY2"))}.get(pid, 0.0)

    def set(self, pid, v):
        return True

    def read(self):
        import time
        time.sleep(1 / 30)
        self.i += 1
        return True, np.full((8, 32), self.i % 250, np.uint8)

    def release(self):
        pass


def test_yuy2_and_a_slow_stream_are_two_findings():
    cfg = DeviceConfig(layout="sbs", lr_order="first_is_right", backend="dshow", stall_frames=0)
    r = doctor.collect(open_camera=True, seconds=0.5, _platform="darwin",
                       _open=lambda: Device(cfg, capture=_SlowYuy2()).open())
    m = r["camera"]["measure"]
    assert r["camera"]["mode"]["fourcc"] == "YUY2" and m["fps"] < 40 and m["stalled"] == []
    got = [f for f in r["findings"] if "not MJPG" in f or "well below the negotiated" in f]
    assert len(got) == 2, r["findings"]
    assert got[0].startswith("The negotiated pixel format is YUY2, not MJPG.")
    assert "well below the negotiated 90 fps. Check the USB link speed" in got[1]


class _FourccCapture:
    """Reports a backend name and a CAP_PROP_FOURCC code; every frame is new."""

    def __init__(self, backend, code):
        import cv2
        self.cv2, self.backend, self.code = cv2, backend, code
        self.rng = np.random.default_rng(3)

    def isOpened(self):  # noqa: N802
        return True

    def getBackendName(self):  # noqa: N802
        return self.backend

    def get(self, pid):
        c = self.cv2
        return {c.CAP_PROP_FRAME_WIDTH: 16, c.CAP_PROP_FRAME_HEIGHT: 8, c.CAP_PROP_FPS: 90.0,
                c.CAP_PROP_FOURCC: float(self.code)}.get(pid, -1.0)

    def set(self, pid, v):
        return True

    def read(self):
        return True, self.rng.integers(0, 255, (8, 16), dtype=np.uint8)

    def release(self):
        pass


def _yuy2():
    import cv2
    return cv2.VideoWriter_fourcc(*"YUY2")


# OpenCV's MSMF backend reads back its output format code (RGB32 = 0x16, RGB24 = 0x14), not the camera's format:
# 904 frames in 10 s (90.27 fps) came with 0x16 on one PC with one camera (2026-11-25).
@pytest.mark.parametrize("backend,code,finding", [("MSMF", 0x16, False), ("MSMF", 0x14, False),
                                                  ("DSHOW", None, True), ("V4L2", None, True)])
def test_the_not_mjpg_finding_is_not_made_from_msmf_output_format_codes(backend, code, finding):
    code = _yuy2() if code is None else code
    cfg = DeviceConfig(layout="sbs", lr_order="first_is_left", stall_frames=0)
    r = doctor.collect(open_camera=True, seconds=0.2, _platform="darwin",
                       _open=lambda: Device(cfg, capture=_FourccCapture(backend, code)).open())
    got = [f for f in r["findings"] if "not MJPG" in f]
    if finding:
        assert got and got[0].startswith("The negotiated pixel format is YUY2, not MJPG."), r["findings"]
    else:
        assert got == [], r["findings"]


@pytest.mark.parametrize("code,shown", [(0x16, "0x00000016"), (0x14, "0x00000014")])
def test_the_msmf_format_line_is_readable_and_says_what_the_code_is(code, shown):
    cfg = DeviceConfig(layout="sbs", lr_order="first_is_left", stall_frames=0)
    r = doctor.collect(open_camera=True, seconds=0.1, _platform="darwin",
                       _open=lambda: Device(cfg, capture=_FourccCapture("MSMF", code)).open())
    text = doctor.format_text(r)
    assert f"negotiated mode: 16x8 {shown} 90 fps (MSMF)\n" in text
    assert ("format:          OpenCV's Media Foundation backend reports its output format code here, not the "
            "camera's format\n") in text
    assert "\x00" not in text


def test_the_dshow_format_line_is_unchanged():
    cfg = DeviceConfig(layout="sbs", lr_order="first_is_left", stall_frames=0)
    r = doctor.collect(open_camera=True, seconds=0.1, _platform="darwin",
                       _open=lambda: Device(cfg, capture=_FourccCapture("DSHOW", _yuy2())).open())
    text = doctor.format_text(r)
    assert "negotiated mode: 16x8 YUY2 90 fps (DSHOW)\n" in text and "format:   " not in text


class _RepeatingCapture:
    """Read i (from 0): i % 4 == 1 repeats the previous frame in both eyes, i % 4 == 3 in the left half only."""

    def __init__(self, backend):
        import cv2
        self.cv2, self.backend, self.i = cv2, backend, -1
        self.rng = np.random.default_rng(8)
        self.frame = self.rng.integers(0, 255, (8, 16), dtype=np.uint8)

    def isOpened(self):  # noqa: N802
        return True

    def getBackendName(self):  # noqa: N802
        return self.backend

    def get(self, pid):
        c = self.cv2
        return {c.CAP_PROP_FRAME_WIDTH: 16, c.CAP_PROP_FRAME_HEIGHT: 8, c.CAP_PROP_FPS: 90.0,
                c.CAP_PROP_FOURCC: float(c.VideoWriter_fourcc(*"MJPG"))}.get(pid, -1.0)

    def set(self, pid, v):
        return True

    def read(self):
        self.i += 1
        if self.i % 4 == 3:
            self.frame = np.hstack([self.frame[:, :8], self.rng.integers(0, 255, (8, 8), dtype=np.uint8)])
        elif self.i % 4 != 1:
            self.frame = self.rng.integers(0, 255, (8, 16), dtype=np.uint8)
        return True, self.frame.copy()

    def release(self):
        pass


@pytest.mark.parametrize("backend", ["MSMF", "DSHOW"])
def test_frames_repeated_in_both_eyes_are_counted_and_a_finding_through_msmf_only(backend):
    cfg = DeviceConfig(layout="sbs", lr_order="first_is_left", stall_frames=0)
    r = doctor.collect(open_camera=True, seconds=0.2, _platform="darwin",
                       _open=lambda: Device(cfg, capture=_RepeatingCapture(backend)).open())
    m = r["camera"]["measure"]
    reads = range(1, m["frames"] + 1)                       # read 0 is the first frame, compared with nothing
    assert m["both_identical_frames"] == m["right_identical_frames"] == sum(1 for i in reads if i % 4 == 1)
    assert m["left_identical_frames"] == sum(1 for i in reads if i % 4 in (1, 3)) and m["stalled"] == []
    got = [f for f in r["findings"] if f.startswith("Repeated images (MSMF)")]
    if backend == "MSMF":
        assert got == [f"Repeated images (MSMF): {m['both_identical_frames']} of {m['frames']} frames repeated the "
                       "previous image in both eyes through OpenCV's Media Foundation backend. Seen on one PC with "
                       "one camera (2026-11-24): 276 of 900 frames per eye through this path; not seen through "
                       "DirectShow (the default; 0 of 900) or the Rust crate (0 of 450); cause not established. "
                       "driver_ns does not show whether a new image arrived."]
    else:
        assert got == []
    assert f"both eyes {m['both_identical_frames']};" in doctor.format_text(r)


def test_msmf_repeats_at_5_percent_or_fewer_are_no_finding():
    r = {"sdk": {"core": {"present": True}, "distributions": {}, "opticmix_vision": "x"}, "synthetic": False,
         "weevia_s1": {"count": None, "name": "WN Dual Camera"}, "usb": {"devices": []},
         "camera": {"error": None, "opened": True, "control_writes": {"by_this_run": 0},
                    "mode": {"fourcc": "MJPG", "fps": 90.0, "backend_name": "MSMF"},
                    "measure": {"frames": 300, "fps": 90.0, "both_identical_frames": 15, "stalled": []}}}
    assert doctor._findings(r) == []
    r["camera"]["measure"]["both_identical_frames"] = 16
    assert [f[:40] for f in doctor._findings(r)] == ["Repeated images (MSMF): 16 of 300 frames"]


def test_backend_is_passed_to_the_weevia_s1_config(monkeypatch):
    seen = {}

    def config(**kw):
        seen.update(kw)
        raise RuntimeError("no camera in this test")
    monkeypatch.setattr(DeviceConfig, "weevia_s1", staticmethod(config))
    r = doctor.collect(open_camera=True, backend="msmf", _platform="darwin")
    assert seen == {"stall_frames": 0, "backend": "msmf"} and r["camera"]["opened"] is False


def test_a_synthetic_run_gets_no_mode_findings(no_hardware):
    pytest.importorskip("opticmix_vision._core")
    r = doctor.collect(open_camera=True, synthetic=True, seconds=0.1)
    assert r["camera"]["mode"]["fourcc"] == "SYNT" and r["findings"] == []


# ---------------------------------------------------------------- matching the name the same way everywhere

def test_a_linux_name_with_a_suffix_still_gets_the_usb_findings(tmp_path):
    cls = _fake_sysfs(tmp_path)
    (cls / "video0" / "name").write_text("WN Dual Camera: WN Dual Camera\n")
    r = doctor.collect(_platform="linux", _sysfs=str(cls))
    assert r["weevia_s1"]["count"] == 1
    assert any("USB link is High" in f for f in r["findings"])


def test_an_os_whose_devices_are_not_read_has_no_weevia_count():
    r = doctor.collect(_platform="darwin")
    assert r["weevia_s1"]["count"] is None and r["video_devices"]["source"] == "not read on darwin"
    assert not any("No Weevia" in f for f in r["findings"])


# ---------------------------------------------------------------- --output, the log, the explorer's line

@pytest.mark.parametrize("name,is_json", [("report.json", True), ("report.txt", False)])
def test_output_writes_utf8(no_hardware, capsys, tmp_path, name, is_json):
    out = tmp_path / name
    assert doctor.main(["--synthetic", "--output", str(out)]) == 0
    assert capsys.readouterr().out == f"Report written to {out}\n"
    raw = out.read_bytes()
    assert not raw.startswith((b"\xff\xfe", b"\xfe\xff", b"\xef\xbb\xbf"))      # UTF-8, no BOM, not UTF-16
    text = raw.decode("utf-8")
    if is_json:
        assert json.loads(text)["report"] == "opticmix_vision.doctor"
    else:
        assert text.startswith("opticmix_vision doctor (format 1") and "== Calibration ==" in text


def test_sdk_warnings_during_the_run_go_into_the_log(no_hardware):
    import logging
    import warnings
    from opticmix_vision.synthetic import SyntheticStereoSource

    class Noisy(SyntheticStereoSource):
        def read(self):
            if self._i == 2:
                logging.getLogger("opticmix_vision.controls").warning("write 3 of this process")
                warnings.warn("guard: many writes", RuntimeWarning)
            return super().read()
    r = doctor.collect(open_camera=True, synthetic=True, seconds=0.1, _open=lambda: Noisy())
    assert r["log"] == ["WARNING opticmix_vision.controls: write 3 of this process", "RuntimeWarning: guard: many writes"]
    text = doctor.format_text(r)
    assert "== Log (opticmix_vision warnings during this run) ==\n  WARNING opticmix_vision.controls: write 3" in text
    assert "opticmix_vision" not in [h.name for h in logging.getLogger("opticmix_vision").handlers if h.name]


def test_usb_summary_for_the_explorer(monkeypatch):
    asked = []

    def lists(backend="dshow"):
        asked.append(backend)
        return [DeviceInfo(0, "WN Dual Camera")]
    monkeypatch.setattr(devices, "list_devices", lists)
    real = doctor._hardware
    monkeypatch.setattr(doctor, "_hardware",
                        lambda *a, **k: real(False, WIN_RECORDS, lambda h, p: HUBS[(h, p)], None, "win32", **k))
    assert doctor._usb_summary() == "1d6b:0200 · REV 0409 · High · no serial"
    assert asked == ["dshow"]                              # the explorer's line lists no Media Foundation devices
    assert doctor._usb_summary(synthetic=True) == "synthetic · no serial"
    monkeypatch.setattr(doctor, "_hardware", lambda *a, **k: real(False, [], None, None, "win32", **k))
    assert doctor._usb_summary() == "USB: — · no serial"


def test_issue_form_and_readme_ask_for_the_doctor():
    """The bug report form makes the doctor's output a required field, and the README's troubleshooting starts with it."""
    sdk = Path(__file__).resolve().parents[2]
    form = sdk / ".github" / "ISSUE_TEMPLATE" / "bug_report.yml"
    if not (sdk / ".github").is_dir():
        pytest.skip("not a repository tree (no .github)")
    text = form.read_text(encoding="utf-8")
    field = text[text.index("id: doctor"):]
    field = field[:field.index("- type:")]                      # up to the next field
    assert "python -m opticmix_vision.doctor" in field and "required: true" in field
    assert "python -m opticmix_vision.calib show" in field
    readme = (sdk / "python" / "README.md").read_text(encoding="utf-8")
    first = readme.split("## When something goes wrong", 1)[1].strip().splitlines()[0]
    assert first.startswith("- **Opening an issue?**") and "python -m opticmix_vision.doctor" in first
