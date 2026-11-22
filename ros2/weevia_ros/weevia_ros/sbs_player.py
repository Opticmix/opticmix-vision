"""PNG 폴더(SBS mono 1600×800)를 고정 fps 로 sbs/image_raw 로 재생. 처음 max_frames 장을 메모리에 올려 되풀이."""
from __future__ import annotations

from pathlib import Path

import cv2
from rclpy.node import Node
from rclpy.qos import qos_profile_system_default
from sensor_msgs.msg import Image

from weevia_ros.rosconv import to_image
from weevia_ros.spin import spin_quietly


class SbsPlayer(Node):
    def __init__(self) -> None:
        super().__init__("sbs_player")
        p = self.declare_parameter
        folder = Path(p("folder", "").value)
        n = p("max_frames", 300).value
        files = sorted(folder.glob("*.png"))[:n]
        if not files:
            raise SystemExit(f"no PNG frames in {folder}")
        self.frames = [cv2.imread(str(f), cv2.IMREAD_GRAYSCALE) for f in files]
        bad = [f.name for f, im in zip(files, self.frames) if im is None or im.shape[1] != 2 * im.shape[0]]
        if bad:
            raise SystemExit(f"not SBS mono (H, 2H): {bad[:3]}")
        self.loop = p("loop", True).value
        self.frame_id = p("frame_id", "weevia_link").value
        self.i = 0
        self.pub = self.create_publisher(Image, "sbs/image_raw", qos_profile_system_default)
        self.create_timer(1.0 / float(p("fps", 90.0).value), self.tick)
        self.get_logger().info(f"loaded {len(self.frames)} frames from {folder}")

    def tick(self) -> None:
        if self.i >= len(self.frames):
            if not self.loop:
                return
            self.i = 0
        self.pub.publish(to_image(self.frames[self.i], self.get_clock().now().to_msg(), self.frame_id))
        self.i += 1


def main() -> None:
    spin_quietly(SbsPlayer)                                      # Ctrl-C·pkill 때 트레이스백 없이 끝낸다
