"""calib.check: a file that fits gives a small epipolar distance, another unit's file a large one, swapped eyes flip
the disparity sign, and a scene without texture is reported as too few matches. No camera: the frames are a
textured plane rendered through a known calibration (synthetic.SyntheticPlaneSource), so "fits" is known exactly."""
import math

import numpy as np
import pytest

pytest.importorskip("opticmix_vision._core")
from opticmix_vision import calib, device, synthetic  # noqa: E402
from opticmix_vision.calibration import (  # noqa: E402
    CameraIntrinsics, StereoCalibration, StereoExtrinsics, save_calibration,
)
from opticmix_vision.device import DeviceError, StereoFrame  # noqa: E402


def _rot_x(deg):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])


def _calib(fx=255.0, cx=399.5, cy=399.5, pitch=0.0, serial="S1-0001"):
    left = CameraIntrinsics(K=[[fx, 0, cx], [0, fx, cy], [0, 0, 1]], D=[-0.01, 0.002, 0.0, 0.0], size=(800, 800))
    right = CameraIntrinsics(K=[[256.0, 0, 401.0], [0, 255.5, 398.0], [0, 0, 1]], D=[-0.012, 0.002, 0.0, 0.0],
                             size=(800, 800))
    return StereoCalibration(left=left, right=right,
                             extrinsics=StereoExtrinsics(R=_rot_x(pitch) @ _rot_x(0.3), t=[-64.0, 0.4, -0.2]),
                             device={"serial": serial})


TRUTH = _calib()
OTHER = _calib(fx=265.0, cx=409.5, cy=391.5, pitch=1.0, serial="S1-0002")   # another unit
OTHER2 = _calib(fx=250.0, cx=392.5, cy=405.5, pitch=-0.7, serial="S1-0003")


@pytest.fixture(scope="module")
def pairs():
    src = synthetic.SyntheticPlaneSource(TRUTH, seed=1)
    return [(f.left, f.right) for f in (src.read() for _ in range(6))]


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(calib, "store_dir", lambda: tmp_path / "store")
    return tmp_path / "store"


def test_matching_file_is_small(pairs):
    r = calib.check_pairs(pairs, TRUTH)
    assert r.enough and r.pairs == 6 and r.points >= calib.MIN_POINTS, r
    assert r.median_px < 0.5 and r.frac_le_1px > 0.8, r
    assert r.disparity_positive > 0.95, r


def test_other_units_file_is_large(pairs):
    good, bad = calib.check_pairs(pairs, TRUTH), calib.check_pairs(pairs, OTHER)
    assert bad.enough, bad
    assert bad.median_px > 3.0 and bad.median_px > 10 * good.median_px, (good, bad)
    assert bad.frac_le_1px < 0.3, bad
    assert "compare its serial with the label" in bad.summary()


def test_compare_pairs_puts_the_matching_file_first(pairs):
    res = calib.compare_pairs(pairs, {"S1-0002": OTHER, "S1-0001": TRUTH, "S1-0003": OTHER2})
    assert list(res) == ["S1-0002", "S1-0001", "S1-0003"]
    assert min(res, key=lambda k: res[k].median_px) == "S1-0001", res
    assert res["S1-0001"] == calib.check_pairs(pairs, TRUTH)      # same numbers as one at a time


def test_swapped_eyes_flip_the_disparity_sign(pairs):
    r = calib.check_pairs([(right, left) for left, right in pairs], TRUTH)
    assert r.enough and r.disparity_positive < 0.1, r
    assert "look swapped" in r.summary() and "look at the file first" in r.summary()
    assert "look swapped" not in calib.check_pairs(pairs, TRUTH).summary()


class _Flat:
    """A camera looking at a blank wall."""
    def read(self):
        img = np.full((800, 800), 128, np.uint8)
        return StereoFrame(left=img, right=img.copy(), host_ns=0, index=0)


def test_no_texture_is_too_few_to_tell():
    r = calib.check(_Flat(), TRUTH, frames=6, every=1)
    assert not r.enough and r.pairs == 0 and r.points == 0 and math.isnan(r.median_px), r
    s = r.summary()
    assert s.startswith("Too few matches to tell") and "more texture" in s and "median" not in s


def test_too_few_pairs_asks_for_more_frames(pairs):
    r = calib.check_pairs(pairs[:3], TRUTH)
    assert r.points >= calib.MIN_POINTS and r.pairs == 3 and not r.enough
    assert "--frames 5 or more" in r.summary() and "more texture" not in r.summary()


def test_frames_below_min_pairs_is_refused(capsys):
    with pytest.raises(SystemExit) as e:
        calib.main(["check", "--synthetic", "--frames", "3"])
    assert e.value.code == 2 and "at least 5" in capsys.readouterr().err


def test_summary_carries_reference_and_no_verdict(pairs):
    s = calib.check_pairs(pairs, TRUTH).summary()
    assert "0.44 px" in s and "30.4 px" in s and "not a precision figure" in s and "no pass/fail threshold" in s
    for word in ("PASS", "FAIL", "pass:", "fail:"):
        assert word not in s


