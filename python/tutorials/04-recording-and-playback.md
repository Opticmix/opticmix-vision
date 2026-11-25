# 04 · Recording and playback

Examples: [`record_video.py`](../examples/record_video.py), [`play_recording.py`](../examples/play_recording.py)

## What you will do

Record both eyes side by side into one video file, with a CSV of frame times and a JSON of what was
capturing; record a lossless file; then read the files back through `Device` with the same loop you use for
the camera — from the start, from a given frame or time, at the recorded pace or faster, and in a loop.

## Before you start

- [02 · Live view and frame rate](02-live-view-and-frame-rate.md) runs on your setup.
- Run the commands from the `python/` folder of the repository — see the [tutorials README](README.md) for
  which version to check out and which `python` to use. The recordings are written into the folder you
  run them from.
- `record_video.py` (MJPG, 5 s) has been run with the camera once (one PC, one camera, 2026-11-24): it wrote
  443 frames with the CSV and JSON (estimated missed 22), and `play_recording.py` read the 443 frames back.
  Lossless recording's pixel values, and playback with seeking, speed and looping: generated frames only
  (`record_video.py --synthetic`); not run with the camera. `--queue-frames` (encoding on a thread, as the
  viewer's `v` does): run on generated frames only; not run with the camera.
- The Rust crate writes and plays the same MJPG `.avi` recordings (`cargo run --release --example record_video`,
  `--example play_recording`); it neither writes nor plays lossless FFV1. Rust recording and playback: generated
  frames only; not run with the camera.

## Run it

With the camera:

```bash
python examples/record_video.py                              # 5 s to recording.avi (MJPG)
python examples/record_video.py --seconds 30 --out run1.avi
python examples/record_video.py --lossless --out run2.mkv    # FFV1: keeps the pixel values the SDK delivered
python examples/record_video.py --queue-frames 64           # encode on a thread; the last line counts dropped frames
python examples/play_recording.py run1.avi                   # window; q or Esc quits
python examples/play_recording.py run1.avi --real-time       # at the recorded pace (the CSV's frame times)
```

Without a camera:

```bash
python examples/record_video.py --synthetic --seconds 1
python examples/play_recording.py recording.avi --headless
python examples/play_recording.py recording.avi --headless --start-seconds 0.5 --speed 2
python examples/play_recording.py recording.avi --headless --loop --frames 150
python examples/record_video.py --synthetic --seconds 1 --lossless --out lossless.mkv
python examples/play_recording.py lossless.mkv --headless
```

The viewer records too (`v`) and plays a recording with a seek bar (`--recording`): see
[Viewer and explorer](tools.md).

## What you should see

Output with `--synthetic` (generated frames); with the camera the numbers differ.

```text
$ python examples/record_video.py --synthetic --seconds 1
negotiated: {'width': 640, 'height': 200, 'fps': 90.0, 'fourcc': 'SYNT', 'backend_name': 'synthetic'}
wrote 96 frames (640x200, MJPG) to recording.avi, labelled 90 fps; frames arrived at 94.7 per second (host-measured); while recording: late 0, estimated missed 0 (of which dropped by the write queue 0); info in recording.json

$ python examples/play_recording.py recording.avi --headless
recorded mode: {'width': 640, 'height': 200, 'fps': 90.0, 'fourcc': 'SYNT', 'backend_name': 'synthetic'}
96 frames, 1.00 s, codec MJPG, compressed True
read 96 frames from recording.avi
whole recording: late 0, estimated missed 0 (from the recorded frame times)
frames read 0..95: late 0, estimated missed 0

$ python examples/play_recording.py recording.avi --headless --start-seconds 0.5 --speed 2
recorded mode: {'width': 640, 'height': 200, 'fps': 90.0, 'fourcc': 'SYNT', 'backend_name': 'synthetic'}
96 frames, 1.00 s, codec MJPG, compressed True
start at frame 50
read 46 frames from recording.avi
whole recording: late 0, estimated missed 0 (from the recorded frame times)
frames read 50..95: late 0, estimated missed 0

$ python examples/play_recording.py recording.avi --headless --loop --frames 150
recorded mode: {'width': 640, 'height': 200, 'fps': 90.0, 'fourcc': 'SYNT', 'backend_name': 'synthetic'}
96 frames, 1.00 s, codec MJPG, compressed True
read 150 frames from recording.avi
whole recording: late 0, estimated missed 0 (from the recorded frame times)
frames read 0..53: late 0, estimated missed 0

$ python examples/record_video.py --synthetic --seconds 1 --lossless --out lossless.mkv
negotiated: {'width': 640, 'height': 200, 'fps': 90.0, 'fourcc': 'SYNT', 'backend_name': 'synthetic'}
wrote 95 frames (640x200, FFV1, lossless) to lossless.mkv, labelled 90 fps; frames arrived at 93.9 per second (host-measured); while recording: late 0, estimated missed 0 (of which dropped by the write queue 0); info in lossless.json

$ python examples/play_recording.py lossless.mkv --headless
recorded mode: {'width': 640, 'height': 200, 'fps': 90.0, 'fourcc': 'SYNT', 'backend_name': 'synthetic'}
95 frames, 1.00 s, codec FFV1, compressed False
read 95 frames from lossless.mkv
whole recording: late 0, estimated missed 0 (from the recorded frame times)
frames read 0..94: late 0, estimated missed 0
```

- `record_video.py` writes three files with the same name: `recording.avi` (grayscale, left eye in the left
  half), `recording.csv` (`frame`, `host_ns`: when each frame arrived; `driver_ns`, `driver_clock`: the capture
  driver's time stamp, empty with DirectShow and `--synthetic`; run without a camera only, not run with the
  camera) and `recording.json` (what was
  capturing; see below). The `.csv` and `.json` take the video's name without its extension, so a second
  recording needs another name, not only another extension: `--out lossless.mkv` here (`--lossless` alone
  writes `recording_lossless.mkv`). A name another recording already has with another extension
  (`--out recording.mkv` next to `recording.avi`) is refused rather than overwrite its `.csv` and `.json`.
- `driver_ns` in the CSV: on Windows fill it with `record_video.py --backend msmf`; the controls then go through OpenCV's fallback (no ranges or defaults).
  Through OpenCV's Media Foundation path the driver times came evenly 11.11 ms apart on one PC with one camera (once, 2026-11-24) while images repeated; these times do not show whether a new image arrived. `--backend msmf` has been run without a camera only;
  not run with the camera.
- `late` and `estimated missed` are estimated from the CSV's frame times at the negotiated rate. The files
  are opened before the first frame is read, so the time to open them is not a gap. The generated source then
  sends at once the frames that came due meanwhile, which is why a second holds a few more than 90 frames.
  When frames were missed, a last line of `record_video.py` suggests what to do; with the camera, a late
  interval is a gap in the recording.
- `recorded mode:` is the mode the recording was made in (from the JSON), not the mode of the file.
- `--start-seconds 0.5` starts at the first frame recorded at or after 0.5 s; `--speed 2` plays at the
  recorded pace, twice as fast. `--loop` starts again at frame 0 after the last frame, so 150 frames are read
  from a 92-frame file; `frames read` gives the numbers of the first and the last frame read.
- `compressed True` (MJPG): the pixel values differ from what the camera sent. `compressed False` (FFV1):
  the file keeps the pixel values the SDK delivered (generated frames only; not run with the camera).

## How it works

`record_video.py` gives every frame to a `Recorder`, which writes the video, the CSV and the JSON:

```python
with open_camera(a.synthetic, a.fps, a.backend, a.device) as cam, \
        Recorder(out, cam, codec=codec, queue_frames=a.queue_frames) as rec:
    print("negotiated:", cam.mode.to_dict())
    t_end = time.perf_counter() + a.seconds
    while True:
        rec.write(cam.read())
        if time.perf_counter() >= t_end:
            break
```

`play_recording.py` opens the file with `DeviceConfig.recording(path)` and sets up `cam.playback` before
reading; `read()` raises `EndOfRecording` after the last frame (unless it loops):

```python
with Device(DeviceConfig.recording(a.video)) as cam:
    pb = cam.playback
    ...
    if a.start is not None or a.start_seconds is not None:
        print("start at frame", pb.seek(a.start, seconds=a.start_seconds))
    pb.real_time = a.real_time or a.speed is not None
    if a.speed is not None:
        pb.set_speed(a.speed)
    pb.loop = a.loop
    ...
    try:
        while a.frames is None or n < a.frames:
            f = cam.read()
            ...
    except EndOfRecording:
        pass
```

- **The JSON** (`recording.capture_info`) is taken when the `Recorder` is created: the negotiated mode, the
  camera's layout and left/right order, the device name, the installed calibration files with their sha256,
  every control value, the SDK version and the time. When the recording closes it adds a `recording` part:
  codec, `compressed`, frame count, duration, pauses (`breaks`), the control changes made while recording, the
  control values at the end, and the late / missed estimates. `recording.write_capture_info(path, source)`
  writes the same information next to files of your own. It reads controls and never writes them.
- **Lossless:** `Recorder(path, source, codec=LOSSLESS_CODEC)` (`"FFV1"`, `.mkv` or `.avi`). Each frame takes
  longer to write than MJPG, so it may not keep up with 90 fps; `rec.estimated_missed_frames` and the JSON
  count the frames missed. To record every frame, record MJPG, ask the camera for a lower `--fps`, or keep
  frames in memory with `capture_dataset_npz.py` ([tutorial 03](03-saving-frames.md)). FFV1 needs an OpenCV
  build with the FFV1 encoder.
- **Playback** (`cam.playback`, a `recording.Playback`): `seek(frame)` or `seek(seconds=…)`, `position`,
  `frame_count` and `duration_s`; `real_time` waits so frames come at the recorded intervals divided by
  `set_speed(…)`; `loop`; `pause()` and `resume()`; `status` is `"playing"`, `"paused"` or `"ended"` (there is
  no callback).
- `cam.mode` is the mode the recording was made in and `cam.recording_info` is its JSON. A frame's `index` is
  its number in the file. `host_ns` is when the frame was read back; `pb.recorded_time = True`
  (`play_recording.py --recorded-time`) gives the time it was recorded, from the CSV, and the driver time
  recorded with it (`driver_ns`, `driver_clock`); a CSV of only `frame, host_ns` (earlier versions) still plays.
- The file's frame rate is only a label; the CSV is the record of real timing.

## If it does not work

- **"OpenCV could not open a video writer for codec …"** (exit code 3) — check that the extension suits the
  codec (MJPG with `.avi`, FFV1 with `.mkv` or `.avi`, `mp4v` with `.mp4`), that your OpenCV build has the
  codec, and that the folder exists and is writable.
- **"Frames were missed while recording"** — if it was lossless, record MJPG (leave out `--lossless`); ask a
  lower rate with `--fps`, or keep frames in memory with `capture_dataset_npz.py`.
- **"OpenCV … failed to write frame … (FFV1)"** — seen now and then with the opencv-python 4.12 and 4.13
  wheels (one Windows PC, generated frames), not with 4.14 or 5.1; upgrade OpenCV or record MJPG.
- **"… has the same name: recording to … would overwrite …"** (exit code 3) — record to another name with
  `--out`, or move the other file and the files next to it to another folder.
- **"….replaced is left from recording to … when the process stopped"** (exit code 3) — a recording to that
  name was not closed. The files without `.replaced` are that unfinished recording, the `.replaced` files the
  one that was there before. To keep the earlier one, delete or move the unfinished files first, then take
  `.replaced` off the names; to keep the unfinished one, delete the `.replaced` files.
- **"Could not play …: no such recording"** — check the file name you passed.
- **A recording that was not closed** (the process stopped while recording) — an MJPG `.avi` cannot be
  opened, and the error says so; an FFV1 `.mkv` plays up to where it was written.
- Problems opening the camera: [When something goes wrong](../README.md#when-something-goes-wrong) in the
  package README. Reading a recording needs no camera: see
  [Without a camera](../README.md#without-a-camera).

## Next

[05 · Camera controls](05-camera-controls.md): read every control the camera advertises, and write exposure
and gain once.
