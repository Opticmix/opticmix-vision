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
    assert f"read {n - 2} frames" in out and "compressed False" in out
    assert f"over the {n - 2} frames read (first frame 2, last frame {n - 1}): late" in out
    assert "whole recording: late" in out
    out = run(tmp_path, "play_recording.py", mkv, "--headless", "--loop", "--frames", str(2 * n + 1))
    assert f"read {2 * n + 1} frames" in out
    # the late / missed counts are over every frame read, not over a range of frame numbers (0.0.7: "frames read 0..0")
    assert f"over the {2 * n + 1} frames read (first frame 0, last frame 0): late" in out
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


def _png(path: Path) -> np.ndarray:
    import cv2
    return cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_UNCHANGED)


def test_export_recording_synthetic_writes_one_pair_per_csv_row_byte_for_byte(tmp_path):
    """--synthetic records generated frames losslessly (FFV1), then exports them: one PNG pair per row of the
    recording's CSV with its recorded times, the recording's JSON copied, the frames in an .npz — and the first PNG
    pair is, byte for byte, the recording's first frame and the generated frame it was recorded from."""
    from opticmix_vision import Device, DeviceConfig
    from opticmix_vision.synthetic import SyntheticStereoSource
    out = run(tmp_path, "export_recording.py", "--synthetic", "--npz")
    video, d = tmp_path / "synthetic.mkv", tmp_path / "synthetic_frames"
    recorded = list(csv.DictReader(open(video.with_suffix(".csv"), encoding="utf-8")))
    rows = list(csv.DictReader(open(d / "pairs.csv", encoding="utf-8")))
    assert list(rows[0]) == ["index", "host_ns", "driver_ns", "driver_clock", "left", "right"]
    assert len(rows) == len(recorded) == 30
    assert [[r["index"], r["host_ns"], r["driver_ns"], r["driver_clock"]] for r in rows] == \
        [[r["frame"], r["host_ns"], r["driver_ns"], r["driver_clock"]] for r in recorded]
    assert [(r["left"], r["right"]) for r in rows] == [(f"left_{i:06d}.png", f"right_{i:06d}.png") for i in range(30)]
    assert sorted(p.name for p in d.iterdir()) == sorted(
        [n for r in rows for n in (r["left"], r["right"])] + ["pairs.csv", "capture_info.json", "frames.npz", "frames.json"])
    assert (d / "capture_info.json").read_bytes() == video.with_suffix(".json").read_bytes()
    assert out.splitlines()[-1] == "exported 30 of 30 frames from synthetic.mkv to synthetic_frames"
    generated = SyntheticStereoSource().read()
    with Device(DeviceConfig.recording(video)) as cam:
        first = cam.read()
    for eye in ("left", "right"):
        png = _png(d / rows[0][eye])
        assert png.dtype == np.uint8 and png.shape == getattr(first, eye).shape == (200, 320)
        assert png.tobytes() == getattr(first, eye).tobytes() == getattr(generated, eye).tobytes()
    with np.load(d / "frames.npz") as z:
        assert z["left"].shape == z["right"].shape == (30, 200, 320) and z["left"].dtype == np.uint8
        assert z["left"][0].tobytes() == first.left.tobytes() and z["right"][0].tobytes() == first.right.tobytes()
        assert z["host_ns"].tolist() == [int(r["host_ns"]) for r in recorded]
    info = json.loads((d / "frames.json").read_text(encoding="utf-8"))
    assert info["npz"]["frames"] == 30 and info["npz"]["file"] == "frames.npz" and info["source"] == "recording"


def test_export_recording_start_every_count_and_nothing_is_overwritten(tmp_path):
    run(tmp_path, "record_video.py", "--synthetic", "--seconds", "0.3", "--out", "r.avi")     # MJPG
    n = len(list(csv.DictReader(open(tmp_path / "r.csv", encoding="utf-8"))))
    assert n >= 12
    out = run(tmp_path, "export_recording.py", "r.avi", "--out", "sel", "--start", "2", "--every", "3", "--count", "4")
    rows = list(csv.DictReader(open(tmp_path / "sel" / "pairs.csv", encoding="utf-8")))
    assert [int(r["index"]) for r in rows] == [2, 5, 8, 11]
    assert out.splitlines()[-1] == f"exported 4 of {n} frames from r.avi to sel"
    from opticmix_vision import Device, DeviceConfig
    with Device(DeviceConfig.recording(tmp_path / "r.avi")) as cam:      # the PNGs hold what the MJPG decodes to
        cam.playback.seek(11)
        f = cam.read()
    assert _png(tmp_path / "sel" / "right_000011.png").tobytes() == f.right.tobytes()

    def refused(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(EXAMPLES / "export_recording.py"), *args], cwd=tmp_path,
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    r = refused("r.avi", "--out", "sel")
    assert r.returncode == 2 and "sel is not empty" in r.stderr
    r = refused("--synthetic", "r.avi")
    assert r.returncode == 2 and "r.avi exists" in r.stderr
    r = refused("r.avi", "--out", "late", "--start", str(n))
    assert r.returncode == 2 and f"the recording has {n} frames" in r.stderr and not (tmp_path / "late").exists()
    assert {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


def test_export_recording_says_when_there_is_no_csv_and_how_much_memory_npz_takes(tmp_path):
    assert "no .csv" not in run(tmp_path, "export_recording.py", "--synthetic", "s.mkv", "--out", "first")
    (tmp_path / "s.csv").unlink()
    out = run(tmp_path, "export_recording.py", "s.mkv", "--out", "nocsv", "--npz", "--count", "5")
    assert ("no .csv next to the file: host_ns in pairs.csv is frame number / frame rate, not recorded times; "
            "driver_ns and driver_clock are empty") in out
    assert "--npz: keeps 5 frames in memory until the end, about 0.6 MB (320x200 per eye)" in out
    with open(tmp_path / "nocsv" / "pairs.csv", encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh))
    assert len(rows) == 6 and {len(r) for r in rows} == {6}
    assert [int(r[1]) for r in rows[1:]] == [round(i * 1e9 / 90.0) for i in range(5)]      # labelled 90 fps
    assert {(r[2], r[3]) for r in rows[1:]} == {("", "")}


