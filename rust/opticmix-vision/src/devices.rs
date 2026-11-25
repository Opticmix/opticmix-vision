//! Enumerating connected cameras — by name, and by USB location (Python `devices.py`).
//!
//! This camera does not report a USB serial number. Indexes shift just by plugging or unplugging another camera,
//! so choosing by index silently opens a different camera. That is why selection is by name — and, when two
//! cameras share the name, by USB location (`DeviceInfo::usb_location`, `pick_by_usb_location`).
//!
//! A USB location is not a serial number: it names the port the camera is plugged into, and it changes when the
//! camera is plugged into another port. Using two cameras at the same time is not supported. Picking a camera by USB
//! location has been run with the camera once (one PC, one camera, 2026-11-25): with one camera connected, the list
//! showed its USB location, and given that location it opened at 1600x800 MJPG and read one frame; a location no device
//! has was refused (Python DirectShow, Rust Media Foundation); not run with two cameras.
//!
//! Each listed device also carries its USB IDs (`vid`, `pid`, `bcd_device`) and link speed (`link_speed`), read from
//! the device tree and the USB hub driver without opening the device — the values `python -m opticmix_vision.doctor`
//! prints for it. Linux (`list_v4l2_devices`, feature `capture`): from sysfs. Not read with the camera: only made-up
//! device lists and sysfs trees have been run.

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
    /// The USB vendor ID (`idVendor`), e.g. `0x1D6B`. Windows: from the USB device's hardware IDs
    /// (`USB\VID_1D6B&PID_0200&REV_0409`, `DEVPKEY_Device_HardwareIds`), read from the device tree without opening
    /// the device — the doctor's `VID:PID`. Linux: sysfs `idVendor` (`list_v4l2_devices`). `None` when it cannot be
    /// read; other systems do not read it.
    pub vid: Option<u16>,
    /// The USB product ID (`idProduct`), e.g. `0x0200`; read as `vid`.
    pub pid: Option<u16>,
    /// The USB device revision (`bcdDevice`, binary-coded decimal), e.g. `0x0409`; read as `vid` — the doctor's
    /// "USB device revision (bcdDevice)". Not a firmware version: how it relates to the firmware version has not
    /// been verified.
    pub bcd_device: Option<u16>,
    /// The speed of the USB link, as the hub driver the device is plugged into reports it for that port (the
    /// doctor's "link speed", read the way Microsoft's USBView does; the device is not opened): `"Low"` (1.5 Mb/s),
    /// `"Full"` (12 Mb/s), `"High"` (480 Mb/s), `"Super"` (5 Gb/s) or `"Super+"` (10 Gb/s or more). `None` when it
    /// cannot be read — the hub cannot be asked, or the hub's port holds another device. Linux: sysfs `speed`
    /// (`list_v4l2_devices`), the same words. Other systems do not read it.
    ///
    /// `vid`, `pid`, `bcd_device` and `link_speed` are read by the lists (`list_devices`, `list_dshow_devices`,
    /// `list_mf_devices`, Linux `list_v4l2_devices`); opening a camera (`Device`, its controls) reads the USB location
    /// only, as 0.0.6 did. Not read with the camera — only made-up device lists, hardware IDs, hub replies and sysfs
    /// trees have been run. The camera reports no USB serial number, no firmware version and no
    /// temperature, so there are no fields for them.
    pub link_speed: Option<String>,
}

impl DeviceInfo {
    /// A device entry, e.g. for a made-up list to pick from (`pick_by_name`, `pick_by_usb_location`).
    /// `vid`, `pid`, `bcd_device` and `link_speed` are `None`; set them on the entry when a made-up list needs them.
    pub fn new(index: usize, name: impl Into<String>, usb_location: Option<String>) -> DeviceInfo {
        DeviceInfo { index, name: name.into(), usb_location, vid: None, pid: None, bcd_device: None, link_speed: None }
    }

    /// An entry of a list, with what the device tree said about its USB device.
    fn listed(index: usize, name: String, usb: Usb) -> DeviceInfo {
        let Usb { location, vid, pid, bcd_device, link_speed } = usb;
        DeviceInfo { index, name, usb_location: location, vid, pid, bcd_device, link_speed }
    }
}

