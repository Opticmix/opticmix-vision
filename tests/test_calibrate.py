"""calibrate — 유닛별 스테레오 KB4 캘리브레이션 도구 (하드웨어 없이 합성으로 검증).

합성 검증 원리: 알려진 KB4 내부 파라미터·외부 파라미터로 체스보드를 여러 자세로 렌더/투영하고,
도구가 그 값을 되찾는지 본다. 투영·렌더는 cv2.fisheye 를 쓰므로 도구가 같은 모델(KB4)을 쓰는지도 같이 검증된다.

실행:
    cd sdk/python && python -m pytest tests/test_calibrate.py -v
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from opticmix_vision.calibrate import (
    BoardSpec,
    CalibrateError,
    calibrate_from_pairs,
    calibrate_stereo_from_points,
    detect_corners,
    main,
    project_board,
    render_chessboard_view,
    synthetic_board_poses,
)
from opticmix_vision.calibration import CameraIntrinsics, StereoExtrinsics, load_calibration

BOARD = BoardSpec(cols=9, rows=6, square_mm=25.0)
SIZE = (640, 400)
GT_L = CameraIntrinsics(K=[[300.0, 0, 319.5], [0, 300.0, 199.5], [0, 0, 1]],
                        D=[-0.02, 0.005, -0.001, 0.0002], size=SIZE)
GT_R = CameraIntrinsics(K=[[302.0, 0, 321.0], [0, 301.0, 198.0], [0, 0, 1]],
                        D=[-0.018, 0.004, -0.0008, 0.0001], size=SIZE)


def _rot_y(deg: float) -> np.ndarray:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


GT_X = StereoExtrinsics(R=_rot_y(0.5), t=[-64.0, 0.2, -0.1])       # p_right = R·p_left + t, 베이스라인 ≈ 64 mm


def _rot_angle_deg(R: np.ndarray) -> float:
    return math.degrees(math.acos(max(-1.0, min(1.0, (np.trace(R) - 1) / 2))))


def test_synthetic_poses_project_inside_both_images():
    poses = synthetic_board_poses(BOARD, GT_L, GT_R, GT_X, n=12, seed=0)
    assert len(poses) == 12
    for rvec, tvec in poses:
        pl = project_board(BOARD, rvec, tvec, GT_L)
        pr = project_board(BOARD, rvec, tvec, GT_R, extrinsics=GT_X)
        for p in (pl, pr):
            assert p.shape == (BOARD.cols * BOARD.rows, 2)
            assert np.all(p[:, 0] >= 0) and np.all(p[:, 0] < SIZE[0])
            assert np.all(p[:, 1] >= 0) and np.all(p[:, 1] < SIZE[1])


def test_calibrate_from_points_recovers_ground_truth():
    poses = synthetic_board_poses(BOARD, GT_L, GT_R, GT_X, n=20, seed=1)
    obj = [BOARD.object_points() for _ in poses]
    img_l = [project_board(BOARD, r, t, GT_L) for r, t in poses]
    img_r = [project_board(BOARD, r, t, GT_R, extrinsics=GT_X) for r, t in poses]
    res = calibrate_stereo_from_points(obj, img_l, img_r, SIZE)
    L, R, X = res.calibration.left, res.calibration.right, res.calibration.extrinsics
    assert abs(L.fx - 300.0) < 1.0 and abs(L.cx - 319.5) < 1.0
    assert abs(R.fx - 302.0) < 1.0 and abs(R.cx - 321.0) < 1.0
    assert np.allclose(L.D, GT_L.D, atol=5e-3)
    assert _rot_angle_deg(X.R.T @ GT_X.R) < 0.05
    assert np.allclose(X.t, GT_X.t, atol=0.3)
    assert res.rms_left < 0.2 and res.rms_right < 0.2 and res.rms_stereo < 0.5
    assert res.n_views == 20


def test_render_and_detect_corners_match_projection():
    rvec, tvec = synthetic_board_poses(BOARD, GT_L, GT_R, GT_X, n=1, seed=3)[0]
    img = render_chessboard_view(BOARD, rvec, tvec, GT_L)
    assert img.shape == (SIZE[1], SIZE[0]) and img.dtype == np.uint8
    pts = detect_corners(img, BOARD)
    assert pts is not None
    truth = project_board(BOARD, rvec, tvec, GT_L)
    err = np.linalg.norm(pts - truth, axis=1)
    assert err.max() < 0.5, f"corner error max {err.max():.3f} px"


def test_calibrate_from_pairs_end_to_end_and_json_roundtrip(tmp_path: Path):
    # 렌더→검출 경로는 코너 노이즈(~0.3 px)가 있어 fx/k 가 서로 상쇄되며 흔들린다. 24 뷰 기준 fx 오차 < 1 % 를 요구한다.
    poses = synthetic_board_poses(BOARD, GT_L, GT_R, GT_X, n=24, seed=2)
    pairs = [(render_chessboard_view(BOARD, r, t, GT_L),
              render_chessboard_view(BOARD, r, t, GT_R, extrinsics=GT_X)) for r, t in poses]
    res = calibrate_from_pairs(pairs, BOARD, device={"model": "OPX-S1", "serial": "SYNTH"})
    calib = res.calibration
    assert abs(calib.baseline_mm - float(np.linalg.norm(GT_X.t))) < 0.5
    assert abs(calib.left.fx - 300.0) < 3.0
    # 가장자리 자세는 검출기가 놓칠 수 있다(실측: 24장 중 0~4장). 놓친 장은 '검출 실패' 로 **보고돼야** 하고, 오염 뷰 제외는 0 이어야 한다.
    assert res.n_views >= 20
    assert all("검출" in why for _, why in res.rejected_reasons), res.rejected_reasons
    assert res.calibration.meta["rejected_views"] == []
    assert res.rms_left < 0.3 and res.rms_right < 0.3
    assert 0.0 < res.coverage_left <= 1.0
    p = tmp_path / "calib.json"
    res.save(p)
    back = load_calibration(p)
    assert back.device["serial"] == "SYNTH"
    assert "rms_left_px" in back.meta and "n_views" in back.meta


def test_pairs_without_detectable_board_are_rejected_not_silently_dropped():
    poses = synthetic_board_poses(BOARD, GT_L, GT_R, GT_X, n=10, seed=4)
    pairs = [(render_chessboard_view(BOARD, r, t, GT_L),
              render_chessboard_view(BOARD, r, t, GT_R, extrinsics=GT_X)) for r, t in poses]
    blank = np.full((SIZE[1], SIZE[0]), 128, dtype=np.uint8)
    pairs.append((blank, blank))
    res = calibrate_from_pairs(pairs, BOARD)
    assert res.n_views == 10 and res.n_rejected == 1
    assert res.rejected_reasons[0][0] == 10                    # (index, reason)


def test_too_few_views_is_an_error():
    poses = synthetic_board_poses(BOARD, GT_L, GT_R, GT_X, n=3, seed=5)
    pairs = [(render_chessboard_view(BOARD, r, t, GT_L),
              render_chessboard_view(BOARD, r, t, GT_R, extrinsics=GT_X)) for r, t in poses]
    with pytest.raises(CalibrateError, match="최소"):
        calibrate_from_pairs(pairs, BOARD, min_views=8)


# ---------------------------------------------------------------- 감사 R3 (2026-09-07) ----

def _pairs(n: int, seed: int):
    poses = synthetic_board_poses(BOARD, GT_L, GT_R, GT_X, n=n, seed=seed)
    return [(render_chessboard_view(BOARD, r, t, GT_L),
             render_chessboard_view(BOARD, r, t, GT_R, extrinsics=GT_X)) for r, t in poses]


def test_swapped_left_right_input_is_rejected_with_lr_order_hint():
    """R3 치명 1: 좌/우가 바뀐 쌍은 rms 0 으로 '성공' 했었다 (t.x 부호 검사 없음). 깊이 +130 mm 오차의 원인."""
    swapped = [(R, L) for L, R in _pairs(16, seed=21)]
    with pytest.raises(CalibrateError, match="lr_order|좌/우"):
        calibrate_from_pairs(swapped, BOARD)


def test_expected_baseline_mismatch_is_rejected():
    """R3 중: --square-mm 오타는 rms 가 그대로라 못 잡는다. 공칭 베이스라인과 비교해야 한다."""
    pairs = _pairs(16, seed=22)
    wrong_board = BoardSpec(cols=9, rows=6, square_mm=10.0)          # 실제 25 mm 보드를 10 mm 로 선언
    with pytest.raises(CalibrateError, match="베이스라인"):
        calibrate_from_pairs(pairs, wrong_board, expected_baseline_mm=64.0)
    res = calibrate_from_pairs(pairs, BOARD, expected_baseline_mm=64.0)   # 맞으면 통과
    assert abs(res.calibration.baseline_mm - 64.0) < 0.5


def test_contaminated_view_is_rejected_by_per_view_rms():
    """R3 치명 2: 한 뷰의 코너가 수 px 흐트러져도(블러·오검출) 전체 rms 는 작아 보여 통과했었다.
    (전 코너가 똑같이 밀리는 '강체 이동' 은 그 뷰의 외부 파라미터가 흡수해 버려 per-view rms 로도 안 잡힌다 — 실측 0.22 px.
    현실의 오염은 코너별로 제각각 튀는 쪽이므로 그걸 흉내낸다.)"""
    poses = synthetic_board_poses(BOARD, GT_L, GT_R, GT_X, n=20, seed=23)
    obj = [BOARD.object_points() for _ in poses]
    img_l = [project_board(BOARD, r, t, GT_L) for r, t in poses]
    img_r = [project_board(BOARD, r, t, GT_R, extrinsics=GT_X) for r, t in poses]
    rng = np.random.default_rng(5)
    img_l[5] = img_l[5] + rng.normal(0.0, 5.0, size=img_l[5].shape)       # 뷰 5 왼쪽 코너들이 σ=5 px 로 흐트러짐
    res = calibrate_stereo_from_points(obj, img_l, img_r, SIZE)
    assert res.n_rejected == 1 and res.rejected_reasons[0][0] == 5
    assert res.n_views == 19
    assert res.rms_left < 0.3 and abs(res.calibration.left.fx - 300.0) < 1.0


def test_observed_theta_range_is_recorded_and_low_coverage_is_noted():
    """R3 치명 3: 관측 θ 가 43° 뿐인데 92.5° 까지 단조성만 검사 — 그 밖은 외삽이라는 사실이 리포트에 없었다."""
    res = calibrate_from_pairs(_pairs(16, seed=24), BOARD)
    m = res.calibration.meta
    assert "theta_max_deg_left" in m and "theta_max_deg_right" in m
    assert 20.0 < m["theta_max_deg_left"] < 90.0
    assert res.theta_max_deg_left == m["theta_max_deg_left"]


def test_synthetic_poses_reach_the_image_edge():
    """합성 자세가 중앙에만 몰리면 k3/k4 가 폭주해 폴백에 의존한다. 가장자리(θ ≥ 55°)까지 닿아야 한다."""
    poses = synthetic_board_poses(BOARD, GT_L, GT_R, GT_X, n=30, seed=25)
    import cv2
    th = []
    for r, t in poses:
        p = project_board(BOARD, r, t, GT_L)
        n = cv2.fisheye.undistortPoints(p.reshape(-1, 1, 2), GT_L.K, GT_L.D.reshape(4, 1)).reshape(-1, 2)
        th.append(np.degrees(np.arctan(np.linalg.norm(n, axis=1))).max())
    # 더 바깥(|nx|>0.8)은 체스보드 검출기가 실패한다(실측) — 검출 가능한 한계에서 50° 이상이면 충분
    assert max(th) >= 50.0, f"max theta {max(th):.1f}"


def test_majority_frame_size_is_the_reference_not_the_first_pair():
    """R3 중: 첫 쌍이 이상 크기면 정상 쌍이 전부 '크기 불일치' 로 거부됐다."""
    pairs = _pairs(12, seed=26)
    odd = (np.zeros((200, 320), np.uint8), np.zeros((200, 320), np.uint8))
    res = calibrate_from_pairs([odd] + pairs, BOARD)
    assert res.n_views == 12 and res.n_rejected == 1 and res.rejected_reasons[0][0] == 0


def test_16bit_frames_are_detected():
    """R3 중: 16-bit 입력은 전부 '검출 실패' 였다."""
    rvec, tvec = synthetic_board_poses(BOARD, GT_L, GT_R, GT_X, n=1, seed=27)[0]
    img8 = render_chessboard_view(BOARD, rvec, tvec, GT_L)
    img16 = (img8.astype(np.uint16) * 257)
    pts16 = detect_corners(img16, BOARD)
    pts8 = detect_corners(img8, BOARD)
    assert pts16 is not None and pts8 is not None
    assert np.allclose(pts16, pts8, atol=1e-3)                     # 16-bit 는 8-bit 와 같은 코너를 내야 한다


def test_cli_synthetic_dry_run_writes_calibration(tmp_path: Path):
    out = tmp_path / "unit.json"
    rc = main(["--synthetic", "--views", "12", "--out", str(out)])
    assert rc == 0
    calib = load_calibration(out)
    assert 60.0 < calib.baseline_mm < 68.0


# ---------------------------------------------------------------------------
# 인쇄용 체스보드 PDF (2026-09-07 — 도착 전날, 캘리브 타깃이 repo 에 하나도 없었다)
# ---------------------------------------------------------------------------

def test_write_chessboard_pdf_a4_landscape_exact_mm(tmp_path):
    from opticmix_vision.calibrate import write_chessboard_pdf

    out = tmp_path / "board.pdf"
    write_chessboard_pdf(out, board=BOARD)            # 9x6 안쪽 코너 = 사각 10x7, 25 mm
    data = out.read_bytes()
    assert data.startswith(b"%PDF-1.")
    text = data.decode("latin-1")
    assert "/MediaBox [0 0 841.89 595.28]" in text    # A4 가로, pt = mm * 72 / 25.4
    # 검정 사각 35개(10x7 의 절반) + 100 mm 눈금자 1개 = 채우기 사각 36개
    assert text.count(" re\n") == 36
    # 첫 검정 사각의 크기가 정확히 25 mm (70.866 pt)
    assert "70.866 70.866 re" in text


def test_cli_board_pdf_writes_without_pairs(tmp_path):
    out = tmp_path / "b.pdf"
    assert main(["--board-pdf", str(out), "--board", "9x6", "--square-mm", "25"]) == 0
    assert out.stat().st_size > 500
