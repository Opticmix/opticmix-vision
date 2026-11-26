"""export_recording — write the frames of a recording out as left/right PNG pairs, with the recorded frame times.

Usage:
  python examples/export_recording.py recording.avi                          # every frame, to recording_frames/
  python examples/export_recording.py run1.avi --out run1_pairs --every 10   # every 10th frame
  python examples/export_recording.py run1.avi --start 300 --count 50        # 50 frames from frame 300
  python examples/export_recording.py run1.avi --npz                         # also frames.npz (left, right, host_ns)
  python examples/export_recording.py --synthetic                            # no camera: records 30 generated frames
                                                                             # to synthetic.mkv (lossless), exports them

It reads the recording through Device(DeviceConfig.recording(path)) with the recorded times
(Playback.recorded_time) and writes into --out (default: the video's name + _frames), a folder that must be new or
empty — nothing is overwritten:
  left_000123.png, right_000123.png   one pair per exported frame (123 = the frame's number in the recording), the
                                       names save_stereo_pairs.py uses
  pairs.csv                            index, host_ns, driver_ns, driver_clock, left, right — save_stereo_pairs.py's
                                       columns, with the times recorded in the recording's .csv
  capture_info.json                    a copy of the recording's .json (mode, control values, SDK version, codec)
  frames.npz, frames.json              with --npz: capture_dataset_npz.py's format (left, right, host_ns), kept in
                                       memory until the end (1.28 MB a frame at 800x800 per eye; the example
                                       prints how much before it starts)

A recording without its .csv still plays, but has no recorded times: host_ns in pairs.csv is then frame number /
the file's frame rate, and driver_ns and driver_clock are empty; the example prints a line saying so.

The frames to export are counted from the recording (one per row of its .csv, from --start, every --every-th, at
most --count); the last line gives the number exported and the recording's frame count, and the example exits 1
when it wrote another number than it counted.

An MJPG recording does not hold the camera's pixel values: the PNGs hold what it decodes to. A lossless recording
(record_video.py --lossless) keeps the values the SDK delivered, and the PNGs keep them too (generated frames only).
Run with generated recordings, and with a camera recording once on Linux, from source before release. On Windows
with one camera (one PC, 2-level USB hub, 2026-11-25, 0.0.7): from MJPG and FFV1 camera recordings it wrote 800x800
8-bit left/right images; covering one lens at a time (left and right as seen from behind the camera, looking
where it looks) removed the scene from the SDK eye of the same name. Not run with a camera: Linux left/right; a
second unit.

Needs: opencv-python (installed with opticmix-vision).
Writes to the camera: no camera is opened.
"""

from __future__ import annotations

import argparse
import csv
import shutil
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # runs without installing
for _stream in (sys.stdout, sys.stderr):                          # Hangul/symbols on a cp949 console
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from opticmix_vision import Device, DeviceConfig, DeviceError, EndOfRecording  # noqa: E402
from opticmix_vision.recording import LOSSLESS_CODEC, Recorder, write_capture_info  # noqa: E402

SYNTHETIC_FRAMES = 30


def save_png(path: Path, img: np.ndarray) -> None:
    import cv2
    ok, buf = cv2.imencode(".png", img)    # cv2.imwrite fails silently on non-ASCII paths on Windows
    if not ok:
        raise OSError(f"PNG encoding failed for {path}")
    buf.tofile(str(path))


