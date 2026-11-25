"""Per-unit calibration files: install the file for your camera once, then `Device.calibration` finds it.

The camera reports no USB serial number, so the SDK cannot tell which unit is connected. You install the
file for your camera; with exactly one file installed, that file is used. `check` measures, in pixels,
whether a file fits the camera in front of it, and compares several files when more than one is installed.

    python -m opticmix_vision.calib add D:/S1-0042.json   # install the file for your camera
    python -m opticmix_vision.calib list                  # what is installed, and where
    python -m opticmix_vision.calib show                  # what the installed file holds (--json for a program)
    python -m opticmix_vision.calib check                 # does the installed file fit the connected camera?
    python -m opticmix_vision.calib check --all           # which installed file fits it best?
    python -m opticmix_vision.calib check --usb-location "PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(1)"   # one of two
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np

from .calibration import (CONVENTION, SCHEMA_NAME, SCHEMA_VERSION, StereoCalibration, StereoExtrinsics,
                          load_calibration, synthetic_equidistant)

__all__ = ["CalibLookupError", "parse_label", "store_dir", "add", "installed", "resolve", "show",
           "CheckResult", "check", "check_pairs", "compare_pairs", "read_pairs", "load_pairs",
           "MIN_PAIRS", "MIN_POINTS", "main"]

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
                               f"the camera you use: delete the others from {store_dir()}. To see which one fits the "
                               "connected camera: python -m opticmix_vision.calib check --all")
    return load_calibration(store_dir() / f"{have[0]}.json")


# ---------------------------------------------------------------------------
# show: what a file holds
# ---------------------------------------------------------------------------

_SHOW_FILE = "python -m opticmix_vision.calib show <file.json>"
_NOTE = {"installed": "installed file, not matched to this camera (no USB serial)",
         "file": "file not installed, not matched to this camera (no USB serial)",
         "generated": "generated calibration (--synthetic): made-up values, not a file and not a camera"}


def show(target: str | None = None) -> dict[str, Any]:
    """What a calibration file holds, as plain values: `calib show` prints them, `calib show --json` this dict.

    `target`: None for the installed file (exactly one installed), an installed file's serial `S1-NNNN` (or the
    label `S1-NNNN-XXXX`), or a file's path. Reads the file only; the camera reports no USB serial number, so the
    file is not matched to a camera."""
    have = installed()
    if target is not None and Path(target).is_file():
        path = Path(target)
    elif target is not None:
        key = target.strip().upper()
        serial = parse_label(key)[0] if LABEL_RE.match(key) else key
        if not SERIAL_RE.match(serial):
            raise CalibLookupError(f"No such file: {target}. Name a calibration file, or the serial of an installed "
                                   f"one (S1-NNNN; installed: {', '.join(have) or 'none'}).")
        if serial not in have:
            raise CalibLookupError(f"{serial} is not installed (installed: {', '.join(have) or 'none'}). Install its "
                                   f"file: python -m opticmix_vision.calib add <file.json> — or show a file without "
                                   f"installing it: {_SHOW_FILE}")
        path = store_dir() / f"{serial}.json"
    elif not have:
        raise CalibLookupError("No calibration installed. Install the file for your camera: python -m "
                               f"opticmix_vision.calib add <file.json> — or show a file without installing it: {_SHOW_FILE}")
    elif len(have) > 1:
        raise CalibLookupError(f"{len(have)} calibrations installed ({', '.join(have)}). Name the one to show: "
                               f"python -m opticmix_vision.calib show {have[0]}. Device.calibration uses a file only "
                               f"when exactly one is installed: keep only the file for the camera you use (delete the "
                               f"others from {store_dir()}); python -m opticmix_vision.calib check --all shows which "
                               "one fits the connected camera.")
    else:
        path = store_dir() / f"{have[0]}.json"
    data = path.read_bytes()
    c = StereoCalibration.from_json(data.decode("utf-8"))
    is_installed = path.stem in have and path.resolve().parent == store_dir().resolve()
    if not is_installed:
        use = f"no: not installed (python -m opticmix_vision.calib add {path} installs it)"
    elif len(have) == 1:
        use = "yes: the only installed file"
    else:
        use = f"no: {len(have)} files are installed, and it needs exactly one"
    return _describe(c, "installed" if is_installed else "file", str(path), hashlib.sha256(data).hexdigest(),
                     is_installed and len(have) == 1, use)


def _describe(c: StereoCalibration, source: str, path: str | None, sha256: str | None, used: bool,
              use: str) -> dict[str, Any]:
    import cv2

    def eye(e: Any) -> dict[str, Any]:
        w, h = e.size
        return {"model": e.model, "size": [w, h], "mode": [2 * w, h], "fx": e.fx, "fy": e.fy, "cx": e.cx,
                "cy": e.cy, "K": e.K.tolist(), "D": e.D.tolist()}

    R = c.extrinsics.R
    rvec = cv2.Rodrigues(R)[0].reshape(3)
    angle = float(np.linalg.norm(rvec))
    return {"source": source, "path": path, "sha256": sha256, "matched_to_camera": False, "note": _NOTE[source],
            "used_by_device_calibration": used, "use": use,
            "schema": SCHEMA_NAME, "schema_version": SCHEMA_VERSION, "units": c.units, "device": dict(c.device),
            "left": eye(c.left), "right": eye(c.right),
            "extrinsics": {"convention": CONVENTION, "R": R.tolist(), "t": c.extrinsics.t.tolist(),
                           "baseline_mm": c.baseline_mm, "rvec_rad": rvec.tolist(),
                           "rvec_deg": np.degrees(rvec).tolist(), "rotation_deg": float(np.degrees(angle)),
                           "rotation_axis": (rvec / angle).tolist() if angle > 0 else None},
            "meta": dict(c.meta)}


def _show_text(d: Mapping[str, Any]) -> str:
    def row(values: Iterable[float], fmt: str) -> str:
        return "  ".join(format(v, fmt) for v in values)

    dev = d["device"]
    L = [f"calibration {dev.get('serial') or '-'}" + (f" ({dev['model']})" if dev.get("model") else "")]
    if d["path"]:
        L.append(f"file:     {d['path']}")
    L.append(f"          {d['note']}")
    if d["sha256"]:
        L.append(f"sha256:   {d['sha256']}")
    L.append(f"used by Device.calibration: {d['use']}")
    L.append(f"schema:   {d['schema']} version {d['schema_version']}, units {d['units']}")
    for side in ("left", "right"):
        e = d[side]
        L.append(f"{side + ':':<9} {e['model']}, eye size {e['size'][0]}x{e['size'][1]} "
                 f"(the {e['mode'][0]}x{e['mode'][1]} mode)")
        L.append(f"  fx {e['fx']:.4f}  fy {e['fy']:.4f}  cx {e['cx']:.4f}  cy {e['cy']:.4f}")
        L.append(f"  D (k1..k4)  {row(e['D'], '.6g')}")
    x = d["extrinsics"]
    L.append(f"left to right ({x['convention']}, mm):")
    L.append(f"  baseline  {x['baseline_mm']:.4f} mm")
    L.append(f"  t         {row(x['t'], '.4f')} mm")
    for i, r in enumerate(x["R"]):
        L.append(("  R         " if i == 0 else "            ") + row(r, "+.6f"))
    L.append(f"  rvec      {row(x['rvec_rad'], '+.6f')} rad = {row(x['rvec_deg'], '+.4f')} deg")
    axis = x["rotation_axis"]
    L.append(f"  rotation  {x['rotation_deg']:.4f} deg" + (f" about the axis {row(axis, '+.4f')}" if axis else ""))
    if d["meta"]:
        L.append("meta:     " + json.dumps(d["meta"], ensure_ascii=False, default=str))
    return "\n".join(L)


# ---------------------------------------------------------------------------
# check: does this file fit this camera?
# ---------------------------------------------------------------------------
# Image features matched between the two eyes must lie on each other's epipolar lines under the calibration.
# The distance of each match from its line, in pixels at the left eye's fx, is small for a file that fits and
# large for another unit's file. There is no pass/fail threshold: the two reference values below come from one
# camera each, which is not enough to set one.

MIN_PAIRS = 5        # fewer pairs with matches than this, or fewer points than MIN_POINTS: too few to tell
MIN_POINTS = 200
_MIN_MATCHES = 20    # a pair counts when it has at least this many matches
_MAX_NORM = 1.2      # points used: |x| and |y| below 1.2 in normalized coordinates, in both eyes
_WARMUP = 30         # frames read and dropped before keeping any

_REFERENCE = ("Reference, measured once on Windows: on one camera a matching file gave a median of 0.44 px; "
              "another camera's file gave 30.4 px. This tells a file that fits from one that does not; "
              "it is not a precision figure, and there is no pass/fail threshold.")


@dataclass(frozen=True)
class CheckResult:
    """How far matched points lie from their epipolar lines under a calibration, in pixels at the left eye's fx.

    `disparity_positive` is the fraction of points further right in the left image than in the right
    (averaged over pairs); it is near 1 when left and right are the right way round. No pass/fail."""

    median_px: float
    p95_px: float
    frac_le_1px: float
    frac_le_2px: float
    pairs: int
    points: int
    disparity_positive: float

    @property
    def enough(self) -> bool:
        """At least MIN_PAIRS pairs with matches and MIN_POINTS points; otherwise the numbers say nothing."""
        return self.pairs >= MIN_PAIRS and self.points >= MIN_POINTS

    def summary(self) -> str:
        """The text `calib check` prints."""
        counts = f"{self.points} points in {self.pairs} pairs with matches"
        if not self.enough:
            if self.points >= MIN_POINTS:
                return (f"Too few pairs to tell: {counts} (needs {MIN_PAIRS} pairs). Use {MIN_PAIRS} pairs or "
                        f"more (--frames {MIN_PAIRS} or more), with a textured scene in view of both eyes in each.")
            return (f"Too few matches to tell: {counts} (needs {MIN_POINTS} points in {MIN_PAIRS} pairs). "
                    "Point the camera at something with more texture, such as printed text or a bookshelf, "
                    "in view of both eyes, and run again.")
        lines = [f"epipolar distance: median {self.median_px:.2f} px, 95th percentile {self.p95_px:.2f} px, "
                 f"within 1 px {self.frac_le_1px:.0%}, within 2 px {self.frac_le_2px:.0%} ({counts})",
                 f"positive disparity: {self.disparity_positive:.1%} of points"]
        if self.disparity_positive < 0.5:
            lines.append("Fewer than half the points have positive disparity: the file's left and right look swapped "
                         "against the camera's. `calib check` opens the camera with DeviceConfig.weevia_s1(), which "
                         "sets the eye order, so look at the file first; in your own code, check the lr_order you "
                         "open the camera with.")
        lines.append(_REFERENCE)
        lines.append("If the median is far above the matching-file value (0.44 px), the file may be another "
                     "unit's: compare its serial with the label on the camera.")
        return "\n".join(lines)


def _gray(img: np.ndarray) -> np.ndarray:
    import cv2
    img = np.asarray(img)
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img


def _match(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    """SIFT matches between the eyes (ratio test 0.7) as rows [xL, yL, xR, yR] in pixels."""
    import cv2
    sift = cv2.SIFT_create(5000)
    k1, d1 = sift.detectAndCompute(left, None)
    k2, d2 = sift.detectAndCompute(right, None)
    if d1 is None or d2 is None or len(k2) < 2:
        return np.zeros((0, 4))
    ms = [p[0] for p in cv2.BFMatcher().knnMatch(d1, d2, k=2) if len(p) == 2 and p[0].distance < 0.7 * p[1].distance]
    return np.array([k1[m.queryIdx].pt + k2[m.trainIdx].pt for m in ms]).reshape(-1, 4)


def _normalized(c: StereoCalibration, P: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Undistorted normalized coordinates of both eyes' points, keeping |x|, |y| < _MAX_NORM in both."""
    import cv2
    # contiguous arrays: cv2.fisheye.undistortPoints misreads a strided view
    p1 = cv2.fisheye.undistortPoints(np.ascontiguousarray(P[:, :2]).reshape(-1, 1, 2), c.left.K, c.left.D).reshape(-1, 2)
    p2 = cv2.fisheye.undistortPoints(np.ascontiguousarray(P[:, 2:]).reshape(-1, 1, 2), c.right.K, c.right.D).reshape(-1, 2)
    ok = (np.abs(p1).max(1) < _MAX_NORM) & (np.abs(p2).max(1) < _MAX_NORM)
    return p1[ok], p2[ok]


