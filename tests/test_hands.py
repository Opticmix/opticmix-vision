import dataclasses

import numpy as np
import pytest

from opticmix_vision.hands import Arm, Bone, Chirality, Finger, FingerKind, Hand, Palm, TrackingFrame

Q0 = (0.0, 0.0, 0.0, 1.0)


def _finger(kind: FingerKind, base: float = 0.0) -> Finger:
    pts = [(base + i, 0.0, 0.0) for i in range(5)]
    bones = tuple(Bone(start=pts[i], end=pts[i + 1], rotation=Q0) for i in range(4))
    return Finger(kind=kind, bones=bones, width_mm=18.0, is_extended=True)


def _hand() -> Hand:
    return Hand(
        chirality=Chirality.RIGHT, confidence=0.9,
        palm=Palm(position=(0.0, 0.0, 0.0), normal=(0.0, 1.0, 0.0), direction=(0.0, 0.0, -1.0), orientation=Q0, width_mm=80.0),
        fingers=tuple(_finger(k, base=10.0 * k) for k in FingerKind),
        arm=Arm(elbow=(0.0, -200.0, 0.0), wrist=(0.0, -50.0, 0.0), rotation=Q0, width_mm=60.0),
        pinch_strength=0.1, pinch_distance_mm=40.0, grab_strength=0.0, grab_angle=0.5,
    )


def test_finger_tip_is_last_bone_end():
    f = _finger(FingerKind.INDEX, base=3.0)
    assert f.tip == (7.0, 0.0, 0.0)
    assert f.bones[3].end == f.tip


def test_bone_endpoint_sharing_in_finger():
    f = _finger(FingerKind.THUMB)
    for i in range(3):
        assert f.bones[i].end == f.bones[i + 1].start


def test_hand_requires_five_fingers_in_kind_order():
    h = _hand()
    assert [f.kind for f in h.fingers] == list(FingerKind)
    with pytest.raises(ValueError):
        dataclasses.replace(h, fingers=h.fingers[:4])


def test_types_are_frozen():
    h = _hand()
    with pytest.raises(dataclasses.FrozenInstanceError):
        h.confidence = 0.1  # type: ignore[misc]


def test_tracking_frame_holds_n_hands():
    tf = TrackingFrame(hands=(_hand(), _hand()), host_ns=123, frame_id=7)
    assert len(tf.hands) == 2 and tf.host_ns == 123 and tf.frame_id == 7
    assert TrackingFrame(hands=(), host_ns=0, frame_id=0).hands == ()


from opticmix_vision.hands import FrameTransform, assemble_hand


def _tensors(offset: float = 0.0):
    joints = np.array([(offset + i, 2.0 * i, 3.0 * i) for i in range(28)], np.float32)
    quats = np.tile(np.array([0, 0, 0, 1], np.float32), (22, 1))
    palm_vecs = np.array([[0, 1, 0], [0, 0, -1]], np.float32)
    metrics = np.array([30.0, 1.0, 0.2, 0.1], np.float32)          # pinch_dist, grab_angle, pinch_str, grab_str
    widths = np.array([80, 20, 18, 18, 17, 16, 60], np.float32)     # palm, thumb..pinky, arm
    extended = np.array([1.0, 1.0, -1.0, -1.0, -1.0], np.float32)
    return joints, quats, palm_vecs, metrics, widths, extended


def test_assemble_hand_maps_every_group():
    j, q, pv, m, w, e = _tensors()
    h = assemble_hand(j, q, pv, m, w, e, presence_logit=5.0, chirality_logit=5.0)
    assert h.chirality == Chirality.RIGHT and 0.99 < h.confidence < 1.0
    for f in range(5):
        fg = h.fingers[f]
        assert fg.kind == FingerKind(f)
        assert fg.bones[0].start == tuple(j[f * 5 + 0]) and fg.tip == tuple(j[f * 5 + 4])
        for b in range(3):
            assert fg.bones[b].end == fg.bones[b + 1].start
        assert fg.width_mm == float(w[1 + f])
    assert h.fingers[0].is_extended and not h.fingers[2].is_extended
    assert h.arm.elbow == tuple(j[25]) and h.arm.wrist == tuple(j[26]) and h.arm.width_mm == 60.0
    assert h.palm.position == tuple(j[27]) and h.palm.width_mm == 80.0
    assert h.palm.normal == (0.0, 1.0, 0.0) and h.palm.direction == (0.0, 0.0, -1.0)
    assert h.pinch_distance_mm == 30.0 and h.grab_angle == 1.0
    assert h.pinch_strength == pytest.approx(0.2) and h.grab_strength == pytest.approx(0.1)


