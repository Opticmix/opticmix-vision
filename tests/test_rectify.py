"""rectify — 맵 모양/유한성, 명시 hfov 로 만든 P 가 퇴화하지 않음, P2/Q 와 베이스라인, 맵이 KB4 투영과 일치(역검증), remap."""

from __future__ import annotations

import math

import numpy as np
import pytest

from opticmix_vision.calibration import CameraIntrinsics, StereoCalibration, StereoExtrinsics, synthetic_equidistant
from opticmix_vision.rectify import pinhole_matrix, remap_pair, stereo_rectify_maps, undistort_maps

SIZE = (640, 400)
FOV_SYNTH = 150.0       # 합성 값 (❓ 실측 아님). 가로 가장자리 입사각이 90° 를 넘도록 일부러 넓게 둔다
BASELINE = 64.0         # 합성 상수
HFOV_OUT = 90.0


def _calib(R: np.ndarray | None = None) -> StereoCalibration:
    left = synthetic_equidistant(SIZE, FOV_SYNTH)
    right = CameraIntrinsics(K=left.K, D=np.array([0.02, -0.004, 0.0, 0.0]), size=SIZE)   # 좌우 D 를 다르게
    return StereoCalibration(left=left, right=right,
                             extrinsics=StereoExtrinsics(R=np.eye(3) if R is None else R, t=[-BASELINE, 0.0, 0.0]))


def test_pinhole_matrix_focal_and_limits():
    K = pinhole_matrix((640, 400), 90.0)
    assert K[0, 0] == pytest.approx(320.0) and K[1, 1] == pytest.approx(320.0)
    assert (K[0, 2], K[1, 2]) == (320.0, 200.0)
    for bad in (0.0, 180.0, 200.0):
        with pytest.raises(ValueError, match="hfov"):
            pinhole_matrix((640, 400), bad)


def test_undistort_maps_shape_finite_and_P():
    intr = synthetic_equidistant(SIZE, FOV_SYNTH)
    m1, m2, P = undistort_maps(intr, rectified_hfov_deg=HFOV_OUT)
    assert m1.shape == (SIZE[1], SIZE[0]) and m2.shape == (SIZE[1], SIZE[0])
    assert np.all(np.isfinite(m1)) and np.all(np.isfinite(m2))
    assert P[0, 0] == pytest.approx((SIZE[0] / 2) / math.tan(math.radians(HFOV_OUT / 2)))
    m1s, _, Ps = undistort_maps(intr, new_size=(320, 200), rectified_hfov_deg=HFOV_OUT)
    assert m1s.shape == (200, 320) and Ps[0, 2] == 160.0


def test_stereo_rectify_not_degenerate_even_when_frame_edges_exceed_90deg():
    """가로 가장자리 입사각: (320 px / f) → 150° 합성 렌즈에서 약 120° > 90°. OpenCV 자동 P 는 여기서 f≈0 으로 무너졌다."""
    c = _calib()
    edge_theta = math.degrees((SIZE[0] / 2) / c.left.fx)
    assert edge_theta > 90.0
    maps = stereo_rectify_maps(c, rectified_hfov_deg=HFOV_OUT)
    assert maps.rectified_focal_px == pytest.approx(320.0)
    for m in (*maps.left, *maps.right):
        assert m.shape == (SIZE[1], SIZE[0]) and np.all(np.isfinite(m))
    assert maps.P1.shape == (3, 4) and maps.P2.shape == (3, 4) and maps.Q.shape == (4, 4)
    assert maps.baseline_from_P2_mm == pytest.approx(BASELINE, rel=1e-9)
    assert maps.P1[0, 0] == maps.P2[0, 0] and maps.P1[0, 2] == maps.P2[0, 2]   # zero-disparity


def test_R1_R2_match_opencv_stereo_rectify():
    import cv2
    from scipy.spatial.transform import Rotation  # gt_capture venv 에 있음; 없으면 skip
    R = Rotation.from_euler("xyz", [1.5, -2.0, 0.7], degrees=True).as_matrix()
    c = _calib(R)
    maps = stereo_rectify_maps(c, rectified_hfov_deg=HFOV_OUT)
    K = np.array([[500.0, 0, 320], [0, 500.0, 200], [0, 0, 1]])
    R1, R2, *_ = cv2.stereoRectify(K, np.zeros(5), K, np.zeros(5), SIZE, R, c.extrinsics.t.reshape(3, 1),
                                   flags=cv2.CALIB_ZERO_DISPARITY, alpha=0.0)
    assert np.allclose(maps.R1, R1) and np.allclose(maps.R2, R2)
    # rectify 후 베이스라인은 순수 수평이어야 한다
    t2 = maps.R2 @ c.extrinsics.t
    assert abs(t2[1]) < 1e-9 and abs(t2[2]) < 1e-9 and t2[0] == pytest.approx(-BASELINE)


def _kb4(intr: CameraIntrinsics, pts_cam: np.ndarray) -> np.ndarray:
    import cv2
    p, _ = cv2.fisheye.projectPoints(pts_cam.reshape(-1, 1, 3), np.zeros(3), np.zeros(3), intr.K, intr.D)
    return p.reshape(-1, 2)


