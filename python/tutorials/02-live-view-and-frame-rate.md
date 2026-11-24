# 02 · Live view and frame rate

Examples: [`live_preview.py`](../examples/live_preview.py), [`measure_fps.py`](../examples/measure_fps.py)

## What you will do

Show both eyes side by side with the frame rate the loop receives, then time a run of frames: the intervals
between them, how many came late, and how many repeated the previous image byte for byte.

## Before you start

- Finish [01 · Hello Weevia](01-hello-weevia.md), or at least install the package
  (`pip install opticmix-vision`).
- Run the commands from the `python/` folder of the repository — see the [tutorials README](README.md) for
  which version to check out and which `python` to use.
- With the camera: the Weevia S1 on a USB 3 port directly. Without one: the `--synthetic` commands.

## Run it

With the camera:

```bash
python examples/live_preview.py                  # window; q quits
python examples/measure_fps.py                   # 450 frames
python examples/measure_fps.py --frames 2700     # a longer run
```

Without a camera:

```bash
python examples/live_preview.py --synthetic --headless --frames 60
python examples/measure_fps.py --synthetic --frames 90
```

Leave out `--headless --frames 60` to see the window with generated frames.

### Rust

The Rust crate has the same `measure_fps` example. In a checkout of the repository, from
`rust/opticmix-vision` (use `--release`: in a debug build, decoding the frames is slower than the camera sends
them):

```bash
cargo run --release --example measure_fps                                              # 450 frames
cargo run --release --features test-support --example measure_fps -- --synthetic --frames 90   # no camera
```

```text
$ cargo run --release --features test-support --example measure_fps -- --synthetic --frames 90
negotiated: 1600x800 MJPG 90.0 fps (synthetic)
90 frames, 89 intervals (host-measured)
interval ms: mean 11.11  min 9.70  max 12.40  -> 90.0 frames per second
intervals over 1.5x the median: 0
frames identical to the previous one: left 0, right 0
MJPG samples skipped because they did not decode: 0
since open, at the negotiated rate: late_frames 0, estimated_missed_frames 0, actual_fps 90.0
driver time stamps: none from this capture backend (synthetic)
driver sequence numbers: none from this capture backend
```

The "MJPG samples skipped" line counts the samples `read()` skipped because they did not decode. The next
line is `Device::late_frames()`, `estimated_missed_frames()` and `actual_fps()`: the same statistics, with the
same rule, as the Python `Device.late_frames`, `estimated_missed_frames` and `actual_fps`. The Rust
`measure_fps` has been run with the camera once (one PC, one camera, 2026-11-24); the output above is `--synthetic` (generated frames
paced in software).
See the crate README, "Examples".

## What you should see

Output with `--synthetic` (generated frames); with the camera the numbers differ.

```text
$ python examples/live_preview.py --synthetic --headless --frames 60
negotiated: {'width': 640, 'height': 200, 'fps': 90.0, 'fourcc': 'SYNT', 'backend_name': 'synthetic'}
60 frames, 90.0 fps over the last 30 intervals (host-measured)

$ python examples/measure_fps.py --synthetic --frames 90
negotiated: {'width': 640, 'height': 200, 'fps': 90.0, 'fourcc': 'SYNT', 'backend_name': 'synthetic'}
90 frames, 89 intervals (host-measured)
interval ms: mean 11.11  min 9.07  max 13.03  -> 90.0 frames per second
intervals over 1.5x the median: 0
frames identical to the previous one: left 0, right 1
driver time stamps: none from this capture backend (synthetic)
```

The generated frames are paced in software, so their rate says nothing about the camera. With the camera,
`negotiated:` shows the mode the driver gave (for `DeviceConfig.weevia_s1()` it asks for 1600×800 MJPG at
90 fps), and the window shows `L | R`, the eye size and the frame rate in its top-left corner.

## How it works

`live_preview.py` keeps the host time of the last 31 frames and turns their 30 intervals into a rate:

```python
stamps: deque[int] = deque(maxlen=31)         # host time of the last 31 frames = 30 intervals
n, fps = 0, 0.0
while a.frames is None or n < a.frames:
    f = cam.read()
    n += 1
    stamps.append(f.host_ns)
    if len(stamps) > 1:
        # The rate frames reach this loop. Drawing the window can make it lower than the camera's.
        fps = (len(stamps) - 1) * 1e9 / (stamps[-1] - stamps[0])
```

