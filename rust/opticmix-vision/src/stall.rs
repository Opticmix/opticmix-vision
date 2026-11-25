//! Same rule and same message as Python `Device._check_stall`. The cause is not known, so the message names no cause.

use crate::error::{Result, SdkError};
use crate::stereo::GrayImage;

pub fn stall_message(eye: &str, n: u32) -> String {
    format!(
        "The {eye} eye has sent the same image for {n} frames in a row: its stream has stopped. \
         Re-insert the USB cable: unplug it and plug it back in (it sometimes takes two tries). \
         stall_frames=0 in DeviceConfig turns this check off."
    )
}

/// Fails when one eye yields a byte-identical image `n` frames in a row. `n == 0` turns it off.
pub struct StallCheck {
    n: u32,
    prev: Option<(Vec<u8>, Vec<u8>)>,
    same: [u32; 2],
    /// Frames whose two eyes were both byte-identical to the frame before.
    repeated: u64,
}

impl StallCheck {
    pub fn new(n: u32) -> StallCheck {
        StallCheck {
            n,
            prev: None,
            same: [0, 0],
            repeated: 0,
        }
    }

    pub fn check(&mut self, left: &GrayImage, right: &GrayImage) -> Result<()> {
        let n = self.n;
        if n == 0 {
            return Ok(()); // 꺼짐 — 복사도 하지 않는다
        }
        // 매 프레임 두 눈을 복사해 비교한다(1600×800 에서 프레임당 약 1.3 MB).
        let Some(prev) = self.prev.replace((left.data.clone(), right.data.clone())) else {
            return Ok(());
        };
        // Both eyes first: a frame that ends in a stall error still counts, as in Python.
        let same = [left.data == prev.0, right.data == prev.1];
        self.repeated += u64::from(same[0] && same[1]);
        for (i, eye) in ["left", "right"].into_iter().enumerate() {
            self.same[i] = if same[i] { self.same[i] + 1 } else { 0 };
            if self.same[i] + 1 >= n {
                return Err(SdkError::Device(stall_message(eye, n)));
            }
        }
        Ok(())
    }

    /// Frames whose two eyes were both byte-identical to the frame before, by the comparison above; `None` when
    /// the check is off (`n == 0`: nothing is compared).
    pub fn repeated(&self) -> Option<u64> {
        (self.n != 0).then_some(self.repeated)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn g(v: u8) -> GrayImage {
        GrayImage::new(2, 1, vec![v, v]).unwrap()
    }

    #[test]
    fn the_nth_identical_frame_is_an_error_naming_the_eye() {
        let mut s = StallCheck::new(3);
        assert!(s.check(&g(1), &g(9)).is_ok());
        assert!(s.check(&g(1), &g(8)).is_ok());
        let e = s.check(&g(1), &g(7)).unwrap_err();
        assert_eq!(e, SdkError::Device(stall_message("left", 3)));
    }

    #[test]
    fn a_changed_frame_resets_the_count() {
        let mut s = StallCheck::new(3);
        s.check(&g(1), &g(1)).unwrap();
        s.check(&g(2), &g(2)).unwrap();
        s.check(&g(2), &g(3)).unwrap();
        assert!(s.check(&g(3), &g(3)).is_ok());
    }

    #[test]
    fn the_right_eye_alone_can_stall() {
        let mut s = StallCheck::new(2);
        s.check(&g(1), &g(5)).unwrap();
        let e = s.check(&g(2), &g(5)).unwrap_err();
        assert_eq!(e, SdkError::Device(stall_message("right", 2)));
    }

    #[test]
    fn repeated_counts_only_frames_whose_two_eyes_both_repeat() {
        let mut s = StallCheck::new(90);
        s.check(&g(1), &g(5)).unwrap();
        s.check(&g(1), &g(5)).unwrap();
        assert_eq!(s.repeated(), Some(1), "both eyes repeated");
        s.check(&g(1), &g(6)).unwrap();
        assert_eq!(s.repeated(), Some(1), "one eye only: not counted");
    }

    #[test]
    fn zero_turns_it_off() {
        let mut s = StallCheck::new(0);
        for _ in 0..10 {
            s.check(&g(1), &g(1)).unwrap();
        }
        assert!(s.prev.is_none(), "off must not copy the frames");
        assert_eq!(
            s.repeated(),
            None,
            "off compares nothing, so it counts nothing"
        );
    }

    #[test]
    fn the_message_is_the_python_text() {
        assert_eq!(
            stall_message("left", 90),
            "The left eye has sent the same image for 90 frames in a row: its stream has stopped. \
             Re-insert the USB cable: unplug it and plug it back in (it sometimes takes two tries). \
             stall_frames=0 in DeviceConfig turns this check off."
        );
    }
}
