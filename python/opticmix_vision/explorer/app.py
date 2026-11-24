"""Weevia S1 Explorer window.

Layout: dark background, mode dropdown top left, device dropdown top right, both eyes side by side
on a black background, monospace stats over the video, icon row bottom left. The sliders are not in
the main window but in the Settings window behind the gear.

Why Tkinter: it is in the standard library, it has comboboxes and sliders, and `PhotoImage` takes
PPM bytes as they are, so numpy frames are drawn **without Pillow**.
An OpenCV window cannot draw dropdowns, and Qt adds 150MB to the executable.

The two things always on screen are facts, not verdicts:
  · both eyes side by side: if one freezes, you can see it
  · FRAMERATE is **measured**, not the requested value, so if format negotiation goes wrong it shows 9, not 90
"""
from __future__ import annotations

import argparse
import sys
import time
import tkinter as tk
from tkinter import ttk

import numpy as np

from .. import __version__
from ..device import WEEVIA_S1, WEEVIA_S1_NAME, Device, DeviceConfig
from ..devices import DeviceInfo, list_devices, list_modes
from .capture import CaptureWorker
from .settings_window import SettingsWindow
from .state import KNOWN_MODE, ExplorerState
from .theme import BG, LIME, MONO, MONO_SMALL, PANEL, TEXT, TEXT_DIM, VIDEO_BG

__all__ = ["ExplorerApp", "main"]

REDRAW_MS = 33          # about 30 fps. The camera runs at its own rate; only the screen draws slower.
MODEL_NAME = "Weevia S1"
VIEW_WIDTH = 960        # default box, used only while the widget size is not yet known.
VIEW_HEIGHT = 460


def _to_photo(gray: np.ndarray) -> tk.PhotoImage:
    """(h, w) uint8 -> PhotoImage. Tk reads PPM (P5) directly, so Pillow is not needed."""
    h, w = gray.shape
    return tk.PhotoImage(data=f"P5 {w} {h} 255 ".encode() + gray.tobytes(), format="ppm")


