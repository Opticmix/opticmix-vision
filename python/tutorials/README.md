# Tutorials

Eight short tutorials for the Weevia S1 Python SDK, in order. Each one runs examples that are already in
[`examples/`](../examples/README.md) and follows the same steps: what you will do, before you start, the
commands, what you should see, how the code works, what to do if it does not work, and where to go next.

**New to the camera? Do 1 → 2 → 3.** That is finding the camera, watching both eyes, and saving frames.

| # | Tutorial | What you learn | Examples | Camera needed | Writes to the camera |
|---|---|---|---|---|---|
| 1 | [Hello Weevia](01-hello-weevia.md) | find the camera by name, open it, read the negotiated mode, split left and right | `list_devices.py`, `quickstart.py` | `quickstart.py` only; `list_devices.py --synthetic` runs without one | none |
| 2 | [Live view and frame rate](02-live-view-and-frame-rate.md) | show both eyes; measure the intervals between frames and repeated images | `live_preview.py`, `measure_fps.py` | no — `--synthetic` | none |
| 3 | [Saving frames](03-saving-frames.md) | PNG pairs with a CSV of frame times; an `.npz` dataset; a PyTorch `DataLoader` | `save_stereo_pairs.py`, `capture_dataset_npz.py`, `torch_dataloader.py` | no — `--synthetic` | none |
| 4 | [Recording and playback](04-recording-and-playback.md) | record both eyes to video (MJPG or lossless) with a JSON of what was capturing; play it back through `Device`, from a frame or a time, at a speed, in a loop | `record_video.py`, `play_recording.py` | no — `--synthetic` | none |
| 5 | [Camera controls](05-camera-controls.md) | read every control; write exposure and gain once at startup and check what was kept; save every control to a preset and load it back | `list_controls.py`, `set_controls_once.py`, `brightness_stats.py`, `presets.py` | no — `--synthetic` (simulated controls) | one `set_many()` call (`set_controls_once.py`, `presets.py --load`) |
| 6 | [IR illuminator](06-ir-illuminator.md) | set the illuminator level once and check it in the image | `led_level.py` | no — `--synthetic` (simulated control) | one write |
| 7 | [Robust capture](07-robust-capture.md) | process the latest frame on another thread; reopen after a read error or a stopped eye | `threaded_capture.py`, `handle_stall.py` | no — `--synthetic` | none |
| 8 | [Calibration file](08-calibration-file.md) | install your camera's calibration file, print it, and check in numbers whether it fits the camera | `calibration_info.py`, `calibration_check.py` | `calibration_check.py` only; `--synthetic` needs no camera and no file | none |

The commands run from the `python/` folder of the repository, where `examples/` is. Check out the tag of the
version you installed (`python -m pip show opticmix-vision`, then for example `git checkout v0.0.5`) and use
the same `python` you ran `pip install` with: the examples load the package from this folder and the compiled
extension from the installed wheel, so both must be the same version. With `--synthetic` an
example uses generated frames and simulated controls with made-up ranges: it shows that the code runs, and its
frame rate, ranges and brightness say nothing about the camera. Of the six examples added in 0.0.4,
`play_recording.py` has been run with the camera once (one PC, one camera, 2026-11-24): it read back the 443 frames of an MJPG recording of the camera. `list_devices.py`, `threaded_capture.py`, `brightness_stats.py`,
`handle_stall.py` and `calibration_info.py` have been run without a camera only, and so have `presets.py` and
`calibration_check.py`, added in 0.0.5. Platform: Windows; on Linux (experimental) the examples have not been
run.

Tutorials 01, 02, 05 and 08 also show the Rust version of their examples (`cargo run --example …` in
`rust/opticmix-vision`), run without a camera only — except `list_devices` and `measure_fps`, run with the camera once (one PC, one camera, 2026-11-24).

The two windows that come with the package, the viewer and the explorer, are described in
[Viewer and explorer](tools.md).

## First run, step by step

1. **Install.** `pip install opticmix-vision`. See the top of the [package README](../README.md) for the
   platforms it installs on.
2. **Look at the picture first.** Connect the Weevia S1 to a USB 3 port directly and run
   `python -m opticmix_vision.explorer` (or `python -m opticmix_vision.viewer`). Both eyes should appear side
   by side. No camera yet? Add `--synthetic`. See [Viewer and explorer](tools.md).
3. **First code.** [01 · Hello Weevia](01-hello-weevia.md): find the camera by name and read frames.
4. **Save.** [03 · Saving frames](03-saving-frames.md) and [04 · Recording and playback](04-recording-and-playback.md).
5. **Controls.** [05 · Camera controls](05-camera-controls.md) and [06 · IR illuminator](06-ir-illuminator.md).
   Writing is risky on this camera — write once at startup.
6. **Next.** [07 · Robust capture](07-robust-capture.md), [08 · Calibration file](08-calibration-file.md),
   every public name in [API.md](../API.md), and [Getting help](../README.md#getting-help).

## If something goes wrong

1. Run `python -m opticmix_vision.doctor` and paste its output into an issue. It prints the OS, Python and SDK
   versions, the video devices, the camera's USB details and the installed calibration, without opening the
   camera.
2. If the camera opens, `python -m opticmix_vision.doctor --open` also opens it and adds the mode, frame
   timing, stalled eyes and every control's value, read only.
3. See [When something goes wrong](../README.md#when-something-goes-wrong) in the package README.

`doctor --open` has been run with the camera once (one PC, one camera, 2026-11-24); without `--open` the doctor has been run without a camera only (generated frames and fake device lists).