def _epipolar_px(c: StereoCalibration, P: np.ndarray) -> np.ndarray:
    """Point-to-epipolar-line distance in normalized coordinates × the left eye's fx, one per kept point."""
    p1, p2 = _normalized(c, P)
    t, R = c.extrinsics.t, c.extrinsics.R
    E = np.array([[0, -t[2], t[1]], [t[2], 0, -t[0]], [-t[1], t[0], 0]]) @ R
    h1, h2 = np.c_[p1, np.ones(len(p1))], np.c_[p2, np.ones(len(p2))]
    line = h1 @ E.T
    return np.abs((h2 * line).sum(1) / np.hypot(line[:, 0], line[:, 1])) * c.left.fx


def _disparity_positive(c: StereoCalibration, P: np.ndarray) -> float:
    """Fraction of kept points with x_left > x_right in normalized coordinates (NaN when none is kept)."""
    p1, p2 = _normalized(c, P)
    return float(np.mean(p1[:, 0] > p2[:, 0])) if len(p1) else float("nan")


def _matches(pairs: Iterable[tuple[np.ndarray, np.ndarray]],
             calibrations: Mapping[str, StereoCalibration]) -> list[np.ndarray]:
    """Grayscale, eye size checked against every calibration, then the SIFT matches of each pair."""
    gray = [(_gray(left), _gray(right)) for left, right in pairs]
    for name, c in calibrations.items():
        for left, right in gray:
            for img, eye in ((left, c.left), (right, c.right)):
                if (img.shape[1], img.shape[0]) != tuple(eye.size):
                    w, h = eye.size
                    raise ValueError(f"{name + ': ' if name else ''}This calibration is for {w}x{h} eyes; the frames "
                                     f"are {img.shape[1]}x{img.shape[0]}. Open the {2 * w}x{h} mode.")
    return [_match(left, right) for left, right in gray]


