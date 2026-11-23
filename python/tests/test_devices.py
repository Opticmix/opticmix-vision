"""ffmpeg 출력 파싱 — 실기기 없이 검증한다. 아래 문자열은 실제 실행에서 딴 형식이고, 다른 장치 이름은 가짜로 바꿨다."""
from __future__ import annotations

import pytest

from opticmix_vision.devices import (
    DeviceInfo,
    Mode,
    MultipleMatches,
    NoMatch,
    find_v4l2_node,
    list_devices,
    list_modes,
    resolve_by_name,
)

FFMPEG_DEVICES = """[dshow @ 000001] "WN Dual Camera" (video)
[dshow @ 000001]   Alternative name "@device_pnp_\\\\?\\usb#vid_1d6b&pid_0200"
[dshow @ 000001] "Example Front Webcam" (video)
[dshow @ 000001] "Example Rear Webcam" (video)
[dshow @ 000001] "Microphone (Example Audio)" (audio)
"""

FFMPEG_OPTIONS = """[in#0] DirectShow video device options (from video devices)
[in#0]  Pin "캡쳐" (alternative pin name "0")
[in#0]   vcodec=mjpeg  min s=1600x800 fps=30 max s=1600x800 fps=90.0001
[in#0]   vcodec=mjpeg  min s=2560x800 fps=30 max s=2560x800 fps=90.0001
[in#0]   pixel_format=yuyv422  min s=1600x800 fps=10 max s=1600x800 fps=45
"""


def test_lists_video_devices_in_enumeration_order():
    got = list_devices(_raw=FFMPEG_DEVICES)
    assert got == [
        DeviceInfo(index=0, name="WN Dual Camera"),
        DeviceInfo(index=1, name="Example Front Webcam"),
        DeviceInfo(index=2, name="Example Rear Webcam"),
    ]


def test_audio_devices_are_not_cameras():
    assert all("Microphone" not in d.name for d in list_devices(_raw=FFMPEG_DEVICES))


def test_resolve_by_name_is_case_insensitive_substring():
    assert resolve_by_name("wn dual", _raw=FFMPEG_DEVICES).index == 0


def test_no_match_names_what_was_seen():
    with pytest.raises(NoMatch) as exc:
        resolve_by_name("nonexistent", _raw=FFMPEG_DEVICES)
    assert "Example Rear Webcam" in str(exc.value)


def test_two_matches_refuses_rather_than_guessing():
    raw = FFMPEG_DEVICES.replace("Example Front Webcam", "WN Dual Camera")
    with pytest.raises(MultipleMatches):
        resolve_by_name("wn dual", _raw=raw)


def test_modes_are_parsed_with_codec_size_and_max_fps():
    got = list_modes("WN Dual Camera", _raw=FFMPEG_OPTIONS)
    assert Mode(fourcc="MJPG", width=1600, height=800, max_fps=90.0) in got
    assert Mode(fourcc="MJPG", width=2560, height=800, max_fps=90.0) in got
    assert Mode(fourcc="YUY2", width=1600, height=800, max_fps=45.0) in got


def test_duplicate_mode_lines_collapse():
    raw = FFMPEG_OPTIONS + FFMPEG_OPTIONS
    assert len(list_modes("WN Dual Camera", _raw=raw)) == 3


def test_no_ffmpeg_output_gives_no_modes_rather_than_raising():
    assert list_modes("WN Dual Camera", _raw="") == []


def test_mode_label_is_what_a_combo_box_shows():
    assert Mode("MJPG", 1600, 800, 90.0).label() == "1600×800 MJPG · 90 fps"


# ---------------------------------------------------------------- Linux: /sys/class/video4linux, faked in a folder
def _sysfs(tmp_path, nodes):
    for n, (name, index) in nodes.items():
        (tmp_path / n).mkdir()
        (tmp_path / n / "name").write_text(name + "\n")
        (tmp_path / n / "index").write_text(f"{index}\n")
    return str(tmp_path)


def test_v4l2_node_is_the_named_node_with_index_0(tmp_path):
    root = _sysfs(tmp_path, {"video0": ("Webcam gadget: WN Dual Camera", 0),
                             "video1": ("Webcam gadget: WN Dual Camera", 1)})  # names as on the camera, 2026-11-23
    assert find_v4l2_node("wn dual", _sysfs=root) == "/dev/video0"


def test_v4l2_metadata_node_alone_is_not_a_match(tmp_path):
    root = _sysfs(tmp_path, {"video0": ("Integrated Webcam", 0), "video1": ("WN Dual Camera", 1)})
    with pytest.raises(NoMatch, match=r"Video nodes found: \['/dev/video0 Integrated Webcam', '/dev/video1 WN Dual Camera'\]"):
        find_v4l2_node("WN Dual Camera", _sysfs=root)


def test_v4l2_two_cameras_are_refused(tmp_path):
    root = _sysfs(tmp_path, {"video0": ("WN Dual Camera", 0), "video1": ("WN Dual Camera", 1),
                             "video2": ("WN Dual Camera", 0), "video3": ("WN Dual Camera", 1)})
    with pytest.raises(MultipleMatches, match=r"^2 video capture nodes .*\['/dev/video0', '/dev/video2'\]$"):
        find_v4l2_node("WN Dual Camera", _sysfs=root)


def test_v4l2_no_sysfs_is_no_match(tmp_path):
    with pytest.raises(NoMatch, match=r"Video nodes found: \[\]"):
        find_v4l2_node("WN Dual Camera", _sysfs=str(tmp_path / "absent"))
