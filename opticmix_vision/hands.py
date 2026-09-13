"""손추적 공개 API — Opticmix Vision 자체 심볼.

정보모델은 손→손바닥+손가락5(각 4 bone)+팔, pinch/grab 지표, 프레임당 N손.
모델이 정확히 이 양들을 회귀한다. 식별자·메모리 레이아웃은 전부 우리 것이다.
좌표: 오른손계, mm, X=우 Y=상 Z=사용자 방향, 장치 원점. 회전은 (x, y, z, w).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import Any, Tuple

import numpy as np

from .device import StereoFrame
from .hands_preprocess import EyeGeometry, eyes_to_model_input

Vec3 = Tuple[float, float, float]
Quat = Tuple[float, float, float, float]   # x, y, z, w

__all__ = [
    "Vec3", "Quat", "FingerKind", "Chirality", "Bone", "Finger", "Palm", "Arm", "Hand", "TrackingFrame",
    "FrameTransform", "CAMERA_TO_DEVICE", "assemble_hand", "HandTracker",
]


class FingerKind(IntEnum):
    THUMB = 0
    INDEX = 1
    MIDDLE = 2
    RING = 3
    PINKY = 4


class Chirality(IntEnum):
    UNKNOWN = 0
    LEFT = 1
    RIGHT = 2


@dataclass(frozen=True)
class Bone:
    start: Vec3
    end: Vec3
    rotation: Quat


@dataclass(frozen=True)
class Finger:
    kind: FingerKind
    bones: tuple[Bone, Bone, Bone, Bone]     # 중수골, 기절, 중절, 말절 순
    width_mm: float
    is_extended: bool

    def __post_init__(self) -> None:
        if len(self.bones) != 4:
            raise ValueError(f"Finger.bones 는 4개: {len(self.bones)}")

    @property
    def tip(self) -> Vec3:
        return self.bones[3].end


@dataclass(frozen=True)
class Palm:
    position: Vec3
    normal: Vec3
    direction: Vec3
    orientation: Quat
    width_mm: float


@dataclass(frozen=True)
class Arm:
    elbow: Vec3
    wrist: Vec3
    rotation: Quat
    width_mm: float


@dataclass(frozen=True)
class Hand:
    chirality: Chirality
    confidence: float
    palm: Palm
    fingers: tuple[Finger, ...]              # 길이 5, FingerKind 순
    arm: Arm
    pinch_strength: float
    pinch_distance_mm: float
    grab_strength: float
    grab_angle: float

    def __post_init__(self) -> None:
        kinds = [f.kind for f in self.fingers]
        if kinds != list(FingerKind):
            raise ValueError(f"Hand.fingers 는 FingerKind 순 5개여야 한다: {kinds}")


@dataclass(frozen=True)
class TrackingFrame:
    hands: tuple[Hand, ...]                  # 0..N — 구조적 제한 없음
    host_ns: int
    frame_id: int


# ---------------------------------------------------------------------------
# 좌표 변환 (상수 T: 모델 학습 기준 프레임 → Opticmix 장치 프레임)
# ---------------------------------------------------------------------------

def _rotmat_to_quat(R: np.ndarray) -> np.ndarray:
    """(3,3) 회전행렬 → (x,y,z,w). Shepperd 방식(분기로 수치 안정)."""
    m = np.asarray(R, dtype=np.float64)
    tr = m[0, 0] + m[1, 1] + m[2, 2]
    if tr > 0:
        s = np.sqrt(tr + 1.0) * 2
        w = 0.25 * s; x = (m[2, 1] - m[1, 2]) / s; y = (m[0, 2] - m[2, 0]) / s; z = (m[1, 0] - m[0, 1]) / s
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = np.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2
        w = (m[2, 1] - m[1, 2]) / s; x = 0.25 * s; y = (m[0, 1] + m[1, 0]) / s; z = (m[0, 2] + m[2, 0]) / s
    elif m[1, 1] > m[2, 2]:
        s = np.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2
        w = (m[0, 2] - m[2, 0]) / s; x = (m[0, 1] + m[1, 0]) / s; y = 0.25 * s; z = (m[1, 2] + m[2, 1]) / s
    else:
        s = np.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2
        w = (m[1, 0] - m[0, 1]) / s; x = (m[0, 2] + m[2, 0]) / s; y = (m[1, 2] + m[2, 1]) / s; z = 0.25 * s
    return np.array([x, y, z, w], dtype=np.float64)


def _quat_mul(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """(x,y,z,w) 해밀턴 곱 a⊗b."""
    ax, ay, az, aw = a; bx, by, bz, bw = b
    return np.array([
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    ], dtype=np.float64)


# OpenCV 카메라 프레임(X우,Y하,Z전방) → Opticmix 장치 프레임(X우, Y=광축, Z=X×Y=−Yc).
CAMERA_TO_DEVICE = np.array([[1.0, 0.0, 0.0],
                             [0.0, 0.0, 1.0],
                             [0.0, -1.0, 0.0]])


@dataclass(frozen=True)
class FrameTransform:
    """p' = R·p + t. 방향벡터는 R 만, 회전은 q' = q_R ⊗ q."""
    R: np.ndarray
    t: np.ndarray

    @classmethod
    def identity(cls) -> "FrameTransform":
        return cls(R=np.eye(3), t=np.zeros(3))

    @classmethod
    def from_calib_T_json(cls, path: "str | Path", *, to: str = "device") -> "FrameTransform":
        """gt_capture `calib_T.json` (T_sir_to_oem: SIR device → OEM 카메라 프레임, 4x4) 를 읽는다.
        to="camera": 그대로(투영·오버레이용). to="device": CAMERA_TO_DEVICE 를 곱해 API 출력 프레임으로."""
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        m = d["T_sir_to_oem"]
        if isinstance(m, dict):
            m = m["matrix"]
        M = np.asarray(m, dtype=float)
        if M.shape != (4, 4):
            raise ValueError(f"T_sir_to_oem 은 4x4 여야 한다: {M.shape}")
        R, t = M[:3, :3], M[:3, 3]
        if to == "camera":
            return cls(R=R, t=t)
        if to == "device":
            return cls(R=CAMERA_TO_DEVICE @ R, t=CAMERA_TO_DEVICE @ t)
        raise ValueError(f"to 는 'camera' 또는 'device': {to!r}")

    def apply_point(self, p: Vec3) -> Vec3:
        v = np.asarray(self.R, float) @ np.asarray(p, float) + np.asarray(self.t, float)
        return (float(v[0]), float(v[1]), float(v[2]))

    def apply_direction(self, d: Vec3) -> Vec3:
        v = np.asarray(self.R, float) @ np.asarray(d, float)
        return (float(v[0]), float(v[1]), float(v[2]))

    def apply_quat(self, q: Quat) -> Quat:
        r = _quat_mul(_rotmat_to_quat(self.R), np.asarray(q, float))
        return (float(r[0]), float(r[1]), float(r[2]), float(r[3]))


# ---------------------------------------------------------------------------
# 텐서 → Hand 조립 (모델 출력 계약 = 내부 스펙)
# ---------------------------------------------------------------------------

def _sigmoid(x: float) -> float:
    return float(1.0 / (1.0 + np.exp(-float(x))))


def _v3(a) -> Vec3:
    return (float(a[0]), float(a[1]), float(a[2]))


def _q4(a) -> Quat:
    return (float(a[0]), float(a[1]), float(a[2]), float(a[3]))


def assemble_hand(
    joints: np.ndarray,        # (28,3) mm
    quats: np.ndarray,         # (22,4) xyzw
    palm_vecs: np.ndarray,     # (2,3) normal, direction
    metrics: np.ndarray,       # (4,) pinch_distance, grab_angle, pinch_strength, grab_strength
    widths: np.ndarray,        # (7,) palm, thumb..pinky, arm
    extended: np.ndarray,      # (5,) 로짓
    *,
    presence_logit: float,
    chirality_logit: float,    # >0 → RIGHT
    transform: FrameTransform | None = None,
) -> Hand:
    """한 슬롯의 모델 출력 텐서를 Hand 로 조립한다. 손가락 5 유니크점 → 4 bone(끝점 공유)."""
    T = transform if transform is not None else FrameTransform.identity()
    P = lambda a: T.apply_point(_v3(a))          # noqa: E731
    D = lambda a: T.apply_direction(_v3(a))      # noqa: E731
    Q = lambda a: T.apply_quat(_q4(a))           # noqa: E731

    fingers = []
    for f in FingerKind:
        pts = [P(joints[f * 5 + i]) for i in range(5)]
        bones = tuple(Bone(start=pts[b], end=pts[b + 1], rotation=Q(quats[f * 4 + b])) for b in range(4))
        fingers.append(Finger(kind=f, bones=bones, width_mm=float(widths[1 + f]), is_extended=bool(extended[f] > 0)))

    palm = Palm(position=P(joints[27]), normal=D(palm_vecs[0]), direction=D(palm_vecs[1]),
                orientation=Q(quats[20]), width_mm=float(widths[0]))
    arm = Arm(elbow=P(joints[25]), wrist=P(joints[26]), rotation=Q(quats[21]), width_mm=float(widths[6]))
    return Hand(
        chirality=Chirality.RIGHT if chirality_logit > 0 else Chirality.LEFT,
        confidence=_sigmoid(presence_logit),
        palm=palm, fingers=tuple(fingers), arm=arm,
        pinch_distance_mm=float(metrics[0]), grab_angle=float(metrics[1]),
        pinch_strength=float(metrics[2]), grab_strength=float(metrics[3]),
    )


# ---------------------------------------------------------------------------
# HandTracker — StereoFrame → TrackingFrame (폴링)
# ---------------------------------------------------------------------------

class HandTracker:
    """`tracker.track(frame)` 폴링 API. 런타임은 주입(테스트) 또는 model_path 로 ONNX 생성.

    onnxruntime 이 없으면 model_path 생성 시점에 명확한 ImportError 가 난다.
    frame_transform=None 이면 출력은 모델 원프레임 그대로이며 model_info["frame"] 이 이를 명시한다.
    """

    def __init__(self, model_path: str | Path | None = None, *, runtime: Any = None,
                 frame_transform: FrameTransform | None = None, geometry: EyeGeometry | None = None,
                 confidence_threshold: float = 0.5) -> None:
        if runtime is None:
            if model_path is None:
                raise ValueError("model_path 또는 runtime 중 하나는 필요하다")
            from ._hands_runtime import OnnxRuntime
            runtime = OnnxRuntime(model_path)
        self._rt = runtime
        self._T = frame_transform
        self._geom = geometry
        self.confidence_threshold = float(confidence_threshold)

    @property
    def model_info(self) -> dict[str, Any]:
        return {**self._rt.info, "frame": "opticmix_device" if self._T is not None else "sir170_rig",
                "confidence_threshold": self.confidence_threshold}

    def track(self, frame: StereoFrame) -> TrackingFrame:
        L, R = eyes_to_model_input(frame.left, frame.right, self._geom, size=self._rt.input_size)
        out = self._rt.run(L[None], R[None])                       # (1,1,S,S)
        hands = []
        for s in range(int(out["presence"].shape[0])):
            conf = _sigmoid(float(out["presence"][s]))
            if conf < self.confidence_threshold:
                continue
            hands.append(assemble_hand(
                out["joints"][s], out["quats"][s], out["palm_vecs"][s], out["metrics"][s],
                out["widths"][s], out["extended"][s],
                presence_logit=float(out["presence"][s]), chirality_logit=float(out["chirality"][s]),
                transform=self._T,
            ))
        return TrackingFrame(hands=tuple(hands), host_ns=int(frame.host_ns), frame_id=int(frame.index))
