"""recording — Recorder writes video + CSV + capture-info JSON; Device plays it back with seek, speed, loop,
pause and recorded times; Device's frame statistics come from host intervals. Generated frames only."""

from __future__ import annotations

import csv
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

import opticmix_vision
from opticmix_vision import Device, DeviceConfig, EndOfRecording, StereoFrame
from opticmix_vision import device as device_mod
from opticmix_vision import recording
from opticmix_vision.recording import LOSSLESS_CODEC, Recorder, capture_info
from opticmix_vision.synthetic import SyntheticStereoSource


def _record(tmp_path: Path, n: int = 12, *, codec: str = "MJPG", step_ns: int | None = None,
            name: str | None = None) -> tuple[Path, list[StereoFrame], SyntheticStereoSource]:
    """n generated frames into a recording. step_ns: replace host_ns with 0, step, 2*step, ..."""
    src = SyntheticStereoSource(eye_size=(64, 32), fps=90.0)
    path = tmp_path / (name or ("rec.mkv" if codec == LOSSLESS_CODEC else "rec.avi"))
    frames = []
    with Recorder(path, src, codec=codec) as rec:
        for i in range(n):
            f = src.read()
            if step_ns is not None:
                f = StereoFrame(left=f.left, right=f.right, host_ns=10**9 + i * step_ns, index=f.index)
            frames.append(f)
            rec.write(f)
    return path, frames, src


def _csv_ns(path: Path) -> list[int]:
    with open(path.with_suffix(".csv"), newline="", encoding="utf-8") as fh:
        return [int(r["host_ns"]) for r in csv.DictReader(fh)]


# ---------------------------------------------------------------- capture info JSON

def test_recording_writes_a_json_with_mode_controls_version_and_frame_count(tmp_path):
    path, frames, src = _record(tmp_path, 10)
    info = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    assert info["opticmix_vision"] == opticmix_vision.__version__
    assert datetime.fromisoformat(info["created"]).tzinfo is not None
    assert info["mode"] == src.mode.to_dict() == {"width": 128, "height": 32, "fps": 90.0, "fourcc": "SYNT",
                                                   "backend_name": "synthetic"}
    assert info["source"] == "synthetic" and "simulated" in info["controls_note"]
    assert info["controls"] == src.controls.snapshot() and "backlight_compensation" in info["controls"]
    r = info["recording"]
    assert r["codec"] == "MJPG" and r["compressed"] is True
    assert (r["layout"], r["lr_order"]) == ("sbs", "first_is_left")
    assert r["frames"] == 10 and r["eye_size"] == [64, 32] and r["csv"] == "rec.csv"
    ns = _csv_ns(path)
    assert (r["first_host_ns"], r["last_host_ns"]) == (ns[0], ns[-1]) == (frames[0].host_ns, frames[-1].host_ns)
    assert r["duration_s"] == pytest.approx((ns[-1] - ns[0]) / 1e9)


def test_capture_info_names_the_camera_its_name_and_calibration_files(tmp_path, monkeypatch):
    import hashlib
    import sys
    from opticmix_vision import calib, devices

    class Cap:                                  # a VideoCapture stand-in: one sbs frame, nothing else
        def isOpened(self): return True
        def get(self, pid): return 0.0
        def set(self, pid, v): return True
        def read(self): return True, np.zeros((8, 16), np.uint8)
        def release(self): pass
    store = tmp_path / "calib"                  # never the user's calibration folder
    store.mkdir()
    (store / "S1-0007.json").write_bytes(b'{"x": 1}')
    monkeypatch.setattr(calib, "store_dir", lambda: store)
    monkeypatch.setattr(devices, "_native_devices", lambda: ["Webcam", "Other", "Third", "WN Dual Camera"])
    monkeypatch.setattr(devices, "SYSFS_V4L", str(tmp_path / "no-sysfs"))   # Linux: no /dev/video3 name
    cfg = DeviceConfig(layout="sbs", lr_order="first_is_right", device=3, backend="dshow")
    with Device(cfg, capture=Cap()) as cam:
        info = capture_info(cam, note="x")
    assert info["source"] == "camera" and info["note"] == "x"
    name = "WN Dual Camera" if sys.platform == "win32" else None
    assert info["camera"] == {"device": 3, "device_name": name, "backend": "dshow", "layout": "sbs",
                              "lr_order": "first_is_right"}
    assert info["calibration_files"] == [{"file": "S1-0007.json",
                                          "sha256": hashlib.sha256(b'{"x": 1}').hexdigest()}]
    assert "controls" in info                   # the values, or None with the reason in controls_note
    assert info["controls"] is not None or info["controls_note"]


def test_playback_exposes_the_recorded_mode_and_info(tmp_path):
    path, _, src = _record(tmp_path, 5)
    with Device(DeviceConfig.recording(path)) as cam:
        assert cam.mode == src.mode                                  # the recorded mode, not the file's
        assert cam.recording_info == json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
        with pytest.raises(opticmix_vision.DeviceError, match="recording_info"):
            cam.controls
        info = capture_info(cam)                                    # re-recording a recording says so
        assert info["source"] == "recording" and info["recorded_from"]["recording"]["frames"] == 5
    with Device(DeviceConfig(layout="sbs", lr_order="first_is_left", device=0), capture=_NoFrames()) as cam:
        assert cam.playback is None and cam.recording_info is None


class _NoFrames:
    def isOpened(self): return True
    def get(self, pid): return 0.0
    def set(self, pid, v): return True
    def read(self): return False, None
    def release(self): pass