def _result(matches: list[np.ndarray], c: StereoCalibration) -> CheckResult:
    dist, pos = [], []
    for P in matches:
        if len(P) < _MIN_MATCHES:
            continue
        dist.append(_epipolar_px(c, P))
        pos.append(_disparity_positive(c, P))
    d = np.concatenate(dist) if dist else np.zeros(0)
    pos = [p for p in pos if p == p]
    nan = float("nan")
    return CheckResult(median_px=float(np.median(d)) if d.size else nan,
                       p95_px=float(np.percentile(d, 95)) if d.size else nan,
                       frac_le_1px=float(np.mean(d <= 1.0)) if d.size else nan,
                       frac_le_2px=float(np.mean(d <= 2.0)) if d.size else nan,
                       pairs=len(dist), points=int(d.size),
                       disparity_positive=float(np.mean(pos)) if pos else nan)


def check_pairs(pairs: Iterable[tuple[np.ndarray, np.ndarray]], calibration: StereoCalibration) -> CheckResult:
    """`check` on (left, right) images you already have, such as pairs from `load_pairs`."""
    return _result(_matches(pairs, {"": calibration}), calibration)


def compare_pairs(pairs: Iterable[tuple[np.ndarray, np.ndarray]],
                  calibrations: Mapping[str, StereoCalibration]) -> dict[str, CheckResult]:
    """`check_pairs` for several files on the same pairs (features are matched once). Keys are yours (serials)."""
    matches = _matches(pairs, calibrations)
    return {name: _result(matches, c) for name, c in calibrations.items()}


