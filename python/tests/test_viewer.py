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


# ---------------------------------------------------------------- recording and playback ----

def _avi_frames(path: Path) -> tuple[int, int]:
    import cv2
    cap = cv2.VideoCapture(str(path))
    n, width = 0, 0
    while True:
        ok, img = cap.read()
        if not ok:
            break
        n, width = n + 1, img.shape[1]
    cap.release()
    return n, width


def _csv_rows(path: Path) -> list[list[str]]:
    import csv
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.reader(fh))


def test_v_records_both_eyes_and_a_csv_with_the_same_frame_count(tmp_path: Path):
    src = SyntheticStereoSource(eye_size=(64, 32), fps=1000.0)
    stats = run_viewer(src, headless=True, out_dir=tmp_path,
                       key_feed=[ord("v"), -1, -1, -1, ord("v"), ord("q")])
    assert stats["frames"] == 6 and stats["last_error"] == ""
    (avi,) = tmp_path.glob("*.avi")
    rows = _csv_rows(avi.with_suffix(".csv"))
    assert rows[0] == ["frame", "host_ns", "driver_ns", "driver_clock"]
    # frames 2-4: v opens the files and the recording starts with the next frame; the second v stops before frame 5
    assert [r[0] for r in rows[1:]] == ["0", "1", "2"]
    assert _avi_frames(avi) == (3, 2 * 64)                          # left | right in one frame


def test_quitting_while_recording_closes_the_file(tmp_path: Path):
    src = SyntheticStereoSource(eye_size=(64, 32), fps=1000.0)
    st = ViewerState()
    run_viewer(src, headless=True, out_dir=tmp_path, state=st, key_feed=[ord("v"), -1, ord("q")])
    (avi,) = tmp_path.glob("*.avi")
    assert _avi_frames(avi)[0] == len(_csv_rows(avi.with_suffix(".csv"))) - 1 == 1
    assert st.messages[-1].startswith("recorded 1 frames")


def test_recording_failure_is_reported_and_the_viewer_keeps_going(tmp_path: Path, capsys):
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x", encoding="utf-8")
    src = SyntheticStereoSource(eye_size=(64, 32), fps=1000.0)
    stats = run_viewer(src, headless=True, out_dir=blocker, key_feed=[ord("v"), -1, ord("q")])
    assert stats["frames"] == 3
    assert stats["last_error"].startswith("recording failed")
    assert "recording failed" in capsys.readouterr().err


def test_cli_plays_a_recording_to_its_end(tmp_path: Path, capsys):
    src = SyntheticStereoSource(eye_size=(64, 32), fps=1000.0)
    run_viewer(src, headless=True, out_dir=tmp_path, key_feed=[ord("v"), -1, -1, -1, -1, ord("v")])
    (avi,) = tmp_path.glob("*.avi")
    rc = main(["--recording", str(avi), "--headless", "--out", str(tmp_path / "out")])
    assert rc == 0
    assert "[viewer] frames 4 " in capsys.readouterr().out      # every recorded frame, then a quiet end


def test_recording_option_refuses_another_source(tmp_path: Path):
    with pytest.raises(SystemExit):
        main(["--recording", str(tmp_path / "x.avi"), "--synthetic", "--headless"])


# ---------------------------------------------------------------- capture info, lossless, playback keys ----

def test_s_writes_the_capture_info_next_to_the_pair(tmp_path: Path):
    import json
    src = SyntheticStereoSource(eye_size=(64, 32), fps=1000.0)
    run_viewer(src, headless=True, out_dir=tmp_path, key_feed=[-1, ord("s"), ord("q")])
    (js,) = tmp_path.glob("*.json")
    info = json.loads(js.read_text(encoding="utf-8"))
    assert info["mode"] == src.mode.to_dict() and info["controls"] == src.controls.snapshot()
    assert info["frame"]["index"] == 1
    assert (tmp_path / info["frame"]["left"]).is_file() and (tmp_path / info["frame"]["right"]).is_file()


def test_v_with_lossless_records_ffv1_with_its_json(tmp_path: Path):
    import json
    src = SyntheticStereoSource(eye_size=(64, 32), fps=1000.0)
    run_viewer(src, headless=True, out_dir=tmp_path, lossless=True, key_feed=[ord("v"), -1, -1, ord("v")])
    (mkv,) = tmp_path.glob("*.mkv")
    r = json.loads(mkv.with_suffix(".json").read_text(encoding="utf-8"))["recording"]
    assert r["codec"] == "FFV1" and r["compressed"] is False and r["frames"] == 2


def _recorded(tmp_path: Path, n: int = 30) -> Path:
    src = SyntheticStereoSource(eye_size=(64, 32), fps=100.0)
    run_viewer(src, headless=True, out_dir=tmp_path, key_feed=[ord("v"), *([-1] * n), ord("v")])   # n frames
    (avi,) = tmp_path.glob("*.avi")
    return avi


def test_playback_keys_pause_step_jump_and_change_speed(tmp_path: Path):
    from opticmix_vision import Device, DeviceConfig
    avi = _recorded(tmp_path)
    seen: list[int] = []

    def view(frame, state):
        seen.append(frame.index)
        return frame.left, frame.right, []

    with Device(DeviceConfig.recording(avi)) as cam:
        pb = cam.playback
        keys = [-1, -1, ord(" "), -1, ord("."), ord("."), ord(","), ord(" "), -1, ord("+"), ord("-"), ord("-"),
                ord("]")]
        st = ViewerState()
        run_viewer(cam, headless=True, key_feed=keys, view=view, state=st)
        # space pauses on frame 2; . . , step to 3, 4, 3; space plays on; ] jumps one second ahead
        assert seen[:10] == [0, 1, 2, 2, 2, 3, 4, 3, 4, 5]
        assert pb.speed == 0.5 and "speed 0.5x" in st.messages
        assert pb.position == pb.seek(seconds=pb.time_s(seen[-1]) + 1.0)
        assert not pb.paused


def test_overlay_shows_frame_statistics_and_hides_led_for_a_recording(tmp_path: Path):
    from opticmix_vision import Device, DeviceConfig
    from opticmix_vision.viewer import _overlay
    src = SyntheticStereoSource(eye_size=(64, 32), fps=1000.0)
    f = src.read(); f = src.read()
    lines = _overlay(f, src, ViewerState(), None, 50.0, f.left, [])
    assert lines[0].startswith("frame 1  fps") and "late 0" in lines[0] and "missed~0" in lines[0]
    assert any(t.startswith("LED ") for t in lines)
    avi = _recorded(tmp_path, 10)
    with Device(DeviceConfig.recording(avi)) as cam:
        cam.read()
        cam.playback.pause()
        g = cam.read()
        lines = _overlay(g, cam, ViewerState(), None, 30.0, g.left, [])
    assert lines[0].startswith("PLAY 0/9") and "PAUSED" in lines[0] and "1x" in lines[0]
    assert not any(t.startswith("LED ") for t in lines)


def test_cli_loops_a_recording(tmp_path: Path, capsys):
    avi = _recorded(tmp_path, 4)
    assert main(["--recording", str(avi), "--loop", "--headless", "--frames", "10"]) == 0
    assert "[viewer] frames 10 " in capsys.readouterr().out


