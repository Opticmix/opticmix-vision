"""Reads frames on a background thread and holds only the most recent one.

The camera delivers 90 fps. Tk cannot redraw at that rate. A queue would build up latency
and the screen would show the past, so only the latest frame is kept and the rest are dropped;
the screen draws at around 30 fps and the camera runs at its own rate.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

__all__ = ["CaptureWorker"]


class CaptureWorker:
    """Opens a source with `open_source()` and reads until stopped.

    The source only needs `read()`; a `Device` and a synthetic source work the same. If it has
    `close()`, that is called on stop.
    """

    def __init__(self, open_source: Callable[[], Any], *, min_interval: float = 0.0) -> None:
        """min_interval is for synthetic sources. A camera sets its own rate, but a synthetic
        source returns immediately; left alone, FRAMERATE would show thousands and the
        'measured' label would be meaningless."""
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
        """fps = inverse of the averaged interval; averaging 1/dt inflates when intervals jitter (alternating 5 and 17 ms -> 129 vs actual 91)."""
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
            # kept so the window does not freeze without explanation; the screen reads this and shows it.
            self.error = f"{type(exc).__name__}: {exc}"
        finally:
            closer = getattr(source, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:
                    pass
