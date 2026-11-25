"""Weevia S1 Viewer — a window that shows in 5 seconds whether the camera works, with no install; saves and records left/right frames.

    python -m opticmix_vision.viewer                                            # Weevia S1 (found by name, 1600x800 MJPG 90)
    python -m opticmix_vision.viewer --device 1 --layout sbs --lr-order first_is_right --width 1600 --height 800 --fps 90 --fourcc MJPG
    python -m opticmix_vision.viewer --synthetic                                # no hardware
    python -m opticmix_vision.viewer --recording captures/20261124_101500_000123.avi   # play a recording back
    python -m opticmix_vision.viewer --lossless                                 # v records lossless (FFV1 .mkv)
    python -m opticmix_vision.viewer --preset bench                             # load a preset before the first frame

Keys:  q/ESC quit · s save left/right PNG (--out) · v start/stop recording (--out) · l/L LED −/+ · e/E exposure −/+ · g/G gain −/+
       a auto-exposure toggle · n save the controls as a new preset · p next preset, then the camera's defaults
       (shows what it would change) · P load that preset · c show/hide the installed calibration file · h help
Playing a recording:  space pause/resume (at the end: play again) · ,/. one frame back/forward · [/] one second back/forward
                      · -/+ half/double speed · the bar under the image seeks. At the end the window stays on the last
                      frame (q quits); --loop starts again instead.
s, v, n, p, P, a, c, h and space act once per press: a key held down repeats, and the same key again within 0.6 s
is ignored (the window says so once per press) — after a short pause it acts again. A keyboard repeat delay set
longer than 0.6 s in the OS lets one repeat through. The LED / exposure / gain and seek keys go on while held.

v writes the same files as examples/record_video.py: a video with both eyes side by side (left eye in the left half),
a CSV of frame, host_ns and a JSON of the mode and control values next to it (recording.Recorder); LED / exposure /
gain changes made while recording are listed in the JSON. The default MJPG changes pixel values; --lossless keeps the
values the SDK delivered (FFV1) but takes longer per frame and may not keep up with 90 fps — the REC line counts the
frames missed since recording started. A thread encodes while the window draws (Recorder queue_frames=REC_QUEUE):
a frame that finds its queue full is not recorded, and the REC line, the JSON (dropped_frames) and the message when
recording stops count it (within the missed estimate, not on top of it); stopping (v, q, the end) writes the frames
still queued first. s writes a JSON next to
each PNG pair too. The JSON of s and v says where its control values come from and when the viewer read them
(controls_source "given", controls_read_at).
The top line shows the frame index, frames per second and the late / missed frame estimates (from host times),
and "repeated N" once a frame's two eyes both repeated the previous image (Device.repeated_frames; run without a
camera only).
The "saturated" line is each eye's share of pixels at 255 (--saturation-level), for setting the LED and exposure.
Every control is read from the camera once, when the viewer opens (controls.snapshot()). The LED / exposure / gain
line (it ends with "(as opened + viewer's writes)") and the controls in the JSON of s and v are those values with
the viewer's own writes (the value read back after each); a control whose write was refused is read once again, and n, p and P read every control. A change made
by another program shows when the viewer is opened again.

Presets (opticmix_vision.presets, the files the explorer and `python -m opticmix_vision.presets` use): n reads every
control into a new file in presets.store_dir() (--preset-name, default viewer_<time>; an existing file is not
replaced). p steps through the presets saved from this camera's model, then "defaults" — the camera's own
defaults (Controls.plan_reset / reset_to_defaults, as the explorer's "Reset to defaults" and `presets load
defaults`: auto/manual modes left alone) — and shows what loading one would change, writing nothing; P then writes
it in one set_many — the whole file is checked first and nothing is written if any value does not fit. The
results (written, refused, or a value the camera did not keep) appear on screen and in the
log; a load made while recording goes into its JSON when P is pressed. A preset from another model is shown with a
warning; P then loads it anyway.

Calibration (c): the installed calibration file (`python -m opticmix_vision.calib add <file>`, one file) — its
serial, file, each eye's model, size, fx fy cx cy and distortion, and the baseline, t and rotation angle between the
eyes. Read from the file only; the camera is not read, and the file is not matched to it (no USB serial). c again
hides it.

Headless mode (--headless) runs the same loop without a window — for tests and CI.
The real-camera path (VideoCapture) cannot be verified without hardware; the synthetic source verifies only the loop, keys and saving.
"""

from __future__ import annotations

import argparse
import sys
import textwrap
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import numpy as np


from . import presets
from .controls import ControlStateError
from .device import EndOfRecording, StereoFrame
from .recording import LOSSLESS_CODEC, Recorder, _now, write_capture_info

__all__ = ["ViewerState", "compose_view", "run_viewer", "main", "KEY_HELP"]

# While a key is held only the target value accumulates; after this much quiet it is written once. The write gap is
# 12 ms (2026-09-28), so key repeat (~30 per second) must not turn straight into writes. It has to be longer than the
# Windows key-repeat start delay (about 0.5 s by default) so nothing is written midway while the key is held down.
SETTLE_S = 0.6

# OpenCV's waitKey reports no key release, so a key held down in the window comes again and again (Windows: after
# about 0.5 s, then about 30 times a second). These keys act once per press: the same key again less than REPEAT_S
# after it last came is taken as still held and ignored (v held down started and stopped 7 recordings, 2026-11-24).
# p reads every control and a / P write. Longer than the key-repeat start delay, like SETTLE_S; a keyboard delay set
# longer than this in the OS lets one repeat through.
_ONCE_KEYS = frozenset(map(ord, "sSvVnpPaAcChH "))
REPEAT_S = 0.6
_clock = time.monotonic                                  # when a window key came; a test replaces it

KEY_HELP = ("q/ESC quit | s save | v record | l/L LED -/+ | e/E exposure -/+ | g/G gain -/+ | a auto-exposure"
            " | n save preset | p/P choose/load preset or defaults | c calibration file"
            " | recording: space pause ,/. step [/] -/+1 s -/+ speed"
            " | LED/exp/gain line: as opened + viewer's writes | h help")
