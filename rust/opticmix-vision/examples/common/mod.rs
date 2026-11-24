//! Shared by the examples: command-line options, error messages, and opening the Weevia S1 — or, with
//! `--synthetic`, a stand-in that needs no camera (generated frames and simulated controls with made-up ranges).
#![allow(dead_code)] // each example uses part of this file

use std::process::ExitCode;
use std::str::FromStr;

use opticmix_vision::{Device, DeviceConfig, NegotiatedMode, Result, SdkError};

/// The options after `--` in `cargo run --example <name> -- <options>`.
pub struct Args {
    args: Vec<String>,
    usage: &'static str,
}

impl Args {
    /// Prints `usage` and exits on `--help`; stops on an option not in `known`.
    pub fn parse(usage: &'static str, known: &[&str]) -> Args {
        let args: Vec<String> = std::env::args().skip(1).collect();
        if args.iter().any(|a| a == "--help" || a == "-h") {
            println!("{usage}");
            std::process::exit(0);
        }
        if let Some(a) = args
            .iter()
            .find(|a| a.starts_with("--") && !known.contains(&a.as_str()))
        {
            Args {
                args: Vec::new(),
                usage,
            }
            .fail(&format!("unknown option {a}"));
        }
        Args { args, usage }
    }

    pub fn flag(&self, name: &str) -> bool {
        self.args.iter().any(|a| a == name)
    }

    /// The value after `name`, or `default` when `name` is not given.
    pub fn value<T: FromStr>(&self, name: &str, default: T) -> T {
        match self.args.iter().position(|a| a == name) {
            None => default,
            Some(i) => match self.args.get(i + 1).and_then(|v| v.parse().ok()) {
                Some(v) => v,
                None => self.fail(&format!("{name} needs a value")),
            },
        }
    }

    pub fn fail(&self, why: &str) -> ! {
        eprintln!("{why}\n{}", self.usage);
        std::process::exit(2);
    }
}

/// This example's name (for the hints below).
fn example_name() -> String {
    std::env::current_exe()
        .ok()
        .and_then(|p| p.file_stem().map(|s| s.to_string_lossy().into_owned()))
        .unwrap_or_else(|| "<name>".into())
}

/// Exit code 0, or the error on stderr — with what to do when no camera or two cameras were found — and exit code 2.
pub fn report(r: std::result::Result<(), Box<dyn std::error::Error>>) -> ExitCode {
    let Err(e) = r else {
        return ExitCode::SUCCESS;
    };
    eprintln!("error: {e}");
    match e.downcast_ref::<SdkError>() {
        Some(SdkError::NoMatch(_)) => eprintln!(
            "Connect the camera. Without a camera: \
             cargo run --release --features test-support --example {} -- --synthetic\n\
             The devices Windows sees: cargo run --example list_devices",
            example_name()
        ),
        Some(SdkError::MultipleMatches(_)) => eprintln!(
            "Connect one camera only: two cameras of the same name cannot be told apart \
             (the camera reports no USB serial number)."
        ),
        _ => {}
    }
    ExitCode::from(2)
}

/// `1600x800 MJPG 90.0 fps (msmf)` — the mode the camera actually gave and the capture backend.
pub fn mode_line(m: &NegotiatedMode) -> String {
    format!(
        "{}x{} {} {:.1} fps ({})",
        m.width, m.height, m.fourcc, m.fps, m.backend_name
    )
}

/// The Weevia S1, found by name — or, with `synthetic`, generated frames (not measured data).
pub fn open_camera(synthetic: bool) -> Result<Device> {
    open_camera_with(DeviceConfig::weevia_s1(), synthetic, None)
}

/// `open_camera` with your own settings. With `synthetic`, `freeze_after` makes one eye repeat the same image
/// from that frame on, as a stalled stream would (the real camera ignores it).
pub fn open_camera_with(
    cfg: DeviceConfig,
    synthetic: bool,
    freeze_after: Option<usize>,
) -> Result<Device> {
    if !synthetic {
        return Device::open(cfg);
    }
    #[cfg(feature = "test-support")]
    {
        Device::open_with(cfg, Box::new(generated::Generated::new(freeze_after)))
    }
    #[cfg(not(feature = "test-support"))]
    {
        let _ = freeze_after;
        Err(SdkError::Unavailable(format!(
            "--synthetic uses the crate's stand-ins: add --features test-support \
             (cargo run --release --features test-support --example {} -- --synthetic)",
            example_name()
        )))
    }
}

/// A stand-in camera: 1600x800 side-by-side MJPG at 90 frames per second, paced in software, with the
/// crate's simulated controls (`MockBackend`: made-up ranges and defaults, nothing is sent anywhere).
#[cfg(feature = "test-support")]
mod generated {
    use std::time::{Duration, Instant};

    use opticmix_vision::backend::{mock::MockBackend, Backend};
    use opticmix_vision::capture::Capture;
    use opticmix_vision::{NegotiatedMode, Result};

    const W: u16 = 1600;
    const H: u16 = 800;

    pub struct Generated {
        /// Alternated, so no eye repeats its image (that would trip the stall check). With `freeze_after`,
        /// frames 2 and 3 keep the first half of the image (one eye) the same.
        frames: Vec<Vec<u8>>,
        freeze_after: Option<usize>,
        n: usize,
        next: Instant,
    }

    /// A diagonal ramp; `first` and `second` shift each half of the side-by-side image.
    fn frame(first: usize, second: usize) -> Vec<u8> {
        let half = W as usize / 2;
        let px: Vec<u8> = (0..H as usize)
            .flat_map(|y| {
                (0..W as usize).map(move |x| {
                    let shift = if x < half { first } else { second };
                    ((x + y + shift) / 8 % 256) as u8
                })
            })
            .collect();
        let mut jpeg = Vec::new();
        jpeg_encoder::Encoder::new(&mut jpeg, 90)
            .encode(&px, W, H, jpeg_encoder::ColorType::Luma)
            .expect("encoding a generated frame");
        jpeg
    }

    impl Generated {
        pub fn new(freeze_after: Option<usize>) -> Generated {
            let mut frames = vec![frame(0, 0), frame(64, 64)];
            if freeze_after.is_some() {
                frames.extend([frame(0, 0), frame(0, 64)]);
            }
            Generated {
                frames,
                freeze_after,
                n: 0,
                next: Instant::now(),
            }
        }
    }

    impl Capture for Generated {
        fn mode(&self) -> NegotiatedMode {
            NegotiatedMode {
                width: W.into(),
                height: H.into(),
                fps: 90.0,
                fourcc: "MJPG".into(),
                backend_name: "synthetic".into(),
            }
        }
        fn device_name(&self) -> &str {
            "synthetic"
        }
        fn read_jpeg(&mut self) -> Result<Vec<u8>> {
            let now = Instant::now();
            if self.next > now {
                std::thread::sleep(self.next - now);
            }
            self.next = self.next.max(now) + Duration::from_secs_f64(1.0 / 90.0);
            let frozen = self.freeze_after.is_some_and(|f| self.n >= f);
            let i = self.n % 2 + if frozen { 2 } else { 0 };
            self.n += 1;
            Ok(self.frames[i].clone())
        }
        fn controls_backend(&self) -> Result<Box<dyn Backend>> {
            Ok(Box::new(MockBackend::new()))
        }
    }
}
