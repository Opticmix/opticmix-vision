"""카메라 열기 · 프레임 획득 · 스테레오 분리.

카메라는 UVC 표준 장치라 OS 기본 드라이버로 잡히고, 여기서는 OpenCV `VideoCapture` 로 연다.
해상도 / fps / fourcc / 백엔드는 전부 **설정값** 이다 — 이 파일은 카메라가 어떤 모드를 내는지
알지 못하고, 알아야 하는 값은 `DeviceConfig` 로 받는다.

스테레오 포장 방식(`layout`)과 좌우 순서(`lr_order`)는 ❓ 실기기 브링업으로 확정되는 값이다.
그래서 `DeviceConfig` 에 기본값이 없다 — 호출자가 명시하거나
`DeviceConfig.from_bringup_constants()` 로 브링업 산출물(`oem_uvc_constants.py`)을 읽는다.

  layout    "sbs"         한 프레임 가로로 [첫째 | 둘째]           (W = 2·w)
            "tb"          한 프레임 세로로 [첫째 / 둘째]           (H = 2·h)
            "plane_pack"  채널 2개짜리 프레임 (H, W, 2). 채널 0 = 첫째, 채널 1 = 둘째.
                          (YUY2 로 태그된 raw 버퍼에서 Y 바이트 = 첫째, U/V 바이트 = 둘째 인 방식.
                           OpenCV 에서는 CAP_PROP_CONVERT_RGB=0 이어야 (H, W, 2) 로 들어온다)
  lr_order  "first_is_left"   첫째 반쪽이 왼쪽 카메라
            "first_is_right"  첫째 반쪽이 오른쪽 카메라

타임스탬프: `StereoFrame.host_ns` 는 `cap.read()` 가 돌아온 직후의 호스트 단조시계
(`time.perf_counter_ns`) 다. 장치 PTS 가 아니다 — 장치 PTS 노출 여부는 ❓ 미검증이며,
OpenCV `CAP_PROP_POS_MSEC` 는 라이브 장치에서 드라이버/호스트 시계인 경우가 많아 여기서는 쓰지 않는다.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping

import numpy as np

__all__ = [
    "LAYOUTS", "LR_ORDERS", "BACKENDS",
    "DeviceConfig", "NegotiatedMode", "StereoFrame", "DeviceError",
    "split_stereo", "Device",
]

LAYOUTS = ("sbs", "tb", "plane_pack")
LR_ORDERS = ("first_is_left", "first_is_right")

# Setting any of these renegotiates the capture format, which discards a pixel
# format set earlier. They belong in DeviceConfig, where the open order is
# controlled, never in extra_props.
_FORMAT_PROPS = frozenset({
    "CAP_PROP_FRAME_WIDTH", "CAP_PROP_FRAME_HEIGHT", "CAP_PROP_FPS", "CAP_PROP_FOURCC",
})
BACKENDS = ("any", "dshow", "msmf", "v4l2", "avfoundation")


class DeviceError(RuntimeError):
    """카메라 열기/읽기 실패. 조용히 빈 프레임을 돌려주지 않는다."""


@dataclass(frozen=True)
class DeviceConfig:
    """카메라 설정. width/height/fps/fourcc 는 **요청값** — 실제 협상 결과는 `Device.mode` 로 확인한다.

    device     OpenCV 장치 인덱스(int) 또는 경로 문자열("/dev/video0").
    backend    "any" | "dshow" | "msmf" (Windows) | "v4l2" (Linux) | "avfoundation" (macOS).
    width/height/fps/fourcc  None 이면 드라이버 기본 모드를 그대로 쓴다 (설정 호출 생략).
    layout / lr_order        필수. 기본값 없음 (❓ 브링업 확정 항목).
    to_gray    True 면 3채널로 디코드된 프레임을 8bit gray 로 바꿔 분리한다 (mono 센서라 정보 손실 없음).
    """

    layout: str
    lr_order: str
    device: int | str = 0
    backend: str = "any"
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    fourcc: str | None = None
    to_gray: bool = True
    buffer_size: int | None = 1          # 큐잉 지연 최소화. None 이면 설정 안 함.
    extra_props: Mapping[str, float] = field(default_factory=dict)   # {"CAP_PROP_AUTO_EXPOSURE": 0.25, ...}

    def __post_init__(self) -> None:
        if self.layout not in LAYOUTS:
            raise ValueError(f"layout 은 {LAYOUTS} 중 하나: {self.layout!r}")
        if self.lr_order not in LR_ORDERS:
            raise ValueError(f"lr_order 는 {LR_ORDERS} 중 하나: {self.lr_order!r}")
        if self.backend not in BACKENDS:
            raise ValueError(f"backend 는 {BACKENDS} 중 하나: {self.backend!r}")
        for name in ("width", "height"):
            v = getattr(self, name)
            if v is not None and int(v) <= 0:
                raise ValueError(f"{name} 는 양수: {v!r}")
        if self.fps is not None and float(self.fps) <= 0:
            raise ValueError(f"fps 는 양수: {self.fps!r}")
        if self.fourcc is not None and len(self.fourcc) != 4:
            raise ValueError(f"fourcc 는 4글자: {self.fourcc!r}")
        object.__setattr__(self, "extra_props", dict(self.extra_props))

    @classmethod
    def from_bringup_constants(cls, path: str | Path, **overrides: Any) -> "DeviceConfig":
        """브링업 도구 산출물 `oem_uvc_constants.py` 를 읽는다.

        파일은 대문자 상수 대입만 있는 파이썬이다 (BACKEND / DEVICE / RESOLUTION / FPS / FOURCC /
        LAYOUT / LR_ORDER ...). BACKEND 가 'opencv' 가 아니면 이 SDK 로는 열 수 없으므로 에러다
        (ffmpeg / pyuvc 백엔드는 v0 범위 밖). LAYOUT 이 'single' 이거나 LR_ORDER 가 미확정이면
        스테레오 분리를 정의할 수 없으므로 역시 에러다 — 추측으로 채우지 않는다.
        """
        ns: dict[str, Any] = {}
        src = Path(path).read_text(encoding="utf-8")
        exec(compile(src, str(path), "exec"), ns)  # noqa: S102 — 상수 대입만 있는 신뢰 파일
        kw: dict[str, Any] = {}

        backend = str(ns.get("BACKEND", "opencv"))
        if backend != "opencv":
            raise ValueError(f"BACKEND={backend!r}: 이 SDK 는 OpenCV 백엔드만 지원한다")

        if "DEVICE" in ns:
            dev = str(ns["DEVICE"])
            m = re.match(r"index (\d+)(?: \((\w+)\))?", dev)      # 'index 0 (dshow)'
            if m:
                kw["device"] = int(m.group(1))
                if m.group(2) in BACKENDS:
                    kw["backend"] = m.group(2)
            else:
                kw["device"] = dev
        if "RESOLUTION" in ns:
            w, h = ns["RESOLUTION"]
            kw["width"], kw["height"] = int(w), int(h)
        if "FPS" in ns:
            kw["fps"] = float(ns["FPS"])
        if "FOURCC" in ns and ns["FOURCC"]:
            kw["fourcc"] = str(ns["FOURCC"])

        layout = str(ns.get("LAYOUT", ""))
        if layout not in LAYOUTS:
            raise ValueError(f"LAYOUT={layout!r}: 스테레오 분리를 정의할 수 없다 (브링업 재실행 필요)")
        lr = str(ns.get("LR_ORDER", ""))
        if lr not in LR_ORDERS:
            raise ValueError(f"LR_ORDER={lr!r}: 좌우 순서 미확정 (손을 20-30 cm 앞에 두고 브링업 재실행)")
        kw["layout"], kw["lr_order"] = layout, lr
        kw.update(overrides)
        return cls(**kw)

    def replace(self, **changes: Any) -> "DeviceConfig":
        return replace(self, **changes)


@dataclass(frozen=True)
class NegotiatedMode:
    """장치가 실제로 준 모드 (`cap.get` 읽기값). 요청값과 다를 수 있다."""

    width: int
    height: int
    fps: float
    fourcc: str
    backend_name: str

    def to_dict(self) -> dict[str, Any]:
        return {"width": self.width, "height": self.height, "fps": self.fps,
                "fourcc": self.fourcc, "backend_name": self.backend_name}


@dataclass(frozen=True)
class StereoFrame:
    left: np.ndarray            # (h, w) uint8
    right: np.ndarray           # (h, w) uint8
    host_ns: int                # 호스트 단조시계, cap.read() 직후
    index: int                  # 이 Device 가 낸 프레임 순번 (장치 번호 아님)

    @property
    def size(self) -> tuple[int, int]:
        """(width, height) — 한 눈 영상."""
        h, w = self.left.shape[:2]
        return int(w), int(h)


# ---------------------------------------------------------------------------
# 순수 함수: 스테레오 분리
# ---------------------------------------------------------------------------

def _to_gray(frame: np.ndarray) -> np.ndarray:
    """(H, W) / (H, W, 1) / (H, W, 3) → (H, W) uint8. 2채널은 여기서 다루지 않는다."""
    a = np.asarray(frame)
    if a.ndim == 2:
        return a
    if a.ndim == 3 and a.shape[2] == 1:
        return a[:, :, 0]
    if a.ndim == 3 and a.shape[2] == 3:
        import cv2
        return cv2.cvtColor(a, cv2.COLOR_BGR2GRAY)
    raise ValueError(f"gray 로 바꿀 수 없는 프레임 모양: {a.shape}")


def split_stereo(frame: np.ndarray, layout: str, lr_order: str, *, to_gray: bool = True
                 ) -> tuple[np.ndarray, np.ndarray]:
    """한 프레임을 (left, right) 로 나눈다. 가능하면 뷰(view)를 돌려준다 — 복사가 필요하면 호출자가 한다.

    sbs / tb 프레임이 3채널(BGR 디코드)로 들어오면 to_gray=True 일 때 gray 로 바꾼 뒤 나눈다.
    plane_pack 은 (H, W, 2) 를 기대하고, (H, 2W) 2D 버퍼가 오면 (H, W, 2) 로 해석한다.
    """
    if layout not in LAYOUTS:
        raise ValueError(f"layout 은 {LAYOUTS} 중 하나: {layout!r}")
    if lr_order not in LR_ORDERS:
        raise ValueError(f"lr_order 는 {LR_ORDERS} 중 하나: {lr_order!r}")
    a = np.asarray(frame)

    if layout == "plane_pack":
        if a.ndim == 3 and a.shape[2] == 2:
            first, second = a[:, :, 0], a[:, :, 1]
        elif a.ndim == 2 and a.shape[1] % 2 == 0:
            packed = a.reshape(a.shape[0], a.shape[1] // 2, 2)
            first, second = packed[:, :, 0], packed[:, :, 1]
        else:
            raise ValueError(f"plane_pack 은 (H, W, 2) 또는 (H, 2W) 여야 한다: {a.shape}")
    else:
        g = _to_gray(a) if to_gray else a
        if g.ndim != 2:
            raise ValueError(f"sbs/tb 분리는 2D gray 프레임이 필요하다 (to_gray=False 면 직접 변환): {g.shape}")
        h, w = g.shape
        if layout == "sbs":
            if w % 2:
                raise ValueError(f"sbs: 폭이 홀수다 ({w})")
            first, second = g[:, : w // 2], g[:, w // 2:]
        else:
            if h % 2:
                raise ValueError(f"tb: 높이가 홀수다 ({h})")
            first, second = g[: h // 2], g[h // 2:]

    if first.shape != second.shape:
        raise ValueError(f"두 반쪽 크기가 다르다: {first.shape} vs {second.shape}")
    return (first, second) if lr_order == "first_is_left" else (second, first)


# ---------------------------------------------------------------------------
# Device
# ---------------------------------------------------------------------------

class Device:
    """OpenCV VideoCapture 위의 얇은 래퍼. `with Device(cfg) as cam: frame = cam.read()`.

    capture 인자로 VideoCapture 호환 객체(get/set/read/isOpened/release)를 주입할 수 있다 — 테스트용.
    """

    def __init__(self, config: DeviceConfig, *, capture: Any = None) -> None:
        self.config = config
        self._cap = capture
        self._injected = capture is not None
        self._mode: NegotiatedMode | None = None
        self._index = 0

    # ---- lifecycle -------------------------------------------------------
    def open(self) -> "Device":
        if self._mode is not None:
            return self
        import cv2

        if self._cap is None:
            api_name = {"any": "CAP_ANY", "dshow": "CAP_DSHOW", "msmf": "CAP_MSMF",
                        "v4l2": "CAP_V4L2", "avfoundation": "CAP_AVFOUNDATION"}[self.config.backend]
            if not hasattr(cv2, api_name):
                raise DeviceError(f"이 OpenCV 빌드에는 {api_name} 이 없다 (backend={self.config.backend})")
            self._cap = cv2.VideoCapture(self.config.device, getattr(cv2, api_name))
        cap = self._cap
        if not cap.isOpened():
            raise DeviceError(f"카메라 열기 실패: device={self.config.device!r} backend={self.config.backend}")

        c = self.config
        # Order matters, and getting it wrong is silent. Size and frame rate
        # each renegotiate the format, so a fourcc set before them is thrown
        # away and the driver keeps its default. Measured on the camera:
        # fourcc first gave YUY2 at 9.3 fps; fourcc last gave MJPG at 90.4 fps,
        # from the same requested mode. Set the pixel format last.
        if c.width is not None:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(c.width))
        if c.height is not None:
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(c.height))
        if c.fps is not None:
            cap.set(cv2.CAP_PROP_FPS, float(c.fps))
        if c.fourcc:
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*c.fourcc))
        if c.buffer_size is not None:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, int(c.buffer_size))
        if c.layout == "plane_pack":
            cap.set(cv2.CAP_PROP_CONVERT_RGB, 0)       # raw 2채널 버퍼 그대로
        for name, value in c.extra_props.items():
            # Same trap from the other direction: these renegotiate the format,
            # so setting one here would undo the fourcc above and drop the
            # stream to the driver's default. Exposure, gain and the
            # illuminator were measured to be safe here; these are not.
            if name in _FORMAT_PROPS:
                raise DeviceError(
                    f"{name} 은 extra_props 로 설정하면 안 된다 — 포맷이 재협상돼 fourcc 가 무시된다. "
                    f"DeviceConfig 의 width/height/fps 를 써라."
                )
            cap.set(getattr(cv2, name), float(value))

        fcc = int(cap.get(cv2.CAP_PROP_FOURCC))
        fcc_s = "".join(chr((fcc >> (8 * i)) & 0xFF) for i in range(4)) if fcc > 0 else ""
        backend_name = ""
        if hasattr(cap, "getBackendName"):
            try:
                backend_name = str(cap.getBackendName())
            except Exception:                           # 일부 빌드는 미지원
                backend_name = ""
        self._mode = NegotiatedMode(
            width=int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            height=int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            fps=float(cap.get(cv2.CAP_PROP_FPS)),
            fourcc=fcc_s, backend_name=backend_name,
        )
        return self

    def close(self) -> None:
        if self._cap is not None and not self._injected:
            self._cap.release()
            self._cap = None
        self._mode = None

    def __enter__(self) -> "Device":
        return self.open()

    def __exit__(self, *exc: Any) -> None:
        self.close()

    @property
    def is_open(self) -> bool:
        return self._mode is not None

    @property
    def mode(self) -> NegotiatedMode:
        if self._mode is None:
            raise DeviceError("open() 전에는 협상 모드가 없다")
        return self._mode

    # ---- frames ------------------------------------------------------------
    def read_raw(self) -> tuple[np.ndarray, int]:
        """분리 전 원본 프레임과 호스트 시각(ns). 실패는 예외."""
        if self._mode is None:
            raise DeviceError("open() 먼저")
        ok, img = self._cap.read()
        t = time.perf_counter_ns()
        if not ok or img is None:
            raise DeviceError("프레임 읽기 실패 (타임아웃 또는 장치 분리)")
        return img, t

    def read(self) -> StereoFrame:
        img, t = self.read_raw()
        left, right = split_stereo(img, self.config.layout, self.config.lr_order,
                                   to_gray=self.config.to_gray)
        frame = StereoFrame(left=left, right=right, host_ns=t, index=self._index)
        self._index += 1
        return frame

    # ---- properties (UVC 컨트롤 통로; led.py 도 이걸 쓴다) --------------------
    def get_prop(self, prop: str | int) -> float:
        import cv2
        if self._cap is None:
            raise DeviceError("open() 먼저")
        pid = getattr(cv2, prop) if isinstance(prop, str) else int(prop)
        return float(self._cap.get(pid))

    def set_prop(self, prop: str | int, value: float) -> bool:
        import cv2
        if self._cap is None:
            raise DeviceError("open() 먼저")
        pid = getattr(cv2, prop) if isinstance(prop, str) else int(prop)
        return bool(self._cap.set(pid, float(value)))