def read_pairs(device: Any, *, frames: int = 10, every: int = 10) -> list[tuple[np.ndarray, np.ndarray]]:
    """Drops 30 frames, then reads `frames * every` and keeps one (left, right) pair every `every` frames."""
    if frames < 1 or every < 1:
        raise ValueError(f"frames and every must be at least 1: frames={frames}, every={every}")
    for _ in range(_WARMUP):
        device.read()
    kept = []
    for i in range(frames * every):
        f = device.read()
        if i % every == 0:
            kept.append((f.left.copy(), f.right.copy()))
    return kept


def check(device: Any, calibration: StereoCalibration | None = None, *, frames: int = 10,
          every: int = 10) -> CheckResult:
    """Measures whether `calibration` (default: `device.calibration`) fits the camera `device` is reading.

    `read_pairs` first, then `check_pairs`: matching starts after the last frame is read, so it does not slow
    the reading. Point the camera at a textured scene in view of both eyes. Returns the numbers only — there is
    no pass/fail threshold."""
    if calibration is None:
        calibration = device.calibration
    return check_pairs(read_pairs(device, frames=frames, every=every), calibration)


def load_pairs(folder: str | Path) -> list[tuple[np.ndarray, np.ndarray]]:
    """Grayscale (left, right) pairs from PNGs saved by the viewer (`<name>_left.png`, `<name>_right.png`) or by
    examples/save_stereo_pairs.py (`left_<n>.png`, `right_<n>.png`), sorted by name."""
    import cv2
    d = Path(folder)
    found = [(p, p.with_name(p.name[:-len("_left.png")] + "_right.png")) for p in sorted(d.glob("*_left.png"))]
    found += [(p, p.with_name("right_" + p.name[len("left_"):])) for p in sorted(d.glob("left_*.png"))]
    pairs = []
    for a, b in found:
        if b.is_file():
            # np.fromfile + imdecode: cv2.imread cannot open a non-ASCII path on Windows
            imgs = [cv2.imdecode(np.fromfile(str(p), np.uint8), cv2.IMREAD_GRAYSCALE) for p in (a, b)]
            if any(i is None for i in imgs):
                raise ValueError(f"Cannot read {a.name} / {b.name} as images.")
            pairs.append((imgs[0], imgs[1]))
    if not pairs:
        raise ValueError(f"No left/right PNG pairs in {d} (expected *_left.png + *_right.png or left_*.png + "
                         "right_*.png).")
    return pairs


