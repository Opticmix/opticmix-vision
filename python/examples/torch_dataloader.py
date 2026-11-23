"""torch_dataloader — read a .npz from capture_dataset_npz.py with a PyTorch Dataset and DataLoader.

Usage:
  python examples/capture_dataset_npz.py --frames 100        # first, make stereo_dataset.npz
  python examples/torch_dataloader.py                         # then read it in batches
  python examples/torch_dataloader.py take1.npz --batch-size 16

Each item is one frame: a (2, H, W) float tensor in 0..1 (channel 0 = left eye, 1 = right eye) and
its host_ns. Batches come out as (B, 2, H, W). No camera is used.

Needs: PyTorch, which opticmix-vision does not install (pip install torch).
Writes to the camera: none (no camera is opened).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

for _stream in (sys.stdout, sys.stderr):                          # Hangul/symbols on a cp949 console
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

try:
    import torch
    from torch.utils.data import DataLoader, Dataset
except ImportError:
    sys.exit("This example needs PyTorch, which opticmix-vision does not install. "
             "Install it with: pip install torch  (see https://pytorch.org for other builds)")


class StereoNpz(Dataset):
    """Frames from one .npz file. The whole file is loaded into memory once."""

    def __init__(self, path: Path) -> None:
        with np.load(path) as z:
            self.left, self.right, self.host_ns = z["left"], z["right"], z["host_ns"]
        if not (len(self.left) == len(self.right) == len(self.host_ns)):
            raise ValueError(f"{path}: left, right and host_ns have different lengths")

    def __len__(self) -> int:
        return len(self.host_ns)

    def __getitem__(self, i: int) -> tuple[torch.Tensor, int]:
        pair = np.stack([self.left[i], self.right[i]])            # (2, H, W) uint8
        return torch.from_numpy(pair).float().div_(255.0), int(self.host_ns[i])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("npz", type=Path, nargs="?", default=Path("stereo_dataset.npz"))
    ap.add_argument("--batch-size", type=int, default=8)
    a = ap.parse_args()
    if not a.npz.exists():
        print(f"{a.npz} not found — make one with capture_dataset_npz.py first", file=sys.stderr)
        return 2

    ds = StereoNpz(a.npz)
    # num_workers=0 keeps this simple. With workers on Windows, the DataLoader must be created
    # under `if __name__ == "__main__":`, as it is here.
    loader = DataLoader(ds, batch_size=a.batch_size, shuffle=True, num_workers=0)
    seen = 0
    for i, (stereo, host_ns) in enumerate(loader):
        if i == 0:
            print(f"batch: {tuple(stereo.shape)} {stereo.dtype}, range {stereo.min():.3f}..{stereo.max():.3f}; "
                  f"host_ns {tuple(host_ns.shape)} {host_ns.dtype}")
        seen += len(stereo)
    print(f"{len(ds)} frames in {len(loader)} batches; one pass read {seen}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
