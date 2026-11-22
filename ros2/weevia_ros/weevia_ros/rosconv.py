"""numpy ↔ sensor_msgs, dict → CameraInfo. 노드들이 같이 쓴다."""
from __future__ import annotations

import numpy as np
from sensor_msgs.msg import CameraInfo, Image


def to_image(arr: np.ndarray, stamp, frame_id: str) -> Image:
    m = Image()
    m.header.stamp, m.header.frame_id = stamp, frame_id
    m.height, m.width = int(arr.shape[0]), int(arr.shape[1])
    m.encoding, m.is_bigendian, m.step = "mono8", 0, int(arr.shape[1])
    m.data = np.ascontiguousarray(arr).tobytes()
    return m


def from_image(m: Image) -> np.ndarray:
    if m.encoding != "mono8":
        raise ValueError(f"mono8 expected, got {m.encoding}")
    return np.frombuffer(m.data, np.uint8).reshape(m.height, m.step)[:, : m.width]


def to_camera_info(ci: dict, stamp, frame_id: str) -> CameraInfo:
    m = CameraInfo()
    m.header.stamp, m.header.frame_id = stamp, frame_id
    m.width, m.height, m.distortion_model = ci["width"], ci["height"], ci["distortion_model"]
    m.d, m.k, m.r, m.p = ci["d"], ci["k"], ci["r"], ci["p"]
    return m