def test_a_recording_without_json_or_csv_still_plays(tmp_path):
    path, frames, _ = _record(tmp_path, 6)
    path.with_suffix(".json").unlink()
    path.with_suffix(".csv").unlink()
    with Device(DeviceConfig.recording(path)) as cam:
        assert cam.recording_info is None
        assert (cam.mode.width, cam.mode.height, cam.mode.fourcc) == (128, 32, "MJPG")   # the file's own
        assert cam.playback.frame_count == 6
        assert cam.playback.duration_s == pytest.approx(5 / cam.mode.fps)
        assert [cam.read().index for _ in range(6)] == list(range(6))


# ---------------------------------------------------------------- lossless round trip

def test_lossless_recording_plays_back_every_pixel(tmp_path):
    path, frames, _ = _record(tmp_path, 8, codec=LOSSLESS_CODEC)
    info = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    assert info["recording"]["codec"] == "FFV1" and info["recording"]["compressed"] is False
    with Device(DeviceConfig.recording(path)) as cam:
        got = []
        with pytest.raises(EndOfRecording):
            while True:
                got.append(cam.read())
    assert len(got) == 8
    for f, g in zip(frames, got):
        assert np.array_equal(f.left, g.left) and np.array_equal(f.right, g.right)


def test_mjpg_recording_is_marked_compressed_and_changes_pixels(tmp_path):
    path, frames, _ = _record(tmp_path, 3)
    with Device(DeviceConfig.recording(path)) as cam:
        g = cam.read()
    assert g.left.shape == frames[0].left.shape
    assert not np.array_equal(g.left, frames[0].left)         # what "compressed": true warns about


# ---------------------------------------------------------------- seek / position / duration

def test_seek_by_frame_and_by_seconds_returns_that_frame(tmp_path):
    path, frames, _ = _record(tmp_path, 20, codec=LOSSLESS_CODEC, step_ns=10_000_000)   # 10 ms apart
    with Device(DeviceConfig.recording(path)) as cam:
        pb = cam.playback
        assert pb.frame_count == 20 and pb.duration_s == pytest.approx(0.19)
        assert pb.seek(13) == 13 and pb.position == 13 and pb.position_s == pytest.approx(0.13)
        f = cam.read()
        assert f.index == 13 and np.array_equal(f.left, frames[13].left)
        assert cam.read().index == 14 and pb.position == 14
        assert pb.seek(seconds=0.05) == 5
        f = cam.read()
        assert f.index == 5 and np.array_equal(f.right, frames[5].right)
        assert pb.seek(2) == 2 and cam.read().index == 2                       # backwards
        assert pb.seek(99) == 19 and pb.seek(-4) == 0                          # clamped
        assert pb.seek(seconds=0.051) == 6                                     # first frame at or after
        with pytest.raises(ValueError):
            pb.seek()
        with pytest.raises(ValueError):
            pb.seek(1, seconds=1.0)


def test_seek_in_mjpg_matches_reading_in_order(tmp_path):
    path, _, _ = _record(tmp_path, 15)
    with Device(DeviceConfig.recording(path)) as cam:
        in_order = [cam.read().left.copy() for _ in range(15)]
        for i in (11, 3, 14, 0, 7):
            cam.playback.seek(i)
            f = cam.read()
            assert f.index == i and np.array_equal(f.left, in_order[i])


# ---------------------------------------------------------------- loop / pause / recorded time

def test_loop_starts_again_and_without_it_the_recording_ends(tmp_path):
    path, _, _ = _record(tmp_path, 5)
    with Device(DeviceConfig.recording(path)) as cam:
        cam.playback.loop = True
        assert [cam.read().index for _ in range(12)] == [0, 1, 2, 3, 4, 0, 1, 2, 3, 4, 0, 1]
    with Device(DeviceConfig.recording(path)) as cam:
        assert [cam.read().index for _ in range(5)] == [0, 1, 2, 3, 4]
        with pytest.raises(EndOfRecording):
            cam.read()


def test_pause_repeats_the_frame_seek_still_moves_resume_goes_on(tmp_path):
    path, frames, _ = _record(tmp_path, 10, codec=LOSSLESS_CODEC)
    with Device(DeviceConfig.recording(path)) as cam:
        pb = cam.playback
        cam.read(); cam.read()
        pb.pause()                                                 # holds the frame shown last (1)
        assert pb.paused and [cam.read().index for _ in range(3)] == [1, 1, 1]
        pb.seek(pb.position + 1)                                   # one frame forward while paused
        assert [cam.read().index for _ in range(2)] == [2, 2]
        pb.seek(pb.position - 2)                                   # two back
        f = cam.read()
        assert f.index == 0 and np.array_equal(f.left, frames[0].left)
        pb.resume()
        assert [cam.read().index for _ in range(3)] == [1, 2, 3]


def test_recorded_time_gives_the_csv_times(tmp_path):
    path, _, _ = _record(tmp_path, 6, step_ns=7_000_000)
    ns = _csv_ns(path)
    with Device(DeviceConfig.recording(path)) as cam:
        assert cam.read().host_ns != ns[0]                         # default: when it was read back
        cam.playback.recorded_time = True
        cam.playback.seek(0)
        assert [cam.read().host_ns for _ in range(6)] == ns


# ---------------------------------------------------------------- driver time columns

_MF = "media_foundation"
# made-up Media Foundation times (whole 100 ns units, as Device gives them); the third frame had none (DirectShow)
_DRIVERS = [(100_000_123_456_700, _MF), (100_000_134_567_800, _MF), (None, None), (100_000_156_789_900, _MF)]


