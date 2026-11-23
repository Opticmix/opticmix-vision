"""The examples run to the end with --synthetic: no camera, no window. Each runs in its own process."""

from __future__ import annotations

import csv
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


def test_list_controls_writes_nothing(tmp_path):
    assert "control writes by this process: 0" in run(tmp_path, "list_controls.py", "--synthetic")


def test_save_stereo_pairs_writes_pngs_and_csv(tmp_path):
    run(tmp_path, "save_stereo_pairs.py", "--synthetic", "--headless", "--every", "5", "--count", "3")
    (run_dir,) = (tmp_path / "captures").iterdir()
    rows = list(csv.DictReader(open(run_dir / "pairs.csv", encoding="utf-8")))
    assert len(rows) == 3
    for row in rows:
        assert (run_dir / row["left"]).is_file() and (run_dir / row["right"]).is_file()


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


def test_dataset_npz_then_torch_dataloader(tmp_path):
    run(tmp_path, "capture_dataset_npz.py", "--synthetic", "--frames", "10", "--out", "d.npz")
    with np.load(tmp_path / "d.npz") as z:
        assert z["left"].shape == z["right"].shape and z["left"].shape[0] == 10
        assert z["left"].dtype == np.uint8 and z["host_ns"].shape == (10,)
        assert np.all(np.diff(z["host_ns"]) > 0)
    pytest.importorskip("torch")
    out = run(tmp_path, "torch_dataloader.py", "d.npz", "--batch-size", "4")
    assert "10 frames in 3 batches" in out


def test_list_devices_counts_two_weevia(tmp_path):
    out = run(tmp_path, "list_devices.py", "--synthetic")
    assert "3 video device(s)" in out and "found: 2" in out and "cannot be told apart" in out


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
