"""실기기 테스트 — 기본 skip. Weevia S1 을 꽂고 `OPTICMIX_HW=1` 로 실행.

    OPTICMIX_HW=1 python -m pytest tests/test_hardware.py -v

카메라는 DeviceConfig.weevia_s1() 로 연다 — 이름으로 찾고 실측 설정을 쓴다. 제어 값은 쓰지 않는다.
"""

from __future__ import annotations

import os

import numpy as np
import pytest

pytestmark = pytest.mark.hardware

_HW = os.environ.get("OPTICMIX_HW") == "1"


@pytest.mark.skipif(not _HW, reason="실기기 필요: OPTICMIX_HW=1")
def test_open_and_read_10_frames():
    from opticmix_vision import Device, DeviceConfig

    cfg = DeviceConfig.weevia_s1()
    with Device(cfg) as cam:
        mode = cam.mode
        assert mode.width > 0 and mode.height > 0
        frames = [cam.read() for _ in range(10)]
    sizes = {f.size for f in frames}
    assert len(sizes) == 1
    assert all(f.left.dtype == np.uint8 and f.right.dtype == np.uint8 for f in frames)
    assert [f.index for f in frames] == list(range(10))
    dt = np.diff([f.host_ns for f in frames])
    assert np.all(dt > 0)


@pytest.mark.skipif(not _HW, reason="실기기 필요: OPTICMIX_HW=1")
def test_requested_pixel_format_is_what_we_get():
    """요청한 fourcc 가 실제로 협상됐는지 확인한다.

    이게 조용히 어긋나면 스트림이 드라이버 기본값(YUY2)으로 떨어지고, 같은 모드가
    90 fps 대신 9 fps 로 돈다. 실측으로 확인된 원인은 **속성 설정 순서**였다 —
    width/height/fps 는 포맷을 재협상하므로 fourcc 를 맨 나중에 설정해야 한다.
    읽기 속도가 아니라 협상된 fourcc 를 본다: 느린 호스트에서도 판정이 흔들리지 않는다.
    """
    from opticmix_vision import Device, DeviceConfig

    cfg = DeviceConfig.weevia_s1()
    with Device(cfg) as cam:
        assert cam.mode.fourcc == "MJPG", (
            f"요청 MJPG, 협상 결과 {cam.mode.fourcc!r} — fourcc 가 무시됐다"
        )
        cam.read()


@pytest.mark.skipif(not _HW, reason="실기기 필요: OPTICMIX_HW=1")
def test_format_props_are_rejected_in_extra_props():
    """포맷을 재협상하는 속성은 extra_props 로 못 넣게 막혀 있어야 한다."""
    from opticmix_vision import Device, DeviceConfig, DeviceError

    cfg = DeviceConfig.weevia_s1(extra_props={"CAP_PROP_FPS": 30})
    with pytest.raises(DeviceError, match="CAP_PROP_FPS"):
        with Device(cfg):
            pass
