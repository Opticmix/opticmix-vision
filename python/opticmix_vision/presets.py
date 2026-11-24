"""Control presets: save every readable control to a JSON file, and load it back in one guarded write.

    with Device(DeviceConfig.weevia_s1()) as cam:
        c = cam.controls
        c.save_preset(presets.preset_path("bench"), note="lab bench")   # reads only
        print(c.plan_preset(presets.preset_path("bench")))             # what loading would change; writes nothing
        r = c.load_preset(presets.preset_path("bench"))                # one set_many(), then waits
        for w in r.results:
            print(w.name, w.status, w.kept)

    python -m opticmix_vision.presets list                # the presets in store_dir() — does not open the camera
    python -m opticmix_vision.presets show NAME|FILE      # what a preset holds — no camera
    python -m opticmix_vision.presets save NAME           # opens the camera and reads every control
    python -m opticmix_vision.presets load NAME|FILE      # opens the camera and writes what differs

Loading writes several controls at once; see README, "Camera controls": write once at startup.
A preset is checked as a whole before anything is written: a different camera model, a control this camera
does not have, other units or a value out of the camera's range raise PresetError and nothing is written.
Controls with no effect on the monochrome sensor (hue, saturation, white balance) are saved but not written
unless include_mono=True — every write is a risk on this camera.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, NamedTuple

from . import __version__, calib
from . import controls as _controls
from .controls import WriteResult, write_counts
from .device import WEEVIA_S1_NAME

if TYPE_CHECKING:
    from .controls import Controls

__all__ = ["FORMAT", "VERSION", "BUILTIN", "PresetError", "PresetModelError", "PresetInfo", "PresetLoad",
           "store_dir", "preset_path", "read_preset", "list_presets", "main"]

log = logging.getLogger("opticmix_vision.presets")

FORMAT = "opticmix-controls-preset"
VERSION = 1
BUILTIN = "defaults"         # command line only: `load defaults` is Controls.reset_to_defaults()
_PRODUCTS = {WEEVIA_S1_NAME: "Weevia S1"}


class PresetError(ValueError):
    """Not a preset file, or it does not fit the connected camera. Nothing was written."""


class PresetModelError(PresetError):
    """The preset was saved from another camera model, or this camera's model is unknown. force=True loads it."""


class PresetInfo(NamedTuple):
    """One file in store_dir(). name is the file name without .json — what `load NAME` takes."""
    name: str
    path: Path
    model: str | None
    saved_at: str
    note: str


@dataclass(frozen=True)
class PresetLoad:
    """What load_preset() did.

    results       one WriteResult per control written (final, or "pending" if timeout ran out)
    unchanged     controls already at the preset's value — not written
    left_out      values of a group the preset puts in auto (e.g. exposure with auto_exposure "auto") — not written
    skipped_mono  controls with no effect on the monochrome sensor whose value differs — not written
                  (include_mono=True writes them)
    """
    results: tuple[WriteResult, ...]
    unchanged: tuple[str, ...]
    left_out: tuple[str, ...]
    skipped_mono: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """Every write finished and the camera kept every value it read back."""
        return all(r.status in ("written", "skipped_same") and r.kept is not False for r in self.results)


def store_dir() -> Path:
    """`%APPDATA%\\opticmix\\presets` on Windows; `$XDG_CONFIG_HOME/opticmix/presets` (or ~/.config/...) elsewhere."""
    return calib.store_dir().with_name("presets")


def preset_path(name: str) -> Path:
    """store_dir()/<name>.json. The name is a file name, not a path."""
    if not name or name in (".", "..") or Path(name).name != name:
        raise PresetError(f"A preset name is a file name, not a path: {name!r}")
    return store_dir() / f"{name}.json"


