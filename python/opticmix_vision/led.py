"""IR LED(플러드 조명) 제어 — 얇은 래퍼. 실제 통로는 UVC Backlight Compensation(`controls` 의
`backlight_compensation`)이고, Device.set_prop("CAP_PROP_BACKLIGHT") 를 거쳐 쓰기 가드를 지킨다.

측정(2026-09-13, 2개체 재현): 0~16 꺼짐, 24 에서 켜지기 시작, 96 초과는 무시(96 으로 읽힘).
readback 은 드라이버가 받은 값이지 실제 광량의 증거가 아니다 — 켜졌는지는 프레임 밝기로 확인한다(`probe()` 는 그 보조).
정규화(0~1) API 는 value_range 를 받았을 때만 동작한다 — README 대로 value_range=(0, 96) (0 = 꺼짐, 24 부터 빛).
전원을 뺐다 꽂은 뒤 값이 남는지는 미검증.
"""

from __future__ import annotations

from typing import Iterable, Protocol, runtime_checkable

__all__ = ["PropAccessor", "LedControl", "LedControlUnavailable", "DEFAULT_LED_PROP",
           "UNAVAILABLE_REASON", "RANGE_UNKNOWN_REASON"]

# UVC Backlight Compensation = IR LED (2026-09-13 측정). Device 가 controls 로 보낸다.
DEFAULT_LED_PROP = "CAP_PROP_BACKLIGHT"

UNAVAILABLE_REASON = (
    "LedControl(prop=None) — LED 컨트롤을 명시적으로 비활성화한 상태다. "
    f"LED 컨트롤은 {DEFAULT_LED_PROP} (UVC Backlight Compensation, 2026-09-13 측정)."
)
RANGE_UNKNOWN_REASON = (
    "value_range 미지정 — 정규화(0~1) API 는 범위가 있어야 한다. raw set()/get() 을 쓰거나 "
    "측정 범위(0 꺼짐 · 24 점등 · 96 최대)를 value_range=(0, 96) 로 넘길 것."
)

_UNSET = object()      # prop 인자 '미지정'(→ 기본 컨트롤) 과 '명시적 None'(→ 비활성) 을 구분한다


class LedControlUnavailable(NotImplementedError):
    """LED 제어 경로가 없다 (prop=None 명시) 또는 정규화에 필요한 값 범위가 없다."""


@runtime_checkable
class PropAccessor(Protocol):
    """`Device` 가 만족하는 프로퍼티 통로. 테스트에서는 가짜 객체를 넣는다."""

    def get_prop(self, prop: str | int) -> float: ...
    def set_prop(self, prop: str | int, value: float) -> bool: ...


class LedControl:
    """prop: 미지정이면 `DEFAULT_LED_PROP`(CAP_PROP_BACKLIGHT). 문자열(OpenCV 프로퍼티 이름) 또는 정수 ID 로
    바꿀 수 있고, **명시적으로 None** 을 주면 비활성(`LedControlUnavailable`).
    value_range: (min, max) — `set_level`/`get_level` (0.0~1.0 정규화) 에만 필요. ❓ 브링업 실측값을 넣는다.
    """

    def __init__(self, props: PropAccessor, *, prop: str | int | None | object = _UNSET,
                 value_range: tuple[float, float] | None = None) -> None:
        self._props = props
        self.prop: str | int | None = DEFAULT_LED_PROP if prop is _UNSET else prop  # type: ignore[assignment]
        if value_range is not None:
            lo, hi = float(value_range[0]), float(value_range[1])
            if not hi > lo:
                raise ValueError(f"value_range 는 (min, max), max > min: {value_range!r}")
            value_range = (lo, hi)
        self.value_range = value_range

    @property
    def available(self) -> bool:
        return self.prop is not None

    def _require(self) -> str | int:
        if self.prop is None:
            raise LedControlUnavailable(UNAVAILABLE_REASON)
        return self.prop

    # ---- raw -----------------------------------------------------------------
    def get(self) -> float:
        return float(self._props.get_prop(self._require()))

    def set(self, value: float) -> float:
        """raw 값을 쓰고 readback 을 돌려준다. readback == value 여도 LED 가 바뀌었다는 증거는 아니다."""
        p = self._require()
        self._props.set_prop(p, float(value))
        return float(self._props.get_prop(p))

    # ---- normalized 0..1 (value_range 가 있을 때만) --------------------------------
    def set_level(self, level: float) -> float:
        if self.value_range is None:
            raise LedControlUnavailable(RANGE_UNKNOWN_REASON)
        if not 0.0 <= float(level) <= 1.0:
            raise ValueError(f"level 은 0.0~1.0: {level!r}")
        lo, hi = self.value_range
        # The control takes whole numbers only (0.3 x 96 = 28.8 would be refused), so round first.
        rb = self.set(round(lo + (hi - lo) * float(level)))
        return (rb - lo) / (hi - lo)

    def get_level(self) -> float:
        if self.value_range is None:
            raise LedControlUnavailable(RANGE_UNKNOWN_REASON)
        lo, hi = self.value_range
        return (self.get() - lo) / (hi - lo)

    # ---- bring-up aid -------------------------------------------------------
    def probe(self, candidates: Iterable[str | int]) -> dict[str, float | None]:
        """후보 컨트롤들의 현재 값을 읽는다 (쓰지 않는다). 읽기 실패는 None. 어느 것이 LED 인지는 사람이 바꿔 보고 판정한다."""
        out: dict[str, float | None] = {}
        for c in candidates:
            try:
                out[str(c)] = float(self._props.get_prop(c))
            except Exception:                               # 없는 컨트롤 — 값이 아니라 None 으로 남긴다
                out[str(c)] = None
        return out
