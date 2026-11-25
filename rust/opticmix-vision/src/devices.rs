//! Enumerating connected cameras — by name, and by USB location (Python `devices.py`).
//!
//! This camera does not report a USB serial number. Indexes shift just by plugging or unplugging another camera,
//! so choosing by index silently opens a different camera. That is why selection is by name — and, when two
//! cameras share the name, by USB location (`DeviceInfo::usb_location`, `pick_by_usb_location`).
//!
//! A USB location is not a serial number: it names the port the camera is plugged into, and it changes when the
//! camera is plugged into another port. Using two cameras at the same time is not supported. Picking a camera by
//! USB location has been run without a camera only (made-up device lists); it has not been run with a camera, and
//! not run with two cameras.

use std::path::Path;
use std::process::Command;

use crate::error::{Result, SdkError};

/// One device, in the order of the list it came from (DirectShow's, or Media Foundation's for `list_mf_devices`).
///
/// `#[non_exhaustive]`: fields may be added — build one with `DeviceInfo::new`, not a struct literal.
#[derive(Debug, Clone, PartialEq, Eq)]
#[non_exhaustive]
pub struct DeviceInfo {
    pub index: usize,
    pub name: String,
    /// Where the device is plugged in, `None` when the list cannot say (not a USB device, or not readable).
    /// Windows: the first `DEVPKEY_Device_LocationPaths` entry of the USB device, e.g.
    /// `PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(4)#USB(2)` — the doctor's `location`. Linux: the USB device's sysfs
    /// name, e.g. `1-4.2`. Not a serial number: it changes when the camera is plugged into another port. Not read
    /// with the camera, and not with two cameras: only made-up device lists have been run.
    pub usb_location: Option<String>,
}

impl DeviceInfo {
    /// A device entry, e.g. for a made-up list to pick from (`pick_by_name`, `pick_by_usb_location`).
    pub fn new(index: usize, name: impl Into<String>, usb_location: Option<String>) -> DeviceInfo {
        DeviceInfo { index, name: name.into(), usb_location }
    }
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

/// The error for an empty or blank USB location (the same text as Python `devices.find_v4l2_node`).
const EMPTY_USB_LOCATION: &str =
    "usb_location is empty: pass a USB location as the device list or the doctor prints it";

/// Python string repr (single quotes, single quotes inside escaped).
pub(crate) fn repr(s: &str) -> String {
    format!("'{}'", s.replace('\\', "\\\\").replace('\'', "\\'"))
}

fn list_repr<'a>(names: impl Iterator<Item = &'a str>) -> String {
    format!("[{}]", names.map(repr).collect::<Vec<_>>().join(", "))
}

/// (name, kind) of a device line `"name" (kind)` of `ffmpeg -list_devices` output.
fn device_line(l: &str) -> Option<(&str, &str)> {
    let a = l.find('"')? + 1;
    let b = l[a..].find('"')? + a;
    let kind = l[b + 1..].strip_prefix(" (")?;
    (b > a).then(|| (&l[a..b], &kind[..kind.find(')').unwrap_or(0)]))
}

/// Video devices from `ffmpeg -list_devices` output, in the order they appear: (name, DevicePath). The DevicePath is
/// the device's `Alternative name` line without ffmpeg's `@device_pnp_` prefix (None when there is none).
/// Rule (same as Python `_VIDEO_LINE`): the ` (…)` right after `"name"` must contain video.
fn parse_video_devices(text: &str) -> Vec<(String, Option<String>)> {
    let mut out: Vec<(String, Option<String>)> = Vec::new();
    let mut last_was_video = false;
    for l in text.lines() {
        if let Some(i) = l.find("Alternative name \"") {
            let alt = l[i + "Alternative name \"".len()..].trim_end().trim_end_matches('"');
            if let (true, Some(last)) = (last_was_video, out.last_mut()) {
                last.1 = alt.strip_prefix("@device_pnp_").map(str::to_string);
            }
            last_was_video = false;
        } else if let Some((name, kind)) = device_line(l) {
            last_was_video = kind.contains("video");
            if last_was_video {
                out.push((name.to_string(), None));
            }
        }
    }
    out
}