def record_synthetic(video: Path) -> None:
    """SYNTHETIC_FRAMES generated frames (not measured data), recorded losslessly to `video`."""
    from opticmix_vision.synthetic import SyntheticStereoSource
    with SyntheticStereoSource(realtime=True) as src, Recorder(video, src, codec=LOSSLESS_CODEC) as rec:
        for _ in range(SYNTHETIC_FRAMES):
            rec.write(src.read())
    print(f"recorded {rec.frames} generated frames ({LOSSLESS_CODEC}, lossless) to {video}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video", type=Path, nargs="?", help="a recording (record_video.py, or v in the viewer)")
    ap.add_argument("--out", type=Path, help="folder to write into, new or empty (default: <video name>_frames)")
    ap.add_argument("--every", type=int, default=1, help="export every Nth frame from --start (default 1: all)")
    ap.add_argument("--start", type=int, default=0, help="first frame number (default 0)")
    ap.add_argument("--count", type=int, default=0, help="stop after this many pairs (default 0: to the end)")
    ap.add_argument("--npz", action="store_true", help="also frames.npz and frames.json (capture_dataset_npz.py's format)")
    ap.add_argument("--synthetic", action="store_true",
                    help=f"first record {SYNTHETIC_FRAMES} generated frames to VIDEO (default synthetic.mkv); no camera")
    a = ap.parse_args()
    if a.every < 1 or a.start < 0 or a.count < 0:
        ap.error("--every needs at least 1; --start and --count take 0 or more")
    if a.synthetic:
        a.video = a.video or Path("synthetic.mkv")
        if a.video.exists():
            ap.error(f"{a.video} exists: --synthetic records a new file, give another name")
    elif a.video is None:
        ap.error("give the recording, or --synthetic")
    out = a.out or a.video.with_name(a.video.stem + "_frames")
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        ap.error(f"{out} is not empty: give a new folder with --out (nothing is overwritten)")

    saved, left, right, host_ns = 0, [], [], []
    try:
        if a.synthetic:
            record_synthetic(a.video)
        with Device(DeviceConfig.recording(a.video)) as cam:
            pb = cam.playback
            pb.recorded_time = True                      # host_ns, driver_ns, driver_clock as the CSV has them
            n = pb.frame_count
            r = (cam.recording_info or {}).get("recording", {})
            print(f"{a.video}: {n} frames"
                  + (f", codec {r['codec']}, compressed {r['compressed']}" if r else " (no .json next to the file)"))
            if not a.video.with_suffix(".csv").is_file():
                print("no .csv next to the file: host_ns in pairs.csv is frame number / frame rate, not recorded "
                      "times; driver_ns and driver_clock are empty")
            if a.start >= n:
                print(f"--start {a.start}: the recording has {n} frames (0..{n - 1})", file=sys.stderr)
                return 2
            want = len(range(a.start, n, a.every))
            want = min(want, a.count) if a.count else want
            if a.npz:
                w, h = cam.mode.width // 2, cam.mode.height          # one eye of the side-by-side recording
                print(f"--npz: keeps {want} frames in memory until the end, about {want * 2 * w * h / 1e6:.1f} MB "
                      f"({w}x{h} per eye)")
            if a.start:
                pb.seek(a.start)
            out.mkdir(parents=True, exist_ok=True)
            if cam.recording_info is not None:
                shutil.copyfile(a.video.with_suffix(".json"), out / "capture_info.json")
            with open(out / "pairs.csv", "w", newline="", encoding="utf-8") as fh:
                rows = csv.writer(fh)
                rows.writerow(["index", "host_ns", "driver_ns", "driver_clock", "left", "right"])
                while saved < want:
                    try:
                        f = cam.read()
                    except EndOfRecording:
                        break
                    if (f.index - a.start) % a.every:
                        continue
                    names = (f"left_{f.index:06d}.png", f"right_{f.index:06d}.png")
                    save_png(out / names[0], f.left)
                    save_png(out / names[1], f.right)
                    rows.writerow([f.index, f.host_ns, f.driver_ns, f.driver_clock, *names])   # None: empty
                    saved += 1
                    if a.npz:
                        left.append(f.left)
                        right.append(f.right)
                        host_ns.append(f.host_ns)
            if a.npz and saved:
                np.savez_compressed(out / "frames.npz", left=np.stack(left), right=np.stack(right),
                                    host_ns=np.array(host_ns, np.int64))
                h, w = left[0].shape[:2]
                write_capture_info(out / "frames.json", cam, npz={
                    "file": "frames.npz", "frames": saved, "eye_size": [w, h], "late_frames": cam.late_frames,
                    "estimated_missed_frames": cam.estimated_missed_frames})
    except DeviceError as exc:
        print(f"Could not read {a.video}: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:              # --synthetic: the video writer could not be opened, or the name is taken
        print(exc, file=sys.stderr)
        return 3
    print(f"exported {saved} of {n} frames from {a.video} to {out}")
    if saved != want:
        print(f"counted {want} frames to export from the recording, wrote {saved}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
