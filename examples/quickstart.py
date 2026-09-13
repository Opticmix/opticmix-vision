"""quickstart — 카메라 열기 → 스테레오 분리 → (선택) 캘리브 로드·rectify → 화면 표시.

사용:
  # 브링업 산출물로 설정 (권장 — layout / lr_order / 모드가 실측값)
  python examples/quickstart.py --constants out/bringup_opencv/oem_uvc_constants.py

  # 또는 직접 지정
  python examples/quickstart.py --device 0 --backend dshow --layout sbs --lr-order first_is_left \
                                --width 2560 --height 800 --fps 60 --fourcc MJPG

  # 캘리브 파일이 있으면 rectify 결과도 함께 표시
  python examples/quickstart.py --constants ... --calib calib.json

키: q 종료, s 현재 좌/우 PNG 저장.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # 설치 없이 실행 가능
for _stream in (sys.stdout, sys.stderr):                          # Windows cp949 콘솔에서 한글·기호 출력 보장
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from opticmix_vision import (  # noqa: E402
    LAYOUTS, LR_ORDERS, Device, DeviceConfig, load_calibration, remap_pair, stereo_rectify_maps,
)


def parse() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--constants", type=Path, help="브링업 산출물 oem_uvc_constants.py")
    ap.add_argument("--device", default=0, help="장치 인덱스 또는 경로 (기본 0)")
    ap.add_argument("--backend", default="any", choices=["any", "dshow", "msmf", "v4l2", "avfoundation"])
    ap.add_argument("--layout", choices=list(LAYOUTS))
    ap.add_argument("--lr-order", choices=list(LR_ORDERS))
    ap.add_argument("--width", type=int)
    ap.add_argument("--height", type=int)
    ap.add_argument("--fps", type=float)
    ap.add_argument("--fourcc")
    ap.add_argument("--calib", type=Path, help="StereoCalibration JSON (있으면 rectify 표시)")
    ap.add_argument("--no-window", action="store_true", help="화면 없이 100프레임 fps 만 측정")
    return ap.parse_args()


def build_config(a: argparse.Namespace) -> DeviceConfig:
    if a.constants:
        over = {k: v for k, v in dict(width=a.width, height=a.height, fps=a.fps, fourcc=a.fourcc).items() if v}
        return DeviceConfig.from_bringup_constants(a.constants, **over)
    if not (a.layout and a.lr_order):
        sys.exit("--constants 가 없으면 --layout 과 --lr-order 를 반드시 지정 (❓ 브링업 확정 항목이라 기본값 없음)")
    dev: int | str = int(a.device) if str(a.device).isdigit() else str(a.device)
    return DeviceConfig(layout=a.layout, lr_order=a.lr_order, device=dev, backend=a.backend,
                        width=a.width, height=a.height, fps=a.fps, fourcc=a.fourcc)


def main() -> int:
    a = parse()
    cfg = build_config(a)
    maps = None
    if a.calib:
        calib = load_calibration(a.calib)
        maps = stereo_rectify_maps(calib)
        print(f"calib: baseline {calib.baseline_mm:.3f} mm, rectified f {maps.rectified_focal_px:.1f} px")

    with Device(cfg) as cam:
        print("negotiated:", cam.mode.to_dict())
        if a.no_window:
            t0 = time.perf_counter()
            n = 100
            for _ in range(n):
                cam.read()
            dt = time.perf_counter() - t0
            print(f"{n} frames in {dt:.3f} s -> {n / dt:.1f} fps (host-measured)")
            return 0

        import cv2
        n, t0 = 0, time.perf_counter()
        while True:
            f = cam.read()
            n += 1
            view = np.hstack([f.left, f.right])
            if maps is not None:
                l2, r2 = remap_pair(f.left, f.right, maps)
                view = np.vstack([view, np.hstack([l2, r2])])
            if n % 30 == 0:
                fps = n / (time.perf_counter() - t0)
                cv2.setWindowTitle("opticmix_vision quickstart", f"L | R  {f.size[0]}x{f.size[1]}  {fps:.1f} fps")
            cv2.imshow("opticmix_vision quickstart", view)
            k = cv2.waitKey(1) & 0xFF
            if k == ord("q"):
                break
            if k == ord("s"):
                cv2.imwrite(f"left_{f.index:06d}.png", f.left)
                cv2.imwrite(f"right_{f.index:06d}.png", f.right)
                print(f"saved left/right_{f.index:06d}.png")
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