def _record_driver(tmp_path: Path, drivers=_DRIVERS) -> Path:
    """Generated frames carrying the given (driver_ns, driver_clock), host_ns 1 s + i * 11,111,111 ns."""
    src = SyntheticStereoSource(eye_size=(64, 32), fps=90.0)
    path = tmp_path / "rec.avi"
    with Recorder(path, src) as rec:
        for i, (d, c) in enumerate(drivers):
            f = src.read()
            rec.write(StereoFrame(f.left, f.right, 10**9 + i * 11_111_111, f.index, driver_ns=d, driver_clock=c))
    return path


def test_the_csv_has_each_frames_driver_time_and_clock_and_empty_cells_without_one(tmp_path):
    path = _record_driver(tmp_path)
    with open(path.with_suffix(".csv"), newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    assert rows == [["frame", "host_ns", "driver_ns", "driver_clock"],
                    ["0", "1000000000", "100000123456700", _MF],
                    ["1", "1011111111", "100000134567800", _MF],
                    ["2", "1022222222", "", ""],
                    ["3", "1033333333", "100000156789900", _MF]]


def test_synthetic_frames_have_no_driver_time_so_the_cells_are_empty(tmp_path):
    path, frames, _ = _record(tmp_path, 3)
    with open(path.with_suffix(".csv"), newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert [(r["driver_ns"], r["driver_clock"]) for r in rows] == [("", "")] * 3
    assert [int(r["host_ns"]) for r in rows] == [f.host_ns for f in frames]


def test_playback_gives_the_recorded_driver_times_with_recorded_time_only(tmp_path):
    path = _record_driver(tmp_path)
    with Device(DeviceConfig.recording(path)) as cam:
        pb = cam.playback
        # default: host_ns is when the frame was read back, so no recorded driver time is put beside it
        assert [(f.driver_ns, f.driver_clock) for f in (cam.read() for _ in range(4))] == [(None, None)] * 4
        pb.recorded_time = True
        pb.seek(0)
        got = [(f.index, f.host_ns, f.driver_ns, f.driver_clock) for f in (cam.read() for _ in range(4))]
        assert got == [(i, 10**9 + i * 11_111_111, d, c) for i, (d, c) in enumerate(_DRIVERS)]
        assert got[1][2] - got[0][2] == 11_111_100 and got[1][1] - got[0][1] == 11_111_111   # each on its own clock
        pb.seek(1)
        pb.pause()                                   # the held frame keeps its recorded time
        assert {(f.driver_ns, f.driver_clock) for f in (cam.read() for _ in range(2))} == {_DRIVERS[1]}
        pb.resume()
        assert [cam.read().driver_ns for _ in range(2)] == [None, _DRIVERS[3][0]]
        assert [pb.recorded_driver(i) for i in range(4)] == _DRIVERS
        assert pb.recorded_driver(-3) == _DRIVERS[0] and pb.recorded_driver(99) == _DRIVERS[3]     # clamped


def _old_csv(path: Path) -> list[int]:
    """Rewrites the recording's CSV as versions before 0.0.5 wrote it (csv.writer, header frame, host_ns). Returns the host_ns."""
    ns = _csv_ns(path)
    with open(path.with_suffix(".csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["frame", "host_ns"])
        w.writerows([i, t] for i, t in enumerate(ns))
    return ns


def test_a_csv_of_only_frame_and_host_ns_still_plays_seeks_and_paces(tmp_path, monkeypatch):
    import warnings
    path, frames, _ = _record(tmp_path, 6, step_ns=20_000_000)       # 20 ms apart at 90 fps: each interval late
    with Device(DeviceConfig.recording(path)) as cam:
        new = (cam.playback.late_frames, cam.playback.estimated_missed_frames)
    ns = _old_csv(path)
    assert path.with_suffix(".csv").read_text(encoding="utf-8").splitlines()[0] == "frame,host_ns"
    with warnings.catch_warnings():
        warnings.simplefilter("error")               # no "CSV and video disagree" warning
        with Device(DeviceConfig.recording(path)) as cam:
            pb = cam.playback
            assert pb.frame_count == 6 and pb.duration_s == pytest.approx(0.1)
            assert (pb.late_frames, pb.estimated_missed_frames) == new == (5, 5)
            pb.recorded_time = True
            got = [cam.read() for _ in range(6)]
            assert [f.host_ns for f in got] == ns == [f.host_ns for f in frames]
            assert [(f.driver_ns, f.driver_clock) for f in got] == [(None, None)] * 6
            assert pb.seek(seconds=0.05) == 3 and cam.read().index == 3
            assert [pb.recorded_driver(i) for i in range(6)] == [(None, None)] * 6
    ms = lambda ts: [round(t / 1e6) for t in ts]                     # noqa: E731
    assert ms(_read_times(path, monkeypatch, real_time=True)) == [0, 20, 40, 60, 80, 100]


def test_a_csv_cell_that_is_not_a_number_names_the_file_and_line(tmp_path):
    path = _record_driver(tmp_path)
    csv_path = path.with_suffix(".csv")
    lines = csv_path.read_text(encoding="utf-8").splitlines()
    lines[3] = lines[3].replace("1022222222", "10x2")                  # line 4: the third frame's host_ns
    csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(opticmix_vision.DeviceError, match=r"rec\.csv line 4: not a frame time row \(host_ns '10x2'"):
        Device(DeviceConfig.recording(path)).open()
    lines[3] = "2,1022222222,35x,media_foundation"                     # a driver_ns that is not a number
    csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"line 4: .*driver_ns '35x'"):
        recording.Playback(path)


def test_a_recording_with_driver_times_but_no_csv_has_none(tmp_path):
    path = _record_driver(tmp_path)
    path.with_suffix(".csv").unlink()
    with Device(DeviceConfig.recording(path)) as cam:
        cam.playback.recorded_time = True
        assert [(f.driver_ns, f.driver_clock) for f in (cam.read() for _ in range(4))] == [(None, None)] * 4


# ---------------------------------------------------------------- real-time pacing and speed

class _Clock:
    """Stands in for time.perf_counter_ns / time.sleep in recording.py: sleeping moves the clock."""

    def __init__(self) -> None:
        self.ns = 5_000_000_000

    def perf_counter_ns(self) -> int:
        return self.ns

    def sleep(self, s: float) -> None:
        self.ns += max(1, round(s * 1e9))


def _read_times(path: Path, monkeypatch, *, real_time: bool, speed: float = 1.0, n: int = 6) -> list[int]:
    clock = _Clock()
    monkeypatch.setattr(recording, "time", clock)
    with Device(DeviceConfig.recording(path)) as cam:
        cam.playback.real_time = real_time
        cam.playback.set_speed(speed)
        out = []
        for _ in range(n):
            cam.read()
            out.append(clock.ns)
    return [t - out[0] for t in out]


def test_real_time_follows_the_recorded_intervals_divided_by_speed(tmp_path, monkeypatch):
    path, _, _ = _record(tmp_path, 6, step_ns=20_000_000)          # recorded 20 ms apart
    ms = lambda ts: [round(t / 1e6) for t in ts]                     # noqa: E731
    assert ms(_read_times(path, monkeypatch, real_time=True)) == [0, 20, 40, 60, 80, 100]
    assert ms(_read_times(path, monkeypatch, real_time=True, speed=4.0)) == [0, 5, 10, 15, 20, 25]
    assert ms(_read_times(path, monkeypatch, real_time=True, speed=0.5)) == [0, 40, 80, 120, 160, 200]
    assert ms(_read_times(path, monkeypatch, real_time=False)) == [0] * 6       # as fast as it decodes


def test_real_time_uses_uneven_recorded_gaps(tmp_path, monkeypatch):
    src = SyntheticStereoSource(eye_size=(32, 16))
    path = tmp_path / "gaps.avi"
    with Recorder(path, src) as rec:
        for t_ms in (0, 10, 50, 60):
            f = src.read()
            rec.write(StereoFrame(left=f.left, right=f.right, host_ns=t_ms * 1_000_000, index=f.index))
    assert [round(t / 1e6) for t in _read_times(path, monkeypatch, real_time=True, n=4)] == [0, 10, 50, 60]


def test_speed_must_be_positive(tmp_path):
    path, _, _ = _record(tmp_path, 2)
    with Device(DeviceConfig.recording(path)) as cam:
        for bad in (0, -1.0):
            with pytest.raises(ValueError):
                cam.playback.set_speed(bad)


# ---------------------------------------------------------------- recorder pause, failures

def test_recorder_pause_leaves_frames_out(tmp_path):
    src = SyntheticStereoSource(eye_size=(32, 16))
    path = tmp_path / "p.avi"
    with Recorder(path, src) as rec:
        for i in range(6):
            if i == 2:
                rec.pause()
            if i == 4:
                rec.resume()
            rec.write(src.read())
    assert rec.frames == 4 and len(_csv_ns(path)) == 4
    assert json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))["recording"]["frames"] == 4


def test_recorder_says_when_the_codec_cannot_be_opened(tmp_path):
    src = SyntheticStereoSource(eye_size=(32, 16))
    with pytest.raises(OSError, match="could not open a video writer"):     # when it is created, before any frame
        Recorder(tmp_path / "x.nosuchext", src)
    assert not (tmp_path / "x.csv").exists() and not (tmp_path / "x.json").exists()
    with pytest.raises(ValueError):
        Recorder(tmp_path / "y.avi", src, codec="MJPEG")


# ---------------------------------------------------------------- late / missed frame estimates

class _Frames:
    """A VideoCapture stand-in that returns n tiny sbs frames at 100 fps (negotiated)."""

    def __init__(self, n: int) -> None:
        self.n = n

    def isOpened(self): return True

    def get(self, pid):
        import cv2
        return 100.0 if pid == cv2.CAP_PROP_FPS else 0.0

    def set(self, pid, v): return True

    def read(self):
        if not self.n:
            return False, None
        self.n -= 1
        return True, np.zeros((4, 8), np.uint8)

    def release(self): pass


def _gapped(tmp_path: Path, times_ms, *, pause_at=None, resume_at=None, codec="MJPG", name="g.avi"):
    """A recording at 100 fps whose frames carry the given host times; frames between pause_at and resume_at
    (indices into times_ms) are left out with Recorder.pause / resume."""
    src = SyntheticStereoSource(eye_size=(32, 16), fps=100.0)
    path = tmp_path / name
    with Recorder(path, src, codec=codec) as rec:
        for k, t_ms in enumerate(times_ms):
            if k == pause_at:
                rec.pause()
            if k == resume_at:
                rec.resume()
            f = src.read()
            rec.write(StereoFrame(left=f.left, right=f.right, host_ns=t_ms * 1_000_000, index=f.index))
    return path, rec


def test_a_pause_in_recording_is_a_break_not_missed_frames(tmp_path, monkeypatch):
    path, rec = _gapped(tmp_path, [0, 10, 20, 30, 40, 50, 60, 70, 80, 90], pause_at=5, resume_at=8)
    assert (rec.frames, rec.late_frames, rec.estimated_missed_frames) == (7, 0, 0)
    r = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))["recording"]
    assert r["breaks"] == [5] and (r["late_frames"], r["estimated_missed_frames"]) == (0, 0)
    with Device(DeviceConfig.recording(path)) as cam:
        pb = cam.playback
        assert pb.breaks == {5} and (pb.late_frames, pb.estimated_missed_frames) == (0, 0)
        for _ in range(7):
            cam.read()
        assert (cam.late_frames, cam.estimated_missed_frames) == (0, 0)
    # real time does not wait the pause out: 0 10 20 30 40 | 80 90 100 recorded -> 0..40, then 40, 50
    assert [round(t / 1e6) for t in _read_times(path, monkeypatch, real_time=True, n=7)] == [0, 10, 20, 30, 40, 40, 50]