def _fit_step(w: int, h: int, box_w: int, box_h: int) -> int:
    """Smallest integer subsampling step that fits inside the box.

    With floor division, a 1600 width in a 960 box gives step 1, so no shrinking happens at all
    and the image is cut off outside the window (that is what was actually seen). Round up, and
    check height as well as width.
    """
    if box_w <= 0 or box_h <= 0:
        return 1
    return max(1, -(-w // box_w), -(-h // box_h))


def _shrink(gray: np.ndarray, box_w: int, box_h: int) -> np.ndarray:
    """Fits the image inside the box by integer subsampling.

    No interpolation, for honesty rather than speed: only if the screen shows the pixels exactly
    as the sensor delivered them does a frozen eye also show up as frozen.
    """
    step = _fit_step(gray.shape[1], gray.shape[0], box_w, box_h)
    return gray[::step, ::step]


def preferred_device(listed: list[DeviceInfo]) -> DeviceInfo:
    """The Weevia S1 if it is plugged in, else the first camera — a laptop webcam is often listed first."""
    return next((d for d in listed if WEEVIA_S1_NAME.lower() in d.name.lower()), listed[0])


class ExplorerApp:
    def __init__(self, *, synthetic: bool = False) -> None:
        self.synthetic = synthetic
        self.state = ExplorerState()
        self.device: Device | None = None
        self.source = None               # what is streaming now (Device or synthetic); the settings window uses its controls
        # a synthetic source returns immediately, so cap it near 90 fps to read on the same scale as a real camera.
        self.worker = CaptureWorker(self._open_source, min_interval=1 / 90 if synthetic else 0.0)
        self.settings: SettingsWindow | None = None
        self._devices: list[DeviceInfo] = []
        self._photo: tk.PhotoImage | None = None
        self._frames = 0

        self.root = tk.Tk()
        self.root.title("Weevia S1 Explorer")
        self.root.configure(bg=BG)
        self.root.geometry("1040x700")
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._style()
        self._build_top_bar()
        self._build_video()
        self._build_bottom_bar()

        self._rescan()
        self.root.after(REDRAW_MS, self._tick)

    # --- chrome ----------------------------------------------------------
    def _style(self) -> None:
        st = ttk.Style(self.root)
        try:
            st.theme_use("clam")
        except tk.TclError:                       # the window must still open on builds without the theme
            pass
        # configure alone does not change the list and pressed-state colors of clam's Combobox.
        # map is needed too, or the light default style remains.
        st.configure("Dark.TCombobox", fieldbackground=PANEL, background=PANEL,
                     foreground=TEXT, arrowcolor=LIME, bordercolor=PANEL,
                     lightcolor=PANEL, darkcolor=PANEL, selectbackground=PANEL,
                     selectforeground=TEXT, padding=4)
        st.map("Dark.TCombobox",
               fieldbackground=[("readonly", PANEL), ("focus", PANEL)],
               background=[("readonly", PANEL), ("active", PANEL)],
               foreground=[("readonly", TEXT)],
               arrowcolor=[("active", LIME)])
        # the drop-down list is colored via the Tk option database, not ttk.
        self.root.option_add("*TCombobox*Listbox.background", PANEL)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", LIME)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#111111")
        self.root.option_add("*TCombobox*Listbox.font", MONO)

    def _build_top_bar(self) -> None:
        bar = tk.Frame(self.root, bg=PANEL, height=48)
        bar.pack(fill="x")
        tk.Label(bar, text="Opticmix", bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 13, "bold"), padx=14, pady=10).pack(side="left")
        self.live_var = tk.StringVar(value="LIVE")
        tk.Label(bar, textvariable=self.live_var, bg=PANEL, fg=LIME, font=MONO, padx=10).pack(side="left")

        self.mode_var = tk.StringVar()
        self.mode_combo = ttk.Combobox(bar, textvariable=self.mode_var, state="readonly",
                                       width=26, style="Dark.TCombobox")
        self.mode_combo.pack(side="left", padx=6)
        self.mode_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_mode())

        # fill from the right; the gear is outermost.
        tk.Button(bar, text="⚙", command=self._open_settings, bg=PANEL, fg=LIME,
                  font=("Segoe UI", 13), relief="flat", activebackground=PANEL,
                  activeforeground=LIME, borderwidth=0).pack(side="right", padx=(4, 12))
        tk.Button(bar, text="⟳", command=self._rescan, bg=PANEL, fg=LIME,
                  font=("Segoe UI", 13), relief="flat", activebackground=PANEL,
                  activeforeground=LIME, borderwidth=0).pack(side="right", padx=4)

        # what the OS knows of the camera's USB device (not opened), and that it has no serial number.
        from ..doctor import _usb_summary
        sn = tk.Label(bar, text=_usb_summary(self.synthetic), bg=PANEL, fg=TEXT_DIM, font=MONO)
        sn.pack(side="right", padx=(8, 4))
        self._tooltip(sn, "VID:PID, USB device revision (bcdDevice) and USB link speed, read from the OS without\n"
                          "opening the camera. This camera reports no USB serial number: two units cannot be told\n"
                          "apart in software — label them physically. python -m opticmix_vision.doctor prints more.")

        self.device_var = tk.StringVar()
        self.device_combo = ttk.Combobox(bar, textvariable=self.device_var, state="readonly",
                                         width=28, style="Dark.TCombobox")
        self.device_combo.pack(side="right", padx=4)
        self.device_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_device())
        tk.Label(bar, text="ID:", bg=PANEL, fg=TEXT_DIM, font=MONO).pack(side="right")

    def _build_video(self) -> None:
        holder = tk.Frame(self.root, bg=VIDEO_BG)
        holder.pack(fill="both", expand=True)
        self.canvas = tk.Label(holder, bg=VIDEO_BG)
        self.canvas.pack(fill="both", expand=True)
        # stats are overlaid on the bottom left of the video.
        self.stats_var = tk.StringVar(value="")
        self.stats = tk.Label(holder, textvariable=self.stats_var, bg=VIDEO_BG, fg=TEXT_DIM,
                              font=MONO_SMALL, justify="left", anchor="w")
        self.stats.place(relx=0.0, rely=1.0, x=14, y=-10, anchor="sw")

    def _build_bottom_bar(self) -> None:
        bar = tk.Frame(self.root, bg=PANEL, height=36)
        bar.pack(fill="x")
        tk.Button(bar, text="📁", command=self._open_folder, bg=PANEL, fg=LIME, relief="flat",
                  font=("Segoe UI", 12), activebackground=PANEL, borderwidth=0).pack(side="left", padx=(12, 4), pady=4)
        tk.Button(bar, text="📷", command=self._snapshot, bg=PANEL, fg=LIME, relief="flat",
                  font=("Segoe UI", 12), activebackground=PANEL, borderwidth=0).pack(side="left", padx=4)
        self.message_var = tk.StringVar(value="")
        tk.Label(bar, textvariable=self.message_var, bg=PANEL, fg=TEXT_DIM,
                 font=MONO_SMALL).pack(side="left", padx=12)
        tk.Label(bar, text=f"v{__version__}", bg=PANEL, fg=TEXT_DIM,
                 font=MONO_SMALL, padx=12).pack(side="right")

    def _tooltip(self, widget: tk.Widget, text: str) -> None:
        tip: dict[str, tk.Toplevel | None] = {"win": None}

        def show(_e):
            if tip["win"] is not None:
                return
            win = tk.Toplevel(widget)
            win.wm_overrideredirect(True)
            win.wm_geometry(f"+{widget.winfo_rootx()}+{widget.winfo_rooty() + 26}")
            tk.Label(win, text=text, bg="#111111", fg=TEXT, font=MONO_SMALL,
                     justify="left", padx=8, pady=6).pack()
            tip["win"] = win

        def hide(_e):
            if tip["win"] is not None:
                tip["win"].destroy()
                tip["win"] = None

        widget.bind("<Enter>", show)
        widget.bind("<Leave>", hide)

    def _model_label(self) -> str:
        """The camera actually open — not 'Weevia S1' for a webcam or generated frames."""
        if self.synthetic:
            return "SYNTHETIC"
        name = self.state.device.name if self.state.device else "—"
        return MODEL_NAME if WEEVIA_S1_NAME.lower() in name.lower() else name

    # --- wiring ----------------------------------------------------------
    def _rescan(self) -> None:
        if self.synthetic:
            self._devices = [DeviceInfo(index=0, name="(synthetic)")]
        else:
            self._devices = list_devices()
        self.device_combo["values"] = [d.name for d in self._devices]
        if not self._devices:
            self.message_var.set("No camera found — connect one and press the refresh button")
            return
        self.device_var.set(preferred_device(self._devices).name)
        self._on_device()

    def _on_device(self) -> None:
        chosen = next(d for d in self._devices if d.name == self.device_var.get())
        self.state.select_device(chosen)
        self.state.set_modes([KNOWN_MODE] if self.synthetic else list_modes(chosen.name))
        self.mode_combo["values"] = [m.label() for m in self.state.modes]
        self.mode_var.set(self.state.modes[0].label())
        self.message_var.set(self.state.mode_note)
        self._on_mode()

    def _on_mode(self) -> None:
        label = self.mode_var.get()
        self.state.select_mode(next(m for m in self.state.modes if m.label() == label))
        self._restart()

    def _open_source(self):
        if self.synthetic:
            from ..synthetic import SyntheticStereoSource
            self.source = SyntheticStereoSource()
            return self.source
        mode = self.state.mode
        cfg = DeviceConfig(layout=WEEVIA_S1["layout"], lr_order=WEEVIA_S1["lr_order"], backend="dshow",
                           device=self.state.device.index,
                           width=mode.width, height=mode.height,
                           fps=mode.max_fps, fourcc=mode.fourcc)
        # No control values are written on open; on this camera writing itself is risky. The screen shows the values
        # left in the camera as they are; to change them, use the Settings window (or Reset to defaults).
        self.device = Device(cfg).__enter__()
        self.source = self.device
        return self.device

    def _current_controls(self):
        src = self.source
        if src is None:
            return None
        try:
            return src.controls
        except Exception:  # noqa: BLE001 — device still opening or already closed
            return None

    def _restart(self) -> None:
        self.worker.stop()
        self._close_device()
        self._frames = 0
        if self.synthetic or self.state.can_stream:
            self.worker.start()

    def _open_settings(self) -> None:
        if self.settings is not None and self.settings.alive:
            self.settings.focus()
            return
        self.settings = SettingsWindow(self.root, self._current_controls)

    def _open_folder(self) -> None:
        from pathlib import Path
        out = Path("captures").resolve()
        out.mkdir(parents=True, exist_ok=True)
        self.message_var.set(str(out))
        try:
            import os
            os.startfile(out)                      # Windows only; this is a GUI app, so fail quietly
        except Exception:
            pass

    def _snapshot(self) -> None:
        frame = self.worker.latest()
        if frame is None:
            self.message_var.set("No frame to save yet")
            return
        from pathlib import Path
        from ..viewer import _imwrite                 # imencode+tofile: cv2.imwrite fails silently on Hangul paths
        out = Path("captures"); out.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        _imwrite(out / f"{stamp}_left.png", frame.left)
        _imwrite(out / f"{stamp}_right.png", frame.right)
        self.message_var.set(f"Saved {stamp}_left.png / _right.png")

    # --- redraw ----------------------------------------------------------
    def _tick(self) -> None:
        frame = self.worker.latest()
        if frame is not None:
            self._frames += 1
            pair = np.hstack([frame.left, frame.right])     # both eyes always side by side
            # fit to the window size. Before the widget is laid out winfo_* returns 1,
            # so only then is the default box used.
            box_w = self.canvas.winfo_width()
            box_h = self.canvas.winfo_height()
            if box_w <= 1 or box_h <= 1:
                box_w, box_h = VIEW_WIDTH, VIEW_HEIGHT
            self._photo = _to_photo(_shrink(pair, box_w, box_h))
            self.canvas.configure(image=self._photo)
        self._refresh_stats()
        self.root.after(REDRAW_MS, self._tick)

    def _refresh_stats(self) -> None:
        if self.worker.error:
            self.live_var.set("ERROR")
            self.stats_var.set(f"{self.worker.error}\n"
                               "Press the refresh button to look again")
            return
        self.live_var.set("LIVE" if self.worker.is_running else "IDLE")
        frame = self.worker.latest()
        mode = getattr(self.device, "mode", None)
        res = f"{mode.width} x {mode.height}" if mode else (
            f"{frame.size[0] * 2} x {frame.size[1]}" if frame else "—")
        fourcc = mode.fourcc if mode else ("SYNTHETIC" if self.synthetic else "—")
        self.stats_var.set(
            f"MODEL: {self._model_label()}\n"
            f"FORMAT: {fourcc}\n"
            f"RESOLUTION: {res}\n"
            f"FRAMERATE: {self.worker.fps:.1f}   (measured, not requested)\n"
            f"FRAMES: {getattr(frame, 'index', 0) if frame else 0}\n"
            f"TIMESTAMP: {getattr(frame, 'host_ns', 0) if frame else 0}"
        )

    # --- teardown --------------------------------------------------------
    def _close_device(self) -> None:
        if self.device is not None:
            try:
                self.device.__exit__(None, None, None)
            except Exception:
                pass
            finally:
                self.device = None
        self.source = None                 # the synthetic source is closed when the capture worker stops

    def close(self) -> None:
        self.worker.stop()
        self._close_device()
        if self.settings is not None and self.settings.alive:
            self.settings.close()
        self.root.destroy()

    def run(self) -> int:
        self.root.mainloop()
        return 0


def main(argv=None) -> int:
    if sys.platform == "win32":
        try:                                   # so it does not look blurry on high-DPI screens
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    ap = argparse.ArgumentParser(prog="opticmix-explorer")
    ap.add_argument("--synthetic", action="store_true",
                    help="run on generated frames, with no camera attached")
    a = ap.parse_args(argv)
    return ExplorerApp(synthetic=a.synthetic).run()


if __name__ == "__main__":
    sys.exit(main())
