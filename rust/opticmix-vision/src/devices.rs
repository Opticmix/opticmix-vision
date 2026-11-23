//! Enumerating connected cameras — by name (Python `devices.py`).
//!
//! This camera does not report a USB serial number. Indexes shift just by plugging or unplugging another camera,
//! so choosing by index silently opens a different camera. That is why selection is always by name.

use std::process::Command;

use crate::error::{Result, SdkError};

/// One device, in DirectShow enumeration order.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DeviceInfo {
    pub index: usize,
    pub name: String,
}

/// One mode the device reports itself.
#[derive(Debug, Clone, PartialEq)]
pub struct Mode {
    pub fourcc: String,
    pub width: u32,
    pub height: u32,
    pub max_fps: f64,
}

impl Mode {
    /// Text shown in a combo box (Python `f"{w}×{h} {fourcc} · {max_fps:g} fps"`; max_fps has one decimal, so it equals Display).
    pub fn label(&self) -> String {
        format!(
            "{}×{} {} · {} fps",
            self.width, self.height, self.fourcc, self.max_fps
        )
    }
}

/// Python string repr (single quotes, single quotes inside escaped).
fn repr(s: &str) -> String {
    format!("'{}'", s.replace('\\', "\\\\").replace('\'', "\\'"))
}

fn list_repr<'a>(names: impl Iterator<Item = &'a str>) -> String {
    format!("[{}]", names.map(repr).collect::<Vec<_>>().join(", "))
}

/// Video device names only from `ffmpeg -list_devices` output, in the order they appear.
/// Rule (same as Python `_VIDEO_LINE`): the ` (…)` right after `"name"` must contain video.
fn parse_video_devices(text: &str) -> Vec<String> {
    text.lines()
        .filter_map(|l| {
            let a = l.find('"')? + 1;
            let b = l[a..].find('"')? + a;
            let kind = l[b + 1..].strip_prefix(" (")?;
            let kind = &kind[..kind.find(')')?];
            (b > a && kind.contains("video")).then(|| l[a..b].to_string())
        })
        .collect()
}

fn ffmpeg_stderr(args: &[&str]) -> std::io::Result<String> {
    let out = Command::new("ffmpeg").args(args).output()?;
    Ok(String::from_utf8_lossy(&out.stderr).into_owned())
}

/// Enumerates DirectShow directly when ffmpeg is missing (Windows). A failure is an empty list — it shows as "not found".
fn native_devices() -> Vec<String> {
    crate::dshow::enumerate_video_devices()
        .map(|v| v.into_iter().map(Option::unwrap_or_default).collect())
        .unwrap_or_default()
}

/// Video devices in DirectShow enumeration order. Laptop webcams are included — the user has to pick among them.
/// Without `raw` this runs ffmpeg; without ffmpeg (most customer PCs) it enumerates DirectShow directly.
pub fn list_devices(raw: Option<&str>) -> Vec<DeviceInfo> {
    let names = match raw {
        Some(r) => parse_video_devices(r),
        None => match ffmpeg_stderr(&[
            "-hide_banner",
            "-list_devices",
            "true",
            "-f",
            "dshow",
            "-i",
            "dummy",
        ]) {
            Ok(r) => parse_video_devices(&r),
            Err(_) => native_devices(),
        },
    };
    names
        .into_iter()
        .enumerate()
        .map(|(index, name)| DeviceInfo { index, name })
        .collect()
}

/// Picks one device by partial name match (case-insensitive). Does not guess when it is ambiguous.
pub fn resolve_by_name(needle: &str, raw: Option<&str>) -> Result<DeviceInfo> {
    pick_by_name(needle, &list_devices(raw))
}

/// Picks from a list you already have (the Python wrapper enumerates via ffmpeg or directly on the Python side and passes the list).
pub fn pick_by_name(needle: &str, devices: &[DeviceInfo]) -> Result<DeviceInfo> {
    let low = needle.to_lowercase();
    let hits: Vec<&DeviceInfo> = devices
        .iter()
        .filter(|d| d.name.to_lowercase().contains(&low))
        .collect();
    match hits.len() {
        0 => Err(SdkError::NoMatch(format!(
            "No camera matches {}. Video devices found: {}",
            repr(needle),
            list_repr(devices.iter().map(|d| d.name.as_str()))
        ))),
        1 => Ok(hits[0].clone()),
        n => Err(SdkError::MultipleMatches(format!(
            "{n} cameras match {} — they cannot be told apart (no USB serial number). Unplug all but one: {}",
            repr(needle),
            list_repr(hits.iter().map(|d| d.name.as_str()))
        ))),
    }
}

