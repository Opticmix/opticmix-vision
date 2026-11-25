//! KB4 fisheye (Kannala-Brandt 4th order, the model of OpenCV's `cv::fisheye`): pixel → unit ray (Newton),
//! 3D point → pixel.
//!
//! One source for both Rust crates that use it: this crate (`Intrinsics::project` / `unproject`) and the geometry
//! crate of the repository, so both give the same numbers. Powers are taken as written: t2*t2 and powf in the closed
//! form (`kb4_theta_d`), the products t4 = t2*t2, t6 = t4*t2, t8 = t4*t4 in the projection and the Newton loop.
use std::f64::consts::PI;

/// The largest angle from the optical axis that is projected or unprojected: half the lens's 185° field (vendor
/// spec), as `DEFAULT_MAX_THETA_DEG`.
pub const MAX_INCIDENCE_RAD: f64 = 92.5 * (PI / 180.0);

/// θ_d(θ), closed form.
pub fn kb4_theta_d(theta: f64, d: &[f64; 4]) -> f64 {
    let t2 = theta * theta;
    theta * (1.0 + d[0] * t2 + d[1] * (t2 * t2) + d[2] * t2.powf(3.0) + d[3] * t2.powf(4.0))
}

/// θ_d(θ) with the powers as products (the projection and the Newton loop).
fn kb4_theta_d_mul(theta: f64, d: &[f64; 4]) -> f64 {
    let t2 = theta * theta;
    let t4 = t2 * t2;
    theta * (1.0 + d[0] * t2 + d[1] * t4 + d[2] * (t4 * t2) + d[3] * (t4 * t4))
}

/// Pixels → (unit rays, ok). Do not use a row whose ok is false (no convergence, not finite, or beyond
/// `MAX_INCIDENCE_RAD`). Convergence is decided for the whole batch at once, so solving the same pixel alone can
/// take a different number of steps.
pub fn unproject_kb4(
    uv: &[[f64; 2]],
    k: &[[f64; 3]; 3],
    d: &[f64; 4],
) -> (Vec<[f64; 3]>, Vec<bool>) {
    let (fx, fy, cx, cy, skew) = (k[0][0], k[1][1], k[0][2], k[1][2], k[0][1]);
    let yd: Vec<f64> = uv.iter().map(|p| (p[1] - cy) / fy).collect();
    let xd: Vec<f64> = uv
        .iter()
        .zip(&yd)
        .map(|(p, y)| (p[0] - cx - skew * y) / fx)
        .collect();
    let theta_d: Vec<f64> = xd.iter().zip(&yd).map(|(x, y)| x.hypot(*y)).collect();
    let mut theta: Vec<f64> = theta_d.iter().map(|t| t.clamp(0.0, PI)).collect();
    let mut ok: Vec<bool> = theta_d.iter().map(|t| t.is_finite()).collect();
    let mut converged = false;
    for _ in 0..20 {
        let mut any_active = false;
        let mut max_step = 0f64;
        for ((t, &td), &oki) in theta.iter_mut().zip(&theta_d).zip(&ok) {
            let t2 = *t * *t;
            let t4 = t2 * t2;
            let (t6, t8) = (t4 * t2, t4 * t4);
            let f = *t * (1.0 + d[0] * t2 + d[1] * t4 + d[2] * t6 + d[3] * t8) - td;
            let df = 1.0 + 3.0 * d[0] * t2 + 5.0 * d[1] * t4 + 7.0 * d[2] * t6 + 9.0 * d[3] * t8;
            let step = if df.abs() > 1e-12 { f / df } else { 0.0 };
            *t = (*t - step).clamp(0.0, PI);
            if oki && step.is_finite() {
                any_active = true;
                max_step = max_step.max(step.abs());
            }
        }
        if !any_active || max_step < 1e-10 {
            converged = true;
            break;
        }
    }
    if !converged {
        for ((o, &t), &td) in ok.iter_mut().zip(&theta).zip(&theta_d) {
            *o = *o && (kb4_theta_d(t, d) - td).abs() < 1e-6;
        }
    }
    let limit = kb4_theta_d(MAX_INCIDENCE_RAD, d);
    let mut rays = Vec::with_capacity(uv.len());
    for i in 0..uv.len() {
        ok[i] = ok[i] && theta_d[i] <= limit + 1e-9 && theta[i] <= MAX_INCIDENCE_RAD + 1e-9;
        let s = if theta_d[i] > 0.0 {
            theta[i].sin() / theta_d[i]
        } else {
            0.0
        };
        let r = [xd[i] * s, yd[i] * s, theta[i].cos()];
        ok[i] = ok[i] && r.iter().all(|v| v.is_finite());
        rays.push(r);
    }
    (rays, ok)
}

/// 3D point (camera coordinates) → pixel. None beyond `MAX_INCIDENCE_RAD`, at the origin, or not finite. The
/// image bounds are the caller's to check; folded coefficients are refused when the calibration is read.
pub fn project_kb4(p: [f64; 3], k: &[[f64; 3]; 3], d: &[f64; 4]) -> Option<[f64; 2]> {
    if !p.iter().all(|v| v.is_finite()) {
        return None;
    }
    let r = p[0].hypot(p[1]);
    if r == 0.0 && p[2] == 0.0 {
        return None;
    }
    let theta = r.atan2(p[2]);
    if theta > MAX_INCIDENCE_RAD {
        return None;
    }
    let s = if r > 0.0 {
        kb4_theta_d_mul(theta, d) / r
    } else {
        0.0
    };
    let (xd, yd) = (p[0] * s, p[1] * s);
    let u = k[0][0] * xd + k[0][1] * yd + k[0][2];
    let v = k[1][1] * yd + k[1][2];
    (u.is_finite() && v.is_finite()).then_some([u, v])
}
