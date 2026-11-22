import sys
import types

import numpy as np
import pytest

from weevia_ros.driver_core import capture


class FakeFrame:
    def __init__(self, i):
        self.left = np.full((800, 800), 2, np.uint8)
        self.right = np.full((800, 800), 1, np.uint8)
        self.host_ns, self.index = 1000 + i, i


class FakeDevice:
    opened_with = None

    def __init__(self, cfg):
        FakeDevice.opened_with = cfg
        self.n = 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @property
    def controls(self):
        raise AssertionError("driver must not touch controls")

    def read(self):
        self.n += 1
        return FakeFrame(self.n)


class FakeConfig:
    def __init__(self, **kw):
        self.kw = kw


class FakeSdk:
    Device = FakeDevice
    DeviceConfig = FakeConfig


def test_capture_rebuilds_sbs_and_no_controls():
    got = []
    capture(FakeSdk, device=2, backend="v4l2", publish=lambda sbs, ns: got.append((sbs, ns)), should_stop=lambda: len(got) >= 3)
    assert len(got) == 3
    sbs, ns = got[0]
    assert sbs.shape == (800, 1600)
    assert int(sbs[0, 0]) == 1 and int(sbs[0, 1599]) == 2        # 앞 절반 = 오른쪽 눈
    kw = FakeDevice.opened_with.kw
    assert kw["device"] == 2 and kw["backend"] == "v4l2" and kw["stall_frames"] == 0 and kw["to_gray"] is True
    assert "extra_props" not in kw


def test_capture_open_failure_raises():
    class Broken(FakeSdk):
        class Device(FakeDevice):
            def __enter__(self):
                raise RuntimeError("카메라 열기 실패")
    with pytest.raises(RuntimeError):
        capture(Broken, device=0, backend="v4l2", publish=lambda *a: None, should_stop=lambda: True)


def test_driver_ends_quietly_after_signal_closes_context(monkeypatch):
    # Ctrl-C·SIGTERM: rclpy 신호 처리기가 문맥을 닫은 뒤 읽기·publish 가 예외 → 예전엔 끝 코드 2
    import rclpy
    from weevia_ros import weevia_driver

    class Interrupted(FakeDevice):
        def read(self):
            if self.n == 2:
                rclpy.shutdown()                           # 신호 처리기가 하는 일
                raise RuntimeError("read interrupted")
            return super().read()

    monkeypatch.setitem(sys.modules, "opticmix_vision", types.SimpleNamespace(Device=Interrupted, DeviceConfig=FakeConfig))
    try:
        weevia_driver.main()                               # SystemExit(2) 면 실패
    finally:
        rclpy.try_shutdown()
