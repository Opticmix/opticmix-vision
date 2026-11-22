"""우리 SDK(opticmix_vision)로 장치를 열어 SBS 를 다시 만든다. 🔴 제어 접근 0 — controls·extra_props 를 쓰지 않는다.

Linux 경로는 실험 단계다. 정지 검사는 SDK 에서 끄고(stall_frames=0) splitter 가 /diagnostics 로 알린다.
"""
from __future__ import annotations

from typing import Callable

import numpy as np


def capture(sdk, *, device: int, backend: str, publish: Callable[[np.ndarray, int], None],
            should_stop: Callable[[], bool]) -> None:
    cfg = sdk.DeviceConfig(layout="sbs", lr_order="first_is_right", backend=backend, device=device,
                           width=1600, height=800, fps=90, fourcc="MJPG", to_gray=True, stall_frames=0)
    with sdk.Device(cfg) as cam:
        while not should_stop():
            f = cam.read()
            publish(np.hstack((f.right, f.left)), f.host_ns)   # first_is_right 로 다시 붙인다
