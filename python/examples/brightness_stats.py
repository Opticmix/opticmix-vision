"""brightness_stats — per-eye brightness over N frames: mean, min, max and the share of saturated pixels.

Usage:
  python examples/brightness_stats.py                          # 90 frames into brightness.csv
  python examples/brightness_stats.py --frames 900 --out run.csv
  python examples/brightness_stats.py --synthetic              # no camera: generated frames

One CSV row per frame: index, host_ns, and for each eye the mean, min and max pixel value and the fraction
of pixels at 255 (saturated, 8-bit). A summary over all frames is printed at the end. Useful to compare
lighting or exposure settings; this example only reads — it does not change a setting.

Needs: opticmix-vision.
Writes to the camera: none.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # runs without installing
for _stream in (sys.stdout, sys.stderr):                          # Hangul/symbols on a cp949 console
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from opticmix_vision import Device, DeviceConfig, DeviceError  # noqa: E402

EYES = ("left", "right")
STATS = ("mean", "min", "max", "saturated")


def open_camera(synthetic: bool):
    """The Weevia S1, found by name — or, with --synthetic, generated frames (not measured data)."""
    if synthetic:
        from opticmix_vision.synthetic import SyntheticStereoSource
        return SyntheticStereoSource(realtime=True)
    return Device(DeviceConfig.weevia_s1()).open()


def eye_stats(img: np.ndarray) -> list[float]:
    return [float(img.mean()), int(img.min()), int(img.max()), float(np.count_nonzero(img == 255) / img.size)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--synthetic", action="store_true", help="generated frames instead of a camera")
    ap.add_argument("--frames", type=int, default=90, help="frames to measure (default 90)")
    ap.add_argument("--out", default="brightness.csv", help="CSV file (default brightness.csv)")
    a = ap.parse_args()
    if a.frames < 1:
        ap.error("--frames needs at least 1")

    rows = []
    try:
        with open_camera(a.synthetic) as cam:
            print("negotiated:", cam.mode.to_dict())
            for _ in range(a.frames):
                f = cam.read()
                rows.append([f.index, f.host_ns, *eye_stats(f.left), *eye_stats(f.right)])
    except DeviceError as exc:
        print(f"Camera error: {exc}", file=sys.stderr)
        return 2

    with open(a.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["index", "host_ns", *(f"{e}_{s}" for e in EYES for s in STATS)])
        w.writerows(rows)

    v = np.array([r[2:] for r in rows], dtype=np.float64)
    print(f"{len(rows)} frames -> {a.out}")
    for i, eye in enumerate(EYES):
        mean, lo, hi, sat = (v[:, 4 * i + k] for k in range(4))
        print(f"{eye:5s}: mean {mean.mean():.1f} (frames {mean.min():.1f}..{mean.max():.1f})  "
              f"min {int(lo.min())}  max {int(hi.max())}  saturated {100 * sat.mean():.3f} % of pixels")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
