//! 연결된 카메라 열거 — 이름 기준(Python `devices.py`).
//!
//! 이 카메라는 USB 시리얼 번호를 보고하지 않는다. 인덱스는 다른 카메라를 꽂거나 빼는 것만으로 밀리므로,
//! 인덱스로 장치를 고르면 조용히 다른 카메라를 연다. 그래서 선택은 항상 이름으로 한다.

use std::process::Command;

use crate::error::{Result, SdkError};

/// DirectShow 열거 순서의 장치 하나.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DeviceInfo {
    pub index: usize,
    pub name: String,
}

/// 장치가 스스로 보고하는 모드 하나.
#[derive(Debug, Clone, PartialEq)]
pub struct Mode {
    pub fourcc: String,
    pub width: u32,
    pub height: u32,
    pub max_fps: f64,
}

impl Mode {
    /// 콤보박스에 보일 문자열(Python `f"{w}×{h} {fourcc} · {max_fps:g} fps"`; max_fps 는 소수 1자리라 Display 와 같다).
    pub fn label(&self) -> String {
        format!(
            "{}×{} {} · {} fps",
            self.width, self.height, self.fourcc, self.max_fps
        )
    }
}

/// Python 문자열 repr(작은따옴표, 안의 작은따옴표는 이스케이프).
fn repr(s: &str) -> String {
    format!("'{}'", s.replace('\\', "\\\\").replace('\'', "\\'"))
}

fn list_repr<'a>(names: impl Iterator<Item = &'a str>) -> String {
    format!("[{}]", names.map(repr).collect::<Vec<_>>().join(", "))
}

/// `ffmpeg -list_devices` 출력에서 영상 장치 이름만, 나온 순서대로.
/// 규칙(Python `_VIDEO_LINE` 과 같음): `"이름"` 바로 뒤 ` (…)` 괄호에 video 가 있어야 한다.
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

/// ffmpeg 가 없을 때 DirectShow 를 직접 열거한다(Windows). 실패는 빈 목록 — "못 찾음"으로 보인다.
fn native_devices() -> Vec<String> {
    crate::dshow::enumerate_video_devices()
        .map(|v| v.into_iter().map(Option::unwrap_or_default).collect())
        .unwrap_or_default()
}

/// DirectShow 열거 순서 그대로의 영상 장치 목록. 노트북 웹캠도 포함한다 — 사용자가 그중에서 골라야 한다.
/// `raw` 가 없으면 ffmpeg 를 부르고, ffmpeg 가 없으면(고객 PC 에 대개 없다) DirectShow 를 직접 열거한다.
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

/// 이름 부분일치(대소문자 무시)로 장치 하나를 고른다. 애매하면 추측하지 않는다.
/// 이름 부분일치(대소문자 무시)로 장치 하나를 고른다. 애매하면 추측하지 않는다.
pub fn resolve_by_name(needle: &str, raw: Option<&str>) -> Result<DeviceInfo> {
    pick_by_name(needle, &list_devices(raw))
}

/// 이미 가진 목록에서 고른다(Python 래퍼는 ffmpeg/직접 열거를 Python 쪽에서 해 목록을 넘긴다).
pub fn pick_by_name(needle: &str, devices: &[DeviceInfo]) -> Result<DeviceInfo> {
    let low = needle.to_lowercase();
    let hits: Vec<&DeviceInfo> = devices
        .iter()
        .filter(|d| d.name.to_lowercase().contains(&low))
        .collect();
    match hits.len() {
        0 => Err(SdkError::NoMatch(format!(
            "{} 에 맞는 카메라가 없다. 보이는 장치: {}",
            repr(needle),
            list_repr(devices.iter().map(|d| d.name.as_str()))
        ))),
        1 => Ok(hits[0].clone()),
        n => Err(SdkError::MultipleMatches(format!(
            "{} 에 {n}개가 맞는다 — 시리얼이 없어 구분할 수 없다. 하나만 남기고 뽑아라: {}",
            repr(needle),
            list_repr(hits.iter().map(|d| d.name.as_str()))
        ))),
    }
}

/// `vcodec=X|pixel_format=X  min s=WxH fps=F  max s=WxH fps=F` 한 줄.
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

/// 장치가 스스로 보고하는 모드들. 빈 목록은 "모드가 없다" 가 아니라 **"알아내지 못했다"** 이다.
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

    /// Python tests/test_devices.py 의 표본(실제 실행에서 딴 형식, 다른 장치 이름·ID 는 가짜로 바꿈).
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

    /// 실제 ffmpeg 8.x 출력에서 딴 목록(2026-09-07). 형식은 그대로, 장치 이름·인스턴스 ID 는 가짜로 바꿈.
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
                "'nonexistent' 에 맞는 카메라가 없다. 보이는 장치: ['WN Dual Camera', 'Example Front Webcam', 'Example Rear Webcam']"
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
                "'wn dual' 에 2개가 맞는다 — 시리얼이 없어 구분할 수 없다. 하나만 남기고 뽑아라: ['WN Dual Camera', 'WN Dual Camera']"
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