`measure_fps.py` throws away the first frame (it carries start-up delay), then reports on the intervals:

```python
def intervals(stamps: np.ndarray) -> tuple[np.ndarray, int]:
    """Intervals between stamps (ns) in ms, and how many are over 1.5x the median."""
    dt = np.diff(stamps) / 1e6
    return dt, int(np.sum(dt > 1.5 * np.median(dt)))

host = np.array(stamps, dtype=np.int64)
dt, late = intervals(host)                                 # ms between frames
print(f"interval ms: mean {dt.mean():.2f}  min {dt.min():.2f}  max {dt.max():.2f}  "
      f"-> {1000.0 / dt.mean():.1f} frames per second")
```

- `host_ns` is the host's monotonic clock right after each frame was read, not a timestamp from the camera,
  so it includes the host's scheduling jitter.
- The last line is about `StereoFrame.driver_ns`, the operating system capture driver's time stamp, which
  OpenCV passes on with V4L2 (Linux) and Media Foundation (Windows: `--backend msmf`, which finds the
  Weevia S1 by name in the Media Foundation list; `--device N` names the index yourself) but not with
  DirectShow, the Windows default. Where there is one, the same intervals are printed on that clock, with
  how far each frame trails the earliest (`host_ns - driver_ns`). It is a driver time, not a camera clock;
  whether it marks the exposure or the arrival of a frame is not known. On one PC (Windows 11) with one camera
  (2026-11-24, once) the first Media Foundation time was about 364,400 s — not the start of streaming; on this
  PC it was within a few seconds of Python's `time.perf_counter_ns()` (compared across two runs by file
  times, not within one process), but what the clock's zero is has not been established. Use differences between frames, not the absolute value.
- With `--backend msmf`, OpenCV asks Media Foundation to convert the camera's MJPG to RGB, and `driver_ns` is
  the time Media Foundation gave with that converted sample (OpenCV 4.13 `cap_msmf.cpp`; `negotiated:` then
  shows the RGB type's code — RGB32 is `'\x16\x00\x00\x00'` — not `MJPG`; lines 1153–1159, 2170–2171,
  2181–2182). The Rust example reads the MJPG samples from Media Foundation without conversion. Run with the camera once (one PC, one camera, 2026-11-24; `examples/measure_fps.py`, Media Foundation, 900 frames): 276 of 900 frames per eye repeated the previous image, and the intervals between `driver_ns` times were 11.11 ms (mean, min and max), so on this path `driver_ns` does not show whether a new image arrived; the cause has not been established. On the same PC and camera, DirectShow gave 0 of 900 and 0 of 450 repeated images, and the Rust crate's `measure_fps` (Media Foundation, no conversion) gave 0 of 450 with driver-time intervals from 0.01 to 16.90 ms (mean 11.23). Host-measured rates: 90.1 frames per second (Python, Media Foundation) and 89.1 (Rust). V4L2 driver times have not been run with the camera.
- Request the fourcc: without `fourcc="MJPG"` taking effect, the stream has run at 9.3 fps (measured
  2026-09-13, one unit). `DeviceConfig.weevia_s1()` requests it for you.
- On the units we have tested, one eye's stream has occasionally stopped. `read()` raises `DeviceError`
  saying which eye once it has sent the byte-identical image 90 frames in a row;
  `DeviceConfig.weevia_s1(stall_frames=0)` turns the check off. [Tutorial 07](07-robust-capture.md) shows
  how to recover.

## If it does not work

See [When something goes wrong](../README.md#when-something-goes-wrong) in the package README:

- **The frame rate is far below 90** — check that the mode is MJPG (`cam.mode`).
- **One eye goes black or stops moving** — re-inserting the USB cable brought it back on the units we have
  tested.
- **"frame read failed (timeout or camera unplugged)"** — check that the camera is still connected, then open
  it again.
- **Which USB port** — connect to a USB 3 port directly; USB 2 is not qualified.

## Next

[03 · Saving frames](03-saving-frames.md): write left/right pairs to PNG and collect frames into a dataset.
