"""ffmpeg 출력 파싱 — 실기기 없이 검증한다. 아래 문자열은 실제 실행에서 딴 형식이고, 다른 장치 이름은 가짜로 바꿨다."""
from __future__ import annotations

import os
import sys

import pytest

from opticmix_vision.devices import (
    DeviceInfo,
    Mode,
    MultipleMatches,
    NoMatch,
    find_v4l2_node,
    list_devices,
    list_modes,
    pick_by_usb_location,
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


# ---------------------------------------------------------------- Windows Media Foundation: a fake list, nothing enumerated
MF_LIST = ["Example Webcam", "WN Dual Camera", "Example IR Camera"]


def test_msmf_list_keeps_media_foundation_order_and_ignores_ffmpeg(monkeypatch):
    from opticmix_vision import devices
    monkeypatch.setattr(devices, "_mf_devices", lambda: list(MF_LIST))
    got = list_devices(backend="msmf", _raw=FFMPEG_DEVICES)          # the DirectShow text must not be used
    assert got == [DeviceInfo(0, "Example Webcam"), DeviceInfo(1, "WN Dual Camera"), DeviceInfo(2, "Example IR Camera")]
    assert resolve_by_name("wn dual", backend="msmf") == DeviceInfo(1, "WN Dual Camera")


def test_msmf_lookup_has_the_directshow_rules_for_none_and_two(monkeypatch):
    from opticmix_vision import devices
    monkeypatch.setattr(devices, "_mf_devices", lambda: list(MF_LIST))
    with pytest.raises(NoMatch, match=r"Video devices found: \['Example Webcam', 'WN Dual Camera', 'Example IR Camera'\]"):
        resolve_by_name("nonexistent", backend="msmf")
    monkeypatch.setattr(devices, "_mf_devices", lambda: ["WN Dual Camera", "WN Dual Camera"])
    with pytest.raises(MultipleMatches, match=r"^2 cameras match"):
        resolve_by_name("WN Dual Camera", backend="msmf")


def test_an_unknown_listing_backend_is_refused():
    with pytest.raises(ValueError, match="'dshow' or 'msmf'"):
        list_devices(backend="v4l2")


def test_os_name_reads_the_media_foundation_list_for_msmf(monkeypatch):
    # presets learn the model from this name; with msmf it has to be the Media Foundation entry at that index
    import sys

    from opticmix_vision import devices
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(devices, "_mf_devices", lambda: list(MF_LIST))
    monkeypatch.setattr(devices, "_native_devices", lambda: ["Example Webcam", "Example Rear Webcam"])
    assert devices._os_name(1, "msmf") == "WN Dual Camera"
    assert devices._os_name(1, "dshow") == "Example Rear Webcam"
    assert devices._os_name(3, "msmf") is None

    def broken():
        raise OSError("enumeration failed")
    monkeypatch.setattr(devices, "_mf_devices", broken)
    assert devices._os_name(1, "msmf") is None                      # a name we cannot read is left out


@pytest.mark.skipif(__import__("sys").platform == "win32", reason="on Windows this would enumerate the cameras")
def test_media_foundation_list_is_empty_off_windows():
    pytest.importorskip("opticmix_vision._core")
    assert list_devices(backend="msmf") == []


# ---------------------------------------------------------------- USB location: made-up lists, nothing is enumerated
LOC_A = "PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(1)"
LOC_B = "PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(4)#USB(2)"
TWO_WEEVIA = [("WN Dual Camera", LOC_A), ("Example Webcam", None), ("WN Dual Camera", LOC_B)]


def _directshow(monkeypatch, listed):
    """A made-up DirectShow list of (name, USB location), as enumerating DirectShow directly gives it (no ffmpeg)."""
    from opticmix_vision import devices

    def no_ffmpeg():
        raise FileNotFoundError("ffmpeg")
    monkeypatch.setattr(devices, "_ffmpeg_devices", no_ffmpeg)
    monkeypatch.setattr(devices, "_native_devices", lambda: list(listed))


def test_listed_devices_carry_their_usb_location(monkeypatch):
    _directshow(monkeypatch, TWO_WEEVIA)
    assert list_devices() == [DeviceInfo(0, "WN Dual Camera", LOC_A), DeviceInfo(1, "Example Webcam"),
                              DeviceInfo(2, "WN Dual Camera", LOC_B)]


def test_two_cameras_of_one_name_are_told_apart_by_usb_location(monkeypatch):
    _directshow(monkeypatch, TWO_WEEVIA)
    assert resolve_by_name("wn dual", usb_location=LOC_B) == DeviceInfo(2, "WN Dual Camera", LOC_B)
    assert resolve_by_name("WN Dual Camera", usb_location=LOC_A.lower()).index == 0    # case does not matter


def test_two_cameras_of_one_name_point_to_their_usb_locations(monkeypatch):
    _directshow(monkeypatch, TWO_WEEVIA)
    with pytest.raises(MultipleMatches) as exc:
        resolve_by_name("wn dual")
    assert str(exc.value) == (
        "2 cameras match 'wn dual' — they cannot be told apart by name (no USB serial number). Unplug all but one, "
        "or pick one by USB location (usb_location: the port it is plugged into, not a serial number): "
        f"['{LOC_A}', '{LOC_B}']")


def test_no_camera_at_the_usb_location_lists_names_and_locations(monkeypatch):
    _directshow(monkeypatch, TWO_WEEVIA)
    with pytest.raises(NoMatch) as exc:
        resolve_by_name("wn dual", usb_location="PCIROOT(0)#USB(9)")
    assert str(exc.value) == (
        "No camera matches 'wn dual' at USB location 'PCIROOT(0)#USB(9)'. Video devices found (name, USB location): "
        f"[('WN Dual Camera', '{LOC_A}'), ('Example Webcam', None), ('WN Dual Camera', '{LOC_B}')]. "
        "None: the USB location could not be read")


def test_a_list_without_usb_locations_cannot_pick_by_one(monkeypatch):
    from opticmix_vision import devices
    monkeypatch.setattr(devices, "_mf_devices", lambda: ["WN Dual Camera", "WN Dual Camera"])    # names only
    assert [d.usb_location for d in list_devices(backend="msmf")] == [None, None]
    with pytest.raises(NoMatch, match=r"\[\('WN Dual Camera', None\), \('WN Dual Camera', None\)\]\. "
                                      r"None: the USB location could not be read$"):
        resolve_by_name("WN Dual Camera", usb_location=LOC_A, backend="msmf")
    with pytest.raises(MultipleMatches, match=r"cannot be told apart \(no USB serial number\)\. Unplug all but one"):
        resolve_by_name("WN Dual Camera", backend="msmf")


def test_pick_by_usb_location_picks_from_a_list_you_already_have():
    listed = [DeviceInfo(0, "WN Dual Camera", LOC_A), DeviceInfo(5, "WN Dual Camera", LOC_B)]
    assert pick_by_usb_location("WN Dual Camera", LOC_B, listed) == DeviceInfo(5, "WN Dual Camera", LOC_B)
    with pytest.raises(NoMatch):
        pick_by_usb_location("Example", LOC_A, listed)          # another camera's location is not a match


def test_media_foundation_entries_carry_their_usb_location(monkeypatch):
    from opticmix_vision import devices
    monkeypatch.setattr(devices, "_mf_devices", lambda: [("Example Webcam", None), ("WN Dual Camera", LOC_B)])
    assert resolve_by_name("WN Dual Camera", usb_location=LOC_B, backend="msmf") == \
        DeviceInfo(1, "WN Dual Camera", LOC_B)


def _link(target, link):
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        if sys.platform != "win32":
            raise
        import _winapi                                     # a junction needs no privilege on Windows
        _winapi.CreateJunction(str(target), str(link))


def _sysfs_usb(tmp_path, nodes):
    """sysfs as Linux lays it out: videoN/device -> the USB interface folder, whose parent folder is the USB device
    (it has idVendor) and is named after the port, e.g. 1-4.2. (Interface folders are named 1-4.2:1.0 on Linux; ':'
    is not allowed in a Windows file name, so 1-4.2_1.0 here.)"""
    usb, cls = tmp_path / "devices" / "usb1", tmp_path / "class"
    for node, (name, index, port) in nodes.items():
        (cls / node).mkdir(parents=True)
        (cls / node / "name").write_text(name + "\n")
        (cls / node / "index").write_text(f"{index}\n")
        if port:
            (usb / port / f"{port}_1.0").mkdir(parents=True, exist_ok=True)
            (usb / port / "idVendor").write_text("1d6b\n")
            _link(usb / port / f"{port}_1.0", cls / node / "device")
    return str(cls)


TWO_ON_LINUX = {"video0": ("WN Dual Camera", 0, "1-2"), "video1": ("WN Dual Camera", 1, "1-2"),
                "video2": ("WN Dual Camera", 0, "1-4.2"), "video3": ("WN Dual Camera", 1, "1-4.2"),
                "video4": ("Laptop webcam", 0, "1-5")}


def test_v4l2_two_cameras_are_told_apart_by_usb_location(tmp_path):
    root = _sysfs_usb(tmp_path, TWO_ON_LINUX)
    assert find_v4l2_node("wn dual", usb_location="1-4.2", _sysfs=root) == "/dev/video2"
    assert find_v4l2_node("wn dual", usb_location="1-2", _sysfs=root) == "/dev/video0"
    with pytest.raises(MultipleMatches) as exc:
        find_v4l2_node("WN Dual Camera", _sysfs=root)
    assert str(exc.value) == (
        "2 video capture nodes match 'WN Dual Camera' — they cannot be told apart by name (no USB serial number). "
        "Unplug all but one, or pick one by USB location (usb_location: the port it is plugged into, not a serial "
        "number): [('/dev/video0', '1-2'), ('/dev/video2', '1-4.2')]")


def test_v4l2_no_node_at_the_usb_location_lists_nodes_and_locations(tmp_path):
    root = _sysfs_usb(tmp_path, TWO_ON_LINUX)
    with pytest.raises(NoMatch) as exc:
        find_v4l2_node("WN Dual Camera", usb_location="1-5", _sysfs=root)       # the webcam's port
    assert str(exc.value).startswith(
        "No video capture node matches 'WN Dual Camera' at USB location '1-5'. Video nodes found (node, USB "
        "location): [('/dev/video0 WN Dual Camera', '1-2'), ('/dev/video1 WN Dual Camera', '1-2'), ")


def test_an_empty_usb_location_is_refused(monkeypatch, tmp_path):
    # an empty or blank usb_location must not match a device whose USB location could not be read
    _directshow(monkeypatch, [("WN Dual Camera", None), ("WN Dual Camera", LOC_B)])
    root = _sysfs(tmp_path, {"video0": ("WN Dual Camera", 0)})                  # no device link: location None
    for blank in ("", "  "):
        with pytest.raises(ValueError, match=r"^usb_location is empty"):
            resolve_by_name("WN Dual Camera", usb_location=blank)
        with pytest.raises(ValueError, match=r"^usb_location is empty"):
            find_v4l2_node("WN Dual Camera", usb_location=blank, _sysfs=root)


def test_v4l2_node_with_no_usb_device_has_no_location(tmp_path):
    root = _sysfs(tmp_path, {"video0": ("WN Dual Camera", 0)})                  # no device link
    with pytest.raises(NoMatch, match=r"\[\('/dev/video0 WN Dual Camera', None\)\]\. "
                                      r"None: the USB location could not be read$"):
        find_v4l2_node("WN Dual Camera", usb_location="1-2", _sysfs=root)
