"""viewer — 설치 없이 카메라 확인용 뷰어 (합성 소스로 헤드리스 검증).

실행:
    cd sdk/python && python -m pytest tests/test_viewer.py -v
"""

from __future__ import annotations

import pytest

from pathlib import Path

import numpy as np

from opticmix_vision.synthetic import SyntheticStereoSource
from opticmix_vision.viewer import ViewerState, compose_view, main, run_viewer


# rectify and depth are not in the published package — they have never been run
# against the camera. Inside the monorepo the files are here and these run; from
# an installed release they skip, because the feature they cover is not shipped.


def _needs_stereo():
    import importlib.util
    missing = [m for m in ("rectify", "depth")
               if importlib.util.find_spec(f"opticmix_vision.{m}") is None]
    return pytest.mark.skipif(
        bool(missing),
        reason=f"not in this build (never run against the camera): {', '.join(missing)}",
    )


needs_stereo = _needs_stereo()


def test_synthetic_source_yields_stereo_frames_with_physical_disparity():
    src = SyntheticStereoSource(eye_size=(160, 100), fps=100.0, disparity=6)
    f0 = src.read()
    f1 = src.read()
    assert f0.left.shape == (100, 160) and f0.right.shape == (100, 160)
    assert f0.left.dtype == np.uint8
    assert f1.index == f0.index + 1 and f1.host_ns > f0.host_ns
    # 물리 규약: R(x) = L(x + d)  (왼쪽 카메라가 같은 점을 더 오른쪽에 본다)
    row = 50
    assert np.array_equal(f0.right[row, 10:100], f0.left[row, 16:106])
    src.close()


def test_synthetic_source_has_uvc_like_props():
    src = SyntheticStereoSource(eye_size=(64, 32))
    v = src.get_prop("CAP_PROP_BACKLIGHT")
    assert src.set_prop("CAP_PROP_BACKLIGHT", v + 10) is True
    assert src.get_prop("CAP_PROP_BACKLIGHT") == v + 10
    assert src.mode.width == 128 and src.mode.height == 32          # sbs 프레임 크기


def test_compose_view_puts_left_right_side_by_side_with_overlay():
    src = SyntheticStereoSource(eye_size=(160, 100))
    f = src.read()
    view = compose_view(f, texts=["fps 90.0", "LED 32"], gap=4)
    assert view.dtype == np.uint8 and view.ndim == 3 and view.shape[2] == 3
    assert view.shape[0] >= 100 and view.shape[1] == 160 * 2 + 4


def test_keys_adjust_props_and_signal_actions():
    src = SyntheticStereoSource(eye_size=(64, 32))
    st = ViewerState()
    led0 = src.get_prop("CAP_PROP_BACKLIGHT")
    assert st.handle_key(ord("L"), src) is None
    assert src.get_prop("CAP_PROP_BACKLIGHT") == led0 + st.led_step
    assert st.handle_key(ord("l"), src) is None
    assert src.get_prop("CAP_PROP_BACKLIGHT") == led0
    exp0 = src.get_prop("CAP_PROP_EXPOSURE")
    st.handle_key(ord("E"), src)
    assert src.get_prop("CAP_PROP_EXPOSURE") == exp0 + st.exposure_step
    assert st.handle_key(ord("s"), src) == "save"
    assert st.handle_key(ord("r"), src) is None and st.rectify is True
    assert st.handle_key(ord("q"), src) == "quit"
    assert st.handle_key(27, src) == "quit"


def test_headless_run_saves_pairs_and_stops_on_quit(tmp_path: Path):
    src = SyntheticStereoSource(eye_size=(64, 32), fps=1000.0)
    stats = run_viewer(src, headless=True, out_dir=tmp_path,
                       key_feed=[ord("s"), -1, ord("s"), ord("q")])
    assert stats["frames"] == 4
    assert stats["saved"] == 2
    files = sorted(p.name for p in tmp_path.glob("*.png"))
    assert len(files) == 4                                   # 쌍당 left/right 2장
    assert any("left" in n for n in files) and any("right" in n for n in files)


def test_headless_run_stops_at_max_frames(tmp_path: Path):
    src = SyntheticStereoSource(eye_size=(64, 32), fps=1000.0)
    stats = run_viewer(src, headless=True, max_frames=7)
    assert stats["frames"] == 7 and stats["saved"] == 0


@needs_stereo
def test_save_writes_raw_frames_even_when_rectify_display_is_on(tmp_path: Path):
    """감사 R2: 's' 저장은 캘리브 입력용이므로 화면에 rectify 를 켜 두었어도 **원본**을 저장해야 한다.
    정류된 영상으로 캘리브하면 조용히 틀린 K/D 가 나온다."""
    import cv2
    from opticmix_vision.calibration import CameraIntrinsics, StereoCalibration, StereoExtrinsics, synthetic_equidistant
    eye = (96, 48)
    intr = synthetic_equidistant(eye, fov_deg=120.0)
    calib = StereoCalibration(left=intr, right=intr, extrinsics=StereoExtrinsics(R=np.eye(3), t=[-64.0, 0.0, 0.0]))
    src = SyntheticStereoSource(eye_size=eye, fps=1000.0, seed=3)
    # 첫 프레임(index 0)을 저장한다: r(rectify 켜기) → s(저장) → q
    stats = run_viewer(src, headless=True, out_dir=tmp_path, calib=calib, key_feed=[ord("r"), ord("s"), ord("q")])
    assert stats["saved"] == 1
    saved_left = cv2.imread(str(next(tmp_path.glob("*_left.png"))), cv2.IMREAD_GRAYSCALE)
    # 저장된 프레임 = 두 번째 read (index 1): 1번째 read 뒤 'r', 2번째 read 뒤 's'. 같은 seed 로 재생해 원본을 만든다.
    raw = SyntheticStereoSource(eye_size=eye, fps=1000.0, seed=3)
    raw.read()
    f1 = raw.read()
    assert np.array_equal(saved_left, f1.left), "저장본이 원본 프레임과 다르다 — rectify 된 영상이 저장됐을 가능성"