/// `USB\VID_…&PID_…&MI_00\9&…&0&0000` from a DirectShow DevicePath or Media Foundation symbolic link
/// (`\\?\usb#vid_…&pid_…&mi_00#9&…&0&0000#{interface class GUID}\global`): the instance ID with `\` written `#`,
/// then the interface class. None when the path is not a USB device's.
#[cfg_attr(not(windows), allow(dead_code))] // used on Windows and in tests
fn usb_instance_id(device_path: &str) -> Option<String> {
    let mut parts = device_path.strip_prefix(r"\\?\")?.split('#');
    let (bus, hw, inst) = (parts.next()?, parts.next()?, parts.next()?);
    (bus.eq_ignore_ascii_case("usb") && !inst.starts_with('{') && parts.next()?.starts_with('{'))
        .then(|| format!(r"{bus}\{hw}\{inst}").to_uppercase())
}

/// The USB location of the device at a DirectShow DevicePath or Media Foundation symbolic link: the first
/// `DEVPKEY_Device_LocationPaths` entry of its USB device (the parent, for one interface of a composite device —
/// the same entry `python -m opticmix_vision.doctor` prints as `location`). Reads the device tree (cfgmgr32);
/// opens no device. None when it is not a present USB device, or off Windows.
fn usb_location_of_device_path(device_path: &str) -> Option<String> {
    #[cfg(windows)]
    {
        usb_instance_id(device_path).and_then(|id| unsafe { location_paths::first(&id) })
    }
    #[cfg(not(windows))]
    {
        let _ = device_path;
        None
    }
}

#[cfg(windows)]
mod location_paths {
    use windows::core::HSTRING;
    use windows::Win32::Devices::DeviceAndDriverInstallation::{
        CM_Get_DevNode_PropertyW, CM_Get_Parent, CM_Locate_DevNodeW, CM_LOCATE_DEVNODE_NORMAL, CR_SUCCESS,
    };
    use windows::Win32::Devices::Properties::{DEVPKEY_Device_LocationPaths, DEVPROPTYPE, DEVPROP_TYPE_STRING_LIST};

    /// The first LocationPaths entry of a present device (its parent's when it is an interface, `&MI_`).
    pub(super) unsafe fn first(instance_id: &str) -> Option<String> {
        let mut node = 0u32;
        if CM_Locate_DevNodeW(&mut node, &HSTRING::from(instance_id), CM_LOCATE_DEVNODE_NORMAL) != CR_SUCCESS {
            return None; // not present
        }
        if instance_id.contains("&MI_") {
            let mut parent = 0u32;
            if CM_Get_Parent(&mut parent, node, 0) != CR_SUCCESS {
                return None;
            }
            node = parent;
        }
        let (mut ty, mut size) = (DEVPROPTYPE(0), 0u32);
        // The first call only reports the size (CR_BUFFER_SMALL).
        let _ = CM_Get_DevNode_PropertyW(node, &DEVPKEY_Device_LocationPaths, &mut ty, None, &mut size, 0);
        if size < 2 {
            return None;
        }
        // A REG_MULTI_SZ-style list of UTF-16 strings; size is in bytes.
        let mut buf = vec![0u16; (size as usize).div_ceil(2)];
        if CM_Get_DevNode_PropertyW(
            node,
            &DEVPKEY_Device_LocationPaths,
            &mut ty,
            Some(buf.as_mut_ptr().cast()),
            &mut size,
            0,
        ) != CR_SUCCESS
            || ty != DEVPROP_TYPE_STRING_LIST
        {
            return None;
        }
        let n = (size as usize / 2).min(buf.len());
        String::from_utf16_lossy(&buf[..n]).split('\0').find(|s| !s.is_empty()).map(str::to_string)
    }
}

/// Linux: the USB location of a V4L2 node (`/dev/video0`) — its USB device's sysfs name, e.g. `1-4.2`. Reads sysfs
/// only. None when the node is not on USB or sysfs cannot be read.
#[cfg(all(feature = "capture", target_os = "linux"))]
pub(crate) fn v4l2_usb_location(node: &Path) -> Option<String> {
    let dev = std::fs::canonicalize(Path::new("/sys/class/video4linux").join(node.file_name()?).join("device")).ok()?;
    usb_device_above(&dev)
}

/// The sysfs USB device a device folder belongs to: the nearest of it and its parents (four in all, as the doctor
/// looks) that has an `idVendor` file — that folder's name.
#[cfg_attr(not(all(feature = "capture", target_os = "linux")), allow(dead_code))] // used on Linux and in tests
fn usb_device_above(dir: &Path) -> Option<String> {
    dir.ancestors()
        .take(4)
        .find(|d| d.join("idVendor").is_file())
        .and_then(|d| d.file_name()?.to_str().map(str::to_string))
}

fn ffmpeg_stderr(args: &[&str]) -> std::io::Result<String> {
    let out = Command::new("ffmpeg").args(args).output()?;
    Ok(String::from_utf8_lossy(&out.stderr).into_owned())
}

/// DeviceInfo for each device of an ffmpeg listing, its USB location found from the DevicePath by `locate`.
fn from_listing(text: &str, locate: impl Fn(&str) -> Option<String>) -> Vec<DeviceInfo> {
    parse_video_devices(text)
        .into_iter()
        .enumerate()
        .map(|(index, (name, path))| DeviceInfo { index, name, usb_location: path.as_deref().and_then(&locate) })
        .collect()
}

/// DirectShow's video input devices, enumerated directly (no ffmpeg), in the order OpenCV's `CAP_DSHOW` counts
/// device indexes (devices that fail to bind are counted too), each with its USB location. Binds to no device
/// (read only). Empty list off Windows. The USB location: not read with the camera, and not with two cameras.
pub fn list_dshow_devices() -> Result<Vec<DeviceInfo>> {
    Ok(crate::dshow::enumerate_video_device_paths()?
        .into_iter()
        .enumerate()
        .map(|(index, (name, path))| DeviceInfo {
            index,
            name: name.unwrap_or_default(),
            usb_location: path.as_deref().and_then(usb_location_of_device_path),
        })
        .collect())
}

/// Video devices in DirectShow enumeration order, each with its USB location. Laptop webcams are included — the
/// user has to pick among them. Without `raw` this runs ffmpeg; without ffmpeg (most customer PCs) it enumerates
/// DirectShow directly (`list_dshow_devices`; a failure there is an empty list — it shows as "not found").
pub fn list_devices(raw: Option<&str>) -> Vec<DeviceInfo> {
    let text = match raw {
        Some(r) => r.to_string(),
        None => match ffmpeg_stderr(&["-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"]) {
            Ok(r) => r,
            Err(_) => return list_dshow_devices().unwrap_or_default(),
        },
    };
    from_listing(&text, usb_location_of_device_path)
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
        n => Err(SdkError::MultipleMatches(
            match hits.iter().map(|d| d.usb_location.as_deref()).collect::<Option<Vec<&str>>>() {
                Some(locations) => format!(
                    "{n} cameras match {} — they cannot be told apart by name (no USB serial number). Unplug all \
                     but one, or pick one by USB location (usb_location: the port it is plugged into, not a serial \
                     number): {}",
                    repr(needle),
                    list_repr(locations.into_iter())
                ),
                None => format!(
                    "{n} cameras match {} — they cannot be told apart (no USB serial number). Unplug all but one: {}",
                    repr(needle),
                    list_repr(hits.iter().map(|d| d.name.as_str()))
                ),
            },
        )),
    }
}