def test_led_changed_while_recording_is_in_the_json(tmp_path: Path, monkeypatch):
    """LED 32 -> 56 with the keys while recording: the JSON lists each change and the value at the end."""
    import json
    from opticmix_vision import viewer
    monkeypatch.setattr(viewer, "SETTLE_S", 0.0)                 # write on the next frame instead of after 0.6 s
    src = SyntheticStereoSource(eye_size=(64, 32), fps=1000.0)
    led0 = src.controls.get("backlight_compensation")
    run_viewer(src, headless=True, out_dir=tmp_path, key_feed=[ord("v"), ord("L"), ord("L"), ord("L"), -1, ord("v")])
    (js,) = tmp_path.glob("*.json")
    r = json.loads(js.read_text(encoding="utf-8"))
    assert r["controls"]["backlight_compensation"] == led0
    changes = r["recording"]["control_changes"]
    assert [(c["frame"], c["name"], c["value"]) for c in changes] == [
        (0, "backlight_compensation", led0 + 8), (1, "backlight_compensation", led0 + 16),
        (2, "backlight_compensation", led0 + 24)]
    assert r["recording"]["controls_at_end"]["backlight_compensation"] == led0 + 24



class _LaggingControls:
    """Controls whose queued writes have not run: get() reads the device at once, without the writes still
    queued (as Controls.get does), and result() is None until apply() runs them; then 'written' with the value
    read back right after the write (WriteResult.readback). gets counts the device reads."""

    def __init__(self, **values) -> None:
        self.device, self.queue, self.n, self.done, self.gets = dict(values), [], 0, {}, 0

    def get(self, name):
        self.gets += 1
        return self.device[name]

    def set(self, name, value):
        from types import SimpleNamespace
        self.n += 1
        self.queue.append((self.n, name, value))
        return SimpleNamespace(status="pending", ticket=self.n)

    def result(self, ticket):
        return self.done.get(ticket)

    def load_preset(self, path, force=False, timeout=None):        # a preset that sets the LED to 10
        from types import SimpleNamespace
        r = self.set("backlight_compensation", 10)
        x = SimpleNamespace(name="backlight_compensation", requested=10, status="pending", ticket=r.ticket,
                            error=None, kept=None, readback=None)
        return SimpleNamespace(results=[x], skipped_mono=[])

    def apply(self, **kept):
        """Runs the queued writes; kept: a value the camera keeps instead of the one written."""
        from types import SimpleNamespace
        for t, name, v in self.queue:
            self.device[name] = kept.get(name, v)
            self.done[t] = SimpleNamespace(status="written", readback=self.device[name])
        self.queue.clear()


def test_quick_presses_go_on_from_a_write_still_queued():
    """Three L presses, each written before the next (SETTLE_S passed), while the writes are still queued: each
    step goes on from the last target, not from the device's old value (the flaky LED test, made certain)."""
    from types import SimpleNamespace
    dev = SimpleNamespace(controls=_LaggingControls(backlight_compensation=32))
    st = ViewerState()
    for i in range(3):
        st.handle_key(ord("L"), dev, now=10.0 * i)
        st.commit(dev, now=10.0 * i + 1.0)
    assert [v for _, v, _ in st._written] == [40, 48, 56]
    dev.controls.apply(backlight_compensation=50)        # the writes ran and the camera kept another value
    st.handle_key(ord("l"), dev, now=100.0)
    st.commit(dev, now=101.0)
    assert st._written[-1][1] == 42


def test_a_key_right_after_a_preset_goes_on_from_the_preset_value(capsys):
    from types import SimpleNamespace
    dev = SimpleNamespace(controls=_LaggingControls(backlight_compensation=32))
    st = ViewerState()
    st._preset, st._preset_label = Path("bench.json"), "bench"
    st.handle_key(ord("P"), dev, now=0.0)                        # the preset's LED 10 is still queued
    st.handle_key(ord("L"), dev, now=1.0)
    st.commit(dev, now=5.0)
    assert st._written[-1][:2] == ("backlight_compensation", 18)


def test_a_twice_quickly_toggles_back_and_exposure_keys_see_the_new_mode():
    from types import SimpleNamespace
    dev = SimpleNamespace(controls=_LaggingControls(auto_exposure="auto", exposure=-6))
    st = ViewerState()
    st.handle_key(ord("a"), dev, now=0.0)                        # auto -> manual, still queued
    st.handle_key(ord("E"), dev, now=0.1)                        # manual now: the key is not refused
    assert "exposure" in st._pending and not any("auto-exposure is on" in m for m in st.messages)
    st.handle_key(ord("a"), dev, now=0.2)                        # manual -> auto
    assert [v for n, v, _ in st._written if n == "auto_exposure"] == ["manual", "auto"]


class _NoMode:
    """A frame source without a mode: the recorder learns the frame size from the first frame."""

    def __init__(self) -> None:
        self.src = SyntheticStereoSource(eye_size=(24, 12), fps=1000.0)

    def read(self):
        return self.src.read()


def test_a_recording_that_fails_on_its_first_frame_stops_and_the_viewer_goes_on(tmp_path: Path, capsys):
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x", encoding="utf-8")
    stats = run_viewer(_NoMode(), headless=True, out_dir=blocker, key_feed=[ord("v"), -1, -1, ord("q")])
    assert stats["frames"] == 4 and stats["last_error"].startswith("recording failed")
    assert "recording failed" in capsys.readouterr().err


def test_v_twice_in_a_row_leaves_no_empty_file(tmp_path: Path):
    src = SyntheticStereoSource(eye_size=(64, 32), fps=1000.0)
    st = ViewerState()
    run_viewer(src, headless=True, out_dir=tmp_path, state=st, key_feed=[ord("v"), ord("v"), -1])
    assert list(tmp_path.iterdir()) == []
    assert any(m.startswith("recorded nothing") for m in st.messages)


class _FakeWindow:
    """cv2's window functions, with keys fed in order and a trackbar the test can drag. No window opens."""

    def __init__(self, keys):
        self.keys = list(keys)
        self.bar = None
        self.drag_to = {}                                        # key count -> trackbar value the user drags to
        self.shown: list[int] = []
        self.calls = 0

    def install(self, monkeypatch):
        import cv2
        monkeypatch.setattr(cv2, "imshow", lambda win, img: None)
        monkeypatch.setattr(cv2, "waitKey", self.wait_key)
        monkeypatch.setattr(cv2, "createTrackbar", lambda name, win, v, mx, cb: setattr(self, "bar", v))
        monkeypatch.setattr(cv2, "getTrackbarPos", lambda name, win: self.bar)
        monkeypatch.setattr(cv2, "setTrackbarPos", lambda name, win, v: setattr(self, "bar", v))
        monkeypatch.setattr(cv2, "destroyWindow", lambda win: None)

    def wait_key(self, ms):
        self.calls += 1
        if self.calls in self.drag_to:
            self.bar = self.drag_to[self.calls]
        return self.keys.pop(0) if self.keys else ord("q")


def test_window_stays_on_the_last_frame_and_the_bar_seeks(tmp_path: Path, monkeypatch):
    from opticmix_vision import Device, DeviceConfig
    avi = _recorded(tmp_path, 8)
    win = _FakeWindow([-1] * 12 + [ord(" ")] + [-1] * 2 + [ord("q")])
    win.drag_to = {3: 6}                                        # on the third frame the user drags the bar to 6
    win.install(monkeypatch)
    seen: list[int] = []

    def view(frame, state):
        seen.append(frame.index)
        return frame.left, frame.right, []

    with Device(DeviceConfig.recording(avi)) as cam:
        run_viewer(cam, headless=False, view=view)
        # 0 1 2, drag to 6: 6 7, then the end: stays on 7 (ended, paused) until space plays again from 0
        assert seen[:5] == [0, 1, 2, 6, 7]
        assert set(seen[5:13]) == {7}
        assert seen[13:] == [0, 1, 2]                           # space (13th key) plays again from 0; then q
        assert win.bar == 2                                     # the bar follows the frame shown


