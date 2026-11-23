"""Per-unit calibration files: install the file for your camera once, then `Device.calibration` finds it.

The camera reports no USB serial number, so the SDK cannot tell which unit is connected. You install the
file for your camera; with exactly one file installed, that file is used.

    python -m opticmix_vision.calib add D:/S1-0042.json   # install the file for your camera
    python -m opticmix_vision.calib list                  # what is installed, and where
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from .calibration import StereoCalibration, load_calibration

__all__ = ["CalibLookupError", "parse_label", "store_dir", "add", "installed", "resolve", "main"]

LABEL_RE = re.compile(r"^(S1-\d{4})-([A-Z0-9]{4})$")
SERIAL_RE = re.compile(r"^S1-\d{4}$")


class CalibLookupError(LookupError):
    """No usable calibration; the message says what to do next."""


def parse_label(text: str) -> tuple[str, str]:
    """'s1-0042-k7q2' -> ('S1-0042', 'S1-0042-K7Q2')."""
    label = text.strip().upper()
    m = LABEL_RE.match(label)
    if not m:
        raise CalibLookupError(f"Not a label: {text!r}. Check the 12 characters on the label (S1-NNNN-XXXX).")
    return m.group(1), label


def store_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ["APPDATA"]) / "opticmix" / "calib"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path(os.environ.get("HOME", "~")).expanduser() / ".config")
    return Path(base) / "opticmix" / "calib"


def add(path: str) -> tuple[str, str]:
    """Installs a calibration file. Returns (serial, 'added' | 'replaced' | 'unchanged')."""
    p = Path(path)
    if not p.is_file():
        if LABEL_RE.match(path.strip().upper()):
            raise CalibLookupError("Installing by label is not available yet. Install the file for your camera: "
                                   "python -m opticmix_vision.calib add <file.json>")
        raise CalibLookupError(f"No such file: {path}")
    text = p.read_text(encoding="utf-8")
    c = StereoCalibration.from_json(text)  # schema v1 check
    serial = c.device.get("serial", "")
    if not SERIAL_RE.match(serial):
        raise CalibLookupError(f"This file has no S1-NNNN serial (device.serial={serial!r}). Not installed.")
    d = store_dir()
    d.mkdir(parents=True, exist_ok=True)
    dst = d / f"{serial}.json"
    status = "added" if not dst.exists() else (
        "unchanged" if dst.read_text(encoding="utf-8") == text else "replaced")
    if status != "unchanged":
        dst.write_text(text, encoding="utf-8")
    return serial, status


def installed() -> list[str]:
    """Serials of the installed files, sorted."""
    d = store_dir()
    return sorted(f.stem for f in d.glob("S1-[0-9][0-9][0-9][0-9].json")) if d.exists() else []


def resolve() -> StereoCalibration:
    """The installed calibration when there is exactly one; otherwise an error that says what to do."""
    have = installed()
    if not have:
        raise CalibLookupError("No calibration installed. Install the file for your camera: "
                               "python -m opticmix_vision.calib add <file.json>")
    if len(have) > 1:
        raise CalibLookupError(f"{len(have)} calibrations installed ({', '.join(have)}). Keep only the file for "
                               f"the camera you use: delete the others from {store_dir()}")
    return load_calibration(store_dir() / f"{have[0]}.json")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m opticmix_vision.calib")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("add", help="install the calibration file for your camera").add_argument("file")
    sub.add_parser("list", help="show installed calibrations and the folder")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "add":
            serial, status = add(a.file)
            print(f"{serial} calibration {status}: {store_dir() / (serial + '.json')}")
        else:
            print(f"folder: {store_dir()}")
            for s in installed():
                print(f"  {s}")
        return 0
    except (LookupError, ValueError, OSError) as exc:
        print(exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
