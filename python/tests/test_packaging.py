"""휠 배치 — 확장은 언제나 `opticmix_vision/_core.pyd` 자리에(2026-10-29 결함: 크레이트에서 직접 빌드한 휠이 최상위
`_core/_core.pyd` 를 담아 `from opticmix_vision import _core` 가 안 잡혔다). 빌드는 하지 않고 설정만 본다."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SDK = Path(__file__).resolve().parents[2]

tomllib = pytest.importorskip("tomllib") if sys.version_info >= (3, 11) else pytest.importorskip("tomli")


def _maturin(p: Path) -> dict:
    return tomllib.loads(p.read_text(encoding="utf-8"))["tool"]["maturin"]


def test_the_full_wheel_puts_the_extension_inside_the_package():
    m = _maturin(SDK / "python" / "pyproject.toml")
    assert m["module-name"] == "opticmix_vision._core" and m["python-source"] == "."
    assert m["manifest-path"].endswith("/opticmix-vision-py/Cargo.toml"), "배포 휠은 공개 확장으로 빌드한다(내부 확장은 -full)"


def test_the_package_also_looks_for_an_extension_installed_beside_an_editable_tree():
    text = (SDK / "python" / "opticmix_vision" / "__init__.py").read_text(encoding="utf-8")
    assert "extend_path(__path__, __name__)" in text