/// What the device tree and the hub driver say about the USB device a listed device belongs to.
#[derive(Debug, Default, Clone, PartialEq, Eq)]
struct Usb {
    location: Option<String>,
    vid: Option<u16>,
    pid: Option<u16>,
    bcd_device: Option<u16>,
    link_speed: Option<String>,
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

/// What the device tree says about the USB device at a DirectShow DevicePath or Media Foundation symbolic link (the
/// parent, for one interface of a composite device). `location`: its first `DEVPKEY_Device_LocationPaths` entry — the
/// same entry `python -m opticmix_vision.doctor` prints as `location`. With `ids_and_link`, also its VID, PID and
/// bcdDevice (`DEVPKEY_Device_HardwareIds`) and the link speed its hub reports — what the doctor reads. Reads the
/// device tree (cfgmgr32) and asks the hub driver; opens no device. Nothing when it is not a present USB device, or
/// off Windows.
fn usb_of_device_path(device_path: &str, ids_and_link: bool) -> Usb {
    #[cfg(windows)]
    {
        usb_instance_id(device_path).map_or_else(Usb::default, |id| unsafe { device_tree::usb(&id, ids_and_link) })
    }
    #[cfg(not(windows))]
    {
        let _ = (device_path, ids_and_link);
        Usb::default()
    }
}

/// The length of a USB_NODE_CONNECTION_INFORMATION_EX reply up to `ConnectionStatus` (usbioctl.h, packed):
/// ConnectionIndex, the 18-byte device descriptor (idVendor at 12, idProduct at 14, bcdDevice at 16),
/// CurrentConfigurationValue, Speed (at 23), DeviceIsHub, DeviceAddress, NumberOfOpenPipes, ConnectionStatus (at 31).
#[cfg_attr(not(windows), allow(dead_code))] // used on Windows and in tests
const EX_LEN: usize = 35;
/// USB_NODE_CONNECTION_INFORMATION_EX_V2: ConnectionIndex, Length, SupportedUsbProtocols, Flags (all ULONG).
#[cfg_attr(not(windows), allow(dead_code))] // used on Windows and in tests
const V2_LEN: usize = 16;

/// The strings of a `DEVPROP_TYPE_STRING_LIST` property (UTF-16, each ended by a NUL), empty ones left out.
#[cfg_attr(not(windows), allow(dead_code))] // used on Windows and in tests
fn utf16_list(bytes: &[u8]) -> Vec<String> {
    let units: Vec<u16> = bytes.as_chunks::<2>().0.iter().map(|c| u16::from_le_bytes(*c)).collect();
    String::from_utf16_lossy(&units).split('\0').filter(|s| !s.is_empty()).map(str::to_string).collect()
}

/// (VID, PID, bcdDevice) from a USB device's hardware IDs: the first `VID_xxxx&PID_xxxx&REV_xxxx`, in hex, case
/// ignored — what the doctor reads (`USB\VID_1D6B&PID_0200&REV_0409` gives `(0x1D6B, 0x0200, 0x0409)`).
#[cfg_attr(not(windows), allow(dead_code))] // used on Windows and in tests
fn ids_from_hardware_ids(ids: &[String]) -> Option<(u16, u16, u16)> {
    let up = ids.join(" ").to_uppercase();
    let hex = |s: &str| {
        (s.len() == 4 && s.bytes().all(|b| b.is_ascii_hexdigit())).then(|| u16::from_str_radix(s, 16).ok()).flatten()
    };
    up.match_indices("VID_").find_map(|(i, _)| {
        let s = up.get(i..i + 26)?; // VID_xxxx&PID_xxxx&REV_xxxx
        (s.get(8..13)? == "&PID_" && s.get(17..22)? == "&REV_").then_some(())?;
        Some((hex(s.get(4..8)?)?, hex(s.get(13..17)?)?, hex(s.get(22..26)?)?))
    })
}

/// The link speed a hub reports for its port, from its EX reply and V2 flags (`None` when that call failed) — read
/// as the doctor reads it: only when the port holds this very device (connected, the same VID and PID), and the V2
/// flags win over the EX speed, which can say High for a SuperSpeed link. `None` otherwise.
#[cfg_attr(not(windows), allow(dead_code))] // used on Windows and in tests
fn hub_link_speed(ex: &[u8], v2_flags: Option<u32>, vid: u16, pid: u16) -> Option<&'static str> {
    let ex = ex.get(..EX_LEN)?;
    let u16_at = |i: usize| u16::from_le_bytes([ex[i], ex[i + 1]]);
    let connected = u32::from_le_bytes([ex[31], ex[32], ex[33], ex[34]]) == 1;
    (connected && (u16_at(12), u16_at(14)) == (vid, pid)).then_some(())?;
    link_name(ex[23], v2_flags)
}

/// `"Low"`, `"Full"`, `"High"`, `"Super"` or `"Super+"` from the EX reply's Speed (USB_DEVICE_SPEED) and the V2 flags
/// (bit 0: operating at SuperSpeed or higher, bit 2: at SuperSpeedPlus or higher) — the doctor's names. `None` for a
/// speed code it does not know.
#[cfg_attr(not(windows), allow(dead_code))] // used on Windows and in tests
fn link_name(speed: u8, v2_flags: Option<u32>) -> Option<&'static str> {
    match v2_flags {
        Some(f) if f & 0x4 != 0 => Some("Super+"),
        Some(f) if f & 0x1 != 0 => Some("Super"),
        _ => ["Low", "Full", "High", "Super"].get(usize::from(speed)).copied(),
    }
}