def test_seek_bar_follows_and_seeks():
    from opticmix_vision.viewer import _SeekBar

    class Cv:
        def __init__(self): self.v = None
        def createTrackbar(self, name, win, v, mx, cb): self.v, self.max = v, mx
        def getTrackbarPos(self, name, win): return self.v
        def setTrackbarPos(self, name, win, v): self.v = v

    class Pb:
        frame_count = 100
        sought: list[int] = []
        def seek(self, i): self.sought.append(i)

    cv, pb = Cv(), Pb()
    bar = _SeekBar(cv, "w", pb)
    assert cv.max == 99
    bar.sync(5)
    assert cv.v == 5 and pb.sought == []
    cv.v = 40                                                   # dragged
    bar.sync(6)
    assert pb.sought == [40] and cv.v == 40
    bar.sync(40)
    assert cv.v == 40 and pb.sought == [40]


def test_rec_line_counts_frames_missed_since_recording_started(tmp_path: Path):
    from opticmix_vision.recording import Recorder
    from opticmix_vision.viewer import _overlay
    src = SyntheticStereoSource(eye_size=(64, 32), fps=100.0)
    rec = Recorder(tmp_path / "r.avi", src)
    f = src.read()
    rec.write(f)
    lines = _overlay(f, src, ViewerState(), rec, 50.0, f.left, [])
    rec.close()
    assert lines[0].startswith("REC r.avi  1 frames") and "missed~0 since start" in lines[0]


# ---------------------------------------------------------------- presets (n / p / P, --preset) ----

@pytest.fixture
def store(monkeypatch, tmp_path):
    """Never the user's %APPDATA%/opticmix: the preset folder goes to a temporary folder."""
    from opticmix_vision import presets
    monkeypatch.setattr(presets, "store_dir", lambda: tmp_path / "presets")
    return tmp_path / "presets"


def _edit_preset(path: Path, fn) -> None:
    import json
    doc = json.loads(path.read_text(encoding="utf-8"))
    fn(doc)
    path.write_text(json.dumps(doc), encoding="utf-8")


def _set(src, **values):
    src.controls.set_many(values)
    src.controls.flush(timeout=5)


def test_n_saves_every_control_as_a_preset_and_never_replaces_one(store):
    from opticmix_vision import presets
    src = SyntheticStereoSource(eye_size=(64, 32))
    st = ViewerState(preset_name="bench")
    n0 = len(src._sim.writes)
    assert st.handle_key(ord("n"), src) is None
    doc = presets.read_preset(store / "bench.json")
    assert {k: e["value"] for k, e in doc["controls"].items()} == src.controls.snapshot()
    assert doc["device"]["model"] == "synthetic" and doc["name"] == "bench"
    assert st.messages[-1] == f"saved preset bench to {store / 'bench.json'}"
    before = (store / "bench.json").read_text(encoding="utf-8")
    _set(src, gain=100)
    st.handle_key(ord("n"), src)                                    # the same name again: refused, file kept
    assert "exists and is not replaced" in st.last_error
    assert (store / "bench.json").read_text(encoding="utf-8") == before
    assert len(src._sim.writes) == n0 + 1                           # only the gain set above; saving reads only
    auto = ViewerState()
    auto.handle_key(ord("n"), src)
    (f,) = store.glob("viewer_*.json")                              # no --preset-name: viewer_<time>
    assert presets.read_preset(f)["controls"]["gain"]["value"] == 100


def test_p_shows_what_would_change_and_P_writes_it_in_one_set_many(store, monkeypatch):
    src = SyntheticStereoSource(eye_size=(64, 32))
    st = ViewerState(preset_name="bench")
    st.handle_key(ord("n"), src)                                    # gain 32, LED 32
    _set(src, gain=100, backlight_compensation=10)
    n0 = len(src._sim.writes)
    st.handle_key(ord("p"), src)
    assert st.messages[-1] == ("preset bench (1/1): 2 to write: backlight_compensation 10->32, gain 100->32"
                               " - P loads")
    assert len(src._sim.writes) == n0, "p writes nothing"
    calls = []
    real = src.controls.set_many
    monkeypatch.setattr(src.controls, "set_many", lambda v: calls.append(dict(v)) or real(v))
    st.handle_key(ord("P"), src)
    assert calls == [{"gain": 32, "backlight_compensation": 32}]
    st.poll(timeout=5)
    assert st.messages[-1] == "preset bench (1/1) loaded: 2 written" and st.last_error == ""
    assert src.controls.get("gain") == 32 and src.controls.get("backlight_compensation") == 32
    st.handle_key(ord("p"), src)                                    # after the last saved preset: the defaults
    st.handle_key(ord("p"), src)
    assert st.messages[-1] == "preset bench (1/1): already at its values - nothing to write"


def test_p_steps_through_the_presets_of_this_model_only(store):
    src = SyntheticStereoSource(eye_size=(64, 32))
    st = ViewerState()
    st.handle_key(ord("p"), src)
    assert st.messages[-2] == f"no presets for this camera in {store} - n saves one"
    assert st.messages[-1].startswith("preset defaults: the camera's own defaults")      # always offered
    for name in ("a", "b", "other"):
        st.preset_name = name
        st.handle_key(ord("n"), src)
    _edit_preset(store / "other.json", lambda d: d["device"].update(model="WN Dual Camera"))
    labels = []
    for _ in range(4):
        st.handle_key(ord("p"), src)
        labels.append(st.messages[-1].split(":")[0])
    assert labels == ["preset a (1/2)", "preset b (2/2)", "preset defaults", "preset a (1/2)"]


@pytest.mark.parametrize("edit, reason", [
    (lambda d: d["controls"]["gain"].update(value=999), "gain must be a whole number within 0..255"),
    (lambda d: d["controls"].update(focus={"value": 3}), "Not controls of this camera: ['focus']"),
    (lambda d: d["controls"]["exposure"].update(units="100us"), "exposure: the preset is in '100us'"),
])
def test_a_preset_that_does_not_fit_is_refused_with_the_reason_and_nothing_is_written(store, edit, reason):
    src = SyntheticStereoSource(eye_size=(64, 32))
    st = ViewerState(preset_name="bad")
    st.handle_key(ord("n"), src)
    _edit_preset(store / "bad.json", lambda d: d["controls"]["brightness"].update(value=80))
    _edit_preset(store / "bad.json", edit)
    n0 = len(src._sim.writes)
    st.handle_key(ord("p"), src)
    assert st.last_error.startswith("preset bad (1/1) not usable: ") and reason in st.last_error
    assert st.last_error.endswith("Nothing was written.")
    st.handle_key(ord("P"), src)
    assert st.messages[-1] == "no preset chosen - p chooses one"
    src.controls.flush(timeout=5)
    assert len(src._sim.writes) == n0 and src.controls.get("brightness") == 50


def test_a_preset_from_another_model_is_shown_with_a_warning_and_P_loads_it_anyway(store):
    src = SyntheticStereoSource(eye_size=(64, 32))
    st = ViewerState(preset_name="lab")
    st.handle_key(ord("n"), src)
    _edit_preset(store / "lab.json", lambda d: d["device"].update(model="WN Dual Camera"))
    _edit_preset(store / "lab.json", lambda d: d["controls"]["gain"].update(value=40))
    n0 = len(src._sim.writes)
    st.choose_preset(src, store / "lab.json")
    assert st.messages[-1] == ("preset lab: saved from WN Dual Camera, not this camera (synthetic); "
                               "1 to write: gain 32->40 - P loads anyway")
    assert len(src._sim.writes) == n0
    st.handle_key(ord("P"), src)
    st.poll(timeout=5)
    assert st.messages[-1] == "preset lab loaded: 1 written" and src.controls.get("gain") == 40


