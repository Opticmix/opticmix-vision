"""Recording frames to a file, playing a recording back, and the capture-info JSON saved next to captures.

A recording is three files with the same name:

  <name>.avi or .mkv  both eyes side by side (left eye in the left half), 8-bit gray
  <name>.csv          frame, host_ns, driver_ns, driver_clock: when each frame arrived (host monotonic clock, not
                      a camera timestamp), and the capture driver's time stamp and its clock name
                      (`StereoFrame.driver_ns`, `driver_clock`), empty where the frame had none (DirectShow, the
                      synthetic source). A CSV of only frame, host_ns (earlier versions) plays back as before
  <name>.json         capture info (`capture_info`): the negotiated mode, the camera's layout and left/right
                      order, device name, installed calibration files, control values when recording started
                      and ended and every change in between (`Recorder.note_control`), where those values
                      came from and when they were read (`controls_source`, `controls_read_at`), SDK version,
                      codec, frame count, pauses, the frames a full write queue dropped, and the late / missed
                      frame estimates over the recording

The .csv and .json are named after the video without its extension, so two recordings in one folder need
different names, not only different extensions: `Recorder` refuses (FileExistsError) rather than overwrite
another recording's files (or the .json of a `capture_dataset_npz.py` .npz).

Codec "MJPG" (default) is compressed: pixel values change. "FFV1" (`LOSSLESS_CODEC`) keeps the pixel values the
SDK delivered; each frame takes longer to write than MJPG, so it may not keep up with 90 fps — the recording's
JSON and `Recorder.estimated_missed_frames` say how many frames were missed. FFV1 needs an OpenCV build with the
FFV1 encoder (checked: the opencv-python 4.12, 4.13, 4.14 and 5.1 wheels on Windows; other builds not checked). With
the 4.12 and 4.13 wheels, writing grayscale frames to FFV1 failed now and then (`cv2.error` on a frame, with cv2
alone too, without this package; one PC, generated frames); with 4.14 and 5.1 it did not in the same test. Whether
the failure is in OpenCV or in the FFmpeg its wheel bundles has not been established; other OpenCV versions and
Linux have not been run. With OpenCV before 4.14 `write()` then raises RuntimeError saying to upgrade OpenCV or
record MJPG.

A recording that was not closed (the process stopped while recording): an MJPG .avi cannot be opened; an FFV1
.mkv plays up to where it was written.

Play a recording with `Device(DeviceConfig.recording(path))`; `Device.playback` is the `Playback` here (seek,
speed, real-time pacing, loop, pause, status). The Rust crate (`opticmix-vision`: `Recorder`,
`Device::open_recording`) writes and plays the same MJPG .avi files, CSV and JSON; it neither writes nor plays
FFV1, and its recordings name a checked V4L2 clock "v4l2_monotonic" in the CSV. Rust recording and playback:
generated frames only; not run with the camera.
"""

from __future__ import annotations

import bisect
import csv
import hashlib
import json
import queue
import threading
import time
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import numpy as np

from .device import _FrameStats
from .devices import _os_name

__all__ = ["LOSSLESS_CODEC", "capture_info", "write_capture_info", "Recorder", "Playback"]

LOSSLESS_CODEC = "FFV1"


def _calibration_files() -> list[dict[str, str]]:
    """Installed calibration files (`calib add`) with their sha256. Reads only."""
    from . import calib
    d = calib.store_dir()
    return [{"file": f"{s}.json", "sha256": hashlib.sha256((d / f"{s}.json").read_bytes()).hexdigest()}
            for s in calib.installed()]


_GIVEN_NOTE = ("values known to the program that captured, not read from the camera here (the viewer: read when it "
               "opened, then its own writes)")


