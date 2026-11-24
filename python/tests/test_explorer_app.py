"""Tk 위젯은 단위 테스트하지 않는다. 창이 만들어지고 닫히는지, 배선이 맞는지만 본다.

**이 프로세스에서 Tk 루트는 하나만 만든다.** 여러 번 만들고 부수면 인터프리터
상태가 남아 `invalid command name "tcl_findLibrary"` 로 무작위 실패한다. 그래서
디스플레이 유무도 임시 루트를 띄워 확인하지 않고, fixture 가 앱을 만들다 실패하면
건너뛴다 — 실제 앱도 루트를 한 번만 만든다.
"""
from __future__ import annotations

import pytest

try:   # not importorskip: a Python with _tkinter but no Tk library raises ImportError, which it does not skip
    import tkinter as tk  # noqa: F401
except ImportError as exc:
    pytest.skip(f"no Tk: {exc}", allow_module_level=True)


@pytest.fixture(scope="module")
def app():
    from opticmix_vision.explorer.app import ExplorerApp

    try:
        a = ExplorerApp(synthetic=True)
    except tk.TclError as exc:                 # 디스플레이 없는 환경
        pytest.skip(f"디스플레이 없음: {exc}")
    a.root.update()
    yield a
    a.close()
    assert not a.worker.is_running, "창을 닫으면 캡처 스레드도 멈춰야 한다"


def test_window_builds_and_draws(app):
    app.root.update()
    assert app.root.winfo_exists()


def test_stats_say_the_frame_rate_is_measured_not_requested(app):
    app._refresh_stats()
    text = app.stats_var.get()
    assert "FRAMERATE" in text and "measured, not requested" in text
    assert "MODEL: SYNTHETIC" in text   # generated frames are not labelled as a Weevia S1


def _open(app):
    import time
    t0 = time.monotonic()
    while getattr(app, "source", None) is None and time.monotonic() - t0 < 5:
        app.root.update()
        time.sleep(0.02)
    app._open_settings()
    return app.settings


def test_settings_window_opens_once_and_carries_every_control(app):
    s = _open(app)
    app._open_settings()
    assert app.settings is s, "톱니를 다시 눌러도 창이 하나여야 한다"
    assert set(s.vars) == set(app.source.controls.names())
    assert not s.mono_body.winfo_ismapped(), "흑백 무효 묶음은 접힌 채로 연다"


def test_the_illuminator_value_reads_off_below_the_measured_onset(app):
    s = _open(app)
    s.vars["backlight_compensation"].set(10)
    s._apply("backlight_compensation")
    assert s.value_labels["backlight_compensation"].cget("text") == "off"
    s.vars["backlight_compensation"].set(48)
    s._apply("backlight_compensation")
    assert s.value_labels["backlight_compensation"].cget("text") == "48"


def test_exposure_slider_is_disabled_while_auto_exposure_is_on(app):
    s = _open(app)
    s.vars["auto_exposure"].set("auto")
    s._apply("auto_exposure")
    assert str(s.widgets["exposure"].cget("state")) == "disabled"
    s.vars["auto_exposure"].set("manual")
    s._apply("auto_exposure")
    assert str(s.widgets["exposure"].cget("state")) == "normal"


def test_reset_dialog_shows_the_plan_or_says_there_is_nothing_to_do(app):
    s = _open(app)
    c = app.source.controls
    c.reset_to_defaults()                         # 앞 테스트가 바꾼 값을 되돌린다
    c.flush(timeout=5)
    s._open_reset()
    assert str(s.reset_confirm.cget("state")) == "disabled"
    s.reset_dialog.destroy()
    c.set("gain", 100)
    c.flush(timeout=5)
    s._open_reset()
    assert str(s.reset_confirm.cget("state")) == "normal"
    s._do_reset()
    c.flush(timeout=5)
    assert c.get("gain") == 32



