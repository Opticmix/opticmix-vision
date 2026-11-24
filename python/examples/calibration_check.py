"""calibration_check — does a calibration file fit the camera? Prints the epipolar distance of features matched
between the eyes (calib.check) — numbers only, there is no pass/fail threshold.

Usage:
  python examples/calibration_check.py                              # the camera and the installed file
  python examples/calibration_check.py --file D:/S1-NNNN.json       # the camera and a file that is not installed
  python examples/calibration_check.py --pairs-dir captures/<run>   # saved PNG pairs instead of the camera
  python examples/calibration_check.py --synthetic                  # no camera, no calibration needed

Point the camera at a textured scene in view of both eyes, such as printed text or a bookshelf.
--pairs-dir reads pairs saved by the viewer (`<name>_left.png`) or by save_stereo_pairs.py (`left_<n>.png`),
taken in the 1600x800 mode; with no --file it uses the installed file.

--synthetic renders a textured plane through a made-up calibration (synthetic_equidistant, not a measured one)
and checks the frames against that calibration and against a deliberately wrong one, to show what each looks
like. Run without a camera only (generated frames); not run with the camera.

Needs: opticmix-vision.
Writes to the camera: none.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # runs without installing
for _stream in (sys.stdout, sys.stderr):                          # Hangul/symbols on a cp949 console
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from opticmix_vision import Device, DeviceConfig, DeviceError, calib  # noqa: E402
from opticmix_vision.calibration import (  # noqa: E402
    CameraIntrinsics, StereoCalibration, StereoExtrinsics, load_calibration, synthetic_equidistant,
)


def synthetic() -> int:
    from opticmix_vision.synthetic import SyntheticPlaneSource
    eye = synthetic_equidistant((800, 800), fov_deg=180.0)                       # made-up, not this camera
    right = StereoExtrinsics(R=np.eye(3), t=[-64.0, 0.0, 0.0])                   # made-up
    made_up = StereoCalibration(left=eye, right=eye, extrinsics=right)
    K = eye.K.copy()
    K[1, 2] += 12.0                                                               # principal point moved 12 px down
    wrong = StereoCalibration(left=CameraIntrinsics(K=K, D=eye.D, size=eye.size), right=eye, extrinsics=right)
    pairs = calib.read_pairs(SyntheticPlaneSource(made_up), frames=10, every=1)
    print("synthetic: generated frames of a made-up calibration, not a camera")
    for name, r in calib.compare_pairs(pairs, {"the calibration the frames were made with": made_up,
                                                "a wrong one (principal point moved 12 px down)": wrong}).items():
        print(f"\n{name}:\n{r.summary()}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", help="a calibration file (default: the installed one)")
    ap.add_argument("--pairs-dir", help="folder of saved left/right PNG pairs, instead of the camera")
    ap.add_argument("--synthetic", action="store_true", help="generated frames and a made-up calibration")
    a = ap.parse_args()
    if a.synthetic:
        return synthetic()
    try:
        c = load_calibration(a.file) if a.file else None
        if a.pairs_dir:
            r = calib.check_pairs(calib.load_pairs(a.pairs_dir), c or calib.resolve())
        else:
            with Device(DeviceConfig.weevia_s1()) as cam:
                r = calib.check(cam, c)                 # c None: cam.calibration, the installed file
    except (LookupError, ValueError, OSError, DeviceError) as exc:
        print(exc, file=sys.stderr)
        return 1
    print(r.summary())
    return 0 if r.enough else 2


if __name__ == "__main__":
    raise SystemExit(main())
