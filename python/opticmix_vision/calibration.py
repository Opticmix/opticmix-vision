"""Calibration file (JSON): per-eye KB4 fisheye intrinsics and the left-to-right extrinsics.

Model: Kannala-Brandt, 4th order (KB4) = `cv2.fisheye`.
  r_d = θ (1 + k1 θ² + k2 θ⁴ + k3 θ⁶ + k4 θ⁸),  θ = angle of incidence from the optical axis
  pixel = K · [r_d · cosφ, r_d · sinφ, 1]ᵀ

File schema (schema_version 1):
{
  "schema": "opticmix_vision.stereo_calibration",
  "schema_version": 1,
  "units": "mm",
  "device": { "model": "...", "serial": "..." },          # strings only, optional
  "left":  { "model": "KB4", "K": [[fx,0,cx],[0,fy,cy],[0,0,1]], "D": [k1,k2,k3,k4], "size": [w,h] },
  "right": { ... same ... },
  "extrinsics": { "R": [[..3x3..]], "t": [tx,ty,tz], "convention": "p_right = R @ p_left + t" },
  "meta": { ... free form (reprojection RMS, tool, date, ...) ... }
}

Extrinsics convention: as OpenCV `stereoCalibrate`. R, t take **left-camera coordinates to right-camera
coordinates** (p_right = R·p_left + t). Camera axes X right, Y down, Z forward along the optical axis, mm.
The baseline is |t|.

Checks: an unknown key is an error (a misspelt key must not be ignored silently). NaN/inf are neither
written nor read. KB4 coefficients that fold inside the field of view (not monotonic) are refused — with
folded coefficients project/unproject are silently wrong. `cv2.fisheye.calibrate` can produce such
coefficients for ultra-wide lenses.

The checks, their messages and the KB4 fold test live in the Rust core (`opticmix-geom`); this module keeps
the public types (dataclasses, numpy arrays) and the JSON written form. Without the compiled extension
(`_core`, e.g. an editable install) using this module raises `ControlsUnavailable` asking you to install it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from opticmix_vision import _native

__all__ = [
    "SCHEMA_NAME", "SCHEMA_VERSION", "DEFAULT_MAX_THETA_DEG",
    "CalibrationError", "CameraIntrinsics", "StereoExtrinsics", "StereoCalibration",
    "load_calibration", "save_calibration", "kb4_is_injective", "synthetic_equidistant",
]

SCHEMA_NAME = "opticmix_vision.stereo_calibration"
SCHEMA_VERSION = 1

# Half of the 185° (vendor spec) lens field; the field of view has not been measured.
DEFAULT_MAX_THETA_DEG = 92.5


class CalibrationError(ValueError):
    """The calibration file breaks the schema or the geometry checks."""


def kb4_is_injective(D: np.ndarray, max_theta_rad: float, n: int = 1024) -> tuple[bool, float | None]:
    """Whether dθ_d/dθ = 1 + 3k1θ² + 5k2θ⁴ + 7k3θ⁶ + 9k4θ⁸ is positive on [0, max] (n points, np.linspace).

    Returns (True, None) when monotonic, (False, first folding angle [rad]) otherwise.
    """
    k = [float(x) for x in np.asarray(D, dtype=np.float64).reshape(-1)]
    if len(k) != 4:
        raise ValueError(f"D must have 4 coefficients: {len(k)}")
    ok, fold = _native.core().kb4_is_injective(k, float(max_theta_rad), int(n))
    return bool(ok), fold


def _jsonable(o: Any) -> Any:
    """numpy arrays and numbers → JSON values (serialization for the checks)."""
    if hasattr(o, "tolist"):
        return o.tolist()
    raise TypeError(f"{type(o).__name__} is not JSON-serializable")


def _dumps(d: Any) -> str:
    """dict → JSON for the core checks (NaN kept as a value so the core reports it with the same message)."""
    return json.dumps(d, default=_jsonable, allow_nan=True)


# ---------------------------------------------------------------------------
# intrinsics
# ---------------------------------------------------------------------------

@dataclass(frozen=True, eq=False)
class CameraIntrinsics:
    """One eye's KB4 intrinsics. K: (3,3) pixels, D: (k1,k2,k3,k4), size: (width, height)."""

    K: np.ndarray
    D: np.ndarray
    size: tuple[int, int]
    model: str = "KB4"

    def __post_init__(self) -> None:
        K = np.array(self.K, dtype=np.float64)
        D = np.array(self.D, dtype=np.float64).reshape(-1)
        _native.core().check_intrinsics_shapes(list(K.shape), int(D.size))
        try:
            size = (int(self.size[0]), int(self.size[1]))
        except (TypeError, ValueError, IndexError):
            raise CalibrationError(f"size must be [width, height]: {self.size!r}") from None
        object.__setattr__(self, "K", K)
        object.__setattr__(self, "D", D)
        object.__setattr__(self, "size", size)
        problems = self.validate()
        if problems:
            raise CalibrationError("CameraIntrinsics: " + "; ".join(problems))

    def validate(self, max_theta_deg: float = DEFAULT_MAX_THETA_DEG) -> list[str]:
        return list(_native.core().intrinsics_problems(
            self.K.ravel().tolist(), self.D.tolist(), (int(self.size[0]), int(self.size[1])), str(self.model),
            float(max_theta_deg)))

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
        _native.core().intrinsics_validate_json(_dumps(dict(d)), where)
        return cls(K=np.asarray(d["K"]), D=np.asarray(d["D"]), size=tuple(d["size"]), model=str(d["model"]))


