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
