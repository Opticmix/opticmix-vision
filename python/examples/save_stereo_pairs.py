"""save_stereo_pairs — save left/right PNG pairs and a CSV of when each frame arrived.

Usage:
  python examples/save_stereo_pairs.py                         # window; space saves a pair, q quits
  python examples/save_stereo_pairs.py --every 30 --count 10   # save every 30th frame, stop after 10 pairs
  python examples/save_stereo_pairs.py --synthetic --headless --every 5 --count 3   # no camera, no window

Each run writes into a new folder, captures/<date-time>/:
  left_000123.png, right_000123.png   one pair per saved frame (123 = frame index in this run)
  pairs.csv                            index, host_ns, left, right

host_ns is the host's monotonic clock (time.perf_counter_ns) right after the frame was read —
not a timestamp from the camera.

Needs: opencv-python (installed with opticmix-vision).
Writes to the camera: none.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # runs without installing
for _stream in (sys.stdout, sys.stderr):                          # Hangul/symbols on a cp949 console
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from opticmix_vision import Device, DeviceConfig, DeviceError  # noqa: E402

WINDOW = "opticmix_vision save pairs (space save, q quit)"


def open_camera(synthetic: bool):
    """The Weevia S1, found by name — or, with --synthetic, generated frames (not measured data)."""
    if synthetic:
        from opticmix_vision.synthetic import SyntheticStereoSource
        return SyntheticStereoSource(realtime=True)
    return Device(DeviceConfig.weevia_s1()).open()


def save_png(path: Path, img: np.ndarray) -> None:
    import cv2
    ok, buf = cv2.imencode(".png", img)    # cv2.imwrite fails silently on non-ASCII paths on Windows
    if not ok:
        raise OSError(f"PNG encoding failed for {path}")
    buf.tofile(str(path))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--synthetic", action="store_true", help="generated frames instead of a camera")
    ap.add_argument("--out", type=Path, default=Path("captures"), help="parent folder (default: captures)")
    ap.add_argument("--every", type=int, default=0, help="save every Nth frame (default 0: only on space)")
    ap.add_argument("--count", type=int, default=0, help="stop after this many pairs (default 0: until q)")
    ap.add_argument("--headless", action="store_true", help="no window; needs --every")
    a = ap.parse_args()
    if a.headless and a.every <= 0:
        ap.error("--headless has no space key: give --every N")

    import cv2
    out = a.out / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    saved = 0
    try:
        with open_camera(a.synthetic) as cam, open(out / "pairs.csv", "w", newline="", encoding="utf-8") as fh:
            print("negotiated:", cam.mode.to_dict())
            rows = csv.writer(fh)
            rows.writerow(["index", "host_ns", "left", "right"])
            while not a.count or saved < a.count:
                f = cam.read()
                key = -1
                if not a.headless:
                    cv2.imshow(WINDOW, np.hstack([f.left, f.right]))
                    key = cv2.waitKey(1) & 0xFF
                    if key == ord("q"):
                        break
                if key == ord(" ") or (a.every > 0 and f.index % a.every == 0):
                    names = (f"left_{f.index:06d}.png", f"right_{f.index:06d}.png")
                    save_png(out / names[0], f.left)
                    save_png(out / names[1], f.right)
                    rows.writerow([f.index, f.host_ns, *names])
                    saved += 1
    except DeviceError as exc:
        print(f"Camera error: {exc}", file=sys.stderr)
        return 2
    finally:
        if not a.headless:
            cv2.destroyAllWindows()
    print(f"saved {saved} pairs in {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
