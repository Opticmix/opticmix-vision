"""device — 스테레오 분리(합성 프레임), 설정 검증, 브링업 상수 로드, 가짜 VideoCapture 로 Device 전 경로."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from opticmix_vision.device import (
    LAYOUTS, Device, DeviceConfig, DeviceError, StereoFrame, split_stereo,
)


def _pair(h: int = 16, w: int = 24) -> tuple[np.ndarray, np.ndarray]:
    """서로 구분되는 두 반쪽. 왼쪽은 10~, 오른쪽은 200~ 대역."""
    L = (np.arange(h * w, dtype=np.uint8).reshape(h, w) % 50) + 10
    R = (np.arange(h * w, dtype=np.uint8).reshape(h, w) % 50) + 200
    return L, R


# ---------------------------------------------------------------- split_stereo

@pytest.mark.parametrize("layout", LAYOUTS)
def test_split_first_is_left_recovers_halves(layout: str):
    L, R = _pair()
    if layout == "sbs":
        frame = np.hstack([L, R])
    elif layout == "tb":
        frame = np.vstack([L, R])
    else:
        frame = np.stack([L, R], axis=2)
    left, right = split_stereo(frame, layout, "first_is_left")
    assert np.array_equal(left, L) and np.array_equal(right, R)
    assert left.shape == (16, 24)


@pytest.mark.parametrize("layout", LAYOUTS)
def test_split_first_is_right_swaps(layout: str):
    L, R = _pair()
    frame = {"sbs": np.hstack([L, R]), "tb": np.vstack([L, R]), "plane_pack": np.stack([L, R], axis=2)}[layout]
    left, right = split_stereo(frame, layout, "first_is_right")
    assert np.array_equal(left, R) and np.array_equal(right, L)


def test_split_plane_pack_accepts_flat_2d_buffer():
    L, R = _pair()
    flat = np.stack([L, R], axis=2).reshape(16, 48)          # (H, 2W) 인터리브 버퍼
    left, right = split_stereo(flat, "plane_pack", "first_is_left")
    assert np.array_equal(left, L) and np.array_equal(right, R)


def test_split_sbs_bgr_frame_is_converted_to_gray():
    L, R = _pair()
    g = np.hstack([L, R])
    bgr = np.repeat(g[:, :, None], 3, axis=2)                 # mono 를 3채널로 디코드한 경우
    left, right = split_stereo(bgr, "sbs", "first_is_left")
    assert left.ndim == 2 and right.ndim == 2
    assert np.array_equal(left, L) and np.array_equal(right, R)


def test_split_rejects_odd_dimension_and_bad_args():
    with pytest.raises(ValueError, match="sbs"):
        split_stereo(np.zeros((16, 25), np.uint8), "sbs", "first_is_left")
    with pytest.raises(ValueError, match="tb"):
        split_stereo(np.zeros((15, 24), np.uint8), "tb", "first_is_left")
    with pytest.raises(ValueError, match="plane_pack"):
        split_stereo(np.zeros((16, 24, 3), np.uint8), "plane_pack", "first_is_left")
    with pytest.raises(ValueError, match="layout"):
        split_stereo(np.zeros((16, 24), np.uint8), "quad", "first_is_left")
    with pytest.raises(ValueError, match="lr_order"):
        split_stereo(np.zeros((16, 24), np.uint8), "sbs", "left_first")


def test_split_returns_views_when_possible():
    L, R = _pair()
    frame = np.vstack([L, R])
    left, _ = split_stereo(frame, "tb", "first_is_left")
    assert np.shares_memory(left, frame)


# ---------------------------------------------------------------- DeviceConfig

def test_config_requires_layout_and_lr_order():
    with pytest.raises(TypeError):
        DeviceConfig()                                          # type: ignore[call-arg]
    with pytest.raises(ValueError, match="layout"):
        DeviceConfig(layout="side", lr_order="first_is_left")
    with pytest.raises(ValueError, match="lr_order"):
        DeviceConfig(layout="sbs", lr_order="unknown")
    with pytest.raises(ValueError, match="backend"):
        DeviceConfig(layout="sbs", lr_order="first_is_left", backend="gstreamer")
    with pytest.raises(ValueError, match="fourcc"):
        DeviceConfig(layout="sbs", lr_order="first_is_left", fourcc="MJPEG")
    with pytest.raises(ValueError, match="fps"):
        DeviceConfig(layout="sbs", lr_order="first_is_left", fps=0)


_CONSTANTS_PASS = """\
# a bring-up constants file, as DeviceConfig.from_bringup_constants reads it
# 2026-09-06T00:00:00+00:00 / overall PASS
BACKEND = 'opencv'
DEVICE = 'index 1 (dshow)'
RESOLUTION = (2560, 800)
FPS = 60.0
FOURCC = 'MJPG'
LAYOUT = 'sbs'
LR_ORDER = 'first_is_right'
HALF_SIZE = (1280, 800)
PTS_AVAILABLE = False
PTS_UNIT = 'unknown'
PTS_SOURCE = 'cv2 CAP_PROP_POS_MSEC (driver/host clock, NOT device PTS)'
FRAME_ID_SOURCE = 'counter'
ARRIVAL_JITTER_SIGMA_MS = 0.8123
MANUAL_EXPOSURE_OK = True
MANUAL_GAIN_OK = True
"""


def test_config_from_bringup_constants(tmp_path: Path):
    p = tmp_path / "oem_uvc_constants.py"
    p.write_text(_CONSTANTS_PASS, encoding="utf-8")
    cfg = DeviceConfig.from_bringup_constants(p)
    assert cfg.device == 1 and cfg.backend == "dshow"
    assert (cfg.width, cfg.height, cfg.fps, cfg.fourcc) == (2560, 800, 60.0, "MJPG")
    assert cfg.layout == "sbs" and cfg.lr_order == "first_is_right"
    over = DeviceConfig.from_bringup_constants(p, device=0, to_gray=False)
    assert over.device == 0 and over.to_gray is False


@pytest.mark.parametrize("patch, msg", [
    ("BACKEND = 'ffmpeg'", "OpenCV"),
    ("LAYOUT = 'single'", "LAYOUT"),
    ("LR_ORDER = 'undetermined'", "LR_ORDER"),
])
def test_config_from_constants_refuses_to_guess(tmp_path: Path, patch: str, msg: str):
    key = patch.split(" =")[0]
    text = "\n".join(patch if line.startswith(key + " =") else line for line in _CONSTANTS_PASS.splitlines())
    p = tmp_path / "c.py"
    p.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match=msg):
        DeviceConfig.from_bringup_constants(p)


# ---------------------------------------------------------------- Device (가짜 VideoCapture)

class FakeCapture:
    """cv2.VideoCapture 의 get/set/read/isOpened/release 만 흉내 낸다."""

    def __init__(self, frames: list[np.ndarray], *, opened: bool = True) -> None:
        import cv2
        self._frames = list(frames)
        self._opened = opened
        self.props: dict[int, float] = {cv2.CAP_PROP_FRAME_WIDTH: 0, cv2.CAP_PROP_FRAME_HEIGHT: 0,
                                        cv2.CAP_PROP_FPS: 0, cv2.CAP_PROP_FOURCC: 0}
        self.set_calls: list[tuple[int, float]] = []
        self.released = False

    def isOpened(self) -> bool:
        return self._opened

    def get(self, pid: int) -> float:
        return float(self.props.get(pid, 0.0))

    def set(self, pid: int, value: float) -> bool:
        self.set_calls.append((pid, value))
        self.props[pid] = value                              # 요청 그대로 협상됐다고 가정
        return True

    def read(self):
        if not self._frames:
            return False, None
        return True, self._frames.pop(0)

    def release(self) -> None:
        self.released = True

    def getBackendName(self) -> str:
        return "FAKE"


def test_device_reads_stereo_frames_and_reports_negotiated_mode():
    import cv2
    L, R = _pair()
    frames = [np.hstack([L, R]), np.hstack([L, R])]
    cap = FakeCapture(frames)
    cfg = DeviceConfig(layout="sbs", lr_order="first_is_left", width=48, height=16, fps=30.0, fourcc="MJPG",
                       extra_props={"CAP_PROP_AUTO_EXPOSURE": 0.25})
    with Device(cfg, capture=cap) as cam:
        assert cam.is_open
        assert cam.mode.width == 48 and cam.mode.height == 16 and cam.mode.fps == 30.0
        assert cam.mode.fourcc == "MJPG" and cam.mode.backend_name == "FAKE"
        assert (cv2.CAP_PROP_AUTO_EXPOSURE, 0.25) in cap.set_calls
        f0 = cam.read()
        f1 = cam.read()
        assert isinstance(f0, StereoFrame)
        assert np.array_equal(f0.left, L) and np.array_equal(f0.right, R)
        assert f0.size == (24, 16)
        assert (f0.index, f1.index) == (0, 1)
        assert f1.host_ns >= f0.host_ns > 0
        with pytest.raises(DeviceError, match="read failed"):
            cam.read()                                       # 프레임 소진 → 조용한 실패 금지
    assert cap.released is False                             # 주입한 capture 는 SDK 가 닫지 않는다
    assert cam.is_open is False


class _V4L2Capture(FakeCapture):
    def getBackendName(self) -> str:
        return "V4L2"


class _TimedCapture(FakeCapture):
    """CAP_PROP_POS_MSEC = the given value of the frame last read, as OpenCV's MSMF/V4L2 backends give it."""

    def __init__(self, backend: str, msec: list[float], n: int | None = None) -> None:
        L, R = _pair()
        super().__init__([np.hstack([L + i, R]) for i in range(n or len(msec))])
        self._backend, self._msec, self.pos_reads = backend, list(msec), 0

    def read(self):
        ok, img = super().read()
        if ok:
            import cv2
            self.props[cv2.CAP_PROP_POS_MSEC] = self._msec.pop(0)
        return ok, img

    def get(self, pid: int) -> float:
        import cv2
        self.pos_reads += pid == cv2.CAP_PROP_POS_MSEC
        return super().get(pid)

    def getBackendName(self) -> str:
        return self._backend


