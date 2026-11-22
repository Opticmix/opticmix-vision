"""viewer — 설치 없이 카메라 확인용 뷰어 (합성 소스로 헤드리스 검증).

실행:
    python -m pytest tests/test_viewer.py -v
"""

from __future__ import annotations

import pytest

from pathlib import Path

import numpy as np

from opticmix_vision.synthetic import SyntheticStereoSource
from opticmix_vision.viewer import ViewerState, compose_view, main, run_viewer



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
    st.commit(src, force=True)                         # 키 쓰기는 멈춘 뒤 한 번 — 여기선 바로 적용
    src.controls.flush(timeout=5)
    assert src.get_prop("CAP_PROP_BACKLIGHT") == led0 + st.led_step
    assert st.handle_key(ord("l"), src) is None
    st.commit(src, force=True)
    src.controls.flush(timeout=5)
    assert src.get_prop("CAP_PROP_BACKLIGHT") == led0
    exp0 = src.get_prop("CAP_PROP_EXPOSURE")
    st.handle_key(ord("E"), src)
    st.commit(src, force=True)
    src.controls.flush(timeout=5)
    assert src.get_prop("CAP_PROP_EXPOSURE") == exp0 + st.exposure_step
    assert st.handle_key(ord("s"), src) == "save"
    assert st.handle_key(ord("q"), src) == "quit"
    assert st.handle_key(27, src) == "quit"


def test_exposure_keys_say_why_they_do_nothing_while_auto_exposure_is_on():
    src = SyntheticStereoSource(eye_size=(64, 32))
    st = ViewerState()
    st.handle_key(ord("a"), src)                       # 수동 → 자동
    src.controls.flush(timeout=5)
    assert src.controls.get("auto_exposure") == "auto"
    exp0 = src.controls.get("exposure")
    st.handle_key(ord("E"), src)
    assert "press a" in st.messages[-1]
    assert src.controls.get("exposure") == exp0


def test_holding_a_key_writes_once_after_the_presses_stop():
    """쓰기 간격 12 ms(2026-09-28)에서는 키 반복(초당 ~30회)이 그대로 쓰기가 된다 — 멈춘 뒤 누적값으로 한 번."""
    src = SyntheticStereoSource(eye_size=(64, 32))
    st = ViewerState()
    be = src._sim
    led0 = src.controls.get("backlight_compensation")
    n0 = len(be.writes)
    for i in range(5):
        st.handle_key(ord("L"), src, now=10.0 + 0.03 * i)      # 키 반복 30 ms
    st.commit(src, now=10.12 + 0.3)                           # 멈춘 지 0.3 s — 아직
    src.controls.flush(timeout=5)
    assert len(be.writes) == n0
    st.commit(src, now=10.12 + 0.7)                           # 0.6 s 넘게 조용 — 한 번
    src.controls.flush(timeout=5)
    assert be.writes[n0:] == [("backlight_compensation", led0 + 5 * int(st.led_step), None)]


def test_quitting_applies_a_change_still_waiting():
    src = SyntheticStereoSource(eye_size=(64, 32))
    st = ViewerState()
    led0 = src.controls.get("backlight_compensation")
    st.handle_key(ord("L"), src, now=1.0)
    assert st.handle_key(ord("q"), src, now=1.1) == "quit"
    src.controls.flush(timeout=5)
    assert src.controls.get("backlight_compensation") == led0 + int(st.led_step)


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


# ---------------------------------------------------------------- 한글 경로 (2026-09-07) ----

def test_save_to_non_ascii_path_actually_writes_files(tmp_path: Path):
    """cv2.imwrite 는 한글 경로에서 False 를 돌려주는데 뷰어는 saved 를 올렸다 (파일 0장)."""
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
    """key_feed 가 끝나고 max_frames 도 없으면 무한 루프였다."""
    src = SyntheticStereoSource(eye_size=(64, 32), fps=1000.0)
    stats = run_viewer(src, headless=True, key_feed=[-1, -1, -1])
    assert stats["frames"] == 3


def test_cli_synthetic_headless(tmp_path: Path):
    rc = main(["--synthetic", "--headless", "--frames", "5", "--out", str(tmp_path)])
    assert rc == 0
