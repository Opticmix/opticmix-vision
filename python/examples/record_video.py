"""record_video — record both eyes into one video file, side by side (left | right).

Usage:
  python examples/record_video.py                                   # 5 s to recording.avi (MJPG)
  python examples/record_video.py --seconds 30 --out run1.avi
  python examples/record_video.py --lossless                        # recording_lossless.mkv, FFV1: the SDK's pixel values kept
  python examples/record_video.py --lossless --fps 30               # ask the camera for a lower rate
  python examples/record_video.py --codec mp4v --out run1.mp4       # needs a codec your OpenCV build has
  python examples/record_video.py --synthetic --seconds 1           # no camera: generated frames
  python examples/record_video.py --backend msmf                    # Windows: fills driver_ns
  python examples/record_video.py --queue-frames 64                 # encode on a thread (Recorder queue_frames)

Writes three files (opticmix_vision.recording.Recorder):
  recording.avi   grayscale, left eye in the left half; the file's frame rate is the rate the camera negotiated
  recording.csv   frame, host_ns, driver_ns, driver_clock — when each frame arrived (host monotonic clock, not a
                  camera timestamp), and the capture driver's time stamp and its clock, empty with DirectShow
                  (the Windows default) and --synthetic (run without a camera only; not run with the camera)
  recording.json  the negotiated mode, the camera's layout and left/right order, device name, installed
                  calibration files, every control value when recording started and ended, the SDK version,
                  codec, whether it is compressed, the frame count and the late / missed frame estimates

The file's frame rate is only a label. If encoding is slower than the camera, frames are missed and the
video plays too fast; the CSV is the record of real timing, and the late / missed counts printed at the end
are estimated from it. MJPG changes pixel values; --lossless (FFV1) keeps the values the SDK delivered, but
takes longer per frame and may not keep up with 90 fps: when frames were missed, the last line says what to
do (MJPG, a lower --fps, or capture_dataset_npz.py). With the opencv-python 4.12 and 4.13 wheels FFV1 writing
failed now and then (one Windows PC, generated frames; not with 4.14 or 5.1): upgrade OpenCV or record MJPG.
play_recording.py plays the result back.
--queue-frames N (default 0: encode before reading the next frame) encodes on a thread through a queue of N
frames, as the viewer's v does; a frame that finds the queue full is dropped, and the last line counts it
(the estimated missed frames include the dropped ones; do not add them). --queue-frames: run on generated
frames only; not run with the camera.
The .csv and .json take the video's name without its extension, so --lossless writes recording_lossless.*
by default, apart from recording.*; a --out whose name another recording (or a .npz) already has
is refused rather than overwrite that recording's .csv and .json.
The lossless file's pixel values: generated frames only; not run with the camera.

--backend msmf (Windows) opens the Weevia S1 through Media Foundation, found by name in its list (--device N
names the index yourself), so the CSV's driver_ns is filled; the controls then go through OpenCV's fallback (no
ranges or defaults). Through OpenCV's Media Foundation path the driver times came evenly 11.11 ms apart on one
PC with one camera (once, 2026-11-24) while images repeated; these times do not show whether a new image
arrived. --backend msmf: run without a camera only; not run with the camera.

Needs: opencv-python (installed with opticmix-vision).
Writes to the camera: none.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # runs without installing
for _stream in (sys.stdout, sys.stderr):                          # Hangul/symbols on a cp949 console
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from opticmix_vision import Device, DeviceConfig, DeviceError  # noqa: E402
from opticmix_vision.recording import LOSSLESS_CODEC, Recorder  # noqa: E402


def open_camera(synthetic: bool, fps: float | None, backend: str | None = None, device: int | None = None):
    """The Weevia S1, found by name (or at --device) — or, with --synthetic, generated frames (not measured data)."""
    if synthetic:
        from opticmix_vision.synthetic import SyntheticStereoSource
        return SyntheticStereoSource(realtime=True, fps=fps or 90.0)
    given = {k: v for k, v in (("backend", backend), ("device", device)) if v is not None}
    if fps:
        given["fps"] = fps
    return Device(DeviceConfig.weevia_s1(**given)).open()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--synthetic", action="store_true", help="generated frames instead of a camera")
    ap.add_argument("--seconds", type=float, default=5.0, help="how long to record (default 5)")
    ap.add_argument("--out", type=Path, default=None, help="default recording.avi (recording_lossless.mkv with --lossless)")
    ap.add_argument("--codec", default=None, help="four-character code (default MJPG, with a .avi file)")
    ap.add_argument("--lossless", action="store_true", help=f"{LOSSLESS_CODEC}: keeps the pixel values the SDK delivered")
    ap.add_argument("--fps", type=float, default=None, help="frame rate to ask the camera for (default: 90)")
    ap.add_argument("--backend", choices=("dshow", "msmf", "v4l2"),
                    help="OpenCV capture backend (default: the Weevia S1 default for this OS); msmf fills driver_ns")
    ap.add_argument("--queue-frames", type=int, default=0, metavar="N",
                    help="encode on a thread through a queue of N frames; a frame that finds it full is dropped"
                         " and counted (default 0: no thread)")
    ap.add_argument("--device", type=int,
                    help="camera index in that backend's list, instead of finding the Weevia S1 by name")
    a = ap.parse_args()
    if a.lossless and a.codec:
        ap.error("--lossless picks the codec; leave out --codec")
    codec = LOSSLESS_CODEC if a.lossless else (a.codec or "MJPG")
    if len(codec) != 4:
        ap.error("--codec takes four characters, e.g. MJPG or mp4v")
    if a.queue_frames < 0:
        ap.error("--queue-frames takes 0 or a number of frames")
    out = a.out or Path("recording_lossless.mkv" if a.lossless else "recording.avi")

    try:
        with open_camera(a.synthetic, a.fps, a.backend, a.device) as cam, \
                Recorder(out, cam, codec=codec, queue_frames=a.queue_frames) as rec:
            print("negotiated:", cam.mode.to_dict())
            t_end = time.perf_counter() + a.seconds
            while True:
                rec.write(cam.read())
                if time.perf_counter() >= t_end:
                    break
    except DeviceError as exc:
        print(f"Camera error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:              # the video writer could not be opened, or another recording has the name
        print(exc, file=sys.stderr)
        return 3
    r = rec.info["recording"]
    rate = (r["frames"] - 1) / r["duration_s"] if r["frames"] > 1 and r["duration_s"] > 0 else 0.0
    w, h = r["eye_size"]
    print(f"wrote {r['frames']} frames ({2 * w}x{h}, {codec}{'' if rec.compressed else ', lossless'}) to {out}, "
          f"labelled {rec.fps:g} fps; frames arrived at {rate:.1f} per second (host-measured); "
          f"while recording: late {rec.late_frames}, estimated missed {rec.estimated_missed_frames} "
          f"(of which dropped by the write queue {rec.dropped_frames}); "
          f"info in {rec.info_path}")
    if rec.missed_advice:
        print("Frames were missed while recording.", rec.missed_advice)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