@pytest.mark.parametrize("sim, expect", [
    ({"fail_keys": ["gain"]}, "preset x: gain failed (OSError: simulated driver rejected gain)"),
    ({"ignore_writes": ["gain"]}, "preset x: gain: camera kept 32, not 40"),
])
def test_a_write_the_camera_refuses_or_does_not_keep_is_reported(store, capsys, sim, expect):
    from opticmix_vision._ctrl_sim import SimBackend
    src = SyntheticStereoSource(eye_size=(64, 32))
    src._sim = SimBackend(**sim)                                    # before the controls are first used
    st = ViewerState(preset_name="x")
    st.handle_key(ord("n"), src)
    _edit_preset(store / "x.json", lambda d: d["controls"]["gain"].update(value=40))
    st.choose_preset(src, store / "x.json")
    st.load_preset(src)
    st.poll(timeout=5)
    assert st.last_error.startswith(expect)
    assert expect in capsys.readouterr().err


def _recorded_changes(src, st, keys, out: Path) -> dict:
    import json
    run_viewer(src, headless=True, out_dir=out, state=st, key_feed=keys)
    (js,) = out.glob("*.json")
    return json.loads(js.read_text(encoding="utf-8"))["recording"]


def test_a_preset_loaded_while_recording_is_in_the_json_when_it_is_requested(store, tmp_path):
    src = SyntheticStereoSource(eye_size=(64, 32), fps=1000.0)
    st = ViewerState(preset_name="bench")
    st.handle_key(ord("n"), src)
    _set(src, gain=100)

    def keys():
        yield from (ord("v"), -1, ord("p"), ord("P"))
        while st._loading is not None:                              # until every write has its result
            yield -1
        yield from (-1, ord("v"))

    r = _recorded_changes(src, st, keys(), tmp_path / "cap")
    # like the keys: the status when it was requested; the camera's values at the end are in controls_at_end
    assert [(c["name"], c["value"], c["status"]) for c in r["control_changes"]] == [
        ("preset", "bench.json", "loaded"), ("gain", 32, "pending")]
    assert r["control_changes"][0]["frame"] == r["control_changes"][1]["frame"] >= 2
    assert r["controls_at_end"]["gain"] == 32
    assert "preset bench (1/1) loaded: 1 written" in st.messages


def test_a_preset_loaded_just_before_quitting_with_a_slow_driver_is_still_in_the_json(store, tmp_path):
    """A slow write still pending at q: the recording used to close with control_changes [] and gain 32 at end."""
    import time
    from opticmix_vision._ctrl_sim import SimBackend

    class Slow(SimBackend):
        def write(self, key, value, auto):
            time.sleep(0.2)
            return super().write(key, value, auto)

    src = SyntheticStereoSource(eye_size=(64, 32), fps=1000.0)
    src._sim = Slow()
    st = ViewerState(preset_name="bench")
    st.handle_key(ord("n"), src)
    _set(src, gain=100)
    r = _recorded_changes(src, st, [ord("v"), -1, ord("p"), ord("P"), ord("q")], tmp_path / "cap")
    assert [(c["name"], c["value"]) for c in r["control_changes"]] == [("preset", "bench.json"), ("gain", 32)]
    assert r["controls_at_end"]["gain"] == 32                       # the end waited for the write (2 s at most)
    assert "preset bench (1/1) loaded: 1 written" in st.messages


def test_cli_preset_loads_before_the_first_frame_and_logs_the_result(store, capsys, monkeypatch):
    from opticmix_vision import viewer
    src = SyntheticStereoSource(eye_size=(64, 32))
    ViewerState(preset_name="bench").handle_key(ord("n"), src)
    _edit_preset(store / "bench.json", lambda d: d["controls"]["gain"].update(value=40))
    cam = SyntheticStereoSource(eye_size=(64, 32))
    monkeypatch.setattr(viewer, "_open_source", lambda a: cam)
    assert main(["--synthetic", "--headless", "--frames", "2", "--preset", "bench"]) == 0
    out = capsys.readouterr().out
    assert "[viewer] preset bench: 1 to write: gain 32->40 - P loads" in out
    assert "[viewer] preset bench loaded: 1 written" in out
    assert cam._sim.writes == [("gain", 40, None)]


def test_cli_names_the_folder_when_the_preset_is_missing_and_opens_nothing(store, capsys, monkeypatch):
    from opticmix_vision import viewer
    monkeypatch.setattr(viewer, "_open_source", lambda a: (_ for _ in ()).throw(AssertionError("opened")))
    assert main(["--synthetic", "--headless", "--preset", "nope"]) == 2
    assert f"No preset named 'nope' in {store}" in capsys.readouterr().err
    assert main(["--synthetic", "--headless", "--preset", "defaults"]) == 2
    assert ("'defaults' is not loaded at startup: in the window, p steps to defaults and shows what it would "
            "change, then P writes it (or python -m opticmix_vision.presets load defaults)") in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(["--recording", "x.avi", "--headless", "--preset", "nope"])


def test_control_and_preset_keys_on_a_recording_say_why_and_the_viewer_keeps_going(tmp_path: Path):
    """Before: l/L/e/E/g/G on a recording raised DeviceError and the viewer ended as if the camera was unplugged."""
    from opticmix_vision import Device, DeviceConfig
    avi = _recorded(tmp_path, 10)
    with Device(DeviceConfig.recording(avi)) as cam:
        st = ViewerState()
        stats = run_viewer(cam, headless=True, state=st, key_feed=[-1, ord("L"), ord("e"), ord("p"), ord("n"), -1])
    assert stats["frames"] == 6
    assert st.messages[-4:] == ["a recording has no camera controls (LED, exposure, gain, presets); the values"
                                " when it was recorded are in its .json (controls)"] * 4


# ---------------------------------------------------------------- saturation ----

def test_saturation_is_the_share_of_pixels_at_the_level():
    from opticmix_vision.viewer import _saturated_pct
    eye = np.full((80, 100), 254, np.uint8)
    eye[:20] = 255                                                  # a quarter of the rows
    assert _saturated_pct(eye) == 25.0
    assert _saturated_pct(eye, 254) == 100.0
    eye[0, :3] = 0
    assert _saturated_pct(eye) == pytest.approx(100.0 * 1997 / 8000)


def test_overlay_shows_each_eyes_saturation_from_the_frame_as_received():
    from opticmix_vision.device import StereoFrame
    from opticmix_vision.viewer import _overlay
    left = np.zeros((40, 40), np.uint8)
    left[:, :10] = 255                                              # 25 %
    right = np.full((40, 40), 210, np.uint8)
    f = StereoFrame(left=left, right=right, host_ns=0, index=0)
    src = SyntheticStereoSource(eye_size=(40, 40))
    drawn = np.zeros_like(left)                                     # what a view draws does not count
    lines = _overlay(f, src, ViewerState(), None, 90.0, drawn, [])
    assert "saturated (>=255)  L 25.00%  R 0.00%" in lines
    lines = _overlay(f, src, ViewerState(saturation_level=200), None, 90.0, drawn, [])
    assert "saturated (>=200)  L 25.00%  R 100.00%" in lines


def test_cli_saturation_level_must_be_a_pixel_value():
    with pytest.raises(SystemExit):
        main(["--synthetic", "--headless", "--frames", "1", "--saturation-level", "0"])
    assert main(["--synthetic", "--headless", "--frames", "1", "--saturation-level", "250"]) == 0