def _synthetic_calibration() -> StereoCalibration:
    eye = synthetic_equidistant((800, 800), 180.0)
    return StereoCalibration(left=eye, right=eye, extrinsics=StereoExtrinsics(R=np.eye(3), t=[-64.0, 0.0, 0.0]),
                             device={"model": "synthetic", "serial": "synthetic"})


def _frames_arg(text: str) -> int:
    try:
        n = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"must be a whole number, got {text!r}") from None
    if n < MIN_PAIRS:
        raise argparse.ArgumentTypeError(f"must be at least {MIN_PAIRS} (fewer pairs cannot tell)")
    return n


def _comparison(results: Mapping[str, CheckResult], sources: Mapping[str, str]) -> str:
    rows = [f"  {'file':<28} {'median px':>10} {'p95 px':>9} {'<=1 px':>7} {'pos. disp.':>10} {'points':>7}"]
    for name, r in results.items():
        label = f"{name} ({sources[name]})"
        rows.append(f"  {label:<28} {r.median_px:>10.2f} {r.p95_px:>9.2f} {r.frac_le_1px:>7.0%} "
                    f"{r.disparity_positive:>10.1%} {r.points:>7}" if r.enough else f"  {label:<28} too few to tell")
    ok = {n: r for n, r in results.items() if r.enough}
    if ok:
        best = min(ok, key=lambda n: ok[n].median_px)
        rows += [f"Smallest median: {best}. The smallest is not necessarily this camera's file: if every median is "
                 "far above the matching-file value (0.44 px), none of these files may belong to this camera. "
                 "Compare the serial with the label on the camera, and keep only that file installed.", _REFERENCE]
    else:
        rows.append(next(iter(results.values())).summary())
    return "\n".join(rows)