def test_assemble_hand_chirality_left_when_logit_negative():
    j, q, pv, m, w, e = _tensors()
    assert assemble_hand(j, q, pv, m, w, e, presence_logit=0.0, chirality_logit=-3.0).chirality == Chirality.LEFT


def test_frame_transform_point_and_quat():
    R = np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]], float)        # +90° about Z
    T = FrameTransform(R=R, t=np.array([10.0, 0.0, 0.0]))
    assert np.allclose(T.apply_point((1.0, 0.0, 0.0)), (10.0, 1.0, 0.0))
    ident = FrameTransform.identity()
    assert np.allclose(ident.apply_point((1.0, 2.0, 3.0)), (1.0, 2.0, 3.0))
    assert np.allclose(ident.apply_quat((0.0, 0.0, 0.0, 1.0)), (0.0, 0.0, 0.0, 1.0))
    qz = T.apply_quat((0.0, 0.0, 0.0, 1.0))
    assert np.allclose(qz, (0.0, 0.0, np.sin(np.pi / 4), np.cos(np.pi / 4)), atol=1e-6)


def test_assemble_hand_applies_transform():
    j, q, pv, m, w, e = _tensors()
    T = FrameTransform(R=np.eye(3), t=np.array([0.0, 0.0, 100.0]))
    h = assemble_hand(j, q, pv, m, w, e, presence_logit=5.0, chirality_logit=5.0, transform=T)
    assert h.palm.position == pytest.approx((27.0, 54.0, 181.0))
    assert h.palm.normal == pytest.approx((0.0, 1.0, 0.0))          # 방향벡터는 평행이동 안 함


import sys

from opticmix_vision._hands_runtime import OnnxRuntime, StubRuntime


def test_stub_runtime_output_contract():
    rt = StubRuntime(slots=2, input_size=256)
    L = np.zeros((1, 1, 256, 256), np.float32); R = np.zeros((1, 1, 256, 256), np.float32)
    out = rt.run(L, R)
    assert out["joints"].shape == (2, 28, 3) and out["quats"].shape == (2, 22, 4)
    assert out["palm_vecs"].shape == (2, 2, 3) and out["metrics"].shape == (2, 4)
    assert out["widths"].shape == (2, 7) and out["extended"].shape == (2, 5)
    assert out["presence"].shape == (2,) and out["chirality"].shape == (2,)
    assert rt.slots == 2 and rt.input_size == 256 and rt.info["runtime"] == "stub"
    assert out["presence"][0] > 0 > out["presence"][1]                 # 기본: 슬롯0만 유효


def test_stub_runtime_rejects_wrong_input_size():
    rt = StubRuntime(slots=1, input_size=256)
    with pytest.raises(ValueError):
        rt.run(np.zeros((1, 1, 128, 128), np.float32), np.zeros((1, 1, 128, 128), np.float32))


def test_onnx_runtime_clear_error_without_onnxruntime(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "onnxruntime", None)             # import 를 강제로 실패시킨다
    with pytest.raises(ImportError, match=r"opticmix-vision\[hands\]"):
        OnnxRuntime(tmp_path / "hand_model.onnx")


from opticmix_vision import StereoFrame
from opticmix_vision.hands import HandTracker


def _frame(v: int = 255) -> StereoFrame:
    return StereoFrame(left=np.full((800, 800), v, np.uint8), right=np.full((800, 800), v, np.uint8),
                       host_ns=999, index=42)


def test_tracker_single_slot_returns_one_hand():
    tr = HandTracker(runtime=StubRuntime(slots=1))
    tf = tr.track(_frame())
    assert len(tf.hands) == 1 and tf.host_ns == 999 and tf.frame_id == 42
    assert tf.hands[0].chirality == Chirality.RIGHT
    assert tr.model_info["frame"] == "sir170_rig"              # T 미적용 → GT 프레임임을 명시


def test_tracker_two_slots_presence_threshold():
    both = HandTracker(runtime=StubRuntime(slots=2, presence=[5.0, 5.0], chirality=[5.0, -5.0]))
    tf = both.track(_frame())
    assert [h.chirality for h in tf.hands] == [Chirality.RIGHT, Chirality.LEFT]
    one = HandTracker(runtime=StubRuntime(slots=2, presence=[5.0, -5.0]))
    assert len(one.track(_frame()).hands) == 1
    none = HandTracker(runtime=StubRuntime(slots=1, presence=[-5.0]))
    assert none.track(_frame()).hands == ()


def test_tracker_threshold_is_configurable():
    rt = StubRuntime(slots=1, presence=[0.0])                     # sigmoid(0) = 0.5
    assert len(HandTracker(runtime=rt, confidence_threshold=0.5).track(_frame()).hands) == 1
    assert len(HandTracker(runtime=rt, confidence_threshold=0.6).track(_frame()).hands) == 0


