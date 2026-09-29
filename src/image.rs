//! Single-band stretch, label, palette, and PNG encoding.

use crate::{Result, raster::Raster};
use png::{BitDepth, ColorType, Encoder};
use std::io::Cursor;

pub type Rgba = [u8; 4];

pub fn stretch_to_u8(raster: &Raster, range: [f64; 2]) -> Vec<u8> {
    let normalization = range[1] - range[0];
    raster
        .values
        .iter()
        .zip(&raster.mask)
        .map(|(&value, &masked)| {
            if masked || !value.is_finite() {
                return 0;
            }
            let normalized = if normalization.abs() > 1e-8 {
                ((value - range[0]) / normalization).clamp(0.0, 1.0)
            } else {
                (value - range[0]).clamp(0.0, 1.0)
            };
            (1.0 + normalized * 254.0).round_ties_even() as u8
        })
        .collect()
}

pub fn label_values(raster: &Raster, palette: &[(f64, Rgba)]) -> Vec<u8> {
    raster
        .values
        .iter()
        .zip(&raster.mask)
        .map(|(&value, &masked)| {
            if masked {
                return 0;
            }
            palette
                .iter()
                .position(|(label, _)| value == *label)
                .map(|index| (index + 1) as u8)
                .unwrap_or(0)
        })
        .collect()
}

pub fn grayscale_png(width: usize, height: usize, pixels: &[u8]) -> Result<Vec<u8>> {
    encode_png(width, height, pixels, None)
}

pub fn palette_png(width: usize, height: usize, pixels: &[u8], colors: &[Rgba]) -> Result<Vec<u8>> {
    if colors.len() > 255 {
        return Err("palette must contain at most 255 colors".into());
    }
    let mut palette = vec![0, 0, 0];
    let mut transparency = vec![0];
    for color in colors {
        palette.extend_from_slice(&color[..3]);
        transparency.push(color[3]);
    }
    encode_png(width, height, pixels, Some((palette, transparency)))
}

pub fn viridis_png(width: usize, height: usize, pixels: &[u8]) -> Result<Vec<u8>> {
    let colors = VIRIDIS
        .chunks_exact(4)
        .map(|entry| [entry[0], entry[1], entry[2], entry[3]])
        .collect::<Vec<_>>();
    palette_png(width, height, pixels, &colors)
}

pub fn empty_png(width: usize, height: usize) -> Result<Vec<u8>> {
    grayscale_png(width, height, &vec![0; width * height])
}

/// Encode legacy RGB output. A fully black pixel is the transparency key, as
/// in Pillow's `transparency=(0, 0, 0)` behavior used by the Python service.
pub fn rgb_png(width: usize, height: usize, channels: [&[u8]; 3]) -> Result<Vec<u8>> {
    let pixel_count = width * height;
    if channels.iter().any(|channel| channel.len() != pixel_count) {
        return Err("RGB channel length does not match dimensions".into());
    }
    let mut pixels = Vec::with_capacity(pixel_count * 3);
    for index in 0..pixel_count {
        pixels.extend_from_slice(&[channels[0][index], channels[1][index], channels[2][index]]);
    }
    let mut output = Vec::new();
    {
        let mut encoder = Encoder::new(Cursor::new(&mut output), width as u32, height as u32);
        encoder.set_depth(BitDepth::Eight);
        encoder.set_color(ColorType::Rgb);
        encoder.set_compression(png::Compression::Fast);
        // Truecolor tRNS stores each transparent sample as a 16-bit big-endian value.
        encoder.set_trns(vec![0, 0, 0, 0, 0, 0]);
        let mut writer = encoder
            .write_header()
            .map_err(|error| format!("could not encode RGB PNG: {error}"))?;
        writer
            .write_image_data(&pixels)
            .map_err(|error| format!("could not encode RGB PNG: {error}"))?;
    }
    configured_compression(output)
}