def test_a_recording_whose_csv_fails_midway_is_closed_and_the_viewer_goes_on(tmp_path: Path, monkeypatch):
    import json
    from opticmix_vision import recording
    made = []
    real_write = recording.Recorder.write

    def write(self, frame):                     # the third frame's CSV row fails, e.g. the disk is full
        made.append(self)
        if self.frames == 2:
            raise OSError(28, "No space left on device")
        real_write(self, frame)
    monkeypatch.setattr(recording.Recorder, "write", write)
    src = SyntheticStereoSource(eye_size=(64, 32), fps=1000.0)
    stats = run_viewer(src, headless=True, out_dir=tmp_path, key_feed=[ord("v"), -1, -1, -1, -1, ord("q")])
    assert stats["frames"] == 6 and stats["last_error"].startswith("recording failed")
    rec = made[-1]
    assert rec._writer is None                  # the video file was released
    r = json.loads(rec.info_path.read_text(encoding="utf-8"))["recording"]
    assert r["closed"] is True and r["frames"] == 2


def _count_reads(monkeypatch, controls) -> dict[str, int]:
    """Counts the device reads of a Controls (get, snapshot, describe) from here on."""
    n = {"reads": 0}
    for meth in ("get", "snapshot", "describe"):
        real = getattr(controls, meth)

        def counted(*a, _real=real, **k):
            n["reads"] += 1
            return _real(*a, **k)
        monkeypatch.setattr(controls, meth, counted)
    return n


@pytest.mark.parametrize("frames", [20, 300])
def test_the_window_reads_the_controls_when_it_opens_not_on_every_frame(monkeypatch, frames):
    """The window draws LED / exposure / gain on every frame but reads them from the camera only when it opens:
    the reads are the same for 20 and 300 frames. (Reading them on every frame went together with a stopped eye.)"""
    src = SyntheticStereoSource(eye_size=(64, 32), fps=1000.0)
    n = _count_reads(monkeypatch, src.controls)
    win = _FakeWindow([-1] * (frames - 1))                       # then q
    win.install(monkeypatch)
    stats = run_viewer(src, headless=False)
    assert stats["frames"] == frames
    assert n["reads"] == 1                                       # every control once (snapshot), when it opens


def test_a_key_changes_the_led_shown_and_the_camera_is_not_read_again(monkeypatch):
    """L in the window: the line shows the new LED (what the viewer wrote) with no further reads."""
    from opticmix_vision import viewer
    monkeypatch.setattr(viewer, "SETTLE_S", 0.0)
    src = SyntheticStereoSource(eye_size=(64, 32), fps=1000.0)
    led0 = src.controls.get("backlight_compensation")
    n = _count_reads(monkeypatch, src.controls)
    shown: list[str] = []
    real_overlay = viewer._overlay

    def overlay(*a, **k):
        lines = real_overlay(*a, **k)
        shown.append(next(t for t in lines if t.startswith("LED ")))
        return lines
    monkeypatch.setattr(viewer, "_overlay", overlay)
    win = _FakeWindow([-1, ord("L"), -1, ord("L")] + [-1] * 20)
    win.install(monkeypatch)
    run_viewer(src, headless=False)
    assert shown[0].startswith(f"LED {led0:g} ") and shown[-1].startswith(f"LED {led0 + 16:g} ")
    assert n["reads"] == 1
    assert src.controls.get("backlight_compensation") == led0 + 16     # it was written, not only shown


def test_the_overlay_shows_what_the_camera_kept_and_reads_again_only_after_a_refused_write():
    """Keys and the overlay go on from what the viewer knows: the value asked for while queued, the value read back
    after the write (here the camera kept 36, not 40), and, after a refused write, one read."""
    from types import SimpleNamespace
    from opticmix_vision.viewer import _overlay
    c = _LaggingControls(backlight_compensation=32, exposure=-6, gain=16)
    dev = SimpleNamespace(controls=c)
    f = SyntheticStereoSource(eye_size=(16, 8)).read()
    st = ViewerState()

    def led() -> str:
        return next(t for t in _overlay(f, dev, st, None, 90.0, f.left, []) if t.startswith("LED "))
    assert led() == "LED 32  exp -6  gain 16  (as opened + viewer's writes)" and c.gets == 3
    for i in range(5):                                           # five presses, each written: no read
        st.handle_key(ord("L"), dev, now=10.0 * i)
        st.commit(dev, now=10.0 * i + 1.0)
        led()
    assert led().startswith("LED 72 ") and c.gets == 3
    c.apply(backlight_compensation=36)                           # the camera kept 36
    assert led().startswith("LED 36 ") and c.gets == 3
    st.handle_key(ord("L"), dev, now=100.0)
    st.commit(dev, now=101.0)
    assert st._written[-1][1] == 44
    c.done[c.n] = SimpleNamespace(status="failed", readback=None)  # refused
    c.queue.clear()
    assert led().startswith("LED 36 ") and c.gets == 4           # read once again
    led()
    assert c.gets == 4


def test_exposure_shows_auto_after_a_turns_auto_exposure_on_without_reading_it():
    from types import SimpleNamespace
    from opticmix_vision.viewer import _overlay
    c = _LaggingControls(auto_exposure="manual", backlight_compensation=32, exposure=-6, gain=16)
    dev = SimpleNamespace(controls=c)
    f = SyntheticStereoSource(eye_size=(16, 8)).read()
    st = ViewerState()

    def line() -> str:
        return next(t for t in _overlay(f, dev, st, None, 90.0, f.left, []) if t.startswith("LED "))
    assert line() == "LED 32  exp -6  gain 16  (as opened + viewer's writes)"
    st.handle_key(ord("a"), dev, now=0.0)                        # manual -> auto (reads the mode once)
    assert line() == "LED 32  exp auto  gain 16  (as opened + viewer's writes)" and c.gets == 4
    c.apply()
    st.handle_key(ord("a"), dev, now=1.0)                        # auto -> manual
    assert line() == "LED 32  exp -6  gain 16  (as opened + viewer's writes)" and c.gets == 4


# ---------------------------------------------------------------- a key held down (key repeat) ----

class _HeldKeys(_FakeWindow):
    """The window with each key at a time in seconds; the viewer's clock reads the time of the key last returned."""

    def __init__(self, events):
        super().__init__([k for _, k in events])
        self.times, self.now = [t for t, _ in events], 0.0

    def install(self, monkeypatch):
        from opticmix_vision import viewer
        super().install(monkeypatch)
        monkeypatch.setattr(viewer, "_clock", lambda: self.now)

    def wait_key(self, ms):
        if self.times:
            self.now = self.times.pop(0)
        return super().wait_key(ms)


def _held(key: int, t0: float = 0.0) -> list[tuple[float, int]]:
    """key held down in a 90 fps window: pressed, then repeated by the OS from 0.5 s on, every 30 ms — 20 arrivals,
    with two frames without a key after each."""
    out = []
    for t in [t0] + [t0 + 0.5 + 0.03 * i for i in range(19)]:
        out += [(t, key), (t + 0.01, -1), (t + 0.02, -1)]
    return out