USB_LOCATION_NOTE = ("A USB location is not a serial number: it names the port the camera is plugged into, and it "
                     "changes when the camera is plugged into another port. Using two cameras at the same time is not "
                     "supported. Picking a camera by USB location has been run with the camera once (one PC, one "
                     "camera, 2026-11-25): with one camera connected, the list showed its USB location, and given "
                     "that location it opened at 1600x800 MJPG and read one frame; a location no device has was "
                     "refused (Python DirectShow, Rust Media Foundation); not run with two cameras.")


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
    monkeypatch.setattr(sys, "platform", "win32")
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


def test_open_by_usb_location_lists_the_v4l2_nodes_on_linux(monkeypatch, capsys, tmp_path):
    # without options on Linux: each Weevia S1 in the (made-up) V4L2 list with its sysfs name; DirectShow's not read
    from opticmix_vision import devices
    from test_devices import GADGET, WEEVIA_USB, _sysfs_tree
    mod = _example("open_by_usb_location.py")
    monkeypatch.setattr(devices, "SYSFS_V4L", _sysfs_tree(tmp_path, {"2-1": WEEVIA_USB, "1-4": WEEVIA_USB}, {
        "video0": (GADGET, 0, "2-1"), "video1": (GADGET, 1, "2-1"), "video2": ("Integrated Webcam", 0, None),
        "video3": (GADGET, 0, "1-4")}))
    monkeypatch.setattr(devices, "_ffmpeg_devices", lambda: pytest.fail("the DirectShow list was read"))
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys, "argv", ["open_by_usb_location.py"])
    assert mod.main() == 0
    assert capsys.readouterr().out.splitlines() == [
        "Weevia S1 in the V4L2 list:", "  [0] USB location: 2-1", "  [3] USB location: 1-4",
        "Pass --usb-location with one of them — on Linux, the USB device's sysfs name (python -m "
        "opticmix_vision.doctor prints it)."]


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


# The Rust list_devices example prints the same lines (tests/examples_synthetic.rs in the Rust crate).
VERBOSE_LIST = [
    "3 video device(s) in the DirectShow list:",
    "  [0] WN Dual Camera   <- Weevia S1   USB location: PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(1)",
    "      vid: 0x1d6b",
    "      pid: 0x0200",
    "      bcd_device: 0x0409",
    "      link_speed: Super",
    "  [1] Example Laptop Webcam   USB location: PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(5)",
    "      vid: not read",
    "      pid: not read",
    "      bcd_device: not read",
    "      link_speed: not read",
    "  [2] WN Dual Camera   <- Weevia S1   USB location: PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(4)#USB(2)",
    "      vid: 0x1d6b",
    "      pid: 0x0200",
    "      bcd_device: 0x0409",
    "      link_speed: High",
    "Not reported by the camera: a USB serial number, a firmware version (bcd_device is the USB device revision; how "
    "it relates to the firmware version has not been verified), a temperature.",
]


def test_list_devices_verbose_prints_the_usb_ids_and_link_speed(tmp_path):
    out = run(tmp_path, "list_devices.py", "--synthetic", "--verbose").splitlines()
    assert out[:len(VERBOSE_LIST)] == VERBOSE_LIST
    plain = run(tmp_path, "list_devices.py", "--synthetic").splitlines()
    added = [ln for ln in out if ln.startswith("      ") or ln == VERBOSE_LIST[-1]]
    assert len(added) == 13 and [ln for ln in out if ln not in added] == plain       # --verbose only adds lines
    msmf = run(tmp_path, "list_devices.py", "--synthetic", "--backend", "msmf", "--verbose").splitlines()
    assert msmf[2:6] == VERBOSE_LIST[7:11] and msmf[7:11] == VERBOSE_LIST[2:6]   # the same devices, another order


