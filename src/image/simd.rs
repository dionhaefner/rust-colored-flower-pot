//! Runtime-dispatched normalization with the scalar f64 operation order.

use super::normalized_byte;
use crate::raster::Raster;

pub(super) fn available() -> bool {
    if !crate::settings::flag("TC_SIMD_RENDER", true).unwrap_or(false) {
        return false;
    }
    #[cfg(target_arch = "x86_64")]
    {
        std::is_x86_feature_detected!("avx2")
    }
    #[cfg(not(target_arch = "x86_64"))]
    false
}

pub(super) fn channel(
    raster: &Raster,
    range: [f64; 2],
    output: &mut [u8],
    stride: usize,
    offset: usize,
) {
    #[cfg(target_arch = "x86_64")]
    if std::is_x86_feature_detected!("avx2") {
        // Both the CPU check and the slice lengths bound every vector load.
        unsafe {
            avx2(raster, range, output, stride, offset);
        }
        return;
    }
    for (i, (&value, &masked)) in raster.values.iter().zip(&raster.mask).enumerate() {
        output[i * stride + offset] = normalized_byte(value, masked, range);
    }
}

#[cfg(target_arch = "x86_64")]
#[target_feature(enable = "avx2")]
unsafe fn avx2(raster: &Raster, range: [f64; 2], output: &mut [u8], stride: usize, offset: usize) {
    use std::arch::x86_64::*;
    unsafe {
        let norm = range[1] - range[0];
        let inverse = if norm.abs() > 1e-8 { 1. / norm } else { 1. };
        let start = _mm256_set1_pd(range[0]);
        let scale = _mm256_set1_pd(inverse);
        let zero = _mm256_setzero_pd();
        let one = _mm256_set1_pd(1.);
        let byte_scale = _mm256_set1_pd(254.);
        let len = raster.values.len().min(raster.mask.len());
        let mut i = 0;
        while i + 4 <= len {
            let value = _mm256_mul_pd(
                _mm256_sub_pd(_mm256_loadu_pd(raster.values.as_ptr().add(i)), start),
                scale,
            );
            let nan = _mm256_cmp_pd::<_CMP_UNORD_Q>(value, value);
            let clipped = _mm256_min_pd(_mm256_max_pd(value, zero), one);
            let bytes = _mm256_add_pd(one, _mm256_mul_pd(clipped, byte_scale));
            let rounded =
                _mm256_round_pd::<{ _MM_FROUND_TO_NEAREST_INT | _MM_FROUND_NO_EXC }>(bytes);
            let integers = _mm256_cvttpd_epi32(_mm256_andnot_pd(nan, rounded));
            let mut lanes = [0i32; 4];
            _mm_storeu_si128(lanes.as_mut_ptr().cast(), integers);
            for lane in 0..4 {
                output[(i + lane) * stride + offset] = if raster.mask[i + lane] {
                    0
                } else {
                    lanes[lane] as u8
                };
            }
            i += 4;
        }
        for i in i..len {
            output[i * stride + offset] = normalized_byte(raster.values[i], raster.mask[i], range);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn vector_normalization_matches_scalar_ties_nonfinite_masks_and_extreme_ranges() {
        if !available() {
            return;
        }
        let mut values = vec![
            -0.,
            0.,
            1. / 508.,
            3. / 508.,
            f64::NAN,
            f64::INFINITY,
            f64::NEG_INFINITY,
        ];
        let mut bits = 0x4321890abcdefu64;
        for _ in 0..10003 {
            bits ^= bits << 13;
            bits ^= bits >> 7;
            bits ^= bits << 17;
            values.push(f64::from_bits(bits));
        }
        let raster = Raster {
            data_type: 7,
            width: values.len(),
            height: 1,
            mask: (0..values.len()).map(|i| i % 7 == 0).collect(),
            values,
        };
        for range in [
            [0., 1.],
            [1., 1.],
            [1., -1.],
            [0., 1e-9],
            [-1e30, 1e30],
            [-1e308, 1e308],
            [f64::NAN, 1.],
        ] {
            let expected = raster
                .values
                .iter()
                .zip(&raster.mask)
                .map(|(&v, &m)| normalized_byte(v, m, range))
                .collect::<Vec<_>>();
            let mut actual = vec![17; raster.values.len() * 3];
            channel(&raster, range, &mut actual, 3, 1);
            assert_eq!(
                actual
                    .chunks_exact(3)
                    .map(|pixel| pixel[1])
                    .collect::<Vec<_>>(),
                expected
            );
            assert!(
                actual
                    .chunks_exact(3)
                    .all(|pixel| pixel[0] == 17 && pixel[2] == 17)
            );
        }
    }
}
