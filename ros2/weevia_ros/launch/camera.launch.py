"""카메라(weevia_driver, Linux 실험 단계) → 좌우 분리 → (보정 파일이 있으면) 보정·시차·점구름(stereo.launch.py).

left_camera_info·right_camera_info 를 둘 다 주면 stereo.launch.py 를 띄우고, 둘 다 없으면 미보정 camera_info 만 낸다.
baseline_m 이 있으면 오른쪽 눈 tf 를 낸다.
"""
import math
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (DeclareLaunchArgument, GroupAction, IncludeLaunchDescription, OpaqueFunction,
                            SetEnvironmentVariable)
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration as L
from launch_ros.actions import Node, PushRosNamespace

RPY = ["--roll", str(-math.pi / 2), "--pitch", "0", "--yaw", str(-math.pi / 2)]   # weevia_link → 광학 축(REP 103)


def _optional(context, stereo):
    """baseline_m 이 있으면 tf_right, 두 camera_info 가 다 있으면 stereo.launch.py. 하나만이면 splitter 가 ValueError."""
    c = context.launch_configurations
    acts = []
    if c["baseline_m"].strip():
        acts.append(Node(package="tf2_ros", executable="static_transform_publisher", name="tf_right",
                         arguments=["--y", str(-float(c["baseline_m"])), "--frame-id", "weevia_link",
                                    "--child-frame-id", "weevia_right_optical_frame", *RPY]))
    if c["left_camera_info"].strip() and c["right_camera_info"].strip():
        acts.append(GroupAction([PushRosNamespace(c["namespace"]),
                                 IncludeLaunchDescription(PythonLaunchDescriptionSource(stereo))]))
    return acts


def generate_launch_description():
    share = get_package_share_directory("weevia_ros")
    cfg = os.path.join(share, "config", "weevia.yaml")
    shm = os.path.join(share, "config", "fastdds_shm.xml")   # Fast DDS 공유 메모리 64 MB — 기본 설정은 1600×800 을 잃음
    stereo = os.path.join(share, "launch", "stereo.launch.py")   # 흑백 전용 — 업스트림 launch 는 컬러 경로를 늘 돌림
    args = [DeclareLaunchArgument(n, default_value=d) for n, d in (
        ("namespace", "weevia"), ("device", "0"), ("backend", "v4l2"), ("use_shm_profile", "true"))]
    args += [DeclareLaunchArgument(e, default_value="", description=f"{e} YAML (camera_calibration_parsers format); "
                                   "give both or neither — without them camera_info is uncalibrated")
             for e in ("left_camera_info", "right_camera_info")]
    args.append(DeclareLaunchArgument("baseline_m", default_value="",
                                      description="stereo baseline in m (per unit); empty = no right-eye tf"))
    env = SetEnvironmentVariable("FASTRTPS_DEFAULT_PROFILES_FILE", shm, condition=IfCondition(L("use_shm_profile")))
    tf_left = Node(package="tf2_ros", executable="static_transform_publisher", name="tf_left",
                   arguments=["--frame-id", "weevia_link", "--child-frame-id", "weevia_left_optical_frame", *RPY])
    group = GroupAction([
        PushRosNamespace(L("namespace")),
        Node(package="weevia_ros", executable="weevia_driver",
             parameters=[cfg, {"device": L("device"), "backend": L("backend")}]),
        Node(package="weevia_ros", executable="sbs_splitter",
             parameters=[cfg, {"left_camera_info": L("left_camera_info"), "right_camera_info": L("right_camera_info")}]),
    ])
    return LaunchDescription([*args, env, tf_left, group, OpaqueFunction(function=_optional, args=[stereo])])
