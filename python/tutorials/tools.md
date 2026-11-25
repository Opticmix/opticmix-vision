# Viewer and explorer

The package comes with two windows that open the camera and show both eyes. Use one of them first, before
writing code, to see that both eyes arrive.

| | Viewer | Explorer |
|---|---|---|
| Command | `python -m opticmix_vision.viewer` | `python -m opticmix_vision.explorer` |
| Window | OpenCV | Tkinter (standard library) |
| Controls | keys (LED, exposure, gain, auto exposure) | Settings window: every control the camera advertises |
| Saving | `s` key (PNG pair), `v` key (recording), into `--out` | camera button, into `captures/` |
| Playing a recording | `--recording FILE`, with a seek bar | — |
| Presets | `n` save, `p` choose (then the camera's defaults), `P` load; `--preset NAME` | `Save preset…` and `Load preset…` in the Settings window |
| Defaults | `p` until `defaults`, then `P` | `Reset to defaults…` in the Settings window |
| Calibration | `c` shows the installed file | — |
| Without a camera | `--synthetic` (also `--headless`), `--recording FILE` | `--synthetic` |

On Linux (experimental), the viewer and the explorer have not been run.
`--synthetic` drives the window, keys and saving with generated frames; the camera's own capture path does
not run, and the generated frames say nothing about the camera.

Writing controls is risky on this camera — see [Camera controls](../README.md#camera-controls) in the package
README. **Write once at startup.** The viewer's keys write once you stop pressing, and the explorer's sliders
write once when released, so a key held down or a drag is one write, not dozens. That does not make writes
safe.

## Viewer

```bash
python -m opticmix_vision.viewer                  # Weevia S1, found by name
python -m opticmix_vision.viewer --synthetic      # no camera
python -m opticmix_vision.viewer --recording captures/20261124_101500_000123.avi   # play a recording back
python -m opticmix_vision.viewer --lossless       # v records lossless (FFV1 .mkv)
```

### Options

From `python -m opticmix_vision.viewer --help` (options only):

```text
usage: python -m opticmix_vision.viewer [-h] [--synthetic]
                                        [--recording RECORDING] [--loop]
                                        [--lossless] [--eye-size EYE_SIZE]
                                        [--device DEVICE]
                                        [--backend {any,dshow,msmf}]
                                        [--layout {sbs,tb,plane_pack}]
                                        [--lr-order {first_is_left,first_is_right}]
                                        [--width WIDTH] [--height HEIGHT]
                                        [--fps FPS] [--fourcc FOURCC]
                                        [--out OUT] [--scale SCALE]
                                        [--headless] [--frames FRAMES]

options:
  -h, --help            show this help message and exit
  --synthetic           synthetic source, no hardware
  --recording RECORDING
                        play a recording (from v or record_video.py), no
                        camera
  --loop                with --recording: start again after the last frame
  --lossless            v records lossless (FFV1 .mkv) instead of MJPG (.avi)
  --eye-size EYE_SIZE   synthetic eye size WxH
  --device DEVICE       camera index (or path)
  --backend {any,dshow,msmf}
  --layout {sbs,tb,plane_pack}
  --lr-order {first_is_left,first_is_right}
  --width WIDTH
  --height HEIGHT
  --fps FPS
  --fourcc FOURCC
  --out OUT             folder for the s and v keys
  --scale SCALE
  --headless
  --frames FRAMES       read this many frames, then exit
```

- With no `--device`, the viewer opens `DeviceConfig.weevia_s1()`; `--width`, `--height`, `--fps`,
  `--fourcc`, `--layout` and `--lr-order` override its values.
- With `--device`, also give `--layout` and `--lr-order` — the viewer does not guess how the eyes are packed.
- `--backend` picks the OpenCV backend with or without `--device` (default `dshow`; without `--device` the
  Weevia S1 is looked up in that backend's list). With `msmf` the camera controls (keys, presets, defaults) go
  through the OpenCV fallback, which has fewer controls (see Camera controls in the package README).
  `--backend` without `--device` has been run without a camera only.
- `--eye-size` sets the size of each generated eye with `--synthetic` (default `320x200`).
- `--out` is the folder the `s` and `v` keys save into (default `captures`). `--scale` resizes the window.
- `--recording FILE` plays a recording made with `v` or `examples/record_video.py` instead of opening a camera
  (not with `--synthetic` or `--device`); `--loop` starts it again after the last frame.
- `--lossless` makes `v` record FFV1 `.mkv`, which keeps the pixel values the SDK delivered, instead of MJPG
  `.avi`, which changes them. Lossless takes longer per frame to write and may not keep up with 90 fps.
  With the opencv-python 4.12 and 4.13 wheels FFV1 writing failed now and then (one Windows PC, generated
  frames; not with 4.14 or 5.1): upgrade OpenCV or record MJPG.
- `--headless` runs the same loop without a window; with `--frames N` it stops after N frames.

### Keys

| Key | What it does |
|---|---|
| `q` or Esc | quit (a change still waiting to be written is written first) |
| `s` | save the current left and right images as PNG into `--out` |
| `v` | start or stop a recording into `--out` |
| `l` / `L` | illuminator level down / up (steps of 8) |
| `e` / `E` | exposure down / up (steps of 1) |
| `g` / `G` | gain down / up (steps of 8) |
| `a` | auto exposure on / off |
| `n` | save every control as a new preset (`--preset-name`, default `viewer_<time>`) |
| `p` | the next preset saved from this camera's model, then `defaults` (the camera's own defaults): shows what loading it would change, writes nothing |
| `P` | load what `p` chose, in one write call (`reset_to_defaults()` for `defaults`) |
| `c` | show or hide the installed calibration file (read from the file, not the camera) |
| `h` | show or hide the key help in the window |

`s`, `v`, `n`, `p`, `P`, `a`, `c`, `h` and space act once per press: held down, the key's repeats are ignored (the
same key again within 0.6 s), so pressing `v` again right after starting a recording does not stop it — wait a
moment. The window says `v ignored: same key within 0.6 s (held?) - press again after a pause` once per press.
A keyboard repeat delay set longer than 0.6 s in the OS lets one repeat through.

While you hold a key, only the target value changes; it is written once about 0.6 s after the last key
press, and the window shows `… (writes when you stop)` meanwhile. Exposure keys do nothing while auto exposure
is on — the window says `auto-exposure is on — press a to switch to manual first`.

Saved files are named `<date>_<time>_<frame index>_left.png` and `…_right.png`, as captured, with a `.json`
next to them: the negotiated mode, the camera's layout and left/right order, the device name, the installed
calibration files, every control value and the SDK version.

`v` writes the files `examples/record_video.py` writes ([tutorial 04](04-recording-and-playback.md)):
`<date>_<time>_<frame index>.avi` (`.mkv` with `--lossless`; the files open when `v` is pressed and the
recording starts with the next frame), both eyes side by side, a `.csv` of frame times
and a `.json` that also lists the LED, exposure and gain changes made while recording and the values at the
end. When the recording stops, the viewer prints `[viewer] recorded N frames to <file>`, and what to do if
frames were missed. The viewer's `v` runs alongside the preview and may not keep up with 90 fps even with
MJPG: to record every frame, use `examples/record_video.py` first (it records with no window), then a lower
`--fps` or `examples/capture_dataset_npz.py`.

A thread encodes while the window draws. A frame that finds its queue (64 frames) full is not recorded: the
REC line, the `.json` (`dropped_frames`) and the message when recording stops count it. A dropped frame is
also a gap in the frame times, so the missed estimate includes it (`estimated_missed_frames` includes
`dropped_frames` — do not add them): the message says `about N frames missed while recording (estimated from
the frame times; the N frames the write queue dropped are among them, not in addition)`. A queue does not
make lossless keep up with 90 fps. Stopping (`v`, `q`) writes the frames still queued before the
files close, so the window waits for them. The `.json` of `s` and `v` says where its control values come from
and when the viewer read them (`controls_source` `"given"`, `controls_read_at`). The writing thread has been
run on generated frames only; not run with the camera.

While a recording plays (`--recording`):

| Key | What it does |
|---|---|
| space | pause / resume; at the end, play again |
| `,` / `.` | one frame back / forward (pauses) |
| `[` / `]` | one second back / forward |
| `-` / `+` | half / double speed |

The bar under the image follows the frame shown; drag it to seek. At the end the window stays on the last
frame (`q` quits), or starts again with `--loop`.

### What the window shows

Both eyes side by side, left eye on the left, with text in the top-left corner:

- `frame N  fps X  late N  missed~N  WxH/eye` — the frame index; the frame rate (`Device.actual_fps`, or the
  rate frames reach the viewer's loop when the source does not report one); the frames that came late and the
  estimated missed frames (`Device.late_frames`, `Device.estimated_missed_frames`, when the source reports
  them); and the eye size. They are estimated from host times, not read from a device frame counter.
  `repeated N` is added once a frame's two eyes both repeated the previous image (`Device.repeated_frames`;
  run without a camera only).
- `REC <file>  N frames  missed~N since start (dropped N)  (v stops)` while recording: the frames written so
  far, those estimated missed, and, among them, those dropped because the write queue was full (a dropped frame
  leaves a gap in the frame times; `lossless` after the frame count with `--lossless`).
- `PLAY <frame>/<last>  <time>/<duration> s  <speed>x` while a recording plays, with `PAUSED` or `ENDED` and
  `loop` when they apply, and the rate frames are shown.
- `LED …  exp …  gain …  (as opened + viewer's writes)` — the illuminator level, exposure and gain as the viewer
  knows them, not a live reading: every control is
  read from the camera once when the viewer opens, then the value read back after each of its own writes (the
  `.json` of `s` and `v` has the same values); a control whose write was refused is read once again, and `n`,
  `p` and `P` read every control. A change made by another program shows when the viewer is opened again;
  `exp auto` while the viewer has turned auto exposure on (not for a recording).
- with `c`: `calibration S1-NNNN (model)  file <path> - installed file, not matched to this camera (no USB
  serial)`, one line per eye
  (`left   KB4 WxH  fx …  fy …  cx …  cy …  D k1 k2 k3 k4`) and `baseline … mm  t … mm  R … deg` — from
  the file installed with `python -m opticmix_vision.calib add <file>`; with none, what to do.
- the last two messages (a change, a save, an error), and the key help when `h` is on. These lines and the
  calibration lines wrap to the window's width.

When the viewer closes it prints one line: frames read, pairs saved and the last rate.

Recording with `v` has been run with the camera once (one PC, one camera, 2026-11-24): one recording started and stopped with `v` (392 frames, about 130 missed while recording; 0.0.5, before the writing thread), no stall error; its `.json` carries `controls_note`. Lossless recording, the `s` key's `.json`, playing a recording in the viewer, presets, the defaults and `c` have been run
on generated frames only; not run with the camera.

### Run it without a camera

Without a camera, from the `python/` folder of the repository (the last two lines record generated frames and
play the file back):

```bash
python -m opticmix_vision.viewer --synthetic --headless --frames 60
python examples/record_video.py --synthetic --seconds 1
python -m opticmix_vision.viewer --recording recording.avi --headless
```

Output with `--synthetic` (generated frames); with the camera the numbers differ.

```text
$ python -m opticmix_vision.viewer --synthetic --headless --frames 60
[viewer] frames 60 saved 0 fps 2689.2

$ python examples/record_video.py --synthetic --seconds 1
negotiated: {'width': 640, 'height': 200, 'fps': 90.0, 'fourcc': 'SYNT', 'backend_name': 'synthetic'}
wrote 96 frames (640x200, MJPG) to recording.avi, labelled 90 fps; frames arrived at 94.7 per second (host-measured); while recording: late 0, estimated missed 0 (of which dropped by the write queue 0); info in recording.json

$ python -m opticmix_vision.viewer --recording recording.avi --headless
[viewer] frames 96 saved 0 fps 2754.7
```

With `--headless`, frames are not paced, so this rate is only how fast the loop ran. Without `--headless`,
the generated frames are paced near the `--fps` value (default 90). Leave out `--headless` on the last line to
see the recording with its seek bar.

## Explorer

```bash
python -m opticmix_vision.explorer                # finds the Weevia S1 if it is connected
python -m opticmix_vision.explorer --synthetic    # no camera
```

### Options

From `python -m opticmix_vision.explorer --help`:

```text
usage: opticmix-explorer [-h] [--synthetic]

options:
  -h, --help   show this help message and exit
  --synthetic  run on generated frames, with no camera attached
```

The explorer has no keyboard shortcuts; everything is a button or a list.

### The window

- **Top bar, left:** `LIVE` (streaming), `IDLE` or `ERROR`, and the **mode** list. If the mode list cannot be
  read, the bottom bar says `Could not read the mode list (needs ffmpeg) — opening 1600×800 MJPG, the mode
  verified on the Weevia S1`.
- **Top bar, right:** the **device** list (`ID:`), which starts on the Weevia S1 if one is connected and on
  the first camera otherwise; the camera's USB details from the operating system, without opening it —
  `VID:PID · REV <USB device revision> · <link speed> · no serial` (`USB: — · no serial` when no Weevia S1 is
  found, `synthetic · no serial` with `--synthetic`); `no serial` because the camera reports no USB serial
  number, so two units cannot be told apart by name — label them physically, or open one by its USB location
  (`DeviceConfig.weevia_s1(usb_location=...)`; the port, not a serial number; picking by USB location has been
  run without a camera only, not with a camera and not with two cameras); the refresh button, which
  lists the cameras again; and the gear, which opens the Settings window.
- **Middle:** both eyes side by side, shrunk to fit by keeping every Nth pixel, without interpolation.
- **Over the video, bottom left:**
  - `MODEL` — `Weevia S1` when the device name is the Weevia S1's, otherwise the device name, or `SYNTHETIC`;
  - `FORMAT` and `RESOLUTION` — the mode the camera opened in;
  - `FRAMERATE` — measured from the frames that arrive, not the requested value;
  - `FRAMES` — the frame index;
  - `TIMESTAMP` — `host_ns`, the host's clock right after the frame was read (not a camera timestamp).
- **Bottom bar:** the folder button opens `captures/`; the camera button saves the current left and right
  images there as `<date-time>_left.png` and `_right.png`; then messages, and the package version.

Opening a camera writes no control values. The window shows the values left in the camera as they are.

### The Settings window

The gear opens one row per control the camera advertises: a slider for a number with a range, a checkbox for
on/off, a list for a choice. Controls with no effect expected on this mono sensor are in a collapsed group at
the bottom.

- A slider shows the value while you drag and writes **once when you release it** (mouse or arrow keys).
- Under a value: `applying in N s` when the guard delays a write; red text when the driver refuses it;
  orange `camera kept N` when the driver accepted it but the camera did not keep it — the control then goes
  back to the camera's value.
- `Writes this session` at the bottom counts every control write by the process.
- `Reset to defaults…` lists what differs from the device defaults and writes only after you press `Reset`.
  Resetting to defaults has not yet been run against the camera.
- `Save preset…` writes every readable control to a JSON file, starting in the preset folder; it only reads.
- `Load preset…` lists the presets saved from this camera's model (and `Other file…`), then shows what would
  change and how many writes that is, and writes only after you press `Load` — in one `set_many()` call, like
  `Reset to defaults…`. A preset from another model offers `Load anyway`. Presets have been run without a
  camera only; not run with the camera.

## If it does not work

Run `python -m opticmix_vision.doctor` and paste its output into an issue; it does not open the camera.
See [When something goes wrong](../README.md#when-something-goes-wrong) in the package README. The viewer
prints `[viewer] could not open the camera: …` with the SDK's message when it cannot open the camera; the
explorer shows the message over the video with `ERROR` — press the refresh button to look again.
