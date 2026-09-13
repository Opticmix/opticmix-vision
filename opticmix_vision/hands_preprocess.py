"""스테레오 IR 눈 영상 → 손추적 모델 입력 (left, right).

학습(SP2)과 추론(HandTracker)이 이 모듈 하나를 import 해 전처리를 일치시킨다(train/inference skew 0).
K(intrinsics)는 여기서 다루지 않는다 — 모델은 이미지만 받는다.

주 API = `eyes_to_model_input(left, right)`: `StereoFrame.left/right`(이미 분리된 눈)를 그대로 받는다.
`split_sbs` / `sbs_frame_to_model_input` 은 raw sbs 파일 재생(오프라인 도구)용으로만 남긴다 — 라이브 캡처의
스테레오 분리는 `device.split_stereo` 가 담당한다.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

__all__ = ["EyeGeometry", "preprocess_eye", "eyes_to_model_input", "split_sbs", "sbs_frame_to_model_input"]


def split_sbs(frame: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """raw side-by-side mono 프레임을 (first, second) 로 나눈다. 폭 = 2×높이 요구. 오프라인 파일 재생용."""
    if frame.ndim != 2:
        raise ValueError(f"mono (H,W) 여야 한다: {frame.shape}")
    h, w = frame.shape
    if w != 2 * h:
        raise ValueError(f"sbs 는 폭=2×높이 여야 한다: {frame.shape}")
    return frame[:, :h].copy(), frame[:, h:].copy()


def _kb4_theta_d(theta: float, D: np.ndarray) -> float:
    """KB4: θ_d = θ(1 + k1θ² + k2θ⁴ + k3θ⁶ + k4θ⁸)."""
    k1, k2, k3, k4 = (list(np.asarray(D, float).reshape(-1)) + [0.0] * 4)[:4]
    t2 = theta * theta
    return float(theta * (1 + k1 * t2 + k2 * t2 ** 2 + k3 * t2 ** 3 + k4 * t2 ** 4))


@dataclass(frozen=True)
class EyeGeometry:
    """눈당 이미지 서클 기하. 캘리브 전 추정값.

    ponytail: 기본값은 800px 눈 중앙·Ø614 추정. SP1 렌즈 캘리브가 cx/cy/diam 을 실측으로 교체한다.
    """
    eye: int = 800
    cx: float = 400.0
    cy: float = 400.0
    diam: float = 614.0

    @classmethod
    def from_intrinsics(cls, K: np.ndarray, D: np.ndarray, size: tuple[int, int], *,
                        diam_px: float | None = None, half_fov_deg: float = 92.5) -> "EyeGeometry":
        """KB4 캘리브(K, D=(k1..k4), size=(w,h)) → 서클 기하. cx/cy = 주점. diam = 실측(diam_px) 우선,
        없으면 KB4 로 반각(half_fov_deg)에서의 반경×2 를 눈 크기로 캡."""
        K = np.asarray(K, float); D = np.asarray(D, float).reshape(-1)
        w, h = int(size[0]), int(size[1])
        if diam_px is None:
            f = 0.5 * (K[0, 0] + K[1, 1])
            theta = np.radians(half_fov_deg)
            diam_px = min(float(2.0 * f * _kb4_theta_d(theta, D)), float(min(w, h)))
        return cls(eye=h, cx=float(K[0, 2]), cy=float(K[1, 2]), diam=float(diam_px))


def _crop_box(geom: "EyeGeometry") -> tuple[int, int, int, int]:
    """서클 정사각 크롭 박스 (x0, y0, x1, y1). 단일 정수 중심·반경으로 폭==높이 구조적 보장."""
    cx = int(round(geom.cx)); cy = int(round(geom.cy)); rad = int(round(geom.diam / 2.0))
    return cx - rad, cy - rad, cx + rad, cy + rad


def preprocess_eye(eye_img: np.ndarray, geom: EyeGeometry, size: int = 256) -> np.ndarray:
    """한 눈(mono H,W)을 모델 입력 (1,size,size) float[-1,+1] 로.

    서클 정사각 크롭 → size 리사이즈(INTER_AREA) → 서클 밖 마스크 → [-1,+1] 정규화.
    """
    x0, y0, x1, y1 = _crop_box(geom)
    h, w = eye_img.shape
    pad_l = max(0, -x0); pad_t = max(0, -y0)
    pad_r = max(0, x1 - w); pad_b = max(0, y1 - h)
    if pad_l or pad_t or pad_r or pad_b:
        eye_img = cv2.copyMakeBorder(eye_img, pad_t, pad_b, pad_l, pad_r, cv2.BORDER_CONSTANT, value=0)
        x0 += pad_l; x1 += pad_l; y0 += pad_t; y1 += pad_t
    crop = eye_img[y0:y1, x0:x1]
    resized = cv2.resize(crop, (size, size), interpolation=cv2.INTER_AREA)
    yy, xx = np.ogrid[:size, :size]
    c = (size - 1) / 2.0
    mask = (xx - c) ** 2 + (yy - c) ** 2 <= (size / 2.0) ** 2
    masked = np.where(mask, resized, 0).astype(np.float32)
    return (masked / 127.5 - 1.0)[None, :, :].astype(np.float32)


def eyes_to_model_input(
    left: np.ndarray, right: np.ndarray, geom: EyeGeometry | None = None, size: int = 256,
) -> tuple[np.ndarray, np.ndarray]:
    """이미 분리된 (left, right) 눈 → 모델 입력 각 (1,size,size) float[-1,+1]. StereoFrame 직결 주 API."""
    geom = geom if geom is not None else EyeGeometry(eye=int(left.shape[0]))
    return preprocess_eye(left, geom, size), preprocess_eye(right, geom, size)


def sbs_frame_to_model_input(
    frame: np.ndarray, geom: EyeGeometry | None = None, size: int = 256, lr_order: str = "first_is_left",
) -> tuple[np.ndarray, np.ndarray]:
    """raw sbs 프레임(오프라인 파일) → (left, right) 모델 입력. 라이브는 eyes_to_model_input 을 써라."""
    a, b = split_sbs(frame)
    first, second = (a, b) if lr_order == "first_is_left" else (b, a)
    return eyes_to_model_input(first, second, geom, size)
