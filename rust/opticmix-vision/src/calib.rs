//! The calibration file installed for your camera, read by the same rules as the Python package's
//! `opticmix_vision.calib` (`store_dir`, `installed`, `resolve`) and `Device.calibration`.
//!
//! The camera reports no USB serial number, so this crate cannot tell which unit is connected. You install the
//! file for your camera with the Python package (`python -m opticmix_vision.calib add <file.json>`; this crate
//! reads installed files and does not install them), and with exactly one file installed, that file is used.
//! Another unit's file loads without an error: it is an installed file, not matched to this camera (no USB
//! serial). `python -m opticmix_vision.calib check` measures whether a file fits the camera. To read a file
//! without Python: `calib::load(path)`.
//!
//! ```no_run
//! use opticmix_vision::calib;
//!
//! let c = calib::resolve()?; // the one installed file; Device::calibration() also checks the eye size
//! println!("K {:?} D {:?}", c.left.k, c.left.d);
//! println!("R {:?} t {:?} mm, baseline {:.2} mm", c.extrinsics.r, c.extrinsics.t, c.baseline_mm());
//! # Ok::<(), opticmix_vision::SdkError>(())
//! ```
//!
//! What a file holds (schema version 1, JSON): for each eye the model (`"KB4"`, Kannala-Brandt 4th order, as
//! OpenCV's `cv::fisheye`), `k` (3×3, pixels), `d` (k1..k4) and `size` (eye width, height); from the left eye to
//! the right `extrinsics.r` and `extrinsics.t` (mm) with p_right = R·p_left + t, and `baseline_mm()` = |t|; the
//! file's `device` block (`serial()`) and its free-form `meta` (a `serde_json::Value`; the crate re-exports
//! `serde_json`). `left` and `right` are the file's blocks as written. A file that breaks the schema or the
//! geometry checks is refused with the Python package's message.
//!
//! Points: `Intrinsics::project` (a point in the eye's camera coordinates → its pixel) and
//! `Intrinsics::unproject` (a pixel → the unit ray), by the KB4 model with the file's K and D, up to 92.5° from the
//! optical axis. The values follow OpenCV's `cv::fisheye` convention. Not in this crate: rectification, depth.
//!
//! ```no_run
//! # let c = opticmix_vision::calib::resolve()?;
//! let px = c.left.project([100.0, -20.0, 400.0]); // Some([u, v]): mm, or any unit (only the direction counts)
//! let ray = c.left.unproject([c.left.cx(), c.left.cy()]); // Some([0.0, 0.0, 1.0]): the optical axis
//! # Ok::<(), opticmix_vision::SdkError>(())
//! ```
//!
//! Run without a camera only (made-up calibration files); not run with the camera.

use std::path::{Path, PathBuf};

mod kb4;
mod schema;
pub use schema::{
    Extrinsics, Intrinsics, StereoCalibration, CONVENTION, DEFAULT_MAX_THETA_DEG, SCHEMA_NAME,
    SCHEMA_VERSION,
};

use crate::error::{Result, SdkError};

/// A file that breaks the schema or the geometry checks (Python `CalibrationError`).
fn cal(m: impl Into<String>) -> SdkError {
    SdkError::Calibration(m.into())
}

/// No usable calibration; the message says what to do (Python `CalibLookupError`).
fn lookup(m: impl Into<String>) -> SdkError {
    SdkError::CalibLookup(m.into())
}

/// The folder for installed files (Python `calib.store_dir()`): `%APPDATA%\opticmix\calib` on Windows;
/// `$XDG_CONFIG_HOME/opticmix/calib`, or `$HOME/.config/opticmix/calib`, elsewhere.
pub fn store_dir() -> Result<PathBuf> {
    let var = |name: &str| {
        std::env::var_os(name).ok_or_else(|| {
            lookup(format!(
                "{name} is not set, so the calibration folder cannot be found"
            ))
        })
    };
    let base = if cfg!(windows) {
        PathBuf::from(var("APPDATA")?)
    } else {
        match std::env::var_os("XDG_CONFIG_HOME").filter(|v| !v.is_empty()) {
            Some(x) => PathBuf::from(x),
            None => PathBuf::from(var("HOME")?).join(".config"),
        }
    };
    Ok(base.join("opticmix").join("calib"))
}

