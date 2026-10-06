//! Single-band stretch, label, palette, and PNG encoding.

use crate::{Result, raster::Raster};
use png::{BitDepth, ColorType, Encoder};
use std::io::Cursor;
mod deflate;
mod simd;
mod webp;

pub type Rgba = [u8; 4];

#[derive(Clone, Copy)]
pub enum RgbFormat {
    Png,
    Webp(f32),
}

impl RgbFormat {
    pub fn content_type(self) -> &'static str {
        match self {
            Self::Png => "image/png",
            Self::Webp(_) => "image/webp",
        }
    }
}

fn normalized_byte(value: f64, masked: bool, range: [f64; 2]) -> u8 {
    if masked {
        return 0;
    }
    let mut normalized = value - range[0];
    let norm = range[1] - range[0];
    if norm.abs() > 1e-8 {
        normalized *= 1.0 / norm;
    }
    (1.0 + normalized.clamp(0., 1.) * 254.).round_ties_even() as u8
}

pub fn normalized_pixels(raster: &Raster, range: [f64; 2]) -> Vec<u8> {
    let _coloring = crate::telemetry::timer(crate::telemetry::Stage::Coloring);
    if simd::available() {
        let mut pixels = vec![0; raster.values.len().min(raster.mask.len())];
        simd::channel(raster, range, &mut pixels, 1, 0);
        return pixels;
    }
    raster
        .values
        .iter()
        .zip(&raster.mask)
        .map(|(&value, &masked)| normalized_byte(value, masked, range))
        .collect()
}

/// Normalize a native layer channel over any finite positive numeric interval.
pub fn native_normalized_pixels(raster: &Raster, range: [f64; 2]) -> Vec<u8> {
    let _coloring = crate::telemetry::timer(crate::telemetry::Stage::Coloring);
    raster
        .values
        .iter()
        .zip(&raster.mask)
        .map(|(&value, &masked)| {
            if masked {
                0
            } else {
                (1.0 + ((value - range[0]) * (1.0 / (range[1] - range[0]))).clamp(0., 1.) * 254.)
                    .round_ties_even() as u8
            }
        })
        .collect()
}

pub fn stretched_rgb_png(rasters: [&Raster; 3], ranges: [[f64; 2]; 3]) -> Result<Vec<u8>> {
    stretched_rgb_image(rasters, ranges, RgbFormat::Png)
}

pub fn stretched_rgb_image(
    rasters: [&Raster; 3],
    ranges: [[f64; 2]; 3],
    format: RgbFormat,
) -> Result<Vec<u8>> {
    let coloring = crate::telemetry::timer(crate::telemetry::Stage::Coloring);
    let first = rasters[0];
    if simd::available() {
        let mut pixels = vec![0; first.width * first.height * 3];
        for channel in 0..3 {
            simd::channel(rasters[channel], ranges[channel], &mut pixels, 3, channel);
        }
        drop(coloring);
        return rgb_pixels_image(first.width, first.height, &pixels, format);
    }
    let mut pixels = Vec::with_capacity(first.width * first.height * 3);
    for i in 0..first.values.len() {
        for channel in 0..3 {
            pixels.push(normalized_byte(
                rasters[channel].values[i],
                rasters[channel].mask[i],
                ranges[channel],
            ));
        }
    }
    drop(coloring);
    rgb_pixels_image(first.width, first.height, &pixels, format)
}

