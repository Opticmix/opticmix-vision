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


# ---------------------------------------------------------------- repeated images (both eyes) are counted; MSMF warns
class _Backend(FakeCapture):
    def __init__(self, frames, name):
        super().__init__(frames)
        self.name = name

    def getBackendName(self):  # noqa: N802
        return self.name


def _with_repeats(n: int, repeat_at: set[int], seed: int = 5) -> list[np.ndarray]:
    """n frames; frame i (i >= 1) in repeat_at is a copy of frame i - 1, the rest are new images."""
    rng = np.random.default_rng(seed)
    frames: list[np.ndarray] = []
    for i in range(n):
        frames.append(frames[-1].copy() if i in repeat_at else _sbs(None, None, rng))
    return frames


def _read_all(cam, n):
    import warnings
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        for _ in range(n):
            cam.read()
    return [w for w in caught if "repeated the previous image" in str(w.message)]


def test_repeated_frames_counts_frames_whose_two_eyes_both_repeat():
    rng = np.random.default_rng(6)
    a = _sbs(None, None, rng)
    left_only = np.hstack([a[:, :8], rng.integers(0, 255, (8, 8), dtype=np.uint8)])   # left repeats, right new
    frames = [a, a.copy(), a.copy(), left_only, _sbs(None, None, rng), _sbs(None, None, rng)]
    frames.append(frames[-1].copy())
    cfg = DeviceConfig(layout="sbs", lr_order="first_is_left")
    cam = Device(cfg, capture=FakeCapture(frames + _with_repeats(4, {2})))
    with cam:
        assert cam.repeated_frames == 0
        for _ in range(7):
            cam.read()
        assert cam.repeated_frames == 3          # frames 1, 2 and 6; frame 3 repeats in one eye only
    cam.open()                                    # counted from this open(): the next frame has no frame before it
    for _ in range(4):
        cam.read()
    assert cam.repeated_frames == 1


def test_repeated_frames_is_none_when_the_stall_check_is_off():
    cfg = DeviceConfig(layout="sbs", lr_order="first_is_left", stall_frames=0)
    with Device(cfg, capture=_Backend(_with_repeats(400, set(range(1, 400, 2))), "MSMF")) as cam:
        assert _read_all(cam, 400) == [] and cam.repeated_frames is None


@pytest.mark.parametrize("repeats, warned", [(15, False), (16, True)])
def test_msmf_warns_once_when_more_than_5_percent_of_300_frames_repeat(repeats, warned):
    at = set(range(10, 10 + 18 * repeats, 18))                     # spread out: never a stall
    frames = _with_repeats(301, at) + _with_repeats(300, set(range(1, 300, 5)), seed=7)
    cfg = DeviceConfig(layout="sbs", lr_order="first_is_left")
    with Device(cfg, capture=_Backend(frames, "MSMF")) as cam:
        first = _read_all(cam, 301)                                 # 300 comparisons fill the window
        assert cam.repeated_frames == repeats
        assert len(first) == int(warned)
        if warned:
            msg = str(first[0].message)
            assert msg.startswith(f"{repeats} of the last 300 frames repeated the previous image in both eyes")
            assert "backend='dshow'" in msg and "Rust crate" in msg and "cause not established" in msg
            assert first[0].filename == __file__                    # points at the caller of read()
        later = _read_all(cam, 300)                                 # 60 more repeats in the next 300 frames
        assert len(first) + len(later) == 1                         # once per open(), whichever window passed
        assert cam.repeated_frames == repeats + 60


def test_the_same_repeats_through_directshow_give_no_warning():
    frames = _with_repeats(601, set(range(1, 601, 3)))
    cfg = DeviceConfig(layout="sbs", lr_order="first_is_left")
    with Device(cfg, capture=_Backend(frames, "DSHOW")) as cam:
        assert _read_all(cam, 601) == [] and cam.repeated_frames == 200


@pytest.mark.parametrize("frozen", [("left",), ("left", "right")])
def test_after_reopening_a_frozen_stream_raises_on_the_stall_frames_th_frame(frozen):
    """50 live frames whose last one already shows the frozen image, close, open again, frozen frames: the count
    starts afresh at open(), so read() raises on the 5th frozen frame after it — not earlier, and not never."""
    rng = np.random.default_rng(10)
    still = rng.integers(0, 255, (8, 8), dtype=np.uint8)

    def frame(stopped):
        halves = {e: (still if e in stopped else rng.integers(0, 255, (8, 8), dtype=np.uint8))
                  for e in ("left", "right")}
        return np.hstack([halves["left"], halves["right"]])          # first_is_left: first half is the left eye

    live = [frame(()) for _ in range(49)] + [frame(frozen)]          # the eye stopped on the last frame before close
    cfg = DeviceConfig(layout="sbs", lr_order="first_is_left", stall_frames=5)
    cam = Device(cfg, capture=FakeCapture(live + [frame(frozen) for _ in range(10)]))
    with cam:
        for _ in range(50):
            cam.read()
    with cam:
        for _ in range(4):
            cam.read()
        with pytest.raises(DeviceError, match=f"The {frozen[0]} eye has sent the same image for 5 frames"):
            cam.read()


def test_each_open_starts_a_new_window_and_may_warn_again():
    repeating = _with_repeats(301, set(range(1, 301, 3)))
    cfg = DeviceConfig(layout="sbs", lr_order="first_is_left")
    cam = Device(cfg, capture=_Backend(repeating + _with_repeats(301, set(), seed=9) + repeating, "MSMF"))
    with cam:
        assert len(_read_all(cam, 301)) == 1
    with cam:                                                       # no repeats: the last open's frames do not count
        assert _read_all(cam, 301) == [] and cam.repeated_frames == 0
    with cam:
        assert len(_read_all(cam, 301)) == 1


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