def _timed_frames(backend: str, msec: list[float], device: Any = 0) -> tuple[list[StereoFrame], _TimedCapture]:
    cap = _TimedCapture(backend, msec)
    cfg = DeviceConfig(layout="sbs", lr_order="first_is_left", device=device)
    with Device(cfg, capture=cap) as cam:
        return [cam.read() for _ in msec], cap


def test_msmf_driver_time_is_the_sample_time_in_whole_100_ns_units():
    # OpenCV MSMF gives sample time / 1e4 as ms: 12345678 and 12456789 units of 100 ns
    frames, _ = _timed_frames("MSMF", [1234.5678, 1245.6789])
    assert [f.driver_ns for f in frames] == [1_234_567_800, 1_245_678_900]
    assert frames[1].driver_ns - frames[0].driver_ns == 11_111_100            # one 90 fps interval, exact
    assert {f.driver_clock for f in frames} == {"media_foundation"}
    assert {f.driver_sequence for f in frames} == {None}                      # OpenCV gives no sequence number
    assert [f.index for f in frames] == [0, 1]


def test_v4l2_driver_time_is_the_buffer_time_in_whole_microseconds():
    # OpenCV V4L2: 1000 * tv_sec + tv_usec / 1000 — 86400.000123 s and 86400.011234 s of CLOCK_MONOTONIC (if it is)
    frames, _ = _timed_frames("V4L2", [86_400_000.123, 86_400_011.234])
    assert [f.driver_ns for f in frames] == [86_400_000_123_000, 86_400_011_234_000]
    assert {f.driver_clock for f in frames} == {"v4l2"}