def test_holding_v_starts_one_recording_and_the_next_press_stops_it(tmp_path: Path, monkeypatch):
    """v held down started and stopped a recording with each key repeat: 7 files from one press (Weevia S1, 2026-11-24)."""
    events = _held(ord("v"))
    t = events[-1][0] + 0.7                                      # released, then pressed again
    win = _HeldKeys(events + [(t, ord("v")), (t + 0.01, -1)])    # then q
    win.install(monkeypatch)
    st = ViewerState()
    run_viewer(SyntheticStereoSource(eye_size=(64, 32), fps=1000.0), out_dir=tmp_path, state=st)
    (avi,) = tmp_path.glob("*.avi")                              # one recording
    assert _avi_frames(avi)[0] == len(events) - 1                # from the frame after the first v to the next press
    assert sum(m.startswith("recording ") for m in st.messages) == 1
    assert any(m.startswith(f"recorded {len(events) - 1} frames to {avi.name}") for m in st.messages)


def test_holding_s_saves_one_pair(tmp_path: Path, monkeypatch):
    win = _HeldKeys(_held(ord("s")))
    win.install(monkeypatch)
    stats = run_viewer(SyntheticStereoSource(eye_size=(64, 32), fps=1000.0), out_dir=tmp_path)
    assert stats["saved"] == 1 and len(list(tmp_path.glob("*.png"))) == 2


def test_holding_a_writes_auto_exposure_once(monkeypatch):
    src = SyntheticStereoSource(eye_size=(64, 32), fps=1000.0)
    sets = []
    real = src.controls.set
    monkeypatch.setattr(src.controls, "set", lambda n, v: sets.append((n, v)) or real(n, v))
    events = _held(ord("a"))
    t = events[-1][0] + 0.7
    win = _HeldKeys(events + [(t, ord("a"))])                    # released, pressed again: back to manual
    win.install(monkeypatch)
    run_viewer(src)
    assert sets == [("auto_exposure", "auto"), ("auto_exposure", "manual")]


def test_holding_P_loads_the_preset_once(store, monkeypatch):
    src = SyntheticStereoSource(eye_size=(64, 32), fps=1000.0)
    st = ViewerState(preset_name="bench")
    st.handle_key(ord("n"), src)                                 # gain 32
    _set(src, gain=100)
    st.handle_key(ord("p"), src)                                 # P loads bench: gain 100 -> 32
    loads, writes = [], []
    real_load, real_many = src.controls.load_preset, src.controls.set_many
    monkeypatch.setattr(src.controls, "load_preset", lambda *a, **k: loads.append(a) or real_load(*a, **k))
    monkeypatch.setattr(src.controls, "set_many", lambda v: writes.append(dict(v)) or real_many(v))
    win = _HeldKeys(_held(ord("P")))
    win.install(monkeypatch)
    run_viewer(src, state=st)
    assert len(loads) == 1 and writes == [{"gain": 32}]
    assert src.controls.get("gain") == 32


def test_keys_from_key_feed_are_separate_presses(tmp_path: Path):
    """No OS key repeat in a scripted key_feed: each v is a press, however fast the loop runs."""
    src = SyntheticStereoSource(eye_size=(64, 32), fps=1000.0)
    run_viewer(src, headless=True, out_dir=tmp_path, key_feed=[ord("v"), -1, ord("v"), ord("v"), -1, ord("v")])
    assert len(list(tmp_path.glob("*.avi"))) == 2


def test_l_held_down_still_steps_with_each_repeat(monkeypatch):
    """The LED / exposure / gain keys go on stepping while held (written once, here by q), as before."""
    src = SyntheticStereoSource(eye_size=(64, 32), fps=1000.0)
    led0 = src.controls.get("backlight_compensation")
    st = ViewerState()
    win = _HeldKeys(_held(ord("L"))[:3 * 8])                   # 8 arrivals: 32 -> 96, the top of the range
    win.install(monkeypatch)
    run_viewer(src, state=st)
    src.controls.flush(timeout=5)
    assert src.controls.get("backlight_compensation") == led0 + 8 * int(st.led_step)


# ---------------------------------------------------------------- s and v write the controls the viewer knows ----

def test_s_and_v_write_the_controls_the_viewer_knows_without_reading_the_camera(tmp_path: Path, monkeypatch):
    """capture_info and Recorder.close read every control (snapshot) at each s and at the start and end of each v,
    while streaming. The viewer now reads them once when it opens and gives them what it knows."""
    import json
    from opticmix_vision import viewer
    monkeypatch.setattr(viewer, "SETTLE_S", 0.0)                 # an L press is written on the next frame
    src = SyntheticStereoSource(eye_size=(64, 32), fps=1000.0)
    snap = src.controls.snapshot()
    n = _count_reads(monkeypatch, src.controls)
    keys = [-1, ord("s"), ord("v"), -1, ord("L"), -1, -1, ord("v"), -1, ord("s"), ord("v"), -1, ord("v"), -1]
    win = _HeldKeys([(float(i), k) for i, k in enumerate(keys)])  # a second apart: separate presses
    win.install(monkeypatch)
    run_viewer(src, out_dir=tmp_path)
    assert n["reads"] == 1                                       # one snapshot when the viewer opened
    first, second = sorted(tmp_path.glob("*.avi"))
    rec = json.loads(first.with_suffix(".json").read_text(encoding="utf-8"))
    assert rec["controls"] == snap and "read when it opened" in rec["controls_note"]
    led = snap["backlight_compensation"] + 8
    assert rec["recording"]["controls_at_end"] == {**snap, "backlight_compensation": led}
    pairs = sorted(j for j in tmp_path.glob("*.json") if not j.with_suffix(".avi").exists())
    assert [json.loads(j.read_text(encoding="utf-8"))["controls"]["backlight_compensation"] for j in pairs] == [
        snap["backlight_compensation"], led]
    assert src.controls.get("backlight_compensation") == led     # what the JSON says is what the camera has


def test_recorder_and_capture_info_read_the_camera_when_given_no_values(tmp_path: Path, monkeypatch):
    from opticmix_vision.recording import Recorder, capture_info
    src = SyntheticStereoSource(eye_size=(64, 32), fps=1000.0)
    n = _count_reads(monkeypatch, src.controls)
    assert capture_info(src)["controls"] == src.controls.snapshot() and n["reads"] == 2
    given = {"gain": 1}
    info = capture_info(src, controls=given)
    assert info["controls"] == given and "not read from the camera" in info["controls_note"] and n["reads"] == 2
    rec = Recorder(tmp_path / "r.avi", src, controls=lambda: given)
    rec.write(src.read())
    rec.close()
    assert rec.info["controls"] == rec.info["recording"]["controls_at_end"] == given and n["reads"] == 2


# ---------------------------------------------------------------- the window says what it shows and what it ignores ----

def test_the_control_line_says_where_its_values_come_from_and_the_help_says_so_too():
    """The LED / exp / gain line is not a live reading: it ends by saying it is the values read when the viewer
    opened plus its own writes, and the key help (shown with h, wrapped to fit) says the same."""
    from types import SimpleNamespace
    from opticmix_vision.viewer import KEY_HELP, _overlay
    c = _LaggingControls(backlight_compensation=32, exposure=-6, gain=16)
    f = SyntheticStereoSource(eye_size=(16, 8)).read()
    st = ViewerState(show_help=True)
    lines = _overlay(f, SimpleNamespace(controls=c), st, None, 90.0, f.left, [])
    assert "LED 32  exp -6  gain 16  (as opened + viewer's writes)" in lines
    assert "| LED/exp/gain line: as opened + viewer's writes |" in KEY_HELP
    sat = next(i for i, t in enumerate(lines) if t.startswith("saturated"))
    shown = lines[sat + 1:]                                      # no messages yet: the help is the rest
    assert len(shown) > 1 and all(len(t) <= 100 for t in shown) and " ".join(shown) == KEY_HELP


