"""presets — save every camera control to a JSON file, and load a preset back in one call.

Usage:
  python examples/presets.py                           # save the current controls to my_preset.json
  python examples/presets.py --load my_preset.json     # show what would change, then load it
  python examples/presets.py --load my_preset.json --dry-run
  python examples/presets.py --synthetic               # no camera: simulated controls (made-up ranges)

Run without a camera only (simulated controls); not run with the camera.

Saving reads only. Loading writes every control whose value differs from the preset, in one set_many()
call — writing a preset to the camera writes several controls at once. On the units we have tested a
control write has occasionally been followed by one eye's stream stopping until the USB cable was
re-inserted, so load once, at startup (see "Camera controls" in the package README). The whole preset
is checked first: another camera model, a control this camera lacks, other units or a value out of
range stop it with nothing written.

The command line does the same with a preset folder: python -m opticmix_vision.presets --help

Needs: the opticmix-vision wheel (camera controls run in its compiled extension).
Writes to the camera: none when saving; with --load, one set_many() call — one write per control group
that differs (a mode and its value, e.g. auto_exposure and exposure, are one write).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # runs without installing
for _stream in (sys.stdout, sys.stderr):                          # Hangul/symbols on a cp949 console
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from opticmix_vision import ControlsUnavailable, Device, DeviceConfig, DeviceError, write_counts  # noqa: E402
from opticmix_vision.presets import PresetError  # noqa: E402


def open_camera(synthetic: bool):
    """The Weevia S1, found by name — or, with --synthetic, generated frames (not measured data)."""
    if synthetic:
        from opticmix_vision.synthetic import SyntheticStereoSource
        return SyntheticStereoSource()
    return Device(DeviceConfig.weevia_s1()).open()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--synthetic", action="store_true", help="simulated controls instead of a camera")
    ap.add_argument("--file", default="my_preset.json", help="where to save (default my_preset.json)")
    ap.add_argument("--load", metavar="FILE", help="load this preset instead of saving")
    ap.add_argument("--dry-run", action="store_true", help="with --load: show what would change, write nothing")
    a = ap.parse_args()

    try:
        with open_camera(a.synthetic) as cam:
            c = cam.controls
            if not a.load:
                path = c.save_preset(a.file, note="saved by examples/presets.py")   # reads only
                print(f"saved {len(c.snapshot())} controls ({c.model}) to {path}")
                return 0

            plan = c.plan_preset(a.load)            # the whole preset is checked here; nothing is written
            for name, (current, preset) in plan.items():
                print(f"{name:24s} {current!s:>8} -> {preset!s}")
            if not plan:
                print("the camera already has every value in the preset")
            if a.dry_run or not plan:
                return 0

            r = c.load_preset(a.load)               # one set_many(), then waits for the results
            for w in r.results:
                print(f"{w.name:24s} {w.status:8s} readback {w.readback!s:>8} kept {w.kept}"
                      + (f"  error: {w.error}" if w.error else ""))
            if r.left_out:
                print("left to auto:", ", ".join(r.left_out))
            if r.skipped_mono:     # include_mono=True would write them; they cannot change a mono image
                print("not written, no effect on the mono sensor:", ", ".join(r.skipped_mono))
            print("all kept" if r.ok else "not everything was kept — see above")
            print(f"control writes by this process: {write_counts()['_total']}")
            return 0 if r.ok else 1
    except PresetError as exc:
        print(f"Not loaded: {exc}", file=sys.stderr)
        return 2
    except (DeviceError, ControlsUnavailable) as exc:
        print(f"Camera error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