#[cfg(windows)]
mod device_tree {
    use windows::core::{GUID, HSTRING, PCWSTR};
    use windows::Win32::Devices::DeviceAndDriverInstallation::{
        CM_Get_DevNode_PropertyW, CM_Get_Device_IDW, CM_Get_Device_Interface_ListW, CM_Get_Device_Interface_List_SizeW,
        CM_Get_Parent, CM_Locate_DevNodeW, CM_GET_DEVICE_INTERFACE_LIST_PRESENT, CM_LOCATE_DEVNODE_NORMAL, CR_SUCCESS,
    };
    use windows::Win32::Devices::Properties::{
        DEVPKEY_Device_Address, DEVPKEY_Device_HardwareIds, DEVPKEY_Device_LocationPaths, DEVPROPTYPE,
        DEVPROP_TYPE_STRING_LIST, DEVPROP_TYPE_UINT32,
    };
    use windows::Win32::Foundation::{CloseHandle, DEVPROPKEY, GENERIC_WRITE};
    use windows::Win32::Storage::FileSystem::{CreateFileW, FILE_FLAGS_AND_ATTRIBUTES, FILE_SHARE_WRITE, OPEN_EXISTING};
    use windows::Win32::System::IO::DeviceIoControl;

    use super::{hub_link_speed, ids_from_hardware_ids, utf16_list, Usb, EX_LEN, V2_LEN};

    /// GUID_DEVINTERFACE_USB_HUB (usbiodef.h).
    const USB_HUB: GUID = GUID::from_u128(0xf18a0e88_c30c_11d0_8815_00a0c906bed8);
    const IOCTL_USB_GET_NODE_CONNECTION_INFORMATION_EX: u32 = 0x220448;
    const IOCTL_USB_GET_NODE_CONNECTION_INFORMATION_EX_V2: u32 = 0x22045C;

    /// What the device tree says about a present device's USB device (its parent when it is an interface, `&MI_`).
    pub(super) unsafe fn usb(instance_id: &str, ids_and_link: bool) -> Usb {
        let mut node = 0u32;
        if CM_Locate_DevNodeW(&mut node, &HSTRING::from(instance_id), CM_LOCATE_DEVNODE_NORMAL) != CR_SUCCESS {
            return Usb::default(); // not present
        }
        if instance_id.contains("&MI_") {
            let mut parent = 0u32;
            if CM_Get_Parent(&mut parent, node, 0) != CR_SUCCESS {
                return Usb::default();
            }
            node = parent;
        }
        let strings = |key| property(node, key, DEVPROP_TYPE_STRING_LIST).map(|b| utf16_list(&b)).unwrap_or_default();
        let location = strings(&DEVPKEY_Device_LocationPaths).into_iter().next();
        if !ids_and_link {
            return Usb { location, ..Usb::default() };
        }
        let Some((vid, pid, bcd_device)) = ids_from_hardware_ids(&strings(&DEVPKEY_Device_HardwareIds)) else {
            return Usb { location, ..Usb::default() };
        };
        let link_speed = hub_reply(node).and_then(|(ex, flags)| hub_link_speed(&ex, flags, vid, pid));
        Usb {
            location,
            vid: Some(vid),
            pid: Some(pid),
            bcd_device: Some(bcd_device),
            link_speed: link_speed.map(str::to_string),
        }
    }

    /// A property of a device node: its bytes, when it has the type `want`.
    unsafe fn property(node: u32, key: &DEVPROPKEY, want: DEVPROPTYPE) -> Option<Vec<u8>> {
        let (mut ty, mut size) = (DEVPROPTYPE(0), 0u32);
        // The first call only reports the size (CR_BUFFER_SMALL).
        let _ = CM_Get_DevNode_PropertyW(node, key, &mut ty, None, &mut size, 0);
        if size == 0 {
            return None;
        }
        let mut buf = vec![0u8; size as usize];
        let ok = CM_Get_DevNode_PropertyW(node, key, &mut ty, Some(buf.as_mut_ptr()), &mut size, 0) == CR_SUCCESS;
        buf.truncate(size as usize);
        (ok && ty == want).then_some(buf)
    }