def test_recorder_counts_missed_frames_while_recording_and_says_what_to_do(tmp_path):
    from opticmix_vision.viewer import _stopped
    path, rec = _gapped(tmp_path, [0, 10, 20, 50, 60, 90, 100])      # 30 ms gaps at 100 fps: 2 late, 4 missed
    assert (rec.late_frames, rec.estimated_missed_frames) == (2, 4)
    r = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))["recording"]
    assert (r["late_frames"], r["estimated_missed_frames"], r["closed"]) == (2, 4, True)
    msg = _stopped(rec)
    assert "about 4 frames missed" in msg and "--fps" in msg and "capture_dataset_npz" in msg
    assert "MJPG" not in msg                                     # it is MJPG already: not advice to give
    # the viewer's v records alongside the preview: first try examples/record_video.py, which has no window
    assert "First try examples/record_video.py (it records with no preview window)." in msg
    assert msg.index("examples/record_video.py") < msg.index("--fps") < msg.index("capture_dataset_npz")
    assert msg.isascii()                                         # it is printed; a cp949 console fails on others
    _, ffv1 = _gapped(tmp_path, [0, 10, 40], codec=LOSSLESS_CODEC, name="l.mkv")
    assert "about 2 frames missed" in _stopped(ffv1) and "record MJPG (leave out --lossless)" in _stopped(ffv1)
    assert rec.missed_advice in msg and ffv1.missed_advice in _stopped(ffv1)
    # record_video.py prints missed_advice itself: it must not tell its user to run record_video.py
    assert "record_video" not in rec.missed_advice and "record_video" not in ffv1.missed_advice
    path2, rec2 = _gapped(tmp_path, [0, 10, 20], name="ok.avi")
    assert "missed" not in _stopped(rec2) and rec2.missed_advice == ""