fn encode_png(
    width: usize,
    height: usize,
    pixels: &[u8],
    palette: Option<(Vec<u8>, Vec<u8>)>,
) -> Result<Vec<u8>> {
    if pixels.len() != width * height {
        return Err("PNG pixel length does not match dimensions".into());
    }
    let mut output = Vec::new();
    {
        let mut encoder = Encoder::new(Cursor::new(&mut output), width as u32, height as u32);
        encoder.set_depth(BitDepth::Eight);
        encoder.set_compression(png::Compression::Fast);
        match palette {
            Some((colors, alpha)) => {
                encoder.set_color(ColorType::Indexed);
                encoder.set_palette(colors);
                encoder.set_trns(alpha);
            }
            None => {
                encoder.set_color(ColorType::Grayscale);
                encoder.set_trns(vec![0, 0]);
            }
        }
        let mut writer = encoder
            .write_header()
            .map_err(|error| format!("could not encode PNG: {error}"))?;
        writer
            .write_image_data(pixels)
            .map_err(|error| format!("could not encode PNG: {error}"))?;
    }
    configured_compression(output)
}

// Exact 255-entry Terracotta viridis palette for compatible rendering.
const VIRIDIS: &[u8] = &[
    68, 1, 84, 255, 68, 2, 85, 255, 68, 3, 87, 255, 69, 5, 88, 255, 69, 6, 90, 255, 69, 8, 91, 255,
    70, 9, 92, 255, 70, 11, 94, 255, 70, 12, 95, 255, 70, 14, 97, 255, 71, 15, 98, 255, 71, 17, 99,
    255, 71, 18, 101, 255, 71, 20, 102, 255, 71, 21, 103, 255, 71, 22, 105, 255, 71, 24, 106, 255,
    72, 25, 107, 255, 72, 26, 108, 255, 72, 28, 110, 255, 72, 29, 111, 255, 72, 30, 112, 255, 72,
    32, 113, 255, 72, 33, 114, 255, 72, 34, 115, 255, 72, 35, 116, 255, 71, 37, 117, 255, 71, 38,
    118, 255, 71, 39, 119, 255, 71, 40, 120, 255, 71, 42, 121, 255, 71, 43, 122, 255, 71, 44, 123,
    255, 70, 45, 124, 255, 70, 47, 124, 255, 70, 48, 125, 255, 70, 49, 126, 255, 69, 50, 127, 255,
    69, 52, 127, 255, 69, 53, 128, 255, 69, 54, 129, 255, 68, 55, 129, 255, 68, 57, 130, 255, 67,
    58, 131, 255, 67, 59, 131, 255, 67, 60, 132, 255, 66, 61, 132, 255, 66, 62, 133, 255, 66, 64,
    133, 255, 65, 65, 134, 255, 65, 66, 134, 255, 64, 67, 135, 255, 64, 68, 135, 255, 63, 69, 135,
    255, 63, 71, 136, 255, 62, 72, 136, 255, 62, 73, 137, 255, 61, 74, 137, 255, 61, 75, 137, 255,
    61, 76, 137, 255, 60, 77, 138, 255, 60, 78, 138, 255, 59, 80, 138, 255, 59, 81, 138, 255, 58,
    82, 139, 255, 58, 83, 139, 255, 57, 84, 139, 255, 57, 85, 139, 255, 56, 86, 139, 255, 56, 87,
    140, 255, 55, 88, 140, 255, 55, 89, 140, 255, 54, 90, 140, 255, 54, 91, 140, 255, 53, 92, 140,
    255, 53, 93, 140, 255, 52, 94, 141, 255, 52, 95, 141, 255, 51, 96, 141, 255, 51, 97, 141, 255,
    50, 98, 141, 255, 50, 99, 141, 255, 49, 100, 141, 255, 49, 101, 141, 255, 49, 102, 141, 255,
    48, 103, 141, 255, 48, 104, 141, 255, 47, 105, 141, 255, 47, 106, 141, 255, 46, 107, 142, 255,
    46, 108, 142, 255, 46, 109, 142, 255, 45, 110, 142, 255, 45, 111, 142, 255, 44, 112, 142, 255,
    44, 113, 142, 255, 44, 114, 142, 255, 43, 115, 142, 255, 43, 116, 142, 255, 42, 117, 142, 255,
    42, 118, 142, 255, 42, 119, 142, 255, 41, 120, 142, 255, 41, 121, 142, 255, 40, 122, 142, 255,
    40, 122, 142, 255, 40, 123, 142, 255, 39, 124, 142, 255, 39, 125, 142, 255, 39, 126, 142, 255,
    38, 127, 142, 255, 38, 128, 142, 255, 38, 129, 142, 255, 37, 130, 142, 255, 37, 131, 141, 255,
    36, 132, 141, 255, 36, 133, 141, 255, 36, 134, 141, 255, 35, 135, 141, 255, 35, 136, 141, 255,
    35, 137, 141, 255, 34, 137, 141, 255, 34, 138, 141, 255, 34, 139, 141, 255, 33, 140, 141, 255,
    33, 141, 140, 255, 33, 142, 140, 255, 32, 144, 140, 255, 32, 145, 140, 255, 31, 146, 140, 255,
    31, 147, 139, 255, 31, 148, 139, 255, 31, 149, 139, 255, 31, 150, 139, 255, 30, 151, 138, 255,
    30, 152, 138, 255, 30, 153, 138, 255, 30, 153, 138, 255, 30, 154, 137, 255, 30, 155, 137, 255,
    30, 156, 137, 255, 30, 157, 136, 255, 30, 158, 136, 255, 30, 159, 136, 255, 30, 160, 135, 255,
    31, 161, 135, 255, 31, 162, 134, 255, 31, 163, 134, 255, 32, 164, 133, 255, 32, 165, 133, 255,
    33, 166, 133, 255, 33, 167, 132, 255, 34, 167, 132, 255, 35, 168, 131, 255, 35, 169, 130, 255,
    36, 170, 130, 255, 37, 171, 129, 255, 38, 172, 129, 255, 39, 173, 128, 255, 40, 174, 127, 255,
    41, 175, 127, 255, 42, 176, 126, 255, 43, 177, 125, 255, 44, 177, 125, 255, 46, 178, 124, 255,
    47, 179, 123, 255, 48, 180, 122, 255, 50, 181, 122, 255, 51, 182, 121, 255, 53, 183, 120, 255,
    54, 184, 119, 255, 56, 185, 118, 255, 57, 185, 118, 255, 59, 186, 117, 255, 61, 187, 116, 255,
    62, 188, 115, 255, 64, 189, 114, 255, 66, 190, 113, 255, 68, 190, 112, 255, 69, 191, 111, 255,
    71, 192, 110, 255, 73, 193, 109, 255, 75, 194, 108, 255, 77, 194, 107, 255, 79, 195, 105, 255,
    81, 196, 104, 255, 83, 197, 103, 255, 85, 198, 102, 255, 87, 198, 101, 255, 89, 199, 100, 255,
    91, 200, 98, 255, 94, 201, 97, 255, 96, 201, 96, 255, 98, 202, 95, 255, 100, 203, 93, 255, 103,
    204, 92, 255, 105, 204, 91, 255, 107, 205, 89, 255, 109, 206, 88, 255, 112, 206, 86, 255, 114,
    207, 85, 255, 116, 208, 84, 255, 119, 208, 82, 255, 121, 209, 81, 255, 124, 210, 79, 255, 126,
    210, 78, 255, 129, 211, 76, 255, 131, 211, 75, 255, 134, 212, 73, 255, 136, 213, 71, 255, 139,
    213, 70, 255, 141, 214, 68, 255, 144, 214, 67, 255, 146, 215, 65, 255, 149, 215, 63, 255, 151,
    216, 62, 255, 154, 216, 60, 255, 157, 217, 58, 255, 159, 217, 56, 255, 162, 218, 55, 255, 165,
    218, 53, 255, 167, 219, 51, 255, 170, 219, 50, 255, 173, 220, 48, 255, 175, 220, 46, 255, 178,
    221, 44, 255, 181, 221, 43, 255, 183, 221, 41, 255, 186, 222, 39, 255, 189, 222, 38, 255, 191,
    223, 36, 255, 194, 223, 34, 255, 197, 223, 33, 255, 199, 224, 31, 255, 202, 224, 30, 255, 205,
    224, 29, 255, 207, 225, 28, 255, 210, 225, 27, 255, 212, 225, 26, 255, 215, 226, 25, 255, 218,
    226, 24, 255, 220, 226, 24, 255, 223, 227, 24, 255, 225, 227, 24, 255, 228, 227, 24, 255, 231,
    228, 25, 255, 233, 228, 25, 255, 236, 228, 26, 255, 238, 229, 27, 255, 241, 229, 28, 255, 243,
    229, 30, 255, 246, 230, 31, 255, 248, 230, 33, 255, 250, 230, 34, 255, 253, 231, 36, 255,
];

