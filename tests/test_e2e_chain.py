"""도구 간 파일 인계 e2e — 도착 당일 실제로 밟을 순서를 합성으로 끝까지 잇는다.

  bringup(내부 도구) ──oem_uvc_constants.py──▶ SDK DeviceConfig
  viewer 's' 저장 ──*_left.png/*_right.png──▶ calibrate --pairs-dir ──▶ calib.json ──▶ load_calibration/rectify

각 도구는 단위 테스트가 있지만 **경계(파일 형식·파일명 규칙)** 는 어느 쪽 테스트도 보지 않았다:
- SDK 의 from_bringup_constants 테스트는 손으로 쓴 상수 문자열을 썼다 → bringup.report.render_constants 의 실제 출력과
  어긋나도 초록불.
- calibrate.load_pairs_dir 는 테스트 0개였다.

실행:
    cd sdk/python && python -m pytest tests/test_e2e_chain.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_SDK_ROOT = Path(__file__).resolve().parents[1]
_GT_ROOT = _SDK_ROOT.parents[1] / "internal" / "gt_capture"
if str(_SDK_ROOT) not in sys.path:
    sys.path.insert(0, str(_SDK_ROOT))

from opticmix_vision.calibrate import (  # noqa: E402
    BoardSpec, calibrate_from_pairs, load_pairs_dir, render_chessboard_view, synthetic_board_poses,
)
from opticmix_vision.calibrate import main as calibrate_main  # noqa: E402
from opticmix_vision.calibration import CameraIntrinsics, StereoExtrinsics, load_calibration  # noqa: E402
from opticmix_vision.device import DeviceConfig  # noqa: E402
from opticmix_vision.rectify import stereo_rectify_maps  # noqa: E402
from opticmix_vision.synthetic import SyntheticStereoSource  # noqa: E402
from opticmix_vision.viewer import run_viewer  # noqa: E402


def _load_gt_capture():
    """internal/gt_capture 를 sys.path 에 넣지 **않고** `gt_capture` 패키지만 등록한다.

    그 디렉토리를 sys.path 에 넣으면 그 안의 정규 패키지 `tests`(__init__.py 있음)가 sdk/python/tests 의
    네임스페이스 `tests` 를 경로 순서와 무관하게 가로채서 `from tests.test_device import …` 가 깨진다(실측).
    """
    import importlib
    import importlib.util
    if "gt_capture" not in sys.modules:
        pkg = _GT_ROOT / "gt_capture"
        # Outside the monorepo this directory does not exist at all, and
        # spec_from_file_location raises rather than returning None there.
        if not (pkg / "__init__.py").is_file():
            pytest.skip("내부 gt_capture 패키지 없음")
        spec = importlib.util.spec_from_file_location("gt_capture", pkg / "__init__.py",
                                                      submodule_search_locations=[str(pkg)])
        if spec is None or spec.loader is None:
            pytest.skip("내부 gt_capture 패키지 없음")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["gt_capture"] = mod
        spec.loader.exec_module(mod)
    return (importlib.import_module("gt_capture.bringup.adapters"),
            importlib.import_module("gt_capture.bringup.report"),
            importlib.import_module("gt_capture.bringup.stereo"),
            importlib.import_module("gt_capture.bringup.timing"))


_adapters, _report, _stereo, _timing = _load_gt_capture()
SyntheticGrabber, collect = _adapters.SyntheticGrabber, _adapters.collect
build_report, render_constants = _report.build_report, _report.render_constants
detect_layout = _stereo.detect_layout
characterize = _timing.characterize


# ---------------------------------------------------------------------------
# 경계 1: bringup 이 실제로 쓰는 상수 파일 → SDK 가 그대로 읽는가
# ---------------------------------------------------------------------------

def _real_constants_file(tmp_path: Path, *, backend_name: str = "opencv") -> Path:
    """bringup 파이프라인을 진짜로 돌려 render_constants 출력을 파일로 만든다 (손으로 쓰지 않는다)."""
    g = SyntheticGrabber(width=640, height=200, fps=90.0, layout="sbs", disparity=8, pts_mode="us", seed=7)
    res = collect(g, n_frames=200)
    timing = characterize(qpc_ns=res.qpc_ns, pts_us=res.pts_us, frame_id=res.frame_id, nominal_fps=90.0,
                          frame_id_source=res.mode.frame_id_source)
    stereo = detect_layout(res.first_image)
    rep = build_report(mode=res.mode, timing=timing, stereo=stereo, controls=None)
    src = render_constants(rep)
    # SDK 는 opencv 백엔드만 열 수 있다. 합성 리포트의 BACKEND/DEVICE 만 실장비 형태로 바꿔 넣는다 — 나머지 줄은 원문 그대로.
    src = src.replace("BACKEND = 'synthetic'", f"BACKEND = '{backend_name}'").replace(
        "DEVICE = 'synthetic'", "DEVICE = 'index 0 (dshow)'")
    p = tmp_path / "oem_uvc_constants.py"
    p.write_text(src, encoding="utf-8")
    return p


def test_sdk_reads_the_constants_file_bringup_actually_writes(tmp_path: Path):
    p = _real_constants_file(tmp_path)
    cfg = DeviceConfig.from_bringup_constants(p)
    assert cfg.layout == "sbs" and cfg.lr_order == "first_is_left"
    assert (cfg.width, cfg.height) == (640, 200)
    assert cfg.fps == 90.0
    assert cfg.device == 0 and cfg.backend == "dshow"


def test_sdk_rejects_bringup_output_when_layout_undetermined(tmp_path: Path):
    """브링업이 좌우 순서를 못 정했으면 SDK 도 추측하지 않고 거부해야 한다 (같은 파일을 두 쪽이 같은 뜻으로 읽는지)."""
    p = _real_constants_file(tmp_path)
    text = p.read_text(encoding="utf-8").replace("LR_ORDER = 'first_is_left'", "LR_ORDER = 'undetermined'")
    p.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="LR_ORDER"):
        DeviceConfig.from_bringup_constants(p)


# ---------------------------------------------------------------------------
# 경계 2: viewer 가 저장한 파일 → calibrate 가 읽는가
# ---------------------------------------------------------------------------

def test_calibrate_loads_exactly_the_pairs_the_viewer_saved(tmp_path: Path):
    src = SyntheticStereoSource(eye_size=(96, 48), fps=1000.0)
    stats = run_viewer(src, headless=True, out_dir=tmp_path, key_feed=[ord("s"), -1, ord("s"), ord("s"), ord("q")])
    assert stats["saved"] == 3
    pairs = load_pairs_dir(tmp_path)
    assert len(pairs) == 3
    for L, R in pairs:
        assert L.shape == (48, 96) and R.shape == (48, 96)
        assert L.dtype == np.uint8 and R.dtype == np.uint8
        assert not np.array_equal(L, R)                   # 좌우가 섞이지 않았다


def test_calibrate_cli_consumes_viewer_named_pairs_end_to_end(tmp_path: Path):
    """뷰어 파일명 규칙(<stem>_left.png/_right.png)으로 저장된 체스보드 쌍을 --pairs-dir 로 풀어 JSON 까지."""
    import cv2
    board = BoardSpec(cols=9, rows=6, square_mm=25.0)
    size = (640, 400)
    gl = CameraIntrinsics(K=[[300.0, 0, 319.5], [0, 300.0, 199.5], [0, 0, 1]], D=[-0.02, 0.005, -0.001, 0.0002], size=size)
    gr = CameraIntrinsics(K=[[302.0, 0, 321.0], [0, 301.0, 198.0], [0, 0, 1]], D=[-0.018, 0.004, -0.0008, 0.0001], size=size)
    Ry, _ = cv2.Rodrigues(np.array([0.0, np.radians(0.5), 0.0]))
    gx = StereoExtrinsics(R=Ry, t=[-64.0, 0.2, -0.1])
    pairs_dir = tmp_path / "captures"; pairs_dir.mkdir()
    for i, (r, t) in enumerate(synthetic_board_poses(board, gl, gr, gx, n=24, seed=11)):
        stem = f"20260908_120000_{i:06d}"                     # 뷰어 _save_pair 와 같은 규칙
        cv2.imwrite(str(pairs_dir / f"{stem}_left.png"), render_chessboard_view(board, r, t, gl))
        cv2.imwrite(str(pairs_dir / f"{stem}_right.png"), render_chessboard_view(board, r, t, gr, extrinsics=gx))
    out = tmp_path / "unit.json"
    rc = calibrate_main(["--pairs-dir", str(pairs_dir), "--board", "9x6", "--square-mm", "25",
                         "--serial", "OPX-S1-TEST", "--out", str(out)])
    assert rc == 0
    calib = load_calibration(out)
    assert calib.device["serial"] == "OPX-S1-TEST"
    assert abs(calib.baseline_mm - float(np.linalg.norm(gx.t))) < 0.5
    maps = stereo_rectify_maps(calib)                         # 결과 JSON 이 rectify 까지 통과
    assert maps.left[0].shape == (400, 640)


def test_pairs_dir_with_orphan_left_is_reported_not_silently_skipped(tmp_path: Path):
    """짝이 없는 파일이 조용히 사라지면 '뷰어에서 10장 찍었는데 8장만 풀렸다' 를 아무도 모른다."""
    import cv2
    img = np.full((48, 96), 128, np.uint8)
    for stem in ("a", "b", "c"):
        cv2.imwrite(str(tmp_path / f"{stem}_left.png"), img)
        cv2.imwrite(str(tmp_path / f"{stem}_right.png"), img)
    cv2.imwrite(str(tmp_path / "d_left.png"), img)             # right 없음
    pairs, orphans = load_pairs_dir(tmp_path, return_orphans=True)
    assert len(pairs) == 3
    assert orphans == ["d_left.png"]