pub fn stretch_to_u8(raster: &Raster, range: [f64; 2]) -> Vec<u8> {
    let _coloring = crate::telemetry::timer(crate::telemetry::Stage::Coloring);
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
    let _coloring = crate::telemetry::timer(crate::telemetry::Stage::Coloring);
    if !palette.is_empty()
        && palette.len() <= 255
        && palette
            .iter()
            .all(|(v, _)| v.is_finite() && v.fract() == 0.)
    {
        let minimum = palette
            .iter()
            .map(|(v, _)| *v)
            .fold(f64::INFINITY, f64::min);
        let maximum = palette
            .iter()
            .map(|(v, _)| *v)
            .fold(f64::NEG_INFINITY, f64::max);
        let span = maximum - minimum;
        if (0. ..=65535.).contains(&span) {
            let mut labels = vec![0; span as usize + 1];
            for (i, (value, _)) in palette.iter().enumerate() {
                let index = (*value - minimum) as usize;
                if labels[index] == 0 {
                    labels[index] = (i + 1) as u8;
                }
            }
            return raster
                .values
                .iter()
                .zip(&raster.mask)
                .map(|(&value, &masked)| {
                    if masked {
                        return 0;
                    }
                    let delta = value - minimum;
                    if delta >= 0. && delta <= span {
                        let index = delta as usize;
                        if value == minimum + index as f64 {
                            return labels[index];
                        }
                    }
                    0
                })
                .collect();
        }
    }

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

/// Encode explicit native coverage without a color transparency key.
pub fn rgba_png(width: usize, height: usize, pixels: &[u8]) -> Result<Vec<u8>> {
    let _timer = crate::telemetry::timer(crate::telemetry::Stage::Encoding);
    if width.checked_mul(height).and_then(|n| n.checked_mul(4)) != Some(pixels.len()) {
        return Err("RGBA pixels do not match dimensions".into());
    }
    let mut bytes = Vec::new();
    {
        let mut encoder = Encoder::new(&mut bytes, width as u32, height as u32);
        encoder.set_color(ColorType::Rgba);
        encoder.set_depth(BitDepth::Eight);
        let mut writer = encoder.write_header().map_err(|e| e.to_string())?;
        writer.write_image_data(pixels).map_err(|e| e.to_string())?;
    }
    Ok(bytes)
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
    rgb_image(width, height, channels, RgbFormat::Png)
}

pub fn rgb_image(
    width: usize,
    height: usize,
    channels: [&[u8]; 3],
    format: RgbFormat,
) -> Result<Vec<u8>> {
    let coloring = crate::telemetry::timer(crate::telemetry::Stage::Coloring);
    let pixel_count = width * height;
    if channels.iter().any(|channel| channel.len() != pixel_count) {
        return Err("RGB channel length does not match dimensions".into());
    }
    let mut pixels = Vec::with_capacity(pixel_count * 3);
    for index in 0..pixel_count {
        pixels.extend_from_slice(&[channels[0][index], channels[1][index], channels[2][index]]);
    }
    drop(coloring);
    rgb_pixels_image(width, height, &pixels, format)
}

fn rgb_pixels_image(
    width: usize,
    height: usize,
    pixels: &[u8],
    format: RgbFormat,
) -> Result<Vec<u8>> {
    let _timer = crate::telemetry::timer(crate::telemetry::Stage::Encoding);
    if let RgbFormat::Webp(quality) = format {
        return webp::encode(width, height, pixels, quality);
    }
    let level = compression_level()?;
    if level != 1 {
        return compressed_png(width, height, pixels, 2, None, &[0; 6], level);
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
            .write_image_data(pixels)
            .map_err(|error| format!("could not encode RGB PNG: {error}"))?;
    }
    Ok(output)
}

fn encode_png(
    width: usize,
    height: usize,
    pixels: &[u8],
    palette: Option<(Vec<u8>, Vec<u8>)>,
) -> Result<Vec<u8>> {
    let _timer = crate::telemetry::timer(crate::telemetry::Stage::Encoding);
    if pixels.len() != width * height {
        return Err("PNG pixel length does not match dimensions".into());
    }
    let level = compression_level()?;
    if level != 1 {
        return match palette {
            Some((colors, alpha)) => {
                compressed_png(width, height, pixels, 3, Some(&colors), &alpha, level)
            }
            None => compressed_png(width, height, pixels, 0, None, &[0, 0], level),
        };
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
    Ok(output)
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

fn compression_level() -> Result<u32> {
    let level = crate::settings::var("TC_PNG_COMPRESS_LEVEL")
        .unwrap_or_else(|_| "1".into())
        .parse::<u32>()
        .map_err(|_| "invalid PNG compression level")?;
    if level > 9 {
        return Err("invalid PNG compression level".into());
    }
    Ok(level)
}

fn compressed_png(
    width: usize,
    height: usize,
    pixels: &[u8],
    color: u8,
    palette: Option<&[u8]>,
    transparency: &[u8],
    level: u32,
) -> Result<Vec<u8>> {
    use std::io::Write;
    let channels = if color == 2 { 3 } else { 1 };
    if width == 0 || height == 0 {
        return Err("PNG dimensions must be positive".into());
    }
    let stride = width * channels;
    if pixels.len() != stride * height {
        return Err("PNG pixel length does not match dimensions".into());
    }
    let mut filtered = Vec::with_capacity((stride + 1) * height);
    for source in pixels.chunks_exact(stride) {
        filtered.push(1);
        for (i, &value) in source.iter().enumerate() {
            filtered.push(value.wrapping_sub(if i >= channels {
                source[i - channels]
            } else {
                0
            }));
        }
    }
    let native = if crate::settings::flag("TC_PNG_LIBDEFLATE", true)? {
        deflate::compress(&filtered, level)
    } else {
        None
    };
    let data = if let Some(data) = native {
        data
    } else {
        let mut encoder =
            flate2::write::ZlibEncoder::new(Vec::new(), flate2::Compression::new(level));
        encoder.write_all(&filtered).map_err(|e| e.to_string())?;
        encoder.finish().map_err(|e| e.to_string())?
    };
    let mut out = b"\x89PNG\r\n\x1a\n".to_vec();
    let mut header = Vec::with_capacity(13);
    header.extend_from_slice(&(width as u32).to_be_bytes());
    header.extend_from_slice(&(height as u32).to_be_bytes());
    header.extend_from_slice(&[8, color, 0, 0, 0]);
    png_chunk(&mut out, b"IHDR", &header);
    if let Some(palette) = palette {
        png_chunk(&mut out, b"PLTE", palette);
    }
    png_chunk(&mut out, b"tRNS", transparency);
    png_chunk(&mut out, b"IDAT", &data);
    png_chunk(&mut out, b"IEND", &[]);
    Ok(out)
}

fn png_chunk(output: &mut Vec<u8>, name: &[u8; 4], data: &[u8]) {
    output.extend_from_slice(&(data.len() as u32).to_be_bytes());
    let start = output.len();
    output.extend_from_slice(name);
    output.extend_from_slice(data);
    let crc = crc32fast::hash(&output[start..]);
    output.extend_from_slice(&crc.to_be_bytes());
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fused_normalization_preserves_stretch_rounding_masks_and_precision() {
        let raster = Raster {
            data_type: 7,
            width: 10,
            height: 1,
            values: vec![
                -1e20,
                0.,
                0.5,
                1.,
                1e20,
                f64::NAN,
                f64::INFINITY,
                -3.,
                1. / 508.,
                3. / 508.,
            ],
            mask: vec![
                false, false, false, false, false, false, false, true, false, false,
            ],
        };
        for range in [[0., 1.], [1., 1.], [1., -1.], [-1e30, 1e30], [0., 1e-9]] {
            let normalized = crate::color::normalize(&raster, range);
            assert_eq!(
                normalized_pixels(&raster, range),
                stretch_to_u8(&normalized, [0., 1.])
            );
        }
    }

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
    fn dense_category_lookup_preserves_first_match_and_float_equality() {
        let values = vec![
            -1.,
            -0.,
            0.,
            0.25,
            1.,
            2.,
            95.,
            f64::NAN,
            f64::INFINITY,
            1e18,
            1e18 + 1024.,
        ];
        let raster = Raster {
            width: values.len(),
            height: 1,
            data_type: 7,
            mask: (0..values.len()).map(|i| i == 5).collect(),
            values,
        };
        for labels in [
            vec![-1., 0., -0., 1., 2., 95.],
            vec![0.25, 1.],
            vec![1e18, 1e18 + 1024.],
            vec![-1e20, 1e20],
            vec![],
        ] {
            let palette = labels
                .into_iter()
                .map(|v| (v, [1, 2, 3, 255]))
                .collect::<Vec<_>>();
            let expected = raster
                .values
                .iter()
                .zip(&raster.mask)
                .map(|(&v, &m)| {
                    if m {
                        0
                    } else {
                        palette
                            .iter()
                            .position(|(label, _)| v == *label)
                            .map_or(0, |i| (i + 1) as u8)
                    }
                })
                .collect::<Vec<_>>();
            assert_eq!(label_values(&raster, &palette), expected);
        }
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

    #[test]
    fn every_compression_level_preserves_rgb_palette_and_grayscale_samples() {
        for level in 0..=9 {
            crate::settings::scope(
                serde_json::json!({"PNG_COMPRESS_LEVEL":level})
                    .as_object()
                    .unwrap()
                    .clone(),
                || {
                    for png in [
                        grayscale_png(3, 2, &[0, 1, 255, 12, 123, 200]).unwrap(),
                        palette_png(3, 2, &[0, 1, 2, 2, 1, 0], &[[1, 2, 3, 128], [4, 5, 6, 255]])
                            .unwrap(),
                        rgb_png(3, 2, [&[0, 1, 255, 12, 123, 200]; 3]).unwrap(),
                    ] {
                        let decoder = png::Decoder::new(Cursor::new(&png));
                        let mut reader = decoder.read_info().unwrap();
                        let mut buffer = vec![0; reader.output_buffer_size()];
                        let info = reader.next_frame(&mut buffer).unwrap();
                        assert_eq!((info.width, info.height), (3, 2));
                        let expected: Vec<_> = match info.color_type {
                            ColorType::Rgb => [0, 1, 255, 12, 123, 200]
                                .iter()
                                .flat_map(|&v| [v; 3])
                                .collect(),
                            ColorType::Indexed => vec![0, 1, 2, 2, 1, 0],
                            _ => vec![0, 1, 255, 12, 123, 200],
                        };
                        assert_eq!(&buffer[..info.buffer_size()], &expected);
                        assert!(reader.info().trns.is_some());
                    }
                },
            );
        }
    }
}