def _now() -> str:
    """Local wall-clock time, ISO 8601 to the millisecond."""
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def capture_info(source: Any, *, controls: dict[str, Any] | None = None, controls_read_at: str | None = None,
                 **extra: Any) -> dict[str, Any]:
    """What was capturing: SDK version, wall-clock time, negotiated mode, the camera's layout and left/right
    order, device name and installed calibration files (camera only), and every control value
    (`controls.snapshot()`; simulated values for the synthetic source). `extra` is added as is.
    `controls`: the values to write instead of reading the camera (controls_note then says so), and
    `controls_read_at` when the program read them from the camera (ISO 8601).
    `controls_source` says where the values came from: "camera" (read here), "simulated" (the synthetic source),
    "given" (`controls`), or None (no controls; controls_note says why); `controls_read_at` is when.
    Reads controls (unless given), never writes them."""
    from . import __version__
    mode = getattr(source, "mode", None)
    info: dict[str, Any] = {
        "opticmix_vision": __version__,
        "created": datetime.now().astimezone().isoformat(timespec="seconds"),
        "host_clock": "host_ns is time.perf_counter_ns() on the host when the frame was read, not a camera clock",
        "mode": mode.to_dict() if mode is not None else None,
    }
    recorded = getattr(source, "recording_info", None)
    cfg = getattr(source, "config", None)
    if getattr(source, "playback", None) is not None:
        info["source"] = "recording"
        info["recorded_from"] = recorded
    elif cfg is not None:
        info["source"] = "camera"
        info["camera"] = {"device": cfg.device, "device_name": _os_name(cfg.device, cfg.backend), "backend": cfg.backend,
                          "layout": cfg.layout, "lr_order": cfg.lr_order}
        try:
            info["calibration_files"] = _calibration_files()
            info["calibration_note"] = "installed files; the SDK cannot check that a file belongs to this camera"
        except Exception as exc:                 # noqa: BLE001
            info["calibration_files"] = None
            info["calibration_note"] = f"{type(exc).__name__}: {exc}"
    else:
        info["source"] = "synthetic" if getattr(mode, "backend_name", "") == "synthetic" else type(source).__name__
    try:
        if controls is not None:
            info["controls"], info["controls_note"] = dict(controls), _GIVEN_NOTE
            info["controls_source"], info["controls_read_at"] = "given", controls_read_at
        else:
            info["controls"] = source.controls.snapshot()
            info["controls_read_at"] = _now()
            info["controls_source"] = "simulated" if info["source"] == "synthetic" else "camera"
        if controls is None and info["source"] == "synthetic":
            info["controls_note"] = "simulated values (synthetic source)"
    except Exception as exc:                     # no controls (a recording, or no compiled extension): say why
        info["controls"] = info["controls_source"] = info["controls_read_at"] = None
        info["controls_note"] = f"{type(exc).__name__}: {exc}"
    info.update(extra)
    return info


