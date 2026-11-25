"""Writes python_mjpg.avi / .csv / .json next to this file: a recording the Python package's Recorder (MJPG) wrote,
which the Rust crate's test reads (rust/opticmix-vision/src/recording.rs,
a_recording_the_python_package_wrote_plays_here). Made-up frames, the same as that test's frame(i): left eye
40 + 20 i with a 250 block in its top-left 8x8, right eye 200 - 20 i, 32x16 each, 90 fps host times, Media
Foundation driver times except the last frame. The files kept here were written with opencv-python 4.14.0 on
Windows. No camera.

    python sdk/python/tests/data/recordings/make_python_mjpg.py

rust_mjpg.* (the other direction) is written by the Rust test the_rust_fixture_the_python_tests_read_is_what_the_
recorder_writes (OV_WRITE_RECORDING_FIXTURE=1).
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))            # sdk/python: this checkout's package

import numpy as np                                    # noqa: E402

from opticmix_vision import StereoFrame               # noqa: E402
from opticmix_vision.recording import Recorder        # noqa: E402
from opticmix_vision.synthetic import SyntheticStereoSource   # noqa: E402

for ext in ("avi", "csv", "json"):
    (HERE / f"python_mjpg.{ext}").unlink(missing_ok=True)
with Recorder(HERE / "python_mjpg.avi", SyntheticStereoSource(eye_size=(32, 16), fps=90.0)) as rec:
    for i in range(6):
        left = np.full((16, 32), 40 + 20 * i, np.uint8)
        left[:8, :8] = 250
        right = np.full((16, 32), 200 - 20 * i, np.uint8)
        d = 5_000_000_000 + i * 11_111_100 if i < 5 else None
        rec.write(StereoFrame(left=left, right=right, host_ns=1_000_000_000 + i * 11_111_111, index=i,
                              driver_ns=d, driver_clock="media_foundation" if d else None))
print("wrote", HERE / "python_mjpg.avi")