    /// (EX reply, V2 flags or `None`) for the hub port the USB device `node` is plugged into, as the hub driver
    /// reports it: IOCTL_USB_GET_NODE_CONNECTION_INFORMATION_EX(_V2) on the hub, as USBView and the doctor ask. The
    /// device itself is not opened. `None` when the hub cannot be asked.
    unsafe fn hub_reply(node: u32) -> Option<(Vec<u8>, Option<u32>)> {
        let port: [u8; 4] = property(node, &DEVPKEY_Device_Address, DEVPROP_TYPE_UINT32)?.get(..4)?.try_into().ok()?;
        let mut hub = 0u32;
        let mut hub_id = [0u16; 512];
        if CM_Get_Parent(&mut hub, node, 0) != CR_SUCCESS || CM_Get_Device_IDW(hub, &mut hub_id, 0) != CR_SUCCESS {
            return None;
        }
        let hub_id = PCWSTR(hub_id.as_ptr());
        let mut n = 0u32;
        if CM_Get_Device_Interface_List_SizeW(&mut n, &USB_HUB, hub_id, CM_GET_DEVICE_INTERFACE_LIST_PRESENT)
            != CR_SUCCESS
            || n < 2
        {
            return None;
        }
        let mut paths = vec![0u16; n as usize];
        if CM_Get_Device_Interface_ListW(&USB_HUB, hub_id, &mut paths, CM_GET_DEVICE_INTERFACE_LIST_PRESENT)
            != CR_SUCCESS
        {
            return None;
        }
        // The first path; GENERIC_WRITE with FILE_SHARE_WRITE, as USBView opens a hub.
        let file = CreateFileW(
            PCWSTR(paths.as_ptr()),
            GENERIC_WRITE.0,
            FILE_SHARE_WRITE,
            None,
            OPEN_EXISTING,
            FILE_FLAGS_AND_ATTRIBUTES(0),
            None,
        )
        .ok()?;
        let mut got = 0u32;
        let mut ex = [0u8; 512];
        ex[..4].copy_from_slice(&port);
        let reply = DeviceIoControl(
            file,
            IOCTL_USB_GET_NODE_CONNECTION_INFORMATION_EX,
            Some(ex.as_ptr().cast()),
            ex.len() as u32,
            Some(ex.as_mut_ptr().cast()),
            ex.len() as u32,
            Some(&mut got),
            None,
        )
        .ok()
        .filter(|_| got as usize >= EX_LEN)
        .map(|_| {
            // Asks about USB 1.1, 2.0 and 3.0 (SupportedUsbProtocols 0x7).
            let mut v2 = [0u8; V2_LEN];
            v2[..4].copy_from_slice(&port);
            v2[4..8].copy_from_slice(&(V2_LEN as u32).to_le_bytes());
            v2[8..12].copy_from_slice(&7u32.to_le_bytes());
            let flags = DeviceIoControl(
                file,
                IOCTL_USB_GET_NODE_CONNECTION_INFORMATION_EX_V2,
                Some(v2.as_ptr().cast()),
                V2_LEN as u32,
                Some(v2.as_mut_ptr().cast()),
                V2_LEN as u32,
                Some(&mut got),
                None,
            )
            .ok()
            .map(|_| u32::from_le_bytes([v2[12], v2[13], v2[14], v2[15]]));
            (ex[..EX_LEN].to_vec(), flags)
        });
        let _ = CloseHandle(file);
        reply
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
    usb_device_dir(dir)?.file_name()?.to_str().map(str::to_string)
}

/// The folder `usb_device_above` names.
#[cfg_attr(not(all(feature = "capture", target_os = "linux")), allow(dead_code))] // used on Linux and in tests
fn usb_device_dir(dir: &Path) -> Option<&Path> {
    dir.ancestors().take(4).find(|d| d.join("idVendor").is_file())
}

/// Linux: one entry per V4L2 video capture node, in `/dev/videoN` order — the values Python's
/// `devices.list_devices(backend="v4l2")` and the doctor give. `index` is the N (`DeviceSelector::Index(n)` opens
/// `/dev/videoN`), `name` the node's sysfs name; `usb_location` is its USB device's sysfs name (e.g. `2-1`), and `vid`,
/// `pid`, `bcd_device` and `link_speed` are that device's `idVendor`, `idProduct`, `bcdDevice` and `speed` (Mb/s: 1.5
/// `"Low"`, 12 `"Full"`, 480 `"High"`, 5000 `"Super"`, more than 5000 `"Super+"`); `None` for what cannot be read. The
/// capture node is the one whose sysfs `index` is 0, as Python's `find_v4l2_node` picks it (a UVC camera's metadata
/// node has 1). Reads sysfs only; opens no device. Run without a camera only (made-up sysfs trees); not run with the
/// camera on Linux.
#[cfg(all(feature = "capture", target_os = "linux"))]
pub fn list_v4l2_devices() -> Vec<DeviceInfo> {
    v4l2_devices_in(Path::new("/sys/class/video4linux"))
}

/// `list_v4l2_devices` over the class folder `class` (`/sys/class/video4linux`).
#[cfg_attr(not(all(feature = "capture", target_os = "linux")), allow(dead_code))] // used on Linux and in tests
fn v4l2_devices_in(class: &Path) -> Vec<DeviceInfo> {
    let read = |p: &Path| std::fs::read_to_string(p).ok().map(|s| s.trim().to_string());
    let mut nodes: Vec<(usize, std::path::PathBuf)> = std::fs::read_dir(class)
        .map(|rd| {
            rd.flatten()
                .filter_map(|e| Some((e.file_name().to_str()?.strip_prefix("video")?.parse().ok()?, e.path())))
                .collect()
        })
        .unwrap_or_default();
    nodes.sort();
    nodes
        .into_iter()
        .filter_map(|(n, dir)| {
            let (name, index) = (read(&dir.join("name"))?, read(&dir.join("index"))?); // gone while listed: left out
            (index == "0").then(|| {
                let dev = std::fs::canonicalize(dir.join("device")).ok();
                let usb = dev.as_deref().and_then(usb_device_dir).map_or_else(Usb::default, sysfs_usb);
                DeviceInfo::listed(n, name, usb)
            })
        })
        .collect()
}

/// What sysfs says about a USB device folder: its name (the USB location), `idVendor`, `idProduct`, `bcdDevice` (hex)
/// and `speed` named as the Windows lists name a link — read as the Python doctor reads them.
#[cfg_attr(not(all(feature = "capture", target_os = "linux")), allow(dead_code))] // used on Linux and in tests
fn sysfs_usb(dir: &Path) -> Usb {
    let read = |f: &str| std::fs::read_to_string(dir.join(f)).map(|s| s.trim().to_string()).unwrap_or_default();
    let hex = |f: &str| u16::from_str_radix(&read(f), 16).ok();
    let speed = read("speed");
    let link_speed = match speed.as_str() {
        "1.5" => Some("Low"),
        "12" => Some("Full"),
        "480" => Some("High"),
        "5000" => Some("Super"),
        s if !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit()) && s.parse::<u64>().is_ok_and(|n| n > 5000) => {
            Some("Super+")
        }
        _ => None,
    };
    Usb {
        location: dir.file_name().and_then(|n| n.to_str()).map(str::to_string),
        vid: hex("idVendor"),
        pid: hex("idProduct"),
        bcd_device: hex("bcdDevice"),
        link_speed: link_speed.map(str::to_string),
    }
}