def read_preset(path: str | Path) -> dict[str, Any]:
    """The preset as a dict, after checking its format. Raises PresetError for a file that is not a preset."""
    p = Path(path)
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except ValueError as exc:                      # bad JSON or bad UTF-8
        raise PresetError(f"{p} is not a preset: {exc}") from exc
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        raise PresetError(f"{p} is not a controls preset (format must be {FORMAT!r})")
    if doc.get("version") != VERSION:
        raise PresetError(f"{p}: preset version {doc.get('version')!r}; this SDK reads version {VERSION}")
    ctl, dev = doc.get("controls"), doc.get("device")
    if not isinstance(dev, dict) or not isinstance(ctl, dict) or not all(
            isinstance(e, dict) and "value" in e for e in ctl.values()):
        raise PresetError(f"{p}: needs 'device' and 'controls' as {{name: {{'value': ..., 'units': ...}}}}")
    return doc


def list_presets(model: str | None = None) -> list[PresetInfo]:
    """The presets in store_dir(), by name. With model, only those saved from that model (`Controls.model`).
    Files that are not presets are skipped (logged)."""
    d = store_dir()
    out = []
    for f in sorted(d.glob("*.json")) if d.is_dir() else []:
        try:
            doc = read_preset(f)
        except (PresetError, OSError) as exc:
            log.warning("skipped %s: %s", f, exc)
            continue
        m = doc["device"].get("model")
        if model is None or m == model:
            out.append(PresetInfo(f.stem, f, m, str(doc.get("saved_at", "")), str(doc.get("note") or "")))
    return out


# --- what Controls.save_preset / plan_preset / load_preset do --------------------------------------

def _save(c: Controls, path: str | Path, name: str | None, note: str | None) -> Path:
    p = Path(path)
    infos = c.describe()                           # reads only
    doc = {
        "format": FORMAT,
        "version": VERSION,
        "name": p.stem if name is None else name,
        "note": note or "",
        "device": {"model": c.model, "product": _PRODUCTS.get(c.model or ""), "backend": c.backend_name},
        "sdk_version": __version__,
        "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "controls": {n: {"value": i.current, "units": i.units} for n, i in infos.items() if i.current is not None},
    }
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return p


def _check(info: Any, value: Any, units: Any) -> None:
    n = info.name
    if units is not None and units != info.units:
        raise PresetError(f"{n}: the preset is in {units!r}, this camera reports {info.units!r} (the preset was "
                          f"saved on another platform or backend). Save the preset again on this computer, or "
                          f"delete {n!r} from the file — a control left out of a preset is not touched. "
                          "force does not override this. Nothing was written.")
    if info.kind == "int":
        ranged = info.minimum is not None and info.maximum is not None
        ok = (isinstance(value, int) and not isinstance(value, bool)
              and (not ranged or info.minimum <= value <= info.maximum))
        want = "a whole number" + (f" within {info.minimum}..{info.maximum}" if ranged else "")
    elif info.kind == "bool":
        ok, want = isinstance(value, bool), "true or false"
    else:
        ok, want = value in (info.choices or ()), f"one of {tuple(info.choices or ())}"
    if not ok:
        raise PresetError(f"{n} must be {want} on this camera: {value!r}. Nothing was written.")


