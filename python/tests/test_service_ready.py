"""What a customer hits first — each test pins one failure found by the 2026-09-25 service-readiness check."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from opticmix_vision import devices
from opticmix_vision.device import Device, DeviceConfig, DeviceError

from test_device import FakeCapture

SDK = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------- finding the camera without ffmpeg
def test_devices_are_listed_without_ffmpeg(monkeypatch):
    def no_ffmpeg():
        raise FileNotFoundError("ffmpeg")
    monkeypatch.setattr(devices, "_ffmpeg_devices", no_ffmpeg)
    monkeypatch.setattr(devices, "_native_devices", lambda: ["Example Front Webcam", "WN Dual Camera"])
    assert devices.list_devices() == [devices.DeviceInfo(0, "Example Front Webcam"),
                                      devices.DeviceInfo(1, "WN Dual Camera")]
    assert devices.resolve_by_name("WN Dual Camera").index == 1


def test_ffmpeg_still_wins_when_present(monkeypatch):
    monkeypatch.setattr(devices, "_ffmpeg_devices", lambda: '[dshow @ 0] "WN Dual Camera" (video)\n')
    monkeypatch.setattr(devices, "_native_devices", lambda: pytest.fail("native enumeration not needed"))
    assert devices.list_devices() == [devices.DeviceInfo(0, "WN Dual Camera")]


# ---------------------------------------------------------------- one place for the measured Weevia S1 settings
def test_weevia_s1_config_uses_the_measured_values_and_finds_the_camera_by_name(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(devices, "list_devices",
                        lambda **_: [devices.DeviceInfo(0, "Example Front Webcam"), devices.DeviceInfo(1, "WN Dual Camera")])
    cfg = DeviceConfig.weevia_s1()
    assert (cfg.device, cfg.layout, cfg.lr_order) == (1, "sbs", "first_is_right")
    assert (cfg.width, cfg.height, cfg.fps, cfg.fourcc) == (1600, 800, 90.0, "MJPG")
    assert cfg.backend == "dshow"
    assert DeviceConfig.weevia_s1(fps=60).fps == 60


def test_weevia_s1_config_says_what_it_saw_when_the_camera_is_missing(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(devices, "list_devices", lambda **_: [devices.DeviceInfo(0, "Example Front Webcam")])
    with pytest.raises(DeviceError, match="Example Front Webcam"):
        DeviceConfig.weevia_s1()


def _sysfs(tmp_path, nodes):
    for n, (name, index) in nodes.items():
        d = tmp_path / n
        d.mkdir()
        (d / "name").write_text(name + "\n")
        (d / "index").write_text(f"{index}\n")
    return str(tmp_path)


def test_weevia_s1_on_linux_finds_the_capture_node_by_name_and_uses_v4l2(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(devices, "SYSFS_V4L", _sysfs(tmp_path, {
        "video0": ("Integrated Webcam", 0), "video1": ("Integrated Webcam", 1),
        "video2": ("WN Dual Camera: WN Dual Camera", 0), "video3": ("WN Dual Camera: WN Dual Camera", 1)}))
    cfg = DeviceConfig.weevia_s1()
    assert (cfg.device, cfg.backend, cfg.lr_order) == ("/dev/video2", "v4l2", "first_is_right")
    assert DeviceConfig.weevia_s1(device="/dev/video5").backend == "v4l2"


def test_weevia_s1_on_linux_refuses_two_cameras(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(devices, "SYSFS_V4L", _sysfs(tmp_path, {
        "video0": ("WN Dual Camera", 0), "video1": ("WN Dual Camera", 1), "video2": ("WN Dual Camera", 0)}))
    with pytest.raises(DeviceError, match=r"not found\. 2 video capture nodes .*\['/dev/video0', '/dev/video2'\]"):
        DeviceConfig.weevia_s1()


# ---------------------------------------------------------------- a frozen eye is reported, not streamed
def _sbs(left_value: int | None, right_value: int | None, rng) -> np.ndarray:
    def half(v):
        return np.full((8, 8), v, np.uint8) if v is not None else rng.integers(0, 255, (8, 8), dtype=np.uint8)
    return np.hstack([half(left_value), half(right_value)])


def test_an_eye_that_stops_changing_raises_with_the_recovery_step():
    rng = np.random.default_rng(0)
    frames = [_sbs(150, None, rng) for _ in range(10)]              # left half constant, right half alive
    cfg = DeviceConfig(layout="sbs", lr_order="first_is_left", stall_frames=5)
    with Device(cfg, capture=FakeCapture(frames)) as cam:
        for _ in range(4):
            cam.read()
        with pytest.raises(DeviceError, match=r"(?si)left eye.*re-insert"):
            cam.read()


def test_live_eyes_never_trip_the_stall_check():
    rng = np.random.default_rng(1)
    cfg = DeviceConfig(layout="sbs", lr_order="first_is_left", stall_frames=3)
    with Device(cfg, capture=FakeCapture([_sbs(None, None, rng) for _ in range(20)])) as cam:
        for _ in range(20):
            cam.read()


def test_stall_check_can_be_switched_off():
    rng = np.random.default_rng(2)
    cfg = DeviceConfig(layout="sbs", lr_order="first_is_left", stall_frames=0)
    with Device(cfg, capture=FakeCapture([_sbs(150, 150, rng) for _ in range(20)])) as cam:
        for _ in range(20):
            cam.read()


# ---------------------------------------------------------------- documented commands run in the published package
def test_quickstart_help_runs():
    r = subprocess.run([sys.executable, str(SDK / "examples" / "quickstart.py"), "--help"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    assert r.returncode == 0, r.stderr


def test_explorer_prefers_the_weevia_s1_over_the_first_camera_listed():
    pytest.importorskip("tkinter")  # explorer.app imports tkinter; Python builds without it (Linux slim images) skip
    from opticmix_vision.explorer.app import preferred_device
    listed = [devices.DeviceInfo(0, "Example Front Webcam"), devices.DeviceInfo(1, "WN Dual Camera")]
    assert preferred_device(listed).name == "WN Dual Camera"
    assert preferred_device(listed[:1]).name == "Example Front Webcam"


def test_the_eye_named_left_is_the_physical_left_even_when_it_arrives_second():
    """first_is_right: the SECOND half is the left eye — a frozen second half must be reported as the left eye."""
    rng = np.random.default_rng(3)
    frames = [_sbs(None, 150, rng) for _ in range(6)]
    cfg = DeviceConfig(layout="sbs", lr_order="first_is_right", stall_frames=3)
    with Device(cfg, capture=FakeCapture(frames)) as cam:
        cam.read(); cam.read()
        with pytest.raises(DeviceError, match="left eye"):
            cam.read()


def test_a_frozen_right_eye_is_caught_too():
    rng = np.random.default_rng(4)
    frames = [_sbs(None, 150, rng) for _ in range(6)]
    cfg = DeviceConfig(layout="sbs", lr_order="first_is_left", stall_frames=3)
    with Device(cfg, capture=FakeCapture(frames)) as cam:
        cam.read(); cam.read()
        with pytest.raises(DeviceError, match="right eye"):
            cam.read()


def test_stall_frames_of_one_is_refused():
    with pytest.raises(ValueError):
        DeviceConfig(layout="sbs", lr_order="first_is_left", stall_frames=1)


def test_native_enumeration_is_importable():
    from opticmix_vision._ctrl_dshow import enumerate_video_devices
    assert callable(enumerate_video_devices)


def test_explorer_opens_the_weevia_s1_with_the_measured_eye_order(monkeypatch):
    try:   # not importorskip: a Python with _tkinter but no Tk library raises ImportError, which it does not skip
        from opticmix_vision.explorer import app as explorer
    except ImportError as exc:
        pytest.skip(f"no Tk: {exc}")
    seen = {}

    class _Dev:
        def __init__(self, cfg):
            seen["cfg"] = cfg

        def __enter__(self):
            return self

    monkeypatch.setattr(explorer, "Device", _Dev)
    fake = explorer.ExplorerApp.__new__(explorer.ExplorerApp)
    fake.synthetic = False
    fake.state = explorer.ExplorerState()
    fake.state.select_device(devices.DeviceInfo(1, "WN Dual Camera"))
    fake.state.set_modes([explorer.KNOWN_MODE])
    fake.state.select_mode(explorer.KNOWN_MODE)
    fake._open_source()
    assert seen["cfg"].lr_order == "first_is_right" and seen["cfg"].device == 1


def test_viewer_with_no_camera_arguments_opens_the_weevia_s1(monkeypatch):
    from opticmix_vision import viewer
    from opticmix_vision.device import DeviceConfig as DC
    called = {}

    def fake_weevia(**over):
        called["over"] = over
        raise DeviceError("stop here")

    monkeypatch.setattr(DC, "weevia_s1", staticmethod(fake_weevia))
    assert viewer.main(["--headless", "--frames", "1", "--lr-order", "first_is_left"]) == 2
    assert called["over"].get("lr_order") == "first_is_left"