/// PNG compression is a transport option; preserve filtered samples and all chunks.
fn configured_compression(png: Vec<u8>) -> Result<Vec<u8>> {
    use std::io::{Read, Write};
    let level = crate::settings::var("TC_PNG_COMPRESS_LEVEL")
        .unwrap_or_else(|_| "1".into())
        .parse::<u32>()
        .map_err(|_| "invalid PNG compression level")?;
    if level > 9 {
        return Err("invalid PNG compression level".into());
    }
    if level == 1 {
        return Ok(png);
    }
    let mut compressed = Vec::new();
    let mut chunks = Vec::new();
    let mut offset = 8;
    while offset < png.len() {
        let length = u32::from_be_bytes(png[offset..offset + 4].try_into().unwrap()) as usize;
        let end = offset + 12 + length;
        let is_data = &png[offset + 4..offset + 8] == b"IDAT";
        if is_data {
            compressed.extend_from_slice(&png[offset + 8..end - 4]);
        }
        chunks.push((offset, end, is_data));
        offset = end;
    }
    let mut filtered = Vec::new();
    flate2::read::ZlibDecoder::new(compressed.as_slice())
        .read_to_end(&mut filtered)
        .map_err(|e| e.to_string())?;
    let mut encoder = flate2::write::ZlibEncoder::new(Vec::new(), flate2::Compression::new(level));
    encoder.write_all(&filtered).map_err(|e| e.to_string())?;
    let data = encoder.finish().map_err(|e| e.to_string())?;
    let mut out = png[..8].to_vec();
    let mut written = false;
    for (start, end, is_data) in chunks {
        if !is_data {
            out.extend_from_slice(&png[start..end]);
        } else if !written {
            out.extend_from_slice(&(data.len() as u32).to_be_bytes());
            let crc_start = out.len();
            out.extend_from_slice(b"IDAT");
            out.extend_from_slice(&data);
            let crc = crc32fast::hash(&out[crc_start..]);
            out.extend_from_slice(&crc.to_be_bytes());
            written = true;
        }
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reserves_zero_for_transparency_and_uses_even_rounding() {
        let raster = Raster {
            data_type: 7,
            width: 3,
            height: 1,
            values: vec![0.0, 0.5, 1.0],
            mask: vec![true, false, false],
        };
        assert_eq!(stretch_to_u8(&raster, [0.0, 1.0]), vec![0, 128, 255]);
    }

    #[test]
    fn writes_valid_non_square_png() {
        let png = grayscale_png(2, 1, &[0, 255]).unwrap();
        assert_eq!(&png[..8], b"\x89PNG\r\n\x1a\n");
    }

    #[test]
    fn writes_legacy_rgb_transparency() {
        let png = rgb_png(2, 1, [&[0, 1], &[0, 2], &[0, 3]]).unwrap();
        assert_eq!(&png[..8], b"\x89PNG\r\n\x1a\n");
        assert!(png.windows(4).any(|chunk| chunk == b"tRNS"));
    }
}