def _check_cmd(a: argparse.Namespace) -> int:
    cals: dict[str, StereoCalibration] = {}
    sources: dict[str, str] = {}
    for f in a.file or []:
        c = load_calibration(f)
        name = c.device.get("serial") or Path(f).stem
        name = Path(f).stem if name in cals else name          # two files with one serial: both stay
        cals[name], sources[name] = c, Path(f).name
    if a.all or (not a.file and not a.synthetic and len(installed()) > 1):
        for s in installed():
            cals[s], sources[s] = load_calibration(store_dir() / f"{s}.json"), "installed"
    if a.synthetic and not cals:
        cals["generated"], sources["generated"] = _synthetic_calibration(), "generated"
    if a.pairs_dir:
        pairs = load_pairs(a.pairs_dir)
        if not cals:
            c = resolve()
            cals[c.device.get("serial", "-")], sources[c.device.get("serial", "-")] = c, "installed"
    elif a.synthetic:
        from .synthetic import SyntheticPlaneSource
        print("synthetic: generated frames of a generated calibration, not a camera")
        pairs = read_pairs(SyntheticPlaneSource(_synthetic_calibration()), frames=a.frames, every=1)
    else:
        from .device import Device, DeviceConfig, DeviceError
        try:
            cfg = DeviceConfig.weevia_s1(usb_location=a.usb_location)
        except (DeviceError, ValueError) as exc:
            if a.usb_location is None:
                raise                                   # not found by name: main's message, exit 1, as before
            print(f"[calib] could not open the camera: {exc}", file=sys.stderr)   # nothing there, or empty: exit 2
            return 2
        with Device(cfg) as cam:
            if not cals:
                c = cam.calibration           # one installed file; also checks the eye size against the mode
                cals[c.device.get("serial", "-")], sources[c.device.get("serial", "-")] = c, "installed"
            pairs = read_pairs(cam, frames=a.frames)
    # the camera is closed here: matching runs after reading
    results = compare_pairs(pairs, cals)
    if len(results) > 1:
        print(f"Comparing {len(results)} files on the same {len(pairs)} pairs:")
        print(_comparison(results, sources))
    else:
        (name, r), = results.items()
        print(f"calibration: {name}" + (f" ({sources[name]})" if sources[name] != name else ""))
        print(r.summary())
    return 0 if any(r.enough for r in results.values()) else 2


def main(argv: list[str] | None = None) -> int:
    from .device import DeviceError
    ap = argparse.ArgumentParser(prog="python -m opticmix_vision.calib")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("add", help="install the calibration file for your camera").add_argument("file")
    sub.add_parser("list", help="show installed calibrations and the folder")
    s = sub.add_parser("show", help="print what a calibration file holds (reads the file only)")
    s.add_argument("target", nargs="?", help="an installed file's serial (S1-NNNN) or a file; default: the installed file")
    s.add_argument("--json", action="store_true", help="print it as JSON")
    s.add_argument("--synthetic", action="store_true", help="a generated calibration instead of a file")
    c = sub.add_parser("check", help="measure whether a calibration fits the connected camera (no pass/fail)")
    c.add_argument("--file", action="append", help="check this file instead of the installed one (repeat to compare)")
    c.add_argument("--all", action="store_true", help="compare every installed file")
    c.add_argument("--frames", type=_frames_arg, default=10, help=f"frame pairs to use (default 10, at least {MIN_PAIRS})")
    c.add_argument("--pairs-dir", help="check saved left/right PNG pairs in this folder instead of the camera")
    c.add_argument("--synthetic", action="store_true", help="generated frames, no camera")
    from .device import _USB_LOCATION_HELP
    c.add_argument("--usb-location", metavar="LOCATION", help=_USB_LOCATION_HELP)
    a = ap.parse_args(argv)
    if getattr(a, "usb_location", None) is not None and (a.synthetic or a.pairs_dir):
        ap.error("--usb-location cannot be combined with --synthetic or --pairs-dir")
    if a.cmd == "show" and a.synthetic and a.target:
        ap.error("show --synthetic takes no serial or file")
    for stream in (sys.stdout, sys.stderr):        # a file's meta or path with non-ASCII characters on a cp949 console
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        if a.cmd == "add":
            serial, status = add(a.file)
            print(f"{serial} calibration {status}: {store_dir() / (serial + '.json')}")
        elif a.cmd == "show":
            d = (_describe(_synthetic_calibration(), "generated", None, None, False, "no: generated, not a file")
                 if a.synthetic else show(a.target))
            print(json.dumps(d, indent=2, ensure_ascii=False, default=str) if a.json else _show_text(d))
        elif a.cmd == "check":
            return _check_cmd(a)
        else:
            print(f"folder: {store_dir()}")
            for s in installed():
                print(f"  {s}")
        return 0
    except DeviceError as exc:
        print(f"{exc}\nConnect the camera (if an eye stopped, re-insert the USB cable) and run again. "
              "Without the camera: --pairs-dir FOLDER checks pairs you saved earlier.", file=sys.stderr)
        return 1
    except (LookupError, ValueError, OSError) as exc:
        if a.cmd == "show" and a.json:            # a program reading stdout gets JSON either way
            print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        print(exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
