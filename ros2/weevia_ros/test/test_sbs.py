import numpy as np
import pytest

from weevia_ros.sbs import EyeDiag, StallMonitor, eye_outputs, split_sbs


def sbs(front: int, back: int) -> np.ndarray:
    img = np.empty((800, 1600), np.uint8)
    img[:, :800] = front
    img[:, 800:] = back
    return img


def test_split_first_is_right():
    left, right = split_sbs(sbs(front=11, back=22))
    assert right.shape == left.shape == (800, 800)
    assert int(right[0, 0]) == 11      # 앞 절반(열 0–799) = 오른쪽 눈
    assert int(left[0, 0]) == 22       # 뒤 절반(열 800–1599) = 왼쪽 눈


def test_split_rejects_non_sbs():
    with pytest.raises(ValueError):
        split_sbs(np.zeros((800, 800), np.uint8))


def noisy(seed: int) -> np.ndarray:
    return np.random.default_rng(seed).integers(1, 255, (800, 800), dtype=np.uint8)


def test_stall_normal_ok():
    m = StallMonitor(zero_frames=1, same_frames=90)
    for i in range(200):
        assert m.update(noisy(i), noisy(i + 1000)) == {"left": None, "right": None}


def test_stall_all_zero_one_frame():
    m = StallMonitor(zero_frames=1, same_frames=90)
    out = m.update(np.zeros((800, 800), np.uint8), noisy(1))
    assert out["left"] is not None and "zero" in out["left"]
    assert out["right"] is None


def test_stall_same_bytes_90_vs_89():
    frozen = noisy(7)
    m = StallMonitor(zero_frames=1, same_frames=90)
    outs = [m.update(noisy(100 + i), frozen) for i in range(90)]
    assert all(o["right"] is None for o in outs[:89])      # 89장 연속 = OK
    assert outs[89]["right"] is not None                     # 90장째 = ERROR
    assert all(o["left"] is None for o in outs)


def test_stall_recovers_after_change():
    frozen = noisy(7)
    m = StallMonitor(zero_frames=1, same_frames=3)
    for _ in range(3):
        m.update(noisy(1), frozen)
    assert m.update(noisy(2), noisy(8))["right"] is None


def test_stall_late_freeze_after_normal_frames():
    # 정상(매번 다른) 영상 뒤에 같은 영상이 same_frames 장 이어지면 그 마지막 장에서 ERROR, 한 장 앞은 None.
    frozen = noisy(7)
    m = StallMonitor(zero_frames=1, same_frames=3)
    for i in range(5):
        assert m.update(noisy(200 + i), noisy(300 + i))["right"] is None
    outs = [m.update(noisy(400 + i), frozen)["right"] for i in range(3)]
    assert outs[1] is None                                   # 같은 영상 2장 = OK
    assert outs[2] is not None and "same" in outs[2]         # 같은 영상 3장째 = ERROR


def test_zero_recovers_after_normal_frame():
    # 한 눈 전부 0 이 나온 뒤 정상 프레임이 오면 그 눈은 None 으로 돌아온다.
    m = StallMonitor(zero_frames=1, same_frames=90)
    assert m.update(np.zeros((800, 800), np.uint8), noisy(1))["left"] is not None
    assert m.update(noisy(2), noisy(3)) == {"left": None, "right": None}


def test_stall_does_not_alias_caller_buffer():
    # 호출자가 같은 버퍼를 제자리에서 덮어써도(내용은 매번 다름) 정지로 보지 않는다 — 직전 프레임은 복사해 둔다.
    buf = noisy(1).copy()
    m = StallMonitor(zero_frames=1, same_frames=2)
    for i in range(3):
        buf[:] = noisy(10 + i)
        assert m.update(noisy(20 + i), buf)["right"] is None


def test_eye_outputs_stamp_frame_and_halves():
    # 시각 도장 = 입력 객체 그대로, left = 뒤 절반 + left frame_id, right = 앞 절반 + right frame_id.
    stamp = object()
    outs = eye_outputs(sbs(front=11, back=22), stamp, "L_FRAME", "R_FRAME")
    assert [o[0] for o in outs] == ["left", "right"]
    (le, limg, lst, lf), (re, rimg, rst, rf) = outs
    assert lst is stamp and rst is stamp
    assert (lf, rf) == ("L_FRAME", "R_FRAME")
    assert int(limg[0, 0]) == 22 and int(rimg[0, 0]) == 11
    assert limg.shape == rimg.shape == (800, 800)


def eye(v: int) -> np.ndarray:
    return np.full((800, 800), v, np.uint8)


OKR = {"left": None, "right": None}


def test_diag_values_per_eye_and_fps():
    d = EyeDiag(t0=0.0)
    for i in range(90):
        d.on_frame(i / 90, eye(22), eye(11), OKR)
    level, msg, values = d.report(1.0)
    v = dict(values)
    assert level == EyeDiag.OK and msg == "both eyes changing"
    assert v["left mean brightness"] == "22.0" and v["right mean brightness"] == "11.0"
    assert v["left fps"] == "90.0" and v["right fps"] == "90.0"
    assert v["left"] == "ok" and v["right"] == "ok"


def test_diag_error_latched_until_next_report():
    # 1 초 사이 한 장만 0 이었다가 회복해도 그 구간 report 는 ERROR, 다음 구간(정상만)은 OK.
    d = EyeDiag(t0=0.0)
    d.on_frame(0.1, eye(5), eye(5), OKR)
    d.on_frame(0.2, eye(0), eye(5), {"left": "left eye all zero for 1 frame(s)", "right": None})
    d.on_frame(0.3, eye(5), eye(5), OKR)
    level, msg, values = d.report(1.0)
    assert level == EyeDiag.ERROR and "left eye all zero" in msg
    assert dict(values)["left"].startswith("left eye all zero") and dict(values)["right"] == "ok"
    d.on_frame(1.5, eye(5), eye(5), OKR)
    assert d.report(2.0)[0] == EyeDiag.OK


def test_diag_no_input_warn():
    d = EyeDiag(t0=0.0, no_input_s=2.0)
    assert d.report(1.0)[0] == EyeDiag.OK                     # 시작 1 초: 아직 아님
    level, msg, _ = d.report(2.5)                             # 입력 없이 2.5 초
    assert level == EyeDiag.WARN and "no input" in msg
    d.on_frame(3.0, eye(5), eye(5), OKR)
    assert d.report(4.0)[0] == EyeDiag.OK                     # 1.0 초 전 입력 = 정상
    level, msg, values = d.report(6.5)                        # 마지막 입력 3.5 초 전
    assert level == EyeDiag.WARN and "no input for 3.5 s" in msg
    assert dict(values)["left fps"] == "0.0" and dict(values)["left mean brightness"] == "n/a"