def test_a_value_the_camera_did_not_keep_is_shown_and_the_slider_snaps_back(app):
    """2026-09-24: 게인 8 을 드라이버가 받았는데 카메라는 0 을 지켰다 — 창이 그걸 말해야 한다."""
    s = _open(app)
    c = app.source.controls
    before = c.get("gain")
    app.source._sim.ignore_writes.add("gain")
    try:
        s.vars["gain"].set(before + 20)
        s._apply("gain")
        c.flush(timeout=5)
        s._poll()
        assert s.status_labels["gain"].cget("text") == f"camera kept {before}"
        assert s.vars["gain"].get() == before
    finally:
        app.source._sim.ignore_writes.discard("gain")

def test_dragging_a_slider_writes_once_on_release_not_on_every_step(app):
    """쓰기 간격 12 ms(2026-09-28)에서 끌 때마다 쓰면 드래그 한 번이 쓰기 수십 번(0.6 s 에 38회)이 된다 — 놓을 때 한 번."""
    s = _open(app)
    c = app.source.controls
    be = app.source._sim
    w = s.widgets["gain"]
    start = c.get("gain")
    n0 = len(be.writes)
    for v in range(start + 1, start + 11):             # 드래그 10단계
        w.set(v)
        app.root.update()
    c.flush(timeout=5)
    assert len(be.writes) == n0, "끄는 동안에는 쓰지 않는다"
    assert s.value_labels["gain"].cget("text") == str(start + 10), "값 칸은 드래그를 따라간다"
    w.event_generate("<ButtonRelease-1>")
    app.root.update()
    c.flush(timeout=5)
    assert be.writes[n0:] == [("gain", start + 10, None)]


def test_releasing_a_locked_slider_writes_nothing(app):
    s = _open(app)
    c = app.source.controls
    s.vars["auto_exposure"].set("auto")
    s._apply("auto_exposure")
    c.flush(timeout=5)
    be = app.source._sim
    n0 = len(be.writes)
    try:
        s.widgets["exposure"].event_generate("<ButtonRelease-1>")
        app.root.update()
        c.flush(timeout=5)
        assert len(be.writes) == n0
        assert "switch AUTO EXPOSURE" in s.status_labels["exposure"].cget("text")
    finally:
        s.vars["auto_exposure"].set("manual")
        s._apply("auto_exposure")
        c.flush(timeout=5)

# --- 영상이 창 안에 들어가는지 (Tk 없이 계산만) ---------------------------------

def test_a_1600x800_pair_is_shrunk_to_fit_a_smaller_window():
    """내림 나눗셈을 쓰면 1600/960 이 step 1 이 되어 축소가 안 걸리고 잘렸다.
    실제로 그렇게 보였고, 사용자가 먼저 발견했다."""
    from opticmix_vision.explorer.app import _fit_step

    step = _fit_step(1600, 800, 960, 460)
    assert step == 2
    assert 1600 // step <= 960 and 800 // step <= 460


def test_height_is_considered_not_only_width():
    from opticmix_vision.explorer.app import _fit_step

    # 폭은 넉넉하지만 높이가 모자란 상자
    assert _fit_step(800, 800, 900, 200) == 4


def test_a_small_frame_is_never_enlarged():
    from opticmix_vision.explorer.app import _fit_step

    assert _fit_step(640, 400, 960, 460) == 1


def test_a_zero_sized_box_does_not_divide_by_zero():
    from opticmix_vision.explorer.app import _fit_step

    assert _fit_step(1600, 800, 0, 0) == 1