/// Picks the one device whose name contains `needle` (case-insensitive) and whose `usb_location` is `usb_location`
/// (ignoring case) — how to choose between cameras that share a name. An empty or blank `usb_location` is
/// `InvalidValue`. A USB location is not a serial number: it names the port the camera is plugged into, and it
/// changes when the camera is plugged into another port. Using two cameras at the same time is not supported.
/// Picking a camera by USB location has been run without a camera only (made-up device lists); it has not been run
/// with a camera, and not run with two cameras.
pub fn pick_by_usb_location(needle: &str, usb_location: &str, devices: &[DeviceInfo]) -> Result<DeviceInfo> {
    if usb_location.trim().is_empty() {
        return Err(SdkError::InvalidValue(EMPTY_USB_LOCATION.into()));
    }
    let low = needle.to_lowercase();
    let hits: Vec<&DeviceInfo> = devices
        .iter()
        .filter(|d| {
            d.name.to_lowercase().contains(&low)
                && d.usb_location.as_deref().is_some_and(|l| l.eq_ignore_ascii_case(usb_location))
        })
        .collect();
    match hits.len() {
        1 => Ok(hits[0].clone()),
        0 => {
            let found: Vec<String> = devices
                .iter()
                .map(|d| format!("({}, {})", repr(&d.name), d.usb_location.as_deref().map_or("None".into(), repr)))
                .collect();
            let none = if devices.iter().any(|d| d.usb_location.is_none()) {
                ". None: the USB location could not be read"
            } else {
                ""
            };
            Err(SdkError::NoMatch(format!(
                "No camera matches {} at USB location {}. Video devices found (name, USB location): [{}]{none}",
                repr(needle),
                repr(usb_location),
                found.join(", ")
            )))
        }
        n => Err(SdkError::MultipleMatches(format!(
            "{n} video devices match {} at USB location {}: indexes {:?}",
            repr(needle),
            repr(usb_location),
            hits.iter().map(|d| d.index).collect::<Vec<_>>()
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

/// Media Foundation's video capture sources (`MFEnumDeviceSources`, source type VIDCAP): their names, in list order.
///
/// OpenCV's `CAP_MSMF` opens device index N as entry N of this same call (OpenCV 4.10.0 and 4.13.0,
/// `modules/videoio/src/cap_msmf.cpp`: `DeviceList::read` enumerates with only the VIDCAP source type set, and
/// `CvCapture_MSMF::open(int)` activates `devices[index]`), so an index in this list is the index OpenCV's MSMF backend
/// takes. Microsoft documents neither the order nor that two calls agree on it — the same assumption the DirectShow
/// lookup makes (a camera plugged in between this call and opening can shift it).
/// Activates no device (read only). Empty list on non-Windows.
pub fn enumerate_mf_video_devices() -> Result<Vec<String>> {
    Ok(list_mf_devices()?.into_iter().map(|d| d.name).collect())
}

/// The same Media Foundation list as `enumerate_mf_video_devices`, each entry with its index and USB location
/// (from the source's symbolic link). An index here is the one `DeviceSelector::Index` takes. Activates no device
/// (read only). Empty list on non-Windows. The USB location: not read with the camera, and not with two cameras.
pub fn list_mf_devices() -> Result<Vec<DeviceInfo>> {
    #[cfg(windows)]
    {
        let _com = crate::dshow::com::ComInit::mta()?;
        // The activation objects are released at the end of this statement, before CoUninitialize (`_com`),
        // for the same reason as in the DirectShow enumeration.
        let list = unsafe { mf_enum::sources()? }
            .into_iter()
            .enumerate()
            .map(|(index, (_, name, usb_location))| DeviceInfo { index, name, usb_location })
            .collect();
        Ok(list)
    }
    #[cfg(not(windows))]
    {
        Ok(Vec::new())
    }
}

/// The Media Foundation video source list, shared by the device list above and Media Foundation capture (`mf.rs`).
#[cfg(windows)]
pub(crate) mod mf_enum {
    use std::sync::OnceLock;

    use windows::core::PWSTR;
    use windows::Win32::Media::MediaFoundation::*;
    use windows::Win32::System::Com::CoTaskMemFree;

    use crate::error::{Result, SdkError};

    pub(crate) fn dev_err(what: &str, e: windows::core::Error) -> SdkError {
        SdkError::Device(format!("{what} failed: HRESULT 0x{:08X}", e.code().0 as u32))
    }

    /// The MFStartup result, once per process. A failure is remembered too, so every call gives the same error.
    pub(crate) static MF_STARTED: OnceLock<Result<()>> = OnceLock::new();

    // MFShutdown is never called: Media Foundation stays started until the process ends.
    pub(crate) fn mf_startup() -> Result<()> {
        MF_STARTED
            .get_or_init(|| unsafe { MFStartup(MF_VERSION, MFSTARTUP_FULL) }.map_err(|e| dev_err("MFStartup", e)))
            .clone()
    }

    /// Every video capture source with its friendly name ("" when it has none) and USB location (from its symbolic
    /// link), in `MFEnumDeviceSources` order. COM must be initialized on this thread. Activates nothing.
    pub(crate) unsafe fn sources() -> Result<Vec<(IMFActivate, String, Option<String>)>> {
        mf_startup()?;
        let mut attrs: Option<IMFAttributes> = None;
        MFCreateAttributes(&mut attrs, 1).map_err(|e| dev_err("MFCreateAttributes", e))?;
        let attrs = attrs.ok_or_else(|| SdkError::Device("MFCreateAttributes gave nothing".into()))?;
        attrs
            .SetGUID(&MF_DEVSOURCE_ATTRIBUTE_SOURCE_TYPE, &MF_DEVSOURCE_ATTRIBUTE_SOURCE_TYPE_VIDCAP_GUID)
            .map_err(|e| dev_err("SetGUID", e))?;
        let mut pp: *mut Option<IMFActivate> = std::ptr::null_mut();
        let mut count = 0u32;
        MFEnumDeviceSources(&attrs, &mut pp, &mut count).map_err(|e| dev_err("MFEnumDeviceSources", e))?;
        // Take over the reference each array element holds (a clone would leave the element's reference
        // unreleased), then free only the array.
        let list: Vec<IMFActivate> = if pp.is_null() || count == 0 {
            Vec::new()
        } else {
            std::slice::from_raw_parts_mut(pp, count as usize).iter_mut().filter_map(Option::take).collect()
        };
        CoTaskMemFree(Some(pp as _));
        Ok(list
            .into_iter()
            .map(|a| {
                let n = string(&a, &MF_DEVSOURCE_ATTRIBUTE_FRIENDLY_NAME).unwrap_or_default();
                let link = string(&a, &MF_DEVSOURCE_ATTRIBUTE_SOURCE_TYPE_VIDCAP_SYMBOLIC_LINK).ok();
                let usb_location = link.as_deref().and_then(super::usb_location_of_device_path);
                (a, n, usb_location)
            })
            .collect())
    }

    unsafe fn string(a: &IMFActivate, key: &windows::core::GUID) -> Result<String> {
        let mut s = PWSTR::null();
        let mut len = 0u32;
        a.GetAllocatedString(key, &mut s, &mut len)
            .map_err(|e| dev_err("GetAllocatedString", e))?;
        let out = s.to_string().unwrap_or_default();
        CoTaskMemFree(Some(s.0 as _));
        Ok(out)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// MFStartup does not open a camera (it only initializes the MF platform). A second call returns the remembered result.
    #[cfg(windows)]
    #[test]
    fn mf_starts_once_per_process() {
        assert_eq!(mf_enum::mf_startup(), Ok(()));
        assert!(mf_enum::MF_STARTED.get().is_some());
        assert_eq!(mf_enum::mf_startup(), Ok(()));
    }

    /// Off Windows there is no Media Foundation: an empty list, not an error.
    #[cfg(not(windows))]
    #[test]
    fn the_media_foundation_list_is_empty_off_windows() {
        assert_eq!(enumerate_mf_video_devices(), Ok(Vec::new()));
    }

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
            usb_location: None,
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

    // ---- USB location: made-up lists only — nothing is enumerated -------------------------------------------

    /// Two Weevia S1 and a webcam in the format of a real ffmpeg 8.x listing (2026-11-23, one Weevia S1 on a hub);
    /// names other than the Weevia S1's and every instance ID made up.
    const TWO_WEEVIA: &str = r#"[in#0 @ 0001] "WN Dual Camera" (video)
[in#0 @ 0001]   Alternative name "@device_pnp_\\?\usb#vid_1d6b&pid_0200&mi_00#9&00000001&0&0000#{65e8773d-8f56-11d0-a3b9-00a0c9223196}\global"
[in#0 @ 0001] "Example Webcam" (video)
[in#0 @ 0001]   Alternative name "@device_pnp_\\?\display#example_01#5&0000000a&0&uid0000000001#{65e8773d-8f56-11d0-a3b9-00a0c9223196}\{00000000-0000-0000-0000-000000000001}"
[in#0 @ 0001] "WN Dual Camera" (video)
[in#0 @ 0001]   Alternative name "@device_pnp_\\?\usb#vid_1d6b&pid_0200&mi_00#9&00000002&0&0000#{65e8773d-8f56-11d0-a3b9-00a0c9223196}\global"
[in#0 @ 0001] "Example Virtual Camera" (none)
[in#0 @ 0001]   Alternative name "@device_sw_{860BB310-5D01-11D0-BD3B-00A0C911CE86}\{00000000-0000-0000-0000-000000000004}"
[in#0 @ 0001] "Microphone (Example Audio)" (audio)
[in#0 @ 0001]   Alternative name "@device_cm_{33D9A762-90C8-11D0-BD43-00A0C911CE86}\wave_{00000000-0000-0000-0000-000000000005}"
"#;
    const LOC1: &str = "PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(1)";
    const LOC2: &str = "PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(4)#USB(2)";

    /// A made-up device tree: the two Weevia S1 instance IDs of TWO_WEEVIA, each on its own port.
    fn fake_locate(path: &str) -> Option<String> {
        match usb_instance_id(path)?.as_str() {
            r"USB\VID_1D6B&PID_0200&MI_00\9&00000001&0&0000" => Some(LOC1.into()),
            r"USB\VID_1D6B&PID_0200&MI_00\9&00000002&0&0000" => Some(LOC2.into()),
            _ => None,
        }
    }

    fn at(index: usize, name: &str, usb_location: Option<&str>) -> DeviceInfo {
        DeviceInfo {
            index,
            name: name.into(),
            usb_location: usb_location.map(str::to_string),
        }
    }

    #[test]
    fn the_usb_instance_id_is_read_from_a_device_path() {
        assert_eq!(
            usb_instance_id(r"\\?\usb#vid_1d6b&pid_0200&mi_00#9&0000abcd&0&0000#{65e8773d-8f56-11d0-a3b9-00a0c9223196}\global")
                .as_deref(),
            Some(r"USB\VID_1D6B&PID_0200&MI_00\9&0000ABCD&0&0000")
        );
        // not a USB device, no instance part, not a device path at all
        assert_eq!(usb_instance_id(r"\\?\display#example_01#5&0000000a&0&uid1#{65e8773d-8f56-11d0-a3b9-00a0c9223196}\{0}"), None);
        assert_eq!(usb_instance_id(r"\\?\usb#vid_1d6b&pid_0200"), None);
        assert_eq!(usb_instance_id(r"@device_sw_{860BB310-5D01-11D0-BD3B-00A0C911CE86}\{0}"), None);
    }

    #[test]
    fn each_listed_device_carries_the_usb_location_of_its_alternative_name() {
        assert_eq!(
            from_listing(TWO_WEEVIA, fake_locate),
            [
                at(0, "WN Dual Camera", Some(LOC1)),
                at(1, "Example Webcam", None),
                at(2, "WN Dual Camera", Some(LOC2))
            ]
        );
    }

    #[test]
    fn picking_by_usb_location_tells_two_cameras_of_one_name_apart() {
        let list = from_listing(TWO_WEEVIA, fake_locate);
        assert_eq!(pick_by_usb_location("wn dual", LOC2, &list).unwrap(), at(2, "WN Dual Camera", Some(LOC2)));
        // the location is compared ignoring case
        assert_eq!(pick_by_usb_location("WN Dual Camera", &LOC1.to_lowercase(), &list).unwrap().index, 0);
    }

    #[test]
    fn two_cameras_of_one_name_point_to_their_usb_locations() {
        match pick_by_name("wn dual", &from_listing(TWO_WEEVIA, fake_locate)) {
            Err(SdkError::MultipleMatches(m)) => assert_eq!(
                m,
                "2 cameras match 'wn dual' — they cannot be told apart by name (no USB serial number). Unplug all \
                 but one, or pick one by USB location (usb_location: the port it is plugged into, not a serial \
                 number): ['PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(1)', 'PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(4)#USB(2)']"
            ),
            other => panic!("{other:?}"),
        }
    }

    #[test]
    fn no_camera_at_the_usb_location_lists_names_and_locations() {
        let list = from_listing(TWO_WEEVIA, fake_locate);
        match pick_by_usb_location("wn dual", "PCIROOT(0)#USB(9)", &list) {
            Err(SdkError::NoMatch(m)) => assert_eq!(
                m,
                "No camera matches 'wn dual' at USB location 'PCIROOT(0)#USB(9)'. Video devices found (name, USB \
                 location): [('WN Dual Camera', 'PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(1)'), ('Example Webcam', None), \
                 ('WN Dual Camera', 'PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(4)#USB(2)')]. None: the USB location could \
                 not be read"
            ),
            other => panic!("{other:?}"),
        }
        // another camera's location does not make this name match
        assert!(matches!(pick_by_usb_location("Example", LOC1, &list), Err(SdkError::NoMatch(_))));
        assert!(matches!(pick_by_usb_location("x", LOC1, &[]), Err(SdkError::NoMatch(_))));
    }

    #[test]
    fn a_list_without_usb_locations_cannot_pick_by_one_and_keeps_the_old_message() {
        let list = [at(0, "WN Dual Camera", None), at(1, "WN Dual Camera", None)];
        let Err(SdkError::NoMatch(m)) = pick_by_usb_location("wn dual", LOC1, &list) else { panic!() };
        assert!(m.ends_with("[('WN Dual Camera', None), ('WN Dual Camera', None)]. None: the USB location could not be read"), "{m}");
        let Err(SdkError::MultipleMatches(m)) = pick_by_name("wn dual", &list) else { panic!() };
        assert!(m.ends_with("they cannot be told apart (no USB serial number). Unplug all but one: ['WN Dual Camera', 'WN Dual Camera']"), "{m}");
    }

    /// An empty or blank location is the caller's mistake: refused, never matched to a device whose location is not
    /// known (or empty).
    #[test]
    fn an_empty_usb_location_is_refused() {
        let list = [at(0, "WN Dual Camera", None), at(1, "WN Dual Camera", Some("")), at(2, "WN Dual Camera", Some(LOC1))];
        for blank in ["", "  "] {
            match pick_by_usb_location("wn dual", blank, &list) {
                Err(SdkError::InvalidValue(m)) => assert!(m.starts_with("usb_location is empty"), "{m}"),
                other => panic!("{other:?}"),
            }
        }
    }

    #[test]
    fn one_device_listed_twice_at_one_location_is_refused() {
        let list = [at(0, "WN Dual Camera", Some(LOC1)), at(1, "WN Dual Camera", Some(LOC1))];
        let Err(SdkError::MultipleMatches(m)) = pick_by_usb_location("wn dual", LOC1, &list) else { panic!() };
        assert_eq!(m, "2 video devices match 'wn dual' at USB location 'PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(1)': indexes [0, 1]");
    }

    /// Linux sysfs laid out in a folder: the USB device `1-2` (has idVendor) and its interface folder. Interface folders
    /// are named `1-2:1.0` on Linux; ':' is not allowed in a Windows file name, so `1-2_1.0` here.
    #[test]
    fn the_sysfs_usb_device_is_the_nearest_folder_with_an_id_vendor() {
        let root = std::env::temp_dir().join(format!("ov_usbloc_{}", std::process::id()));
        let iface = root.join("usb1").join("1-2").join("1-2_1.0");
        std::fs::create_dir_all(&iface).unwrap();
        std::fs::write(root.join("usb1").join("1-2").join("idVendor"), "1d6b\n").unwrap();
        assert_eq!(usb_device_above(&iface).as_deref(), Some("1-2"));
        assert_eq!(usb_device_above(&root.join("usb1").join("1-2")).as_deref(), Some("1-2"));
        assert_eq!(usb_device_above(&root.join("usb1")), None);
        std::fs::remove_dir_all(&root).unwrap();
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
