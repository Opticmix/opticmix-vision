"""톱니 버튼 뒤의 Settings 창 — 카메라가 광고하는 제어 전부.

행은 controls.describe() 에서 만든다: 범위를 준 정수 → 슬라이더, 불리언 → 체크박스, 메뉴 → 드롭다운,
범위를 안 준 정수 → 값만(슬라이더를 지어내지 않는다). 흑백 센서에서 효과가 없는 제어는 맨 아래 접힌 묶음.
슬라이더는 끄는 동안 값만 보여 주고 **놓을 때(마우스·방향키) 한 번** 쓴다 — 쓰기 간격이 12 ms 라(2026-09-28)
끌 때마다 쓰면 드래그 한 번이 쓰기 수십 번이 된다.
쓰기는 기다리지 않는다 — 가드가 늦추면 값 아래에 'applying in N s' 가 뜨고, 드라이버가 거부하면 빨간 글씨,
드라이버는 받았는데 카메라가 값을 지키지 않으면 주황 'camera kept N' 과 함께 위젯이 카메라의 실제 값으로 돌아간다. 창 아래 'Writes this session' 은 프로세스 전체의 쓰기 합계다.
"""
from __future__ import annotations

import tkinter as tk
from typing import Any, Callable

from .. import controls as controls_mod
from ..controls import Controls, ControlsUnavailable, ControlStateError, write_counts
from .state import MONO_NOTE, RANGE_NOTE, LOCKS, Row, banner_text, build_rows, locked, reset_lines, value_text
from .theme import BG, LIME, MONO, MONO_SMALL, ORANGE, PANEL, RED, TEXT, TEXT_DIM, TRACK

__all__ = ["SettingsWindow"]

POLL_MS = 200


class SettingsWindow:
    """이미 열려 있으면 앞으로 가져오고, 새로 만들지 않는다. 카메라가 다시 열리면 행을 다시 만든다."""

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

        self.top = tk.Toplevel(parent)
        self.top.title("Settings")
        self.top.configure(bg=BG)
        self.top.minsize(640, 200)           # 높이는 내용대로 — 흑백 묶음을 펼치면 창이 늘어난다

        tabs = tk.Frame(self.top, bg=PANEL)
        tabs.pack(fill="x")
        tk.Label(tabs, text="CAMERA", bg=LIME, fg="#111111", font=MONO, padx=24, pady=8).pack(side="left")
        # 아래 줄을 먼저 붙인다 — 나중에 붙이면 창이 모자랄 때 먼저 잘려 Reset 버튼이 사라진다
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
        self.writes_label = tk.Label(footer, text="", bg=PANEL, fg=TEXT_DIM, font=MONO_SMALL)
        self.writes_label.pack(side="right")
        self._rebuild()
        self.top.after(POLL_MS, self._poll)

    # ---- 행 만들기 ------------------------------------------------------------------------------
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
            self.reset_button.configure(state="disabled")
            return
        text = banner_text(self.controls.backend_name, self.controls.backend_note)
        if text:
            self.banner.configure(text=text)
            self.banner.pack(fill="x", after=self.top.winfo_children()[0])
        else:
            self.banner.pack_forget()
        self.reset_button.configure(state="normal")
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
        # 상태 줄은 행 아래에, 글이 있을 때만 — 옆에 두면 창 밖으로 밀려 안 보인다
        status = tk.Label(parent, text="", bg=BG, fg=TEXT_DIM, font=MONO_SMALL, anchor="w")
        status.row_frame = f  # type: ignore[attr-defined]
        self.vars[row.name] = var
        self.widgets[row.name] = w
        self.value_labels[row.name] = value
        self.status_labels[row.name] = status
        if row.mono:
            self._status(row.name, MONO_NOTE, TEXT_DIM)

    def _show_value(self, name: str, value: Any) -> None:
        """값 칸 — 드롭다운은 자기 글자가 값이라 비워 둔다."""
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

    # ---- 쓰기 ------------------------------------------------------------------------------------
    def _preview(self, name: str) -> None:
        """끄는 동안 — 값 칸만 따라가고 쓰지 않는다."""
        if name in self.vars:
            self._show_value(name, self.vars[name].get())

    def _release(self, name: str) -> None:
        """놓을 때 한 번 쓴다. 잠긴 슬라이더(자동 노출 중의 노출 등)는 쓰지 않는다."""
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
                        troughcolor=BG if target in off else TRACK)          # 잠긴 슬라이더는 트랙을 지운다
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

    # ---- 주기적으로: 대기 표시, 결과, 쓰기 횟수, 카메라가 바뀌었는지 --------------------------------------
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
                elif r.kept is False:                   # 드라이버는 받았는데 카메라가 값을 지키지 않았다
                    self._status(name, f"camera kept {value_text(name, r.readback)}", ORANGE)
                    self._reload(name)
                elif name not in pend:
                    self._status(name, "", TEXT_DIM)
        total = write_counts()["_total"]
        self.writes_label.configure(text=f"Writes this session: {total}",
                                    fg=ORANGE if total >= controls_mod.WARN_AFTER_TOTAL else TEXT_DIM)
        self.top.after(POLL_MS, self._poll)

    # ---- 기본값 복원 -------------------------------------------------------------------------------
    def _open_reset(self) -> None:
        c = self.controls
        if c is None:
            return
        if self.reset_dialog is not None and self.reset_dialog.winfo_exists():
            self.reset_dialog.destroy()
        plan = c.plan_reset()
        dlg = self.reset_dialog = tk.Toplevel(self.top)
        dlg.title("Reset to defaults")
        dlg.configure(bg=BG, padx=16, pady=12)
        for line in reset_lines(plan):
            tk.Label(dlg, text=line, bg=BG, fg=TEXT, font=MONO, anchor="w").pack(fill="x")
        buttons = tk.Frame(dlg, bg=BG)
        buttons.pack(fill="x", pady=(10, 0))
        self.reset_confirm = tk.Button(buttons, text="Reset", font=MONO, command=self._do_reset,
                                       state="normal" if plan else "disabled")
        self.reset_confirm.pack(side="left")
        tk.Button(buttons, text="Cancel", font=MONO, command=dlg.destroy).pack(side="left", padx=8)

    def _do_reset(self) -> None:
        c = self.controls
        if c is not None:
            for r in c.reset_to_defaults():
                if r.name in self.vars:
                    self.vars[r.name].set(r.requested)
                    self._show_value(r.name, r.requested)
                if r.status == "pending":
                    self._tickets[r.ticket] = r.name
        if self.reset_dialog is not None:
            self.reset_dialog.destroy()

    # ---- 창 --------------------------------------------------------------------------------------
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
