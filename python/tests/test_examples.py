"""The examples run to the end with --synthetic: no camera, no window. Each runs in its own process."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def run(tmp_path: Path, script: str, *args: str) -> str:
    r = subprocess.run([sys.executable, str(EXAMPLES / script), *args], cwd=tmp_path, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=120)
    assert r.returncode == 0, f"{script} exited {r.returncode}\n{r.stdout}\n{r.stderr}"
    return r.stdout


@pytest.mark.parametrize("script, args", [
    ("live_preview.py", ["--headless", "--frames", "30"]),
    ("measure_fps.py", ["--frames", "30"]),
    ("list_controls.py", []),
    ("set_controls_once.py", []),
    ("led_level.py", ["--frames", "3", "--settle", "2"]),
])
def test_runs_with_synthetic(tmp_path, script, args):
    run(tmp_path, script, "--synthetic", *args)


def test_measure_fps_says_when_there_are_no_driver_times(tmp_path):
    out = run(tmp_path, "measure_fps.py", "--synthetic", "--frames", "5")
    assert "driver time stamps: none from this capture backend (synthetic)" in out


def test_measure_fps_prints_the_driver_time_figures(monkeypatch, capsys):
    """The lines a camera run with driver times would print, on made-up times (the example's main, in this process)."""
    import importlib.util

    from opticmix_vision import NegotiatedMode, StereoFrame
    spec = importlib.util.spec_from_file_location("measure_fps_example", EXAMPLES / "measure_fps.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    host_ms = [0.0, 11.2, 22.2, 33.5, 44.4, 55.6]             # the frame read first is not timed
    driver_ms = [0.0, 11.1, 22.2, 33.3, 44.4, 55.5]

    class Source:
        mode = NegotiatedMode(1600, 800, 90.0, "MJPG", "MSMF")

        def __init__(self):
            self.i = 0

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def read(self):
            i, self.i = self.i, self.i + 1
            eye = np.full((4, 4), i, np.uint8)
            return StereoFrame(eye, eye, round(host_ms[i] * 1e6), i, driver_ns=round(driver_ms[i] * 1e6),
                               driver_clock="media_foundation")

    monkeypatch.setattr(mod, "open_camera", lambda *_a, **_k: Source())
    monkeypatch.setattr(sys, "argv", ["measure_fps.py", "--frames", "5"])
    assert mod.main() == 0
    out = capsys.readouterr().out
    assert "driver time stamps (media_foundation): interval ms: mean 11.10  min 11.10  max 11.10" in out
    assert "driver time stamps: intervals over 1.5x the median: 0" in out
    assert "driver time stamps: first timed frame at 0.011100 s on that clock" in out
    # host - driver over the timed frames: 0.1, 0, 0.2, 0, 0.1 ms
    assert "host_ns - driver_ns, above its smallest value, ms: median 0.10  95th percentile 0.18  max 0.20" in out


def test_measure_fps_prints_device_repeated_frames_beside_its_own_count(monkeypatch, capsys):
    import importlib.util

    from opticmix_vision import Device, DeviceConfig
    from test_device import FakeCapture
    spec = importlib.util.spec_from_file_location("measure_fps_example", EXAMPLES / "measure_fps.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    rng = np.random.default_rng(11)
    frames = []
    for i in range(7):                                        # read 0 untimed; 2 and 5 repeat both eyes, 3 the left
        if i in (2, 5):
            frames.append(frames[-1].copy())
        elif i == 3:
            frames.append(np.hstack([frames[-1][:, :4], rng.integers(0, 255, (4, 4), dtype=np.uint8)]))
        else:
            frames.append(rng.integers(0, 255, (4, 8), dtype=np.uint8))
    cfg = DeviceConfig(layout="sbs", lr_order="first_is_left")
    monkeypatch.setattr(mod, "open_camera", lambda *_a, **_k: Device(cfg, capture=FakeCapture(frames)).open())
    monkeypatch.setattr(sys, "argv", ["measure_fps.py", "--frames", "6"])
    assert mod.main() == 0
    out = capsys.readouterr().out
    assert "frames identical to the previous one: left 3, right 2\n" in out
    assert "frames identical in both eyes (Device.repeated_frames): 2\n" in out


def test_measure_fps_prints_no_device_count_for_generated_frames(tmp_path):
    out = run(tmp_path, "measure_fps.py", "--synthetic", "--frames", "5")
    assert "frames identical to the previous one:" in out and "Device.repeated_frames" not in out


def test_measure_fps_points_directshow_users_to_msmf(monkeypatch, capsys):
    import importlib.util

    from opticmix_vision import NegotiatedMode, StereoFrame
    spec = importlib.util.spec_from_file_location("measure_fps_example", EXAMPLES / "measure_fps.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    class Source:                                             # DirectShow: OpenCV gives no driver time
        i = 0
        closed = False

        @property
        def mode(self):                                       # like Device: no mode once closed
            if self.closed:
                raise mod.DeviceError("no negotiated mode before open()")
            return NegotiatedMode(1600, 800, 90.0, "MJPG", "DSHOW")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.closed = True

        def read(self):
            self.i += 1
            eye = np.full((4, 4), self.i, np.uint8)
            return StereoFrame(eye, eye, self.i * 11_111_111, self.i)

    monkeypatch.setattr(mod, "open_camera", lambda *_a, **_k: Source())
    monkeypatch.setattr(sys, "argv", ["measure_fps.py", "--frames", "3"])
    assert mod.main() == 0
    assert ("driver time stamps: none from this capture backend (DSHOW) — on Windows use --backend msmf\n"
            in capsys.readouterr().out)


def test_measure_fps_backend_msmf_needs_no_device(monkeypatch):
    # --backend msmf alone: the Weevia S1's index comes from the (fake) Media Foundation list; nothing is opened
    import importlib.util

    from opticmix_vision import devices
    spec = importlib.util.spec_from_file_location("measure_fps_example", EXAMPLES / "measure_fps.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(devices, "_mf_devices", lambda: ["Example Webcam", "WN Dual Camera"])
    monkeypatch.setattr(devices, "_ffmpeg_devices", lambda: '[dshow] "WN Dual Camera" (video)\n')

    class NotOpened:
        def __init__(self, config):
            self.config = config

        def open(self):
            return self
    monkeypatch.setattr(mod, "Device", NotOpened)
    cfg = mod.open_camera(False, "msmf", None).config
    assert (cfg.backend, cfg.device) == ("msmf", 1)


def _example(name: str):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py") + "_example", EXAMPLES / name)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("script, args", [("record_video.py", (None,)), ("save_stereo_pairs.py", ())])
def test_backend_msmf_finds_the_weevia_by_name_and_device_names_the_index(monkeypatch, script, args):
    # --backend msmf alone: the index comes from the (fake) Media Foundation list; nothing is opened
    from opticmix_vision import devices
    mod = _example(script)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(devices, "_mf_devices", lambda: ["Example Webcam", "WN Dual Camera"])
    monkeypatch.setattr(devices, "_ffmpeg_devices", lambda: '[dshow] "WN Dual Camera" (video)\n')

    class NotOpened:
        def __init__(self, config):
            self.config = config

        def open(self):
            return self
    monkeypatch.setattr(mod, "Device", NotOpened)
    cfg = mod.open_camera(False, *args, "msmf", None).config
    assert (cfg.backend, cfg.device) == ("msmf", 1)
    cfg = mod.open_camera(False, *args, "msmf", 3).config
    assert (cfg.backend, cfg.device) == ("msmf", 3)
    cfg = mod.open_camera(False, *args, None, None).config
    assert (cfg.backend, cfg.device) == ("dshow", 0)             # the default: DirectShow, found by name


class _MsmfCapture:
    """A VideoCapture stand-in for OpenCV's MSMF backend: 1600x800 side-by-side frames, each a new image, and
    CAP_PROP_POS_MSEC = a made-up Media Foundation sample time (100 ns units) / 1e4, 111,111 units apart."""

    START = 3_525_001_234_567                                       # 100 ns units

    def __init__(self):
        self.n = 0

    def isOpened(self):
        return True

    def set(self, pid, value):
        return True

    def getBackendName(self):
        return "MSMF"

    def read(self):
        self.n += 1
        return True, np.full((800, 1600), self.n % 251, np.uint8)

    def get(self, pid):
        import cv2
        return {cv2.CAP_PROP_POS_MSEC: (self.START + (self.n - 1) * 111_111) / 1e4, cv2.CAP_PROP_FRAME_WIDTH: 1600.0,
                cv2.CAP_PROP_FRAME_HEIGHT: 800.0, cv2.CAP_PROP_FPS: 90.0}.get(pid, 0.0)

    def release(self):
        pass


def _run_through_fake_msmf(monkeypatch, mod, argv: list[str]) -> list:
    """The example's main with --backend msmf --device 0, Device given the fake MSMF capture; nothing is opened."""
    from opticmix_vision import Device, devices
    configs = []

    def fake_device(config):
        configs.append(config)
        return Device(config, capture=_MsmfCapture())
    monkeypatch.setattr(mod, "Device", fake_device)
    monkeypatch.setattr(devices, "_mf_devices", lambda: ["WN Dual Camera"])      # the name for capture_info
    monkeypatch.setattr(sys, "argv", [mod.__file__, "--backend", "msmf", "--device", "0", *argv])
    assert mod.main() == 0
    assert [(c.backend, c.device) for c in configs] == [("msmf", 0)]
    return configs


def _driver_cells(rows: list[list[str]], first: int) -> tuple[list[int], set[str]]:
    """The driver_ns column as ints (none empty) and the clock names, from csv rows without the header."""
    assert rows and all(r[first] for r in rows)
    return [int(r[first]) for r in rows], {r[first + 1] for r in rows}


def test_record_video_backend_msmf_fills_driver_ns(tmp_path, monkeypatch):
    mod = _example("record_video.py")
    _run_through_fake_msmf(monkeypatch, mod, ["--seconds", "0.05", "--out", str(tmp_path / "rec.avi")])
    rows = list(csv.reader(open(tmp_path / "rec.csv", encoding="utf-8", newline="")))
    assert rows[0] == ["frame", "host_ns", "driver_ns", "driver_clock"]
    ns, clocks = _driver_cells(rows[1:], 2)
    assert len(ns) >= 2 and clocks == {"media_foundation"}
    assert set(np.diff(ns)) == {11_111_100}                         # whole 100 ns units, frame after frame
    assert (ns[0] - _MsmfCapture.START * 100) % 11_111_100 == 0
    info = json.loads((tmp_path / "rec.json").read_text(encoding="utf-8"))
    assert info["camera"]["backend"] == "msmf" and info["mode"]["backend_name"] == "MSMF"


def test_save_stereo_pairs_backend_msmf_fills_driver_ns(tmp_path, monkeypatch):
    mod = _example("save_stereo_pairs.py")
    _run_through_fake_msmf(monkeypatch, mod, ["--headless", "--every", "2", "--count", "3", "--out", str(tmp_path)])
    (run_dir,) = tmp_path.iterdir()
    rows = list(csv.reader(open(run_dir / "pairs.csv", encoding="utf-8", newline="")))
    ns, clocks = _driver_cells(rows[1:], 2)
    assert [r[0] for r in rows[1:]] == ["0", "2", "4"] and clocks == {"media_foundation"}
    assert ns == [_MsmfCapture.START * 100 + i * 11_111_100 for i in (0, 2, 4)]


def test_list_controls_writes_nothing(tmp_path):
    assert "control writes by this process: 0" in run(tmp_path, "list_controls.py", "--synthetic")


def test_save_stereo_pairs_writes_pngs_and_csv(tmp_path):
    run(tmp_path, "save_stereo_pairs.py", "--synthetic", "--headless", "--every", "5", "--count", "3")
    (run_dir,) = (tmp_path / "captures").iterdir()
    rows = list(csv.DictReader(open(run_dir / "pairs.csv", encoding="utf-8")))
    assert len(rows) == 3
    assert list(rows[0]) == ["index", "host_ns", "driver_ns", "driver_clock", "left", "right"]
    for row in rows:
        assert (run_dir / row["left"]).is_file() and (run_dir / row["right"]).is_file()
        assert (row["driver_ns"], row["driver_clock"]) == ("", "")       # generated frames have no driver time
    info = json.loads((run_dir / "capture_info.json").read_text(encoding="utf-8"))
    assert info["mode"]["fourcc"] == "SYNT" and info["source"] == "synthetic" and info["controls"]


def test_save_stereo_pairs_writes_each_pairs_driver_time(tmp_path, monkeypatch):
    """Made-up Media Foundation times, one frame without (the example's main, in this process)."""
    import importlib.util

    from opticmix_vision import NegotiatedMode, StereoFrame
    spec = importlib.util.spec_from_file_location("save_stereo_pairs_example", EXAMPLES / "save_stereo_pairs.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    driver = [100_000_123_456_700, None, 100_000_145_678_900]

    class Source:
        mode = NegotiatedMode(8, 4, 90.0, "RGB3", "MSMF")
        i = 0

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def read(self):
            i, self.i = self.i, self.i + 1
            eye = np.full((4, 4), i, np.uint8)
            d = driver[i]
            return StereoFrame(eye, eye, 10**9 + i, i, driver_ns=d, driver_clock=None if d is None else "media_foundation")

    monkeypatch.setattr(mod, "open_camera", lambda *_a, **_k: Source())
    monkeypatch.setattr(sys, "argv", ["save_stereo_pairs.py", "--headless", "--every", "1", "--count", "3",
                                      "--out", str(tmp_path)])
    assert mod.main() == 0
    (run_dir,) = tmp_path.iterdir()
    rows = list(csv.reader(open(run_dir / "pairs.csv", encoding="utf-8", newline="")))
    assert [r[:4] for r in rows] == [["index", "host_ns", "driver_ns", "driver_clock"],
                                     ["0", "1000000000", "100000123456700", "media_foundation"],
                                     ["1", "1000000001", "", ""],
                                     ["2", "1000000002", "100000145678900", "media_foundation"]]
    assert [r[4:] for r in rows[1:]] == [[f"left_{i:06d}.png", f"right_{i:06d}.png"] for i in range(3)]


def test_record_video_writes_a_readable_file(tmp_path):
    import cv2
    run(tmp_path, "record_video.py", "--synthetic", "--seconds", "0.3")
    cap = cv2.VideoCapture(str(tmp_path / "recording.avi"))
    n = 0
    while cap.read()[0]:
        n += 1
    cap.release()
    lines = (tmp_path / "recording.csv").read_text(encoding="utf-8").splitlines()
    assert n > 0 and len(lines) == n + 1          # header + one row per frame
    r = json.loads((tmp_path / "recording.json").read_text(encoding="utf-8"))["recording"]
    assert r["frames"] == n and r["codec"] == "MJPG" and r["compressed"] is True


def test_record_video_queue_frames_encodes_on_a_thread_and_its_last_line_counts_the_dropped_frames(tmp_path):
    import re
    out = run(tmp_path, "record_video.py", "--synthetic", "--seconds", "0.3", "--queue-frames", "8")
    r = json.loads((tmp_path / "recording.json").read_text(encoding="utf-8"))["recording"]
    assert r["write_queue"] == 8 and r["frames"] > 0 and r["closed"] is True
    m = re.search(r"estimated missed (\d+) \(of which dropped by the write queue (\d+)\)", out)
    assert m and (int(m[1]), int(m[2])) == (r["estimated_missed_frames"], r["dropped_frames"])
    lines = (tmp_path / "recording.csv").read_text(encoding="utf-8").splitlines()
    assert len(lines) == r["frames"] + 1
    r0 = subprocess.run([sys.executable, str(EXAMPLES / "record_video.py"), "--synthetic", "--queue-frames", "-1"],
                        cwd=tmp_path, capture_output=True, text=True)
    assert r0.returncode == 2 and "--queue-frames takes 0 or a number of frames" in r0.stderr


def test_record_video_lossless_then_play_recording_with_seek_speed_and_loop(tmp_path):
    out = run(tmp_path, "record_video.py", "--synthetic", "--seconds", "0.3", "--lossless")
    assert "lossless" in out and (tmp_path / "recording_lossless.mkv").is_file()
    info = json.loads((tmp_path / "recording_lossless.json").read_text(encoding="utf-8"))
    n = info["recording"]["frames"]
    assert info["recording"]["codec"] == "FFV1" and info["recording"]["compressed"] is False and n > 3
    mkv = "recording_lossless.mkv"
    out = run(tmp_path, "play_recording.py", mkv, "--headless", "--start", "2", "--speed", "8")
    assert f"read {n - 2} frames" in out and f"frames read 2..{n - 1}" in out and "compressed False" in out
    assert "whole recording: late" in out
    out = run(tmp_path, "play_recording.py", mkv, "--headless", "--loop", "--frames", str(2 * n + 1))
    assert f"read {2 * n + 1} frames" in out
    out = run(tmp_path, "play_recording.py", mkv, "--headless", "--start-seconds", "0", "--recorded-time")
    assert f"read {n} frames" in out


def test_play_recording_reads_back_every_frame(tmp_path):
    run(tmp_path, "record_video.py", "--synthetic", "--seconds", "0.3")
    n = len((tmp_path / "recording.csv").read_text(encoding="utf-8").splitlines()) - 1
    out = run(tmp_path, "play_recording.py", "recording.avi", "--headless")
    assert f"read {n} frames" in out


def test_recording_config_ends_with_end_of_recording(tmp_path):
    from opticmix_vision import Device, DeviceConfig, DeviceError, EndOfRecording
    run(tmp_path, "record_video.py", "--synthetic", "--seconds", "0.3")
    with Device(DeviceConfig.recording(tmp_path / "recording.avi")) as cam:
        f = cam.read()
        assert f.left.shape == f.right.shape and f.left.ndim == 2
        with pytest.raises(EndOfRecording):
            while True:
                cam.read()
    with pytest.raises(DeviceError, match="no such recording"):
        DeviceConfig.recording(tmp_path / "nope.avi")


def test_record_video_says_when_the_codec_fails(tmp_path):
    r = subprocess.run([sys.executable, str(EXAMPLES / "record_video.py"), "--synthetic", "--seconds", "0.1",
                        "--out", "x.nosuchext"], cwd=tmp_path, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    assert r.returncode == 3 and "could not open a video writer" in r.stderr



def test_record_video_default_then_lossless_keep_their_own_csv_and_json(tmp_path):
    run(tmp_path, "record_video.py", "--synthetic", "--seconds", "0.3")
    n = json.loads((tmp_path / "recording.json").read_text(encoding="utf-8"))["recording"]["frames"]
    run(tmp_path, "record_video.py", "--synthetic", "--seconds", "0.1", "--lossless")     # recording_lossless.*
    assert f"read {n} frames" in run(tmp_path, "play_recording.py", "recording.avi", "--headless")
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    r = subprocess.run([sys.executable, str(EXAMPLES / "record_video.py"), "--synthetic", "--seconds", "0.1",
                        "--lossless", "--out", "recording.mkv"], cwd=tmp_path, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    assert r.returncode == 3 and "recording.avi has the same name" in r.stderr
    assert "Record to another name" in r.stderr
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before

def test_dataset_npz_then_torch_dataloader(tmp_path):
    run(tmp_path, "capture_dataset_npz.py", "--synthetic", "--frames", "10", "--out", "d.npz")
    info = json.loads((tmp_path / "d.json").read_text(encoding="utf-8"))
    assert info["npz"]["frames"] == 10 and info["npz"]["file"] == "d.npz" and info["mode"]["fourcc"] == "SYNT"
    with np.load(tmp_path / "d.npz") as z:
        assert z["left"].shape == z["right"].shape and z["left"].shape[0] == 10
        assert z["left"].dtype == np.uint8 and z["host_ns"].shape == (10,)
        assert np.all(np.diff(z["host_ns"]) > 0)
    pytest.importorskip("torch")
    out = run(tmp_path, "torch_dataloader.py", "d.npz", "--batch-size", "4")
    assert "10 frames in 3 batches" in out


USB_LOCATION_NOTE = ("A USB location is not a serial number: it names the port the camera is plugged into, and it "
                     "changes when the camera is plugged into another port. Using two cameras at the same time is not "
                     "supported. Picking a camera by USB location has been run without a camera only (made-up device "
                     "lists); it has not been run with a camera, and not run with two cameras.")


def test_list_devices_counts_two_weevia(tmp_path):
    out = run(tmp_path, "list_devices.py", "--synthetic")
    assert "3 video device(s) in the DirectShow list:" in out and "found: 2" in out and "cannot be told apart" in out
    assert ("  [2] WN Dual Camera   <- Weevia S1   USB location: PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(4)#USB(2)\n" in out
            and 'DeviceConfig.weevia_s1(usb_location="<USB location>")' in out and f"\n{USB_LOCATION_NOTE}\n" in out)
    assert out.endswith("The Media Foundation list (--backend msmf) can be in a different order: an index goes only "
                        "with the backend whose list it came from.\n")


def test_list_devices_shows_the_usb_location_windows_reports(monkeypatch, capsys):
    # without --synthetic: a (fake) DirectShow list in which one device has no USB location — its line has none
    import importlib.util

    from opticmix_vision import devices
    spec = importlib.util.spec_from_file_location("list_devices_example", EXAMPLES / "list_devices.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    def no_ffmpeg():
        raise FileNotFoundError("ffmpeg")
    monkeypatch.setattr(devices, "_ffmpeg_devices", no_ffmpeg)
    monkeypatch.setattr(devices, "_native_devices", lambda: [("WN Dual Camera", "PCIROOT(0)#USB(3)"),
                                                             ("OBS Virtual Camera", None)])
    monkeypatch.setattr(sys, "argv", ["list_devices.py"])
    assert mod.main() == 0
    assert capsys.readouterr().out.splitlines()[:3] == [
        "2 video device(s) in the DirectShow list:",
        "  [0] WN Dual Camera   <- Weevia S1   USB location: PCIROOT(0)#USB(3)",
        "  [1] OBS Virtual Camera"]


def test_open_by_usb_location_picks_one_of_two_made_up_weevia_and_reads_a_frame(tmp_path):
    out = run(tmp_path, "open_by_usb_location.py", "--synthetic")
    assert out.splitlines() == [
        "Weevia S1 in the made-up list:",
        "  [0] USB location: PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(1)",
        "  [2] USB location: PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(4)#USB(2)",
        "picked [2] at USB location PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(4)#USB(2)",
        "negotiated: {'width': 640, 'height': 200, 'fps': 90.0, 'fourcc': 'SYNT', 'backend_name': 'synthetic'}",
        "one frame: left 320x200, right 320x200"]
    out = run(tmp_path, "open_by_usb_location.py", "--synthetic", "--usb-location",
              "pciroot(0)#pci(1400)#usbroot(0)#usb(1)")                  # case does not matter
    assert "picked [0] at USB location" in out
    # the webcam's location holds no Weevia S1: exit code 2, nothing opened
    r = subprocess.run([sys.executable, str(EXAMPLES / "open_by_usb_location.py"), "--synthetic", "--usb-location",
                        "PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(5)"], cwd=tmp_path, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    assert r.returncode == 2 and "negotiated" not in r.stdout
    assert "No camera matches 'WN Dual Camera' at USB location" in r.stderr
    # an empty --usb-location is an error, not "pick the last one"
    r = subprocess.run([sys.executable, str(EXAMPLES / "open_by_usb_location.py"), "--synthetic", "--usb-location", ""],
                       cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    assert r.returncode == 2 and "negotiated" not in r.stdout
    assert "error: usb_location is empty" in r.stderr


def test_open_by_usb_location_opens_the_camera_at_that_location(monkeypatch, capsys):
    # without --synthetic: two (fake) Weevia S1 in the DirectShow list; the Device is opened at the second one's index
    import importlib.util

    from opticmix_vision import devices
    from opticmix_vision.synthetic import SyntheticStereoSource
    spec = importlib.util.spec_from_file_location("open_by_usb_location_example", EXAMPLES / "open_by_usb_location.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    def no_ffmpeg():
        raise FileNotFoundError("ffmpeg")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(devices, "_ffmpeg_devices", no_ffmpeg)
    monkeypatch.setattr(devices, "_native_devices", lambda: [("WN Dual Camera", "PCIROOT(0)#USB(1)"),
                                                             ("WN Dual Camera", "PCIROOT(0)#USB(2)")])
    opened = []

    class FakeDevice:
        def __init__(self, cfg):
            opened.append(cfg)

        def open(self):
            return SyntheticStereoSource()
    monkeypatch.setattr(mod, "Device", FakeDevice)
    monkeypatch.setattr(sys, "argv", ["open_by_usb_location.py", "--usb-location", "PCIROOT(0)#USB(2)"])
    assert mod.main() == 0
    assert [c.device for c in opened] == [1]
    out = capsys.readouterr().out.splitlines()
    assert out[:3] == ["Weevia S1 in the DirectShow list:", "  [0] USB location: PCIROOT(0)#USB(1)",
                       "  [1] USB location: PCIROOT(0)#USB(2)"]
    assert out[-1] == "one frame: left 320x200, right 320x200"


def test_list_devices_synthetic_msmf_is_the_same_devices_in_another_order(tmp_path):
    out = run(tmp_path, "list_devices.py", "--synthetic", "--backend", "msmf")
    assert out.splitlines()[:4] == [
        "3 video device(s) in the Media Foundation list:",
        "  [0] Example Laptop Webcam   USB location: PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(5)",
        "  [1] WN Dual Camera   <- Weevia S1   USB location: PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(1)",
        "  [2] WN Dual Camera   <- Weevia S1   USB location: PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(4)#USB(2)"]
    assert out.endswith("The DirectShow list (--backend dshow) can be in a different order: an index goes only with "
                        "the backend whose list it came from.\n")


def test_list_devices_backend_msmf_reads_the_media_foundation_list(monkeypatch, capsys):
    # without --synthetic: the (fake) Media Foundation list, in its own order; DirectShow's must not be read
    import importlib.util

    from opticmix_vision import devices
    spec = importlib.util.spec_from_file_location("list_devices_example", EXAMPLES / "list_devices.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(devices, "_mf_devices", lambda: ["Laptop webcam", "WN Dual Camera", "Laptop AF webcam"])

    def no_directshow():
        raise AssertionError("the DirectShow list was read")
    monkeypatch.setattr(devices, "_ffmpeg_devices", no_directshow)
    monkeypatch.setattr(devices, "_native_devices", no_directshow)
    monkeypatch.setattr(sys, "argv", ["list_devices.py", "--backend", "msmf"])
    assert mod.main() == 0
    assert capsys.readouterr().out.splitlines() == [
        "3 video device(s) in the Media Foundation list:",
        "  [0] Laptop webcam",
        "  [1] WN Dual Camera   <- Weevia S1",
        "  [2] Laptop AF webcam",
        "Weevia S1 ('WN Dual Camera') found: 1",
        "The DirectShow list (--backend dshow) can be in a different order: an index goes only with the backend "
        "whose list it came from."]


def test_threaded_capture_processes_and_counts_skips(tmp_path):
    out = run(tmp_path, "threaded_capture.py", "--synthetic", "--frames", "20", "--work-ms", "20")
    assert "processed 20 frames, skipped" in out


def test_brightness_stats_writes_one_row_per_frame(tmp_path):
    run(tmp_path, "brightness_stats.py", "--synthetic", "--frames", "5", "--out", "b.csv")
    rows = list(csv.DictReader(open(tmp_path / "b.csv", encoding="utf-8")))
    assert len(rows) == 5
    for r in rows:
        for eye in ("left", "right"):
            assert 0 <= int(r[f"{eye}_min"]) <= float(r[f"{eye}_mean"]) <= int(r[f"{eye}_max"]) <= 255
            assert 0.0 <= float(r[f"{eye}_saturated"]) <= 1.0


def test_handle_stall_reopens_then_gives_up_when_out_of_retries(tmp_path):
    out = run(tmp_path, "handle_stall.py", "--synthetic", "--frames", "200", "--wait", "0")
    assert "read 200 frames, 1 reopen(s)" in out
    r = subprocess.run([sys.executable, str(EXAMPLES / "handle_stall.py"), "--synthetic", "--frames", "200",
                        "--wait", "0", "--retries", "0"], cwd=tmp_path, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    assert r.returncode == 2 and "same image for 90 frames" in r.stderr and "Gave up" in r.stderr


def test_calibration_info_synthetic_leaves_the_user_folder_alone(tmp_path):
    import os
    import site
    appdata = tmp_path / "appdata"     # where the user's calibration folder would be on Windows
    env = {**os.environ, "APPDATA": str(appdata), "PYTHONUSERBASE": site.getuserbase()}
    r = subprocess.run([sys.executable, str(EXAMPLES / "calibration_info.py"), "--synthetic"], cwd=tmp_path,
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, env=env)
    assert r.returncode == 0, r.stderr
    assert "baseline: 50.00 mm" in r.stdout and "eye size 800x800" in r.stdout
    assert not appdata.exists()


def test_calibration_check_synthetic_tells_the_wrong_file(tmp_path):
    out = run(tmp_path, "calibration_check.py", "--synthetic")
    medians = [float(part.split(" px")[0]) for part in out.split("epipolar distance: median ")[1:]]
    assert len(medians) == 2 and medians[0] < 0.5 < 3.0 < medians[1], out
    assert "no pass/fail threshold" in out


def test_calibration_check_reads_saved_pairs(tmp_path):
    import cv2
    pytest.importorskip("opticmix_vision._core")
    from opticmix_vision import calib
    from opticmix_vision.calibration import save_calibration
    from opticmix_vision.synthetic import SyntheticPlaneSource
    c = calib._synthetic_calibration()
    src = SyntheticPlaneSource(c, seed=4)
    d = tmp_path / "captures"
    d.mkdir()
    for i in range(5):                                     # the names save_stereo_pairs.py writes
        f = src.read()
        cv2.imencode(".png", f.left)[1].tofile(str(d / f"left_{i:06d}.png"))
        cv2.imencode(".png", f.right)[1].tofile(str(d / f"right_{i:06d}.png"))
    cj = save_calibration(c, tmp_path / "calib.json")
    out = run(tmp_path, "calibration_check.py", "--pairs-dir", str(d), "--file", str(cj))
    assert float(out.split("median ")[1].split(" px")[0]) < 0.5, out


def test_presets_saves_then_loads_a_changed_value(tmp_path):
    import json
    assert "saved" in run(tmp_path, "presets.py", "--synthetic")
    p = tmp_path / "my_preset.json"
    doc = json.loads(p.read_text(encoding="utf-8"))
    assert doc["device"]["model"] == "synthetic"
    doc["controls"]["gain"]["value"] = 40
    p.write_text(json.dumps(doc), encoding="utf-8")
    assert "gain" in run(tmp_path, "presets.py", "--synthetic", "--load", str(p), "--dry-run")
    out = run(tmp_path, "presets.py", "--synthetic", "--load", str(p))
    assert "gain" in out and "kept True" in out and "all kept" in out
