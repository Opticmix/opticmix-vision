"""save_stereo_pairs — save left/right PNG pairs and a CSV of when each frame arrived.

Usage:
  python examples/save_stereo_pairs.py                         # window; space saves a pair, q quits
  python examples/save_stereo_pairs.py --every 30 --count 10   # save every 30th frame, stop after 10 pairs
  python examples/save_stereo_pairs.py --synthetic --headless --every 5 --count 3   # no camera, no window
  python examples/save_stereo_pairs.py --backend msmf          # Windows: fills driver_ns in pairs.csv

Each run writes into a new folder, captures/<date-time>/:
  left_000123.png, right_000123.png   one pair per saved frame (123 = frame index in this run)
  pairs.csv                            index, host_ns, driver_ns, driver_clock, left, right
  capture_info.json                    the negotiated mode, the camera's layout and left/right order, every
                                       control value when the run started, the SDK version

host_ns is the host's monotonic clock (time.perf_counter_ns) right after the frame was read —
not a timestamp from the camera. driver_ns and driver_clock are the capture driver's time stamp and its clock
(StereoFrame.driver_ns): empty with DirectShow, the Windows default, and with --synthetic. --backend msmf
(Windows) opens the Weevia S1 through Media Foundation, found by name in its list (--device N names the index
yourself), so driver_ns is filled; the controls then go through OpenCV's fallback (no ranges or defaults).
Through OpenCV's Media Foundation path the driver times came evenly 11.11 ms apart on one PC with one camera
(once, 2026-11-24) while images repeated; these times do not show whether a new image arrived. Run without a
camera only; not run with the camera.

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
from opticmix_vision.recording import write_capture_info  # noqa: E402

WINDOW = "opticmix_vision save pairs (space save, q quit)"


def open_camera(synthetic: bool, backend: str | None = None, device: int | None = None):
    """The Weevia S1, found by name (or at --device) — or, with --synthetic, generated frames (not measured data)."""
    if synthetic:
        from opticmix_vision.synthetic import SyntheticStereoSource
        return SyntheticStereoSource(realtime=True)
    given = {k: v for k, v in (("backend", backend), ("device", device)) if v is not None}
    return Device(DeviceConfig.weevia_s1(**given)).open()


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
    ap.add_argument("--backend", choices=("dshow", "msmf", "v4l2"),
                    help="OpenCV capture backend (default: the Weevia S1 default for this OS); msmf fills driver_ns")
    ap.add_argument("--device", type=int,
                    help="camera index in that backend's list, instead of finding the Weevia S1 by name")
    a = ap.parse_args()
    if a.headless and a.every <= 0:
        ap.error("--headless has no space key: give --every N")

    import cv2
    out = a.out / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    saved = 0
    try:
        with open_camera(a.synthetic, a.backend, a.device) as cam, open(out / "pairs.csv", "w", newline="", encoding="utf-8") as fh:
            print("negotiated:", cam.mode.to_dict())
            write_capture_info(out / "capture_info.json", cam, pairs="pairs.csv")
            rows = csv.writer(fh)
            rows.writerow(["index", "host_ns", "driver_ns", "driver_clock", "left", "right"])
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
                    rows.writerow([f.index, f.host_ns, f.driver_ns, f.driver_clock, *names])   # None: empty
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
