"""camera_calibration_parsers 형식 YAML(ROS camera_calibration 이 저장하는 형식) → 평평한 dict. ROS 를 import 하지 않는다."""
from __future__ import annotations

from pathlib import Path

import yaml


def load_camera_info(path: str | Path) -> dict:
    if not str(path).strip():
        raise ValueError("camera_info YAML path is empty — pass left_camera_info / right_camera_info")
    y = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return {
        "width": int(y["image_width"]), "height": int(y["image_height"]),
        "distortion_model": y["distortion_model"],
        "d": [float(v) for v in y["distortion_coefficients"]["data"]],
        "k": [float(v) for v in y["camera_matrix"]["data"]],
        "r": [float(v) for v in y["rectification_matrix"]["data"]],
        "p": [float(v) for v in y["projection_matrix"]["data"]],
    }


def load_stereo_camera_info(left: str | Path, right: str | Path) -> dict | None:
    """둘 다 주면 {"left": dict, "right": dict}, 둘 다 비면 None(미보정), 하나만 주면 ValueError."""
    given = [bool(str(p).strip()) for p in (left, right)]
    if not any(given):
        return None
    if not all(given):
        raise ValueError("pass both left_camera_info and right_camera_info, or neither (uncalibrated)")
    return {"left": load_camera_info(left), "right": load_camera_info(right)}


def uncalibrated(width: int, height: int) -> dict:
    """보정 파일이 없을 때 — 크기만 채우고 K·R·P 는 0, D 는 비움(camera_info_manager 의 미보정 관례)."""
    return {"width": int(width), "height": int(height), "distortion_model": "",
            "d": [], "k": [0.0] * 9, "r": [0.0] * 9, "p": [0.0] * 12}
