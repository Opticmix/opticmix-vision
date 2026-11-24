# 05 · Camera controls

Examples: [`list_controls.py`](../examples/list_controls.py),
[`set_controls_once.py`](../examples/set_controls_once.py),
[`brightness_stats.py`](../examples/brightness_stats.py), [`presets.py`](../examples/presets.py)

## What you will do

Read every control the camera advertises (writing nothing), set manual exposure and gain in one call at
startup and check what the camera kept, record per-eye brightness to compare settings, and save every control
to a preset file that you load back in one call.

## Before you start

- Read [Camera controls](../README.md#camera-controls) in the package README. **Writing is risky on this
  camera**: on the units we have tested, a control write has occasionally been followed by one eye's stream
  stopping until the USB cable was re-inserted. **Write once at startup.**
- Camera controls run in the compiled extension that ships in the wheel (`pip install opticmix-vision`).
- The controls were measured with the earlier Python implementation; the Rust implementation (since 0.0.3)
  has not yet been verified against the camera.
- Run the commands from the `python/` folder of the repository — see the [tutorials README](README.md) for
  which version to check out and which `python` to use.

| Example | Writes to the camera |
|---|---|
| `list_controls.py` | none |
| `set_controls_once.py` | one `set_many()` call |
| `brightness_stats.py` | none |
| `presets.py` | none when saving; `--load` makes one `set_many()` call |

## Run it

With the camera:

```bash
python examples/list_controls.py                           # reads only
python examples/set_controls_once.py                       # exposure -5, gain 28, in one set_many() call
python examples/set_controls_once.py --exposure -6 --gain 40
python examples/brightness_stats.py                        # 90 frames into brightness.csv
python examples/presets.py                                 # every control into my_preset.json; reads only
python examples/presets.py --load my_preset.json --dry-run  # what loading would change; writes nothing
python examples/presets.py --load my_preset.json           # one set_many() call
```

Without a camera (simulated controls with made-up ranges):

```bash
python examples/list_controls.py --synthetic
python examples/set_controls_once.py --synthetic
python examples/brightness_stats.py --synthetic --frames 30
python examples/presets.py --synthetic
python examples/presets.py --synthetic --load my_preset.json --dry-run
```

### Rust

The Rust crate has the same `list_controls` and `set_controls_once` examples. In a checkout of the repository,
from `rust/opticmix-vision`, without a camera (simulated controls with made-up ranges):

```bash
cargo run --release --features test-support --example list_controls -- --synthetic
cargo run --release --features test-support --example set_controls_once -- --synthetic
```

```text
$ cargo run --release --features test-support --example list_controls -- --synthetic
controls backend: sim (simulated controls — ranges and defaults are made up, not measured)
name                    current  range               default  units           effect
brightness              50       0..100 step 1       50       raw             unverified
contrast                50       0..100 step 1       50       raw             unverified
hue                     64       0..128 step 1       64       raw             none_expected_mono
saturation              36       0..128 step 1       36       raw             none_expected_mono
sharpness               28       0..100 step 1       28       raw             unverified
gamma                   50       1..500 step 1       50       raw             unverified
white_balance           128      0..255 step 1       128      raw             none_expected_mono
auto_white_balance      false    -                   -        raw             none_expected_mono
backlight_compensation  32       0..96 step 1        32       raw             verified
gain                    32       0..255 step 1       32       raw             verified
power_line_frequency    60hz     disabled/50hz/60hz  60hz     raw             unverified
exposure                -5       -11..-3 step 1      -5       log2_s_nominal  verified
auto_exposure           manual   manual/auto         -        raw             verified
auto_exposure_priority  false    -                   false    raw             unverified
control writes by this process: 0

$ cargo run --release --features test-support --example set_controls_once -- --synthetic
before: auto_exposure='manual', exposure=-5, gain=32
auto_exposure  requested 'manual'  skipped_same readback -         kept -
exposure       requested -5        skipped_same readback -         kept -
gain           requested 28        written      readback 28        kept yes
first frame after the write: mean left 174.7, right 99.4
```

Leave out `--features test-support` and `-- --synthetic` to use the camera. The Rust examples have been run
without a camera only (`--synthetic`); not run with the camera. Writing through the Rust crate has not been
run on the camera. See the crate README, "Examples".

## What you should see

Output with `--synthetic` (generated frames); with the camera the numbers differ.

```text
$ python examples/list_controls.py --synthetic
controls backend: sim (simulated controls — ranges and defaults are made up, not measured)
name                    current  range               default  units           effect
brightness              50       0..100 step 1       50       raw             unverified
contrast                50       0..100 step 1       50       raw             unverified
hue                     64       0..128 step 1       64       raw             none_expected_mono
saturation              36       0..128 step 1       36       raw             none_expected_mono
sharpness               28       0..100 step 1       28       raw             unverified
gamma                   50       1..500 step 1       50       raw             unverified
white_balance           128      0..255 step 1       128      raw             none_expected_mono
auto_white_balance      False    -                   -        raw             none_expected_mono
backlight_compensation  32       0..96 step 1        32       raw             verified
gain                    32       0..255 step 1       32       raw             verified
power_line_frequency    60hz     disabled/50hz/60hz  60hz     raw             unverified
exposure                -5       -11..-3 step 1      -5       log2_s_nominal  verified
auto_exposure           manual   manual/auto         -        raw             verified
auto_exposure_priority  False    -                   False    raw             unverified
control writes by this process: 0

$ python examples/set_controls_once.py --synthetic
before: {'auto_exposure': 'manual', 'exposure': -5, 'gain': 32}
auto_exposure  requested manual  skipped_same readback None    kept None
exposure       requested -5      skipped_same readback None    kept None
gain           requested 28      written      readback 28      kept True
first frame after the write: mean left 77.4, right 77.3

$ python examples/brightness_stats.py --synthetic --frames 30
negotiated: {'width': 640, 'height': 200, 'fps': 90.0, 'fourcc': 'SYNT', 'backend_name': 'synthetic'}
30 frames -> brightness.csv
left : mean 85.6 (frames 85.6..85.7)  min 21  max 150  saturated 0.000 % of pixels
right: mean 85.6 (frames 85.5..85.6)  min 21  max 150  saturated 0.000 % of pixels

$ python examples/presets.py --synthetic
saved 14 controls (synthetic) to my_preset.json

$ python examples/presets.py --synthetic --load my_preset.json --dry-run
the camera already has every value in the preset
```

The simulated ranges and defaults are made up; with the camera, ranges and defaults come from the device.
The `effect` column is how far each control's effect on the image has been verified. The last line of
`list_controls.py` counts this process's control writes, to show that it wrote nothing.

In `set_controls_once.py`, `skipped_same` means the camera already had that value, so nothing was sent.

`presets.py --load` first prints one line per control that differs (`name  current -> preset`). Each
`--synthetic` run starts from the same simulated values, so loading the preset just saved changes nothing.
With the camera, change a value (for example with `set_controls_once.py`) and load the preset to put it back.

## How it works

`list_controls.py` asks for every control at once; `describe()` with no name only reads:

```python
c = cam.controls
print(f"controls backend: {c.backend_name}" + (f" ({c.backend_note})" if c.backend_note else ""))
for name, i in c.describe().items():          # describe() with no name = every control, read only
    ...
print(f"control writes by this process: {write_counts().get('_total', 0)}")
```

`set_controls_once.py` gives the exposure mode and value in the same call, waits for the writes, and prints
what the camera reports back:

```python
results = c.set_many({"auto_exposure": "manual", "exposure": a.exposure, "gain": a.gain})
done = [r for r in results if r.status != "pending"]
done += c.wait([r.ticket for r in results if r.status == "pending"], timeout=5)
for r in done:
    print(f"{r.name:14s} requested {r.requested!s:7s} {r.status:12s} "
          f"readback {r.readback!s:7s} kept {r.kept}" + (f"  error: {r.error}" if r.error else ""))
```

- Every write goes through one guard: equal values are skipped, requests inside the interval collapse to the
  latest value, and writes are spaced at least 12 ms apart. The guard does not make writes safe.
- A `written` result only means the driver accepted the value. `result.readback` is what the camera reports
  afterwards and `result.kept` says whether it matches.
- Writing exposure while `auto_exposure` is `auto` raises `ControlStateError`; give both in one `set_many`.
- Right after an exposure change the two eyes can disagree in brightness for about 90 ms (measured on one
  unit) — drop those frames. `set_controls_once.py` drops 0.1 s of frames before it prints the first one.
- `brightness_stats.py` only reads: one CSV row per frame with each eye's mean, min, max and share of pixels
  at 255. Run it before and after a change to compare. It was added in 0.0.4 and has been run without a camera
  only.

`presets.py` saves with a call that only reads, and loads with a plan first:

```python
path = c.save_preset(a.file, note="saved by examples/presets.py")   # reads only
...
plan = c.plan_preset(a.load)            # the whole preset is checked here; nothing is written
for name, (current, preset) in plan.items():
    print(f"{name:24s} {current!s:>8} -> {preset!s}")
...
r = c.load_preset(a.load)               # one set_many(), then waits for the results
```

- `save_preset(path)` writes every readable control (value, units, auto/manual modes), the camera model and
  the SDK version to a JSON file.
- `plan_preset(path)` returns `{name: (current, preset)}` for what loading would change, and writes nothing.
- `load_preset(path)` writes only what differs, in one `set_many()` call, and returns each result with
  `kept`. Another camera model, a control this camera lacks, other units or a value out of range raise
  `PresetError` before anything is written. Controls with no effect on the mono sensor are written only
  with `include_mono=True`.
- `python -m opticmix_vision.presets` saves, loads and lists presets in a folder next to the calibration
  folder; the explorer's Settings window has `Save preset…` and `Load preset…` ([tools](tools.md)).
- Presets have been run without a camera only (simulated controls); not run with the camera. Loading a
  preset writes several controls at once — load once, at startup.

To change controls by hand instead, the [viewer and the explorer](tools.md) write once when you stop
pressing a key or release a slider.

## If it does not work

See [When something goes wrong](../README.md#when-something-goes-wrong) in the package README:

- **A setting did not stick** — look at `result.kept`.
- **"ControlStateError: auto_exposure is on — set it to 'manual' in the same call"** — give the mode and the
  value in one call.
- **"… is not a camera control — set resolution, fps and format in DeviceConfig"** — those are set when the
  camera opens.
- **"ControlsUnavailable: opticmix_vision._core (the compiled extension) is not installed"** — install the wheel with the same `python` you run the example with
  (`python -m pip install opticmix-vision`).
- **"Not loaded: …"** from `presets.py --load` — the whole preset was checked and nothing was written; the
  message says which control, model, unit or range does not match.
- **One eye goes black or stops moving after a write** — re-insert the USB cable; see
  [tutorial 07](07-robust-capture.md).

## Next

[06 · IR illuminator](06-ir-illuminator.md): set the illuminator level once and check it in the image.