def test_directshow_and_other_backends_give_no_driver_time_and_are_not_asked():
    for backend in ("DSHOW", "FAKE", ""):
        frames, cap = _timed_frames(backend, [5.0, 6.0])
        assert [(f.driver_ns, f.driver_clock, f.driver_sequence) for f in frames] == [(None, None, None)] * 2
        assert cap.pos_reads == 0, backend


def test_a_negative_driver_time_is_none_not_a_number():
    frames, _ = _timed_frames("MSMF", [-1.0, 3.0])
    assert [(f.driver_ns, f.driver_clock) for f in frames] == [(None, None), (3_000_000, "media_foundation")]


def test_a_recording_has_no_driver_time(tmp_path):
    # a file opened through the MSMF backend: its POS_MSEC is the position in the file, not a driver time
    video = tmp_path / "rec.avi"
    video.write_bytes(b"not read here")
    frames, cap = _timed_frames("MSMF", [0.0, 11.1], device=str(video))
    assert [f.driver_ns for f in frames] == [None, None] and cap.pos_reads == 0


def test_playing_a_recording_made_through_msmf_gives_no_driver_time(tmp_path):
    # Playback reports the backend the recording was made with; its POS_MSEC is a file position, not a driver time
    import json

    from opticmix_vision.recording import Recorder
    from opticmix_vision.synthetic import SyntheticStereoSource
    src = SyntheticStereoSource(eye_size=(64, 32), fps=90.0)
    path = tmp_path / "rec.avi"
    with Recorder(path, src) as rec:
        for _ in range(3):
            rec.write(src.read())
    info_path = path.with_suffix(".json")
    info = json.loads(info_path.read_text(encoding="utf-8"))
    info["mode"]["backend_name"] = "MSMF"
    info_path.write_text(json.dumps(info), encoding="utf-8")
    with Device(DeviceConfig.recording(path)) as cam:
        assert cam.mode.backend_name == "MSMF"
        frames = [cam.read() for _ in range(3)]
    assert [(f.driver_ns, f.driver_clock) for f in frames] == [(None, None)] * 3


