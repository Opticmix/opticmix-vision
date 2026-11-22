"""weevia_driver — opticmix_vision 으로 열어 sbs/image_raw 발행(Linux 실험 단계, 카메라 제어 없음). 열 수 없으면 원인과 함께 끝 코드 2."""
from __future__ import annotations

import sys

import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_system_default
from sensor_msgs.msg import Image

from weevia_ros.driver_core import capture
from weevia_ros.rosconv import to_image


def main() -> None:
    rclpy.init()
    node = Node("weevia_driver")
    device = node.declare_parameter("device", 0).value
    backend = node.declare_parameter("backend", "v4l2").value
    frame_id = node.declare_parameter("frame_id", "weevia_link").value
    pub = node.create_publisher(Image, "sbs/image_raw", qos_profile_system_default)
    try:
        import opticmix_vision as sdk
        capture(sdk, device=device, backend=backend,
                publish=lambda sbs, ns: pub.publish(to_image(sbs, node.get_clock().now().to_msg(), frame_id)),
                should_stop=lambda: not rclpy.ok())
    except Exception as exc:                              # SDK 없음·카메라 없음·열기 실패
        if not rclpy.ok():                                # Ctrl-C·SIGTERM 으로 문맥이 닫힌 뒤 publish 가 RCLError = 정상 종료(끝 코드 0)
            return
        node.get_logger().error(f"weevia_driver: cannot stream ({type(exc).__name__}: {exc}). The Linux driver is "
                                "experimental, and camera controls on Linux are not verified and not supported. Is opticmix_vision "
                                "installed in the Python that ROS uses, and is the camera connected?")
        rclpy.shutdown()
        sys.exit(2)