def test_playback_reports_the_whole_recording_and_the_frames_read(tmp_path):
    path, _ = _gapped(tmp_path, [0, 10, 40, 50, 60, 90, 100])        # gaps at frames 2 and 5
    with Device(DeviceConfig.recording(path)) as cam:
        pb = cam.playback
        assert (pb.late_frames, pb.estimated_missed_frames) == (2, 4)    # whole file, before reading
        pb.seek(3)
        for _ in range(2):                                               # frames 3, 4: no gap
            cam.read()
        assert (cam.late_frames, cam.estimated_missed_frames) == (0, 0)


def test_control_changes_and_end_values_go_into_the_json(tmp_path):
    src = SyntheticStereoSource(eye_size=(32, 16))
    led0 = src.controls.get("backlight_compensation")
    path = tmp_path / "c.avi"
    with Recorder(path, src) as rec:
        rec.write(src.read())
        src.controls.set("backlight_compensation", led0 + 8)
        rec.note_control("backlight_compensation", led0 + 8, "written")
        rec.write(src.read())
    r = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    assert r["controls"]["backlight_compensation"] == led0
    assert r["recording"]["control_changes"] == [{"frame": 1, "name": "backlight_compensation",
                                                  "value": led0 + 8, "status": "written"}]
    assert r["recording"]["controls_at_end"]["backlight_compensation"] == led0 + 8


def test_status_is_playing_paused_or_ended(tmp_path):
    path, _, _ = _record(tmp_path, 3)
    with Device(DeviceConfig.recording(path)) as cam:
        pb = cam.playback
        assert pb.status == "playing"
        cam.read()
        pb.pause()
        assert pb.status == "paused"
        pb.resume()
        cam.read(); cam.read()
        with pytest.raises(EndOfRecording):
            cam.read()
        assert pb.status == "ended"
        pb.seek(0)
        assert pb.status == "playing" and cam.read().index == 0


_CRASH = """
import os, sys
sys.path.insert(0, sys.argv[1])
from opticmix_vision.synthetic import SyntheticStereoSource
from opticmix_vision.recording import Recorder
src = SyntheticStereoSource(eye_size=(160, 100))
rec = Recorder(sys.argv[2], src, codec=sys.argv[3])
for _ in range(50):
    rec.write(src.read())
os._exit(0)          # the process stops while recording: no release, no close
"""


def _crash(tmp_path: Path, name: str, codec: str) -> Path:
    import subprocess
    import sys
    sdk = Path(__file__).resolve().parents[1]
    r = subprocess.run([sys.executable, "-c", _CRASH, str(sdk), str(tmp_path / name), codec], capture_output=True,
                       timeout=120)
    assert r.returncode == 0, r.stderr
    return tmp_path / name


def test_a_recording_that_was_not_closed(tmp_path):
    avi = _crash(tmp_path, "x.avi", "MJPG")
    with pytest.raises(opticmix_vision.DeviceError, match="not closed"):
        Device(DeviceConfig.recording(avi)).open()
    mkv = _crash(tmp_path, "y.mkv", LOSSLESS_CODEC)
    assert len(_csv_ns(mkv)) == 50                                   # the CSV is written line by line
    with pytest.warns(RuntimeWarning, match="not closed"):
        cam = Device(DeviceConfig.recording(mkv)).open()
    with cam:
        n = cam.playback.frame_count
        assert 0 < n < 50                                            # what the .mkv holds
        got = 0
        with pytest.raises(EndOfRecording):
            while True:
                cam.read()
                got += 1
        assert got == n