_PLAY_KEYS = frozenset(map(ord, " ,.[]-+="))
_CONTROL_KEYS = frozenset(map(ord, "lLeEgGaAnpP"))     # a recording has none of these
# What `python -m opticmix_vision.presets` catches (PresetError is a ValueError, ControlsUnavailable a RuntimeError):
# the viewer shows the reason and keeps going.
_PRESET_ERRORS = (ValueError, LookupError, OSError, RuntimeError)
# The overlay's control line: label, control name, get_prop name (a source without camera controls)
_HUD = (("LED", "backlight_compensation", "CAP_PROP_BACKLIGHT"), ("exp", "exposure", "CAP_PROP_EXPOSURE"),
        ("gain", "gain", "CAP_PROP_GAIN"))
_HUD_SOURCE = "  (as opened + viewer's writes)"   # the line is not a live reading of the camera (_current)
_CHAR_PX = 9        # overlay text (FONT_HERSHEY_SIMPLEX 0.5): 8.4 to 8.9 px per character measured on these lines
# v: a thread encodes while the window draws (Recorder queue_frames). The queue holds this many frames (64 of
# 1600x800 gray: about 82 MB); a frame that finds it full is dropped and counted (REC line, JSON, stop message).
# Drawing and encoding in one loop recorded 392 frames with about 130 missed on a camera (2026-11-24).
REC_QUEUE = 64


