"""The Settings window behind the gear button: every control the camera advertises.

Rows are built from controls.describe(): integer with a range -> slider, boolean -> checkbox, menu -> dropdown,
integer without a range -> value only (no made-up slider). Controls with no effect on a monochrome sensor go
in a collapsed group at the bottom.
While dragging, a slider only shows the value and writes **once on release (mouse or arrow keys)**. The write
gap is 12 ms (2026-09-28), so writing on every drag step would turn one drag into dozens of writes.
Writes do not wait: if the guard delays one, 'applying in N s' appears under the value; if the driver refuses it,
red text; if the driver accepted it but the camera did not keep the value, orange 'camera kept N' and the widget
goes back to the camera's actual value. 'Writes this session' at the bottom of the window is the write total for
the whole process.
'Save preset…' writes every readable control to a JSON file (reads only); 'Load preset…' lists the presets saved
from this camera's model (or another file), shows what would change and how many writes that is, and writes only
after 'Load' (one set_many, like 'Reset to defaults…'). A preset from another model offers 'Load anyway'.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog
from typing import Any, Callable

from .. import controls as controls_mod
from .. import presets
from ..controls import DEFAULTS_NOT_WRITTEN, Controls, ControlsUnavailable, ControlStateError, write_counts
from .state import MONO_NOTE, RANGE_NOTE, LOCKS, Row, banner_text, build_rows, locked, reset_lines, value_text
from .theme import BG, LIME, MONO, MONO_SMALL, ORANGE, PANEL, RED, TEXT, TEXT_DIM, TRACK

__all__ = ["SettingsWindow"]

POLL_MS = 200


class SettingsWindow:
    """If already open, brings it to the front instead of making a new one. Rebuilds the rows when the camera is reopened."""

    def __init__(self, parent: tk.Misc, get_controls: Callable[[], Controls | None]) -> None:
        self.get_controls = get_controls
        self.controls: Controls | None = None
        self.vars: dict[str, tk.Variable] = {}
        self.widgets: dict[str, tk.Widget] = {}
        self.value_labels: dict[str, tk.Label] = {}
        self.status_labels: dict[str, tk.Label] = {}
        self.mono_names: set[str] = set()
        self._tickets: dict[int, str] = {}
        self.reset_dialog: tk.Toplevel | None = None
        self.reset_confirm: tk.Button | None = None
        self.preset_dialog: tk.Toplevel | None = None
        self.preset_confirm: tk.Button | None = None
        self.preset_picker: tk.Toplevel | None = None

        self.top = tk.Toplevel(parent)
        self.top.title("Settings")
        self.top.configure(bg=BG)
        self.top.minsize(640, 200)           # height follows content; expanding the monochrome group makes the window taller

        tabs = tk.Frame(self.top, bg=PANEL)
        tabs.pack(fill="x")
        tk.Label(tabs, text="CAMERA", bg=LIME, fg="#111111", font=MONO, padx=24, pady=8).pack(side="left")
        # pack the footer first; packed later, it is cut first when the window is too small and the Reset button disappears
        footer = tk.Frame(self.top, bg=PANEL, padx=18, pady=8)
        footer.pack(side="bottom", fill="x")
        self.banner = tk.Label(self.top, text="", bg="#3a3a1a", fg=TEXT, font=MONO_SMALL, anchor="w",
                               justify="left", wraplength=660, padx=12, pady=6)
        self.body = tk.Frame(self.top, bg=BG, padx=18, pady=12)
        self.body.pack(fill="x")
        self.mono_toggle = tk.Button(self.top, text="No effect expected on this mono sensor ▸", bg=BG, fg=TEXT_DIM,
                                     font=MONO_SMALL, relief="flat", anchor="w", command=self._toggle_mono)
        self.mono_toggle.pack(fill="x", padx=18)
        self.mono_body = tk.Frame(self.top, bg=BG, padx=18, pady=4)
        self.reset_button = tk.Button(footer, text="Reset to defaults…", font=MONO, command=self._open_reset)
        self.reset_button.pack(side="left")
        self.save_preset_button = tk.Button(footer, text="Save preset…", font=MONO, command=self._save_preset)
        self.save_preset_button.pack(side="left", padx=(8, 0))
        self.load_preset_button = tk.Button(footer, text="Load preset…", font=MONO, command=self._open_load_preset)
        self.load_preset_button.pack(side="left", padx=(8, 0))
        self.preset_label = tk.Label(footer, text="", bg=PANEL, fg=TEXT_DIM, font=MONO_SMALL, wraplength=320,
                                     justify="left")   # an error message must not widen the window
        self.preset_label.pack(side="left", padx=(8, 0))
        self.writes_label = tk.Label(footer, text="", bg=PANEL, fg=TEXT_DIM, font=MONO_SMALL)
        self.writes_label.pack(side="right")
        self._rebuild()
        self.top.after(POLL_MS, self._poll)

    # ---- building rows ---------------------------------------------------------------------------
    def _rebuild(self) -> None:
        for frame in (self.body, self.mono_body):
            for w in frame.winfo_children():
                w.destroy()
        for d in (self.vars, self.widgets, self.value_labels, self.status_labels):
            d.clear()
        self._tickets.clear()
        self.controls = self.get_controls()
        infos: dict[str, Any] = {}
        if self.controls is not None:
            try:
                infos = self.controls.describe()
            except ControlsUnavailable:
                self.controls = None
        if self.controls is None:
            tk.Label(self.body, text="No camera open", bg=BG, fg=TEXT_DIM, font=MONO).pack(anchor="w")
            self.banner.pack_forget()
            for b in (self.reset_button, self.save_preset_button, self.load_preset_button):
                b.configure(state="disabled")
            return
        text = banner_text(self.controls.backend_name, self.controls.backend_note)
        if text:
            self.banner.configure(text=text)
            self.banner.pack(fill="x", after=self.top.winfo_children()[0])
        else:
            self.banner.pack_forget()
        for b in (self.reset_button, self.save_preset_button, self.load_preset_button):
            b.configure(state="normal")
        main, mono = build_rows(infos)
        self.mono_names = {r.name for r in mono}
        for row in main:
            self._row(self.body, row, infos[row.name])
        for row in mono:
            self._row(self.mono_body, row, infos[row.name])
        self._sync_locks()

    def _row(self, parent: tk.Misc, row: Row, info: Any) -> None:
        f = tk.Frame(parent, bg=BG)
        f.pack(fill="x", pady=3)
        tk.Label(f, text=row.label, bg=BG, fg=TEXT_DIM if row.mono else TEXT, font=MONO, width=20,
                 anchor="w").pack(side="left")
        cur = info.current
        if row.widget == "slider":
            var: tk.Variable = tk.IntVar(value=int(cur) if isinstance(cur, int) else int(row.minimum or 0))
            w: tk.Widget = tk.Scale(f, from_=row.minimum, to=row.maximum, variable=var, orient="horizontal",
                                    showvalue=False, resolution=max(1, int(info.step or 1)), bg=BG, fg=TEXT,
                                    troughcolor=TRACK, activebackground=LIME, highlightthickness=0,
                                    sliderrelief="flat", borderwidth=0, length=300,
                                    command=lambda _v, n=row.name: self._preview(n))
            w.bind("<ButtonRelease-1>", lambda _e, n=row.name: self._release(n))
            w.bind("<KeyRelease>", lambda _e, n=row.name: self._release(n))
        elif row.widget == "check":
            var = tk.BooleanVar(value=bool(cur))
            w = tk.Checkbutton(f, variable=var, bg=BG, activebackground=BG, selectcolor=PANEL,
                               command=lambda n=row.name: self._apply(n))
        elif row.widget == "menu":
            var = tk.StringVar(value="" if cur is None else str(cur))
            w = tk.OptionMenu(f, var, *row.choices, command=lambda _v, n=row.name: self._apply(n))
            w.configure(bg=PANEL, fg=TEXT, font=MONO_SMALL, highlightthickness=0, width=10)
        else:
            var = tk.StringVar(value=value_text(row.name, cur))
            w = tk.Label(f, text=RANGE_NOTE, bg=BG, fg=TEXT_DIM, font=MONO_SMALL)
        w.pack(side="left", padx=8)
        value = tk.Label(f, text="" if row.widget == "menu" else value_text(row.name, cur), bg=BG, fg=LIME,
                         font=MONO, width=6, anchor="e")
        value.pack(side="left")
        # status line goes under the row, only when there is text; beside it, it is pushed out of the window and not visible
        status = tk.Label(parent, text="", bg=BG, fg=TEXT_DIM, font=MONO_SMALL, anchor="w")
        status.row_frame = f  # type: ignore[attr-defined]
        self.vars[row.name] = var
        self.widgets[row.name] = w
        self.value_labels[row.name] = value
        self.status_labels[row.name] = status
        if row.mono:
            self._status(row.name, MONO_NOTE, TEXT_DIM)

    def _show_value(self, name: str, value: Any) -> None:
        """Value cell; left empty for dropdowns, since their own text is the value."""
        label = self.value_labels.get(name)
        if label is not None:
            label.configure(text="" if isinstance(self.widgets.get(name), tk.OptionMenu) else value_text(name, value))

    def _toggle_mono(self) -> None:
        if self.mono_body.winfo_ismapped():
            self.mono_body.pack_forget()
            self.mono_toggle.configure(text="No effect expected on this mono sensor ▸")
        else:
            self.mono_body.pack(fill="x", after=self.mono_toggle)
            self.mono_toggle.configure(text="No effect expected on this mono sensor ▾")

    # ---- writing ---------------------------------------------------------------------------------
    def _preview(self, name: str) -> None:
        """While dragging: only the value cell follows; nothing is written."""
        if name in self.vars:
            self._show_value(name, self.vars[name].get())

    def _release(self, name: str) -> None:
        """Writes once on release. A locked slider (e.g. exposure during auto exposure) is not written."""
        w = self.widgets.get(name)
        if w is None or str(w.cget("state")) == "disabled":
            return
        self._apply(name)

    def _apply(self, name: str) -> None:
        c = self.controls
        if c is None or name not in self.vars:
            return
        value = self.vars[name].get()
        self._show_value(name, value)
        if name in LOCKS:
            self._sync_locks()
        try:
            r = c.set(name, value)
        except (ControlStateError, ValueError, KeyError) as exc:
            self._status(name, str(exc).strip("'\""), RED)
            return
        except ControlsUnavailable:
            self._rebuild()
            return
        if r.status == "pending":
            self._tickets[r.ticket] = name
        else:
            self._status(name, "", TEXT_DIM)

    def _sync_locks(self) -> None:
        values = {mode: self.vars[mode].get() for mode in LOCKS if mode in self.vars}
        off = locked({k: (bool(v) if k == "auto_white_balance" else v) for k, v in values.items()})
        for _mode, (target, _on) in LOCKS.items():
            w = self.widgets.get(target)
            if w is None or not isinstance(w, tk.Scale):
                continue
            w.configure(state="disabled" if target in off else "normal",
                        troughcolor=BG if target in off else TRACK)          # a locked slider hides its track
            if target == "exposure":
                self._status(target, "switch AUTO EXPOSURE to manual to set exposure" if target in off else "",
                             TEXT_DIM)

    def _status(self, name: str, text: str, color: str) -> None:
        label = self.status_labels.get(name)
        if label is None:
            return
        if not text and name in self.mono_names:
            text = MONO_NOTE
        label.configure(text=text, fg=color)
        if text and not label.winfo_ismapped():
            label.pack(after=label.row_frame, anchor="w", padx=(12, 0))
        elif not text:
            label.pack_forget()

    def _reload(self, name: str) -> None:
        try:
            v = self.controls.get(name) if self.controls is not None else None
        except Exception:  # noqa: BLE001
            return
        if v is not None and name in self.vars:
            self.vars[name].set(v)
            self._show_value(name, v)

    # ---- periodically: pending display, results, write count, whether the camera changed ----------
    def _poll(self) -> None:
        if not self.alive:
            return
        if self.get_controls() is not self.controls:
            self._rebuild()
        c = self.controls
        if c is not None:
            try:
                pend = c.pending()
            except ControlsUnavailable:
                pend = {}
            for name, due in pend.items():
                self._status(name, f"applying in {due:.1f} s" if due > 0.05 else "applying…", TEXT_DIM)
            for ticket, name in list(self._tickets.items()):
                r = c.result(ticket)
                if r is None:
                    continue
                del self._tickets[ticket]
                if r.status == "failed":
                    self._status(name, "rejected by driver", RED)
                    self._reload(name)
                elif r.kept is False:                   # the driver accepted it but the camera did not keep the value
                    self._status(name, f"camera kept {value_text(name, r.readback)}", ORANGE)
                    self._reload(name)
                elif name not in pend:
                    self._status(name, "", TEXT_DIM)
        total = write_counts()["_total"]
        self.writes_label.configure(text=f"Writes this session: {total}",
                                    fg=ORANGE if total >= controls_mod.WARN_AFTER_TOTAL else TEXT_DIM)
        self.top.after(POLL_MS, self._poll)

    # ---- restoring defaults ----------------------------------------------------------------------
    def _open_reset(self) -> None:
        c = self.controls
        if c is None:
            return
        if self.reset_dialog is not None and self.reset_dialog.winfo_exists():
            self.reset_dialog.destroy()
        plan = c.plan_reset()
        lines = reset_lines(plan) if c.writes_defaults else [DEFAULTS_NOT_WRITTEN]
        self.reset_dialog, self.reset_confirm = self._confirm("Reset to defaults", lines, "Reset",
                                                              self._do_reset, bool(plan))

    def _confirm(self, title: str, lines: list[str], ok: str, command: Callable[[], None],
                 enabled: bool) -> tuple[tk.Toplevel, tk.Button]:
        """A dialog that lists what will be written; nothing is written until the OK button."""
        dlg = tk.Toplevel(self.top)
        dlg.title(title)
        dlg.configure(bg=BG, padx=16, pady=12)
        for line in lines:
            tk.Label(dlg, text=line, bg=BG, fg=TEXT, font=MONO, anchor="w").pack(fill="x")
        buttons = tk.Frame(dlg, bg=BG)
        buttons.pack(fill="x", pady=(10, 0))
        confirm = tk.Button(buttons, text=ok, font=MONO, command=command, state="normal" if enabled else "disabled")
        confirm.pack(side="left")
        tk.Button(buttons, text="Cancel", font=MONO, command=dlg.destroy).pack(side="left", padx=8)
        return dlg, confirm

    def _show_queued(self, results: Any) -> None:
        """Widgets take the requested values; the poll reports each result (refused, or 'camera kept N')."""
        for r in results:
            if r.name in self.vars:
                self.vars[r.name].set(r.requested)
                self._show_value(r.name, r.requested)
            if r.status == "pending":
                self._tickets[r.ticket] = r.name
        self._sync_locks()

    def _do_reset(self) -> None:
        c = self.controls
        if c is not None:
            self._show_queued(c.reset_to_defaults())
        if self.reset_dialog is not None:
            self.reset_dialog.destroy()

    # ---- presets ---------------------------------------------------------------------------------
    def _save_preset(self, path: str | None = None) -> None:
        """Reads every control into a JSON file. path=None asks for one, starting in the preset folder."""
        c = self.controls
        if c is None:
            return
        if path is None:
            folder = presets.store_dir()
            folder.mkdir(parents=True, exist_ok=True)
            path = filedialog.asksaveasfilename(parent=self.top, initialdir=str(folder), defaultextension=".json",
                                                filetypes=[("Preset", "*.json")], title="Save preset")
            if not path:
                return
        try:
            saved = c.save_preset(path)
        except (OSError, ControlsUnavailable) as exc:
            self.preset_label.configure(text=f"Not saved: {exc}", fg=RED)
            return
        self.preset_label.configure(text=f"Saved {saved.name}", fg=TEXT_DIM)

    def _open_load_preset(self, path: str | None = None, force: bool = False) -> None:
        """Without a path: the presets saved from this camera's model, and 'Other file…'. With one: what loading it
        would change. Writes nothing until 'Load'. A preset from another model offers 'Load anyway' (force)."""
        c = self.controls
        if c is None:
            return
        if path is None:
            self._open_picker()
            return
        for d in (self.preset_picker, self.preset_dialog):
            if d is not None and d.winfo_exists():
                d.destroy()
        try:
            plan = c.plan_preset(path, force=force)
        except presets.PresetModelError as exc:
            self.preset_dialog, self.preset_confirm = self._confirm(
                "Load preset", [str(exc)], "Load anyway", lambda: self._open_load_preset(path, force=True), True)
            return
        except (presets.PresetError, OSError, ControlsUnavailable) as exc:
            self.preset_label.configure(text=f"Not loaded: {exc}", fg=RED)
            return
        lines = reset_lines(plan, nothing="Everything is already at the preset's values.")
        self.preset_dialog, self.preset_confirm = self._confirm(
            "Load preset", lines, "Load", lambda: self._do_load_preset(path, force), bool(plan))

    def _open_picker(self) -> None:
        """The presets for this camera's model (name, time saved, note), then 'Other file…'."""
        c = self.controls
        if self.preset_picker is not None and self.preset_picker.winfo_exists():
            self.preset_picker.destroy()
        dlg = self.preset_picker = tk.Toplevel(self.top)
        dlg.title("Load preset")
        dlg.configure(bg=BG, padx=16, pady=12)
        found = presets.list_presets(c.model if c is not None else None)
        if not found:
            tk.Label(dlg, text=f"No presets for this camera in {presets.store_dir()}", bg=BG, fg=TEXT_DIM,
                     font=MONO_SMALL, anchor="w").pack(fill="x")
        for i in found:
            tk.Button(dlg, text=f"{i.name}   {i.saved_at}   {i.note}", font=MONO, anchor="w",
                      command=lambda p=str(i.path): self._open_load_preset(p)).pack(fill="x", pady=1)
        tk.Button(dlg, text="Other file…", font=MONO, command=self._pick_file).pack(anchor="w", pady=(8, 0))

    def _pick_file(self) -> None:
        path = filedialog.askopenfilename(parent=self.top, initialdir=str(presets.store_dir()),
                                          filetypes=[("Preset", "*.json")], title="Load preset")
        if path:
            self._open_load_preset(path)

    def _do_load_preset(self, path: str, force: bool = False) -> None:
        c = self.controls
        if self.preset_dialog is not None:
            self.preset_dialog.destroy()
        if c is None:
            return
        try:
            r = c.load_preset(path, force=force, timeout=0)   # does not wait: the poll shows each result under its row
        except (presets.PresetError, ControlStateError, OSError, ControlsUnavailable) as exc:
            self.preset_label.configure(text=f"Not loaded: {exc}", fg=RED)
            return
        self._show_queued(r.results)
        mono = f", {len(r.skipped_mono)} mono-only skipped" if r.skipped_mono else ""
        self.preset_label.configure(text=f"Loaded: {len(r.results)} queued, {len(r.unchanged)} already set{mono}",
                                    fg=TEXT_DIM)

    # ---- window ----------------------------------------------------------------------------------
    def focus(self) -> None:
        self.top.lift()
        self.top.focus_force()

    @property
    def alive(self) -> bool:
        try:
            return bool(self.top.winfo_exists())
        except tk.TclError:
            return False

    def close(self) -> None:
        self.top.destroy()
