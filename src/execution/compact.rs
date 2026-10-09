//! Lossless private cache storage with f64 samples for public reads.

use crate::raster::Raster;
use std::sync::{Arc, Mutex, Weak};

macro_rules! samples {
    ($( $variant:ident($type:ty) = $dtype:literal ),*) => {
        enum Samples { $( $variant(Vec<$type>), )* }
        impl Samples {
            fn pack(raster: &Raster) -> Option<Self> {
                match raster.data_type {
                    $( $dtype if raster.values.iter().all(|&value| {
                        ((value as $type) as f64).to_bits() == value.to_bits()
                    }) => Some(Self::$variant(raster.values.iter().map(|&value| value as $type).collect())), )*
                    _ => None,
                }
            }
            fn unpack(&self) -> Vec<f64> {
                match self { $( Self::$variant(values) => values.iter().map(|&value| value as f64).collect(), )* }
            }
            fn bytes(&self) -> usize {
                match self { $( Self::$variant(values) => values.capacity() * std::mem::size_of::<$type>(), )* }
            }
        }
    }
}
samples!(
    Byte(u8) = 1,
    UInt16(u16) = 2,
    Int16(i16) = 3,
    UInt32(u32) = 4,
    Int32(i32) = 5,
    Float32(f32) = 6,
    UInt64(u64) = 12,
    Int64(i64) = 13,
    Int8(i8) = 14
);

pub(super) struct Compact {
    width: usize,
    height: usize,
    data_type: i32,
    samples: Samples,
    mask: Vec<u64>,
    expanded: Mutex<Weak<Raster>>,
}

#[derive(Clone)]
pub(super) enum CachedRaster {
    Full(Arc<Raster>),
    Compact(Arc<Compact>),
}

impl CachedRaster {
    pub fn new(raster: Arc<Raster>, compact: bool) -> Self {
        if compact {
            if let Some(samples) = Samples::pack(&raster) {
                let mut mask = vec![0; raster.mask.len().div_ceil(64)];
                for (i, &masked) in raster.mask.iter().enumerate() {
                    if masked {
                        mask[i / 64] |= 1 << (i % 64);
                    }
                }
                let compact = Self::Compact(Arc::new(Compact {
                    width: raster.width,
                    height: raster.height,
                    data_type: raster.data_type,
                    samples,
                    mask,
                    expanded: Mutex::new(Arc::downgrade(&raster)),
                }));
                if compact.bytes() < raster.values.capacity() * 8 + raster.mask.capacity() {
                    return compact;
                }
            }
        }
        Self::Full(raster)
    }

    pub fn same(&self, other: &Self) -> bool {
        match (self, other) {
            (Self::Full(a), Self::Full(b)) => Arc::ptr_eq(a, b),
            (Self::Compact(a), Self::Compact(b)) => Arc::ptr_eq(a, b),
            _ => false,
        }
    }

    pub fn bytes(&self) -> usize {
        match self {
            Self::Full(raster) => raster.values.capacity() * 8 + raster.mask.capacity(),
            Self::Compact(raster) => raster.samples.bytes() + raster.mask.capacity() * 8,
        }
    }

    pub fn raster(&self) -> Arc<Raster> {
        match self {
            Self::Full(raster) => raster.clone(),
            Self::Compact(raster) => {
                let mut expanded = raster.expanded.lock().expect("cached raster expansion");
                if let Some(raster) = expanded.upgrade() {
                    return raster;
                }
                let values = raster.samples.unpack();
                let mask = (0..values.len())
                    .map(|i| raster.mask[i / 64] & (1 << (i % 64)) != 0)
                    .collect();
                let result = Arc::new(Raster {
                    width: raster.width,
                    height: raster.height,
                    data_type: raster.data_type,
                    values,
                    mask,
                });
                *expanded = Arc::downgrade(&result);
                result
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn compact_samples_preserve_bits_masks_and_dtype_including_hidden_values() {
        for dtype in [1, 2, 3, 4, 5, 6, 7, 12, 13, 14] {
            for values in [
                (0..130).map(|i| (i % 100) as f64).collect::<Vec<_>>(),
                vec![
                    -0.,
                    f64::NAN,
                    f64::from_bits(0x7ff8000000004321),
                    f64::INFINITY,
                    f64::NEG_INFINITY,
                    -3.5,
                    f64::MAX,
                    65536.,
                    0.1,
                    (u64::MAX as f64),
                    i64::MIN as f64,
                ],
                vec![
                    f32::MIN as f64,
                    f32::MAX as f64,
                    f32::from_bits(1) as f64,
                    -0.,
                    0.125,
                ],
            ] {
                let raster = Arc::new(Raster {
                    width: values.len(),
                    height: 1,
                    data_type: dtype,
                    mask: (0..values.len()).map(|i| i % 3 == 0).collect(),
                    values,
                });
                let cache = CachedRaster::new(raster.clone(), true);
                let expected_bits = raster
                    .values
                    .iter()
                    .map(|v| v.to_bits())
                    .collect::<Vec<_>>();
                let expected_mask = raster.mask.clone();
                let expected_shape = (raster.width, raster.height, dtype);
                let expected_bytes = raster.values.capacity() * 8 + raster.mask.capacity();
                drop(raster);
                let actual = cache.raster();
                assert_eq!(
                    actual
                        .values
                        .iter()
                        .map(|v| v.to_bits())
                        .collect::<Vec<_>>(),
                    expected_bits
                );
                assert_eq!(actual.mask, expected_mask);
                assert_eq!(
                    (actual.width, actual.height, actual.data_type),
                    expected_shape
                );
                assert!(cache.bytes() <= expected_bytes);
            }
        }
    }
}
