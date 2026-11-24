"""Per-unit calibration files: install the file for your camera once, then `Device.calibration` finds it.

The camera reports no USB serial number, so the SDK cannot tell which unit is connected. You install the
file for your camera; with exactly one file installed, that file is used. `check` measures, in pixels,
whether a file fits the camera in front of it, and compares several files when more than one is installed.

    python -m opticmix_vision.calib add D:/S1-0042.json   # install the file for your camera
    python -m opticmix_vision.calib list                  # what is installed, and where
    python -m opticmix_vision.calib check                 # does the installed file fit the connected camera?
    python -m opticmix_vision.calib check --all           # which installed file fits it best?
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np

from .calibration import StereoCalibration, StereoExtrinsics, load_calibration, synthetic_equidistant

__all__ = ["CalibLookupError", "parse_label", "store_dir", "add", "installed", "resolve",
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
        from .device import Device, DeviceConfig
        with Device(DeviceConfig.weevia_s1()) as cam:
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
    c = sub.add_parser("check", help="measure whether a calibration fits the connected camera (no pass/fail)")
    c.add_argument("--file", action="append", help="check this file instead of the installed one (repeat to compare)")
    c.add_argument("--all", action="store_true", help="compare every installed file")
    c.add_argument("--frames", type=_frames_arg, default=10, help=f"frame pairs to use (default 10, at least {MIN_PAIRS})")
    c.add_argument("--pairs-dir", help="check saved left/right PNG pairs in this folder instead of the camera")
    c.add_argument("--synthetic", action="store_true", help="generated frames, no camera")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "add":
            serial, status = add(a.file)
            print(f"{serial} calibration {status}: {store_dir() / (serial + '.json')}")
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
        print(exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