def _windows_lists(monkeypatch, mf: list[str], dshow: list[str]) -> None:
    """Fake Media Foundation and DirectShow lists on 'Windows' — nothing is enumerated."""
    import sys

    from opticmix_vision import devices
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(devices, "_mf_devices", lambda: list(mf))
    monkeypatch.setattr(devices, "_ffmpeg_devices", lambda: "".join(f'[dshow] "{n}" (video)\n' for n in dshow))


def test_weevia_s1_with_msmf_takes_its_index_from_the_media_foundation_list(monkeypatch):
    # the two lists in different orders: the MSMF index must come from Media Foundation's list, not DirectShow's
    _windows_lists(monkeypatch, mf=["Example Webcam", "Example IR Camera", "WN Dual Camera"],
                   dshow=["WN Dual Camera", "Example Webcam"])
    cfg = DeviceConfig.weevia_s1(backend="msmf")
    assert (cfg.backend, cfg.device, cfg.lr_order) == ("msmf", 2, "first_is_right")
    assert DeviceConfig.weevia_s1().device == 0                       # dshow (the default) still uses its own list
    assert DeviceConfig.weevia_s1(backend="msmf", device=1).device == 1   # device= skips the lookup


def test_weevia_s1_with_msmf_not_in_the_list_says_what_was_found(monkeypatch):
    _windows_lists(monkeypatch, mf=["Example Webcam"], dshow=["WN Dual Camera"])
    with pytest.raises(DeviceError, match=r"Weevia S1 \('WN Dual Camera'\) not found\. .*\['Example Webcam'\]"):
        DeviceConfig.weevia_s1(backend="msmf")


def test_weevia_s1_with_msmf_refuses_two_cameras(monkeypatch):
    _windows_lists(monkeypatch, mf=["WN Dual Camera", "Example Webcam", "WN Dual Camera"], dshow=["WN Dual Camera"])
    with pytest.raises(DeviceError, match=r"2 cameras match 'WN Dual Camera' .*Unplug all but one"):
        DeviceConfig.weevia_s1(backend="msmf")