def _plan(c: Controls, path: str | Path, force: bool, include_mono: bool
          ) -> tuple[dict[str, tuple[Any, Any]], list[str], list[str], list[str]]:
    """(plan {name: (current, preset)}, unchanged, left_out, skipped_mono). Checks the whole preset; writes nothing."""
    doc = read_preset(path)
    saved = doc["device"].get("model")
    if not force and (saved is None or saved != c.model):
        here = c.model or f"a camera whose model the {c.backend_name} backend cannot tell"
        raise PresetModelError(f"The preset was saved from {saved or 'an unknown model'}; this is {here}. "
                               "Nothing was written. Pass force=True (command line: --force) to load it anyway.")
    infos = c.describe()
    values = {n: e["value"] for n, e in doc["controls"].items()}
    unknown = sorted(set(values) - set(infos))
    if unknown:
        raise PresetError(f"Not controls of this camera: {unknown}. Nothing was written.")
    for n, v in values.items():
        _check(infos[n], v, doc["controls"][n].get("units"))
    auto_of = {v: _controls._AUTO_NAME[g] for g, v in _controls._VALUE_NAME.items()}   # exposure -> auto_exposure
    left_out = [n for n in values if values.get(auto_of.get(n, "")) in ("auto", True)]
    differ = {n: (infos[n].current, v) for n, v in values.items() if n not in left_out and infos[n].current != v}
    skipped_mono = [] if include_mono else [n for n in differ if _controls._BY_NAME[n].mono]
    plan = {n: d for n, d in differ.items() if n not in skipped_mono}
    unchanged = [n for n in values if n not in left_out and n not in differ]
    return plan, unchanged, left_out, skipped_mono


def _load(c: Controls, path: str | Path, force: bool, include_mono: bool, timeout: float | None) -> PresetLoad:
    plan, unchanged, left_out, skipped_mono = _plan(c, path, force, include_mono)
    results: list[WriteResult] = []
    if plan:
        # One call: set_many checks every value before queuing any, and merges a mode with its value.
        results = c.set_many({n: new for n, (_, new) in plan.items()})
        tickets = [r.ticket for r in results if r.status == "pending"]
        final = {r.ticket: r for r in c.wait(tickets, timeout)} if tickets else {}
        results = [final.get(r.ticket, r) for r in results]
    return PresetLoad(tuple(results), tuple(unchanged), tuple(left_out), tuple(skipped_mono))


# --- command line ------------------------------------------------------------------------------

def _open(synthetic: bool) -> Any:
    """The only place the command line opens a camera (save and load)."""
    if synthetic:
        from .synthetic import SyntheticStereoSource
        return SyntheticStereoSource()
    from .device import Device, DeviceConfig
    return Device(DeviceConfig.weevia_s1()).open()


def _connected_model() -> str | None:
    """WEEVIA_S1_NAME when one is plugged in, from the device list (DirectShow / sysfs) — the camera is not opened."""
    from . import devices
    try:
        if sys.platform.startswith("linux"):
            devices.find_v4l2_node(WEEVIA_S1_NAME)
        else:
            devices.resolve_by_name(WEEVIA_S1_NAME)
    except devices.MultipleMatches:
        pass
    except Exception:  # noqa: BLE001 — not found, or the list is not available: show every preset
        return None
    return WEEVIA_S1_NAME


def _resolve(arg: str) -> Path:
    p = Path(arg)
    if p.is_file():
        return p
    if p.suffix.lower() == ".json" or len(p.parts) > 1:
        raise PresetError(f"No such file: {p}")
    q = preset_path(arg)
    if not q.is_file():
        raise PresetError(f"No preset named {arg!r} in {store_dir()} — see: python -m opticmix_vision.presets list")
    return q


def _print_plan(plan: Mapping[str, tuple[Any, Any]]) -> None:
    if not plan:
        print("nothing differs — nothing would be written")
    for n, (cur, new) in plan.items():
        print(f"  {n:24} {cur!r} -> {new!r}")


