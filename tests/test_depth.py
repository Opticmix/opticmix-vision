"""depth — 정류된 스테레오 쌍에서 SGBM 시차/깊이. 하드웨어 없이 합성으로 검증.

검증 원리: 정면 평행 평면은 **전 화면 동일 시차**를 만든다. 텍스처 영상을 d 픽셀 밀어
오른쪽 영상을 합성하면 SGBM 이 되찾아야 하는 정답이 d 로 확정된다. 깊이는 Z = f·B/d.

실행:
    cd sdk/python && python -m pytest tests/test_depth.py -v
"""

from __future__ import annotations

import numpy as np
import pytest

from opticmix_vision.calibration import CameraIntrinsics, StereoCalibration, StereoExtrinsics
from opticmix_vision.depth import (
    DepthParams,
    DepthResult,
    compute_disparity,
    depth_from_pair,
    depth_from_rectified,
    disparity_to_depth_mm,
)
from opticmix_vision.rectify import stereo_rectify_maps

SIZE = (320, 256)          # (width, height)
SHIFT = 24                 # 정답 시차 (px)


def _texture(size: tuple[int, int], seed: int = 0) -> np.ndarray:
    """SGBM 이 매칭할 수 있는 고주파 + 저주파 혼합 텍스처 (uint8 mono)."""
    w, h = size
    rng = np.random.default_rng(seed)
    base = rng.integers(0, 256, size=(h, w), dtype=np.uint8)
    yy, xx = np.mgrid[0:h, 0:w]
    ramp = (127 + 100 * np.sin(xx / 7.0) * np.cos(yy / 9.0)).astype(np.uint8)
    return ((base.astype(np.uint16) + ramp) // 2).astype(np.uint8)


def _shifted_pair(shift: int = SHIFT, size: tuple[int, int] = SIZE) -> tuple[np.ndarray, np.ndarray]:
    """right[x] = left[x + shift] → 왼쪽 영상 기준 시차 = shift (정면 평행 평면)."""
    left = _texture(size)
    w = size[0]
    right = np.zeros_like(left)
    right[:, : w - shift] = left[:, shift:]
    return left, right


# ---------------------------------------------------------------- 순수 변환

def test_disparity_to_depth_uses_Z_equals_f_times_B_over_d():
    disp = np.array([[10.0, 20.0, 40.0]], dtype=np.float32)
    z = disparity_to_depth_mm(disp, fx_rect_px=500.0, baseline_mm=64.0)
    # Z = 500 * 64 / d
    assert np.allclose(z, [[3200.0, 1600.0, 800.0]], rtol=1e-6)


def test_disparity_zero_or_negative_becomes_nan_not_infinity():
    disp = np.array([[0.0, -3.0, 8.0]], dtype=np.float32)
    z = disparity_to_depth_mm(disp, fx_rect_px=500.0, baseline_mm=64.0)
    assert np.isnan(z[0, 0]) and np.isnan(z[0, 1])          # 0 으로 나눈 무한대를 내보내지 않는다
    assert np.isfinite(z[0, 2])


def test_disparity_to_depth_rejects_nonpositive_geometry():
    disp = np.ones((2, 2), dtype=np.float32)
    with pytest.raises(ValueError, match="baseline"):
        disparity_to_depth_mm(disp, fx_rect_px=500.0, baseline_mm=0.0)
    with pytest.raises(ValueError, match="fx"):
        disparity_to_depth_mm(disp, fx_rect_px=-1.0, baseline_mm=64.0)


def test_nan_disparity_propagates_as_nan_depth():
    disp = np.array([[np.nan, 16.0]], dtype=np.float32)
    z = disparity_to_depth_mm(disp, fx_rect_px=400.0, baseline_mm=64.0)
    assert np.isnan(z[0, 0]) and np.isfinite(z[0, 1])


# ---------------------------------------------------------------- SGBM

def test_sgbm_recovers_the_known_shift_of_a_synthetic_pair():
    left, right = _shifted_pair()
    disp = compute_disparity(left, right, DepthParams(num_disparities=64, block_size=7))
    # 좌우 무효 구간을 빼고 가운데만 본다
    core = disp[40:-40, 96:-64]
    ok = np.isfinite(core)
    assert ok.mean() > 0.8, f"유효 픽셀이 너무 적다: {ok.mean():.2f}"
    assert abs(float(np.median(core[ok])) - SHIFT) <= 1.0


def test_unmatchable_left_border_is_nan_not_a_wrong_number():
    left, right = _shifted_pair()
    disp = compute_disparity(left, right, DepthParams(num_disparities=64, block_size=7))
    # 왼쪽 num_disparities 폭은 대응점이 프레임 밖이라 매칭 불가 — 조용히 값을 만들면 안 된다
    assert np.isnan(disp[:, :16]).mean() > 0.9


def test_disparity_is_float_not_sgbm_fixed_point():
    left, right = _shifted_pair()
    disp = compute_disparity(left, right, DepthParams(num_disparities=64, block_size=7))
    assert disp.dtype == np.float32
    finite = disp[np.isfinite(disp)]
    assert finite.max() < 64.0 + 1.0          # ×16 고정소수점이 그대로 새어 나오면 여기서 걸린다


def test_num_disparities_must_be_multiple_of_16():
    with pytest.raises(ValueError, match="16"):
        DepthParams(num_disparities=50)
    with pytest.raises(ValueError, match="홀수"):
        DepthParams(block_size=8)


def test_mismatched_shapes_are_rejected():
    left, right = _shifted_pair()
    with pytest.raises(ValueError, match="크기"):
        compute_disparity(left, right[:, :-10], DepthParams())


# ---------------------------------------------------------------- 정류 + 깊이

def _synthetic_calib(baseline_mm: float = 64.0) -> StereoCalibration:
    intr = CameraIntrinsics(K=[[180.0, 0, SIZE[0] / 2 - 0.5], [0, 180.0, SIZE[1] / 2 - 0.5], [0, 0, 1]],
                            D=[0.0, 0.0, 0.0, 0.0], size=SIZE)
    ext = StereoExtrinsics(R=np.eye(3), t=[-baseline_mm, 0.0, 0.0])
    return StereoCalibration(left=intr, right=intr, extrinsics=ext, device={"model": "TEST"}, meta={})


def test_depth_from_rectified_matches_f_B_over_d_of_the_maps():
    calib = _synthetic_calib()
    maps = stereo_rectify_maps(calib, rectified_hfov_deg=90.0)
    left, right = _shifted_pair()
    res = depth_from_rectified(left, right, maps, DepthParams(num_disparities=64, block_size=7))

    assert isinstance(res, DepthResult)
    assert res.baseline_mm == pytest.approx(64.0, rel=1e-6)
    assert res.fx_rect_px == pytest.approx(float(maps.P1[0, 0]), rel=1e-9)
    expected = res.fx_rect_px * res.baseline_mm / SHIFT
    core = res.depth_mm[40:-40, 96:-64]
    ok = np.isfinite(core)
    assert abs(float(np.median(core[ok])) - expected) / expected < 0.06
    assert res.valid.dtype == np.bool_ and res.valid.shape == res.depth_mm.shape
    assert not np.isfinite(res.depth_mm[~res.valid]).any()      # 무효 화소는 반드시 NaN


def test_depth_from_pair_rectifies_first_and_reuses_given_maps():
    calib = _synthetic_calib()
    maps = stereo_rectify_maps(calib, rectified_hfov_deg=90.0)
    left, right = _shifted_pair()
    a = depth_from_pair(left, right, calib, maps=maps, params=DepthParams(num_disparities=64, block_size=7))
    b = depth_from_pair(left, right, calib, params=DepthParams(num_disparities=64, block_size=7),
                        rectified_hfov_deg=90.0)
    ok = np.isfinite(a.depth_mm) & np.isfinite(b.depth_mm)
    assert ok.mean() > 0.3
    assert np.allclose(a.depth_mm[ok], b.depth_mm[ok], rtol=1e-6)


def test_depth_range_summary_reports_only_valid_pixels():
    calib = _synthetic_calib()
    maps = stereo_rectify_maps(calib, rectified_hfov_deg=90.0)
    left, right = _shifted_pair()
    res = depth_from_rectified(left, right, maps, DepthParams(num_disparities=64, block_size=7))
    s = res.summary()
    assert "valid" in s and "mm" in s
    assert np.isfinite(res.valid_fraction) and 0.0 < res.valid_fraction <= 1.0
