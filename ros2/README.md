# Weevia S1 ROS 2 driver

**Experimental — not a supported feature.**

A ROS 2 package, `weevia_ros`, that publishes the Weevia S1 stereo infrared camera as left and right mono
image streams with per-eye `camera_info`. When you give it calibration files, the launch also runs
rectification, disparity and a point cloud with the standard `image_proc` / `stereo_image_proc` nodes.

The driver opens the camera through the `opticmix_vision` Python SDK. It does not write camera controls (exposure, gain, illuminator).

## What it publishes

Default namespace `weevia` (launch argument `namespace`).

| Topic | Type | Notes |
|---|---|---|
| `/weevia/sbs/image_raw` | `sensor_msgs/Image` | camera frame, both eyes side by side, `mono8` 1600×800 |
| `/weevia/left/image_raw`, `/weevia/right/image_raw` | `sensor_msgs/Image` | one eye each, `mono8` 800×800 |
| `/weevia/left/camera_info`, `/weevia/right/camera_info` | `sensor_msgs/CameraInfo` | from your calibration files; without them uncalibrated (image size only, K, R, P all zero, D empty) |
| `/diagnostics` | `diagnostic_msgs/DiagnosticArray` | `eye stall` status from `sbs_splitter`: per-eye state, mean brightness, frame rate |
| `/tf_static` | `tf2_msgs/TFMessage` | `weevia_link` → `weevia_left_optical_frame`; `weevia_link` → `weevia_right_optical_frame` only when `baseline_m` is given |

With both calibration files, also:

| Topic | Type | Notes |
|---|---|---|
| `/weevia/left/image_rect`, `/weevia/right/image_rect` | `sensor_msgs/Image` | rectified (`image_proc`) |
| `/weevia/disparity` | `stereo_msgs/DisparityImage` | `stereo_image_proc` |
| `/weevia/points2` | `sensor_msgs/PointCloud2` | in `weevia_left_optical_frame` |

The camera sends the right eye in the first half of the side-by-side frame (columns 0–799; measured on one
unit, 2026-09-16); `sbs_splitter` takes care of that. Optical frames follow REP 103 (x right, y down, z forward). `weevia_link` is x forward,
y left, z up, with its origin at the left eye.

## Requirements

- Linux on x86_64.
- ROS 2 with `image_proc`, `stereo_image_proc`, `diagnostic_updater`, `tf2_ros`, `python3-opencv`, `python3-yaml`
  and colcon, for example:

  ```bash
  sudo apt install ros-$ROS_DISTRO-image-proc ros-$ROS_DISTRO-stereo-image-proc \
    ros-$ROS_DISTRO-diagnostic-updater ros-$ROS_DISTRO-tf2-ros \
    python3-opencv python3-yaml python3-pip python3-colcon-common-extensions
  ```

