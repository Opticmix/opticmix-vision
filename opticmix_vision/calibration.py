"""캘리브레이션 파일 (JSON) — 눈별 KB4 어안 내부 파라미터 + 좌→우 외부 파라미터.

모델: Kannala-Brandt 4차 (KB4) = `cv2.fisheye`.
  r_d = θ (1 + k1 θ² + k2 θ⁴ + k3 θ⁶ + k4 θ⁸),  θ = 광축과 이루는 입사각
  픽셀 = K · [r_d · cosφ, r_d · sinφ, 1]ᵀ

파일 스키마 (schema_version 1):
{
  "schema": "opticmix_vision.stereo_calibration",
  "schema_version": 1,
  "units": "mm",
  "device": { "model": "...", "serial": "..." },          # 문자열만, 선택
  "left":  { "model": "KB4", "K": [[fx,0,cx],[0,fy,cy],[0,0,1]], "D": [k1,k2,k3,k4], "size": [w,h] },
  "right": { ... 동일 ... },
  "extrinsics": { "R": [[..3x3..]], "t": [tx,ty,tz], "convention": "p_right = R @ p_left + t" },
  "meta": { ... 자유 형식 (재투영 RMS, 도구, 날짜 등) ... }
}

눈별 블록(model/K/D/size)은 내부 캡처 파이프라인의 `CameraIntrinsics` 와 같은 키를 쓴다 —
같은 파일을 양쪽에서 읽을 수 있게 하기 위해서다.

외부 파라미터 규약: OpenCV `stereoCalibrate` 와 동일. R, t 는 **왼쪽 카메라 좌표를 오른쪽 카메라
좌표로** 옮긴다 (p_right = R·p_left + t). 카메라 좌표계는 X=우, Y=하, Z=광축 전방, 단위 mm.
베이스라인은 |t| 다.

검증 원칙: 모르는 키는 에러다 (오타 난 키가 조용히 무시되면 안 된다). NaN/inf 는 쓰지도 읽지도 않는다.
KB4 계수가 화각 안에서 접히면(비단조) 거부한다 — 접힌 계수는 project/unproject 가 오차 신호 없이
조용히 틀린다. `cv2.fisheye.calibrate` 가 초광각 렌즈에서 그런 계수를 내는 일이 있다.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import numpy as np

__all__ = [
    "SCHEMA_NAME", "SCHEMA_VERSION", "DEFAULT_MAX_THETA_DEG",
    "CalibrationError", "CameraIntrinsics", "StereoExtrinsics", "StereoCalibration",
    "load_calibration", "save_calibration", "kb4_is_injective", "synthetic_equidistant",
]

SCHEMA_NAME = "opticmix_vision.stereo_calibration"
SCHEMA_VERSION = 1

# KB4 단조성 검사 상한. 공칭 화각급 185° 의 절반. ❓ 실측 FOV 는 미검증 — 확정되면 그 절반으로 맞춘다.
DEFAULT_MAX_THETA_DEG = 92.5


class CalibrationError(ValueError):
    """캘리브레이션 파일 스키마/기하 위반."""


def _reject_unknown(d: Mapping[str, Any], allowed: tuple[str, ...], where: str) -> None:
    unknown = sorted(set(d) - set(allowed))
    if unknown:
        raise CalibrationError(f"{where}: 모르는 키 {unknown} (허용: {list(allowed)})")


def _require(d: Mapping[str, Any], key: str, where: str) -> Any:
    if key not in d:
        raise CalibrationError(f"{where}: 필수 키 {key!r} 없음")
    return d[key]


def _finite(a: np.ndarray, where: str) -> np.ndarray:
    if not np.all(np.isfinite(a)):
        raise CalibrationError(f"{where}: NaN/inf 가 있다")
    return a


def kb4_is_injective(D: np.ndarray, max_theta_rad: float, n: int = 1024) -> tuple[bool, float | None]:
    """θ_d(θ) 의 도함수 1 + 3k1θ² + 5k2θ⁴ + 7k3θ⁶ + 9k4θ⁸ 가 [0, max] 에서 전부 양수인지.

    반환 (True, None) 단조 | (False, 처음 접힌 각도[rad]).
    """
    k1, k2, k3, k4 = (float(x) for x in np.asarray(D, dtype=np.float64).reshape(-1))
    th = np.linspace(0.0, float(max_theta_rad), int(n))
    t2 = th * th
    d = 1.0 + 3 * k1 * t2 + 5 * k2 * t2**2 + 7 * k3 * t2**3 + 9 * k4 * t2**4
    bad = np.nonzero(d <= 0.0)[0]
    if bad.size == 0:
        return True, None
    return False, float(th[bad[0]])


# ---------------------------------------------------------------------------
# intrinsics
# ---------------------------------------------------------------------------

@dataclass(frozen=True, eq=False)
class CameraIntrinsics:
    """한 눈의 KB4 내부 파라미터. K: (3,3) 픽셀, D: (k1,k2,k3,k4), size: (width, height)."""

    K: np.ndarray
    D: np.ndarray
    size: tuple[int, int]
    model: str = "KB4"

    def __post_init__(self) -> None:
        K = np.array(self.K, dtype=np.float64)
        D = np.array(self.D, dtype=np.float64).reshape(-1)
        if K.shape != (3, 3):
            raise CalibrationError(f"K 는 (3,3): {K.shape}")
        if D.shape != (4,):
            raise CalibrationError(f"D 는 KB4 4계수 (k1..k4): {D.shape}")
        try:
            size = (int(self.size[0]), int(self.size[1]))
        except (TypeError, ValueError, IndexError):
            raise CalibrationError(f"size 는 [width, height]: {self.size!r}") from None
        object.__setattr__(self, "K", K)
        object.__setattr__(self, "D", D)
        object.__setattr__(self, "size", size)
        problems = self.validate()
        if problems:
            raise CalibrationError("CameraIntrinsics: " + "; ".join(problems))

    def validate(self, max_theta_deg: float = DEFAULT_MAX_THETA_DEG) -> list[str]:
        errs: list[str] = []
        if self.model != "KB4":
            errs.append(f"model {self.model!r}: KB4 만 지원")
        if not np.all(np.isfinite(self.K)):
            errs.append("K 에 NaN/inf")
        elif self.K[0, 0] <= 0 or self.K[1, 1] <= 0:
            errs.append(f"fx/fy 는 양수: fx={self.K[0, 0]}, fy={self.K[1, 1]}")
        if not np.all(np.isfinite(self.D)):
            errs.append("D 에 NaN/inf")
        else:
            ok, fold = kb4_is_injective(self.D, math.radians(max_theta_deg))
            if not ok:
                errs.append(f"KB4 계수가 화각 안에서 접힌다(비단조): 약 {math.degrees(fold or 0.0):.2f}° "
                            f"(검사 상한 {max_theta_deg:g}°)")
        if self.size[0] <= 0 or self.size[1] <= 0:
            errs.append(f"size 는 양수: {self.size}")
        return errs

    @property
    def fx(self) -> float: return float(self.K[0, 0])
    @property
    def fy(self) -> float: return float(self.K[1, 1])
    @property
    def cx(self) -> float: return float(self.K[0, 2])
    @property
    def cy(self) -> float: return float(self.K[1, 2])

    def to_dict(self) -> dict[str, Any]:
        return {"model": self.model, "K": self.K.tolist(), "D": self.D.tolist(), "size": list(self.size)}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any], where: str = "intrinsics") -> "CameraIntrinsics":
        _reject_unknown(d, ("model", "K", "D", "size"), where)
        return cls(K=np.asarray(_require(d, "K", where)), D=np.asarray(_require(d, "D", where)),
                   size=tuple(_require(d, "size", where)), model=str(_require(d, "model", where)))


# ---------------------------------------------------------------------------
# extrinsics
# ---------------------------------------------------------------------------

CONVENTION = "p_right = R @ p_left + t"


@dataclass(frozen=True, eq=False)
class StereoExtrinsics:
    """왼쪽 → 오른쪽 카메라 변환. p_right = R·p_left + t. t 단위 mm."""

    R: np.ndarray
    t: np.ndarray

    def __post_init__(self) -> None:
        R = np.array(self.R, dtype=np.float64)
        t = np.array(self.t, dtype=np.float64).reshape(-1)
        if R.shape != (3, 3):
            raise CalibrationError(f"R 는 (3,3): {R.shape}")
        if t.shape != (3,):
            raise CalibrationError(f"t 는 (3,): {t.shape}")
        _finite(R, "R"); _finite(t, "t")
        if not np.allclose(R.T @ R, np.eye(3), atol=1e-6) or not math.isclose(float(np.linalg.det(R)), 1.0, abs_tol=1e-6):
            raise CalibrationError("R 가 회전행렬이 아니다 (RᵀR≠I 또는 det≠+1)")
        if float(np.linalg.norm(t)) <= 0.0:
            raise CalibrationError("t 가 0 벡터다 (베이스라인 0)")
        object.__setattr__(self, "R", R)
        object.__setattr__(self, "t", t)

    @property
    def baseline_mm(self) -> float:
        return float(np.linalg.norm(self.t))

    def to_dict(self) -> dict[str, Any]:
        return {"R": self.R.tolist(), "t": self.t.tolist(), "convention": CONVENTION}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "StereoExtrinsics":
        _reject_unknown(d, ("R", "t", "convention"), "extrinsics")
        conv = d.get("convention", CONVENTION)
        if conv != CONVENTION:
            raise CalibrationError(f"extrinsics.convention {conv!r} ≠ {CONVENTION!r}")
        return cls(R=np.asarray(_require(d, "R", "extrinsics")), t=np.asarray(_require(d, "t", "extrinsics")))


# ---------------------------------------------------------------------------
# stereo calibration file
# ---------------------------------------------------------------------------

@dataclass(frozen=True, eq=False)
class StereoCalibration:
    left: CameraIntrinsics
    right: CameraIntrinsics
    extrinsics: StereoExtrinsics
    device: dict[str, str] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)
    units: str = "mm"

    def __post_init__(self) -> None:
        if self.units != "mm":
            raise CalibrationError(f"units 는 'mm' 로 강제한다: {self.units!r}")
        if not all(isinstance(k, str) and isinstance(v, str) for k, v in self.device.items()):
            raise CalibrationError("device 블록은 문자열→문자열 만 허용")
        object.__setattr__(self, "device", dict(self.device))
        object.__setattr__(self, "meta", dict(self.meta))

    @property
    def baseline_mm(self) -> float:
        return self.extrinsics.baseline_mm

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA_NAME,
            "schema_version": SCHEMA_VERSION,
            "units": self.units,
            "device": dict(self.device),
            "left": self.left.to_dict(),
            "right": self.right.to_dict(),
            "extrinsics": self.extrinsics.to_dict(),
            "meta": dict(self.meta),
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "StereoCalibration":
        _reject_unknown(d, ("schema", "schema_version", "units", "device", "left", "right", "extrinsics", "meta"),
                        "calibration")
        if _require(d, "schema", "calibration") != SCHEMA_NAME:
            raise CalibrationError(f"schema {d['schema']!r} ≠ {SCHEMA_NAME!r}")
        ver = _require(d, "schema_version", "calibration")
        if ver != SCHEMA_VERSION:
            raise CalibrationError(f"schema_version {ver!r} 지원 안 함 (이 SDK: {SCHEMA_VERSION})")
        return cls(
            left=CameraIntrinsics.from_dict(_require(d, "left", "calibration"), "left"),
            right=CameraIntrinsics.from_dict(_require(d, "right", "calibration"), "right"),
            extrinsics=StereoExtrinsics.from_dict(_require(d, "extrinsics", "calibration")),
            device=dict(d.get("device", {})),
            meta=dict(d.get("meta", {})),
            units=str(d.get("units", "mm")),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False, allow_nan=False) + "\n"

    @classmethod
    def from_json(cls, text: str) -> "StereoCalibration":
        def _no_nan(x: str) -> Any:
            raise CalibrationError(f"JSON 에 비표준 숫자 {x!r} (NaN/Infinity 금지)")
        return cls.from_dict(json.loads(text, parse_constant=_no_nan))


def load_calibration(path: str | Path) -> StereoCalibration:
    return StereoCalibration.from_json(Path(path).read_text(encoding="utf-8"))


def save_calibration(calib: StereoCalibration, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(calib.to_json(), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# 개발/테스트용 합성 내부 파라미터 — 측정값이 아니다
# ---------------------------------------------------------------------------

def synthetic_equidistant(size: tuple[int, int], fov_deg: float) -> CameraIntrinsics:
    """이상적 등거리(equidistant, D=0) 어안 K 를 만든다. **테스트·초기 추정용** — 실측 캘리브를 대체하지 않는다.

    이미지 서클이 짧은 변에 내접한다고 두고 f = (min(w,h)/2) / (fov/2 [rad]). 주점은 영상 중심.
    """
    w, h = int(size[0]), int(size[1])
    if w <= 0 or h <= 0 or not (0.0 < float(fov_deg) < 360.0):
        raise CalibrationError(f"size={size!r} fov_deg={fov_deg!r}")
    f = (min(w, h) / 2.0) / math.radians(float(fov_deg) / 2.0)
    K = np.array([[f, 0.0, (w - 1) / 2.0], [0.0, f, (h - 1) / 2.0], [0.0, 0.0, 1.0]])
    return CameraIntrinsics(K=K, D=np.zeros(4), size=(w, h))