def test_list_devices_on_linux_lists_the_v4l2_capture_nodes(monkeypatch, capsys, tmp_path):
    # without --synthetic on Linux: the (made-up) sysfs list, the default there; DirectShow's must not be read
    from opticmix_vision import devices
    from test_devices import GADGET, ONE_CAMERA, WEEVIA_USB, _sysfs_tree
    mod = _example("list_devices.py")
    monkeypatch.setattr(devices, "SYSFS_V4L", _sysfs_tree(tmp_path, {"2-1": WEEVIA_USB}, ONE_CAMERA))
    monkeypatch.setattr(devices, "_ffmpeg_devices", lambda: pytest.fail("the DirectShow list was read"))
    monkeypatch.setattr(sys, "platform", "linux")
    for argv in (["--verbose"], ["--backend", "v4l2", "--verbose"]):
        monkeypatch.setattr(sys, "argv", ["list_devices.py", *argv])
        assert mod.main() == 0
        assert capsys.readouterr().out.splitlines() == [
            "1 video device(s) in the V4L2 list:",
            f"  [0] {GADGET}   <- Weevia S1   USB location: 2-1",
            "      vid: 0x1d6b", "      pid: 0x0200", "      bcd_device: 0x0409", "      link_speed: Super",
            VERBOSE_LIST[-1],
            "Weevia S1 ('WN Dual Camera') found: 1"]


# The Rust list_devices example prints the same device lines (tests/examples_synthetic.rs in the Rust crate).
SYNTHETIC_V4L2_LIST = [
    "3 video device(s) in the V4L2 list:",
    "  [0] Webcam gadget: WN Dual Camera   <- Weevia S1   USB location: 2-1",
    "      vid: 0x1d6b",
    "      pid: 0x0200",
    "      bcd_device: 0x0409",
    "      link_speed: Super",
    "  [2] Example Laptop Webcam   USB location: 1-5",
    "      vid: not read",
    "      pid: not read",
    "      bcd_device: not read",
    "      link_speed: not read",
    "  [4] Webcam gadget: WN Dual Camera   <- Weevia S1   USB location: 1-4.2",
]


def test_list_devices_synthetic_v4l2_is_a_made_up_v4l2_list(tmp_path):
    out = run(tmp_path, "list_devices.py", "--synthetic", "--backend", "v4l2", "--verbose").splitlines()
    assert out[:12] == SYNTHETIC_V4L2_LIST
    assert out[16:18] == [VERBOSE_LIST[-1], "Weevia S1 ('WN Dual Camera') found: 2"]
    assert out[19] == USB_LOCATION_NOTE and len(out) == 20                # no line about another backend's list
    plain = run(tmp_path, "list_devices.py", "--synthetic", "--backend", "v4l2").splitlines()
    assert plain[:4] == [SYNTHETIC_V4L2_LIST[i] for i in (0, 1, 6, 11)]


def test_list_devices_backend_v4l2_off_linux_says_the_list_is_linuxs(monkeypatch, capsys):
    from opticmix_vision import devices
    mod = _example("list_devices.py")
    monkeypatch.setattr(devices, "_v4l2_devices", lambda _: pytest.fail("the V4L2 list was read"))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "argv", ["list_devices.py", "--backend", "v4l2"])
    assert mod.main() == 0
    assert capsys.readouterr().out.splitlines() == [
        "The V4L2 list is Linux's. On Windows: the DirectShow list (the default) or --backend msmf (Media "
        "Foundation's)."]


def test_list_devices_verbose_shows_what_windows_reports(monkeypatch, capsys):
    # without --synthetic: a (fake) DirectShow list as the Rust core gives it, one device with nothing read
    from opticmix_vision import devices
    mod = _example("list_devices.py")

    def no_ffmpeg():
        raise FileNotFoundError("ffmpeg")
    monkeypatch.setattr(devices, "_ffmpeg_devices", no_ffmpeg)
    monkeypatch.setattr(devices, "_native_devices", lambda: [
        ("WN Dual Camera", "PCIROOT(0)#USB(3)", 0x1D6B, 0x0200, 0x0409, "Super+"), ("OBS Virtual Camera", None)])
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "argv", ["list_devices.py", "--verbose"])
    assert mod.main() == 0
    assert capsys.readouterr().out.splitlines()[:11] == [
        "2 video device(s) in the DirectShow list:",
        "  [0] WN Dual Camera   <- Weevia S1   USB location: PCIROOT(0)#USB(3)",
        "      vid: 0x1d6b", "      pid: 0x0200", "      bcd_device: 0x0409", "      link_speed: Super+",
        "  [1] OBS Virtual Camera",
        "      vid: not read", "      pid: not read", "      bcd_device: not read", "      link_speed: not read"]
