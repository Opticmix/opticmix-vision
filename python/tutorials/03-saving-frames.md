# 03 · Saving frames

Examples: [`save_stereo_pairs.py`](../examples/save_stereo_pairs.py),
[`capture_dataset_npz.py`](../examples/capture_dataset_npz.py),
[`torch_dataloader.py`](../examples/torch_dataloader.py)

## What you will do

Save left/right PNG pairs with a CSV of when each frame arrived, collect frames into one `.npz` file, and read
that file back in batches with PyTorch.

## Before you start

- [02 · Live view and frame rate](02-live-view-and-frame-rate.md) runs on your setup.
- Run the commands from the `python/` folder of the repository — see the [tutorials README](README.md) for
  which version to check out and which `python` to use. They write files into the folder you run
  them from.
- `torch_dataloader.py` needs PyTorch, which opticmix-vision does not install (`pip install torch`). It opens
  no camera.

## Run it

With the camera:

```bash
python examples/save_stereo_pairs.py                         # window; space saves a pair, q quits
python examples/save_stereo_pairs.py --every 30 --count 10   # every 30th frame, stop after 10 pairs
python examples/capture_dataset_npz.py --frames 300 --out take1.npz
python examples/torch_dataloader.py take1.npz --batch-size 16
```

Without a camera:

```bash
python examples/save_stereo_pairs.py --synthetic --headless --every 5 --count 3
python examples/capture_dataset_npz.py --synthetic --frames 20
python examples/torch_dataloader.py --batch-size 8
```

The last command reads `stereo_dataset.npz`, the file the one before it wrote.

## What you should see

Output with `--synthetic` (generated frames); with the camera the numbers differ.

```text
$ python examples/save_stereo_pairs.py --synthetic --headless --every 5 --count 3
negotiated: {'width': 640, 'height': 200, 'fps': 90.0, 'fourcc': 'SYNT', 'backend_name': 'synthetic'}
saved 3 pairs in captures\20261124-001308

$ python examples/capture_dataset_npz.py --synthetic --frames 20
negotiated: {'width': 640, 'height': 200, 'fps': 90.0, 'fourcc': 'SYNT', 'backend_name': 'synthetic'}
saved 20 frames of 320x200 per eye to stereo_dataset.npz (0.21 s from first to last frame, host-measured)

$ python examples/torch_dataloader.py --batch-size 8
batch: (8, 2, 200, 320) torch.float32, range 0.082..0.588; host_ns (8,) torch.int64
20 frames in 3 batches; one pass read 20
```

Each run of `save_stereo_pairs.py` writes into a new folder, `captures/<date-time>/`:

```text
left_000123.png, right_000123.png   one pair per saved frame (123 = frame index in this run)
pairs.csv                            index, host_ns, driver_ns, driver_clock, left, right
```

The `.npz` file holds three arrays: `left` and `right`, each `(N, H, W)` `uint8`, and `host_ns`, `(N,)`
`int64`.

## How it works

`save_stereo_pairs.py` saves on the space key or every Nth frame, and writes one CSV row per pair:

```python
if key == ord(" ") or (a.every > 0 and f.index % a.every == 0):
    names = (f"left_{f.index:06d}.png", f"right_{f.index:06d}.png")
    save_png(out / names[0], f.left)
    save_png(out / names[1], f.right)
    rows.writerow([f.index, f.host_ns, f.driver_ns, f.driver_clock, *names])   # None: empty
    saved += 1
```

`save_png` encodes with `cv2.imencode` and writes the bytes itself, because `cv2.imwrite` fails silently on
non-ASCII paths on Windows.

`capture_dataset_npz.py` copies each eye out of the frame into preallocated arrays, then writes them once:

```python
for i in range(a.frames):
    if i:
        f = cam.read()
    left[i], right[i], host_ns[i] = f.left, f.right, f.host_ns     # copies out of the frame
...
np.savez_compressed(a.out, left=left, right=right, host_ns=host_ns)
```

`torch_dataloader.py` turns each frame into a `(2, H, W)` float tensor in 0..1 (channel 0 = left eye,
1 = right eye), so batches come out as `(B, 2, H, W)`.

- Images are as captured: not rectified, no depth — rectification and depth are not in this package.
- `host_ns` is the host's monotonic clock right after each frame was read, not a timestamp from the camera.
  `driver_ns`/`driver_clock` in `pairs.csv` are the capture driver's time stamp
  ([tutorial 02](02-live-view-and-frame-rate.md)): empty with DirectShow, the Windows default, and with
  `--synthetic`. Run without a camera only; not run with the camera.
- `capture_dataset_npz.py` keeps the frames in memory until the end; its docstring gives the size per frame.

## If it does not work

See [When something goes wrong](../README.md#when-something-goes-wrong) in the package README:

- **One eye goes black or stops moving** — the saved pairs show it too; see [tutorial 07](07-robust-capture.md).
- **"frame read failed (timeout or camera unplugged)"** — check that the camera is still connected, then open
  it again.
- **`save_stereo_pairs.py --headless` stops with "--headless has no space key: give --every N"** — without a
  window there is no space key; give `--every`.
- **`torch_dataloader.py` says it needs PyTorch** — install it with `pip install torch`.

## Next

[04 · Recording and playback](04-recording-and-playback.md): record both eyes into a video file and read it
back as if it were the camera.