def test_late_and_missed_frames_come_from_host_intervals(monkeypatch):
    # 100 fps = 10 ms. Gaps: 10, 14 (not late: under 1.5 periods), 30 (late, 2 missed), 10, 16 (late, 1 missed), 10
    times_ms = [0, 10, 24, 54, 64, 80, 90]
    it = iter(t * 1_000_000 for t in times_ms)
    monkeypatch.setattr(device_mod.time, "perf_counter_ns", lambda: next(it))
    cfg = DeviceConfig(layout="sbs", lr_order="first_is_left", stall_frames=0)
    with Device(cfg, capture=_Frames(len(times_ms))) as cam:
        assert cam.mode.fps == 100.0
        assert (cam.late_frames, cam.estimated_missed_frames, cam.actual_fps) == (0, 0, 0.0)
        for _ in times_ms:
            cam.read()
        assert cam.late_frames == 2
        assert cam.estimated_missed_frames == 3
        assert cam.actual_fps == pytest.approx(6 / 0.090)       # 7 frames in 90 ms


def test_frame_statistics_skip_repeats_and_jumps():
    s = device_mod._FrameStats()
    for i, ms in [(0, 0), (1, 10), (1, 500), (2, 510), (8, 900), (9, 910)]:   # paused at 1, then a seek to 8
        s.add(i, ms * 1_000_000, 100.0)
    assert (s.late, s.missed) == (0, 0)
    assert s.fps == pytest.approx(100.0)


def test_actual_fps_is_over_the_last_30_frames():
    s = device_mod._FrameStats()
    t = 0
    for i in range(40):
        t += 20_000_000 if i < 10 else 10_000_000                   # 50 fps, then 100 fps
        s.add(i, t, 100.0)
    assert s.fps == pytest.approx(100.0)


def test_playback_statistics_describe_the_recording(tmp_path):
    src = SyntheticStereoSource(eye_size=(32, 16), fps=100.0)
    path = tmp_path / "late.avi"
    with Recorder(path, src) as rec:
        for t_ms in (0, 10, 20, 50, 60):                            # one 30 ms gap at 100 fps: late 1, missed 2
            f = src.read()
            rec.write(StereoFrame(left=f.left, right=f.right, host_ns=t_ms * 1_000_000, index=f.index))
    with Device(DeviceConfig.recording(path)) as cam:
        for _ in range(5):
            cam.read()
        assert (cam.late_frames, cam.estimated_missed_frames) == (1, 2)


def test_synthetic_source_has_the_same_statistics():
    src = SyntheticStereoSource(eye_size=(32, 16), fps=1000.0)
    for _ in range(5):
        src.read()
    assert src.late_frames >= 0 and src.estimated_missed_frames >= 0 and src.actual_fps > 0


# ---------------------------------------------------------------- opening the files; names

class _Clock2:
    """time.perf_counter_ns for the synthetic source: a camera's arrival times, moved on by the test."""

    def __init__(self) -> None:
        self.ns = 7_000_000_000

    def perf_counter_ns(self) -> int:
        return self.ns

    def perf_counter(self) -> float:
        return self.ns / 1e9


def test_the_files_are_open_before_the_first_frame_so_opening_is_not_a_gap(tmp_path, monkeypatch):
    """Opening the video file takes time (here 0.5 s on a fake clock). Frames that arrive meanwhile would be
    missed, so the files open when the recorder is created, not between the first two frames."""
    import cv2
    from opticmix_vision import synthetic
    clock = _Clock2()
    monkeypatch.setattr(synthetic, "time", clock)
    real = cv2.VideoWriter

    def slow_writer(*a, **k):
        clock.ns += 500_000_000
        return real(*a, **k)
    monkeypatch.setattr(cv2, "VideoWriter", slow_writer)
    src = SyntheticStereoSource(eye_size=(32, 16), fps=100.0)
    path = tmp_path / "open.avi"
    with Recorder(path, src) as rec:
        assert path.is_file() and rec.csv_path.is_file() and rec.info_path.is_file()   # before any frame
        assert json.loads(rec.info_path.read_text(encoding="utf-8"))["recording"]["eye_size"] == [32, 16]
        for _ in range(5):
            rec.write(src.read())
            clock.ns += 10_000_000                                 # the camera's next frame, 10 ms later
    ns = _csv_ns(path)
    assert [b - a for a, b in zip(ns, ns[1:])] == [10_000_000] * 4
    assert (rec.late_frames, rec.estimated_missed_frames) == (0, 0)


def test_a_real_gap_is_counted_also_between_the_first_two_frames(tmp_path):
    """Opening early must not hide frames that were really missed: every interval counts, the first one too."""
    path, rec = _gapped(tmp_path, [0, 30, 40, 50, 100, 110])       # 100 fps: 30 ms first (2 missed), 50 ms (4)
    assert (rec.late_frames, rec.estimated_missed_frames) == (2, 6)
    r = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))["recording"]
    assert (r["late_frames"], r["estimated_missed_frames"]) == (2, 6)
    with Device(DeviceConfig.recording(path)) as cam:
        assert (cam.playback.late_frames, cam.playback.estimated_missed_frames) == (2, 6)


class _Bare:
    """A frame source with no mode: the recorder cannot know the frame size before the first frame."""

    def __init__(self) -> None:
        self.src = SyntheticStereoSource(eye_size=(24, 12))

    def read(self) -> StereoFrame:
        return self.src.read()