def test_tracker_applies_frame_transform_and_labels_frame():
    T = FrameTransform(R=np.eye(3), t=np.array([0.0, 0.0, 100.0]))
    tr = HandTracker(runtime=StubRuntime(slots=1), frame_transform=T)
    tf = tr.track(_frame())
    assert tr.model_info["frame"] == "opticmix_device"
    assert tf.hands[0].palm.position[2] == pytest.approx(3.0 * 27 + 100.0)


def test_tracker_requires_model_or_runtime():
    with pytest.raises(ValueError):
        HandTracker()


def test_package_exports_hands_api():
    import opticmix_vision as ov
    for name in ("Hand", "HandTracker", "TrackingFrame", "FrameTransform", "Chirality", "FingerKind"):
        assert hasattr(ov, name), name


# --- OnnxRuntime 메타 처리: 가짜 onnxruntime 모듈로 (실 onnxruntime·실모델 불필요) --------------------

def _fake_onnxruntime(input_shape, presence_shape=(2,), version=3):
    import types
    from opticmix_vision._hands_runtime import OUTPUT_NAMES

    class _IO:
        def __init__(self, name, shape):
            self.name, self.shape = name, shape

    class _Meta:
        pass

    class InferenceSession:
        def __init__(self, path, providers=None):
            self._providers = list(providers or [])

        def get_inputs(self):
            return [_IO("left", list(input_shape)), _IO("right", list(input_shape))]

        def get_outputs(self):
            return [_IO(n, list(presence_shape) if n == "presence" else [None]) for n in OUTPUT_NAMES]

        def get_providers(self):
            return list(self._providers)

        def get_modelmeta(self):
            m = _Meta(); m.version = version; m.producer_name = "test"
            return m

    mod = types.ModuleType("onnxruntime")
    mod.InferenceSession = InferenceSession
    return mod


def test_onnx_runtime_symbolic_input_size_requires_explicit_override(monkeypatch, tmp_path):
    model = tmp_path / "m.onnx"; model.write_bytes(b"x")
    monkeypatch.setitem(sys.modules, "onnxruntime", _fake_onnxruntime([1, 1, "H", "W"]))
    with pytest.raises(ValueError, match="input_size"):
        OnnxRuntime(model)                                        # 추측(256) 금지 → 명확히 실패
    rt = OnnxRuntime(model, input_size=224)
    assert rt.input_size == 224 and rt.info["input_size"] == 224


def test_onnx_runtime_reads_concrete_shape_slots_and_version(monkeypatch, tmp_path):
    model = tmp_path / "m.onnx"; model.write_bytes(b"x")
    monkeypatch.setitem(sys.modules, "onnxruntime", _fake_onnxruntime([1, 1, 256, 256], presence_shape=(2,), version=7))
    rt = OnnxRuntime(model)
    assert rt.input_size == 256 and rt.slots == 2
    assert rt.info["model_version"] == 7 and rt.info["producer"] == "test"


import json


def _calib_t_json(tmp_path, matrix):
    p = tmp_path / "calib_T.json"
    p.write_text(json.dumps({"T_sir_to_oem": matrix, "method": "test"}), encoding="utf-8")
    return p


def test_from_calib_t_json_camera_frame_is_raw_T(tmp_path):
    M = [[1, 0, 0, 5.0], [0, 1, 0, -40.0], [0, 0, 1, 2.1], [0, 0, 0, 1]]
    T = FrameTransform.from_calib_T_json(_calib_t_json(tmp_path, M), to="camera")
    assert np.allclose(T.apply_point((0.0, 0.0, 0.0)), (5.0, -40.0, 2.1))


def test_from_calib_t_json_device_frame_applies_axis_map(tmp_path):
    M = [[1, 0, 0, 0.0], [0, 1, 0, 0.0], [0, 0, 1, 0.0], [0, 0, 0, 1]]      # T = 항등: SIR == OEM 카메라 프레임
    T = FrameTransform.from_calib_T_json(_calib_t_json(tmp_path, M), to="device")
    assert np.allclose(T.apply_point((0.0, 0.0, 300.0)), (0.0, 300.0, 0.0))      # 카메라 앞 300 → 장치 +Y
    assert np.allclose(T.apply_point((0.0, -100.0, 0.0)), (0.0, 0.0, 100.0))     # 이미지 위쪽 → 장치 +Z


def test_from_calib_t_json_accepts_matrix_wrapper_and_rejects_bad(tmp_path):
    p = tmp_path / "t.json"
    p.write_text(json.dumps({"T_sir_to_oem": {"matrix": np.eye(4).tolist()}}), encoding="utf-8")
    assert np.allclose(FrameTransform.from_calib_T_json(p, to="camera").R, np.eye(3))
    with pytest.raises(ValueError):
        FrameTransform.from_calib_T_json(p, to="nowhere")