/// One `vcodec=X|pixel_format=X  min s=WxH fps=F  max s=WxH fps=F` line.
fn parse_mode_line(l: &str) -> Option<Mode> {
    let (codec, rest) = ["vcodec=", "pixel_format="].iter().find_map(|k| {
        let i = l.find(k)? + k.len();
        let r = &l[i..];
        let end = r
            .find(|c: char| !(c.is_alphanumeric() || c == '_'))
            .unwrap_or(r.len());
        (end > 0).then(|| (&r[..end], &r[end..]))
    })?;
    let rest = rest.trim_start().strip_prefix("min s=")?;
    let rest = &rest[rest.find("max s=")? + "max s=".len()..];
    let (size, rest) = rest.split_once(" fps=")?;
    let (w, h) = size.split_once('x')?;
    let fps_txt: String = rest
        .chars()
        .take_while(|c| c.is_ascii_digit() || *c == '.')
        .collect();
    let fps: f64 = fps_txt.parse().ok()?;
    let fourcc = match codec {
        "mjpeg" => "MJPG".to_string(),
        "yuyv422" => "YUY2".to_string(),
        other => other.to_uppercase(),
    };
    Some(Mode {
        fourcc,
        width: w.parse().ok()?,
        height: h.parse().ok()?,
        // Python round(x, 1) 과 같다(정확한 이진값을 반올림)
        max_fps: format!("{fps:.1}").parse().ok()?,
    })
}