def test_without_a_known_size_the_files_open_on_the_first_write(tmp_path):
    bare = _Bare()
    path = tmp_path / "bare.avi"
    with Recorder(path, bare) as rec:
        assert not path.exists() and not rec.csv_path.exists()
        for _ in range(3):
            rec.write(bare.read())
    with Device(DeviceConfig.recording(path)) as cam:
        assert cam.read().left.shape == (12, 24) and cam.playback.frame_count == 3
    with pytest.raises(OSError, match="could not open a video writer"):
        Recorder(tmp_path / "bad.nosuchext", bare).write(bare.read())
    # frames of another size than the mode gives: the file is opened again, at the frames' size, on the first write
    src = SyntheticStereoSource(eye_size=(32, 16))
    path = tmp_path / "other.mkv"
    with Recorder(path, src, codec=LOSSLESS_CODEC) as rec:
        small = [StereoFrame(left=f.left[:8, :20].copy(), right=f.right[:8, :20].copy(), host_ns=f.host_ns,
                             index=f.index) for f in (src.read() for _ in range(4))]
        for f in small:
            rec.write(f)
    assert rec.info["recording"]["eye_size"] == [20, 8] and len(_csv_ns(path)) == 4
    with Device(DeviceConfig.recording(path)) as cam:
        got = [cam.read() for _ in range(4)]
    assert all(np.array_equal(g.left, f.left) and np.array_equal(g.right, f.right) for g, f in zip(got, small))


def test_another_recording_with_the_same_name_is_not_overwritten(tmp_path):
    first, frames, _ = _record(tmp_path, 7, name="take.avi")
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    src = SyntheticStereoSource(eye_size=(64, 32))
    with pytest.raises(FileExistsError) as e:
        Recorder(tmp_path / "take.mkv", src, codec=LOSSLESS_CODEC)
    msg = str(e.value)
    assert "take.avi" in msg and "take.csv" in msg and "take.json" in msg and "take_2.mkv" in msg
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before     # nothing written, nothing replaced
    with Device(DeviceConfig.recording(first)) as cam:
        assert cam.playback.frame_count == 7
    again, _, _ = _record(tmp_path, 3, name="take.avi")             # the same path: recording again replaces it
    assert len(_csv_ns(again)) == 3
    (tmp_path / "set.npz").write_bytes(b"x")                        # capture_dataset_npz.py writes set.npz + set.json
    (tmp_path / "set.json").write_text("{}", encoding="utf-8")
    with pytest.raises(FileExistsError, match="set.npz has the same name"):
        Recorder(tmp_path / "set.avi", src)
    assert (tmp_path / "set.json").read_text(encoding="utf-8") == "{}"


def test_a_recorder_closed_before_any_frame_leaves_no_files(tmp_path):
    src = SyntheticStereoSource(eye_size=(32, 16))
    with Recorder(tmp_path / "none.avi", src):
        assert (tmp_path / "none.avi").is_file()                    # open from the start ...
    assert list(tmp_path.iterdir()) == []                           # ... and gone: an empty video cannot be played


class _TbCap:
    """A VideoCapture stand-in for a top-bottom camera: 40x60 frames (two 40x30 eyes), 100 fps."""

    def isOpened(self): return True

    def get(self, pid):
        import cv2
        return {cv2.CAP_PROP_FRAME_WIDTH: 40.0, cv2.CAP_PROP_FRAME_HEIGHT: 60.0, cv2.CAP_PROP_FPS: 100.0}.get(pid, 0.0)

    def set(self, pid, v): return True

    def read(self): return True, np.arange(60 * 40, dtype=np.uint8).reshape(60, 40)

    def release(self): pass


def test_a_top_bottom_camera_is_recorded_at_its_eye_size_from_the_start(tmp_path, monkeypatch):
    import cv2
    opened = []
    real = cv2.VideoWriter

    def counting(*a, **k):
        opened.append(a[3])                                          # (width, height) of the file
        return real(*a, **k)
    monkeypatch.setattr(cv2, "VideoWriter", counting)
    cfg = DeviceConfig(layout="tb", lr_order="first_is_left", stall_frames=0)
    with Device(cfg, capture=_TbCap()) as cam, Recorder(tmp_path / "tb.mkv", cam, codec=LOSSLESS_CODEC) as rec:
        assert opened == [(80, 30)] and rec.info["recording"]["eye_size"] == [40, 30]   # before any frame
        frames = [cam.read() for _ in range(3)]
        for f in frames:
            rec.write(f)
    assert opened == [(80, 30)]                                      # not opened again on the first frame
    with Device(DeviceConfig.recording(tmp_path / "tb.mkv")) as cam:
        g = cam.read()
    assert np.array_equal(g.left, frames[0].left) and np.array_equal(g.right, frames[0].right)


def test_recording_again_to_the_same_path_with_no_frame_keeps_the_old_recording(tmp_path):
    src = SyntheticStereoSource(eye_size=(32, 16), fps=1000.0)
    path = tmp_path / "recording.avi"
    with Recorder(path, src) as rec:
        for _ in range(5):
            rec.write(src.read())
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    with pytest.raises(RuntimeError):
        with Recorder(path, src):
            raise RuntimeError("camera read failed before the first frame")
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before      # all three back, unchanged
    with Device(DeviceConfig.recording(path)) as cam:
        assert cam.playback.frame_count == 5