fn ffmpeg_stderr(args: &[&str]) -> std::io::Result<String> {
    let out = Command::new("ffmpeg").args(args).output()?;
    Ok(String::from_utf8_lossy(&out.stderr).into_owned())
}

/// DeviceInfo for each device of an ffmpeg listing, what the device tree says about it read from the DevicePath by
/// `read`.
fn from_listing(text: &str, read: impl Fn(&str) -> Usb) -> Vec<DeviceInfo> {
    parse_video_devices(text)
        .into_iter()
        .enumerate()
        .map(|(index, (name, path))| DeviceInfo::listed(index, name, path.as_deref().map_or_else(Usb::default, &read)))
        .collect()
}

/// DirectShow's video input devices, enumerated directly (no ffmpeg), in the order OpenCV's `CAP_DSHOW` counts
/// device indexes (devices that fail to bind are counted too), each with its USB location, IDs and link speed. Binds
/// to no device (read only). Empty list off Windows. The USB location: not read with the camera, and not with two
/// cameras. The IDs and the link speed: not read with the camera.
pub fn list_dshow_devices() -> Result<Vec<DeviceInfo>> {
    list_dshow_devices_with(true)
}

/// `list_dshow_devices`; without `ids_and_link` only the USB location is read, as in 0.0.6 — no hardware IDs and no
/// hub query: all that finding a camera to open needs (this crate's controls, the Python package's open path). For
/// the Python extension; not part of the documented API.
#[doc(hidden)]
pub fn list_dshow_devices_with(ids_and_link: bool) -> Result<Vec<DeviceInfo>> {
    Ok(crate::dshow::enumerate_video_device_paths()?
        .into_iter()
        .enumerate()
        .map(|(index, (name, path))| {
            let usb = path.as_deref().map_or_else(Usb::default, |p| usb_of_device_path(p, ids_and_link));
            DeviceInfo::listed(index, name.unwrap_or_default(), usb)
        })
        .collect())
}

/// Video devices in DirectShow enumeration order, each with its USB location, IDs and link speed. Laptop webcams are
/// included — the user has to pick among them. Without `raw` this runs ffmpeg; without ffmpeg (most customer PCs) it enumerates
/// DirectShow directly (`list_dshow_devices`; a failure there is an empty list — it shows as "not found").
pub fn list_devices(raw: Option<&str>) -> Vec<DeviceInfo> {
    list_devices_with(raw, true)
}

/// `list_devices`; without `ids_and_link` only the USB location is read (as `list_dshow_devices_with`). For the
/// Python extension; not part of the documented API.
#[doc(hidden)]
pub fn list_devices_with(raw: Option<&str>, ids_and_link: bool) -> Vec<DeviceInfo> {
    let text = match raw {
        Some(r) => r.to_string(),
        None => match ffmpeg_stderr(&["-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"]) {
            Ok(r) => r,
            Err(_) => return list_dshow_devices_with(ids_and_link).unwrap_or_default(),
        },
    };
    from_listing(&text, |p| usb_of_device_path(p, ids_and_link))
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
/// Picking a camera by USB location has been run with the camera once (one PC, one camera, 2026-11-25): with one camera
/// connected, the list showed its USB location, and given that location it opened at 1600x800 MJPG and read one frame;
/// a location no device has was refused (Python DirectShow, Rust Media Foundation); not run with two cameras.
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
    Ok(list_mf_devices_with(false)?.into_iter().map(|d| d.name).collect())
}

/// The same Media Foundation list as `enumerate_mf_video_devices`, each entry with its index, USB location, IDs and
/// link speed (from the source's symbolic link). An index here is the one `DeviceSelector::Index` takes. Activates no
/// device (read only). Empty list on non-Windows. The USB location: not read with the camera, and not with two
/// cameras. The IDs and the link speed: not read with the camera.
pub fn list_mf_devices() -> Result<Vec<DeviceInfo>> {
    list_mf_devices_with(true)
}