def _print_results(results: list[WriteResult] | tuple[WriteResult, ...]) -> None:
    for r in results:
        kept = "" if r.kept is None else ("kept" if r.kept else f"camera kept {r.readback!r}")
        print(f"  {r.name:24} {r.requested!r:>10}  {r.status:8} {kept}{'  ' + r.error if r.error else ''}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m opticmix_vision.presets",
                                 description="Save and load camera control presets. Only save and load open the "
                                             "camera; loading writes every control that differs, in one call.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list", help="the presets in the preset folder (for the connected model, if one is found)")
    ls.add_argument("--model", help="only presets saved from this model (e.g. 'WN Dual Camera')")
    ls.add_argument("--all", action="store_true", help="every preset, whatever the model")
    sub.add_parser("show", help="what a preset holds").add_argument("preset", help="NAME or FILE")
    s = sub.add_parser("save", help="read every control from the camera into NAME")
    s.add_argument("name")
    s.add_argument("--note", default="")
    s.add_argument("--overwrite", action="store_true", help="replace an existing preset of that name")
    ld = sub.add_parser("load", help=f"write a preset to the camera ('{BUILTIN}': the camera's own defaults)")
    ld.add_argument("preset", help=f"NAME, FILE or {BUILTIN}")
    ld.add_argument("--dry-run", action="store_true", help="print what would change; write nothing")
    ld.add_argument("--force", action="store_true", help="load a preset saved from another model")
    ld.add_argument("--include-mono", action="store_true",
                    help="also write controls with no effect on the mono sensor (hue, saturation, white balance)")
    for p in (s, ld):
        p.add_argument("--synthetic", action="store_true", help="simulated controls instead of a camera")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "list":
            model = None if a.all else (a.model or _connected_model())
            print(f"folder: {store_dir()}")
            print(f"showing: presets from {model} ({'--model' if a.model else 'connected'}; --all shows every one)"
                  if model else "showing: every preset")
            print(f"  {BUILTIN:20} built-in: the camera's own defaults (reset_to_defaults; auto/manual modes left "
                  "alone; not yet run against the camera)")
            for i in list_presets(model):
                print(f"  {i.name:20} {i.model or 'unknown model'}  {i.saved_at}  {i.note}")
            return 0
        if a.cmd == "show":
            path = _resolve(a.preset)
            doc = read_preset(path)
            dev = doc["device"]
            print(f"{path}\n  name: {doc.get('name')}  note: {doc.get('note')}\n  model: {dev.get('model')} "
                  f"({dev.get('product')})  backend: {dev.get('backend')}  sdk: {doc.get('sdk_version')}  "
                  f"saved: {doc.get('saved_at')}")
            for n, e in doc["controls"].items():
                print(f"  {n:24} {e['value']!r:>10}  {e.get('units', '')}")
            return 0
        if a.cmd == "save":
            if a.name == BUILTIN:
                raise PresetError(f"{BUILTIN!r} is the built-in preset; choose another name")
            path = preset_path(a.name)
            if path.exists() and not a.overwrite:
                raise PresetError(f"{path} exists. Pass --overwrite to replace it.")
            with _open(a.synthetic) as cam:
                n = len(read_preset(cam.controls.save_preset(path, name=a.name, note=a.note))["controls"])
            print(f"saved {n} controls to {path}")
            return 0
        builtin = a.preset == BUILTIN and not Path(a.preset).is_file()
        path = None if builtin else _resolve(a.preset)          # before opening the camera
        mono = a.include_mono
        with _open(a.synthetic) as cam:
            c = cam.controls
            if a.dry_run:
                _print_plan(c.plan_reset(include_mono=mono) if builtin
                            else c.plan_preset(path, force=a.force, include_mono=mono))
                return 0
            if builtin:
                results = c.reset_to_defaults(include_mono=mono)
                results = c.wait([r.ticket for r in results], 10.0)
                ok = all(r.status == "written" and r.kept is not False for r in results)
            else:
                r = c.load_preset(path, force=a.force, include_mono=mono)
                results, ok = list(r.results), r.ok
                for label, names in (("already at the preset's value", r.unchanged), ("left to auto", r.left_out),
                                     ("no effect on the mono sensor (--include-mono writes them)", r.skipped_mono)):
                    if names:
                        print(f"  {label}, not written:", ", ".join(names))
            _print_results(results)
        print(f"control writes by this process: {write_counts()['_total']}"
              + (" (simulated controls are not counted)" if a.synthetic else ""))
        return 0 if ok else 1
    except (ValueError, LookupError, OSError, RuntimeError) as exc:   # PresetError is a ValueError
        print(exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