def test_a_once_key_held_down_says_once_per_press_that_it_is_ignored(tmp_path: Path, monkeypatch):
    """s held down (20 arrivals): one pair saved and one message, not 19; pressed again after a pause, it saves
    again, and a second hold says it once more."""
    first = _held(ord("s"))
    second = _held(ord("s"), t0=first[-1][0] + 0.7)
    win = _HeldKeys(first + second)
    win.install(monkeypatch)
    st = ViewerState()
    stats = run_viewer(SyntheticStereoSource(eye_size=(64, 32), fps=1000.0), out_dir=tmp_path, state=st)
    said = [i for i, m in enumerate(st.messages) if "ignored" in m]
    assert [st.messages[i] for i in said] == ["s ignored: same key within 0.6 s (held?) - press again after a pause"] * 2
    assert stats["saved"] == 2
    saved = [i for i, m in enumerate(st.messages) if m.startswith("saved ")]
    assert saved[0] < said[0] < saved[1] < said[1]               # one message for each press, after its save


# ---------------------------------------------------------------- c: the installed calibration file ----

@pytest.fixture
def calib_store(monkeypatch, tmp_path):
    """Never the user's %APPDATA%/opticmix: the calibration folder goes to a temporary folder."""
    from opticmix_vision import calib
    monkeypatch.setattr(calib, "store_dir", lambda: tmp_path / "calib")
    return tmp_path / "calib"


def _install_calibration(tmp_path: Path, serial: str = "S1-0042", size=(64, 32)) -> None:
    """A calibration file with values the test can recognise, installed as `calib add` does."""
    from opticmix_vision import calib
    from opticmix_vision.calibration import CameraIntrinsics, StereoCalibration, StereoExtrinsics
    a = np.radians(1.0)
    R = [[np.cos(a), 0, np.sin(a)], [0, 1, 0], [-np.sin(a), 0, np.cos(a)]]      # 1 degree about y
    c = StereoCalibration(
        left=CameraIntrinsics(K=np.array([[400.0, 0, 31.5], [0, 401.0, 15.5], [0, 0, 1]]),
                              D=np.array([0.01, -0.002, 0.0003, -4e-05]), size=size),
        right=CameraIntrinsics(K=np.array([[402.5, 0, 32.25], [0, 403.0, 16.0], [0, 0, 1]]),
                               D=np.array([0.02, 0.0, 0.0, 0.0]), size=size),
        extrinsics=StereoExtrinsics(R=np.array(R), t=[-64.0, 0.5, 1.0]),
        device={"model": "Weevia S1", "serial": serial})
    src = tmp_path / f"{serial}_from_station.json"
    src.write_text(c.to_json(), encoding="utf-8")
    calib.add(str(src))


def test_c_shows_the_installed_calibration_file_and_reads_and_writes_no_control(calib_store, tmp_path, monkeypatch):
    """c in the window: the file's serial, path, each eye's intrinsics and distortion, and the baseline, t and rotation
    between the eyes, from the file; c again hides them. The camera's controls are read no more than without c."""
    from opticmix_vision import viewer
    _install_calibration(tmp_path)
    expected = [f"calibration S1-0042 (Weevia S1)  file {calib_store / 'S1-0042.json'} - installed file, not "
                "matched to this camera (no USB serial)",
                "left   KB4 64x32  fx 400.00  fy 401.00  cx 31.50  cy 15.50  D 0.01 -0.002 0.0003 -4e-05",
                "right  KB4 64x32  fx 402.50  fy 403.00  cx 32.25  cy 16.00  D 0.02 0 0 0",
                "baseline 64.01 mm  t -64.00 0.50 1.00 mm  R 1.000 deg  (left to right; c hides)"]
    real_overlay = viewer._overlay

    def run(keys):
        src = SyntheticStereoSource(eye_size=(400, 32), fps=1000.0)    # 88 characters a line: the last line fits
        reads, real_read = [], src._sim.read
        monkeypatch.setattr(src._sim, "read", lambda k: reads.append(k) or real_read(k))
        shown: list[list[str]] = []
        monkeypatch.setattr(viewer, "_overlay", lambda *a, **k: shown.append(real_overlay(*a, **k)) or shown[-1])
        _HeldKeys([(float(i), k) for i, k in enumerate(keys)]).install(monkeypatch)   # a second apart
        run_viewer(src)
        return shown, len(reads), len(src._sim.writes)

    keys = [-1, ord("c")] + [-1] * 5 + [ord("c")] + [-1] * 3
    shown, reads, writes = run(keys)
    with_c = [expected[-1] in lines and any(t.startswith("calibration S1-0042 (Weevia S1)") for t in lines)
              for lines in shown]
    assert with_c == [False] * 2 + [True] * 6 + [False] * 4      # from the frame after c until the next c
    assert (reads, writes) == run([-1] * len(keys))[1:]           # the same device reads as without c; no write
    assert writes == 0
    st = ViewerState()
    st.handle_key(ord("c"), SyntheticStereoSource(eye_size=(64, 32)))
    assert st._calib_lines == expected


def test_c_says_what_to_do_with_no_file_two_files_or_another_eye_size(calib_store, tmp_path, capsys):
    from opticmix_vision import Device, DeviceConfig
    src = SyntheticStereoSource(eye_size=(64, 32))
    st = ViewerState()

    def shown() -> str:
        st._calib_lines = []                                      # hidden: c shows it again
        st.handle_key(ord("c"), src)
        assert st._calib_lines and all(len(t) <= 100 for t in st._calib_lines)
        return " ".join(st._calib_lines)
    assert shown() == ("calibration: No calibration installed. Install the file for your camera: python -m "
                       "opticmix_vision.calib add <file.json>")
    assert "[viewer] calibration: No calibration installed." in capsys.readouterr().out    # in the log too
    _install_calibration(tmp_path, "S1-0042")
    _install_calibration(tmp_path, "S1-0043")
    assert shown().startswith("calibration: 2 calibrations installed (S1-0042, S1-0043). Keep only the file")
    (calib_store / "S1-0043.json").unlink()
    avi = _recorded(tmp_path / "rec", 3)                          # a recording of 64x32 eyes: Device.calibration
    with Device(DeviceConfig.recording(avi)) as cam:
        st._calib_lines = []
        st.handle_key(ord("c"), cam)
        assert st._calib_lines[1].startswith("left   KB4 64x32  fx 400.00")
    _install_calibration(tmp_path, "S1-0042", size=(80, 40))      # replaced by one for other eyes
    with Device(DeviceConfig.recording(avi)) as cam:
        st._calib_lines = []
        st.handle_key(ord("c"), cam)
        assert st._calib_lines == ["calibration: This calibration is for 80x40 eyes. Open the 160x40 mode."]


# ---------------------------------------------------------------- p / P: the camera's own defaults ----

