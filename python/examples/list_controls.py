"""list_controls — print every control the camera advertises: current value, range, default. Reads only.

Usage:
  python examples/list_controls.py
  python examples/list_controls.py --synthetic     # no camera: simulated controls (made-up ranges)

Ranges and defaults come from the device, not from this package. The "effect" column is how far the
control's effect on the image has been verified: verified / partial / unverified / none_expected_mono.

Needs: the opticmix-vision wheel (camera controls run in its compiled extension).
Writes to the camera: none — the last line prints this process's control-write count to show it.
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


def open_camera(synthetic: bool):
    """The Weevia S1, found by name — or, with --synthetic, generated frames (not measured data)."""
    if synthetic:
        from opticmix_vision.synthetic import SyntheticStereoSource
        return SyntheticStereoSource(realtime=True)
    return Device(DeviceConfig.weevia_s1()).open()


def show(v) -> str:
    return "-" if v is None else str(v)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--synthetic", action="store_true", help="simulated controls instead of a camera")
    a = ap.parse_args()

    try:
        with open_camera(a.synthetic) as cam:
            c = cam.controls
            print(f"controls backend: {c.backend_name}" + (f" ({c.backend_note})" if c.backend_note else ""))
            rows = [("name", "current", "range", "default", "units", "effect")]
            for name, i in c.describe().items():          # describe() with no name = every control, read only
                if i.choices:
                    rng = "/".join(i.choices)
                elif i.minimum is None:
                    rng = "-"
                else:
                    rng = f"{i.minimum}..{i.maximum} step {show(i.step)}"
                rows.append((name, show(i.current), rng, show(i.default), i.units, i.effect))
            widths = [max(len(r[k]) for r in rows) for k in range(len(rows[0]))]
            for r in rows:
                print("  ".join(cell.ljust(wd) for cell, wd in zip(r, widths)).rstrip())
    except (DeviceError, ControlsUnavailable) as exc:
        print(f"Camera error: {exc}", file=sys.stderr)
        return 2
    print(f"control writes by this process: {write_counts().get('_total', 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