def test_preset_buttons_save_then_load_through_the_confirm_dialog(app, tmp_path):
    """Save reads only; Load lists the change, writes nothing until 'Load', then one write."""
    s = _open(app)
    c = app.source.controls
    be = app.source._sim
    assert str(s.save_preset_button.cget("state")) == "normal"
    assert str(s.load_preset_button.cget("state")) == "normal"
    path = str(tmp_path / "p.json")
    n0 = len(be.writes)
    s._save_preset(path)
    assert len(be.writes) == n0 and s.preset_label.cget("text") == "Saved p.json"
    s._open_load_preset(path)
    assert str(s.preset_confirm.cget("state")) == "disabled", "nothing differs: nothing to load"
    s.preset_dialog.destroy()
    before = c.get("gain")
    c.set("gain", before + 30)
    c.flush(timeout=5)
    n0 = len(be.writes)
    s._open_load_preset(path)
    assert str(s.preset_confirm.cget("state")) == "normal"
    assert len(be.writes) == n0, "the dialog alone writes nothing"
    s.preset_confirm.invoke()
    c.flush(timeout=5)
    s._poll()
    assert be.writes[n0:] == [("gain", before, None)]
    assert s.vars["gain"].get() == before
    assert s.preset_label.cget("text").startswith("Loaded: 1 queued")


def test_a_bad_preset_is_reported_and_nothing_is_written(app, tmp_path):
    s = _open(app)
    be = app.source._sim
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    n0 = len(be.writes)
    s._open_load_preset(str(bad))
    assert s.preset_label.cget("text").startswith("Not loaded:")
    assert len(be.writes) == n0


def _preset(c, path, **device):
    import json
    c.save_preset(path)
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["device"].update(device)
    path.write_text(json.dumps(doc), encoding="utf-8")


def test_load_preset_lists_this_models_presets_then_other_file(app, tmp_path, monkeypatch):
    from opticmix_vision import presets
    monkeypatch.setattr(presets, "store_dir", lambda: tmp_path)
    s = _open(app)
    c = app.source.controls
    _preset(c, tmp_path / "mine.json")
    _preset(c, tmp_path / "other.json", model="WN Dual Camera")
    s._open_load_preset()
    texts = [w.cget("text") for w in s.preset_picker.winfo_children()]
    assert any(t.startswith("mine") for t in texts) and not any(t.startswith("other") for t in texts)
    assert texts[-1] == "Other file…"
    s.preset_picker.winfo_children()[0].invoke()
    assert s.preset_dialog.winfo_exists() and not s.preset_picker.winfo_exists()
    s.preset_dialog.destroy()


def test_a_preset_from_another_model_offers_load_anyway(app, tmp_path):
    s = _open(app)
    c = app.source.controls
    be = app.source._sim
    path = tmp_path / "cam.json"
    _preset(c, path, model="WN Dual Camera")
    before = c.get("gain")
    c.set("gain", before + 30)
    c.flush(timeout=5)
    n0 = len(be.writes)
    s._open_load_preset(str(path))
    assert s.preset_confirm.cget("text") == "Load anyway"
    assert len(be.writes) == n0
    s.preset_confirm.invoke()                     # re-plans with force: the usual list and 'Load'
    assert s.preset_confirm.cget("text") == "Load" and len(be.writes) == n0
    s.preset_confirm.invoke()
    c.flush(timeout=5)
    assert be.writes[n0:] == [("gain", before, None)]


def test_the_settings_poll_and_the_redraw_read_no_control_while_streaming(app, monkeypatch):
    """The settings window's poll (every POLL_MS) and the redraw look at the write queue only, never the camera:
    the event loop runs both several times and no control is read."""
    import time
    s = _open(app)
    c = app.source.controls
    n = {"reads": 0, "polls": 0, "ticks": 0}
    for meth in ("get", "snapshot", "describe"):
        real = getattr(c, meth)
        monkeypatch.setattr(c, meth, lambda *a, _real=real, **k: (n.__setitem__("reads", n["reads"] + 1), _real(*a, **k))[1])
    for name, key in (("_poll", "polls"), ("_tick", "ticks")):
        obj = s if name == "_poll" else app
        real = getattr(obj, name)
        monkeypatch.setattr(obj, name, lambda _real=real, _key=key: (n.__setitem__(_key, n[_key] + 1), _real())[1])
    t0 = time.monotonic()
    while time.monotonic() - t0 < 1.2:
        app.root.update()
        time.sleep(0.01)
    assert n["polls"] >= 3 and n["ticks"] >= 10, n
    assert n["reads"] == 0, n