/// `list_mf_devices`; without `ids_and_link` only the USB location is read (as `list_dshow_devices_with`). For the
/// Python extension; not part of the documented API.
#[doc(hidden)]
pub fn list_mf_devices_with(ids_and_link: bool) -> Result<Vec<DeviceInfo>> {
    #[cfg(windows)]
    {
        let _com = crate::dshow::com::ComInit::mta()?;
        // The activation objects are released at the end of this statement, before CoUninitialize (`_com`),
        // for the same reason as in the DirectShow enumeration.
        let list = unsafe { mf_enum::sources(ids_and_link)? }.into_iter().map(|(_, d)| d).collect();
        Ok(list)
    }
    #[cfg(not(windows))]
    {
        let _ = ids_and_link;
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

    /// Every video capture source with its entry of the list: index, friendly name ("" when it has none) and what
    /// the device tree says about it (from its symbolic link; without `ids_and_link` the USB location only), in
    /// `MFEnumDeviceSources` order. COM must be initialized on this thread. Activates nothing.
    pub(crate) unsafe fn sources(ids_and_link: bool) -> Result<Vec<(IMFActivate, super::DeviceInfo)>> {
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
            .enumerate()
            .map(|(index, a)| {
                let n = string(&a, &MF_DEVSOURCE_ATTRIBUTE_FRIENDLY_NAME).unwrap_or_default();
                let link = string(&a, &MF_DEVSOURCE_ATTRIBUTE_SOURCE_TYPE_VIDCAP_SYMBOLIC_LINK).ok();
                let usb = link.as_deref().map_or_else(super::Usb::default, |l| super::usb_of_device_path(l, ids_and_link));
                (a, super::DeviceInfo::listed(index, n, usb))
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
        DeviceInfo::new(index, name, None)
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
        DeviceInfo::new(index, name, usb_location.map(str::to_string))
    }

    /// The same made-up device tree, read for the USB location only.
    fn fake_tree(path: &str) -> Usb {
        Usb { location: fake_locate(path), ..Usb::default() }
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
            from_listing(TWO_WEEVIA, fake_tree),
            [
                at(0, "WN Dual Camera", Some(LOC1)),
                at(1, "Example Webcam", None),
                at(2, "WN Dual Camera", Some(LOC2))
            ]
        );
    }

    #[test]
    fn picking_by_usb_location_tells_two_cameras_of_one_name_apart() {
        let list = from_listing(TWO_WEEVIA, fake_tree);
        assert_eq!(pick_by_usb_location("wn dual", LOC2, &list).unwrap(), at(2, "WN Dual Camera", Some(LOC2)));
        // the location is compared ignoring case
        assert_eq!(pick_by_usb_location("WN Dual Camera", &LOC1.to_lowercase(), &list).unwrap().index, 0);
    }

    #[test]
    fn two_cameras_of_one_name_point_to_their_usb_locations() {
        match pick_by_name("wn dual", &from_listing(TWO_WEEVIA, fake_tree)) {
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
        let list = from_listing(TWO_WEEVIA, fake_tree);
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

    // ---- Linux list: made-up sysfs trees only — nothing is enumerated ------------------------------------------

    /// Node names, USB location and values as the camera showed them on Linux (2026-11-25, one mini PC, one camera,
    /// not opened): /dev/video0 (index 0) and /dev/video1 (index 1, the metadata node) on the USB device 2-1.
    const GADGET: &str = "Webcam gadget: WN Dual Camera";
    const WEEVIA_USB: [(&str, &str); 4] =
        [("idVendor", "1d6b"), ("idProduct", "0200"), ("bcdDevice", "0409"), ("speed", "5000")];

    /// `dir/name/link` -> `dir/target`: a symbolic link on Linux, a junction on Windows (no privilege needed).
    fn link(target: &Path, link: &Path) {
        #[cfg(unix)]
        std::os::unix::fs::symlink(target, link).unwrap();
        #[cfg(windows)]
        {
            let made = Command::new("cmd").args(["/C", "mklink", "/J"]).arg(link).arg(target).output().unwrap();
            assert!(made.status.success(), "{made:?}");
        }
    }

    /// sysfs as Linux lays it out, in a new folder: `usb` = (USB device, its files), `nodes` = (videoN, name, index,
    /// USB device); videoN/device -> the USB device's interface folder (2-1:1.0 on Linux; ':' is not allowed in a
    /// Windows file name, so 2-1_1.0 here). Returns (the folder, its class folder /sys/class/video4linux).
    type Node<'a> = (&'a str, &'a str, &'a str, Option<&'a str>);

    fn sysfs_tree(
        tag: &str,
        usb: &[(&str, &[(&str, &str)])],
        nodes: &[Node],
    ) -> (std::path::PathBuf, std::path::PathBuf) {
        let root = std::env::temp_dir().join(format!("ov_v4l2_{tag}_{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        for (dev, files) in usb {
            std::fs::create_dir_all(root.join("devices").join(dev).join(format!("{dev}_1.0"))).unwrap();
            for (f, v) in *files {
                std::fs::write(root.join("devices").join(dev).join(f), format!("{v}\n")).unwrap();
            }
        }
        let class = root.join("class");
        for (node, name, index, dev) in nodes {
            std::fs::create_dir_all(class.join(node)).unwrap();
            std::fs::write(class.join(node).join("name"), format!("{name}\n")).unwrap();
            std::fs::write(class.join(node).join("index"), format!("{index}\n")).unwrap();
            if let Some(dev) = dev {
                link(&root.join("devices").join(dev).join(format!("{dev}_1.0")), &class.join(node).join("device"));
            }
        }
        (root, class)
    }

    fn listed(index: usize, name: &str, location: &str, ids: (u16, u16, u16), link: Option<&str>) -> DeviceInfo {
        let mut d = at(index, name, Some(location));
        (d.vid, d.pid, d.bcd_device, d.link_speed) = (Some(ids.0), Some(ids.1), Some(ids.2), link.map(str::to_string));
        d
    }

    #[test]
    fn the_v4l2_list_has_the_capture_node_of_each_camera_with_its_usb_ids_and_link_speed() {
        let usb_2 = [("idVendor", "1d6b"), ("idProduct", "0200"), ("bcdDevice", "0409"), ("speed", "480")];
        let (root, class) = sysfs_tree(
            "list",
            &[("2-1", &WEEVIA_USB), ("1-4.2", &usb_2), ("1-5", &[("idVendor", "636e")])],
            &[
                ("video0", GADGET, "0", Some("2-1")),
                ("video1", GADGET, "1", Some("2-1")),
                ("video10", GADGET, "0", Some("1-4.2")),
                ("video11", GADGET, "1", Some("1-4.2")),
                ("video2", "Laptop webcam", "0", Some("1-5")),
                ("video4", "Dummy video device", "0", None),
            ],
        );
        std::fs::create_dir_all(class.join("video5")).unwrap(); // a node that went away while it was listed
        let mut webcam = at(2, "Laptop webcam", Some("1-5"));
        webcam.vid = Some(0x636E); // the rest could not be read
        assert_eq!(
            v4l2_devices_in(&class),
            [
                listed(0, GADGET, "2-1", (0x1D6B, 0x0200, 0x0409), Some("Super")),
                webcam,
                at(4, "Dummy video device", None),
                listed(10, GADGET, "1-4.2", (0x1D6B, 0x0200, 0x0409), Some("High")),
            ]
        );
        assert!(v4l2_devices_in(&root.join("absent")).is_empty());
        std::fs::remove_dir_all(&root).unwrap();
    }

    /// sysfs `speed` (Mb/s) gets the words of the Windows lists — the Python doctor's table.
    #[test]
    fn the_v4l2_link_speed_has_the_words_of_the_windows_lists() {
        let root = std::env::temp_dir().join(format!("ov_v4l2_speed_{}", std::process::id()));
        std::fs::create_dir_all(&root).unwrap();
        for (speed, want) in [
            ("1.5", Some("Low")),
            ("12", Some("Full")),
            ("480", Some("High")),
            ("5000", Some("Super")),
            ("10000", Some("Super+")),
            ("20000", Some("Super+")),
            ("53.3", None),
            ("", None),
        ] {
            std::fs::write(root.join("speed"), format!("{speed}\n")).unwrap();
            assert_eq!(sysfs_usb(&root).link_speed.as_deref(), want, "speed {speed:?}");
        }
        std::fs::remove_file(root.join("speed")).unwrap();
        assert_eq!(sysfs_usb(&root).link_speed, None);
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

    // ---- USB IDs and link speed: made-up hardware IDs and hub replies only — nothing is enumerated --------------

    fn hw(ids: &[&str]) -> Vec<String> {
        ids.iter().map(|s| s.to_string()).collect()
    }

    /// The hardware IDs of the doctor's tests (a Weevia S1's composite USB device, a laptop webcam).
    #[test]
    fn the_usb_ids_are_read_from_the_hardware_ids() {
        let weevia = hw(&[r"USB\VID_1D6B&PID_0200&REV_0409&MI_00", r"USB\VID_1D6B&PID_0200&REV_0409"]);
        assert_eq!(ids_from_hardware_ids(&weevia), Some((0x1D6B, 0x0200, 0x0409)));
        assert_eq!(ids_from_hardware_ids(&hw(&[r"usb\vid_636e&pid_0bda&rev_0007"])), Some((0x636E, 0x0BDA, 0x0007)));
        // an ID with all three is found when an earlier one has no revision
        let later = hw(&[r"USB\VID_1D6B&PID_0200", r"USB\VID_1D6B&PID_0200&REV_0409"]);
        assert_eq!(ids_from_hardware_ids(&later), Some((0x1D6B, 0x0200, 0x0409)));
        // no revision, not hex, too short, nothing: not read
        assert_eq!(ids_from_hardware_ids(&hw(&[r"USB\VID_1D6B&PID_0200"])), None);
        assert_eq!(ids_from_hardware_ids(&hw(&[r"USB\VID_1D6B&PID_02G0&REV_0409"])), None);
        assert_eq!(ids_from_hardware_ids(&hw(&[r"USB\VID_1D6B&PID_0200&REV_04"])), None);
        assert_eq!(ids_from_hardware_ids(&[]), None);
    }

    #[test]
    fn a_string_list_property_is_split_at_its_nuls() {
        let bytes: Vec<u8> = "A\0BC\0\0".encode_utf16().flat_map(u16::to_le_bytes).collect();
        assert_eq!(utf16_list(&bytes), ["A", "BC"]);
        assert!(utf16_list(&[]).is_empty());
    }

    /// A USB_NODE_CONNECTION_INFORMATION_EX reply laid out as the doctor's tests lay it out (`_ex_reply`).
    fn ex_reply(vid: u16, pid: u16, speed: u8, status: u32) -> Vec<u8> {
        let mut b = vec![0u8; 64];
        b[0..4].copy_from_slice(&2u32.to_le_bytes());
        (b[4], b[5]) = (18, 1);
        b[6..8].copy_from_slice(&0x0320u16.to_le_bytes());
        b[12..14].copy_from_slice(&vid.to_le_bytes());
        b[14..16].copy_from_slice(&pid.to_le_bytes());
        b[16..18].copy_from_slice(&0x0409u16.to_le_bytes());
        (b[22], b[23], b[24]) = (1, speed, 0);
        b[25..27].copy_from_slice(&7u16.to_le_bytes());
        b[27..31].copy_from_slice(&3u32.to_le_bytes());
        b[31..35].copy_from_slice(&status.to_le_bytes());
        b
    }

    /// The doctor's `test_link_name` table: the same speeds and flags give the same names (its "unknown" is `None`).
    #[test]
    fn link_speed_names_are_the_doctors() {
        for (speed, flags, want) in [
            (0, None, Some("Low")),
            (1, None, Some("Full")),
            (2, None, Some("High")),
            (3, None, Some("Super")),
            (2, Some(0b0000), Some("High")),
            (2, Some(0b0010), Some("High")),
            (2, Some(0b0011), Some("Super")),
            (3, Some(0b1111), Some("Super+")),
            (9, None, None),
        ] {
            assert_eq!(link_name(speed, flags), want, "speed {speed}, flags {flags:?}");
        }
    }

    #[test]
    fn a_hub_reply_for_another_device_or_an_empty_port_is_not_used() {
        let speed = |vid, status, flags| hub_link_speed(&ex_reply(vid, 0x0200, 2, status), flags, 0x1D6B, 0x0200);
        assert_eq!(speed(0x1D6B, 1, Some(0b10)), Some("High"));
        assert_eq!(speed(0x1D6B, 1, Some(0b11)), Some("Super")); // the V2 flags win over the EX speed
        assert_eq!(speed(0x05E3, 1, Some(0b10)), None); // the port holds another device
        assert_eq!(speed(0x1D6B, 0, Some(0b10)), None); // no device connected
        let short = &ex_reply(0x1D6B, 0x0200, 2, 1)[..EX_LEN - 1];
        assert_eq!(hub_link_speed(short, None, 0x1D6B, 0x0200), None);
    }

    #[test]
    fn a_new_entry_has_no_usb_ids_or_link_speed() {
        let d = DeviceInfo::new(0, "WN Dual Camera", Some(LOC1.into()));
        assert_eq!((d.vid, d.pid, d.bcd_device, d.link_speed), (None, None, None, None));
    }

    #[test]
    fn each_listed_device_carries_the_usb_ids_and_link_speed_read_with_it() {
        // a made-up device tree: the first Weevia S1 read in full, the second's hub could not be asked
        let tree = |path: &str| {
            let full = |link: Option<&str>| Usb {
                location: fake_locate(path),
                vid: Some(0x1D6B),
                pid: Some(0x0200),
                bcd_device: Some(0x0409),
                link_speed: link.map(str::to_string),
            };
            match fake_locate(path).as_deref() {
                Some(LOC1) => full(Some("Super")),
                Some(LOC2) => full(None),
                _ => Usb::default(),
            }
        };
        let with = |mut d: DeviceInfo, link: Option<&str>| {
            (d.vid, d.pid, d.bcd_device, d.link_speed) = (Some(0x1D6B), Some(0x0200), Some(0x0409), link.map(str::to_string));
            d
        };
        let list = from_listing(TWO_WEEVIA, tree);
        assert_eq!(
            list,
            [
                with(at(0, "WN Dual Camera", Some(LOC1)), Some("Super")),
                at(1, "Example Webcam", None),
                with(at(2, "WN Dual Camera", Some(LOC2)), None)
            ]
        );
        // picking hands back the whole entry
        assert_eq!(pick_by_usb_location("wn dual", LOC1, &list).unwrap(), list[0]);
        assert_eq!(pick_by_name("webcam", &list).unwrap(), list[1]);
    }
}
