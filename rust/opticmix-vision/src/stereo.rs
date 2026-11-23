//! Splits one frame into two eyes — same rule as Python `device.split_stereo` (gray only).

use crate::error::{Result, SdkError};

/// 8-bit gray image. Row-major, `data.len() == width * height`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GrayImage {
    pub width: u32,
    pub height: u32,
    pub data: Vec<u8>,
}

impl GrayImage {
    pub fn new(width: u32, height: u32, data: Vec<u8>) -> Result<GrayImage> {
        if (width as usize).checked_mul(height as usize) != Some(data.len()) {
            return Err(SdkError::InvalidValue(format!(
                "GrayImage: {} bytes for {width}x{height}",
                data.len()
            )));
        }
        Ok(GrayImage { width, height, data })
    }
}

/// How the two eyes sit in one frame. Weevia S1 = `Sbs`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Layout {
    Sbs,
    Tb,
    PlanePack,
}

/// Which eye the first half is. Weevia S1 = `FirstIsRight` (confirmed 2026-09-16).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LrOrder {
    FirstIsLeft,
    FirstIsRight,
}

pub fn split_stereo(
    frame: &GrayImage,
    layout: Layout,
    lr_order: LrOrder,
) -> Result<(GrayImage, GrayImage)> {
    let (w, h) = (frame.width as usize, frame.height as usize);
    // 칸이 pub 이라 new 를 거치지 않은 영상도 온다 — 자르기 전에 길이부터 본다(패닉 대신 오류).
    if w.checked_mul(h) != Some(frame.data.len()) {
        return Err(SdkError::InvalidValue(format!(
            "the frame has {} bytes for {w}x{h}",
            frame.data.len()
        )));
    }
    if w == 0 || h == 0 {
        return Err(SdkError::InvalidValue(format!("the frame is empty ({w}x{h})")));
    }
    let eye = |width: usize, height: usize, data: Vec<u8>| GrayImage {
        width: width as u32,
        height: height as u32,
        data,
    };
    let (first, second) = match layout {
        Layout::Sbs => {
            if w % 2 != 0 {
                return Err(SdkError::InvalidValue(format!("sbs: the width is odd ({w})")));
            }
            let half = w / 2;
            let (mut a, mut b) = (Vec::with_capacity(half * h), Vec::with_capacity(half * h));
            for row in frame.data.chunks_exact(w) {
                a.extend_from_slice(&row[..half]);
                b.extend_from_slice(&row[half..]);
            }
            (eye(half, h, a), eye(half, h, b))
        }
        Layout::Tb => {
            if h % 2 != 0 {
                return Err(SdkError::InvalidValue(format!("tb: the height is odd ({h})")));
            }
            let cut = w * (h / 2);
            (
                eye(w, h / 2, frame.data[..cut].to_vec()),
                eye(w, h / 2, frame.data[cut..].to_vec()),
            )
        }
        Layout::PlanePack => {
            if w % 2 != 0 {
                return Err(SdkError::InvalidValue(format!(
                    "plane_pack: expected (H, 2W), the width is odd ({w})"
                )));
            }
            let a = frame.data.iter().step_by(2).copied().collect();
            let b = frame.data.iter().skip(1).step_by(2).copied().collect();
            (eye(w / 2, h, a), eye(w / 2, h, b))
        }
    };
    Ok(match lr_order {
        LrOrder::FirstIsLeft => (first, second),
        LrOrder::FirstIsRight => (second, first),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn img(w: u32, h: u32) -> GrayImage {
        GrayImage::new(w, h, (0..w * h).map(|i| i as u8).collect()).unwrap()
    }

    #[test]
    fn sbs_first_is_right_gives_the_second_half_as_left() {
        let (l, r) = split_stereo(&img(4, 1), Layout::Sbs, LrOrder::FirstIsRight).unwrap();
        assert_eq!((l.data, r.data), (vec![2, 3], vec![0, 1]));
    }

    #[test]
    fn sbs_first_is_left_keeps_the_order_on_every_row() {
        let (l, r) = split_stereo(&img(4, 2), Layout::Sbs, LrOrder::FirstIsLeft).unwrap();
        assert_eq!((l.width, l.height), (2, 2));
        assert_eq!((l.data, r.data), (vec![0, 1, 4, 5], vec![2, 3, 6, 7]));
    }

    #[test]
    fn tb_splits_the_rows() {
        let (l, r) = split_stereo(&img(2, 4), Layout::Tb, LrOrder::FirstIsLeft).unwrap();
        assert_eq!((l.width, l.height), (2, 2));
        assert_eq!((l.data, r.data), (vec![0, 1, 2, 3], vec![4, 5, 6, 7]));
    }

    #[test]
    fn plane_pack_reads_h_by_2w_as_interleaved_pairs() {
        let (l, r) = split_stereo(&img(4, 1), Layout::PlanePack, LrOrder::FirstIsLeft).unwrap();
        assert_eq!((l.data, r.data), (vec![0, 2], vec![1, 3]));
    }

    #[test]
    fn odd_sizes_and_empty_frames_are_refused() {
        for (im, layout, word) in [
            (img(3, 1), Layout::Sbs, "odd"),
            (img(2, 3), Layout::Tb, "odd"),
            (img(3, 1), Layout::PlanePack, "odd"),
            (img(0, 0), Layout::Sbs, "empty"),
        ] {
            let e = split_stereo(&im, layout, LrOrder::FirstIsLeft).unwrap_err();
            assert!(matches!(e, SdkError::InvalidValue(_)), "{e:?}");
            assert!(e.to_string().contains(word), "{e}");
        }
    }

    #[test]
    fn gray_image_refuses_a_wrong_byte_count() {
        assert!(GrayImage::new(2, 2, vec![0; 3]).is_err());
    }

    #[test]
    fn a_hand_built_image_with_the_wrong_byte_count_is_an_error_not_a_panic() {
        // 칸이 pub 이라 new 를 거치지 않고 만들 수 있다.
        let bad = GrayImage { width: 4, height: 2, data: vec![0; 3] };
        for layout in [Layout::Sbs, Layout::Tb, Layout::PlanePack] {
            let e = split_stereo(&bad, layout, LrOrder::FirstIsLeft).unwrap_err();
            assert!(matches!(e, SdkError::InvalidValue(_)), "{layout:?}: {e:?}");
        }
    }

    #[test]
    fn every_layout_and_order_by_hand() {
        // 4×2 영상, 값 = 0..8: 행 [0 1 2 3] / [4 5 6 7].
        type Eye = (u32, u32, Vec<u8>);
        let sbs_a: Eye = (2, 2, vec![0, 1, 4, 5]);
        let sbs_b: Eye = (2, 2, vec![2, 3, 6, 7]);
        let tb_a: Eye = (4, 1, vec![0, 1, 2, 3]);
        let tb_b: Eye = (4, 1, vec![4, 5, 6, 7]);
        let pp_a: Eye = (2, 2, vec![0, 2, 4, 6]);
        let pp_b: Eye = (2, 2, vec![1, 3, 5, 7]);
        let table = [
            (Layout::Sbs, LrOrder::FirstIsLeft, &sbs_a, &sbs_b),
            (Layout::Sbs, LrOrder::FirstIsRight, &sbs_b, &sbs_a),
            (Layout::Tb, LrOrder::FirstIsLeft, &tb_a, &tb_b),
            (Layout::Tb, LrOrder::FirstIsRight, &tb_b, &tb_a),
            (Layout::PlanePack, LrOrder::FirstIsLeft, &pp_a, &pp_b),
            (Layout::PlanePack, LrOrder::FirstIsRight, &pp_b, &pp_a),
        ];
        for (layout, order, left, right) in table {
            let (l, r) = split_stereo(&img(4, 2), layout, order).unwrap();
            assert_eq!((l.width, l.height, l.data), left.clone(), "{layout:?} {order:?} left");
            assert_eq!((r.width, r.height, r.data), right.clone(), "{layout:?} {order:?} right");
        }
    }
}
