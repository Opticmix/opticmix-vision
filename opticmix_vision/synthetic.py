"""합성 스테레오 소스 — 하드웨어 없이 뷰어·캘리브 CLI·예제를 끝까지 돌리기 위한 것. **측정값이 아니다.**

물리 규약을 지킨다: 유한 거리의 점은 왼쪽 카메라에서 더 오른쪽에 맺히므로 R(x) = L(x + d), d > 0.
UVC 컨트롤 흉내: CAP_PROP_BACKLIGHT(LED) / CAP_PROP_EXPOSURE / CAP_PROP_GAIN 값이 밝기에 반영된다 —
뷰어의 키 조작이 화면에서 보이게 하려는 목적이지 실제 장치의 응답 곡선이 아니다.
"""

from __future__ import annotations

import time
from typing import Any

import numpy as np

from .device import NegotiatedMode, StereoFrame

__all__ = ["SyntheticStereoSource"]


def _texture(h: int, w: int, rng: np.random.Generator) -> np.ndarray:
    coarse = rng.random((h // 8 + 2, w // 8 + 2))
    ys = np.linspace(0, coarse.shape[0] - 1.001, h)
    xs = np.linspace(0, coarse.shape[1] - 1.001, w)
    yi, xi = np.floor(ys).astype(int), np.floor(xs).astype(int)
    fy, fx = (ys - yi)[:, None], (xs - xi)[None, :]
    img = (coarse[yi][:, xi] * (1 - fy) * (1 - fx) + coarse[yi + 1][:, xi] * fy * (1 - fx)
           + coarse[yi][:, xi + 1] * (1 - fy) * fx + coarse[yi + 1][:, xi + 1] * fy * fx)
    return img.astype(np.float32)          # 0..1


class SyntheticStereoSource:
    """`Device` 와 같은 표면(read / get_prop / set_prop / mode / close)을 가진 합성 카메라.

    eye_size: 한 눈 (width, height). 프레임(sbs) 폭은 2×width 로 mode 에 보고한다.
    realtime: True 면 fps 에 맞춰 대기한다 (뷰어에서 실제 속도감을 보려고). 테스트는 False.
    """

    _PROPS_DEFAULT = {
        "CAP_PROP_BACKLIGHT": 32.0, "CAP_PROP_EXPOSURE": -5.0, "CAP_PROP_GAIN": 32.0,
        "CAP_PROP_AUTO_EXPOSURE": 0.75, "CAP_PROP_BRIGHTNESS": 0.0,
    }

    def __init__(self, *, eye_size: tuple[int, int] = (320, 200), fps: float = 90.0, disparity: int = 8,
                 seed: int = 0, realtime: bool = False) -> None:
        self.w, self.h = int(eye_size[0]), int(eye_size[1])
        self.fps, self.disparity, self.realtime = float(fps), int(disparity), bool(realtime)
        self._rng = np.random.default_rng(seed)
        self._wide = _texture(self.h, self.w + self.disparity, self._rng)
        self._props: dict[str, float] = dict(self._PROPS_DEFAULT)
        self._props.update({"CAP_PROP_FPS": self.fps, "CAP_PROP_FRAME_WIDTH": float(2 * self.w),
                            "CAP_PROP_FRAME_HEIGHT": float(self.h)})
        self._i = 0
        self._t0 = time.perf_counter()
        self.mode = NegotiatedMode(width=2 * self.w, height=self.h, fps=self.fps, fourcc="SYNT",
                                   backend_name="synthetic")

    # ---- props -----------------------------------------------------------
    @staticmethod
    def _name(prop: str | int) -> str:
        if isinstance(prop, str):
            return prop
        try:
            import cv2
            for n in ("CAP_PROP_BACKLIGHT", "CAP_PROP_EXPOSURE", "CAP_PROP_GAIN", "CAP_PROP_AUTO_EXPOSURE",
                      "CAP_PROP_BRIGHTNESS", "CAP_PROP_FPS", "CAP_PROP_FRAME_WIDTH", "CAP_PROP_FRAME_HEIGHT"):
                if getattr(cv2, n, None) == int(prop):
                    return n
        except ImportError:
            pass
        return f"PROP_{int(prop)}"

    def get_prop(self, prop: str | int) -> float:
        return float(self._props.get(self._name(prop), 0.0))

    def set_prop(self, prop: str | int, value: float) -> bool:
        self._props[self._name(prop)] = float(value)
        return True

    # ---- frames ----------------------------------------------------------
    def _brightness(self) -> float:
        e = self._props["CAP_PROP_EXPOSURE"]; g = self._props["CAP_PROP_GAIN"]; b = self._props["CAP_PROP_BACKLIGHT"]
        return float(np.clip(2.0 ** (e + 5.0) * (g / 32.0) * (0.3 + 0.7 * b / 64.0), 0.02, 4.0))

    def read(self) -> StereoFrame:
        if self.realtime:
            target = self._t0 + self._i / self.fps
            while (rem := target - time.perf_counter()) > 0:
                time.sleep(min(rem, 0.002))
        wide = self._wide.copy()
        # 움직이는 밝은 점 — 프레임이 살아 있다는 신호. 두 눈이 같은 시차로 본다 (wide 위에 그림).
        cx = int((self._i * 3) % (self.w + self.disparity)); cy = int((self._i * 2) % self.h)
        wide[max(0, cy - 3): cy + 3, max(0, cx - 3): cx + 3] = 1.0
        img = np.clip(wide * 200.0 * self._brightness() + 20.0, 0, 255).astype(np.uint8)
        left = img[:, : self.w].copy()
        right = img[:, self.disparity: self.disparity + self.w].copy()
        f = StereoFrame(left=left, right=right, host_ns=time.perf_counter_ns(), index=self._i)
        self._i += 1
        return f

    def close(self) -> None:
        pass

    def __enter__(self) -> "SyntheticStereoSource":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
