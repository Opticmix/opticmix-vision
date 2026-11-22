import importlib.util
import re
import types
from pathlib import Path

import pytest

from weevia_ros.caminfo import load_camera_info

YAML = """image_width: 800
image_height: 800
camera_name: weevia_right
camera_matrix:
  rows: 3
  cols: 3
  data: [300.0, 0.0, 400.0, 0.0, 300.0, 400.0, 0.0, 0.0, 1.0]
distortion_model: equidistant
distortion_coefficients:
  rows: 1
  cols: 4
  data: [0.1, -0.01, 0.001, 0.0]
rectification_matrix:
  rows: 3
  cols: 3
  data: [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
projection_matrix:
  rows: 3
  cols: 4
  data: [300.0, 0.0, 400.0, -19.2, 0.0, 300.0, 400.0, 0.0, 0.0, 0.0, 1.0, 0.0]
"""


def test_load_camera_info(tmp_path):
    p = tmp_path / "r.yaml"
    p.write_text(YAML)
    ci = load_camera_info(p)
    assert (ci["width"], ci["height"]) == (800, 800)
    assert ci["distortion_model"] == "equidistant"
    assert len(ci["k"]) == 9 and len(ci["r"]) == 9 and len(ci["p"]) == 12 and len(ci["d"]) == 4
    assert ci["k"] == [300.0, 0.0, 400.0, 0.0, 300.0, 400.0, 0.0, 0.0, 1.0]       # camera_matrix → k
    assert ci["r"] == [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]                # rectification_matrix → r
    assert ci["p"] == [300.0, 0.0, 400.0, -19.2, 0.0, 300.0, 400.0, 0.0, 0.0, 0.0, 1.0, 0.0]   # projection_matrix → p
    assert ci["d"] == [0.1, -0.01, 0.001, 0.0]                                     # distortion_coefficients → d
    assert all(type(v) is float for key in ("k", "r", "p", "d") for v in ci[key])


def test_empty_path_is_a_clear_error():
    # 빈 경로는 Path("") = "." → IsADirectoryError 가 아니라 분명한 ValueError (빈 값 = 미보정은 splitter 가 정한다)
    with pytest.raises(ValueError, match="camera_info"):
        load_camera_info("")


def _load_launch(name):
    path = Path(__file__).parents[1] / "launch" / name
    spec = importlib.util.spec_from_file_location(name.replace(".", "_"), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("name", ["camera.launch.py", "replay.launch.py"])
def test_launch_files_camera_info_optional(name, monkeypatch):
    # camera_info·baseline_m 은 선택(기본값 ""). 두 camera_info 가 다 있을 때만 stereo, baseline_m 이 있을 때만 tf_right.
    from launch import LaunchContext
    from launch.actions import DeclareLaunchArgument, GroupAction, IncludeLaunchDescription
    from launch.utilities import perform_substitutions
    from launch_ros.actions import Node
    mod = _load_launch(name)
    monkeypatch.setattr(mod, "get_package_share_directory", lambda pkg: "/share")
    defaults = {a.name: perform_substitutions(LaunchContext(), a.default_value)
                for a in mod.generate_launch_description().entities if isinstance(a, DeclareLaunchArgument)}
    assert [defaults[k] for k in ("left_camera_info", "right_camera_info", "baseline_m")] == ["", "", ""]

    def optional(**given):
        cfg = {"namespace": "weevia", "left_camera_info": "", "right_camera_info": "", "baseline_m": "", **given}
        return mod._optional(types.SimpleNamespace(launch_configurations=cfg), "stereo.launch.py")

    assert optional() == []
    assert optional(left_camera_info="l.yaml") == [] and optional(right_camera_info="r.yaml") == []   # 하나만 = splitter 가 ValueError
    assert [type(a) for a in optional(baseline_m="0.066")] == [Node]                                  # tf_right 만
    both = optional(left_camera_info="l.yaml", right_camera_info="r.yaml")
    assert [type(a) for a in both] == [GroupAction]
    assert any(isinstance(e, IncludeLaunchDescription) for e in both[0].get_sub_entities())


def test_point_cloud_uses_mono_rect():
    # upstream stereo_image_proc.launch.py wires the cloud to image_rect_color → debayer runs on mono input
    launch = Path(__file__).parents[1] / "launch"
    for name in ("camera.launch.py", "replay.launch.py"):
        src = (launch / name).read_text(encoding="utf-8")
        assert "stereo_image_proc.launch.py" not in src and '"stereo.launch.py"' in src, name
    stereo = (launch / "stereo.launch.py").read_text(encoding="utf-8")
    assert '("left/image_rect_color", "left/image_rect")' in stereo
    assert "DebayerNode" not in stereo
    assert 'remappings=[("image", "image_raw")]' in stereo   # 보정 입력이 image_mono 면 배선이 끊김


def test_stereo_container_preloads_opencv_tbb():
    # 구독 뒤 종료 exit -11: dlclose 로 OpenCV 의 TBB 가 언매핑되지 않게 예압.
    path = Path(__file__).parents[1] / "launch" / "stereo.launch.py"
    assert '"LD_PRELOAD": [_opencv_tbb()' in path.read_text(encoding="utf-8")
    spec = importlib.util.spec_from_file_location("stereo_launch", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    names = mod._opencv_tbb().split()
    assert names and all(re.fullmatch(r"libtbb\.so\.[\d.]+", n) for n in names), names   # Lyrical·Jazzy .12, Humble .2
