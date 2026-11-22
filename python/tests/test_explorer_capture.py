"""캡처 스레드 — 합성 소스로 검증한다. 하드웨어 불필요."""
from __future__ import annotations

import time

from opticmix_vision.explorer.capture import CaptureWorker
from opticmix_vision.synthetic import SyntheticStereoSource


def _wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_latest_returns_none_before_anything_arrives():
    assert CaptureWorker(SyntheticStereoSource).latest() is None


def test_it_produces_frames_and_reports_a_rate():
    w = CaptureWorker(SyntheticStereoSource)
    w.start()
    try:
        assert _wait_until(lambda: w.latest() is not None), "프레임이 오지 않았다"
        assert w.fps > 0
    finally:
        w.stop()
    assert not w.is_running


def test_a_source_that_raises_is_reported_not_swallowed():
    def boom():
        raise RuntimeError("device gone")

    w = CaptureWorker(boom)
    w.start()
    assert _wait_until(lambda: w.error is not None)
    w.stop()
    assert "device gone" in (w.error or "")


def test_the_source_is_closed_when_the_worker_stops():
    closed = []

    class Source(SyntheticStereoSource):
        def close(self):
            closed.append(True)

    w = CaptureWorker(Source)
    w.start()
    assert _wait_until(lambda: w.latest() is not None)
    w.stop()
    assert _wait_until(lambda: closed == [True])


def test_stop_is_idempotent():
    w = CaptureWorker(SyntheticStereoSource)
    w.start(); w.stop(); w.stop()
    assert not w.is_running


def test_frame_rate_averages_the_interval_not_its_reciprocal():
    """드라이버가 프레임을 몰아 주면 간격이 5 ms·17 ms 로 흔들린다. 1/dt 를 평균하면 129 fps 로 부풀고
    (2026-09-28 실카메라에서 탐색기가 105 fps 를 '측정값'으로 표시), 간격을 평균하면 실제 91 fps 가 나온다."""
    from opticmix_vision.explorer.capture import CaptureWorker
    w = CaptureWorker(lambda: None)
    for i in range(400):
        w._note_interval(0.005 if i % 2 else 0.017)
    assert 88 < w.fps < 94, w.fps