def _project(P: np.ndarray, X_rect: np.ndarray) -> np.ndarray:
    hom = (P @ np.hstack([X_rect, np.ones((len(X_rect), 1))]).T).T
    return hom[:, :2] / hom[:, 2:3]


def test_rectify_maps_agree_with_kb4_projection_both_eyes():
    """OpenCV 규약: P1, P2 는 둘 다 **rectified-left 좌표계**의 점을 받는다 (P2 의 4열 = Tx·f 가 오른쪽 카메라
    오프셋). 그러므로 X_left → X' = R1·X_left → u_l = P1[X',1], u_r = P2[X',1] 이고,
    왼쪽 맵(u_l) 은 X_left 의 KB4 투영을, 오른쪽 맵(u_r) 은 X_right = R·X_left + t 의 KB4 투영을 가리켜야 한다.
    이 검사가 통과하면 R1/R2/P1/P2 의 방향·부호가 initUndistortRectifyMap 과 모순 없이 맞물린 것이다."""
    from scipy.spatial.transform import Rotation
    R = Rotation.from_euler("xyz", [1.0, -1.5, 0.5], degrees=True).as_matrix()
    c = _calib(R)
    maps = stereo_rectify_maps(c, rectified_hfov_deg=HFOV_OUT)
    rng = np.random.default_rng(0)
    X_left = rng.uniform([-150, -100, 300], [150, 100, 700], size=(60, 3))          # 왼쪽 카메라 좌표, mm
    X_right = (c.extrinsics.R @ X_left.T).T + c.extrinsics.t                          # 같은 점, 오른쪽 좌표
    X_rect = (maps.R1 @ X_left.T).T
    u_l, u_r = _project(maps.P1, X_rect), _project(maps.P2, X_rect)
    f_l, f_r = _kb4(c.left, X_left), _kb4(c.right, X_right)
    checked = 0
    for (ul, vl), (ur, vr), (fl_x, fl_y), (fr_x, fr_y) in zip(u_l, u_r, f_l, f_r):
        xl, yl, xr, yr = int(round(ul)), int(round(vl)), int(round(ur)), int(round(vr))
        if not (0 <= xl < SIZE[0] and 0 <= yl < SIZE[1] and 0 <= xr < SIZE[0] and 0 <= yr < SIZE[1]):
            continue
        assert abs(maps.left[0][yl, xl] - fl_x) < 1.5 and abs(maps.left[1][yl, xl] - fl_y) < 1.5
        assert abs(maps.right[0][yr, xr] - fr_x) < 1.5 and abs(maps.right[1][yr, xr] - fr_y) < 1.5
        assert abs(vl - vr) < 1e-6                              # rectified: 같은 행(epipolar line 수평)
        assert ul - ur > 0                                      # 양의 시차 (x_l − x_r > 0)
        checked += 1
    assert checked >= 30


def test_Q_reprojects_disparity_to_depth():
    """rectified 좌표에서 같은 3D 점의 좌우 픽셀 시차 d 에 대해 Q 가 Z = f·B/d 를 돌려줘야 한다."""
    c = _calib()
    maps = stereo_rectify_maps(c, rectified_hfov_deg=HFOV_OUT)
    X = np.array([30.0, -20.0, 500.0])                       # 왼쪽 (=rectified, R=I) 좌표
    f, cx, cy = maps.P1[0, 0], maps.P1[0, 2], maps.P1[1, 2]
    u_l, v_l = f * X[0] / X[2] + cx, f * X[1] / X[2] + cy
    d = f * BASELINE / X[2]                                   # 양의 시차 (x_l − x_r)
    hom = maps.Q @ np.array([u_l, v_l, d, 1.0])
    XYZ = hom[:3] / hom[3]
    assert np.allclose(XYZ, X, atol=1e-6)


def test_remap_pair_outputs_new_size():
    c = _calib()
    maps = stereo_rectify_maps(c, new_size=(320, 200), rectified_hfov_deg=HFOV_OUT)
    L = np.random.default_rng(1).integers(0, 255, (SIZE[1], SIZE[0]), dtype=np.uint8)
    l2, r2 = remap_pair(L, L.copy(), maps)
    assert l2.shape == (200, 320) and r2.shape == (200, 320) and l2.dtype == np.uint8


def test_stereo_rectify_rejects_size_mismatch_and_vertical_baseline():
    a = synthetic_equidistant(SIZE, FOV_SYNTH)
    b = synthetic_equidistant((320, 200), FOV_SYNTH)
    with pytest.raises(ValueError, match="크기"):
        stereo_rectify_maps(StereoCalibration(left=a, right=b, extrinsics=StereoExtrinsics(R=np.eye(3), t=[-1, 0, 0])))
    with pytest.raises(ValueError, match="수평"):
        stereo_rectify_maps(StereoCalibration(left=a, right=a, extrinsics=StereoExtrinsics(R=np.eye(3), t=[0, -64, 0])))