def test_msmf_controls_know_the_model_from_the_media_foundation_list(monkeypatch):
    # the OpenCV controls fallback cannot ask the device; presets refuse a preset whose model differs
    _windows_lists(monkeypatch, mf=["Example Webcam", "WN Dual Camera"], dshow=["Example Webcam"])
    cfg = DeviceConfig(layout="sbs", lr_order="first_is_right", device=1, backend="msmf")
    assert Device(cfg, capture=FakeCapture([np.zeros((4, 8), np.uint8)]))._usb_model() == "WN Dual Camera"
    assert Device(cfg.replace(device=0), capture=FakeCapture([]))._usb_model() is None


def test_any_refuses_a_capture_another_backend_opened_on_windows(monkeypatch):
    # CAP_ANY falls through to e.g. OBSENSOR with the same index when MSMF and DSHOW fail: not the camera meant
    import sys

    import cv2
    opened: list[_TimedCapture] = []

    def fake_videocapture(_device, _api):
        cap = _TimedCapture(backend, [0.0])
        opened.append(cap)
        return cap
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(cv2, "VideoCapture", fake_videocapture)
    cfg = DeviceConfig(layout="sbs", lr_order="first_is_right", device=1)          # backend="any"
    backend = "OBSENSOR"
    with pytest.raises(DeviceError, match=r"backend='any' opened device 1 through OBSENSOR.*backend='msmf'"):
        Device(cfg).open()
    assert opened[-1].released and opened[-1].set_calls == []    # let go at once, nothing negotiated on it
    for backend in ("MSMF", "DSHOW"):
        with Device(cfg) as cam:
            assert cam.mode.backend_name == backend


LOC_A = "PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(1)"
LOC_B = "PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(4)#USB(2)"


def _two_weevia_on_windows(monkeypatch, mf, dshow) -> None:
    """Made-up Media Foundation and DirectShow lists of (name, USB location) on 'Windows' — nothing is enumerated."""
    import sys

    from opticmix_vision import devices

    def no_ffmpeg():
        raise FileNotFoundError("ffmpeg")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(devices, "_mf_devices", lambda: list(mf))
    monkeypatch.setattr(devices, "_ffmpeg_devices", no_ffmpeg)
    monkeypatch.setattr(devices, "_native_devices", lambda: list(dshow))


def test_weevia_s1_opens_one_of_two_cameras_by_usb_location(monkeypatch):
    # the two lists in different orders: each backend's index comes from its own list, at the same location
    _two_weevia_on_windows(monkeypatch, mf=[("WN Dual Camera", LOC_B), ("Example Webcam", None), ("WN Dual Camera", LOC_A)],
                           dshow=[("WN Dual Camera", LOC_A), ("WN Dual Camera", LOC_B)])
    assert DeviceConfig.weevia_s1(usb_location=LOC_B).device == 1                     # DirectShow (the default)
    assert DeviceConfig.weevia_s1(usb_location=LOC_B, backend="msmf").device == 0
    assert DeviceConfig.weevia_s1(usb_location=LOC_A, backend="msmf").device == 2
    with pytest.raises(DeviceError, match=r"2 cameras match 'WN Dual Camera' .*pick one by USB location"):
        DeviceConfig.weevia_s1()
    with pytest.raises(DeviceError, match=r"No camera matches 'WN Dual Camera' at USB location 'PCIROOT\(0\)#USB\(9\)'"):
        DeviceConfig.weevia_s1(usb_location="PCIROOT(0)#USB(9)")
    with pytest.raises(DeviceError, match=r"index 0 in the Media Foundation list but 1 in the DirectShow list"):
        DeviceConfig.weevia_s1(usb_location=LOC_B, backend="any")
    with pytest.raises(ValueError, match="device= or usb_location="):
        DeviceConfig.weevia_s1(usb_location=LOC_A, device=0)
    for backend in ("dshow", "msmf"):
        with pytest.raises(ValueError, match=r"^usb_location is empty"):
            DeviceConfig.weevia_s1(usb_location=" ", backend=backend)


