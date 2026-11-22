"""노드 층 시험(rclpy 메시지·노드 생성, spin 하지 않음). colcon test 환경(ROS)에서 돈다.

발행기는 가짜로 바꿔 끼워 실제로 publish 되는 메시지를 본다 — 시각 도장·frame_id·좌우가 노드 코드에서 뒤바뀌어도 잡히게.
"""
import numpy as np
import pytest
import rclpy
from builtin_interfaces.msg import Time
from diagnostic_msgs.msg import DiagnosticStatus
from diagnostic_updater import DiagnosticStatusWrapper

from test_caminfo import YAML
from weevia_ros.rosconv import to_image
from weevia_ros.sbs_splitter import SbsSplitter


class FakePub:
    def __init__(self):
        self.msgs = []

    def publish(self, m):
        self.msgs.append(m)


@pytest.fixture
def ros():
    made = []

    def start(params: dict, cls):
        args = ["--ros-args"]
        for k, v in params.items():
            args += ["-p", f"{k}:={v}"]
        rclpy.init(args=args)
        node = cls()
        made.append(node)
        return node

    yield start
    for n in made:
        n.destroy_node()
    rclpy.try_shutdown()


@pytest.fixture
def splitter(ros, tmp_path):
    for eye in ("left", "right"):
        (tmp_path / f"{eye}.yaml").write_text(YAML)
    node = ros({"left_camera_info": tmp_path / "left.yaml", "right_camera_info": tmp_path / "right.yaml"}, SbsSplitter)
    node.pub = {e: (FakePub(), FakePub()) for e in ("left", "right")}
    return node


def sbs_msg(front: int, back: int, sec: int = 123, nanosec: int = 456):
    img = np.empty((800, 1600), np.uint8)
    img[:, :800] = front
    img[:, 800:] = back
    return to_image(img, Time(sec=sec, nanosec=nanosec), "weevia_link")


def test_splitter_stamp_frame_ids_and_halves(splitter):
    splitter.on_image(sbs_msg(front=11, back=22))
    for eye, frame, value in (("left", "weevia_left_optical_frame", 22),      # 뒤 절반 = left
                              ("right", "weevia_right_optical_frame", 11)):   # 앞 절반 = right
        img_pub, info_pub = splitter.pub[eye]
        assert len(img_pub.msgs) == 1 and len(info_pub.msgs) == 1
        for m in (img_pub.msgs[0], info_pub.msgs[0]):
            assert (m.header.stamp.sec, m.header.stamp.nanosec) == (123, 456)   # 입력 시각 도장 그대로
            assert m.header.frame_id == frame
        im = img_pub.msgs[0]
        assert (im.width, im.height, im.encoding) == (800, 800, "mono8")
        assert set(bytes(im.data)) == {value}


def test_splitter_diag_latches_zero_frame(splitter):
    splitter.on_image(sbs_msg(front=5, back=0))          # left(뒤 절반) 전부 0 한 장
    splitter.on_image(sbs_msg(front=5, back=6))          # 곧 회복
    stat = splitter.diag(DiagnosticStatusWrapper())
    assert stat.level == DiagnosticStatus.ERROR and "left eye all zero" in stat.message
    values = {kv.key: kv.value for kv in stat.values}
    assert values["left mean brightness"] == "3.0" and values["right mean brightness"] == "5.0"
    splitter.on_image(sbs_msg(front=7, back=8))
    stat = splitter.diag(DiagnosticStatusWrapper())
    assert stat.level == DiagnosticStatus.OK


def test_splitter_uncalibrated_without_camera_info(ros):
    # 보정 파일 둘 다 없음 → 눈 영상 크기만 채운 미보정 camera_info(K·R·P 0, D 비움)
    node = ros({}, SbsSplitter)
    node.pub = {e: (FakePub(), FakePub()) for e in ("left", "right")}
    node.on_image(sbs_msg(front=11, back=22))
    for eye in ("left", "right"):
        info = node.pub[eye][1].msgs[0]
        assert (info.width, info.height) == (800, 800)
        assert list(info.k) == [0.0] * 9 and list(info.r) == [0.0] * 9 and list(info.p) == [0.0] * 12
        assert list(info.d) == [] and info.distortion_model == ""


@pytest.mark.parametrize("given", ["left_camera_info", "right_camera_info"])
def test_splitter_rejects_one_camera_info(ros, tmp_path, given):
    (tmp_path / "eye.yaml").write_text(YAML)
    with pytest.raises(ValueError, match="both left_camera_info and right_camera_info"):
        ros({given: tmp_path / "eye.yaml"}, SbsSplitter)