@dataclass
class ViewerState:
    led_step: float = 8.0
    exposure_step: float = 1.0
    gain_step: float = 8.0
    saved: int = 0
    show_help: bool = False
    key_help: str = KEY_HELP
    last_error: str = ""
    messages: list[str] = field(default_factory=list)
    saturation_level: int = 255             # the overlay counts pixel values at this level or above as saturated
    preset_name: str | None = None          # n saves under this name; None: viewer_<time>
    _pending: dict[str, int] = field(default_factory=dict)
    _pending_at: float = 0.0
    _written: list[tuple[str, Any, str]] = field(default_factory=list)   # (name, value, status) since run_viewer looked
    _sent: dict[str, tuple[int, int]] = field(default_factory=dict)       # name -> (target, ticket) of the last write
    _known: dict[str, Any] = field(default_factory=dict)  # control name -> the value read once, or written since
    _preset: Path | str | None = None      # chosen with p (or --preset); presets.BUILTIN: the defaults; P writes it
    _preset_label: str = ""
    _preset_force: bool = False             # saved from another model: P loads it anyway (the explorer's 'Load anyway')
    _preset_i: int = -1
    _loading: Any = None                    # (label, controls, results, PresetLoad) until every write has its result
    _opened: bool = False                   # every control was read once (_known_values)
    _read_at: str | None = None             # when (ISO 8601): the JSON's controls_read_at
    _calib_lines: list[str] = field(default_factory=list)   # c: the calibration file, shown until c again

    def _say(self, msg: str, error: bool = False) -> None:
        """On screen and in the log (stderr for an error). ASCII: a cp949 console and cv2.putText print no other."""
        if error:
            self.last_error = msg
        self.messages.append(msg)
        print(f"[viewer] {msg}", file=sys.stderr if error else sys.stdout)

    def _controls_of(self, dev: Any) -> Any:
        """dev.controls, or None with the reason on screen."""
        try:
            c, why = getattr(dev, "controls", None), "this source has no camera controls"
        except Exception as exc:                      # Device.controls raises for a recording or a closed camera
            c, why = None, str(exc)
        if c is None:
            self._say(f"presets: {why}", error=True)
        return c

    def save_preset(self, dev: Any) -> Path | None:
        """n: every readable control into a new preset in presets.store_dir(), named preset_name or viewer_<time>.
        Reads only; an existing file is not replaced. Returns the file, or None (the reason is on screen)."""
        c = self._controls_of(dev)
        if c is None:
            return None
        name = self.preset_name or time.strftime("viewer_%Y%m%d_%H%M%S")
        try:
            if name == presets.BUILTIN:
                raise presets.PresetError(f"{name!r} is the built-in preset; choose another --preset-name")
            path = presets.preset_path(name)
            if path.exists():
                raise presets.PresetError(f"{path} exists and is not replaced; choose another --preset-name")
            c.save_preset(path, name=name)
        except _PRESET_ERRORS as exc:
            self._say(f"preset not saved: {exc}", error=True)
            return None
        self._say(f"saved preset {name} to {path}")
        return path

    def choose_preset(self, dev: Any, path: str | Path | None = None) -> None:
        """p: the next preset saved from this camera's model (presets.list_presets), after the last one the camera's
        own defaults (presets.BUILTIN), or path ("defaults": the defaults), and what loading it would change
        (Controls.plan_preset; Controls.plan_reset for the defaults, as the explorer's Reset to defaults). Writes
        nothing; P loads it. A preset that does not fit this camera (a control it lacks, other units, a value out of
        range) is refused here with the reason."""
        c = self._controls_of(dev)
        if c is None:
            return
        self._preset = None
        label = None
        if path is None:
            found = presets.list_presets(c.model)
            self._preset_i = (self._preset_i + 1) % (len(found) + 1)
            if self._preset_i < len(found):
                info = found[self._preset_i]
                path, label = info.path, f"{info.name} ({self._preset_i + 1}/{len(found)})"
            else:                                     # after the last saved preset: the defaults, then the first again
                if not found:
                    self._say(f"no presets for this camera in {presets.store_dir()} - n saves one")
                self._preset_i, path = -1, presets.BUILTIN
        defaults = path == presets.BUILTIN            # a str; a file named "defaults" comes as a Path
        label = label or (presets.BUILTIN if defaults else Path(path).stem)
        warn, force = "", False
        try:
            if defaults:
                plan, warn = c.plan_reset(), "the camera's own defaults, auto/manual modes left alone; "
            else:
                try:
                    plan = c.plan_preset(path)
                except presets.PresetModelError:
                    plan, force = c.plan_preset(path, force=True), True
                    saved = presets.read_preset(path)["device"].get("model")
                    warn = (f"saved from {saved or 'an unknown model'}, not this camera "
                            f"({c.model or 'model unknown'}); ")
        except _PRESET_ERRORS as exc:
            self._say(f"preset {label} not usable: {exc}", error=True)
            return
        self._preset = presets.BUILTIN if defaults else Path(path)
        self._preset_label, self._preset_force = label, force
        if plan:
            changes = ", ".join(f"{n} {cur!r}->{new!r}" for n, (cur, new) in plan.items())
            self._say(f"preset {label}: {warn}{len(plan)} to write: {changes} - P loads{' anyway' if force else ''}")
        else:
            self._say(f"preset {label}: {warn}already at its values - nothing to write")

    def load_preset(self, dev: Any) -> None:
        """P: writes the preset chosen with p (or --preset) in one set_many (Controls.load_preset: the whole file is
        checked first; nothing is written if any value does not fit), or, for the defaults, Controls.reset_to_defaults
        (as the explorer's Reset to defaults). Does not wait; poll() reports the results.
        Like the keys, the request goes to a recording's JSON when it is made (run_viewer): a "preset" entry with the
        file name, then each control queued with its status at the request."""
        if self._preset is None:
            self._say("no preset chosen - p chooses one")
            return
        c = self._controls_of(dev)
        if c is None:
            return
        self.commit(dev, force=True)                  # a key change still waiting goes first, so the preset's values win
        label = self._preset_label
        try:
            if self._preset == presets.BUILTIN:
                results, mono, name = c.reset_to_defaults(), (), presets.BUILTIN
            else:
                r = c.load_preset(self._preset, force=self._preset_force, timeout=0)
                results, mono, name = r.results, r.skipped_mono, Path(self._preset).name
        except _PRESET_ERRORS as exc:
            self._say(f"preset {label} not loaded: {exc}", error=True)
            return
        if not results:
            self._say(f"preset {label}: already at its values - nothing written")
            return
        self._written.append(("preset", name, "loaded"))
        self._written.extend((x.name, x.requested, x.status) for x in results)
        self._sent.update((x.name, (x.requested, x.ticket)) for x in results if x.status == "pending")
        self._known.update((x.name, x.requested if x.readback is None else x.readback)
                           for x in results if x.status in ("written", "skipped_same"))
        self._loading = (label, c, list(results), mono)
        self._say(f"loading preset {label}: {len(results)} writes")
        self.poll()

    def poll(self, timeout: float | None = None) -> None:
        """Reports a preset load once every write has its final result, on screen and in the log (the recording's
        JSON got the request from load_preset). Only looks; with timeout, waits up to that long first."""
        if self._loading is None:
            return
        label, c, results, skipped = self._loading
        tickets = [x.ticket for x in results if x.status == "pending"]
        if tickets:
            final = c.wait(tickets, timeout) if timeout is not None else [c.result(t) for t in tickets]
            done = {x.ticket: x for x in final if x is not None}
            results = [done.get(x.ticket, x) for x in results]
            if timeout is None and any(x.status == "pending" for x in results):
                self._loading = (label, c, results, skipped)
                return
        self._loading = None
        bad = [f"{x.name} {x.status}" + (f" ({x.error})" if x.error else "")
               for x in results if x.status not in ("written", "skipped_same")]
        bad += [f"{x.name}: camera kept {x.readback!r}, not {x.requested!r}" for x in results if x.kept is False]
        mono = f"; {len(skipped)} mono-only not written" if skipped else ""
        if bad:
            self._say(f"preset {label}: " + ", ".join(bad) + mono, error=True)
        else:
            self._say(f"preset {label} loaded: {len(results)} written{mono}")
        presets._print_results(results)

    def show_calibration(self, dev: Any) -> None:
        """c: the installed calibration file as a few lines on screen and in the log; c again hides them. The file is
        dev.calibration (Device.calibration: the one installed file, its eye size checked against the mode), or
        calib.resolve() for a source without it. The camera reports no USB serial, so the file is not matched to it.
        Reads the file only: no camera read, no write. With no file, two files, or another eye size, the lines are
        what to do (calib.CalibLookupError)."""
        if self._calib_lines:
            self._calib_lines = []
            return
        from . import calib
        try:
            cal = getattr(dev, "calibration", None) or calib.resolve()
            have = calib.installed()
            where = calib.store_dir() / f"{have[0]}.json" if len(have) == 1 else calib.store_dir()
            d = cal.device
            lines = [f"calibration {d.get('serial', '-')}" + (f" ({d['model']})" if d.get("model") else "")
                     + f"  file {where} - installed file, not matched to this camera (no USB serial)"]
            for side, e in (("left ", cal.left), ("right", cal.right)):
                lines.append(f"{side}  {e.model} {e.size[0]}x{e.size[1]}  fx {e.fx:.2f}  fy {e.fy:.2f}  cx {e.cx:.2f}"
                             f"  cy {e.cy:.2f}  D {' '.join(f'{k:.4g}' for k in e.D)}")
            R = cal.extrinsics.R                      # rotation angle from the trace of R
            angle = float(np.degrees(np.arccos(np.clip((np.trace(R) - 1.0) / 2.0, -1.0, 1.0))))
            lines.append(f"baseline {cal.baseline_mm:.2f} mm  t {' '.join(f'{v:.2f}' for v in cal.extrinsics.t)} mm"
                         f"  R {angle:.3f} deg  (left to right; c hides)")
        except (LookupError, ValueError, OSError) as exc:   # CalibLookupError says what to do
            lines = textwrap.wrap(f"calibration: {exc}", 100)
        self._calib_lines = lines
        for line in lines:
            print(f"[viewer] {line}")

    def _read_once(self, dev: Any, name: str, prop: str) -> Any:
        """A source without camera controls: get_prop the first time, then what the viewer wrote (_bump)."""
        if name not in self._known:
            self._known[name] = dev.get_prop(prop)
        return self._known[name]

    def _bump(self, dev: Any, name: str, prop: str, delta: float) -> None:
        try:
            v = self._read_once(dev, name, prop)
            ok = dev.set_prop(prop, v + delta)
            if ok:
                self._known[name] = v + delta
            self.messages.append(f"{prop.replace('CAP_PROP_', '')} {v:g} -> {v + delta:g}" + ("" if ok else " (rejected)"))
        except Exception as exc:                      # the viewer keeps going even if the device refuses the control
            self.last_error = f"{prop}: {type(exc).__name__}: {exc}"
            self.messages.append(self.last_error)

    def _current(self, controls: Any, name: str) -> Any:
        """The value a key goes on from and the overlay shows: what the viewer knows, not a fresh read. The camera is
        read once the first time (run_viewer: the snapshot when it opens; otherwise controls.get); after that only
        the viewer's own writes change it — while the last write it asked for (a key, a, a preset) is queued, its
        value; once done, the value read back right after it (WriteResult.readback, so a value the camera did not keep
        shows), or, refused or cancelled, one read again.
        Reading the controls on every frame while streaming went together with a stopped eye on a camera (2026-11-24)."""
        sent = self._sent.get(name)
        if sent is not None:
            r = controls.result(sent[1])              # the write queue's record; reads no device
            if r is None:
                return sent[0]
            del self._sent[name]
            if r.status in ("written", "skipped_same"):
                self._known[name] = sent[0] if r.readback is None else r.readback
            else:
                self._known.pop(name, None)
        if name not in self._known:
            self._known[name] = controls.get(name)
        return self._known[name]

    def _known_values(self, dev: Any) -> dict[str, Any] | None:
        """Every control as the viewer knows it, for the JSON of s and v: one controls.snapshot() the first time
        (run_viewer: when it opens, which also gives the overlay its values), then the viewer's own writes as
        _current gives them. Reads the camera only that first time (and, as the overlay does, once after a refused
        write). None for a source without camera controls: capture_info then says why, as before."""
        try:
            c = getattr(dev, "controls", None)
        except Exception:                             # Device.controls raises for a recording or a closed camera
            return None
        if c is None:
            return None
        if not self._opened:
            self._opened = True
            try:
                for name, v in c.snapshot().items():
                    self._known.setdefault(name, v)    # a write asked for before (--preset) keeps its value
                self._read_at = _now()
            except Exception:                         # noqa: BLE001 - the JSON then has what the overlay reads
                pass
        out: dict[str, Any] = {}
        for name in dict.fromkeys([*self._known, *self._sent]):
            try:
                out[name] = self._current(c, name)
            except Exception:                         # noqa: BLE001 - a refused write read again, and failing
                self._known[name] = None
        return {n: v for n, v in out.items() if v is not None}

    def _hud(self, source: Any) -> list[Any]:
        """LED, exposure and gain for the overlay, from _current (or _read_once without controls). A value that
        cannot be read is None and is not tried again until the viewer writes it. Exposure is 'auto' while the viewer
        knows auto exposure is on (the camera then sets the exposure itself)."""
        ae = self._sent["auto_exposure"][0] if "auto_exposure" in self._sent else self._known.get("auto_exposure")
        out: list[Any] = []
        for _, name, prop in _HUD:
            if name == "exposure" and ae == "auto":
                out.append("auto")
                continue
            try:
                c = getattr(source, "controls", None)
                out.append(self._current(c, name) if c is not None else self._read_once(source, name, prop))
            except Exception:                         # noqa: BLE001 — no such control here: show '?'
                self._known.setdefault(name, None)
                out.append(None)
        return out

    def _nudge(self, dev: Any, name: str, delta: float, legacy_prop: str, now: float | None = None) -> None:
        """Moves one control by delta, only accumulating the target value. commit() writes once after the key stops."""
        controls = getattr(dev, "controls", None)
        if controls is None:
            self._bump(dev, name, legacy_prop, delta)
            return
        try:
            if name == "exposure" and self._current(controls, "auto_exposure") == "auto":
                self.messages.append("auto-exposure is on — press a to switch to manual first")
                return
            base = self._pending.get(name)
            if base is None:
                base = self._current(controls, name)
                if base is None:
                    raise ValueError(f"{name} is not readable")
            target = int(round(base + delta))
            self._pending[name] = target
            self._pending_at = time.monotonic() if now is None else now
            self.messages.append(f"{name} {base:g} -> {target:g} (writes when you stop)")
        except Exception as exc:                      # the viewer keeps going even if the device refuses the control
            self.last_error = f"{name}: {type(exc).__name__}: {exc}"
            self.messages.append(self.last_error)

    def commit(self, dev: Any, now: float | None = None, *, force: bool = False) -> None:
        """Writes the accumulated target value once, when it has been quiet for SETTLE_S after the last key, or with force."""
        if not self._pending:
            return
        t = time.monotonic() if now is None else now
        if not force and t - self._pending_at < SETTLE_S:
            return
        pending, self._pending = self._pending, {}
        controls = getattr(dev, "controls", None)
        for name, target in pending.items():
            try:
                r = controls.set(name, target)
                if r.status == "skipped_same":
                    self._known[name] = target
                    self.messages.append(f"{name} {target:g} (no change)")
                elif r.status in ("written", "pending", "superseded"):
                    self._written.append((name, target, r.status))
                    self._sent[name] = (target, r.ticket)
            except ControlStateError:
                self.messages.append("auto-exposure is on — press a to switch to manual first")
            except Exception as exc:                  # the viewer keeps going even if the device refuses the control
                self.last_error = f"{name}: {type(exc).__name__}: {exc}"
                self.messages.append(self.last_error)

    def _play_key(self, pb: Any, key: int) -> None:
        """Pause, step, jump and speed keys for a recording (`Device.playback`)."""
        try:
            if key == ord(" "):
                if pb.status == "ended":
                    pb.seek(0)
                    pb.resume()
                else:
                    (pb.resume if pb.paused else pb.pause)()
                self.messages.append("paused" if pb.paused else "playing")
            elif key in (ord(","), ord(".")):
                pb.pause()
                pb.seek(pb.position + (1 if key == ord(".") else -1))
            elif key in (ord("["), ord("]")):
                pb.seek(seconds=pb.position_s + (1.0 if key == ord("]") else -1.0))
            else:
                faster = key in (ord("+"), ord("="))
                pb.set_speed(min(16.0, pb.speed * 2) if faster else max(1 / 16, pb.speed / 2))
                self.messages.append(f"speed {pb.speed:g}x")
        except (OSError, ValueError) as exc:
            self.last_error = f"playback: {exc}"
            self.messages.append(self.last_error)

    def handle_key(self, key: int, dev: Any, now: float | None = None) -> str | None:
        """Handles one key. Returns 'quit' | 'save' | 'record' | None. On quit, pending changes are written before exiting."""
        pb = getattr(dev, "playback", None)
        if pb is not None and key in _PLAY_KEYS:
            self._play_key(pb, key)
            return None
        if key in (ord("q"), ord("Q"), 27):
            self.commit(dev, now, force=True)
            return "quit"
        if key in (ord("s"), ord("S")):
            return "save"
        if key in (ord("v"), ord("V")):
            return "record"
        if pb is not None and key in _CONTROL_KEYS:  # Device.controls raises for a recording
            self.messages.append("a recording has no camera controls (LED, exposure, gain, presets); the values when"
                                 " it was recorded are in its .json (controls)")
            return None
        if key == ord("n"):
            self.save_preset(dev)
        elif key == ord("p"):
            self.choose_preset(dev)
        elif key == ord("P"):
            self.load_preset(dev)
        elif key == ord("l"):
            self._nudge(dev, "backlight_compensation", -self.led_step, "CAP_PROP_BACKLIGHT", now)
        elif key == ord("L"):
            self._nudge(dev, "backlight_compensation", +self.led_step, "CAP_PROP_BACKLIGHT", now)
        elif key == ord("e"):
            self._nudge(dev, "exposure", -self.exposure_step, "CAP_PROP_EXPOSURE", now)
        elif key == ord("E"):
            self._nudge(dev, "exposure", +self.exposure_step, "CAP_PROP_EXPOSURE", now)
        elif key == ord("g"):
            self._nudge(dev, "gain", -self.gain_step, "CAP_PROP_GAIN", now)
        elif key == ord("G"):
            self._nudge(dev, "gain", +self.gain_step, "CAP_PROP_GAIN", now)
        elif key in (ord("a"), ord("A")):
            try:
                controls = getattr(dev, "controls", None)
                if controls is not None:
                    new = "manual" if self._current(controls, "auto_exposure") == "auto" else "auto"
                    r = controls.set("auto_exposure", new)
                    if r.status in ("written", "pending", "superseded"):
                        self._written.append(("auto_exposure", new, r.status))
                        self._sent["auto_exposure"] = (new, r.ticket)
                    self.messages.append(f"auto-exposure {'on' if new == 'auto' else 'off'}")
                else:
                    cur = self._read_once(dev, "auto_exposure", "CAP_PROP_AUTO_EXPOSURE")
                    new_n = 0.25 if cur >= 0.5 else 0.75          # DirectShow convention: 0.75 auto / 0.25 manual
                    if dev.set_prop("CAP_PROP_AUTO_EXPOSURE", new_n):
                        self._known["auto_exposure"] = new_n
                    self.messages.append(f"auto-exposure {'off' if new_n == 0.25 else 'on'}")
            except Exception as exc:
                self.last_error = f"AUTO_EXPOSURE: {exc}"
                self.messages.append(self.last_error)
        elif key in (ord("c"), ord("C")):
            self.show_calibration(dev)
        elif key in (ord("h"), ord("H")):
            self.show_help = not self.show_help
        return None


