"""손추적 모델 런타임. `HandRuntime` 프로토콜 뒤에 ONNX(선택 의존) 와 스텁을 둔다.

출력 계약: joints[S,28,3] quats[S,22,4] palm_vecs[S,2,3] metrics[S,4] widths[S,7] extended[S,5]
presence[S] chirality[S]. 입력: left/right 각 [1,1,size,size] float32 [-1,1].
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

import numpy as np

__all__ = ["HandRuntime", "StubRuntime", "OnnxRuntime", "OUTPUT_NAMES"]

OUTPUT_NAMES = ("joints", "quats", "palm_vecs", "metrics", "widths", "extended", "presence", "chirality")
_HANDS_EXTRA_HINT = "손추적 런타임에는 onnxruntime 이 필요하다: pip install opticmix-vision[hands]"


class HandRuntime(Protocol):
    slots: int
    input_size: int
    info: dict[str, Any]

    def run(self, left: np.ndarray, right: np.ndarray) -> dict[str, np.ndarray]: ...


def _check_inputs(left: np.ndarray, right: np.ndarray, size: int) -> None:
    want = (1, 1, size, size)
    if tuple(left.shape) != want or tuple(right.shape) != want:
        raise ValueError(f"입력은 각 {want} 여야 한다: left {left.shape}, right {right.shape}")


class StubRuntime:
    """결정론 텐서를 돌려주는 가짜 런타임 — 조립·임계·변환 로직 테스트용. 실모델 불필요."""

    def __init__(self, slots: int = 1, input_size: int = 256, *,
                 presence: list[float] | None = None, chirality: list[float] | None = None) -> None:
        self.slots = int(slots)
        self.input_size = int(input_size)
        self._presence = list(presence) if presence is not None else [5.0] + [-5.0] * (self.slots - 1)
        self._chirality = list(chirality) if chirality is not None else [5.0] * self.slots
        if len(self._presence) != self.slots or len(self._chirality) != self.slots:
            raise ValueError("presence/chirality 길이는 slots 와 같아야 한다")
        self.info = {"runtime": "stub", "slots": self.slots, "input_size": self.input_size}

    def run(self, left: np.ndarray, right: np.ndarray) -> dict[str, np.ndarray]:
        _check_inputs(left, right, self.input_size)
        S = self.slots
        joints = np.stack([np.array([(i + 100.0 * s, 2.0 * i, 3.0 * i) for i in range(28)], np.float32) for s in range(S)])
        quats = np.tile(np.array([0, 0, 0, 1], np.float32), (S, 22, 1))
        palm_vecs = np.tile(np.array([[0, 1, 0], [0, 0, -1]], np.float32), (S, 1, 1))
        metrics = np.tile(np.array([30.0, 1.0, 0.2, 0.1], np.float32), (S, 1))
        widths = np.tile(np.array([80, 20, 18, 18, 17, 16, 60], np.float32), (S, 1))
        extended = np.tile(np.array([1.0, 1.0, -1.0, -1.0, -1.0], np.float32), (S, 1))
        return {
            "joints": joints, "quats": quats, "palm_vecs": palm_vecs, "metrics": metrics,
            "widths": widths, "extended": extended,
            "presence": np.array(self._presence, np.float32), "chirality": np.array(self._chirality, np.float32),
        }


class OnnxRuntime:
    """onnxruntime 세션 래퍼. 미설치면 생성 시점에 명확한 ImportError (조용한 실패 금지)."""

    def __init__(self, model_path: str | Path, *, providers: list[str] | None = None,
                 input_size: int | None = None) -> None:
        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise ImportError(_HANDS_EXTRA_HINT) from exc
        path = Path(model_path)
        if not path.exists():
            raise FileNotFoundError(f"모델 파일 없음: {path}")
        self._sess = ort.InferenceSession(str(path), providers=providers or ["CPUExecutionProvider"])
        names = {i.name for i in self._sess.get_inputs()}
        if not {"left", "right"} <= names:
            raise ValueError(f"ONNX 입력은 'left','right' 여야 한다: {sorted(names)}")
        shape = self._sess.get_inputs()[0].shape
        # 입력 크기는 추측하지 않는다 — 심볼릭 shape 면 호출자가 학습 해상도를 명시해야 한다
        # (기본값을 박으면 학습과 다른 해상도로 전처리돼 조용히 잘못된 추론이 된다).
        if input_size is not None:
            self.input_size = int(input_size)
        elif len(shape) >= 1 and isinstance(shape[-1], int):
            self.input_size = int(shape[-1])
        else:
            raise ValueError(f"ONNX 입력 크기를 shape 에서 확정할 수 없다(심볼릭 {shape}). "
                             "학습 해상도를 input_size= 로 명시하라")
        outs = [o.name for o in self._sess.get_outputs()]
        missing = [n for n in OUTPUT_NAMES if n not in outs]
        if missing:
            raise ValueError(f"ONNX 출력 누락: {missing} (있음: {outs})")
        pshape = next(o.shape for o in self._sess.get_outputs() if o.name == "presence")
        self.slots = int(pshape[0]) if pshape and isinstance(pshape[0], int) else 1
        meta = self._sess.get_modelmeta()
        self.info = {"runtime": "onnxruntime", "model": str(path), "slots": self.slots, "input_size": self.input_size,
                     "providers": self._sess.get_providers(),
                     "model_version": int(getattr(meta, "version", 0) or 0),
                     "producer": str(getattr(meta, "producer_name", "") or "")}

    def run(self, left: np.ndarray, right: np.ndarray) -> dict[str, np.ndarray]:
        _check_inputs(left, right, self.input_size)
        vals = self._sess.run(list(OUTPUT_NAMES), {"left": left.astype(np.float32), "right": right.astype(np.float32)})
        out = dict(zip(OUTPUT_NAMES, vals))
        self.slots = int(out["presence"].shape[0])             # 심볼릭 shape 였으면 첫 실행에서 확정
        return out
