"""play_recording — read a recording back through Device, as if it were the camera.

Usage:
  python examples/play_recording.py recording.avi                 # show both eyes as fast as they decode; q or Esc quits
  python examples/play_recording.py recording.avi --real-time     # at the recorded pace (the CSV's frame times)
  python examples/play_recording.py recording.avi --speed 0.5     # half speed (implies --real-time)
  python examples/play_recording.py recording.avi --start 120     # from frame 120 (or --start-seconds 2.5)
  python examples/play_recording.py recording.avi --loop --frames 1000   # start over after the last frame
  python examples/play_recording.py recording.avi --headless      # no window: just count the frames

The same loop you write for the camera works on the file: `cam.read()` gives `left` and `right`, and after the
last frame `read()` raises `EndOfRecording`. `cam.playback` seeks, sets the speed, loops and pauses;
`cam.recording_info` is the JSON saved with the recording (mode, control values, codec, frame count).

A frame's `index` is its number in the file. `host_ns` is when the frame was read back, unless
--recorded-time asks for the time it was recorded (from the CSV). At the end it prints the late / missed frame
estimates twice: over the whole recording (`cam.playback`) and over the frames read (`cam`), both from the
recorded frame times. A compressed (MJPG) recording does not
have the camera's pixel values; the lossless file (record_video.py --lossless) keeps the pixel values the SDK
delivered (generated frames only; not run with the camera).
Seek, speed and loop have been run on generated frames only; not run with the camera.
Run once on a recording made with the camera (443 MJPG frames; one PC, one camera, 2026-11-24).

Needs: opencv-python (installed with opticmix-vision).
Writes to the camera: no camera is opened.
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

from opticmix_vision import Device, DeviceConfig, DeviceError, EndOfRecording  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video", type=Path, help="a recording (record_video.py, or v in the viewer)")
    ap.add_argument("--headless", action="store_true", help="no window; read to the end and print the count")
    ap.add_argument("--start", type=int, default=None, help="first frame number")
    ap.add_argument("--start-seconds", type=float, default=None, help="start this many seconds after the first frame")
    ap.add_argument("--real-time", action="store_true", help="wait so frames come at the recorded intervals")
    ap.add_argument("--speed", type=float, default=None, help="playback speed for --real-time (2 = twice as fast)")
    ap.add_argument("--loop", action="store_true", help="start again after the last frame")
    ap.add_argument("--frames", type=int, default=None, help="stop after this many frames")
    ap.add_argument("--recorded-time", action="store_true", help="host_ns (and driver_ns) = the recorded times from the CSV")
    a = ap.parse_args()
    if a.start is not None and a.start_seconds is not None:
        ap.error("give --start or --start-seconds, not both")
    if a.loop and a.headless and a.frames is None:
        ap.error("--loop with --headless never ends: give --frames")
    n = 0
    try:
        with Device(DeviceConfig.recording(a.video)) as cam:
            pb = cam.playback
            print("recorded mode:", cam.mode.to_dict())
            r = (cam.recording_info or {}).get("recording", {})
            print(f"{pb.frame_count} frames, {pb.duration_s:.2f} s"
                  + (f", codec {r['codec']}, compressed {r['compressed']}" if r else " (no .json next to the file)"))
            if a.start is not None or a.start_seconds is not None:
                print("start at frame", pb.seek(a.start, seconds=a.start_seconds))
            pb.real_time = a.real_time or a.speed is not None
            if a.speed is not None:
                pb.set_speed(a.speed)
            pb.loop = a.loop
            pb.recorded_time = a.recorded_time
            first = last = None
            try:
                while a.frames is None or n < a.frames:
                    f = cam.read()
                    n += 1
                    first = f if first is None else first
                    last = f
                    if not a.headless:
                        import cv2
                        cv2.imshow("play_recording (left | right)", np.hstack([f.left, f.right]))
                        if cv2.waitKey(1) in (27, ord("q")):
                            break
            except EndOfRecording:
                pass
            late, missed = cam.late_frames, cam.estimated_missed_frames
            whole = (pb.late_frames, pb.estimated_missed_frames)
    except DeviceError as exc:
        print(f"Could not play {a.video}: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:                     # a bad --speed
        print(exc, file=sys.stderr)
        return 2
    print(f"read {n} frames from {a.video}")
    print(f"whole recording: late {whole[0]}, estimated missed {whole[1]} (from the recorded frame times)")
    if last is not None:
        print(f"frames read {first.index}..{last.index}: late {late}, estimated missed {missed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
