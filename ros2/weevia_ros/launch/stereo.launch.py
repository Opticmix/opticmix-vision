"""left|right/image_raw(mono8) → 보정 → 시차 → 점구름. stereo_image_proc 기본 launch 대신 쓴다.

그쪽 점구름 노드는 use_color 와 무관하게 left/image_rect_color 를 구독해, points2 를 구독하면 debayer·컬러 보정이 흑백 입력에도 돌고
debayer 가 프레임마다 WARN 을 낸다. 여기서는 흑백 보정만 띄우고 점구름 입력을 image_rect 로 돌린다.
네임스페이스는 바깥 PushRosNamespace 를 따른다.

컨테이너에 OpenCV 가 링크한 TBB 를 LD_PRELOAD 한다. 없으면 끌 때 컨테이너가 exit -11 로 죽는 일이 잦다: 컨테이너가 끝날 때
컴포넌트 라이브러리를 dlclose 하면 그 라이브러리를 통해서만 들어온 OpenCV·TBB 가 함께 언매핑되는데, OpenCV 가 만든 TBB 작업자
스레드가 아직 살아 있다. 미리 적재한 라이브러리는 내려가지 않는다. TBB 이름은 배포판마다 달라(libtbb.so.12 / libtbb.so.2) ldd 로 찾는다."""
import os
import re
import subprocess

from ament_index_python.packages import get_package_prefix
from launch import LaunchDescription
from launch.substitutions import EnvironmentVariable
from launch_ros.actions import ComposableNodeContainer
from launch_ros.descriptions import ComposableNode


def _opencv_tbb() -> str:
    """컨테이너가 적재할 컴포넌트 라이브러리를 따라 들어오는 TBB soname(ldd). 없으면 ""."""
    libs = [os.path.join(get_package_prefix(p), "lib", f)
            for p, f in (("image_proc", "librectify.so"), ("stereo_image_proc", "libstereo_image_proc.so"))]
    out = subprocess.run(["ldd", *libs], capture_output=True, text=True).stdout
    return " ".join(sorted(set(re.findall(r"^\s*(libtbb\.so\.[\d.]+) =>", out, re.M))))


def generate_launch_description():
    nodes = [ComposableNode(package="image_proc", plugin="image_proc::RectifyNode", name="rectify_mono_node",
                            namespace=e, remappings=[("image", "image_raw")]) for e in ("left", "right")]
    nodes += [
        ComposableNode(package="stereo_image_proc", plugin="stereo_image_proc::DisparityNode"),
        ComposableNode(package="stereo_image_proc", plugin="stereo_image_proc::PointCloudNode",
                       remappings=[("left/image_rect_color", "left/image_rect"),
                                   ("right/image_rect_color", "right/image_rect")]),
    ]
    return LaunchDescription([ComposableNodeContainer(
        name="stereo_image_proc_container", namespace="", package="rclcpp_components",
        executable="component_container", composable_node_descriptions=nodes,
        additional_env={"LD_PRELOAD": [_opencv_tbb() + " ", EnvironmentVariable("LD_PRELOAD", default_value="")]})])