/// Modes the device reports itself. An empty list does not mean "no modes" — it means **"could not find out"**.
pub fn list_modes(name: &str, raw: Option<&str>) -> Vec<Mode> {
    let text = match raw {
        Some(r) => r.to_string(),
        None => {
            let input = format!("video={name}");
            match ffmpeg_stderr(&[
                "-hide_banner",
                "-f",
                "dshow",
                "-list_options",
                "true",
                "-i",
                &input,
            ]) {
                Ok(r) => r,
                Err(_) => return Vec::new(),
            }
        }
    };
    let mut out: Vec<Mode> = Vec::new();
    for m in text.lines().filter_map(parse_mode_line) {
        if !out.iter().any(|x| {
            (x.fourcc.as_str(), x.width, x.height) == (m.fourcc.as_str(), m.width, m.height)
        }) {
            out.push(m);
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Sample from Python tests/test_devices.py (format taken from a real run, other device names and IDs replaced with fake ones).
    const FFMPEG_DEVICES: &str = r#"[dshow @ 000001] "WN Dual Camera" (video)
[dshow @ 000001]   Alternative name "@device_pnp_\\?\usb#vid_1d6b&pid_0200"
[dshow @ 000001] "Example Front Webcam" (video)
[dshow @ 000001] "Example Rear Webcam" (video)
[dshow @ 000001] "Microphone (Example Audio)" (audio)
"#;

    const FFMPEG_OPTIONS: &str = r#"[in#0] DirectShow video device options (from video devices)
[in#0]  Pin "캡쳐" (alternative pin name "0")
[in#0]   vcodec=mjpeg  min s=1600x800 fps=30 max s=1600x800 fps=90.0001
[in#0]   vcodec=mjpeg  min s=2560x800 fps=30 max s=2560x800 fps=90.0001
[in#0]   pixel_format=yuyv422  min s=1600x800 fps=10 max s=1600x800 fps=45
"#;

    /// List taken from real ffmpeg 8.x output (2026-09-07). Format kept, device names and instance IDs replaced with fake ones.
    const REAL_LISTING: &str = r#"[in#0 @ 000001ec2b2e9180] "Example Front Webcam" (video)
[in#0 @ 000001ec2b2e9180]   Alternative name "@device_pnp_\\?\display#example_01#5&0000000a&0&uid0000000001#{65e8773d-8f56-11d0-a3b9-00a0c9223196}\{00000000-0000-0000-0000-000000000001}"
[in#0 @ 000001ec2b2e9180] "Example Rear Webcam" (video)
[in#0 @ 000001ec2b2e9180]   Alternative name "@device_pnp_\\?\usb#vid_0000&pid_0000&mi_00#7&0000000b&0&0000#{65e8773d-8f56-11d0-a3b9-00a0c9223196}\global"
[in#0 @ 000001ec2b2e9180] "Microphone Array(Example(R) Audio)" (audio)
[in#0 @ 000001ec2b2e9180]   Alternative name "@device_cm_{33D9A762-90C8-11D0-BD43-00A0C911CE86}\wave_{00000000-0000-0000-0000-000000000002}"
[in#0 @ 000001ec2b2e9180] "머리에 거는 수화기(Example Headset)" (audio)
[in#0 @ 000001ec2b2e9180]   Alternative name "@device_cm_{33D9A762-90C8-11D0-BD43-00A0C911CE86}\wave_{00000000-0000-0000-0000-000000000003}"
dummy: Immediate exit requested
"#;

    fn d(index: usize, name: &str) -> DeviceInfo {
        DeviceInfo {
            index,
            name: name.into(),
        }
    }

    fn m(fourcc: &str, width: u32, height: u32, max_fps: f64) -> Mode {
        Mode {
            fourcc: fourcc.into(),
            width,
            height,
            max_fps,
        }
    }

    #[test]
    fn lists_video_devices_in_enumeration_order() {
        assert_eq!(
            list_devices(Some(FFMPEG_DEVICES)),
            [
                d(0, "WN Dual Camera"),
                d(1, "Example Front Webcam"),
                d(2, "Example Rear Webcam")
            ]
        );
    }

    #[test]
    fn audio_devices_are_not_cameras() {
        assert!(list_devices(Some(FFMPEG_DEVICES))
            .iter()
            .all(|d| !d.name.contains("Microphone")));
    }

    #[test]
    fn resolve_by_name_is_case_insensitive_substring() {
        assert_eq!(
            resolve_by_name("wn dual", Some(FFMPEG_DEVICES))
                .unwrap()
                .index,
            0
        );
    }

    #[test]
    fn no_match_names_what_was_seen() {
        match resolve_by_name("nonexistent", Some(FFMPEG_DEVICES)) {
            Err(SdkError::NoMatch(m)) => assert_eq!(
                m,
                "No camera matches 'nonexistent'. Video devices found: ['WN Dual Camera', 'Example Front Webcam', 'Example Rear Webcam']"
            ),
            other => panic!("{other:?}"),
        }
    }

    #[test]
    fn two_matches_refuses_rather_than_guessing() {
        let raw = FFMPEG_DEVICES.replace("Example Front Webcam", "WN Dual Camera");
        match resolve_by_name("wn dual", Some(&raw)) {
            Err(SdkError::MultipleMatches(m)) => assert_eq!(
                m,
                "2 cameras match 'wn dual' — they cannot be told apart (no USB serial number). Unplug all but one: ['WN Dual Camera', 'WN Dual Camera']"
            ),
            other => panic!("{other:?}"),
        }
    }

    #[test]
    fn picking_from_a_given_list_keeps_the_enumeration_index() {
        let list = [d(0, "Example Front Webcam"), d(1, "WN Dual Camera")];
        assert_eq!(
            pick_by_name("WN Dual", &list).unwrap(),
            d(1, "WN Dual Camera")
        );
        assert!(matches!(pick_by_name("x", &[]), Err(SdkError::NoMatch(_))));
    }

    #[test]
    fn modes_are_parsed_with_codec_size_and_max_fps() {
        let got = list_modes("WN Dual Camera", Some(FFMPEG_OPTIONS));
        assert_eq!(
            got,
            [
                m("MJPG", 1600, 800, 90.0),
                m("MJPG", 2560, 800, 90.0),
                m("YUY2", 1600, 800, 45.0)
            ]
        );
    }

    #[test]
    fn duplicate_mode_lines_collapse() {
        let raw = format!("{FFMPEG_OPTIONS}{FFMPEG_OPTIONS}");
        assert_eq!(list_modes("WN Dual Camera", Some(&raw)).len(), 3);
    }

    #[test]
    fn no_ffmpeg_output_gives_no_modes_rather_than_raising() {
        assert!(list_modes("WN Dual Camera", Some("")).is_empty());
    }

    #[test]
    fn mode_label_is_what_a_combo_box_shows() {
        assert_eq!(m("MJPG", 1600, 800, 90.0).label(), "1600×800 MJPG · 90 fps");
        assert_eq!(m("YUY2", 640, 480, 29.9).label(), "640×480 YUY2 · 29.9 fps");
    }

    #[test]
    fn unknown_codecs_are_upper_cased() {
        let raw = "  vcodec=h264  min s=640x480 fps=5 max s=640x480 fps=30\n  pixel_format=nv12  min s=1x1 fps=1 max s=2x2 fps=29.97\n";
        assert_eq!(
            list_modes("x", Some(raw)),
            [m("H264", 640, 480, 30.0), m("NV12", 2, 2, 30.0)]
        );
    }

    #[test]
    fn parses_a_captured_listing() {
        let names: Vec<_> = list_devices(Some(REAL_LISTING))
            .into_iter()
            .map(|d| d.name)
            .collect();
        assert_eq!(names, ["Example Front Webcam", "Example Rear Webcam"]);
        assert!(list_devices(Some("")).is_empty());
        assert!(list_devices(Some("[x] \"Mic (video)\" (audio)\n")).is_empty()); // 이름 속 "video" 는 무시
        let names: Vec<_> = list_devices(Some("[dshow @ 01] \"A\" (audio, video)\n"))
            .into_iter()
            .map(|d| d.name)
            .collect();
        assert_eq!(names, ["A"]);
    }
}