def test_p_offers_the_camera_defaults_after_the_saved_presets_and_P_writes_them_like_reset_to_defaults(
        store, monkeypatch):
    """The explorer's Reset to defaults and `presets load defaults` in the viewer: p shows what plan_reset would
    change and writes nothing; P writes it with reset_to_defaults; then p starts again at the first saved preset."""
    src = SyntheticStereoSource(eye_size=(64, 32))
    st = ViewerState(preset_name="bench")
    st.handle_key(ord("n"), src)
    _set(src, gain=100, backlight_compensation=10)
    plan = src.controls.plan_reset()
    calls = {"plan_reset": 0, "reset_to_defaults": 0}
    for meth in calls:
        real = getattr(src.controls, meth)

        def counted(*a, _real=real, _m=meth, **k):
            calls[_m] += 1
            return _real(*a, **k)
        monkeypatch.setattr(src.controls, meth, counted)
    w0 = len(src._sim.writes)
    st.handle_key(ord("p"), src)
    assert st.messages[-1].startswith("preset bench (1/1): ")
    st.handle_key(ord("p"), src)
    assert st.messages[-1] == ("preset defaults: the camera's own defaults, auto/manual modes left alone; 2 to write:"
                               " backlight_compensation 10->32, gain 100->32 - P loads")
    assert calls == {"plan_reset": 1, "reset_to_defaults": 0} and len(src._sim.writes) == w0   # p writes nothing
    st.handle_key(ord("P"), src)
    st.poll(timeout=5)
    assert calls == {"plan_reset": 1, "reset_to_defaults": 1}
    assert st.messages[-1] == "preset defaults loaded: 2 written" and st.last_error == ""
    assert len(src._sim.writes) == w0 + 2
    assert {n: src.controls.get(n) for n in plan} == {n: d for n, (_, d) in plan.items()}
    st.handle_key(ord("p"), src)                                    # after the defaults: the first saved preset
    assert st.messages[-1].startswith("preset bench (1/1): ")
    st.choose_preset(src, "defaults")                               # by name, as `presets load defaults`
    assert st.messages[-1] == "preset defaults: the camera's own defaults, auto/manual modes left alone; " \
                              "already at its values - nothing to write"


def test_defaults_written_with_P_show_on_the_control_line_and_the_camera_is_not_read_per_frame(store, monkeypatch):
    """After P the LED / gain line shows the defaults from the write results (#67's cache), and the device reads
    are the same whether 20 or 300 frames follow: p reads once, nothing reads per frame."""
    from opticmix_vision import viewer
    real_overlay = viewer._overlay

    def run(frames: int):
        src = SyntheticStereoSource(eye_size=(64, 32), fps=1000.0)
        _set(src, gain=100, backlight_compensation=10)
        reads, real_read = [], src._sim.read
        monkeypatch.setattr(src._sim, "read", lambda k: reads.append(k) or real_read(k))
        last: list[str] = []
        monkeypatch.setattr(viewer, "_overlay",
                            lambda *a, **k: last.__setitem__(slice(None), real_overlay(*a, **k)) or list(last))
        keys = [-1, ord("p"), -1, ord("P")] + [-1] * frames
        _HeldKeys([(float(i), k) for i, k in enumerate(keys)]).install(monkeypatch)
        st = ViewerState()
        run_viewer(src, state=st)
        assert src.controls.get("gain") == 32 and src.controls.get("backlight_compensation") == 32
        return len(reads), next(t for t in last if t.startswith("LED ")), st

    (r20, line20, st), (r300, line300, _) = run(20), run(300)
    assert r20 == r300
    assert line20 == line300 and line20.startswith("LED 32  exp ") and "  gain 32  (as opened" in line20
    assert st._known["gain"] == 32 and st._known["backlight_compensation"] == 32
    assert "preset defaults loaded: 2 written" in st.messages


def test_defaults_loaded_while_recording_are_in_the_json(store, tmp_path):
    src = SyntheticStereoSource(eye_size=(64, 32), fps=1000.0)
    _set(src, gain=100)
    st = ViewerState()

    def keys():
        yield from (ord("v"), -1, ord("p"), ord("P"))
        while st._loading is not None:
            yield -1
        yield from (-1, ord("v"))

    r = _recorded_changes(src, st, keys(), tmp_path / "cap")
    assert [(c["name"], c["value"]) for c in r["control_changes"]] == [("preset", "defaults"), ("gain", 32)]
    assert r["control_changes"][0]["status"] == "loaded" and r["controls_at_end"]["gain"] == 32


def test_holding_P_on_the_defaults_resets_once(store, monkeypatch):
    src = SyntheticStereoSource(eye_size=(64, 32), fps=1000.0)
    _set(src, gain=100)
    st = ViewerState()
    st.handle_key(ord("p"), src)                                    # no saved preset: the defaults
    resets, real = [], src.controls.reset_to_defaults
    monkeypatch.setattr(src.controls, "reset_to_defaults", lambda **k: resets.append(k) or real(**k))
    _HeldKeys(_held(ord("P"))).install(monkeypatch)
    run_viewer(src, state=st)
    assert len(resets) == 1 and src.controls.get("gain") == 32


def test_holding_c_shows_the_calibration_once_instead_of_toggling_it_on_and_off(calib_store, tmp_path, monkeypatch):
    """20 arrivals of a held c would show and hide it 10 times each, ending hidden; held, it is one press."""
    _install_calibration(tmp_path)
    st = ViewerState()
    _HeldKeys(_held(ord("c"))).install(monkeypatch)
    run_viewer(SyntheticStereoSource(eye_size=(64, 32), fps=1000.0), state=st)
    assert len(st._calib_lines) == 4 and st._calib_lines[0].startswith("calibration S1-0042 (Weevia S1)")



def test_the_calibration_messages_and_help_wrap_to_the_window_width(calib_store, tmp_path, monkeypatch):
    """About 9 pixels a character: a window 808 pixels wide (two 400-pixel eyes and the gap) takes 88, half
    the scale 43, two 800-pixel eyes 177; nothing is lost in the wrapping."""
    from opticmix_vision import viewer
    from opticmix_vision.viewer import KEY_HELP
    _install_calibration(tmp_path)
    real_overlay = viewer._overlay
    long_msg = "a message " * 30
    for eye_w, scale, cols in ((400, 1.0, 88), (400, 0.5, 43), (800, 1.0, 177)):
        last: list[str] = []
        monkeypatch.setattr(viewer, "_overlay",
                            lambda *a, **k: last.__setitem__(slice(None), real_overlay(*a, **k)) or list(last))
        _HeldKeys([(0.0, ord("h")), (1.0, ord("c")), (2.0, -1)]).install(monkeypatch)
        st = ViewerState()
        st.messages.append(long_msg)
        run_viewer(SyntheticStereoSource(eye_size=(eye_w, 32), fps=1000.0), state=st, scale=scale)
        after = last[next(i for i, t in enumerate(last) if t.startswith("saturated")) + 1:]
        assert max(len(t) for t in after) <= cols and max(len(t) for t in after) > cols - 12, (eye_w, scale)
        text = " ".join(after)
        assert KEY_HELP in text and long_msg.strip() in text and "R 1.000 deg" in text


def test_backend_without_device_goes_to_the_weevia_s1_lookup(monkeypatch):
    """--backend msmf without --device used to be dropped (DirectShow opened); it now goes to
    DeviceConfig.weevia_s1(backend=...). With --device, DirectShow stays the default."""
    from types import SimpleNamespace
    from opticmix_vision import device, viewer
    got = []
    monkeypatch.setattr(device.DeviceConfig, "weevia_s1", classmethod(lambda cls, **kw: got.append(kw) or "cfg"))
    monkeypatch.setattr(device, "Device", lambda cfg: SimpleNamespace(open=lambda: cfg))

    def opened(*argv):
        return viewer._open_source(viewer.build_parser().parse_args(list(argv)))
    assert opened("--backend", "msmf") == "cfg" and got == [{"backend": "msmf"}]
    assert opened() == "cfg" and got[-1] == {}                      # not given: weevia_s1's own (DirectShow)
    assert opened("--device", "0", "--layout", "sbs", "--lr-order", "first_is_right").backend == "dshow"
    assert opened("--device", "0", "--layout", "sbs", "--lr-order", "first_is_right",
                  "--backend", "msmf").backend == "msmf"
