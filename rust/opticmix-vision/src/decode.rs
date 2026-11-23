//! One MJPG frame → 8-bit gray (Y channel). UVC MJPG may omit the Huffman tables (DHT). zune-jpeg 0.5 fills in the standard
//! tables (OpenDML) only when APP0 carries an `AVI1` tag — so when there is neither DHT nor `AVI1` before SOS, a copy with the tag inserted is decoded.

use std::borrow::Cow;
use std::ops::Range;

use zune_core::bytestream::ZCursor;
use zune_core::colorspace::ColorSpace;
use zune_core::options::DecoderOptions;
use zune_jpeg::JpegDecoder;

use crate::error::{Result, SdkError};
use crate::stereo::GrayImage;

/// Segments before SOS (marker, byte range from the marker to the segment end). Skips by length and stops at SOS —
/// bytes inside entropy-coded data are not mistaken for markers. Empty if the data does not start with SOI.
fn header_segments(data: &[u8]) -> Vec<(u8, Range<usize>)> {
    let mut out = Vec::new();
    if !data.starts_with(&[0xFF, 0xD8]) {
        return out;
    }
    let mut i = 2;
    while i + 1 < data.len() && data[i] == 0xFF {
        match data[i + 1] {
            0xFF => i += 1,               // 채움 바이트
            0xDA => break,                // SOS — 여기부터 엔트로피 데이터
            0x01 | 0xD0..=0xD9 => i += 2, // 길이 없는 표지
            m => {
                let Some(len) = data.get(i + 2..i + 4).map(|b| u16::from_be_bytes([b[0], b[1]]) as usize) else {
                    break;
                };
                let end = (i + 2 + len).min(data.len());
                out.push((m, i..end));
                i = end;
            }
        }
    }
    out
}

/// APP0 `AVI1\0` (length 16) — when zune-jpeg sees it, it fills in missing Huffman tables with the standard ones.
const AVI1_APP0: [u8; 18] = [0xFF, 0xE0, 0x00, 0x10, b'A', b'V', b'I', b'1', 0, 0, 0, 0, 0, 0, 0, 0, 0, 0];

/// If there is neither DHT nor `AVI1` before SOS, a copy with `AVI1_APP0` inserted right after SOI; otherwise unchanged.
fn with_mjpeg_tables(data: &[u8]) -> Cow<'_, [u8]> {
    if !data.starts_with(&[0xFF, 0xD8]) {
        return Cow::Borrowed(data);
    }
    let has_tables = header_segments(data).iter().any(|(m, r)| {
        *m == 0xC4 || (*m == 0xE0 && data.get(r.start + 4..r.end).is_some_and(|b| b.starts_with(b"AVI1\0")))
    });
    if has_tables {
        return Cow::Borrowed(data);
    }
    Cow::Owned([&data[..2], &AVI1_APP0[..], &data[2..]].concat())
}

/// One MJPG frame to 8-bit gray (Y). Accepts UVC frames without Huffman tables. Corrupt bytes give `SdkError::Device`.
pub fn decode_mjpg_gray(data: &[u8]) -> Result<GrayImage> {
    let data = with_mjpeg_tables(data);
    let opts = DecoderOptions::default().jpeg_set_out_colorspace(ColorSpace::Luma);
    let mut d = JpegDecoder::new_with_options(ZCursor::new(&data[..]), opts);
    let px = d
        .decode()
        .map_err(|e| SdkError::Device(format!("an MJPG frame could not be decoded: {e:?}")))?;
    let info = d
        .info()
        .ok_or_else(|| SdkError::Device("an MJPG frame has no header".into()))?;
    GrayImage::new(info.width as u32, info.height as u32, px)
        .map_err(|e| SdkError::Device(format!("an MJPG frame decoded to the wrong size: {e}")))
}

#[cfg(test)]
pub(crate) fn encode_gray(w: u16, h: u16, px: &[u8]) -> Vec<u8> {
    let mut out = Vec::new();
    jpeg_encoder::Encoder::new(&mut out, 100)
        .encode(px, w, h, jpeg_encoder::ColorType::Luma)
        .unwrap();
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_gray_jpeg_comes_back_close_to_its_pixels() {
        let (w, h) = (16u16, 8u16);
        let px: Vec<u8> = (0..w as usize * h as usize).map(|i| (i * 2) as u8).collect();
        let g = decode_mjpg_gray(&encode_gray(w, h, &px)).unwrap();
        assert_eq!((g.width, g.height), (16, 8));
        let worst = g.data.iter().zip(&px).map(|(a, b)| a.abs_diff(*b)).max().unwrap();
        assert!(worst <= 8, "max diff {worst}");
    }

    /// RGB 4:2:2 MJPG (the shape UVC cameras send). Standard Huffman tables (jpeg-encoder default, not optimized).
    fn encode_rgb422(w: u16, h: u16, rgb: &[u8]) -> Vec<u8> {
        let mut out = Vec::new();
        let mut e = jpeg_encoder::Encoder::new(&mut out, 100);
        e.set_sampling_factor(jpeg_encoder::SamplingFactor::R_4_2_2);
        e.encode(rgb, w, h, jpeg_encoder::ColorType::Rgb).unwrap();
        out
    }

    fn rgb_ramp(w: u16, h: u16) -> (Vec<u8>, Vec<u8>) {
        let n = w as usize * h as usize;
        let rgb: Vec<u8> = (0..n).flat_map(|i| [(i * 2) as u8, (i * 2 + 10) as u8, 90]).collect();
        // BT.601 휘도 — JPEG 의 Y 와 같은 식
        let y = rgb
            .as_chunks::<3>()
            .0
            .iter()
            .map(|&[r, g, b]| (0.299 * r as f64 + 0.587 * g as f64 + 0.114 * b as f64).round() as u8)
            .collect();
        (rgb, y)
    }

    #[test]
    fn an_rgb_422_frame_decodes_to_its_luma() {
        let (w, h) = (16u16, 8u16);
        let (rgb, y) = rgb_ramp(w, h);
        let g = decode_mjpg_gray(&encode_rgb422(w, h, &rgb)).unwrap();
        assert_eq!((g.width, g.height), (16, 8));
        let worst = g.data.iter().zip(&y).map(|(a, b)| a.abs_diff(*b)).max().unwrap();
        assert!(worst <= 8, "max diff {worst}");
    }

    #[test]
    fn a_frame_without_huffman_tables_decodes_with_the_standard_ones() {
        let (w, h) = (16u16, 8u16);
        let jpg = encode_rgb422(w, h, &rgb_ramp(w, h).0);
        let mut bare = jpg.clone();
        let dht: Vec<_> = header_segments(&jpg).into_iter().filter(|(m, _)| *m == 0xC4).collect();
        assert!(!dht.is_empty(), "the encoder wrote no DHT");
        for (_, r) in dht.into_iter().rev() {
            bare.drain(r);
        }
        assert!(header_segments(&bare).iter().all(|(m, _)| *m != 0xC4));
        assert_eq!(decode_mjpg_gray(&bare).unwrap(), decode_mjpg_gray(&jpg).unwrap());
    }

    #[test]
    fn broken_bytes_are_a_device_error_not_an_empty_frame() {
        let e = decode_mjpg_gray(&[0xFF, 0xD8, 0x00, 0x01]).unwrap_err();
        assert!(matches!(e, SdkError::Device(_)), "{e:?}");
    }
}
