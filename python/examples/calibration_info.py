"""calibration_info — print the installed calibration: K, D and eye size per eye, R, t and the baseline (mm).

Usage:
  python examples/calibration_info.py               # the file installed with `python -m opticmix_vision.calib add`
  python examples/calibration_info.py --synthetic   # no calibration needed: a made-up one in a temporary folder

Reads the calibration with opticmix_vision.calib.resolve() — the same file `cam.calibration` returns, which
also checks that the eye size matches the mode the camera opened in. It does not open the camera.
With no file installed, or more than one, it prints the SDK's message as is and exits with code 1.

The SDK cannot check that a file belongs to the camera you opened — the camera does not send a USB serial
number, so another unit's file loads without an error.

--synthetic builds an ideal fisheye (synthetic_equidistant, made-up values, not a measured calibration),
installs it into a temporary folder and reads it back from there; your calibration folder is not touched.

Needs: opticmix-vision.
Writes to the camera: none.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # runs without installing
for _stream in (sys.stdout, sys.stderr):                          # Hangul/symbols on a cp949 console
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from opticmix_vision import calib  # noqa: E402
from opticmix_vision.calibration import (  # noqa: E402
    StereoCalibration, StereoExtrinsics, save_calibration, synthetic_equidistant,
)


def install_synthetic(folder: Path) -> None:
    """Points the calibration store at `folder` and installs a made-up calibration there."""
    calib.store_dir = lambda: folder / "store"           # every calib function now uses the temporary folder
    eye = synthetic_equidistant((800, 800), fov_deg=180.0)              # made-up, not this camera
    c = StereoCalibration(left=eye, right=eye,
                          extrinsics=StereoExtrinsics(R=np.eye(3), t=[-50.0, 0.0, 0.0]),   # made-up
                          device={"model": "synthetic", "serial": "S1-0000"})
    calib.add(str(save_calibration(c, folder / "S1-0000.json")))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--synthetic", action="store_true", help="a made-up calibration in a temporary folder")
    a = ap.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        if a.synthetic:
            install_synthetic(Path(tmp))
            print("synthetic: made-up values, not a camera")
        print(f"calibration folder: {calib.store_dir()}")
        try:
            c = calib.resolve()
        except calib.CalibLookupError as exc:
            print(exc, file=sys.stderr)
            return 1

    np.set_printoptions(precision=4, suppress=True)
    print(f"device: {c.device}")
    for name, eye in (("left", c.left), ("right", c.right)):
        print(f"{name}: model {eye.model}, eye size {eye.size[0]}x{eye.size[1]}")
        print(f"  K =\n{eye.K}")
        print(f"  D (k1..k4) = {eye.D}")
    print(f"R (left -> right) =\n{c.extrinsics.R}")
    print(f"t (mm) = {c.extrinsics.t}")
    print(f"baseline: {c.baseline_mm:.2f} mm")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