def _dump(path: Path, info: dict[str, Any]) -> None:
    path.write_text(json.dumps(info, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def write_capture_info(path: str | Path, source: Any, **extra: Any) -> dict[str, Any]:
    """`capture_info(source, **extra)` written to `path` as JSON. Returns the dict."""
    info = capture_info(source, **extra)
    _dump(Path(path), info)
    return info


# files whose .csv / .json a recording of the same name would overwrite: videos, and capture_dataset_npz.py's .npz
_SIBLING_SUFFIXES = (".avi", ".mkv", ".mp4", ".mov", ".npz")


def _expected_size(source: Any) -> tuple[int, int, bool] | None:
    """(eye width, eye height, color) of the frames `source` will deliver, from its negotiated mode and layout
    (sbs, or tb for a camera configured so); None when there is no mode or the layout is plane_pack."""
    mode = getattr(source, "mode", None)
    w, h = int(getattr(mode, "width", 0) or 0), int(getattr(mode, "height", 0) or 0)
    layout = getattr(getattr(source, "config", None), "layout", "sbs")
    if w <= 0 or h <= 0 or layout not in ("sbs", "tb"):
        return None
    return (w // 2, h, False) if layout == "sbs" else (w, h // 2, False)


def _interval_stats(ns: list[int], fps: float, breaks: frozenset[int]) -> _FrameStats:
    """Late / missed estimates over a whole list of frame times; a break (a pause in recording) is not a gap."""
    s = _FrameStats()
    for i, t in enumerate(ns):
        s.add(i, t, fps, restart=i in breaks)
    return s


class Recorder:
    """Writes frames to `path` (both eyes side by side) with the `.csv` of frame times and the `.json` capture
    info next to it. `source` is the Device (or synthetic source) the frames come from; its mode and control
    values go into the JSON when the recorder is created.

    The three files are opened when the recorder is created, at the eye size the source's negotiated mode
    gives, so opening them does not hold up the first frames. When that size is unknown (a source without a
    mode, plane_pack) or the first frame has another size, the files are opened on the first `write()`; frames
    that arrive while they open are missed and counted. Raises OSError when the video file cannot be opened, and
    FileExistsError when another video (or a capture_dataset_npz.py .npz) with the same name but another
    extension is in the folder (the .csv and .json are its files). Recording again to the same path replaces
    that recording: it is moved to <file>.replaced while recording, deleted when this recording closes with a
    frame, and put back when it closes with none. If the process stops while recording, the earlier recording
    stays in the .replaced files next to the unfinished one, and recording to that path is refused (the message
    says how to keep either). A recorder closed before any frame was written leaves no files of its own.

    `codec`: four characters; "MJPG" (compressed, with .avi) or `LOSSLESS_CODEC` "FFV1" (with .mkv or .avi).
    `fps`: the frame rate written into the file, a label only (default: the source's negotiated rate, or 30).
    `controls`: a function giving the control values for the JSON, called when the recorder is created and when it
    closes (`controls_at_end`), instead of reading the camera then; None from it reads the camera as without it.
    `controls_read_at`: when those values were read from the camera (ISO 8601), for the JSON.

    `queue_frames`: 0 (default): `write()` encodes the frame before it returns. N > 0: `write()` hands the frame
    to a writing thread through a queue of N frames and returns at once, so encoding does not hold up the caller
    (the viewer draws while a thread encodes). A frame that finds the queue full is not recorded: it is counted
    in `dropped_frames` (and the JSON's), and the frame numbers stay those of the file. An error on the writing
    thread is raised by the next `write()` (every later one too; nothing more is recorded) or, if none comes, by
    `close()`; the frames given after it are counted as dropped. `close()` writes the frames still queued first.
    The queued frames' arrays are kept until written: do not change them after `write()`. A queue does not make
    an encoder that is slower than the camera keep up (FFV1 at 90 fps): the frames it cannot take are dropped
    and counted. `write()` after `close()` raises ValueError.

    `late_frames` / `estimated_missed_frames`: over the frames written so far, from their host_ns intervals at
    the source's negotiated rate (the same rule as `Device.late_frames`); a pause is not counted as a gap, a
    dropped frame is (it leaves a gap in the recorded times): `estimated_missed_frames` includes
    `dropped_frames` — do not add them.
    """

    def __init__(self, path: str | Path, source: Any, *, codec: str = "MJPG", fps: float | None = None,
                 controls: Callable[[], dict[str, Any] | None] | None = None, controls_read_at: str | None = None,
                 queue_frames: int = 0) -> None:
        if len(codec) != 4:
            raise ValueError(f"codec takes four characters, e.g. MJPG or FFV1: {codec!r}")
        if int(queue_frames) < 0:
            raise ValueError(f"queue_frames is 0 (write before returning) or a number of frames: {queue_frames!r}")
        self.path = Path(path)
        self.csv_path = self.path.with_suffix(".csv")
        self.info_path = self.path.with_suffix(".json")
        for ext in _SIBLING_SUFFIXES:
            other = self.path.with_suffix(ext)
            if ext != self.path.suffix.lower() and other.is_file():
                raise FileExistsError(
                    f"{other} has the same name: recording to {self.path.name} would overwrite "
                    f"{self.csv_path.name} and {self.info_path.name}, the files next to {other.name}. "
                    f"Record to another name (for example {self.path.stem}_2{self.path.suffix}), or move "
                    f"{other.name} and the files next to it to another folder.")
        self.codec = codec
        self.compressed = codec.upper() != LOSSLESS_CODEC
        self._mode_fps = float(getattr(getattr(source, "mode", None), "fps", 0) or 0.0)
        self.fps = float(fps or self._mode_fps or 30.0)     # some drivers report 0
        self.frames = 0
        self.paused = False
        self._source = source
        self._given = controls
        self._writer: Any = None
        self._fh: Any = None
        self._size: tuple[int, int, bool] | None = None     # what the open video file takes: eye w, h, color
        self._kept: list[tuple[Path, Path]] | None = None   # an earlier recording here, set aside: (file, .replaced)
        self._first_ns = self._last_ns = None
        self._stats = _FrameStats()
        self._resumed = False
        self._next = 0                  # the file's number for the next frame write() takes (written + queued)
        self._full = self._failed = 0   # not recorded: the queue was full (caller) / writing failed (writing thread)
        self._error: BaseException | None = None
        self._told = False
        self._closed = False
        self._queue: queue.Queue | None = None
        self.info = capture_info(source, controls=None if controls is None else controls(),
                                 controls_read_at=controls_read_at, recording={
            "codec": codec, "compressed": self.compressed, "layout": "sbs", "lr_order": "first_is_left",
            "fps_label": self.fps, "frames": 0, "csv": self.csv_path.name, "closed": False,
            "write_queue": int(queue_frames), "dropped_frames": 0, "breaks": [], "control_changes": []})
        size = _expected_size(source)
        if size is not None:
            self._open(size)
        if queue_frames:
            self._queue = queue.Queue(int(queue_frames))
            self._thread = threading.Thread(target=self._drain, name=f"Recorder {self.path.name}", daemon=True)
            self._thread.start()

    @property
    def late_frames(self) -> int:
        return self._stats.late

    @property
    def estimated_missed_frames(self) -> int:
        return self._stats.missed

    @property
    def dropped_frames(self) -> int:
        """Frames `write()` took (not paused) that are not in the file: the write queue was full, or writing had
        failed. Always 0 without a queue (`write()` then raises instead)."""
        return self._full + self._failed

    @property
    def missed_advice(self) -> str:
        """What to do when frames were missed while recording, in one ASCII line; "" when none were. Suggests MJPG
        only for a lossless recording. examples/record_video.py and the viewer print it (the viewer first suggests
        examples/record_video.py)."""
        if not self.estimated_missed_frames:
            return ""
        mjpg = "" if self.compressed else "record MJPG (leave out --lossless), "
        return (f"To record every frame: {mjpg}ask a lower rate with --fps, or keep frames in memory with "
                "examples/capture_dataset_npz.py.")

    def _open(self, size: tuple[int, int, bool]) -> None:
        import cv2
        if self._writer is not None:             # opened for another frame size; no frame has been written yet
            self._writer.release()
            self._fh.close()
            self._writer = self._size = None
        if self.path.parent.exists() and not self.path.parent.is_dir():
            raise OSError(f"recording path is not a directory: {self.path.parent}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self._kept is None:
            self._set_aside()
        w, h, color = size
        try:
            writer = cv2.VideoWriter(str(self.path), cv2.VideoWriter_fourcc(*self.codec), self.fps, (2 * w, h),
                                     isColor=color)
            if not writer.isOpened():
                raise OSError(f"OpenCV could not open a video writer for codec {self.codec!r} and file "
                              f"{str(self.path)!r}. Check that the extension suits the codec (MJPG with .avi, FFV1 "
                              "with .mkv or .avi, mp4v with .mp4), that your OpenCV build has the codec, and that "
                              "the folder is writable.")
            self._writer, self._size = writer, size
            # line-buffered: if the process stops mid-recording, the CSV still has the frames written so far
            self._fh = open(self.csv_path, "w", newline="", encoding="utf-8", buffering=1)
        except Exception:
            if self._writer is not None:
                self._writer.release()
                self._writer = self._size = None
            self._put_back()
            raise
        self._rows = csv.writer(self._fh)
        self._rows.writerow(["frame", "host_ns", "driver_ns", "driver_clock"])
        self.info["recording"]["eye_size"] = [int(w), int(h)]
        self._write_info()                       # there is a JSON even if the process dies mid-recording

    def _set_aside(self) -> None:
        """An earlier recording at this path is kept until this one closes with a frame: moved to <file>.replaced."""
        ours = (self.path, self.csv_path, self.info_path)
        files = [p for p in ours if p.is_file()]
        for p in ours:                           # every name: also when the unfinished files were deleted already
            q = p.with_name(p.name + ".replaced")
            if q.exists():
                names = f"{self.path.name}, {self.csv_path.name} and {self.info_path.name}"
                if not files:
                    raise FileExistsError(
                        f"{q} is left from recording to {self.path.name} when the process stopped: the .replaced "
                        "files are the recording that was there before. Take .replaced off the names to get it "
                        "back, or delete the .replaced files.")
                raise FileExistsError(
                    f"{q} is left from recording to {self.path.name} when the process stopped: {names} are that "
                    "unfinished recording, the .replaced files the recording that was there before. To keep the "
                    f"earlier one, delete or move {names} first, then take .replaced off the names; to keep the "
                    "unfinished one, delete the .replaced files.")
        moved: list[tuple[Path, Path]] = []
        try:
            for p in files:
                q = p.with_name(p.name + ".replaced")
                p.replace(q)
                moved.append((p, q))
        except BaseException:                    # e.g. the .csv open in another program: undo the moves made
            for p, q in reversed(moved):
                q.replace(p)
            raise
        self._kept = moved

    def _put_back(self) -> None:
        """Removes this recording's files and brings back the recording set aside, as it was."""
        for p in (self.path, self.csv_path, self.info_path):
            p.unlink(missing_ok=True)
        for p, q in self._kept or ():
            q.replace(p)
        self._kept = None

    def write(self, frame: Any) -> None:
        """Appends one StereoFrame. Does nothing while paused. Raises OSError when the video file cannot be opened.
        With a queue (`queue_frames`), hands the frame to the writing thread, or drops it when the queue is full,
        and raises the error the writing thread met. Raises ValueError after `close()`."""
        if self._closed:
            raise ValueError("recorder is closed")
        if self.paused:
            return
        if self._error is not None:
            self._told = True
            raise self._error
        brk = self._resumed and self._next > 0
        if self._queue is None:
            self._write(frame, brk)
        else:
            try:
                self._queue.put_nowait((frame, brk))
            except queue.Full:
                self._full += 1                  # a break still waiting goes with the next frame recorded
                return
        self._resumed = False
        if brk:
            self.info["recording"]["breaks"].append(self._next)
        self._next += 1

    def _drain(self) -> None:
        """The writing thread: writes the queued frames in order until close() queues None."""
        while (item := self._queue.get()) is not None:
            if self._error is None:
                try:
                    self._write(*item)
                    continue
                except BaseException as exc:     # noqa: BLE001 — raised to the caller by write() or close()
                    self._error = exc
            self._failed += 1

    def _write(self, frame: Any, brk: bool) -> None:
        if self.frames == 0:
            h, w = frame.left.shape[:2]
            if self._size != (w, h, frame.left.ndim == 3):    # not opened yet, or opened for another size
                self._open((w, h, frame.left.ndim == 3))
        import cv2
        try:
            self._writer.write(np.hstack([frame.left, frame.right]))
        except cv2.error as exc:
            if self.compressed or tuple(int(v) for v in cv2.__version__.split(".")[:2]) >= (4, 14):
                raise
            raise RuntimeError(
                f"OpenCV {cv2.__version__} failed to write frame {self.frames} to {self.path.name} (FFV1): {exc}. "
                "On one Windows PC, with generated grayscale frames, FFV1 writing failed this way now and then "
                "with the opencv-python 4.12 and 4.13 wheels and not with 4.14 or 5.1. Upgrade OpenCV "
                "(pip install -U opencv-python) or record MJPG.") from exc
        # csv writes None as an empty cell; a frame-like object without driver_ns gets empty cells too
        self._rows.writerow([self.frames, frame.host_ns, getattr(frame, "driver_ns", None),
                             getattr(frame, "driver_clock", None)])
        self._stats.add(self.frames, int(frame.host_ns), self._mode_fps, restart=brk)
        self.frames += 1
        self._last_ns = int(frame.host_ns)
        if self._first_ns is None:
            self._first_ns = self._last_ns

    def note_control(self, name: str, value: Any, status: str | None = None) -> None:
        """Records a control change made while recording: {"frame", "name", "value"} (and "status" when given),
        where frame is the number of the next frame written (counting the frames still queued). The viewer calls it
        for its key changes."""
        change: dict[str, Any] = {"frame": self._next, "name": name, "value": value}
        if status is not None:
            change["status"] = status
        self.info["recording"]["control_changes"].append(change)

    def pause(self) -> None:
        """Stops writing until `resume()`; the frames in between are not recorded. The JSON lists the frame
        number after each pause in "breaks", and playback does not count the pause as missed frames."""
        self.paused = True

    def resume(self) -> None:
        if self.paused:
            self._resumed = True
        self.paused = False

    def _write_info(self) -> None:
        r = self.info["recording"]
        r["frames"] = self.frames
        r["first_host_ns"], r["last_host_ns"] = self._first_ns, self._last_ns
        r["duration_s"] = (self._last_ns - self._first_ns) / 1e9 if self._first_ns is not None else 0.0
        r["late_frames"], r["estimated_missed_frames"] = self.late_frames, self.estimated_missed_frames
        r["dropped_frames"] = self.dropped_frames
        _dump(self.info_path, self.info)

    def close(self) -> None:
        """Writes the frames still queued, closes the files and writes the final frame count, the late / missed
        estimates and the control values at the end into the JSON, and deletes the earlier recording it replaced.
        With no frame written, removes its files instead (an empty video cannot be played) and puts back the
        recording it replaced. Then raises the writing thread's error if no `write()` raised it. Safe to call more
        than once; `write()` after it raises ValueError."""
        self._closed = True
        if self._queue is not None and self._thread.is_alive():
            self._queue.put(None)                # after the frames still queued
            self._thread.join()
        self._close_files()
        if self._error is not None and not self._told:
            self._told = True
            raise self._error

    def _close_files(self) -> None:
        if self._writer is None:
            return
        self._writer.release()
        self._fh.close()
        self._writer = None
        if self.frames == 0:
            self._put_back()
            return
        for _, q in self._kept or ():
            q.unlink(missing_ok=True)
        self._kept = []
        r = self.info["recording"]
        try:
            controls = self._source.controls
            controls.flush(timeout=1.0)          # let writes still queued land before reading the end values
            given = None if self._given is None else self._given()
            if given is None:
                r["controls_at_end"] = controls.snapshot()
                r["controls_at_end_source"] = "simulated" if self.info["source"] == "synthetic" else "camera"
            else:
                r["controls_at_end"], r["controls_at_end_source"] = given, "given"
        except Exception:                        # noqa: BLE001 — no controls (synthetic without core, a recording)
            r["controls_at_end"] = r["controls_at_end_source"] = None
        r["closed"] = True
        self._write_info()

    def __enter__(self) -> "Recorder":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def _fourcc_int(s: str) -> float:
    s = (s or "")[:4].ljust(4)
    return float(sum(ord(ch) << (8 * i) for i, ch in enumerate(s))) if s.strip() else 0.0


class Playback:
    """A recording read back frame by frame. `Device(DeviceConfig.recording(path))` uses it as its capture, and
    `Device.playback` returns it for the controls below.

    position     the file frame number of the current frame: the one `read()` returned last, or the one a
                 `seek()` moved to (it is returned next)
    real_time    False (default): `read()` returns the next frame as soon as it is decoded. True: `read()` waits
                 so frames come at the recorded intervals (the CSV's host_ns), divided by `speed`. Frames are
                 not dropped when the caller is slower. A pause in recording ("breaks") is not waited out.
    loop         True: after the last frame, playback starts again at frame 0 instead of ending.
    recorded_time  True: a frame's `host_ns` is the recorded time (from the CSV) instead of the time it was read back.
                 Then also its `driver_ns` / `driver_clock` are the recorded ones (None where the CSV cell is
                 empty or the CSV has no such column); without it they are None, so `host_ns - driver_ns` never
                 mixes the time of reading back with a recorded driver time.
    paused       while paused, `read()` returns the current frame again; `seek()` still moves.
    status       "playing", "paused" or "ended" (after the last frame, without loop). There is no status
                 callback: read `status`, and `read()` ends with `EndOfRecording`.
    late_frames / estimated_missed_frames  over the whole recording, from the CSV's intervals at the recorded
                 rate (pauses in recording are not gaps). `Device.late_frames` counts only the frames read.

    Without the CSV, frame times are frame number / the file's frame rate. Without the JSON, `info` is None and
    the mode is the file's own. When the CSV and the video disagree on the frame count (a recording that was not
    closed), the smaller count is played and a RuntimeWarning says so.
    """

    def __init__(self, path: str | Path) -> None:
        import cv2
        self.path = Path(path)
        info_path = self.path.with_suffix(".json")
        self.info: dict[str, Any] | None = (json.loads(info_path.read_text(encoding="utf-8"))
                                            if info_path.is_file() else None)
        rec = (self.info or {}).get("recording") or {}
        self._cap = cv2.VideoCapture(str(self.path))
        if not self._cap.isOpened():
            why = ""
            if rec.get("closed") is False:
                why = (" It was not closed (the process stopped while recording): an MJPG .avi that was not closed"
                       " cannot be opened; an FFV1 .mkv plays up to where it was written.")
            raise OSError(f"OpenCV could not open the recording {self.path}.{why}")
        csv_path = self.path.with_suffix(".csv")
        ns: list[int] | None = None
        drv: list[tuple[int | None, str | None]] = []
        if csv_path.is_file():
            with open(csv_path, newline="", encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))
            ns = []
            for line, r in enumerate(rows, 2):                       # line 1 is the header
                try:
                    ns.append(int(r["host_ns"]))
                    # an earlier version's CSV has no driver columns, and an empty cell is a frame without a
                    # driver time: both None
                    d = r.get("driver_ns")
                    drv.append((int(d), r.get("driver_clock") or None) if d else (None, None))
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError(f"{csv_path} line {line}: not a frame time row "
                                     f"(host_ns {r.get('host_ns')!r}, driver_ns {r.get('driver_ns')!r})") from exc
        file_fps = self._cap.get(cv2.CAP_PROP_FPS) or 30.0
        # how many frames the video holds: trust a closed recording, else make sure by decoding
        cap_n = int(self._cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if rec.get("closed"):
            n_video = int(rec.get("frames", cap_n))
        elif ns is not None and len(ns) == cap_n:
            n_video = cap_n
        else:
            n_video = self._count_decodable()
        if ns is None:
            ns = [round(i * 1e9 / file_fps) for i in range(n_video)]
        elif len(ns) != n_video:
            warnings.warn(f"{self.path.name}: the CSV has {len(ns)} frames and the video {n_video} — the recording "
                          f"was probably not closed. Playing {min(len(ns), n_video)} frames.", RuntimeWarning,
                          stacklevel=2)
        self._ns = ns[:n_video]
        self._drv = drv[:n_video]
        self.breaks = frozenset(int(b) for b in rec.get("breaks", []))
        mode = (self.info or {}).get("mode") or {}
        whole = _interval_stats(self._ns, float(mode.get("fps") or file_fps), self.breaks)
        self.late_frames, self.estimated_missed_frames = whole.late, whole.missed
        self.real_time = False
        self.loop = False
        self.recorded_time = False
        self.paused = False
        self.speed = 1.0
        self._ended = False
        self._next = 0                 # the frame cap.read() decodes next
        self._pos = 0
        self._held: np.ndarray | None = None
        self._anchor: tuple[int, int] | None = None    # (wall ns, recorded ns) real-time pacing counts from

    def _count_decodable(self) -> int:
        import cv2
        n = 0
        while self._cap.grab():
            n += 1
        self._cap.release()
        self._cap = cv2.VideoCapture(str(self.path))      # back to the start
        return n

    # ---- what the recording holds ------------------------------------------------------------
    @property
    def frame_count(self) -> int:
        return len(self._ns)

    @property
    def duration_s(self) -> float:
        """Recorded time from the first frame to the last, in seconds."""
        return (self._ns[-1] - self._ns[0]) / 1e9 if self._ns else 0.0

    @property
    def position(self) -> int:
        return self._pos

    @property
    def position_s(self) -> float:
        """Recorded time of `position` from the first frame, in seconds."""
        return self.time_s(self._pos)

    @property
    def status(self) -> str:
        """"playing", "paused" or "ended"."""
        return "ended" if self._ended else "paused" if self.paused else "playing"

    def time_s(self, index: int) -> float:
        """Recorded time of frame `index` from the first frame, in seconds."""
        return (self.recorded_ns(index) - self._ns[0]) / 1e9 if self._ns else 0.0

    def recorded_ns(self, index: int) -> int:
        """The host_ns recorded for frame `index` (clamped to the recording)."""
        return self._ns[min(max(int(index), 0), len(self._ns) - 1)] if self._ns else 0

    def recorded_driver(self, index: int) -> tuple[int | None, str | None]:
        """(driver_ns, driver_clock) recorded for frame `index` (clamped to the recording); (None, None) when the
        CSV cell is empty, the CSV has no driver columns, or there is no CSV."""
        return self._drv[min(max(int(index), 0), len(self._drv) - 1)] if self._drv else (None, None)

    # ---- controls ---------------------------------------------------------------------------
    def seek(self, frame: int | None = None, *, seconds: float | None = None) -> int:
        """Moves to a frame number, or to the first frame recorded at or after `seconds` from the first frame.
        Clamped to the recording. The next `read()` returns that frame. Returns the frame number."""
        import cv2
        if (frame is None) == (seconds is None):
            raise ValueError("give either a frame number or seconds")
        if not self._ns:
            return 0
        if seconds is not None:
            frame = bisect.bisect_left(self._ns, self._ns[0] + round(float(seconds) * 1e9))
        i = min(max(int(frame), 0), len(self._ns) - 1)
        self._cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        got = int(round(self._cap.get(cv2.CAP_PROP_POS_FRAMES)))
        if got != i:
            raise OSError(f"{self.path.name}: seeking to frame {i} landed on {got}")
        self._next = self._pos = i
        self._held = None
        self._anchor = None
        self._ended = False
        return i

    def set_speed(self, speed: float) -> None:
        """Playback speed for `real_time`: 2.0 is twice as fast as recorded, 0.5 half."""
        if not float(speed) > 0:
            raise ValueError(f"speed must be positive: {speed!r}")
        self.speed = float(speed)
        self._anchor = None

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False
        self._anchor = None

    # ---- the VideoCapture surface Device uses -------------------------------------------------
    def isOpened(self) -> bool:
        return self._cap.isOpened()

    def _decode(self) -> tuple[bool, Any]:
        if self._next >= len(self._ns):          # the end, also of a video longer than its CSV
            return False, None
        return self._cap.read()

    def read(self) -> tuple[bool, np.ndarray | None]:
        if self.paused and self._held is not None:
            return True, self._held
        ok, img = self._decode()
        if not ok and self.loop and self._ns:
            self.seek(0)
            ok, img = self._decode()
        if not ok or img is None:
            self._ended = True
            return False, None
        self._pos = self._next
        self._next += 1
        self._held = img
        if self.real_time:
            if self._pos in self.breaks:          # a pause in recording: carry on without waiting it out
                self._anchor = None
            self._wait_for(self.recorded_ns(self._pos))
        return True, img

    def _wait_for(self, rec_ns: int) -> None:
        now = time.perf_counter_ns()
        if self._anchor is None:
            self._anchor = (now, rec_ns)
            return
        due = self._anchor[0] + (rec_ns - self._anchor[1]) / self.speed
        if due < self._anchor[0]:                     # time went backwards in the CSV: start counting again here
            self._anchor = (now, rec_ns)
            return
        while (rem := due - time.perf_counter_ns()) > 0:
            time.sleep(min(rem / 1e9, 0.005))

    def get(self, prop: int) -> float:
        import cv2
        mode = (self.info or {}).get("mode") or {}
        if prop == cv2.CAP_PROP_FRAME_WIDTH and "width" in mode:
            return float(mode["width"])
        if prop == cv2.CAP_PROP_FRAME_HEIGHT and "height" in mode:
            return float(mode["height"])
        if prop == cv2.CAP_PROP_FPS and "fps" in mode:
            return float(mode["fps"])
        if prop == cv2.CAP_PROP_FOURCC and "fourcc" in mode:
            return _fourcc_int(mode["fourcc"])
        if prop == cv2.CAP_PROP_POS_FRAMES:
            return float(self._pos)
        if prop == cv2.CAP_PROP_FRAME_COUNT:
            return float(self.frame_count)
        return float(self._cap.get(prop))

    def set(self, prop: int, value: float) -> bool:
        return False                                  # a recording's mode cannot be changed

    def getBackendName(self) -> str:
        mode = (self.info or {}).get("mode") or {}
        return str(mode.get("backend_name", self._cap.getBackendName()))

    def release(self) -> None:
        self._cap.release()