/// `S1-NNNN` when `name` is an installed file's name `S1-NNNN.json` (Python's glob `S1-[0-9][0-9][0-9][0-9].json`,
/// which ignores case on Windows).
fn serial_of(name: &str) -> Option<&str> {
    let same = |a: &str, b: &str| {
        if cfg!(windows) {
            a.eq_ignore_ascii_case(b)
        } else {
            a == b
        }
    };
    let (head, digits, ext) = (name.get(..3)?, name.get(3..7)?, name.get(7..)?);
    (same(head, "S1-") && digits.bytes().all(|b| b.is_ascii_digit()) && same(ext, ".json"))
        .then(|| &name[..7])
}

fn installed_in(dir: &Path) -> Result<Vec<String>> {
    let entries = match std::fs::read_dir(dir) {
        Ok(rd) => rd,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(e) => return Err(lookup(format!("{}: {e}", dir.display()))),
    };
    let mut have: Vec<String> = entries
        .flatten()
        .filter_map(|e| serial_of(&e.file_name().to_string_lossy()).map(String::from))
        .collect();
    have.sort();
    Ok(have)
}

/// Serials of the installed files, sorted (Python `calib.installed()`).
pub fn installed() -> Result<Vec<String>> {
    installed_in(&store_dir()?)
}

fn installed_file_in(dir: &Path) -> Result<PathBuf> {
    let have = installed_in(dir)?;
    match have.as_slice() {
        [] => Err(lookup(
            "No calibration installed. Install the file for your camera: python -m opticmix_vision.calib add <file.json>",
        )),
        [one] => Ok(dir.join(format!("{one}.json"))),
        _ => Err(lookup(format!(
            "{} calibrations installed ({}). Keep only the file for the camera you use: delete the others from {}. \
             To see which one fits the connected camera: python -m opticmix_vision.calib check --all",
            have.len(),
            have.join(", "),
            dir.display()
        ))),
    }
}

/// The path of the file `resolve()` reads: the one installed file. With none or several installed, the error
/// (`SdkError::CalibLookup`) says what to do — the same messages as Python's `calib.resolve()`.
pub fn installed_file() -> Result<PathBuf> {
    installed_file_in(&store_dir()?)
}

/// The installed calibration when exactly one file is installed (Python `calib.resolve()`). Errors:
/// `SdkError::CalibLookup` for none or several (the message says what to do), `SdkError::Calibration` for a file
/// that breaks the schema or the checks. It does not check that the file is this camera's (no USB serial).
pub fn resolve() -> Result<StereoCalibration> {
    load(&installed_file()?)
}

/// Reads and checks one calibration file, installed or not (Python `calibration.load_calibration`; UTF-8).
pub fn load(path: &Path) -> Result<StereoCalibration> {
    let text =
        std::fs::read_to_string(path).map_err(|e| cal(format!("{}: {e}", path.display())))?;
    schema::parse(&text, false)
}

impl StereoCalibration {
    /// The `device.serial` entry of the file (`S1-NNNN` for an installed file).
    pub fn serial(&self) -> Option<&str> {
        self.device
            .iter()
            .find(|(k, _)| k == "serial")
            .map(|(_, v)| v.as_str())
    }

    /// The check `Device::calibration()` makes, for frames you capture another way: the file's eye size must be half
    /// the frame width × the frame height (Python `Device.calibration`). Otherwise `SdkError::CalibLookup` names the
    /// mode to open.
    pub fn check_eye_size(&self, frame_width: u32, frame_height: u32) -> Result<()> {
        let (w, h) = self.left.size;
        if (i64::from(frame_width / 2), i64::from(frame_height)) != (w, h) {
            return Err(lookup(format!(
                "This calibration is for {w}x{h} eyes. Open the {}x{h} mode.",
                2 * w
            )));
        }
        Ok(())
    }
}

