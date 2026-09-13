"""ros — 캘리브 JSON → ROS camera_info YAML. 하드웨어·ROS 설치 없이 문자열로 검증.

ROS 는 KB4(Kannala-Brandt) 를 `equidistant` 왜곡 모델로 부른다. image_pipeline 이 읽는 키 이름과
행렬 모양(3×3 K/R, 3×4 P)이 정확해야 하므로 그 구조를 고정한다.

실행:
    cd sdk/python && python -m pytest tests/test_ros.py -v
"""

from __future__ import annotations

import numpy as np
import pytest

from opticmix_vision.calibration import CameraIntrinsics, StereoCalibration, StereoExtrinsics
from opticmix_vision.rectify import stereo_rectify_maps
from opticmix_vision.ros import camera_info_yaml, write_stereo_camera_info

SIZE = (320, 256)


def _calib(baseline_mm: float = 64.0) -> StereoCalibration:
    left = CameraIntrinsics(K=[[180.0, 0, 159.5], [0, 181.0, 127.5], [0, 0, 1]],
                            D=[-0.01, 0.002, -0.0003, 0.00004], size=SIZE)
    right = CameraIntrinsics(K=[[181.0, 0, 160.5], [0, 180.5, 128.0], [0, 0, 1]],
                             D=[-0.011, 0.0021, -0.00031, 0.000041], size=SIZE)
    ext = StereoExtrinsics(R=np.eye(3), t=[-baseline_mm, 0.0, 0.0])
    return StereoCalibration(left=left, right=right, extrinsics=ext, device={"model": "OPX-S1"}, meta={})


def _parse(yaml_text: str) -> dict:
    """의존성 없이 읽는 최소 파서 — 우리가 쓰는 평평한 구조(스칼라 + rows/cols/data)만 다룬다."""
    out: dict = {}
    cur: dict | None = None
    for raw in yaml_text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if not raw.startswith(" "):
            key, _, val = raw.partition(":")
            val = val.strip()
            if val:
                out[key.strip()] = val.strip('"')
                cur = None
            else:
                cur = {}
                out[key.strip()] = cur
        else:
            assert cur is not None, raw
            key, _, val = raw.strip().partition(":")
            val = val.strip()
            if val.startswith("["):
                cur[key] = [float(x) for x in val.strip("[]").split(",") if x.strip()]
            else:
                cur[key] = val
    return out


def test_camera_info_yaml_has_ros_keys_and_equidistant_model():
    c = _calib()
    text = camera_info_yaml(c.left, frame_id="omv_s1_left")
    d = _parse(text)
    assert d["image_width"] == "320" and d["image_height"] == "256"
    assert d["camera_name"] == "omv_s1_left"
    assert d["distortion_model"] == "equidistant"          # KB4 의 ROS 이름
    assert d["camera_matrix"]["rows"] == "3" and d["camera_matrix"]["cols"] == "3"
    assert d["camera_matrix"]["data"][:3] == [180.0, 0.0, 159.5]
    assert d["distortion_coefficients"]["cols"] == "4"     # equidistant 는 계수 4개
    assert d["distortion_coefficients"]["data"] == [-0.01, 0.002, -0.0003, 0.00004]
    assert d["projection_matrix"]["rows"] == "3" and d["projection_matrix"]["cols"] == "4"


def test_left_projection_has_no_baseline_term_and_right_has_minus_fx_times_baseline():
    c = _calib(baseline_mm=64.0)
    maps = stereo_rectify_maps(c, rectified_hfov_deg=90.0)
    fx = float(maps.P1[0, 0])
    dl = _parse(camera_info_yaml(c.left, frame_id="l", maps=maps, eye="left"))
    dr = _parse(camera_info_yaml(c.right, frame_id="r", maps=maps, eye="right"))
    assert dl["projection_matrix"]["data"][3] == pytest.approx(0.0)
    # ROS/OpenCV 규약: P2[0,3] = -fx · B (미터). 부호와 단위가 여기서 갈린다.
    assert dr["projection_matrix"]["data"][3] == pytest.approx(-fx * 0.064, rel=1e-6)
    # rectification_matrix 는 R1/R2 여야 한다 (단위행렬로 두면 정류가 안 맞는다)
    assert dl["rectification_matrix"]["data"][:3] == pytest.approx(list(maps.R1[0]), rel=1e-6)
    assert dr["rectification_matrix"]["data"][:3] == pytest.approx(list(maps.R2[0]), rel=1e-6)


def test_eye_must_be_left_or_right_when_maps_given():
    c = _calib()
    maps = stereo_rectify_maps(c, rectified_hfov_deg=90.0)
    with pytest.raises(ValueError, match="eye"):
        camera_info_yaml(c.left, frame_id="x", maps=maps, eye="middle")


def test_without_maps_projection_falls_back_to_K_and_warns_in_a_comment():
    c = _calib()
    text = camera_info_yaml(c.left, frame_id="l")
    d = _parse(text)
    assert d["projection_matrix"]["data"][0] == 180.0      # 정류 전이므로 K 그대로
    assert "rectif" in text.lower() or "정류" in text       # 미정류임을 문서에 남긴다


def test_write_stereo_camera_info_creates_two_files(tmp_path):
    c = _calib()
    maps = stereo_rectify_maps(c, rectified_hfov_deg=90.0)
    paths = write_stereo_camera_info(c, tmp_path, maps=maps, name_prefix="omv_s1")
    assert len(paths) == 2 and all(p.exists() for p in paths)
    names = sorted(p.name for p in paths)
    assert names == ["omv_s1_left.yaml", "omv_s1_right.yaml"]
    assert "equidistant" in paths[0].read_text(encoding="utf-8")


def test_yaml_is_ascii_safe_for_ros_tools(tmp_path):
    """ROS 도구가 로케일에 따라 UTF-8 을 못 읽는 경우가 있어 값 영역은 ASCII 로 쓴다."""
    c = _calib()
    text = camera_info_yaml(c.left, frame_id="omv_s1_left")
    body = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    body.encode("ascii")        # 주석 밖에 비ASCII 가 있으면 여기서 실패
