//! KB4 fisheye: pixel → unit ray (Newton), 3D → pixel, monotonicity (fold) check.
//! Same numbers as the Python implementation; powers are taken the same way it takes them
//! (t2*t2 and powf for the closed form, products t4=t2*t2, t6=t4*t2, t8=t4*t4 inside the Newton loop).
use std::f64::consts::PI;

/// 공칭 185° 어안의 반각 = np.deg2rad(92.5).
pub const MAX_INCIDENCE_RAD: f64 = 92.5 * (PI / 180.0);

/// θ_d(θ), closed form.
pub fn kb4_theta_d(theta: f64, d: &[f64; 4]) -> f64 {
    let t2 = theta * theta;
    theta * (1.0 + d[0] * t2 + d[1] * (t2 * t2) + d[2] * t2.powf(3.0) + d[3] * t2.powf(4.0))
}

/// geometry.`_kb4_theta_d` · Newton 루프 (곱 형).
fn kb4_theta_d_mul(theta: f64, d: &[f64; 4]) -> f64 {
    let t2 = theta * theta;
    let t4 = t2 * t2;
    theta * (1.0 + d[0] * t2 + d[1] * t4 + d[2] * (t4 * t2) + d[3] * (t4 * t4))
}

/// 픽셀 묶음 → (광선, ok). ok=false 행은 쓰지 말 것(수렴 실패·비유한·원 밖).
/// 수렴 판정은 묶음 전체 공동(Python 벡터 연산과 같음) — 같은 점을 따로 풀면 반복 횟수가 달라질 수 있다.
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
            // Python 은 여기서 _kb4_theta_d(배열) — 배열 `**` 도 2→곱, 3·4→npy_pow 라 kb4_theta_d 와 같다.
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

/// 3D 점(카메라 좌표, mm) → 픽셀. 화각(92.5°) 밖·원점·비유한이면 None. 화면 경계 검사는 호출자 몫.
/// KB4 접힘 검사는 캘리브 로드(`kb4_is_injective`)가 이미 한다.
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

/// Python `kb4_is_injective(D, max_theta_rad, n)`: θ_d(θ) 의 도함수 1+3k1θ²+5k2θ⁴+7k3θ⁶+9k4θ⁸ 가 `np.linspace(0, max, n)`
/// 의 모든 점에서 양수면 None, 아니면 처음 접힌 각(rad). 격자 사이의 접힘은 보지 않는다(원래 설계).
/// linspace 는 numpy 와 같은 식: i·step(step=(max-0)/(n-1)), 마지막 점은 max 그대로. `**3`·`**4` 는 npy_pow(=powf).
pub fn kb4_is_injective(d: &[f64; 4], max_theta_rad: f64, n: usize) -> Option<f64> {
    let step = if n > 1 {
        max_theta_rad / (n - 1) as f64
    } else {
        0.0
    };
    for i in 0..n {
        let th = if n > 1 && i == n - 1 {
            max_theta_rad
        } else {
            i as f64 * step
        };
        let t2 = th * th;
        let dd = 1.0
            + 3.0 * d[0] * t2
            + 5.0 * d[1] * (t2 * t2)
            + 7.0 * d[2] * t2.powf(3.0)
            + 9.0 * d[3] * t2.powf(4.0);
        if dd <= 0.0 {
            return Some(th);
        }
    }
    None
}

/// numpy `linspace(0, max, n)` 의 i 번째 점(시험·진단용).
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
