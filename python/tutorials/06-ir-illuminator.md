# 06 · IR illuminator

Example: [`led_level.py`](../examples/led_level.py)

## What you will do

Set the IR illuminator level once, and compare the image before and after the write.

## Before you start

- [05 · Camera controls](05-camera-controls.md): the illuminator is a camera control, and the same rule
  applies — **write once at startup**.
- `led_level.py` makes one write (none if the illuminator is already at that level).
- Run the commands from the `python/` folder of the repository — see the [tutorials README](README.md) for
  which version to check out and which `python` to use.

## Run it

With the camera:

```bash
python examples/led_level.py                # full level (1.0 = 96)
python examples/led_level.py --level 0      # off
```

Without a camera (simulated control, generated frames):

```bash
python examples/led_level.py --synthetic
```

## What you should see

Output with `--synthetic` (generated frames); with the camera the numbers differ.

```text
$ python examples/led_level.py --synthetic
illuminator: 32 -> requested 96, readback 96
left  eye: mean   85.6 ->  156.7   pixels changed by >10:  99.5 %
right eye: mean   85.6 ->  156.5   pixels changed by >10:  99.5 %
```

The first line is the level before, the level requested and the driver's readback. The other two lines
compare each eye, averaged over several frames, before and after the write. When the level is already the
requested one, the example writes nothing and prints `no write (already …)` — the changes it prints then are
not the illuminator.

## How it works

The illuminator is exposed as the UVC backlight-compensation control. `LedControl` with a `value_range`
maps a level from 0.0 to 1.0 onto that range and rounds to a whole number before writing:

```python
led = LedControl(cam, value_range=(0, 96))
was = led.get()
for _ in range(a.settle):                 # frames right after opening are still settling
    cam.read()
before = average(cam, a.frames)
led.set_level(a.level)                    # one write, waits for it; rounds to a whole number
now = led.get()
for _ in range(a.settle):
    cam.read()
after = average(cam, a.frames)
```

- The ceiling of 96 was seen on two units; the dead zone and the onset were swept on one. **0–16 is off.**
  Light first appears at **24** and grows to **96**. Values above 96 are ignored. These figures were measured
  with the earlier Python implementation; the Rust implementation (since 0.0.3) has not yet been verified
  against the camera.
- A readback only means the driver accepted the number. Check the image — and frame mean is a poor check
  here, because the illuminator lights part of the field of view. That is why the example also prints the
  share of pixels that changed by more than 10.
- Illuminance, working distance and beam coverage have not been measured.

## If it does not work

See [When something goes wrong](../README.md#when-something-goes-wrong) and
[IR illuminator](../README.md#ir-illuminator) in the package README:

- **"value_range not set — the normalized (0..1) API needs a range"** — create it as
  `LedControl(cam, value_range=(0, 96))`, or use `set()`/`get()` with raw values.
- **A setting did not stick** — compare the readback with the level you requested.
- **The "after" image looks unchanged** — how long the light takes to change has not been measured; raise
  `--settle`.

## Next

[07 · Robust capture](07-robust-capture.md): keep processing off the read loop, and recover when an eye stops.

## Rust

The Rust crate has `led_level`. In a checkout of the repository, from
`rust/opticmix-vision`, without a camera:

```bash
cargo run --release --features test-support --example led_level -- --synthetic
```

Output with `--synthetic` (generated frames); with the camera the numbers differ.

```text
$ cargo run --release --features test-support --example led_level -- --synthetic
illuminator: 32 -> requested 96, written, readback 96
left  eye: mean  173.8 ->  173.8   pixels changed by >10:   0.0 %
right eye: mean  103.4 ->  103.4   pixels changed by >10:   0.0 %
```

- With `--synthetic` the generated image does not respond to the level at all, so the pixels do not change
  here.
- With the camera: not run yet. Writing through this crate has not been run on the camera.
