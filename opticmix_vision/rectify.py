"""cv2.fisheye 기반 undistort / stereo rectify 맵 생성. 의존성: numpy, opencv 뿐.

맵은 한 번 만들어 두고 매 프레임 `cv2.remap` 만 한다 (`remap_pair`).

왜 OpenCV 의 자동 새 카메라 행렬(balance / fov_scale)을 쓰지 않는가:
  `cv2.fisheye.stereoRectify` / `estimateNewCameraMatrixForUndistortRectify` 는 프레임 **가장자리 픽셀**을
  undistort 한 결과로 출력 초점거리를 정한다. 화각이 180° 에 가까운 렌즈는 프레임 가장자리의 입사각이
  90° 를 넘고, 그 점은 어떤 핀홀 평면에도 맺히지 않는다(tan θ 발산). 그러면 f 가 0 근처로 무너진 P 가
  조용히 나온다 — 합성 150° 렌즈로 실제 재현했다 (P1[0,0] ≈ 1e-11). 그래서 여기서는
  **출력 핀홀의 수평 화각(`rectified_hfov_deg`)을 호출자가 명시** 하고 P 를 직접 만든다.
  회전(R1, R2)은 K 와 무관하므로 OpenCV `stereoRectify` 에서 가져온다.

핀홀 출력의 한계: 입사각 < 90° 만 담긴다. 넓게 펼수록(hfov ↑) 중심 해상도가 떨어진다.
이 카메라에 적절한 hfov 는 ❓ 실측 FOV 확정 후 정할 것. 기본값 90° 는 설계 선택이지 하드웨어 사양이 아니다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from .calibration import CameraIntrinsics, StereoCalibration

__all__ = ["RectifyMaps", "pinhole_matrix", "undistort_maps", "stereo_rectify_maps", "remap_pair",
           "DEFAULT_RECTIFIED_HFOV_DEG"]

DEFAULT_RECTIFIED_HFOV_DEG = 90.0


@dataclass(frozen=True, eq=False)
class RectifyMaps:
    """`cv2.remap` 용 맵 쌍과 rectify 행렬. P1/P2 는 3x4, Q 는 4x4 (`cv2.reprojectImageTo3D` 용)."""

    left: tuple[np.ndarray, np.ndarray]
    right: tuple[np.ndarray, np.ndarray]
    R1: np.ndarray
    R2: np.ndarray
    P1: np.ndarray
    P2: np.ndarray
    Q: np.ndarray
    size: tuple[int, int]                 # (width, height) 출력

    @property
    def rectified_focal_px(self) -> float:
        return float(self.P1[0, 0])

    @property
    def baseline_from_P2_mm(self) -> float:
        """P2[0,3] = Tx · f 관계에서 역산. 입력 t 의 |t| 와 일치해야 한다."""
        return float(abs(self.P2[0, 3] / self.P2[0, 0]))


def _size_tuple(size: Any) -> tuple[int, int]:
    w, h = int(size[0]), int(size[1])
    if w <= 0 or h <= 0:
        raise ValueError(f"size 는 양의 (width, height): {size!r}")
    return w, h


def pinhole_matrix(size: tuple[int, int], hfov_deg: float) -> np.ndarray:
    """출력 핀홀 K. f = (w/2) / tan(hfov/2), 주점 = 영상 중심. hfov 는 (0, 180) 이어야 한다."""
    w, h = _size_tuple(size)
    if not 0.0 < float(hfov_deg) < 180.0:
        raise ValueError(f"rectified_hfov_deg 는 0 < hfov < 180 (핀홀 한계): {hfov_deg!r}")
    f = (w / 2.0) / math.tan(math.radians(float(hfov_deg)) / 2.0)
    return np.array([[f, 0.0, w / 2.0], [0.0, f, h / 2.0], [0.0, 0.0, 1.0]], dtype=np.float64)


def undistort_maps(intr: CameraIntrinsics, *, rectified_hfov_deg: float = DEFAULT_RECTIFIED_HFOV_DEG,
                   new_size: tuple[int, int] | None = None, R: np.ndarray | None = None,
                   map_type: int | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """한 눈 undistort 맵. 반환 (map1, map2, P_new). P_new 는 출력 영상의 핀홀 K (3x3)."""
    import cv2
    out = _size_tuple(new_size) if new_size is not None else _size_tuple(intr.size)
    Rm = np.eye(3) if R is None else np.asarray(R, dtype=np.float64)
    P = pinhole_matrix(out, rectified_hfov_deg)
    m1, m2 = cv2.fisheye.initUndistortRectifyMap(
        intr.K, intr.D, Rm, P, out, cv2.CV_32FC1 if map_type is None else int(map_type))
    return m1, m2, P


def stereo_rectify_maps(calib: StereoCalibration, *, rectified_hfov_deg: float = DEFAULT_RECTIFIED_HFOV_DEG,
                        new_size: tuple[int, int] | None = None, map_type: int | None = None) -> RectifyMaps:
    """좌우 rectify 맵. extrinsics 규약 p_right = R·p_left + t (OpenCV stereoRectify 와 동일).

    R1/R2 는 `cv2.stereoRectify` (K 무관) 에서, P1/P2/Q 는 `pinhole_matrix` 로 직접 구성한다
    (OpenCV 와 같은 형태: P2[0,3] = Tx·f, Q[3,2] = -1/Tx, zero-disparity 이므로 좌우 주점 동일).
    베이스라인이 수평(|Tx| > |Ty|) 이 아니면 에러 — 수직 스테레오는 v0 범위 밖.
    """
    import cv2
    if calib.left.size != calib.right.size:
        raise ValueError(f"좌우 영상 크기가 다르다: {calib.left.size} vs {calib.right.size}")
    size = _size_tuple(calib.left.size)
    out = _size_tuple(new_size) if new_size is not None else size

    R, t = calib.extrinsics.R, calib.extrinsics.t.reshape(3, 1)
    dummy_K = np.array([[1.0, 0.0, size[0] / 2.0], [0.0, 1.0, size[1] / 2.0], [0.0, 0.0, 1.0]])
    R1, R2, _, _, _, _, _ = cv2.stereoRectify(dummy_K, np.zeros(5), dummy_K, np.zeros(5), size, R, t,
                                              flags=cv2.CALIB_ZERO_DISPARITY, alpha=0.0)
    R1, R2 = np.asarray(R1, dtype=np.float64), np.asarray(R2, dtype=np.float64)
    t2 = (R2 @ t).reshape(3)
    if not abs(t2[0]) > abs(t2[1]):
        raise ValueError(f"베이스라인이 수평이 아니다 (rectified t = {t2.tolist()}); 수직 스테레오는 미지원")
    Tx = float(t2[0])

    Kn = pinhole_matrix(out, rectified_hfov_deg)
    f, cx, cy = Kn[0, 0], Kn[0, 2], Kn[1, 2]
    P1 = np.array([[f, 0.0, cx, 0.0], [0.0, f, cy, 0.0], [0.0, 0.0, 1.0, 0.0]])
    P2 = np.array([[f, 0.0, cx, Tx * f], [0.0, f, cy, 0.0], [0.0, 0.0, 1.0, 0.0]])
    Q = np.array([[1.0, 0.0, 0.0, -cx], [0.0, 1.0, 0.0, -cy], [0.0, 0.0, 0.0, f],
                  [0.0, 0.0, -1.0 / Tx, 0.0]])

    mt = cv2.CV_32FC1 if map_type is None else int(map_type)
    l1, l2 = cv2.fisheye.initUndistortRectifyMap(calib.left.K, calib.left.D, R1, P1, out, mt)
    r1, r2 = cv2.fisheye.initUndistortRectifyMap(calib.right.K, calib.right.D, R2, P2, out, mt)
    return RectifyMaps(left=(l1, l2), right=(r1, r2), R1=R1, R2=R2, P1=P1, P2=P2, Q=Q, size=out)


def remap_pair(left: np.ndarray, right: np.ndarray, maps: RectifyMaps, *,
               interpolation: int | None = None) -> tuple[np.ndarray, np.ndarray]:
    import cv2
    interp = cv2.INTER_LINEAR if interpolation is None else int(interpolation)
    return (cv2.remap(left, maps.left[0], maps.left[1], interp),
            cv2.remap(right, maps.right[0], maps.right[1], interp))
