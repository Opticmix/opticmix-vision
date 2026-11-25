//! KB4 fisheye: pixel → unit ray (Newton), 3D → pixel, monotonicity (fold) check.
//! Projection and unprojection are one source with the published crate: its `src/calib/kb4.rs` is compiled here too
//! (as `calib` does with the calibration file's `schema.rs`).
#[path = "../../opticmix-vision/src/calib/kb4.rs"]
mod shared;
pub use shared::*;

pub use crate::calib::kb4_is_injective;

/// The i-th point of numpy's `linspace(0, max, n)` (tests and diagnostics).
pub fn linspace_point(max_theta_rad: f64, n: usize, i: usize) -> f64 {
    if n > 1 && i == n - 1 {
        max_theta_rad
    } else {
        i as f64 * (max_theta_rad / (n - 1).max(1) as f64)
    }
}

// Tests use a unit calibration kept in this repository (not in the published crate).
#[cfg(all(test, feature = "repo-tests"))]
#[path = "kb4_repo_tests.rs"]
mod repo_tests;