- The `opticmix_vision` 0.0.3 Linux wheel, installed into the Python that ROS uses (the system `python3`,
  not a virtual environment):

  Download `opticmix_vision-0.0.3-cp310-abi3-linux_x86_64.whl` from the
  [v0.0.3 release](https://github.com/Opticmix/opticmix-vision/releases/tag/v0.0.3) and install it
  without its pip dependencies, so that ROS keeps using the `numpy` and OpenCV from `python3-opencv`:

  ```bash
  sudo pip3 install --break-system-packages --no-deps opticmix_vision-0.0.3-cp310-abi3-linux_x86_64.whl
  ```

  `--break-system-packages` is needed on Ubuntu 24.04 and newer, where pip refuses to write into the system
  Python otherwise. This is how our Jazzy run installed it (in a container, as root).
  On Ubuntu 22.04 (ROS 2 Humble) pip does not know that option: leave it out. Humble's `numpy` (1.21) is
  older than the wheel's `numpy>=1.24` requirement; Humble has not been run with the camera.

  The wheel is **experimental**: it has been run on one Ubuntu 26.04 machine with one camera, and camera
  controls on Linux have not been verified and are not supported.

Replay (see below) needs neither the camera nor `opticmix_vision`.

## Build

```bash
git clone https://github.com/Opticmix/opticmix-vision
mkdir -p ~/weevia_ws/src
cp -r opticmix-vision/ros2 ~/weevia_ws/src/weevia
cd ~/weevia_ws
source /opt/ros/$ROS_DISTRO/setup.bash
colcon build
source install/setup.bash
```

The package tests need no camera:

```bash
colcon test
colcon test-result --verbose
```

The last line should read `Summary: 27 tests, 0 errors, 0 failures, 0 skipped`.

## Run without calibration

```bash
ros2 launch weevia_ros camera.launch.py
```

This publishes the images, uncalibrated `camera_info`, `/diagnostics` and the left-eye transform. No stereo
processing is started.

Check that frames arrive with `ros2 topic hz /weevia/left/image_raw` in a second terminal (after
`source install/setup.bash`).

If the driver cannot open the camera it logs `weevia_driver: cannot stream (...)` and stops. Check that
`opticmix_vision` imports in the system `python3` and that the camera shows up as `/dev/videoN`.

| Argument | Default | Meaning |
|---|---|---|
| `device` | `0` | V4L2 camera index N (`/dev/videoN`) |
| `backend` | `v4l2` | capture backend passed to `opticmix_vision` |
| `namespace` | `weevia` | namespace for the nodes and topics |
| `use_shm_profile` | `true` | use the bundled Fast DDS shared-memory profile (see Known issues) |
| `left_camera_info`, `right_camera_info` | empty | calibration files — both or neither |
| `baseline_m` | empty | distance between the eyes in metres; empty = no right-eye transform |

Without a camera, `ros2 launch weevia_ros replay.launch.py folder:=<directory>` plays a folder of
side-by-side mono PNG frames (1600×800) in place of the camera. It takes the same calibration arguments,
plays at `fps:=90.0` by default and reads at most the first 300 frames, looping. Frames of another shape
stop it with `not SBS mono (H, 2H)`.

## Run with calibration

```bash
ros2 launch weevia_ros camera.launch.py \
  left_camera_info:=/path/to/left.yaml right_camera_info:=/path/to/right.yaml \
  baseline_m:=<baseline in metres>
```

- The two files are `camera_calibration_parsers` YAML (`image_width`, `image_height`, `camera_name`,
  `camera_matrix`, `distortion_model`, `distortion_coefficients`, `rectification_matrix`,
  `projection_matrix`), one per eye, from a stereo calibration of your camera.
- `baseline_m` is the baseline of that calibration: `-P[3] / P[0]` of the right file's `projection_matrix`
  (ROS stores `Tx = -fx' * B` there).
- Give both files or neither. With only one, `sbs_splitter` stops at start with a `ValueError`.

## Making calibration files

The standard ROS `camera_calibration` package (`cameracalibrator`) calibrates a stereo pair from a
checkerboard. Run it in stereo mode with its left and right image inputs remapped to
`/weevia/left/image_raw` and `/weevia/right/image_raw`; see that package's documentation for the arguments
and lens-model options. These nodes do not provide the `set_camera_info` service, so save the result
instead of committing it; the saved archive contains a YAML file per eye.

**Not tested with Weevia.**

## Stopping

Press Ctrl-C in the terminal running `ros2 launch`.

From a script, start the launch in its own process group and send SIGINT to the whole group. Signalling only
the `ros2 launch` process can leave the nodes running.

```bash
setsid ros2 launch weevia_ros camera.launch.py &
LAUNCH_PID=$!
# ...
kill -INT -- -$LAUNCH_PID
```

## Known issues

- **Docker: run with `--ipc=host`.** Our Jazzy run used it; running without it has not been tried.

- **Camera controls on Linux are not verified and not supported.** Exposure, gain and illuminator controls have
  not been run on Linux. The driver does not call them.
- **One eye can freeze when the stream is reopened.** After the camera stream is closed and opened again, one
  eye sometimes keeps sending the same image. The cause is not resolved. `sbs_splitter` reports it on
  `/diagnostics` (`eye stall`, level ERROR, for example `left eye sent the same image for 90 frames`;
  checked on generated frames, it has not fired with the camera).
  Seen on Windows. On Linux, 20 reopens (3 s each) left neither eye frozen, but the freeze has not been
  reproduced on Linux, so this does not show that Linux is free of it.
- **Tools started outside the launch need the same DDS profile.** With `use_shm_profile:=true` (default) the
  launch points `FASTRTPS_DEFAULT_PROFILES_FILE` at the bundled Fast DDS profile, which enlarges the
  shared-memory segment for 1600×800 images. `rviz2`, `ros2 bag` and other tools you start yourself need the
  same variable (untested):

  ```bash
  export FASTRTPS_DEFAULT_PROFILES_FILE=$(ros2 pkg prefix weevia_ros)/share/weevia_ros/config/fastdds_shm.xml
  ```

- **Stereo container preload.** `stereo.launch.py` preloads (`LD_PRELOAD`) the TBB library that OpenCV uses
  into the component container, found with `ldd`. Without it the container often crashes (exit code -11)
  when it is stopped.

## Tested configurations

Run on 2026-11-22 with one Weevia S1 (firmware version not recorded), connected directly over USB SuperSpeed to
an Ubuntu 26.04 (kernel 7.0) mini PC. PipeWire and WirePlumber were stopped during the runs. The driver was
built from an earlier commit of this package, before calibration files became optional; that change was
checked without the camera (tests on generated frames) and has not been rerun with the camera.

| Configuration | What was run | Result |
|---|---|---|
| SDK wheel (a candidate build of 0.0.3 from the same source), Ubuntu 26.04 | 20 s capture, 1600×800 MJPG 90 fps negotiated | both eyes changed on every frame; about 85.6 unique frames/s (our measurement script), 88.95/s by kernel buffer count; no kernel USB errors |
| ROS 2 Lyrical, Ubuntu 26.04 | `camera.launch.py` with calibration files, 3 runs | image topic received at a mean of 86.9–88.3 messages/s (30 s each) |
| ROS 2 Jazzy user space (Docker), Ubuntu 26.04 host kernel | the same, 2 runs | driver ran; image topic received at about 82 messages/s |
| Stopping | SIGINT to the launch's process group, 5 times (Lyrical 3, Jazzy 2) | no process left running |
| Reopening | stream opened and closed 20 times, 3 s each | neither eye stopped — we have not reproduced the stop on Linux, so this does not show it cannot happen |

Not run with the camera: running without calibration files, ROS 2 Humble, Ubuntu 22.04 or 24.04 as the host, camera controls on Linux, depth and
point-cloud accuracy, more than one camera, long runs. SIGTERM was checked only on replayed generated frames.
