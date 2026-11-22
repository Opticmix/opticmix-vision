"""시뮬레이션 백엔드 — 합성 카메라와 테스트용. **범위·기본값은 지어낸 값**이다(장치 측정값 아님)."""
from __future__ import annotations

import threading
from typing import Iterable, Mapping

from .controls import RawRange

__all__ = ["SIM_RANGES", "SimBackend"]

SIM_RANGES: dict[str, RawRange] = {
    "brightness": RawRange(0, 100, 1, 50, False),
    "contrast": RawRange(0, 100, 1, 50, False),
    "hue": RawRange(0, 128, 1, 64, False),
    "saturation": RawRange(0, 128, 1, 36, False),
    "sharpness": RawRange(0, 100, 1, 28, False),
    "gamma": RawRange(1, 500, 1, 50, False),
    "white_balance": RawRange(0, 255, 1, 128, True),
    "backlight_compensation": RawRange(0, 96, 1, 32, False),
    "gain": RawRange(0, 255, 1, 32, False),
    "power_line_frequency": RawRange(0, 2, 1, 2, False),
    "exposure": RawRange(-11, -3, 1, -5, True),
    "auto_exposure_priority": RawRange(0, 1, 1, 0, False),
}


class SimBackend:
    """값을 dict 에 들고 있는 백엔드. 쓰기를 기록하고, 어느 스레드가 불렀는지 남긴다(테스트가 본다)."""

    name = "sim"
    exposure_units = "log2_s_nominal"
    note = "simulated controls — ranges and defaults are made up, not measured"

    def __init__(self, ranges: Mapping[str, RawRange] | None = None, *, autos: Mapping[str, bool] | None = None,
                 fail_keys: Iterable[str] = (), unranged: Iterable[str] = (),
                 ignore_writes: Iterable[str] = ()) -> None:
        self.ranges = dict(SIM_RANGES if ranges is None else ranges)
        self.values = {k: (r.default if r.default is not None else r.minimum) for k, r in self.ranges.items()}
        self.autos = {k: False for k, r in self.ranges.items() if r.can_auto}
        self.autos.update(autos or {})
        self.fail_keys = set(fail_keys)
        self.unranged = set(unranged)          # 장치가 범위를 주지 않는 척할 키
        self.ignore_writes = set(ignore_writes)  # 쓰기를 받고도 값을 바꾸지 않는 척할 키 (2026-09-24 의 게인 8 → 0)
        self.writes: list[tuple[str, int | None, bool | None]] = []
        self.started = False
        self.closed = False
        self.threads: set[int] = set()

    def _touch(self) -> None:
        self.threads.add(threading.get_ident())

    def start(self) -> None:
        self._touch()
        self.started = True

    def supports(self, key: str) -> bool:
        self._touch()
        return key in self.ranges

    def query(self, key: str) -> RawRange | None:
        self._touch()
        return None if key in self.unranged else self.ranges.get(key)

    def read(self, key: str) -> tuple[int, bool | None]:
        self._touch()
        return self.values[key], self.autos.get(key)

    def write(self, key: str, value: int | None, auto: bool | None) -> None:
        self._touch()
        if key in self.fail_keys:
            raise OSError(f"simulated driver rejected {key}")
        self.writes.append((key, value, auto))
        if key in self.ignore_writes:
            return
        if auto is not None:
            self.autos[key] = auto
        if value is not None:
            self.values[key] = int(value)

    def close(self) -> None:
        self._touch()
        self.closed = True