# ---------------------------------------------------------------------------
# extrinsics
# ---------------------------------------------------------------------------

CONVENTION = "p_right = R @ p_left + t"


@dataclass(frozen=True, eq=False)
class StereoExtrinsics:
    """Left → right camera transform. p_right = R·p_left + t. t in mm."""

    R: np.ndarray
    t: np.ndarray

    def __post_init__(self) -> None:
        R = np.array(self.R, dtype=np.float64)
        t = np.array(self.t, dtype=np.float64).reshape(-1)
        _native.core().check_extrinsics(list(R.shape), R.ravel().tolist(), t.tolist())
        object.__setattr__(self, "R", R)
        object.__setattr__(self, "t", t)

    @property
    def baseline_mm(self) -> float:
        return float(np.linalg.norm(self.t))

    def to_dict(self) -> dict[str, Any]:
        return {"R": self.R.tolist(), "t": self.t.tolist(), "convention": CONVENTION}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "StereoExtrinsics":
        _native.core().extrinsics_validate_json(_dumps(dict(d)))
        return cls(R=np.asarray(d["R"]), t=np.asarray(d["t"]))


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
            raise CalibrationError(f"units must be 'mm': {self.units!r}")
        if not all(isinstance(k, str) and isinstance(v, str) for k, v in self.device.items()):
            raise CalibrationError("the device block allows string→string entries only")
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
        v = dict(d)
        if "meta" in v and isinstance(v["meta"], Mapping):
            v["meta"] = {}                          # meta is free form — not checked (values JSON cannot hold are accepted too)
        _native.core().calib_validate_json(_dumps(v), True)
        return cls(
            left=CameraIntrinsics.from_dict(d["left"], "left"),
            right=CameraIntrinsics.from_dict(d["right"], "right"),
            extrinsics=StereoExtrinsics.from_dict(d["extrinsics"]),
            device=dict(d.get("device", {})),
            meta=dict(d.get("meta", {})),
            units=str(d.get("units", "mm")),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False, allow_nan=False) + "\n"

    @classmethod
    def from_json(cls, text: str) -> "StereoCalibration":
        _native.core().calib_validate_json(text, False)      # NaN/Infinity tokens and schema violations are caught here
        return cls.from_dict(json.loads(text))


def load_calibration(path: str | Path) -> StereoCalibration:
    return StereoCalibration.from_json(Path(path).read_text(encoding="utf-8"))


def save_calibration(calib: StereoCalibration, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(calib.to_json(), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Synthetic intrinsics for development and tests — not measured values
# ---------------------------------------------------------------------------

def synthetic_equidistant(size: tuple[int, int], fov_deg: float) -> CameraIntrinsics:
    """An ideal equidistant (D=0) fisheye K. **For tests and first guesses** — not a measured calibration.

    The image circle is taken as inscribed in the short side: f = (min(w,h)/2) / (fov/2 [rad]); principal
    point at the image centre.
    """
    try:
        w, h = int(size[0]), int(size[1])
        fov = float(fov_deg)
    except (TypeError, ValueError, IndexError):
        raise CalibrationError(f"size={size!r} fov_deg={fov_deg!r}") from None
    K, D, sz = _native.core().synthetic_equidistant((w, h), fov)
    return CameraIntrinsics(K=np.array(K, dtype=np.float64).reshape(3, 3), D=np.array(D, dtype=np.float64), size=sz)