# ---------------------------------------------------------------- 감사 R3 (2026-09-07) ----

def test_save_to_non_ascii_path_actually_writes_files(tmp_path: Path):
    """R3 치명 4: cv2.imwrite 는 한글 경로에서 False 를 돌려주는데 뷰어는 saved 를 올렸다 (파일 0장)."""
    out = tmp_path / "캡처 폴더"
    src = SyntheticStereoSource(eye_size=(64, 32), fps=1000.0)
    stats = run_viewer(src, headless=True, out_dir=out, key_feed=[ord("s"), ord("s"), ord("q")])
    assert stats["saved"] == 2
    assert len(list(out.glob("*.png"))) == 4


def test_save_failure_is_counted_as_error_not_as_saved(tmp_path: Path):
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x", encoding="utf-8")                # 디렉토리 자리에 파일이 있다
    src = SyntheticStereoSource(eye_size=(64, 32), fps=1000.0)
    stats = run_viewer(src, headless=True, out_dir=blocker, key_feed=[ord("s"), ord("q")])
    assert stats["saved"] == 0
    assert "저장" in stats["last_error"] or "save" in stats["last_error"].lower()


def test_headless_stops_when_key_feed_is_exhausted_without_max_frames():
    """R3 경: key_feed 가 끝나고 max_frames 도 없으면 무한 루프였다."""
    src = SyntheticStereoSource(eye_size=(64, 32), fps=1000.0)
    stats = run_viewer(src, headless=True, key_feed=[-1, -1, -1])
    assert stats["frames"] == 3


def test_cli_synthetic_headless(tmp_path: Path):
    rc = main(["--synthetic", "--headless", "--frames", "5", "--out", str(tmp_path)])
    assert rc == 0


# ---------------------------------------------------------------------------
# 'd' 라이브 깊이 (2026-09-08) — 온보드 깊이 엔진이 없으므로 뷰어에서 바로 보여 준다
# ---------------------------------------------------------------------------

def _calib_for(eye: tuple[int, int], baseline_mm: float = 64.0):
    from opticmix_vision.calibration import StereoCalibration, StereoExtrinsics, synthetic_equidistant
    intr = synthetic_equidistant(eye, fov_deg=120.0)
    return StereoCalibration(left=intr, right=intr,
                             extrinsics=StereoExtrinsics(R=np.eye(3), t=[-baseline_mm, 0.0, 0.0]))


def test_depth_key_requires_calibration_and_says_so():
    src = SyntheticStereoSource(eye_size=(64, 32))
    st = ViewerState()
    assert st.handle_key(ord("d"), src) is None
    assert st.depth is False                         # 캘리브 없이 켜지면 안 된다
    assert any("calib" in m or "캘리브" in m for m in st.messages)
    src.close()


def test_depth_key_toggles_when_calibration_is_available():
    src = SyntheticStereoSource(eye_size=(64, 32))
    st = ViewerState(has_calib=True)
    st.handle_key(ord("d"), src)
    assert st.depth is True
    st.handle_key(ord("D"), src)
    assert st.depth is False
    src.close()


@needs_stereo
def test_headless_depth_run_reports_valid_fraction_and_does_not_crash():
    eye = (128, 96)
    src = SyntheticStereoSource(eye_size=eye, fps=1000.0, disparity=8, seed=5)
    stats = run_viewer(src, headless=True, calib=_calib_for(eye),
                       key_feed=[ord("d"), -1, -1, ord("q")],
                       depth_params={"num_disparities": 32, "block_size": 5})
    assert stats["frames"] == 4
    assert stats["last_error"] == ""
    assert 0.0 <= stats["depth_valid_fraction"] <= 1.0

@needs_stereo
def test_saving_while_depth_is_on_still_writes_raw_frames(tmp_path: Path):
    """'s' 는 캘리브 입력용이다 — 깊이 표시 중에도 저장은 원본이어야 한다(R2 와 같은 함정)."""
    import cv2
    eye = (96, 64)
    src = SyntheticStereoSource(eye_size=eye, fps=1000.0, seed=7)
    run_viewer(src, headless=True, out_dir=tmp_path, calib=_calib_for(eye),
               key_feed=[ord("d"), ord("s"), ord("q")],
               depth_params={"num_disparities": 32, "block_size": 5})
    saved = cv2.imread(str(next(tmp_path.glob("*_left.png"))), cv2.IMREAD_GRAYSCALE)
    # 저장된 것은 두 번째 read(index 1) — 1번째 read 뒤 'd', 2번째 read 뒤 's'. 같은 seed 로 원본을 재생한다.
    raw = SyntheticStereoSource(eye_size=eye, fps=1000.0, seed=7)
    raw.read()
    f1 = raw.read()
    assert saved.ndim == 2, "깊이 컬러맵이 저장됐다 — 저장은 원본이어야 한다"
    assert np.array_equal(saved, f1.left)
