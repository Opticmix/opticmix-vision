"""sbs/image_raw → left|right/image_raw + camera_info, /diagnostics 로 눈별 정지 감시·평균 밝기·fps·무입력(알리기만).

camera_info 는 left/right_camera_info YAML 둘 다 주면 그 값, 둘 다 없으면 미보정(크기만), 하나만 주면 시작할 때 ValueError.
"""
from __future__ import annotations

import time

from diagnostic_msgs.msg import DiagnosticStatus
from diagnostic_updater import Updater
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data, qos_profile_system_default
from rclpy.qos_overriding_options import QoSOverridingOptions
from sensor_msgs.msg import CameraInfo, Image

from weevia_ros.caminfo import load_stereo_camera_info, uncalibrated
from weevia_ros.rosconv import from_image, to_camera_info, to_image
from weevia_ros.sbs import EyeDiag, StallMonitor, eye_outputs
from weevia_ros.spin import spin_quietly


class SbsSplitter(Node):
    def __init__(self) -> None:
        super().__init__("sbs_splitter")
        p = self.declare_parameter
        self.info = load_stereo_camera_info(p("left_camera_info", "").value, p("right_camera_info", "").value)
        if self.info is None:
            self.get_logger().info("no left/right_camera_info: publishing uncalibrated camera_info (size only)")
        self.left_frame = p("left_frame_id", "weevia_left_optical_frame").value
        self.right_frame = p("right_frame_id", "weevia_right_optical_frame").value
        self.monitor = StallMonitor(p("zero_frames", 1).value, p("same_frames", 90).value)
        ov = QoSOverridingOptions.with_default_policies()          # 발행 = SystemDefaults(신뢰), 덮어쓰기 허용
        self.pub = {e: (self.create_publisher(Image, f"{e}/image_raw", qos_profile_system_default, qos_overriding_options=ov),
                        self.create_publisher(CameraInfo, f"{e}/camera_info", qos_profile_system_default,
                                              qos_overriding_options=ov)) for e in ("left", "right")}
        self.create_subscription(Image, "sbs/image_raw", self.on_image, qos_profile_sensor_data)
        self.eye_diag = EyeDiag(time.monotonic(), p("no_input_s", 2.0).value)
        self.updater = Updater(self)
        self.updater.setHardwareID("weevia_s1")
        self.updater.add("eye stall", self.diag)

    def on_image(self, msg: Image) -> None:
        outs = eye_outputs(from_image(msg), msg.header.stamp, self.left_frame, self.right_frame)
        for eye, img, stamp, frame in outs:
            info = self.info[eye] if self.info else uncalibrated(img.shape[1], img.shape[0])
            self.pub[eye][0].publish(to_image(img, stamp, frame))
            self.pub[eye][1].publish(to_camera_info(info, stamp, frame))
        left, right = outs[0][1], outs[1][1]
        self.eye_diag.on_frame(time.monotonic(), left, right, self.monitor.update(left, right))

    def diag(self, stat):
        level, message, values = self.eye_diag.report(time.monotonic())
        stat.summary((DiagnosticStatus.OK, DiagnosticStatus.WARN, DiagnosticStatus.ERROR)[level], message)
        for k, v in values:
            stat.add(k, v)
        return stat


def main() -> None:
    spin_quietly(SbsSplitter)                                      # Ctrl-C·pkill 때 트레이스백 없이 끝낸다