def compose_view(frame: StereoFrame, *, texts: Sequence[str] = (), gap: int = 8, scale: float = 1.0,
                 left: np.ndarray | None = None, right: np.ndarray | None = None) -> np.ndarray:
    """Left|right side by side (BGR). texts go line by line in the top-left. If left/right are given, those are drawn (images changed by view)."""
    import cv2
    L = frame.left if left is None else left
    R = frame.right if right is None else right
    if L.ndim == 2:
        L = cv2.cvtColor(L, cv2.COLOR_GRAY2BGR)
    if R.ndim == 2:
        R = cv2.cvtColor(R, cv2.COLOR_GRAY2BGR)
    h = max(L.shape[0], R.shape[0])
    sep = np.full((h, gap, 3), 40, dtype=np.uint8)
    canvas = np.hstack([L, sep, R])
    if scale != 1.0:
        canvas = cv2.resize(canvas, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    y = 18
    for t in texts:
        cv2.putText(canvas, t, (6, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(canvas, t, (6, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (80, 255, 80), 1, cv2.LINE_AA)
        y += 18
    return canvas


def _imwrite(path: Path, img: np.ndarray) -> None:
    """cv2.imwrite returns False and writes no file for a non-ASCII (e.g. Korean) path (measured on Windows).
    Writes with imencode -> tofile and raises on failure; it must never silently count as 'saved'."""
    import cv2
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise OSError(f"PNG encoding failed: {path.name}")
    buf.tofile(str(path))
    if not path.exists() or path.stat().st_size == 0:
        raise OSError(f"save failed (no file was written): {path}")


def _save_pair(frame: StereoFrame, out_dir: Path, left: np.ndarray, right: np.ndarray,
               source: Any = None, controls: dict[str, Any] | None = None,
               controls_read_at: str | None = None) -> tuple[Path, Path]:
    """left/right PNG and, next to them, the capture info JSON (mode, controls - the given ones, if any, read at
    controls_read_at - this frame's index and host_ns)."""
    if out_dir.exists() and not out_dir.is_dir():
        raise OSError(f"save path is not a directory: {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{time.strftime('%Y%m%d_%H%M%S')}_{frame.index:06d}"
    lp, rp = out_dir / f"{stem}_left.png", out_dir / f"{stem}_right.png"
    _imwrite(lp, left); _imwrite(rp, right)
    write_capture_info(out_dir / f"{stem}.json", source, controls=controls, controls_read_at=controls_read_at,
                       frame={"index": frame.index, "host_ns": frame.host_ns, "left": lp.name, "right": rp.name})
    return lp, rp


def _saturated_pct(eye: np.ndarray, level: int = 255) -> float:
    """Percent of the pixel values at level or above. Every pixel, every frame: about 0.05 ms for an 800x800 eye."""
    return 100.0 * np.count_nonzero(eye >= level) / eye.size


def _overlay(frame: StereoFrame, source: Any, state: ViewerState, rec: Recorder | None, loop_fps: float,
             eye: np.ndarray, extra: Sequence[str], cols: int = 100) -> list[str]:
    """The text lines drawn on the window. The calibration, the messages and the help wrap at cols characters."""
    fps = getattr(source, "actual_fps", None)
    line = f"frame {frame.index}  fps {loop_fps if fps is None else fps:5.1f}"
    if getattr(source, "late_frames", None) is not None:
        line += f"  late {source.late_frames}  missed~{source.estimated_missed_frames}"
    if getattr(source, "repeated_frames", None):        # images the same as the frame before, in both eyes
        line += f"  repeated {source.repeated_frames}"
    texts = [line + f"  {eye.shape[1]}x{eye.shape[0]}/eye", *extra]
    pb = getattr(source, "playback", None)
    if pb is not None:              # fps / late / missed above describe the recording; this is the playback
        status = "" if pb.status == "playing" else f"  {pb.status.upper()}"
        texts.insert(0, f"PLAY {pb.position}/{max(pb.frame_count - 1, 0)}  {pb.position_s:.2f}/{pb.duration_s:.2f} s"
                        f"  {pb.speed:g}x{status}{'  loop' if pb.loop else ''}  shown {loop_fps:.1f}/s")
    if rec is not None:             # frames written so far; missed~ includes those the full write queue dropped
        texts.insert(0, f"REC {rec.path.name}  {rec.frames} frames{'' if rec.compressed else '  lossless'}"
                        f"  missed~{rec.estimated_missed_frames} since start (dropped {rec.dropped_frames})"
                        "  (v stops)")
    if pb is None:                  # a file has no LED or exposure
        vals = state._hud(source)   # read once when the viewer opens, then its own writes
        if any(v is not None for v in vals):
            texts.append("  ".join(f"{label} {'?' if v is None else v if isinstance(v, str) else format(v, 'g')}"
                                   for (label, _, _), v in zip(_HUD, vals)) + _HUD_SOURCE)
    lvl = state.saturation_level              # the frame as received, whatever view draws
    texts.append(f"saturated (>={lvl})  L {_saturated_pct(frame.left, lvl):.2f}%"
                 f"  R {_saturated_pct(frame.right, lvl):.2f}%")
    for t in [*state._calib_lines, *state.messages[-2:]]:   # one line would run off the window
        texts += textwrap.wrap(t, cols) or [t]
    if state.show_help:
        texts += textwrap.wrap(state.key_help, cols, break_on_hyphens=False)
    return texts


def _stopped(rec: Recorder) -> str:
    """The message when a recording stops, with what to do when frames were missed. ASCII: it is also printed,
    and a cp949 console cannot print every character."""
    if not rec.frames:
        return "recorded nothing: no frame came before recording stopped (no file written)"
    msg = f"recorded {rec.frames} frames to {rec.path.name}"
    missed, dropped = rec.estimated_missed_frames, rec.dropped_frames
    if not (missed or dropped):
        return msg
    if dropped > missed:                      # the estimate comes from frame times and can fall below the exact count
        msg += (f"; at least {dropped} frames not recorded (dropped by the write queue; the estimate from the frame"
                f" times is {missed})")
    else:
        msg += f"; about {missed} frames missed while recording"
        if dropped:                           # a dropped frame is a gap in the frame times: the estimate has it
            msg += (f" (estimated from the frame times; the {dropped} frames the write queue dropped are among"
                    " them, not in addition)")
    if 2 * dropped > missed:                  # mostly the encoder falling behind: record_video.py encodes the same
        msg += (". The writing did not keep up: " + ("ask a lower rate with --fps" if rec.compressed
                                                       else "record MJPG (leave out --lossless)")
                + ", or keep frames in memory with examples/capture_dataset_npz.py.")
    else:                                     # v records alongside the preview; record_video.py has no window
        msg += (f". First try examples/record_video.py (it records with no preview window). {rec.missed_advice}")
    return msg


def _finish(rec: Recorder, state: ViewerState, failed: BaseException | None = None) -> None:
    """Closes a recording (the frames still queued are written first) and says how it went: _stopped, or, when
    writing failed (failed, or the error close() raises), the error with the frames written and dropped."""
    try:
        rec.close()
    except Exception as exc:                  # noqa: BLE001 — any writing error: say it, and the window still closes
        failed = failed or exc                # the first error says why
    if failed is None:
        state.messages.append(_stopped(rec))
        print(f"[viewer] {state.messages[-1]}")
    else:
        state._say(f"recording failed: {failed} ({rec.frames} frames written to {rec.path.name}, "
                   f"{rec.dropped_frames} dropped)", error=True)


class _SeekBar:
    """A trackbar under the window: follows the playback position, and seeks when it is dragged."""

    NAME = "frame"

    def __init__(self, cv: Any, window: str, pb: Any) -> None:
        self.cv, self.window, self.pb = cv, window, pb
        cv.createTrackbar(self.NAME, window, 0, max(pb.frame_count - 1, 1), lambda _v: None)
        self.shown = 0

    def sync(self, index: int) -> None:
        """After waitKey: a bar moved by the user seeks there; otherwise the bar moves to the frame shown."""
        v = self.cv.getTrackbarPos(self.NAME, self.window)
        if v != self.shown:
            self.pb.seek(v)
            self.shown = v
        elif index != self.shown:
            self.cv.setTrackbarPos(self.NAME, self.window, index)
            self.shown = index


View = Callable[[StereoFrame, "ViewerState"], "tuple[np.ndarray, np.ndarray, list[str]]"]


def run_viewer(source: Any, *, headless: bool = False, max_frames: int | None = None,
               out_dir: str | Path | None = None, key_feed: Iterable[int] | None = None,
               window: str = "Weevia S1 Viewer", scale: float = 1.0,
               state: ViewerState | None = None, view: View | None = None, wait_ms: int = 1,
               lossless: bool = False) -> dict[str, Any]:
    """Loop: read -> view -> compose -> show -> key.

    state: key-handling state (default ViewerState()). view: frame -> (left, right, extra text lines); it changes only what is drawn.
    's' always saves, and 'v' always records, the frame as received (frame.left/right), regardless of view.
    wait_ms: how long the window waits for a key each frame. lossless: 'v' records with FFV1 (.mkv) instead of MJPG (.avi).
    The loop ends quietly when a recording reaches its end (EndOfRecording).
    Returns {'frames', 'saved', 'fps', 'last_error'}.
    """
    import cv2
    state = ViewerState() if state is None else state
    out = Path(out_dir) if out_dir is not None else Path("captures")
    keys = iter(key_feed) if key_feed is not None else None
    _EXHAUSTED = object()
    rec: Recorder | None = None
    pb = getattr(source, "playback", None)
    bar: _SeekBar | None = None
    pressed: dict[int, float] = {}                # a _ONCE_KEYS key -> when the window last gave it
    told: set[int] = set()                        # held keys whose repeats the window has said it ignores
    n = 0
    t_prev = time.perf_counter(); fps = 0.0; dt_avg = 0.0

    def known() -> dict[str, Any] | None:         # the controls in the JSON of s and v, without reading the camera
        return state._known_values(source)
    known()                                       # every control once, before the first frame
    try:
        while True:
            fed_key: Any = None
            if headless and keys is not None:
                fed_key = next(keys, _EXHAUSTED)
                if fed_key is _EXHAUSTED:                 # key sequence exhausted = end of scenario (prevents an endless loop)
                    break
            try:
                frame = source.read()
            except EndOfRecording:
                if headless or pb is None or not pb.frame_count:
                    break
                pb.pause()                                # a window stays on the last frame; q quits
                try:
                    frame = source.read()
                except EndOfRecording:
                    break
            n += 1
            now = time.perf_counter()
            dt = now - t_prev; t_prev = now
            if dt > 0:                                    # average the intervals, then invert; averaging 1/dt inflates under jitter
                dt_avg = 0.9 * dt_avg + 0.1 * dt if dt_avg > 0 else dt
                fps = 1.0 / dt_avg
            L, R, extra = (frame.left, frame.right, []) if view is None else view(frame, state)
            paused = getattr(getattr(source, "playback", None), "paused", False)
            if not headless:
                width = (L.shape[1] + 8 + R.shape[1]) * scale          # compose_view: left, gap, right, scaled
                cols = max(20, int(width - 12) // _CHAR_PX)
                texts = _overlay(frame, source, state, rec, fps, L, extra, cols=cols)
                cv2.imshow(window, compose_view(frame, texts=texts, scale=scale, left=L, right=R))
                if pb is not None and bar is None:
                    bar = _SeekBar(cv2, window, pb)
                key = cv2.waitKey(30 if paused else max(1, int(wait_ms))) & 0xFF      # paused: do not spin
                if key == 255:
                    key = -1
                elif key in _ONCE_KEYS:                   # held down, it repeats: once per press (REPEAT_S)
                    t, last = _clock(), pressed.get(key, float("-inf"))
                    pressed[key] = t
                    if t - last < REPEAT_S:
                        if key not in told:           # once per press, not on every repeat
                            told.add(key)
                            state.messages.append(f"{'space' if key == 32 else chr(key)} ignored: same key within "
                                                  f"{REPEAT_S:g} s (held?) - press again after a pause")
                        key = -1
                    else:
                        told.discard(key)
                if bar is not None:
                    bar.sync(frame.index)
            else:
                key = fed_key if keys is not None else -1
            action = state.handle_key(key, source) if key not in (-1, 255) else None
            state.commit(source)                                  # write once if SETTLE_S has passed since the key stopped
            state.poll()                                          # a preset's results, once they are all in
            if state._written:                                    # control changes made while recording go into its JSON
                if rec is not None:
                    for name, value, status in state._written:
                        rec.note_control(name, value, status)
                state._written.clear()
            started = False
            if action == "save":
                # Even if view changed the display, saving uses the frames **as received** (frame.left/right). If changed images
                # were saved, whatever uses those files as input would silently give wrong results (this actually happened).
                try:
                    lp, rp = _save_pair(frame, out, frame.left, frame.right, source, known(), state._read_at)
                except OSError as exc:
                    state.last_error = f"save failed: {exc}"
                    state.messages.append(state.last_error)
                else:
                    state.saved += 1
                    state.messages.append(f"saved {lp.name}" + ("  (as captured)" if view is not None else ""))
            elif action == "record":
                if rec is None:
                    ext = ".mkv" if lossless else ".avi"
                    path = out / f"{time.strftime('%Y%m%d_%H%M%S')}_{frame.index:06d}{ext}"
                    try:
                        # opens the files now; the recording starts with the next frame, since this one was
                        # read before they were open (writing it would put the opening time between frames 0 and 1)
                        rec = Recorder(path, source, codec=LOSSLESS_CODEC if lossless else "MJPG", controls=known,
                                       controls_read_at=state._read_at, queue_frames=REC_QUEUE)
                    except OSError as exc:
                        rec = None
                        state._say(f"recording failed: {exc}", error=True)
                    else:
                        started = True
                        state.messages.append(f"recording {rec.path.name}")
                else:
                    _finish(rec, state)                           # waits for the frames still queued
                    rec = None
            elif action == "quit":
                break
            if rec is not None and not started and not paused:   # a paused recording repeats its frame
                try:
                    rec.write(frame)          # queued for the writing thread (REC_QUEUE), or dropped when it is full
                except Exception as exc:      # noqa: BLE001 — what the writing thread met: e.g. the disk is full,
                    _finish(rec, state, exc)  # the files could not open, or the encoder failed; the viewer goes on
                    rec = None
            if max_frames is not None and n >= max_frames:
                state.commit(source, force=True)
                break
    finally:
        state.poll(timeout=2.0)                                   # a preset still loading: its results go to the log
        if rec is not None:
            _finish(rec, state)
        if not headless:
            try:
                cv2.destroyWindow(window)
            except Exception:
                pass
    return {"frames": n, "saved": state.saved, "fps": fps, "last_error": state.last_error}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m opticmix_vision.viewer", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--synthetic", action="store_true", help="synthetic source, no hardware")
    p.add_argument("--recording", type=Path, default=None, help="play a recording (from v or record_video.py), no camera")
    p.add_argument("--loop", action="store_true", help="with --recording: start again after the last frame")
    p.add_argument("--lossless", action="store_true", help="v records lossless (FFV1 .mkv) instead of MJPG (.avi)")
    p.add_argument("--eye-size", default="320x200", help="synthetic eye size WxH")
    p.add_argument("--constants", type=Path, default=None, help=argparse.SUPPRESS)   # bring-up tool output
    p.add_argument("--device", default=None, help="camera index (or path)")
    p.add_argument("--backend", default=None, choices=("any", "dshow", "msmf"))
    p.add_argument("--layout", default=None, choices=("sbs", "tb", "plane_pack"))
    p.add_argument("--lr-order", default=None, choices=("first_is_left", "first_is_right"))
    p.add_argument("--width", type=int, default=None); p.add_argument("--height", type=int, default=None)
    p.add_argument("--fps", type=float, default=None); p.add_argument("--fourcc", default=None)
    p.add_argument("--out", type=Path, default=Path("captures"), help="folder for the s and v keys")
    p.add_argument("--scale", type=float, default=1.0)
    p.add_argument("--headless", action="store_true")
    p.add_argument("--frames", type=int, default=None, help="read this many frames, then exit")
    p.add_argument("--preset", default=None, metavar="NAME|FILE",
                   help="load this preset before the first frame (python -m opticmix_vision.presets list)")
    p.add_argument("--preset-name", default=None, metavar="NAME",
                   help="n saves the controls as this preset (default viewer_<time>)")
    p.add_argument("--saturation-level", type=int, default=255, metavar="N",
                   help="the overlay counts pixel values at N or above as saturated (1-255)")
    return p


def _open_source(a: argparse.Namespace) -> Any:
    if a.recording is not None:
        if a.synthetic or a.device is not None:
            raise SystemExit("--recording cannot be combined with --synthetic or --device")
        from .device import Device, DeviceConfig
        dev = Device(DeviceConfig.recording(a.recording)).open()     # left|right order comes from the recording format
        dev.playback.real_time = not a.headless                      # at the recorded pace in a window
        dev.playback.loop = a.loop
        return dev
    if a.synthetic:
        from .synthetic import SyntheticStereoSource
        w, h = (int(v) for v in a.eye_size.lower().split("x"))
        return SyntheticStereoSource(eye_size=(w, h), fps=a.fps or 90.0, realtime=not a.headless)
    from .device import Device, DeviceConfig
    over: dict[str, Any] = {}
    for k in ("backend", "layout", "lr_order", "width", "height", "fps", "fourcc"):
        v = getattr(a, k)
        if v is not None:
            over[k] = v
    if a.device is not None:
        over["device"] = int(a.device) if str(a.device).isdigit() else a.device
    if a.device is not None or a.constants is not None:
        over.setdefault("backend", "dshow")
    if a.constants is not None:
        cfg = DeviceConfig.from_bringup_constants(a.constants, **over)
    elif a.device is None:
        # Weevia S1 found by name (in the given --backend's list), measured settings; only the given arguments
        # override them. Its backend is DirectShow (Linux: V4L2) unless --backend says otherwise.
        cfg = DeviceConfig.weevia_s1(**over)
    else:
        if "layout" not in over or "lr_order" not in over:
            raise SystemExit("with --device, also give --layout and --lr-order (the viewer does not guess them)")
        cfg = DeviceConfig(**over)
    return Device(cfg).open()


def serve(a: argparse.Namespace, run: Callable[[Any], dict[str, Any]]) -> int:
    """Opens the camera (or synthetic source) from the arguments, runs run(source), then closes it. Failures print one line instead of a traceback."""
    from .device import DeviceError
    try:
        src = _open_source(a)
    except DeviceError as exc:
        what = "the recording" if getattr(a, "recording", None) is not None else "the camera"
        print(f"[viewer] could not open {what}: {exc}", file=sys.stderr)
        return 2
    try:
        stats = run(src)
    except DeviceError as exc:                      # unplugged or timed out while streaming: one line instead of a traceback
        print(f"[viewer] frame read failed (camera unplugged?): {exc}", file=sys.stderr)
        return 2
    finally:
        try:
            src.close()
        except Exception:
            pass
    print(f"[viewer] frames {stats['frames']} saved {stats['saved']} fps {stats['fps']:.1f}"
          + (f" last_error: {stats['last_error']}" if stats["last_error"] else ""))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")   # so a cp949 console does not crash on characters like '—'
    ap = build_parser()
    a = ap.parse_args(argv)
    if not 1 <= a.saturation_level <= 255:
        ap.error("--saturation-level must be within 1..255")
    preset = None
    if a.preset is not None:
        if a.recording is not None:
            raise SystemExit("--preset needs a camera (or --synthetic); a recording has no camera controls")
        try:
            if a.preset == presets.BUILTIN and not Path(a.preset).is_file():   # as the presets command decides
                raise presets.PresetError(f"{presets.BUILTIN!r} is not loaded at startup: in the window, p steps to "
                                          f"{presets.BUILTIN} and shows what it would change, then P writes it (or "
                                          f"python -m opticmix_vision.presets load {presets.BUILTIN})")
            preset = presets._resolve(a.preset)                  # before opening the camera
        except presets.PresetError as exc:
            print(f"[viewer] {exc}", file=sys.stderr)
            return 2

    def run(src: Any) -> dict[str, Any]:
        st = ViewerState(preset_name=a.preset_name, saturation_level=a.saturation_level)
        if preset is not None:
            st.choose_preset(src, preset)
            if st._preset is not None and not st._preset_force:   # another model's preset waits for P
                st.load_preset(src)                              # README, "Camera controls": write once at startup
        return run_viewer(src, headless=a.headless, max_frames=a.frames, out_dir=a.out, scale=a.scale,
                          lossless=a.lossless, state=st)
    return serve(a, run)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
