"""capture_dataset_npz — collect N stereo frames into one .npz file for training or analysis.

Usage:
  python examples/capture_dataset_npz.py                              # 100 frames to stereo_dataset.npz
  python examples/capture_dataset_npz.py --frames 300 --out take1.npz
  python examples/capture_dataset_npz.py --synthetic --frames 20      # no camera: generated frames

The file holds three arrays:
  left     (N, H, W) uint8   left eye, as captured (no calibration or rectification)
  right    (N, H, W) uint8   right eye
  host_ns  (N,)      int64   host monotonic clock right after each frame was read (not a camera timestamp)

Frames are kept in memory until the end: at 800x800 per eye that is 1.28 MB a frame, so 100 frames
take about 128 MB. Load the file with numpy.load(); torch_dataloader.py reads it into PyTorch.

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

from opticmix_vision import Device, DeviceConfig, DeviceError  # noqa: E402


def open_camera(synthetic: bool):
    """The Weevia S1, found by name — or, with --synthetic, generated frames (not measured data)."""
    if synthetic:
        from opticmix_vision.synthetic import SyntheticStereoSource
        return SyntheticStereoSource(realtime=True)
    return Device(DeviceConfig.weevia_s1()).open()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--synthetic", action="store_true", help="generated frames instead of a camera")
    ap.add_argument("--frames", type=int, default=100, help="frames to collect (default 100)")
    ap.add_argument("--out", type=Path, default=Path("stereo_dataset.npz"))
    a = ap.parse_args()
    if a.frames < 1:
        ap.error("--frames needs at least 1")

    try:
        with open_camera(a.synthetic) as cam:
            print("negotiated:", cam.mode.to_dict())
            f = cam.read()
            h, w = f.left.shape
            left = np.empty((a.frames, h, w), np.uint8)
            right = np.empty((a.frames, h, w), np.uint8)
            host_ns = np.empty(a.frames, np.int64)
            for i in range(a.frames):
                if i:
                    f = cam.read()
                left[i], right[i], host_ns[i] = f.left, f.right, f.host_ns     # copies out of the frame
    except DeviceError as exc:
        print(f"Camera error: {exc}", file=sys.stderr)
        return 2

    np.savez_compressed(a.out, left=left, right=right, host_ns=host_ns)
    span_s = (host_ns[-1] - host_ns[0]) / 1e9
    print(f"saved {a.frames} frames of {w}x{h} per eye to {a.out} "
          f"({span_s:.2f} s from first to last frame, host-measured)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
