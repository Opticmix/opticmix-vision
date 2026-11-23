# Examples

Each example is one file. Install the wheel first, then run the file:

```bash
python examples/live_preview.py              # Weevia S1, found by name
python examples/live_preview.py --synthetic  # no camera
python examples/live_preview.py --help       # every option, and what the example writes to the camera
```

| Example | What it does | Writes to the camera | Without a camera |
|---|---|---|---|
| `quickstart.py` | Opens the camera, prints the negotiated mode, shows both eyes; `s` saves a pair | none | no |
| `live_preview.py` | Both eyes side by side in a window, with the frame rate the loop receives | none | `--synthetic` |
| `save_stereo_pairs.py` | Left/right PNG pairs on the space key or every Nth frame, plus `pairs.csv` (index, host_ns) | none | `--synthetic` |
| `record_video.py` | Both eyes side by side into one video file (MJPG `.avi` by default) and a CSV of frame times | none | `--synthetic` |
| `play_recording.py` | Reads that video file back through `Device` as if it were the camera, and shows or counts the frames; run on generated recordings only | no camera | needs no camera |
| `list_controls.py` | Every control the camera advertises: current value, range, default, how far its effect is verified | none | `--synthetic` |
| `set_controls_once.py` | Manual exposure and gain in one `set_many()` call at startup; prints readback and `kept` | one `set_many()` call | `--synthetic` |
| `led_level.py` | Sets the IR illuminator level once and compares the image before and after | one write | `--synthetic` |
| `measure_fps.py` | Intervals between frames from `host_ns`: mean, min, max, late frames, repeated images | none | `--synthetic` |
| `capture_dataset_npz.py` | N frames into one `.npz` (`left`, `right`, `host_ns`) for training or analysis | none | `--synthetic` |
| `torch_dataloader.py` | Reads that `.npz` with a PyTorch `Dataset` and `DataLoader` (needs `torch`) | no camera | needs no camera |
| `list_devices.py` | Video devices Windows reports, in order, and how many are a Weevia S1; what to do with none or two | no camera | `--synthetic` (a made-up list) |
| `threaded_capture.py` | Reads on one thread, processes the latest frame on another (a queue of one); counts the frames skipped | none | `--synthetic` |
| `brightness_stats.py` | Per-eye mean, min, max and share of saturated pixels for N frames, into a CSV | none | `--synthetic` |
| `handle_stall.py` | On `DeviceError` (including one eye stopping): close, ask for the USB cable to be re-inserted, reopen; gives up after N tries | none | `--synthetic` (a fake capture) |
| `calibration_info.py` | The installed calibration: K, D, eye size, R, t and baseline (mm), or the SDK's message when there is none | no camera | `--synthetic` (a made-up calibration in a temporary folder) |

- **`--synthetic`** uses `opticmix_vision.synthetic`: generated frames and simulated controls with made-up
  ranges. It shows that the code runs; its frame rate, ranges and brightness say nothing about the camera.
  `live_preview.py` and `save_stereo_pairs.py` also take `--headless` to run without a window.
- **The compiled extension**: every example that opens the camera needs it — finding the camera by name
  and the camera controls run in it. Install the wheel (a plain source checkout does not have it).
- **Camera controls** (`list_controls.py`, `set_controls_once.py`, `led_level.py`) were measured with the
  earlier Python implementation; the Rust implementation (since 0.0.3) has not yet been verified against the
  camera. Write once, at startup — see *Camera controls* in the [package README](../README.md).
- **Timestamps** (`host_ns`) are the host's monotonic clock right after each frame was read, not a
  timestamp from the camera.
- **Images are as captured**: not rectified, no depth — rectification and depth are not in this package.
- **Platform**: Windows. On Linux (experimental) the examples have not been run.
- The six examples added in 0.0.4 (`play_recording.py`, `list_devices.py`, `threaded_capture.py`,
  `brightness_stats.py`, `handle_stall.py`, `calibration_info.py`) have been run without a camera only.
