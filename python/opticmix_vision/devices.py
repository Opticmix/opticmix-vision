"""연결된 카메라 열거 — 이름 기준.

이 카메라는 USB 시리얼 번호를 보고하지 않는다. 인덱스는 다른 카메라를 꽂거나
빼는 것만으로 밀리므로, 인덱스로 장치를 고르면 조용히 다른 카메라를 연다.
그래서 선택은 항상 이름으로 한다.

같은 `find_camera()` 가 measurements 스크립트 3곳에 각자의 정규식으로 복사돼
있었다. 여기가 그 하나다.

구현(2026-10-29): ffmpeg 출력 파싱·이름으로 고르기는 Rust 코어(`devices.rs`)가 한다. ffmpeg 실행과
DirectShow 직접 열거로 물러서기는 여기(Python)에 남는다 — 호출자가 그 단계를 바꿔 끼울 수 있게.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass

from opticmix_vision import _native

__all__ = [
    "DeviceInfo", "Mode", "NoMatch", "MultipleMatches",
    "list_devices", "list_modes", "resolve_by_name",
]


@dataclass(frozen=True)
class DeviceInfo:
    index: int
    name: str


@dataclass(frozen=True)
class Mode:
    fourcc: str
    width: int
    height: int
    max_fps: float

    def label(self) -> str:
        """콤보박스에 보일 문자열."""
        return f"{self.width}×{self.height} {self.fourcc} · {self.max_fps:g} fps"


class NoMatch(LookupError):
    """이름에 맞는 카메라가 없다."""


class MultipleMatches(LookupError):
    """이름에 맞는 카메라가 둘 이상 — 시리얼이 없어 구분할 수 없다."""


def _ffmpeg_devices() -> str:
    return subprocess.run(
        ["ffmpeg", "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
        capture_output=True, encoding="utf-8", errors="replace",
    ).stderr


def _ffmpeg_options(name: str) -> str:
    return subprocess.run(
        ["ffmpeg", "-hide_banner", "-f", "dshow", "-list_options", "true", "-i", f"video={name}"],
        capture_output=True, encoding="utf-8", errors="replace",
    ).stderr


def _native_devices() -> list[str]:
    """ffmpeg 없이 DirectShow 를 직접 열거한다(Windows, 의존성 0). 다른 OS 나 실패는 빈 목록."""
    try:
        from ._ctrl_dshow import enumerate_video_devices
        return [n or "" for n in enumerate_video_devices()]
    except Exception:  # noqa: BLE001 — 열거 실패는 "못 찾음"으로 보인다
        return []


def list_devices(*, _raw: str | None = None) -> list[DeviceInfo]:
    """DirectShow 열거 순서 그대로의 비디오 장치 목록. 노트북 웹캠도 포함한다 —
    사용자가 그중에서 골라야 하므로 걸러내지 않는다.

    ffmpeg 는 고객 PC 에 대개 없다. 없으면 DirectShow 를 직접 열거한다 — 순서는 OpenCV 와 같다."""
    if _raw is None:
        try:
            _raw = _ffmpeg_devices()
        except (OSError, subprocess.SubprocessError):
            return [DeviceInfo(index=i, name=n) for i, n in enumerate(_native_devices())]
    return [DeviceInfo(index=i, name=n) for i, n in _native.core().list_devices(_raw)]


def resolve_by_name(needle: str, *, _raw: str | None = None) -> DeviceInfo:
    """이름 부분일치(대소문자 무시)로 장치 하나를 고른다. 애매하면 추측하지 않는다."""
    devices = list_devices(_raw=_raw)
    index, name = _native.core().pick_by_name(needle, [(d.index, d.name) for d in devices])
    return DeviceInfo(index=index, name=name)


def list_modes(name: str, *, _raw: str | None = None) -> list[Mode]:
    """장치가 스스로 보고하는 모드들.

    빈 목록은 "모드가 없다" 가 아니라 **"알아내지 못했다"** 이다. 호출자는 확인된
    모드 하나로 물러서야지, 모드가 없다고 말하면 안 된다.
    """
    if _raw is None:
        try:
            _raw = _ffmpeg_options(name)
        except (OSError, subprocess.SubprocessError):
            return []
    return [Mode(fourcc=f, width=w, height=h, max_fps=fps) for f, w, h, fps in _native.core().list_modes(name, _raw)]
