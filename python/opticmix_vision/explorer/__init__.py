"""Weevia S1 Explorer — 카메라가 되는지 보는 창.

창 자체(`app`)는 Tk 를 import 하므로 여기서 미리 끌어오지 않는다. 상태 객체는
화면 없이 쓸 수 있어야 하고, 테스트도 그 경로로만 돈다.
"""
from __future__ import annotations

__all__ = ["main"]


def main(argv=None) -> int:
    from .app import main as _main
    return _main(argv)
