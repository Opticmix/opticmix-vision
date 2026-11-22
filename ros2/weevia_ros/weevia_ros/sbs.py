"""Weevia S1 SBS 한 장 → 좌우 눈, 그리고 눈별 정지 감시. ROS 를 import 하지 않는다.

SBS 앞 절반(열 0–799) = 오른쪽 눈, 뒤 절반(800–1599) = 왼쪽 눈(`first_is_right`).
"""
from __future__ import annotations

import numpy as np


def split_sbs(img: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(H, 2H) mono → (left, right). 복사하지 않는 view 다."""
    if img.ndim != 2 or img.shape[1] != 2 * img.shape[0]:
        raise ValueError(f"SBS mono (H, 2H) expected, got {img.shape}")
    w = img.shape[1] // 2
    return img[:, w:], img[:, :w]


def eye_outputs(img: np.ndarray, stamp, left_frame: str, right_frame: str) -> list[tuple]:
    """SBS 한 장 → [(눈, 영상, 시각 도장, frame_id)] 두 줄(left 먼저). 시각 도장은 입력 그대로 넘긴다(새로 찍지 않음)."""
    left, right = split_sbs(img)
    return [("left", left, stamp, left_frame), ("right", right, stamp, right_frame)]


class EyeDiag:
    """진단 발행 한 번 사이의 눈별 값을 모은다. 시각은 호출자가 준다(초, 단조).

    - 정지 이유(StallMonitor)는 그 사이 한 장이라도 있었으면 다음 report 까지 남긴다(1 Hz 사이 0 프레임 한 장도 보고).
    - 마지막 입력 뒤 no_input_s 초 이상 지나면 WARN 「no input」(입력이 한 번도 없었으면 생성 시각부터 센다).
    - 눈별 평균 밝기(그 사이 프레임 평균의 평균)·fps 는 값으로만 낸다(판정에 쓰지 않음).
    """

    OK, WARN, ERROR = 0, 1, 2
    EYES = ("left", "right")

    def __init__(self, t0: float, no_input_s: float = 2.0) -> None:
        self.no_input_s = no_input_s
        self._last_frame_t = t0
        self._current: dict[str, str | None] = {e: None for e in self.EYES}
        self._reset(t0)

    def _reset(self, t: float) -> None:
        self._win_start = t
        self._n = 0
        self._sum = {e: 0.0 for e in self.EYES}
        self._latched: dict[str, str | None] = {e: None for e in self.EYES}

    def on_frame(self, t: float, left: np.ndarray, right: np.ndarray, reasons: dict[str, str | None]) -> None:
        self._n += 1
        self._last_frame_t = t
        for eye, img in (("left", left), ("right", right)):
            self._sum[eye] += float(img.mean())
            self._current[eye] = reasons[eye]
            if reasons[eye] and self._latched[eye] is None:
                self._latched[eye] = reasons[eye]

    def report(self, t: float) -> tuple[int, str, list[tuple[str, str]]]:
        """(수준, 요약, [(키, 값)]) 를 내고 다음 구간을 시작한다."""
        dt = t - self._win_start
        fps = self._n / dt if dt > 0 else 0.0
        values: list[tuple[str, str]] = []
        errors = []
        for eye in self.EYES:
            reason = self._latched[eye] or self._current[eye]
            if reason:
                errors.append(reason)
            mean = f"{self._sum[eye] / self._n:.1f}" if self._n else "n/a"
            values += [(eye, reason or "ok"), (f"{eye} mean brightness", mean), (f"{eye} fps", f"{fps:.1f}")]
        idle = t - self._last_frame_t
        warn = f"no input for {idle:.1f} s" if idle >= self.no_input_s else None
        level = self.ERROR if errors else self.WARN if warn else self.OK
        message = "; ".join([*errors, *([warn] if warn else [])]) or "both eyes changing"
        self._reset(t)
        return level, message, values


class StallMonitor:
    """눈별로 「전부 0 이 zero_frames 장 연속」 또는 「직전과 바이트 동일이 same_frames 장 연속」이면 이유 문자열.

    same_frames 셈은 SDK `Device._check_stall` 과 같다(같은 영상 N장 = 비교 N−1번 동일). 알리기만 한다.
    """

    def __init__(self, zero_frames: int = 1, same_frames: int = 90) -> None:
        if zero_frames < 1 or same_frames < 2:
            raise ValueError("zero_frames >= 1, same_frames >= 2")
        self.zero_frames, self.same_frames = zero_frames, same_frames
        self._zero = [0, 0]
        self._same = [0, 0]
        self._prev: list[np.ndarray | None] = [None, None]

    def update(self, left: np.ndarray, right: np.ndarray) -> dict[str, str | None]:
        out: dict[str, str | None] = {}
        for i, (eye, img) in enumerate((("left", left), ("right", right))):
            self._zero[i] = self._zero[i] + 1 if not img.any() else 0
            prev = self._prev[i]
            self._same[i] = self._same[i] + 1 if prev is not None and np.array_equal(img, prev) else 0
            self._prev[i] = img.copy()
            if self._zero[i] >= self.zero_frames:
                out[eye] = f"{eye} eye all zero for {self._zero[i]} frame(s)"
            elif self._same[i] + 1 >= self.same_frames:
                out[eye] = f"{eye} eye sent the same image for {self._same[i] + 1} frames"
            else:
                out[eye] = None
        return out