def test_weevia_s1_opens_one_of_two_cameras_by_usb_location_on_linux(monkeypatch, tmp_path):
    import sys

    from opticmix_vision import devices
    from test_devices import TWO_ON_LINUX, _sysfs_usb
    monkeypatch.setattr(devices, "SYSFS_V4L", _sysfs_usb(tmp_path, TWO_ON_LINUX))     # before 'linux': junctions
    monkeypatch.setattr(sys, "platform", "linux")
    cfg = DeviceConfig.weevia_s1(usb_location="1-4.2")
    assert (cfg.device, cfg.backend) == ("/dev/video2", "v4l2")
    with pytest.raises(ValueError, match=r"^usb_location is empty"):
        DeviceConfig.weevia_s1(usb_location="")


def test_weevia_s1_with_any_needs_the_same_index_in_both_lists(monkeypatch):
    # OpenCV's CAP_ANY tries MSMF, then DSHOW with the same index: only an index valid for both is safe
    _windows_lists(monkeypatch, mf=["Example Webcam", "WN Dual Camera"], dshow=["Example Webcam", "WN Dual Camera"])
    assert DeviceConfig.weevia_s1(backend="any").device == 1
    _windows_lists(monkeypatch, mf=["Example Webcam", "WN Dual Camera"], dshow=["WN Dual Camera", "Example Webcam"])
    with pytest.raises(DeviceError, match=r"index 1 in the Media Foundation list but 0 in the DirectShow list"):
        DeviceConfig.weevia_s1(backend="any")


def test_stereo_frame_still_takes_the_four_original_fields():
    L, R = _pair()
    f = StereoFrame(L, R, 5, 0)
    assert (f.driver_ns, f.driver_clock, f.driver_sequence) == (None, None, None)


def _format_order(backend, cap_cls=FakeCapture):
    import cv2
    cap = cap_cls([])
    cfg = DeviceConfig(layout="sbs", lr_order="first_is_right", backend=backend,
                       width=1600, height=800, fps=90.0, fourcc="MJPG")
    Device(cfg, capture=cap).open()
    names = {cv2.CAP_PROP_FRAME_WIDTH: "W", cv2.CAP_PROP_FRAME_HEIGHT: "H",
             cv2.CAP_PROP_FPS: "FPS", cv2.CAP_PROP_FOURCC: "FOURCC"}
    return [names[p] for p, _ in cap.set_calls if p in names]


def test_v4l2_sets_fourcc_before_fps():
    # OpenCV CAP_V4L2: FPS before FOURCC committed 222222 (45 fps), FOURCC before FPS committed 111111 (90 fps),
    # 2/2 each on Weevia S1.
    assert _format_order("v4l2") == ["W", "H", "FOURCC", "FPS"]


def test_any_backend_that_opens_v4l2_sets_fourcc_before_fps():
    assert _format_order("any", _V4L2Capture) == ["W", "H", "FOURCC", "FPS"]


def test_dshow_keeps_fourcc_last():
    # DirectShow: fourcc last measured 90.4 fps, fourcc first 9.3 fps. FOURCC→FPS untested there — keep as is.
    assert _format_order("dshow") == ["W", "H", "FPS", "FOURCC"]


def test_device_plane_pack_disables_rgb_conversion():
    import cv2
    L, R = _pair()
    cap = FakeCapture([np.stack([L, R], axis=2)])
    with Device(DeviceConfig(layout="plane_pack", lr_order="first_is_left"), capture=cap) as cam:
        assert (cv2.CAP_PROP_CONVERT_RGB, 0.0) in cap.set_calls
        f = cam.read()
        assert np.array_equal(f.left, L) and np.array_equal(f.right, R)


def test_device_open_failure_raises():
    cap = FakeCapture([], opened=False)
    with pytest.raises(DeviceError, match="failed to open"):
        Device(DeviceConfig(layout="sbs", lr_order="first_is_left"), capture=cap).open()


def test_device_prop_passthrough_by_name_and_id():
    import cv2
    cap = FakeCapture([])
    cam = Device(DeviceConfig(layout="sbs", lr_order="first_is_left"), capture=cap).open()
    assert cam.set_prop("CAP_PROP_GAIN", 7) is True
    assert cam.get_prop(cv2.CAP_PROP_GAIN) == 7.0
    with pytest.raises(DeviceError):
        Device(DeviceConfig(layout="sbs", lr_order="first_is_left")).get_prop("CAP_PROP_GAIN")
