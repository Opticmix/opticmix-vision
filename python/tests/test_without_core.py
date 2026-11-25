"""Rust 확장(_core)이 없어도 패키지 import 는 된다 — 제어·장치 목록을 실제로 쓸 때만 이유를 말하고 실패한다.

소스 트리만 경로에 둔 환경에는 확장이 없다. 프레임만 쓰는 사용자는 _core 없이도 돌아야 한다. 자식 프로세스에서 _core import 를 막고 확인한다.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

SDK = Path(__file__).resolve().parents[1]

_BLOCK = textwrap.dedent("""
    import sys
    sys.path.insert(0, {sdk!r})
    class _NoCore:
        def find_spec(self, name, path=None, target=None):
            if name == "opticmix_vision._core":
                raise ImportError("blocked for test: no _core")
            return None
    sys.meta_path.insert(0, _NoCore())
""")


def _run(body: str) -> subprocess.CompletedProcess:
    code = _BLOCK.format(sdk=str(SDK)) + textwrap.dedent(body)
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8",
                          env={"PYTHONIOENCODING": "utf-8", **__import__("os").environ})


def test_the_package_imports_without_the_rust_extension():
    r = _run("""
        import opticmix_vision as ov
        from opticmix_vision import controls, devices, device, _ctrl_dshow
        assert ov.Device and ov.DeviceConfig and ov.split_stereo
        assert device.WEEVIA_S1_NAME == "WN Dual Camera"
        print("IMPORT_OK")
    """)
    assert r.returncode == 0 and "IMPORT_OK" in r.stdout, r.stderr


def test_using_controls_or_devices_without_the_extension_says_what_to_install():
    r = _run("""
        from opticmix_vision import controls as C, devices
        from opticmix_vision._ctrl_sim import SimBackend
        for call in (lambda: C.Controls(SimBackend()), lambda: devices.list_devices(backend="dshow", _raw=""), C.write_counts):
            try:
                call()
            except C.ControlsUnavailable as exc:
                assert "opticmix_vision._core" in str(exc) and "pip install" in str(exc), exc
            else:
                raise SystemExit("no error")
        print("RAISE_OK")
    """)
    assert r.returncode == 0 and "RAISE_OK" in r.stdout, r.stderr


def test_the_doctor_reports_a_missing_extension_instead_of_failing():
    r = _run("""
        import tempfile
        from pathlib import Path
        from opticmix_vision import calib, doctor
        calib.store_dir = lambda: Path(tempfile.mkdtemp()) / "calib"     # not the user's folder
        assert doctor.main(["--synthetic", "--open", "--seconds", "0.1"]) == 0
    """)
    assert r.returncode == 0, r.stderr
    assert "_core:           MISSING" in r.stdout and "The compiled extension (opticmix_vision._core) is missing" in r.stdout
    assert "controls: ControlsUnavailable" in r.stdout and "control writes by this run: -" in r.stdout
