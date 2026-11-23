"""threaded_capture — read frames on one thread, process them on another, always the latest frame.

Usage:
  python examples/threaded_capture.py                          # 300 processed frames, 20 ms of work each
  python examples/threaded_capture.py --work-ms 5              # lighter processing
  python examples/threaded_capture.py --synthetic              # no camera: generated frames, paced in software

The reader thread calls read() in a loop and puts each frame into a queue that holds one frame; when the
processing thread has not taken the previous one yet, the old frame is thrown away. Processing therefore
never falls behind the camera — it skips frames instead. At the end the example prints how many frames
were processed and how many were skipped (gaps in frame.index).

--work-ms stands in for your own processing (here: a sleep plus the mean of each eye).

Needs: opticmix-vision.
Writes to the camera: none.
"""

from __future__ import annotations

import argparse
import queue
import sys
import threading
import time
from pathlib import Path

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


def reader(cam, latest: queue.Queue, stop: threading.Event, error: list) -> None:
    """Reads until stop is set. Keeps only the newest frame in `latest`."""
    try:
        while not stop.is_set():
            frame = cam.read()
            try:
                latest.get_nowait()          # drop the frame nobody has taken yet
            except queue.Empty:
                pass
            latest.put_nowait(frame)         # one producer: the slot is free now
    except Exception as exc:                 # noqa: BLE001 — handed to the main thread, which reports it
        error.append(exc)
        stop.set()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--synthetic", action="store_true", help="generated frames instead of a camera")
    ap.add_argument("--frames", type=int, default=300, help="frames to process (default 300)")
    ap.add_argument("--work-ms", type=float, default=20.0, help="simulated processing time per frame (default 20)")
    a = ap.parse_args()
    if a.frames < 1:
        ap.error("--frames needs at least 1")

    latest: queue.Queue = queue.Queue(maxsize=1)
    stop = threading.Event()
    error: list = []
    processed = skipped = 0
    try:
        with open_camera(a.synthetic) as cam:
            print("negotiated:", cam.mode.to_dict())
            t = threading.Thread(target=reader, args=(cam, latest, stop, error), daemon=True)
            t.start()
            last_index = None
            try:
                while processed < a.frames and not stop.is_set():
                    try:
                        f = latest.get(timeout=1.0)
                    except queue.Empty:
                        continue
                    if last_index is not None:
                        skipped += f.index - last_index - 1
                    last_index = f.index
                    time.sleep(a.work_ms / 1000.0)                 # your processing goes here
                    _ = (float(f.left.mean()), float(f.right.mean()))
                    processed += 1
            finally:
                stop.set()
                t.join(timeout=5)       # the reader must be done with cam before it closes
    except DeviceError as exc:
        print(f"Camera error: {exc}", file=sys.stderr)
        return 2
    if error:
        print(f"Camera error: {error[0]}", file=sys.stderr)
        return 2

    print(f"processed {processed} frames, skipped {skipped} "
          f"(frames read while the previous one was still being processed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