def test_recording_again_to_the_same_path_replaces_it_once_a_frame_is_written(tmp_path):
    src = SyntheticStereoSource(eye_size=(32, 16), fps=1000.0)
    path = tmp_path / "recording.avi"
    for n in (5, 3):
        with Recorder(path, src) as rec:
            for _ in range(n):
                rec.write(src.read())
            if n == 3:                                               # the earlier one is set aside meanwhile
                assert (tmp_path / "recording.avi.replaced").is_file()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["recording.avi", "recording.csv", "recording.json"]
    assert len(_csv_ns(path)) == 3
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    with pytest.raises(OSError, match="could not open a video writer"):   # a failed open puts it back too
        Recorder(path, src, codec="ZZZZ")
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


def test_a_recording_over_another_that_was_never_closed_leaves_the_earlier_one_in_replaced_files(tmp_path):
    src = SyntheticStereoSource(eye_size=(32, 16), fps=1000.0)
    path = tmp_path / "take.avi"
    with Recorder(path, src) as rec:
        for _ in range(5):
            rec.write(src.read())
    stopped = Recorder(path, src)                                    # the process stops while recording
    stopped.write(src.read())
    with pytest.raises(FileExistsError, match=r"take\.avi\.replaced is left from recording") as e:
        Recorder(path, src)
    msg = str(e.value)                                               # which files are which, and how to keep either
    assert "take.avi, take.csv and take.json are that unfinished recording" in msg
    assert "delete or move take.avi, take.csv and take.json first" in msg and "delete the .replaced files" in msg
    assert len((tmp_path / "take.csv.replaced").read_text(encoding="utf-8").splitlines()) == 6   # the earlier 5
    stopped.close()                                                  # (here it can still close: the test's files)
    assert not (tmp_path / "take.avi.replaced").exists() and len(_csv_ns(path)) == 1


def test_replaced_files_left_without_the_unfinished_ones_still_stop_a_new_recording(tmp_path):
    # the user did half of the advice (deleted the unfinished files, kept .replaced): a new recording to the same
    # path must not go ahead, or a later one would call that good recording "unfinished"
    path = tmp_path / "take.avi"
    _five_frames(path)
    for name in ("take.avi", "take.csv", "take.json"):
        (tmp_path / name).replace(tmp_path / (name + ".replaced"))
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    with pytest.raises(FileExistsError, match=r"Take \.replaced off the names to get it back"):
        Recorder(path, SyntheticStereoSource(eye_size=(32, 16), fps=1000.0))
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


def _five_frames(path: Path) -> SyntheticStereoSource:
    src = SyntheticStereoSource(eye_size=(32, 16), fps=1000.0)
    with Recorder(path, src) as rec:
        for _ in range(5):
            rec.write(src.read())
    return src


def test_a_failure_while_setting_the_earlier_recording_aside_leaves_it_whole(tmp_path, monkeypatch):
    path = tmp_path / "recording.avi"
    src = _five_frames(path)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    real, calls = Path.replace, []

    def second_fails(self, target):
        calls.append(self.name)
        if len(calls) == 2:
            raise PermissionError(13, "in use by another program", str(self))
        return real(self, target)
    monkeypatch.setattr(Path, "replace", second_fails)
    with pytest.raises(PermissionError):
        Recorder(path, src)
    monkeypatch.undo()
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before      # nothing split, nothing lost


@pytest.mark.skipif(sys.platform != "win32", reason="an open file cannot be moved on Windows only")
def test_recording_again_while_the_csv_is_open_elsewhere_keeps_the_earlier_recording(tmp_path):
    path = tmp_path / "recording.avi"
    src = _five_frames(path)
    with open(tmp_path / "recording.csv", encoding="utf-8"):          # e.g. open in a spreadsheet
        with pytest.raises(OSError):
            Recorder(path, src)
    Recorder(path, src).close()                                       # again, once it is closed: no frame
    assert sorted(p.name for p in tmp_path.iterdir()) == ["recording.avi", "recording.csv", "recording.json"]
    with Device(DeviceConfig.recording(path)) as cam:
        assert cam.playback.frame_count == 5


def test_recording_again_with_frames_of_another_size_reopens_and_replaces_the_earlier_one(tmp_path):
    path = tmp_path / "r.mkv"
    src = SyntheticStereoSource(eye_size=(32, 16), fps=1000.0)
    with Recorder(path, src, codec=LOSSLESS_CODEC) as rec:
        for _ in range(5):
            rec.write(src.read())
    with Recorder(path, src, codec=LOSSLESS_CODEC) as rec:          # mode 32x16, frames 20x8: opened again
        for _ in range(3):
            f = src.read()
            rec.write(StereoFrame(left=f.left[:8, :20].copy(), right=f.right[:8, :20].copy(), host_ns=f.host_ns,
                                  index=f.index))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["r.csv", "r.json", "r.mkv"]
    with Device(DeviceConfig.recording(path)) as cam:
        assert cam.playback.frame_count == 3 and cam.read().left.shape == (8, 20)


def test_after_a_process_stopped_over_a_recording_the_message_gets_the_earlier_one_back(tmp_path):
    path = tmp_path / "x.mkv"
    _five_frames(path)
    _crash(tmp_path, "x.mkv", LOSSLESS_CODEC)                         # records over it and stops without closing
    with pytest.raises(FileExistsError) as e:
        Recorder(path, SyntheticStereoSource(eye_size=(32, 16)))
    assert "delete or move x.mkv, x.csv and x.json first" in str(e.value)
    for name in ("x.mkv", "x.csv", "x.json"):                       # do what it says
        (tmp_path / name).unlink()
    for q in tmp_path.glob("*.replaced"):
        q.rename(q.with_name(q.name[: -len(".replaced")]))
    with Device(DeviceConfig.recording(path)) as cam:
        assert cam.playback.frame_count == 5
