"""테스트가 설치 없이 패키지를 import 하도록 이 폴더의 위(패키지 폴더)를 sys.path 앞에 넣는다."""

from __future__ import annotations

import sys
from pathlib import Path

_SDK_ROOT = Path(__file__).resolve().parents[1]
if str(_SDK_ROOT) not in sys.path:
    sys.path.insert(0, str(_SDK_ROOT))

import pytest


@pytest.fixture(autouse=True)
def _fast_control_guard(monkeypatch):
    """제품 기본 간격(12 ms)도 테스트마다 쌓이면 느려진다. 간격을 재는 테스트는 자기 값을 다시 넣는다."""
    try:
        from opticmix_vision import controls
    except ImportError:          # controls.py 가 생기기 전
        yield
        return
    monkeypatch.setattr(controls, "GROUP_MIN_INTERVAL_S", {})
    monkeypatch.setattr(controls, "GLOBAL_MIN_GAP_S", 0.0)
    monkeypatch.setattr(controls, "WARN_AFTER_TOTAL", 10**9)
    controls.reset_write_counts()
    yield
    controls.reset_write_counts()