impl Intrinsics {
    /// The pixel (u, v) of a point given in this eye's camera coordinates (X right, Y down, Z forward; mm or any unit,
    /// only the direction counts), by the KB4 model with `k` and `d` (OpenCV's `cv::fisheye`, the skew `k[0][1]`
    /// included). `None` for a point more than `DEFAULT_MAX_THETA_DEG` (92.5°) from the optical axis, at the origin,
    /// or not finite. The pixel is not checked against `size`. A point behind the eye (Z < 0) within 92.5° of the axis
    /// gets its own pixel, where `cv::fisheye::projectPoints`, which divides by Z, gives the pixel of -p. A point in
    /// the left eye's coordinates is `R·p + t` in the right eye's (`Extrinsics`, mm).
    pub fn project(&self, p: [f64; 3]) -> Option<[f64; 2]> {
        kb4::project_kb4(p, &self.k, &self.d)
    }

    /// The unit ray, in this eye's camera coordinates as `project` takes them, that projects to the pixel (u, v):
    /// the inverse of `project`, the angle from the optical axis solved by Newton's method (at most 20 steps).
    /// `cv::fisheye::undistortPoints` gives x/z, y/z of this ray. `None` when the solve fails, for a pixel more than
    /// 92.5° from the axis (`DEFAULT_MAX_THETA_DEG`), or for one not finite. A pixel beyond 90° gives z < 0.
    pub fn unproject(&self, uv: [f64; 2]) -> Option<[f64; 3]> {
        let (rays, ok) = kb4::unproject_kb4(&[uv], &self.k, &self.d);
        ok[0].then_some(rays[0])
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A fresh folder under the temporary folder, with a non-ASCII name.
    fn tmp(tag: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("ov_calib_보정 é_{tag}_{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    /// Left and right differ in every value, so a swap shows. Numbers chosen to need exact parsing.
    const DISTINCT: &str = r#"{
  "schema": "opticmix_vision.stereo_calibration", "schema_version": 1, "units": "mm",
  "device": {"model": "Weevia S1", "serial": "S1-0042"},
  "left": {"model": "KB4", "K": [[370.50000000000006, 0, 401.2], [0, 370.1, 398.7], [0, 0, 1]],
           "D": [-0.2577, 0.0437, -0.001, 2.2250738585072014e-308], "size": [800, 800]},
  "right": {"model": "KB4", "K": [[368.9, 0, 389.4], [0, 368.6, 402.3], [0, 0, 1]],
            "D": [-0.2551, 0.0421, 0.30000000000000004, 0.0], "size": [800, 800]},
  "extrinsics": {"R": [[1, 0, 0], [0, 1, 0], [0, 0, 1]], "t": [-66.56, 0.31, -0.42],
                 "convention": "p_right = R @ p_left + t"},
  "meta": {"tool": "made up — test", "rms_stereo_px": 0.25}
}"#;

    fn f(s: &str) -> f64 {
        s.parse().unwrap() // correctly rounded, like Python float()
    }

    #[test]
    fn installed_files_are_the_s1_nnnn_json_ones_sorted() {
        let d = tmp("pattern");
        for name in [
            "S1-0057.json",
            "S1-0042.json",
            "S1-42.json",
            "S1-00421.json",
            "S1-0042.json.bak",
            "notes.txt",
            "S2-0042.json",
        ] {
            std::fs::write(d.join(name), "{}").unwrap();
        }
        assert_eq!(installed_in(&d).unwrap(), ["S1-0042", "S1-0057"]);
        assert_eq!(
            installed_in(&d.join("missing")).unwrap(),
            Vec::<String>::new()
        );
        assert_eq!(serial_of("S1-0042.json"), Some("S1-0042"));
        assert_eq!(serial_of("S1-004a.json"), None);
        assert_eq!(serial_of("é1-0042.json"), None); // not char boundaries: no panic
        assert_eq!(serial_of("S1é042.json"), None);
        assert_eq!(serial_of("S1-004é.json"), None);
        // Python's glob ignores case on Windows only
        if cfg!(windows) {
            assert_eq!(serial_of("s1-0042.JSON"), Some("s1-0042"));
        } else {
            assert_eq!(serial_of("s1-0042.JSON"), None);
        }
    }

    #[test]
    fn exactly_one_file_is_used_and_none_or_several_say_what_to_do() {
        let d = tmp("one");
        assert_eq!(
            installed_file_in(&d).unwrap_err(),
            SdkError::CalibLookup(
                "No calibration installed. Install the file for your camera: python -m opticmix_vision.calib add <file.json>".into()
            )
        );
        std::fs::write(d.join("S1-0042.json"), DISTINCT).unwrap();
        assert_eq!(installed_file_in(&d).unwrap(), d.join("S1-0042.json"));
        std::fs::write(d.join("S1-0057.json"), DISTINCT).unwrap();
        let e = installed_file_in(&d).unwrap_err();
        assert_eq!(e.python_type(), "CalibLookupError");
        assert_eq!(
            e.to_string(),
            format!(
                "2 calibrations installed (S1-0042, S1-0057). Keep only the file for the camera you use: delete the others \
                 from {}. To see which one fits the connected camera: python -m opticmix_vision.calib check --all",
                d.display()
            )
        );
    }

    #[test]
    fn every_number_is_read_exactly_and_left_and_right_are_not_swapped() {
        let d = tmp("values");
        let p = d.join("S1-0042.json");
        std::fs::write(&p, DISTINCT).unwrap();
        let c = load(&p).unwrap();
        assert_eq!(c.left.k[0], [f("370.50000000000006"), 0.0, f("401.2")]);
        assert_eq!(c.left.k[1], [0.0, f("370.1"), f("398.7")]);
        assert_eq!(c.left.k[2], [0.0, 0.0, 1.0]);
        assert_eq!(
            c.left.d,
            [
                f("-0.2577"),
                f("0.0437"),
                f("-0.001"),
                f("2.2250738585072014e-308")
            ]
        );
        assert_eq!(c.right.k[0], [f("368.9"), 0.0, f("389.4")]);
        assert_eq!(
            c.right.d,
            [f("-0.2551"), f("0.0421"), f("0.30000000000000004"), 0.0]
        );
        assert_eq!((c.left.size, c.left.model.as_str()), ((800, 800), "KB4"));
        assert_eq!(
            (c.left.fx(), c.left.fy(), c.left.cx(), c.left.cy()),
            (f("370.50000000000006"), f("370.1"), f("401.2"), f("398.7"))
        );
        assert_eq!(c.extrinsics.t, [f("-66.56"), f("0.31"), f("-0.42")]);
        assert_eq!(
            c.extrinsics.r,
            [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
        );
        let b = (f("-66.56").powi(2) + f("0.31").powi(2) + f("-0.42").powi(2)).sqrt();
        assert_eq!(c.baseline_mm(), b);
        assert_eq!(c.serial(), Some("S1-0042"));
        assert_eq!(
            c.device,
            [
                ("model".into(), "Weevia S1".into()),
                ("serial".into(), "S1-0042".into())
            ]
        );
        assert_eq!(c.meta["tool"], "made up — test");
        assert_eq!(c.units, "mm");
    }

    #[test]
    fn a_broken_file_gives_the_python_message() {
        let d = tmp("broken");
        let p = d.join("S1-0042.json");
        let cases = [
            ("{", "cannot read JSON: EOF while parsing an object at line 1 column 1"),
            (
                &DISTINCT.replace("\"units\": \"mm\",", "\"units\": \"mm\", \"baseline\": 64,"),
                "calibration: unknown key ['baseline'] (allowed: ['schema', 'schema_version', 'units', 'device', 'left', \
                 'right', 'extrinsics', 'meta'])",
            ),
            (
                &DISTINCT.replace("-0.2577, 0.0437, -0.001, 2.2250738585072014e-308", "-0.5, 0, 0, 0"),
                "CameraIntrinsics: KB4 coefficients fold inside the field of view (not monotonic): at about 46.84° \
                 (checked up to 92.5°)",
            ),
            (
                &DISTINCT.replace("0.25}", "NaN}"),
                "non-standard number in JSON: 'NaN' (NaN/Infinity are not allowed)",
            ),
        ];
        for (text, want) in cases {
            std::fs::write(&p, text).unwrap();
            let e = load(&p).unwrap_err();
            assert_eq!(e, SdkError::Calibration(want.into()), "{text}");
            assert_eq!(e.python_type(), "CalibrationError");
        }
        let missing = d.join("missing.json");
        assert!(
            matches!(load(&missing), Err(SdkError::Calibration(m)) if m.starts_with(&missing.display().to_string()))
        );
    }

    #[test]
    fn constructors_refuse_short_slices_instead_of_panicking() {
        let k = [400.0, 0.0, 399.5, 0.0, 400.0, 399.5, 0.0, 0.0, 1.0];
        Intrinsics::new(&[3, 3], &k, &[0.0; 4], (800, 800), "KB4").unwrap();
        assert_eq!(
            Intrinsics::new(&[3, 3], &k[..8], &[0.0; 4], (800, 800), "KB4").unwrap_err(),
            SdkError::Calibration("K must have 9 values (3x3), got 8".into())
        );
        let eye = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0];
        Extrinsics::new(&[3, 3], &eye, &[-64.0, 0.0, 0.0]).unwrap();
        assert_eq!(
            Extrinsics::new(&[3, 3], &eye[..3], &[-64.0, 0.0, 0.0]).unwrap_err(),
            SdkError::Calibration("R must have 9 values (3x3), got 3".into())
        );
        // the shape and D / t length checks keep the Python messages
        assert_eq!(
            Intrinsics::new(&[3, 3], &k, &[0.0; 3], (800, 800), "KB4").unwrap_err(),
            SdkError::Calibration("D must have the 4 KB4 coefficients (k1..k4): (3,)".into())
        );
        assert_eq!(
            Extrinsics::new(&[3, 3], &eye, &[1.0, 0.0]).unwrap_err(),
            SdkError::Calibration("t must be (3,): (2,)".into())
        );
    }

    /// One eye with distortion (made up) and, when `skew` is not 0, a skew term in K.
    fn kb4_eye(skew: f64) -> Intrinsics {
        let k = [370.5, skew, 401.2, 0.0, 370.1, 398.7, 0.0, 0.0, 1.0];
        Intrinsics::new(
            &[3, 3],
            &k,
            &[-0.2577, 0.0437, -0.001, 0.0002],
            (800, 800),
            "KB4",
        )
        .unwrap()
    }

    /// The unit direction `theta_deg` from the optical axis, turned `phi_deg` about it from +X.
    fn dir(theta_deg: f64, phi_deg: f64) -> [f64; 3] {
        let (t, p) = (theta_deg.to_radians(), phi_deg.to_radians());
        [t.sin() * p.cos(), t.sin() * p.sin(), t.cos()]
    }

    fn near<const N: usize>(a: [f64; N], b: [f64; N], tol: f64) -> bool {
        a.iter().zip(&b).all(|(x, y)| (x - y).abs() <= tol)
    }

    #[test]
    fn the_projection_is_the_kb4_formula() {
        // θd = θ(1 + k1θ² + k2θ⁴ + k3θ⁶ + k4θ⁸); (x, y) = θd·(cos φ, sin φ); u = fx·x + skew·y + cx, v = fy·y + cy
        let e = kb4_eye(0.8);
        let [k1, k2, k3, k4] = e.d;
        let (t, phi) = (40f64.to_radians(), 30f64.to_radians());
        let td = t * (1.0 + k1 * t.powi(2) + k2 * t.powi(4) + k3 * t.powi(6) + k4 * t.powi(8));
        let (x, y) = (td * phi.cos(), td * phi.sin());
        let want = [370.5 * x + 0.8 * y + 401.2, 370.1 * y + 398.7];
        let got = e.project(dir(40.0, 30.0).map(|v| v * 612.0)).unwrap();
        assert!(near(got, want, 1e-9), "{got:?} vs {want:?}");
        // no distortion (equidistant): the distance from the principal point is f·θ
        let f = 400.0 / 90f64.to_radians();
        let eq = Intrinsics::new(
            &[3, 3],
            &[f, 0.0, 399.5, 0.0, f, 399.5, 0.0, 0.0, 1.0],
            &[0.0; 4],
            (800, 800),
            "KB4",
        )
        .unwrap();
        assert!(near(
            eq.project(dir(45.0, 0.0)).unwrap(),
            [599.5, 399.5],
            1e-9
        ));
        assert!(near(
            eq.project(dir(90.0, 90.0)).unwrap(),
            [399.5, 799.5],
            1e-9
        ));
        assert!(near(
            eq.unproject([399.5, 199.5]).unwrap(),
            dir(45.0, 270.0),
            1e-12
        ));
    }

    /// OpenCV's answers for `kb4_eye(0.0)` (its K and D), printed with opencv-python 4.13.0 and numpy 2.4.4 on
    /// Windows by `cv2.fisheye.projectPoints(P.reshape(-1, 1, 3), np.zeros((3, 1)), np.zeros((3, 1)), K, D)` and
    /// `cv2.fisheye.undistortPoints(px.reshape(-1, 1, 2), K, D)` (x/z, y/z), each float as Python's `repr`.
    const OPENCV_PROJECT: [([f64; 3], [f64; 2]); 5] = [
        (
            [100.0, -20.0, 400.0],
            [490.45186593459323, 380.86889847104294],
        ),
        (
            [-250.0, 130.0, 300.0],
            [186.182311327905, 510.38848642462085],
        ),
        ([30.0, 260.0, 90.0], [438.2970907270482, 719.8610123527612]),
        ([-5.0, -3.0, 600.0], [398.112672329871, 396.8496032862919]),
        (
            [400.0, 300.0, 120.0],
            [668.6647814283444, 599.0820153980369],
        ),
    ];
    const OPENCV_UNDISTORT: [([f64; 2], [f64; 2]); 5] = [
        ([180.0, 240.0], [-1.0134934276577727, -0.7279170098951971]),
        ([700.0, 420.0], [1.7607144724015389, 0.12564843148264146]),
        ([401.7, 398.7], [0.001349529117957419, 0.0]),
        ([550.0, 690.0], [1.4776986424017349, 2.8959599734735884]),
        ([250.0, 600.0], [-0.61336656196054, 0.817487663241386]),
    ];
    /// Points behind the eye (Z < 0), same command: OpenCV divides by Z, so it gives the pixel of -p.
    const OPENCV_BEHIND: [([f64; 3], [f64; 2]); 2] = [
        ([400.0, 0.0, -7.0], [41.306753062812334, 398.7]), // 91.0° from the axis
        (
            [-300.0, 250.0, -40.0],
            [669.4727407192565, 175.3807437242535],
        ), // 95.8°
    ];

    #[test]
    fn project_and_unproject_give_opencv_fisheye_values() {
        let e = kb4_eye(0.0);
        for (p, want) in OPENCV_PROJECT {
            let got = e.project(p).unwrap();
            assert!(near(got, want, 1e-9), "{p:?}: {got:?} vs OpenCV {want:?}");
        }
        for (px, want) in OPENCV_UNDISTORT {
            let r = e.unproject(px).unwrap();
            let got = [r[0] / r[2], r[1] / r[2]];
            assert!(near(got, want, 1e-9), "{px:?}: {got:?} vs OpenCV {want:?}");
        }
        // behind the eye: OpenCV's pixel is that of -p; project gives p's own pixel within 92.5°, None beyond
        for (p, want) in OPENCV_BEHIND {
            let got = e.project(p.map(|v| -v)).unwrap();
            assert!(near(got, want, 1e-9), "-{p:?}: {got:?} vs OpenCV {want:?}");
        }
        let own = e.project(OPENCV_BEHIND[0].0).unwrap();
        assert!(
            own[0] > e.cx() + 300.0,
            "{own:?}: on the +X side, not OpenCV's"
        );
        assert_eq!(e.project(OPENCV_BEHIND[1].0), None);
    }

    #[test]
    fn the_optical_axis_is_the_principal_point() {
        for e in [kb4_eye(0.0), kb4_eye(0.8)] {
            assert_eq!(e.project([0.0, 0.0, 250.0]), Some([e.cx(), e.cy()]));
            assert_eq!(e.unproject([e.cx(), e.cy()]), Some([0.0, 0.0, 1.0]));
        }
    }

    #[test]
    fn a_point_projected_then_unprojected_is_back_on_its_ray() {
        for e in [kb4_eye(0.0), kb4_eye(0.8)] {
            for theta in [0.5, 10.0, 30.0, 60.0, 85.0, 89.9, 90.0, 91.0, 92.4] {
                for phi in (0..360).step_by(15).map(f64::from) {
                    let ray = dir(theta, phi);
                    let uv = e.project(ray.map(|v| v * 437.0)).expect("inside the field");
                    let back = e.unproject(uv).expect("inside the field");
                    assert!(
                        near(back, ray, 1e-9),
                        "{theta}° {phi}°: {back:?} vs {ray:?}"
                    );
                    assert!((back.iter().map(|v| v * v).sum::<f64>() - 1.0).abs() < 1e-12);
                }
            }
        }
    }

    #[test]
    fn a_pixel_unprojected_then_projected_is_the_same_pixel() {
        let e = kb4_eye(0.8);
        let [k1, k2, k3, k4] = e.d;
        let t = 92.5f64.to_radians();
        let edge = t * (1.0 + k1 * t.powi(2) + k2 * t.powi(4) + k3 * t.powi(6) + k4 * t.powi(8));
        let (mut inside, mut outside) = (0, 0);
        for v in (0..=800).step_by(25) {
            for u in (0..=800).step_by(25) {
                let uv = [f64::from(u) + 0.25, f64::from(v) - 0.25];
                let y = (uv[1] - 398.7) / 370.1;
                let r = ((uv[0] - 401.2 - 0.8 * y) / 370.5).hypot(y); // θd of this pixel
                match e.unproject(uv) {
                    Some(ray) => {
                        inside += 1;
                        assert!(r <= edge + 1e-9, "{uv:?} is beyond 92.5°");
                        let back = e.project(ray).expect("a ray from unproject projects");
                        assert!(near(back, uv, 1e-6), "{uv:?} -> {ray:?} -> {back:?}");
                    }
                    None => {
                        outside += 1;
                        assert!(r > edge - 1e-9, "{uv:?} is within 92.5°");
                    }
                }
            }
        }
        assert!(
            inside > 0 && outside > 0,
            "{inside} inside, {outside} outside"
        );
    }

    #[test]
    fn beyond_92_5_degrees_at_the_origin_or_not_finite_gives_none() {
        let e = kb4_eye(0.0);
        assert!(e.project(dir(92.4, 10.0)).is_some());
        assert_eq!(e.project(dir(92.6, 10.0)), None);
        assert_eq!(e.project([0.0, 0.0, -1.0]), None);
        assert_eq!(e.project([0.0; 3]), None);
        assert_eq!(e.project([f64::NAN, 0.0, 1.0]), None);
        assert_eq!(e.project([f64::INFINITY, 0.0, 1.0]), None);
        assert_eq!(e.unproject([f64::NAN, 400.0]), None);
        assert_eq!(e.unproject([0.0, 0.0]), None); // the corner is beyond 92.5°
        let edge = e.project(dir(92.499, 0.0)).unwrap();
        assert!(e.unproject(edge).is_some());
        assert_eq!(e.unproject([edge[0] + 0.5, edge[1]]), None);
    }

    #[test]
    fn the_eye_size_is_half_the_frame_width_by_the_frame_height() {
        let d = tmp("size");
        let p = d.join("S1-0042.json");
        std::fs::write(&p, DISTINCT).unwrap();
        let c = load(&p).unwrap();
        c.check_eye_size(1600, 800).unwrap();
        c.check_eye_size(1601, 800).unwrap(); // Python: width // 2
        for (w, h) in [(1280, 640), (800, 800), (1600, 801)] {
            assert_eq!(
                c.check_eye_size(w, h).unwrap_err(),
                SdkError::CalibLookup(
                    "This calibration is for 800x800 eyes. Open the 1600x800 mode.".into()
                )
            );
        }
    }
}
