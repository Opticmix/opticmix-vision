"""라이브 손 스켈레톤 오버레이 — L3 완료정의 ②(오버레이가 손에 붙음) / ③(양손) 육안 판정 도구.

  python examples/hands_overlay.py --device-name "WN Dual Camera" --model hand_model.onnx \
      --layout sbs --lr-order first_is_left --intrinsics-left left_intrinsics.json
  python examples/hands_overlay.py --stub          # 실모델 없이 파이프라인만 (StubRuntime)

투영: --intrinsics-left(CameraIntrinsics.to_dict() JSON: model/K/D/size)가 있으면 왼눈 KB4 로 3D→2D 투영해 그린다.
없으면 그리지 않고 손 수·신뢰도만 표시한다 — 가짜 기하를 그리지 않는다.
주의: frame_transform 을 주기 전의 출력은 모델을 학습시킨 기준 프레임이라 이 카메라의 픽셀과 정확히 안 맞을 수 있다.
--calib-t calib_T.json(기준 프레임 → 카메라 프레임 변환)을 주면 출력이 카메라 프레임이 되어 KB4 투영이 실제 픽셀과 맞는다.
장치는 인덱스가 아니라 **이름**으로 고른다(dshow 열거 순서가 다른 카메라 연결 시 밀림, 2026-09-10 오판 사례).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))          # sdk/python
from opticmix_vision import CameraIntrinsics, Device, DeviceConfig, FrameTransform, HandTracker, StereoFrame  # noqa: E402
from opticmix_vision.hands import Hand  # noqa: E402
from opticmix_vision.viewer import compose_view  # noqa: E402

BONE_COLOR = (80, 255, 80)
JOINT_COLOR = (0, 200, 255)


def resolve_device_index(name: str) -> int:
    """ffmpeg dshow 열거 순서 = OpenCV CAP_DSHOW 인덱스. 이름 부분일치 정확히 1개여야 한다."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
                       capture_output=True, encoding="utf-8", errors="replace")
    names = re.findall(r'"([^"]+)" \([^)]*video[^)]*\)', r.stderr)
    hits = [i for i, n in enumerate(names) if name.lower() in n.lower()]
    if len(hits) != 1:
        raise SystemExit(f"--device-name {name!r}: {len(hits)} matches in {names}")
    return hits[0]


def project_points(pts_mm: np.ndarray, intr: CameraIntrinsics) -> np.ndarray:
    """(N,3) 카메라 프레임 mm → (N,2) 픽셀 (KB4, cv2.fisheye)."""
    obj = np.asarray(pts_mm, np.float64).reshape(-1, 1, 3)
    px, _ = cv2.fisheye.projectPoints(obj, np.zeros(3), np.zeros(3), intr.K, intr.D)
    return px.reshape(-1, 2)


def draw_hand(img: np.ndarray, hand: Hand, intr: CameraIntrinsics) -> None:
    segs: list[tuple[np.ndarray, np.ndarray]] = []
    for f in hand.fingers:
        for b in f.bones:
            segs.append((np.array(b.start), np.array(b.end)))
    segs.append((np.array(hand.arm.elbow), np.array(hand.arm.wrist)))
    pts = np.array([p for s in segs for p in s])
    px = project_points(pts, intr).astype(int)
    for i in range(0, len(px), 2):
        cv2.line(img, tuple(px[i]), tuple(px[i + 1]), BONE_COLOR, 2, cv2.LINE_AA)
        cv2.circle(img, tuple(px[i + 1]), 3, JOINT_COLOR, -1, cv2.LINE_AA)
    palm = project_points(np.array([hand.palm.position]), intr).astype(int)[0]
    cv2.circle(img, tuple(palm), 5, (0, 0, 255), -1, cv2.LINE_AA)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--device-name", default="WN Dual Camera")
    p.add_argument("--backend", default="dshow")
    p.add_argument("--layout", default="sbs"); p.add_argument("--lr-order", default="first_is_left")
    p.add_argument("--width", type=int, default=1600); p.add_argument("--height", type=int, default=800)
    p.add_argument("--fps", type=float, default=90.0); p.add_argument("--fourcc", default="MJPG")
    p.add_argument("--model", default=None, help="hand_model.onnx (없으면 --stub 필요)")
    p.add_argument("--stub", action="store_true", help="StubRuntime 으로 파이프라인만 확인")
    p.add_argument("--intrinsics-left", default=None, help="왼눈 CameraIntrinsics JSON (model/K/D/size)")
    p.add_argument("--threshold", type=float, default=0.5)
    p.add_argument("--calib-t", default=None, help="gt_capture calib_T.json — 투영용(카메라 프레임 T 적용)")
    return p


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    T = FrameTransform.from_calib_T_json(a.calib_t, to="camera") if a.calib_t else None   # 투영은 카메라 프레임
    if a.stub:
        from opticmix_vision._hands_runtime import StubRuntime
        tracker = HandTracker(runtime=StubRuntime(slots=2), confidence_threshold=a.threshold, frame_transform=T)
    elif a.model:
        tracker = HandTracker(a.model, confidence_threshold=a.threshold, frame_transform=T)
    else:
        raise SystemExit("--model 또는 --stub 중 하나가 필요하다")
    intr = None
    if a.intrinsics_left:
        intr = CameraIntrinsics.from_dict(json.loads(Path(a.intrinsics_left).read_text(encoding="utf-8")))

    cfg = DeviceConfig(layout=a.layout, lr_order=a.lr_order, device=resolve_device_index(a.device_name),
                       backend=a.backend, width=a.width, height=a.height, fps=a.fps, fourcc=a.fourcc)
    print("model:", tracker.model_info)
    with Device(cfg) as cam:
        print("mode:", cam.mode)
        while True:
            frame: StereoFrame = cam.read()
            tf = tracker.track(frame)
            left_bgr = cv2.cvtColor(frame.left, cv2.COLOR_GRAY2BGR)
            if intr is not None:
                for h in tf.hands:
                    draw_hand(left_bgr, h, intr)
            texts = [f"hands {len(tf.hands)}  frame {tf.frame_id}  [{tracker.model_info['frame']}]"] + \
                    [f"{h.chirality.name} conf {h.confidence:.2f} pinch {h.pinch_strength:.2f}" for h in tf.hands]
            if intr is None:
                texts.append("no --intrinsics-left: skeleton not drawn")
            canvas = compose_view(frame, texts=texts, left=left_bgr)
            cv2.imshow("Opticmix Vision — hands", canvas)
            if (cv2.waitKey(1) & 0xFF) == ord("q"):
                break
    cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    sys.exit(main())
