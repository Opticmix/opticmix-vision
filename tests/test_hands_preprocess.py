import numpy as np
import pytest
from opticmix_vision.hands_preprocess import (
    EyeGeometry, _crop_box, eyes_to_model_input, preprocess_eye, split_sbs, sbs_frame_to_model_input,
)


def _raw_sbs(eye=800):
    f = np.zeros((eye, 2 * eye), np.uint8)
    f[:, :eye] = 40
    f[:, eye:] = 200
    return f


def test_split_sbs_halves():
    left, right = split_sbs(_raw_sbs(800))
    assert left.shape == (800, 800) and right.shape == (800, 800)
    assert int(left.mean()) == 40 and int(right.mean()) == 200


def test_split_sbs_rejects_non_2to1():
    with pytest.raises(ValueError):
        split_sbs(np.zeros((800, 900), np.uint8))


def test_preprocess_eye_shape_and_range():
    out = preprocess_eye(np.full((800, 800), 255, np.uint8), EyeGeometry(), size=256)
    assert out.shape == (1, 256, 256) and out.dtype == np.float32
    assert out.max() <= 1.0 + 1e-6 and out.min() >= -1.0 - 1e-6
    assert out[0, 128, 128] > 0.9
    assert out[0, 0, 0] == -1.0


def test_preprocess_eye_black_is_minus_one():
    out = preprocess_eye(np.zeros((800, 800), np.uint8), EyeGeometry(), size=256)
    assert np.allclose(out, -1.0)


def test_sbs_frame_to_model_input_pair():
    raw = np.zeros((800, 1600), np.uint8)
    raw[:, :800] = 255
    left, right = sbs_frame_to_model_input(raw, size=256)
    assert left.shape == right.shape == (1, 256, 256)
    assert left[0, 128, 128] > 0.9
    assert np.allclose(right, -1.0)


@pytest.mark.parametrize("cx,cy,diam", [
    (400.0, 400.0, 614.0), (400.1, 399.6, 613.0), (400.0, 400.0, 615.0), (383.0, 369.0, 614.0),
])
def test_crop_box_is_square(cx, cy, diam):
    x0, y0, x1, y1 = _crop_box(EyeGeometry(cx=cx, cy=cy, diam=diam))
    assert (x1 - x0) == (y1 - y0)


def test_preprocess_eye_edge_crop_padded():
    out = preprocess_eye(np.full((800, 800), 255, np.uint8), EyeGeometry(cx=50.0, cy=50.0, diam=614.0), size=256)
    assert out.shape == (1, 256, 256)
    assert (out == -1.0).any()


def test_eyes_to_model_input_takes_split_eyes():
    """StereoFrame 이 이미 준 (left, right) 눈을 그대로 받는다 — 주 API."""
    left_eye = np.full((800, 800), 255, np.uint8)
    right_eye = np.zeros((800, 800), np.uint8)
    L, R = eyes_to_model_input(left_eye, right_eye, size=256)
    assert L.shape == R.shape == (1, 256, 256)
    assert L[0, 128, 128] > 0.9
    assert np.allclose(R, -1.0)


def test_eye_geometry_from_intrinsics_uses_principal_point_and_measured_diam():
    K = np.array([[300.0, 0, 395.0], [0, 300.0, 372.0], [0, 0, 1]])
    g = EyeGeometry.from_intrinsics(K, D=np.zeros(4), size=(800, 800), diam_px=614.0)
    assert (g.cx, g.cy, g.eye, g.diam) == (395.0, 372.0, 800, 614.0)


def test_eye_geometry_diam_from_kb4_when_not_measured():
    # 등거리(D=0): r = f·θ. f=300, θ=92.5° → diam 968 > 눈 800 → 캡 800. f=150 → 2·150·1.6144 = 484.3
    K = np.array([[300.0, 0, 400.0], [0, 300.0, 400.0], [0, 0, 1]])
    g = EyeGeometry.from_intrinsics(K, D=np.zeros(4), size=(800, 800))
    assert g.diam == 800.0
    K2 = np.array([[150.0, 0, 400.0], [0, 150.0, 400.0], [0, 0, 1]])
    g2 = EyeGeometry.from_intrinsics(K2, D=np.zeros(4), size=(800, 800))
    assert g2.diam == pytest.approx(2 * 150.0 * np.radians(92.5), rel=1e-6)
