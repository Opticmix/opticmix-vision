# 07 · Robust capture

Examples: [`threaded_capture.py`](../examples/threaded_capture.py), [`handle_stall.py`](../examples/handle_stall.py)

## What you will do

Read frames on one thread and process the latest one on another, so processing never falls behind the
camera. Then handle `DeviceError` — including one eye stopping — by closing the camera, asking for the USB
cable to be re-inserted, and opening it again.

## Before you start

- [02 · Live view and frame rate](02-live-view-and-frame-rate.md) runs on your setup.
- Both examples were added in 0.0.4; both have been run without a camera only.
- Neither example writes to the camera.
- Run the commands from the `python/` folder of the repository — see the [tutorials README](README.md) for
  which version to check out and which `python` to use.

## Run it

With the camera:

```bash
python examples/threaded_capture.py                 # 300 processed frames, 20 ms of work each
python examples/threaded_capture.py --work-ms 5     # lighter processing
python examples/handle_stall.py                     # read 900 frames, up to 3 reopens
python examples/handle_stall.py --retries 5 --wait 10
```

Without a camera:

```bash
python examples/threaded_capture.py --synthetic --frames 60
python examples/handle_stall.py --synthetic --frames 200 --wait 0
```

With `--synthetic`, `handle_stall.py` uses a fake capture (not a camera) in which one eye repeats one image
after 10 frames on the first open only, so the error and the reopen run without hardware.

## What you should see

Output with `--synthetic` (generated frames); with the camera the numbers differ.

```text
$ python examples/threaded_capture.py --synthetic --frames 60
negotiated: {'width': 640, 'height': 200, 'fps': 90.0, 'fourcc': 'SYNT', 'backend_name': 'synthetic'}
processed 60 frames, skipped 50 (frames read while the previous one was still being processed)

$ python examples/handle_stall.py --synthetic --frames 200 --wait 0
Camera error after 99 frames: The right eye has sent the same image for 90 frames in a row: its stream has stopped. Re-insert the USB cable: unplug it and plug it back in (it sometimes takes two tries). stall_frames=0 in DeviceConfig turns this check off.
Camera closed. If an eye stopped or the camera is gone, re-insert the USB cable now: unplug it and plug it back in. Reopening in 0 s (reopen 1 of 3).
read 200 frames, 1 reopen(s)
```

The first line of `handle_stall.py` is printed to stderr. The fake capture freezes the first half of the
frame, and `DeviceConfig.weevia_s1()` treats the first half as the right eye, so the message names the right
eye.

## How it works

`threaded_capture.py`: the reader thread keeps only the newest frame in a queue that holds one, throwing
away a frame nobody has taken yet:

```python
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
```

The processing thread counts skipped frames from gaps in `frame.index`. `--work-ms` stands in for your own
processing.

`handle_stall.py`: leaving the `with` block closes the camera, and every attempt opens a new `Device`.
`Device.open()` also starts the stall count afresh, so opening the same `Device` again judges the new stream
on its own frames, not on the count of the stream before:

```python
while got < a.frames:
    try:
        with open_camera(a.synthetic, reopens) as cam:
            while got < a.frames:
                cam.read()                     # your processing goes here
                got += 1
    except DeviceError as exc:                 # leaving the with-block closed the camera
        print(f"Camera error after {got} frames: {exc}", file=sys.stderr)
        if reopens >= a.retries:
            ...
            return 2
        reopens += 1
        ...
        time.sleep(a.wait)
```

- On the units we have tested, one eye's stream has occasionally stopped; re-inserting the USB cable brought
  it back. `read()` raises `DeviceError` saying which eye once it has sent the byte-identical image 90 frames
  in a row. `DeviceConfig.weevia_s1(stall_frames=0)` turns the check off.
- `read()` also raises `DeviceError` when a frame cannot be read (timeout or camera unplugged), and opening the
  camera raises it while the cable is out ("Weevia S1 … not found").

## If it does not work

See [When something goes wrong](../README.md#when-something-goes-wrong) in the package README:

- **One eye goes black or stops moving** — re-insert the USB cable.
- **"Weevia S1 not found"** while reopening — the cable is still out, or Windows does not see the camera yet;
  raise `--wait`.
- **"frame read failed (timeout or camera unplugged)"** — check that the camera is still connected.
- **`handle_stall.py` ends with "Gave up after N reopen(s)"** (exit code 2) — re-insert the USB cable and run
  again.

## Next

[08 · Calibration file](08-calibration-file.md): install the calibration file for your camera and read it.

## Rust

The Rust crate has `threaded_capture` and `handle_stall`. In a checkout of the repository, from
`rust/opticmix-vision`, without a camera:

```bash
cargo run --release --features test-support --example threaded_capture -- --synthetic --frames 60
cargo run --release --features test-support --example handle_stall -- --synthetic --frames 200 --wait 0
```

Output with `--synthetic` (generated frames); with the camera the numbers differ.

```text
$ cargo run --release --features test-support --example threaded_capture -- --synthetic --frames 60
negotiated: 1600x800 MJPG 90.0 fps (synthetic)
processed 60 frames, skipped 50 (frames read while the previous one was still being processed)
reader thread: late_frames 0, estimated_missed_frames 0, actual_fps 90.0 (frames the reader itself got late or not at all)

$ cargo run --release --features test-support --example handle_stall -- --synthetic --frames 200 --wait 0
Camera error after 99 frames: The right eye has sent the same image for 90 frames in a row: its stream has stopped. Re-insert the USB cable: unplug it and plug it back in (it sometimes takes two tries). stall_frames=0 in DeviceConfig turns this check off.
Camera closed. If an eye stopped or the camera is gone, re-insert the USB cable now: unplug it and plug it back in. Reopening in 0 s (reopen 1 of 3).
read 200 frames, 1 reopen(s)
```

- `threaded_capture` moves the `Device` into a reader thread (`Device` is `Send`). Beside the processed and
  skipped frames it prints the reader's `Device` statistics, returned when its thread is joined.
- `handle_stall` prints its first line to stderr, as the Python example does.
- With the camera: `threaded_capture` — same calls run on Windows on one camera in an internal check of this
  crate (not the statistics line); the example itself not yet. `handle_stall`: not run yet.
