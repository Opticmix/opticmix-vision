"""calibration — 스키마 라운드트립, 모르는 키/NaN/접힌 KB4/비회전 R 거부, 베이스라인."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from opticmix_vision.calibration import (
    SCHEMA_NAME, SCHEMA_VERSION, CalibrationError, CameraIntrinsics, StereoCalibration,
    StereoExtrinsics, kb4_is_injective, load_calibration, save_calibration, synthetic_equidistant,
)

SIZE = (1280, 800)
FOV_SYNTH = 180.0        # 합성 값. 실측 FOV 아님 (❓)
BASELINE_SYNTH = 64.0    # 합성 값 (도면 베이스라인과 같은 수치지만 여기서는 테스트 상수일 뿐)


def _calib(**meta) -> StereoCalibration:
    intr = synthetic_equidistant(SIZE, FOV_SYNTH)
    ext = StereoExtrinsics(R=np.eye(3), t=np.array([-BASELINE_SYNTH, 0.0, 0.0]))
    return StereoCalibration(left=intr, right=intr, extrinsics=ext, device={"model": "synthetic"}, meta=meta)


def test_synthetic_equidistant_is_valid_and_centered():
    intr = synthetic_equidistant(SIZE, FOV_SYNTH)
    assert intr.validate() == []
    assert intr.size == SIZE and intr.model == "KB4"
    assert intr.cx == pytest.approx((SIZE[0] - 1) / 2) and intr.cy == pytest.approx((SIZE[1] - 1) / 2)
    assert intr.fx == pytest.approx((min(SIZE) / 2) / np.radians(FOV_SYNTH / 2))
    assert np.all(intr.D == 0)


def test_round_trip_json_file(tmp_path: Path):
    c = _calib(reproj_rms_px=0.42, tool="test")
    p = save_calibration(c, tmp_path / "sub" / "calib.json")
    loaded = load_calibration(p)
    assert np.array_equal(loaded.left.K, c.left.K) and np.array_equal(loaded.right.D, c.right.D)
    assert np.array_equal(loaded.extrinsics.R, c.extrinsics.R) and np.array_equal(loaded.extrinsics.t, c.extrinsics.t)
    assert loaded.baseline_mm == pytest.approx(BASELINE_SYNTH)
    assert loaded.device == {"model": "synthetic"} and loaded.meta["reproj_rms_px"] == 0.42
    d = json.loads(p.read_text(encoding="utf-8"))
    assert d["schema"] == SCHEMA_NAME and d["schema_version"] == SCHEMA_VERSION and d["units"] == "mm"
    assert set(d) == {"schema", "schema_version", "units", "device", "left", "right", "extrinsics", "meta"}
    assert set(d["left"]) == {"model", "K", "D", "size"}        # 내부 파이프라인 CameraIntrinsics 와 동일 키


def test_unknown_keys_are_rejected():
    d = _calib().to_dict()
    d["baseline"] = 64.0
    with pytest.raises(CalibrationError, match="모르는 키"):
        StereoCalibration.from_dict(d)
    d = _calib().to_dict()
    d["left"]["fov_deg"] = 185
    with pytest.raises(CalibrationError, match="left"):
        StereoCalibration.from_dict(d)


def test_schema_mismatch_is_rejected():
    d = _calib().to_dict()
    d["schema_version"] = 99
    with pytest.raises(CalibrationError, match="schema_version"):
        StereoCalibration.from_dict(d)
    d = _calib().to_dict()
    d["schema"] = "something.else"
    with pytest.raises(CalibrationError, match="schema"):
        StereoCalibration.from_dict(d)


def test_nan_is_rejected_on_write_and_read():
    intr = synthetic_equidistant(SIZE, FOV_SYNTH)
    with pytest.raises(CalibrationError, match="NaN"):
        CameraIntrinsics(K=intr.K * np.nan, D=intr.D, size=SIZE)
    text = _calib().to_json().replace('"units": "mm"', '"units": "mm", "meta": {"x": NaN}', 1)
    # 위 치환은 meta 키 중복이 되므로 별도로: JSON 파서가 NaN 을 만나면 거부하는지 직접 확인
    with pytest.raises(CalibrationError, match="NaN"):
        StereoCalibration.from_json('{"schema": "x", "v": NaN}')
    assert "NaN" in text                                          # 문자열 조작 자체는 성립


def test_folded_kb4_is_rejected():
    ok, fold = kb4_is_injective(np.array([-0.5, 0, 0, 0]), np.radians(92.5))
    assert not ok and fold is not None and np.degrees(fold) < 60
    with pytest.raises(CalibrationError, match="접힌다"):
        CameraIntrinsics(K=synthetic_equidistant(SIZE, FOV_SYNTH).K, D=[-0.5, 0, 0, 0], size=SIZE)
    assert kb4_is_injective(np.array([0.01, -0.002, 0.0, 0.0]), np.radians(92.5))[0]


def test_bad_shapes_and_values():
    intr = synthetic_equidistant(SIZE, FOV_SYNTH)
    with pytest.raises(CalibrationError, match="D 는"):
        CameraIntrinsics(K=intr.K, D=[0, 0, 0], size=SIZE)
    with pytest.raises(CalibrationError, match="K 는"):
        CameraIntrinsics(K=np.eye(4), D=intr.D, size=SIZE)
    with pytest.raises(CalibrationError, match="fx/fy"):
        CameraIntrinsics(K=np.diag([-1.0, 1.0, 1.0]), D=intr.D, size=SIZE)
    with pytest.raises(CalibrationError, match="KB4 만"):
        CameraIntrinsics(K=intr.K, D=intr.D, size=SIZE, model="pinhole")
    with pytest.raises(CalibrationError, match="size"):
        CameraIntrinsics(K=intr.K, D=intr.D, size=(0, 800))


def test_extrinsics_validation():
    with pytest.raises(CalibrationError, match="회전행렬"):
        StereoExtrinsics(R=np.eye(3) * 2, t=[1, 0, 0])
    with pytest.raises(CalibrationError, match="회전행렬"):
        StereoExtrinsics(R=np.diag([1, 1, -1]), t=[1, 0, 0])        # det = -1 (반사)
    with pytest.raises(CalibrationError, match="베이스라인 0"):
        StereoExtrinsics(R=np.eye(3), t=[0, 0, 0])
    with pytest.raises(CalibrationError, match="convention"):
        StereoExtrinsics.from_dict({"R": np.eye(3).tolist(), "t": [1, 0, 0], "convention": "p_left = ..."})
    e = StereoExtrinsics(R=np.eye(3), t=[3.0, 4.0, 0.0])
    assert e.baseline_mm == pytest.approx(5.0)


def test_units_and_device_block_are_strict():
    intr = synthetic_equidistant(SIZE, FOV_SYNTH)
    ext = StereoExtrinsics(R=np.eye(3), t=[1, 0, 0])
    with pytest.raises(CalibrationError, match="units"):
        StereoCalibration(left=intr, right=intr, extrinsics=ext, units="m")
    with pytest.raises(CalibrationError, match="device"):
        StereoCalibration(left=intr, right=intr, extrinsics=ext, device={"serial": 1234})   # type: ignore[dict-item]