def test_check_reads_everything_before_matching(monkeypatch):
    src = synthetic.SyntheticPlaneSource(TRUTH, seed=2)
    reads = []
    real_read, real_match = src.read, calib._match
    src.read = lambda: reads.append(1) or real_read()
    seen = []
    monkeypatch.setattr(calib, "_match", lambda L, R: seen.append(len(reads)) or real_match(L, R))
    r = calib.check(src, frames=3, every=2)            # calibration from src.calibration, like Device.calibration
    assert len(reads) == calib._WARMUP + 3 * 2
    assert seen == [len(reads)] * 3                     # every match after the last read
    assert r.pairs == 3


def test_eye_size_mismatch_is_refused():
    small = np.zeros((400, 400), np.uint8)
    with pytest.raises(ValueError, match="for 800x800 eyes; the frames are 400x400. Open the 1600x800 mode"):
        calib.check_pairs([(small, small)], TRUTH)
    with pytest.raises(ValueError, match="at least 1"):
        calib.check(_Flat(), TRUTH, frames=0)


def test_cli_synthetic_runs_without_camera(capsys, tmp_path):
    assert calib.main(["check", "--synthetic", "--frames", "5"]) == 0
    out = capsys.readouterr().out
    assert "synthetic" in out and "epipolar distance: median" in out and "30.4 px" in out
    other = tmp_path / "other.json"
    save_calibration(OTHER, other)
    assert calib.main(["check", "--synthetic", "--frames", "5", "--file", str(other)]) == 0
    out = capsys.readouterr().out
    assert "calibration: S1-0002 (other.json)" in out
    median = float(out.split("median ")[1].split(" px")[0])
    assert median > 3.0, out
    assert calib.main(["check", "--synthetic", "--frames", "5", "--file", str(other), "--file", str(other)]) == 0
    assert "Comparing 2 files on the same 5 pairs" in capsys.readouterr().out     # same serial twice: both rows


def test_cli_compares_every_installed_file(store, capsys):
    gen = calib._synthetic_calibration()
    store.mkdir(parents=True)
    for serial, c in (("S1-0011", OTHER), ("S1-0010", gen), ("S1-0012", OTHER2)):
        save_calibration(StereoCalibration(left=c.left, right=c.right, extrinsics=c.extrinsics,
                                           device={"serial": serial}), store / f"{serial}.json")
    assert calib.main(["check", "--synthetic", "--all", "--frames", "5"]) == 0
    out = capsys.readouterr().out
    assert "Comparing 3 files on the same 5 pairs" in out
    assert "Smallest median: S1-0010." in out and "not necessarily this camera's file" in out
    assert "0.44 px" in out
    with pytest.raises(calib.CalibLookupError, match="calib check --all"):
        calib.resolve()


def test_cli_too_few_exits_2(monkeypatch, capsys):
    class FlatSource(_Flat):
        def __init__(self, calibration, seed=0):
            self.calibration = calibration
    monkeypatch.setattr(synthetic, "SyntheticPlaneSource", FlatSource)
    assert calib.main(["check", "--synthetic", "--frames", "5"]) == 2
    assert "Too few matches to tell" in capsys.readouterr().out


def test_cli_without_camera_says_what_to_do(monkeypatch, store, capsys):
    def missing(cls, **kw):
        raise DeviceError("Weevia S1 ('WN Dual Camera') not found. Cameras Windows reported: []")
    monkeypatch.setattr(device.DeviceConfig, "weevia_s1", classmethod(missing))
    assert calib.main(["check"]) == 1
    err = capsys.readouterr().err
    assert "not found" in err and "Connect the camera" in err and "--pairs-dir" in err


def _save_png(path, img):
    import cv2
    cv2.imencode(".png", img)[1].tofile(str(path))


def test_pairs_dir_reads_both_naming_styles(pairs, tmp_path, capsys):
    d = tmp_path / "saved_é"                               # a non-ASCII folder name, as on many desktops
    d.mkdir()
    for i, (left, right) in enumerate(pairs):
        if i % 2:
            _save_png(d / f"left_{i:06d}.png", left); _save_png(d / f"right_{i:06d}.png", right)   # save_stereo_pairs
        else:
            _save_png(d / f"{i:03d}_left.png", left); _save_png(d / f"{i:03d}_right.png", right)   # viewer
    loaded = calib.load_pairs(d)
    assert len(loaded) == 6 and all(a.ndim == 2 for a, _ in loaded)
    truth = tmp_path / "truth.json"
    save_calibration(TRUTH, truth)
    assert calib.main(["check", "--pairs-dir", str(d), "--file", str(truth)]) == 0
    out = capsys.readouterr().out
    assert float(out.split("median ")[1].split(" px")[0]) < 0.5, out
    empty = tmp_path / "empty"
    empty.mkdir()
    assert calib.main(["check", "--pairs-dir", str(empty), "--file", str(truth)]) == 1
    assert "No left/right PNG pairs" in capsys.readouterr().err
