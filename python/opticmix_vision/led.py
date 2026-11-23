"""IR LED (flood illuminator) control, a thin wrapper. The actual path is UVC Backlight Compensation
(`backlight_compensation` in `controls`); writes go through Device.set_prop("CAP_PROP_BACKLIGHT") and keep the write guard.

Measured 2026-09-13: values above 96 are ignored, read back as 96 (two units); 0-16 off and starts to light at 24 (one unit).
The read-back value is what the driver accepted, not evidence of the actual light output; check frame brightness
to see whether it is on (`probe()` helps with that).
The normalized (0-1) API works only when value_range is given; per the README, value_range=(0, 96) (0 = off, light from 24).
Whether the value survives unplugging and re-plugging power is not verified.
"""

from __future__ import annotations

from typing import Iterable, Protocol, runtime_checkable

__all__ = ["PropAccessor", "LedControl", "LedControlUnavailable", "DEFAULT_LED_PROP",
           "UNAVAILABLE_REASON", "RANGE_UNKNOWN_REASON"]

# UVC Backlight Compensation = IR LED (measured 2026-09-13). Device sends it through controls.
DEFAULT_LED_PROP = "CAP_PROP_BACKLIGHT"

UNAVAILABLE_REASON = (
    "LedControl(prop=None) — the LED control was explicitly disabled. "
    f"The LED control is {DEFAULT_LED_PROP} (UVC Backlight Compensation, measured 2026-09-13)."
)
RANGE_UNKNOWN_REASON = (
    "value_range not set — the normalized (0..1) API needs a range. Use raw set()/get(), or pass "
    "the measured range (0 off · 24 on · 96 max) as value_range=(0, 96)."
)

_UNSET = object()      # tells the prop argument 'not given' (-> default control) apart from 'explicit None' (-> disabled)


class LedControlUnavailable(NotImplementedError):
    """There is no LED control path (prop=None given explicitly), or the value range needed for normalization is missing."""


@runtime_checkable
class PropAccessor(Protocol):
    """The property path that `Device` satisfies. Tests pass a fake object."""

    def get_prop(self, prop: str | int) -> float: ...
    def set_prop(self, prop: str | int, value: float) -> bool: ...


class LedControl:
    """prop: if not given, `DEFAULT_LED_PROP` (CAP_PROP_BACKLIGHT). Can be changed to a string (OpenCV property
    name) or an integer ID; passing **None explicitly** disables it (`LedControlUnavailable`).
    value_range: (min, max), needed only for `set_level`/`get_level` (0.0-1.0 normalized). Unverified: pass the
    value measured at bring-up.
    """

    def __init__(self, props: PropAccessor, *, prop: str | int | None | object = _UNSET,
                 value_range: tuple[float, float] | None = None) -> None:
        self._props = props
        self.prop: str | int | None = DEFAULT_LED_PROP if prop is _UNSET else prop  # type: ignore[assignment]
        if value_range is not None:
            lo, hi = float(value_range[0]), float(value_range[1])
            if not hi > lo:
                raise ValueError(f"value_range must be (min, max) with max > min: {value_range!r}")
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
        """Writes a raw value and returns the read-back value. Even if readback == value, that is no evidence the LED changed."""
        p = self._require()
        self._props.set_prop(p, float(value))
        return float(self._props.get_prop(p))

    # ---- normalized 0..1 (only when value_range is set) ----------------------------
    def set_level(self, level: float) -> float:
        if self.value_range is None:
            raise LedControlUnavailable(RANGE_UNKNOWN_REASON)
        if not 0.0 <= float(level) <= 1.0:
            raise ValueError(f"level must be within 0.0..1.0: {level!r}")
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
        """Reads the current value of each candidate control (does not write). A failed read is None. Which one is the LED is decided by a person changing it and looking."""
        out: dict[str, float | None] = {}
        for c in candidates:
            try:
                out[str(c)] = float(self._props.get_prop(c))
            except Exception:                               # control does not exist: keep None, not a value
                out[str(c)] = None
        return out
