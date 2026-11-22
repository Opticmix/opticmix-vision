"""배경 스레드에서 프레임을 읽고, 가장 최근 것 하나만 들고 있는다.

카메라는 90 fps 를 낸다. Tk 는 그 속도로 다시 그릴 수 없다. 큐를 쓰면 지연이
쌓여 화면이 과거를 보여주게 되므로, 최신 프레임 한 장만 두고 나머지는 버린다 —
화면은 30 fps 근처로 그리고 카메라는 제 속도로 돈다.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

__all__ = ["CaptureWorker"]


class CaptureWorker:
    """`open_source()` 로 소스를 열고, 멈출 때까지 읽는다.

    소스는 `read()` 만 있으면 된다 — `Device` 든 합성 소스든 같다. `close()` 가
    있으면 멈출 때 불러 준다.
    """

    def __init__(self, open_source: Callable[[], Any], *, min_interval: float = 0.0) -> None:
        """min_interval 은 합성 소스용이다. 카메라는 스스로 속도를 정하지만 합성
        소스는 즉시 돌려주므로, 그대로 두면 FRAMERATE 에 수천이 찍혀 '측정값'
        이라는 표시가 무의미해진다."""
        self._open_source = open_source
        self._min_interval = float(min_interval)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._latest: Any = None
        self.error: str | None = None
        self.fps: float = 0.0
        self._dt_avg = 0.0

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def latest(self) -> Any:
        with self._lock:
            return self._latest

    def start(self) -> None:
        if self.is_running:
            return
        self._stop.clear()
        self.error = None
        self._thread = threading.Thread(target=self._run, name="capture", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        t, self._thread = self._thread, None
        if t is not None:
            t.join(timeout)

    def _note_interval(self, dt: float) -> None:
        """간격을 평균하고 그 역수를 fps 로 — 1/dt 를 평균하면 간격이 흔들릴 때 부푼다(5·17 ms 교대 → 129 vs 실제 91)."""
        if dt <= 0:
            return
        self._dt_avg = 0.9 * self._dt_avg + 0.1 * dt if self._dt_avg > 0 else dt
        self.fps = 1.0 / self._dt_avg

    def _run(self) -> None:
        source = None
        try:
            source = self._open_source()
            t_prev = time.perf_counter()
            while not self._stop.is_set():
                frame = source.read()
                now = time.perf_counter()
                dt, t_prev = now - t_prev, now
                self._note_interval(dt)
                with self._lock:
                    self._latest = frame
                if self._min_interval:
                    self._stop.wait(self._min_interval)
        except Exception as exc:
            # 창이 아무 설명 없이 얼어붙지 않도록 남긴다. 화면이 이걸 읽어 띄운다.
            self.error = f"{type(exc).__name__}: {exc}"
        finally:
            closer = getattr(source, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:
                    pass
