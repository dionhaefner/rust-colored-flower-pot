//! Private TIFF boundary checks with independently acquired expected arrays.
use super::*;
use serde_json::Value;
use std::io::Write;

fn matrix() -> Value {
    let path = std::env::var("RCFP_TIFF_TEST_MATRIX").expect("prepare with tests/tiff_backend.py");
    serde_json::from_slice(&std::fs::read(path).unwrap()).unwrap()
}

#[test]
#[ignore = "requires the pinned fixture matrix prepared by tests/tiff_backend.py"]
fn tiff_windows_match_independent_arrays() {
    let matrix = matrix();
    let backend = Backend::new(true, Duration::from_secs(60));
    let gdal_only = crate::settings::scope(
        serde_json::from_value(serde_json::json!({"ASYNC_TIFF": false})).unwrap(),
        crate::raster::Gdal::load,
    )
    .unwrap();
    let mixed = crate::raster::Gdal::load().unwrap();
    for case in matrix["windows"].as_array().unwrap() {
        let name = case["id"].as_str().unwrap();
        let source = backend
            .source(Path::new(case["path"].as_str().unwrap()))
            .unwrap_or_else(|| panic!("header fallback: {name}"));
        let window = serde_json::from_value(case["window"].clone()).unwrap();
        let bands: Vec<usize> = serde_json::from_value(case["bands"].clone()).unwrap();
        let mode = match case["mode"].as_str().unwrap() {
            "native" => Validity::Native,
            "asset" => Validity::Asset,
            _ => panic!("unknown mode"),
        };
        let rasters = backend
            .window(
                &source,
                case["level"].as_u64().unwrap() as usize,
                window,
                &bands,
                mode,
            )
            .unwrap_or_else(|| panic!("pixel fallback: {name}"));
        let values = std::fs::read(case["values"].as_str().unwrap()).unwrap();
        let masks = std::fs::read(case["mask"].as_str().unwrap()).unwrap();
        let dtypes: Vec<i32> = serde_json::from_value(case["dtypes"].clone()).unwrap();
        let mut offset = 0;
        for (raster, dtype) in rasters.iter().zip(dtypes) {
            assert_eq!(raster.data_type, dtype, "{name}: dtype");
            for (&actual, &masked) in raster.values.iter().zip(&raster.mask) {
                let expected =
                    f64::from_le_bytes(values[offset * 8..offset * 8 + 8].try_into().unwrap());
                assert!(
                    actual.to_bits() == expected.to_bits() || actual.is_nan() && expected.is_nan(),
                    "{name}: value {offset}: {actual} vs {expected}"
                );
                assert_eq!(masked, masks[offset] != 0, "{name}: mask {offset}");
                offset += 1;
            }
        }
        assert_eq!(offset, masks.len(), "{name}: sample count");
        {
            let step = 128. * 2f64.powi(case["level"].as_u64().unwrap() as i32);
            let west = 1260000. + window[0] as f64 * step;
            let north = 5680000. - window[1] as f64 * step;
            let requests = bands
                .iter()
                .map(|&band| ReadRequest {
                    grid: matches!(mode, Validity::Asset).then_some(crate::layers::GridRead {
                        epsg: 3857,
                        resampling: crate::layers::Resampling::Nearest,
                        revision: [9; 32],
                    }),
                    band,
                    bounds_mercator: Some([
                        west,
                        north - window[3] as f64 * step,
                        west + window[2] as f64 * step,
                        north,
                    ]),
                    size: [window[3], window[2]],
                    preserve_values: true,
                })
                .collect::<Vec<_>>();
            let path = Path::new(case["path"].as_str().unwrap());
            if case["public_supported"] == false {
                assert!(
                    backend.read(path, &requests, 0).is_none(),
                    "{name}: asset overview fallback"
                );
                continue;
            }
            assert!(
                backend.read(path, &requests, 0).is_some(),
                "{name}: native selection"
            );
            let actual = mixed.read_bands(path, &requests).unwrap();
            let expected = gdal_only.read_bands(path, &requests).unwrap();
            for (actual, expected) in actual.iter().zip(expected) {
                assert_eq!(actual.data_type, expected.data_type, "{name}: public dtype");
                assert_eq!(actual.mask, expected.mask, "{name}: public mask");
                assert!(
                    actual
                        .values
                        .iter()
                        .zip(expected.values)
                        .all(|(a, b)| a.to_bits() == b.to_bits() || a.is_nan() && b.is_nan()),
                    "{name}: public values"
                );
            }
        }
    }
}

#[test]
#[ignore = "requires the pinned fixture matrix prepared by tests/tiff_backend.py"]
fn tiff_exact_grids_and_fallbacks() {
    let matrix = matrix();
    let backend = Backend::new(true, Duration::from_secs(60));
    let gdal_only = crate::settings::scope(
        serde_json::from_value(serde_json::json!({"ASYNC_TIFF": false})).unwrap(),
        crate::raster::Gdal::load,
    )
    .unwrap();
    let mixed = crate::raster::Gdal::load().unwrap();
    for case in matrix["grids"].as_array().unwrap() {
        let request = ReadRequest {
            bounds_mercator: serde_json::from_value(case["bounds"].clone()).unwrap(),
            size: serde_json::from_value(case["size"].clone()).unwrap(),
            preserve_values: case["nearest"].as_bool().unwrap(),
            ..Default::default()
        };
        let source = backend
            .source(Path::new(case["path"].as_str().unwrap()))
            .unwrap();
        let path = Path::new(case["path"].as_str().unwrap());
        assert_eq!(
            mixed.inspect(path).unwrap(),
            gdal_only.inspect(path).unwrap()
        );
        assert_eq!(
            mixed.band_count(path).unwrap(),
            gdal_only.band_count(path).unwrap()
        );
        assert_eq!(
            mixed.band_nodata(path, 1).unwrap(),
            gdal_only.band_nodata(path, 1).unwrap()
        );
        let selected = source.exact_grid(&request, 1);
        if case["supported"] == true {
            let (level, window) =
                selected.unwrap_or_else(|| panic!("grid fallback: {}", case["id"]));
            assert_eq!(level, case["level"].as_u64().unwrap() as usize);
            assert_eq!(
                window,
                serde_json::from_value::<[usize; 4]>(case["window"].clone()).unwrap()
            );
            if level > 0 {
                let mut duplicate = Source {
                    reader: source.reader.clone(),
                    ifds: source.ifds.clone(),
                    levels: source.levels.clone(),
                    header_bytes: source.header_bytes,
                };
                duplicate
                    .ifds
                    .push(source.ifds[source.levels[level]].clone());
                duplicate.levels.push(duplicate.ifds.len() - 1);
                assert!(duplicate.exact_grid(&request, 1).is_none());
            }
            assert!(backend.read(path, &[request], 1).is_some());
            let actual = mixed.read(path, request).unwrap();
            let expected = gdal_only.read(path, request).unwrap();
            assert_eq!(
                actual.data_type, expected.data_type,
                "{}: dtype",
                case["id"]
            );
            assert_eq!(actual.values, expected.values, "{}: values", case["id"]);
            assert_eq!(actual.mask, expected.mask, "{}: mask", case["id"]);
        } else {
            assert!(selected.is_none(), "unexpected grid: {}", case["id"]);
        }
    }
    for case in matrix["fallbacks"].as_array().unwrap() {
        let path = Path::new(case["path"].as_str().unwrap());
        if let Some(source) = backend.source(path) {
            let window = if case["id"] == "changing" {
                [460, 460, 16, 16]
            } else {
                [0, 0, 16, 16]
            };
            assert!(
                backend
                    .window(&source, 0, window, &[1], Validity::Native)
                    .is_none(),
                "unexpected decode: {}",
                case["id"]
            );
        }
    }
    let path = Path::new(matrix["generation_source"].as_str().unwrap());
    let first = backend.source(path).unwrap();
    assert!(gdal_only.tiff.source(path).is_none());
    let legacy = crate::settings::scope(
        serde_json::from_value(
            serde_json::json!({"ASYNC_TIFF": true, "COMPAT_PROFILE": "terracotta-0.10.1"}),
        )
        .unwrap(),
        crate::raster::Gdal::load,
    )
    .unwrap();
    assert!(legacy.tiff.source(path).is_none());
    let second = backend.source(path).unwrap();
    assert!(Arc::ptr_eq(&first, &second));
    let sidecar = path.with_extension("tfw");
    std::fs::write(&sidecar, b"128\n0\n0\n-128\n0\n0\n").unwrap();
    assert!(backend.source(path).is_none());
    std::fs::remove_file(sidecar).unwrap();
    let mut file = std::fs::OpenOptions::new().append(true).open(path).unwrap();
    file.write_all(&[0]).unwrap();
    assert!(!Arc::ptr_eq(&first, &backend.source(path).unwrap()));
    let a = backend.source_revision(path, Some([1; 32])).unwrap();
    let b = backend.source_revision(path, Some([2; 32])).unwrap();
    assert!(!Arc::ptr_eq(&a, &b));
}

#[test]
fn tiff_compression_limits_and_invalid_lzw() {
    use flate2::{Compression as Level, write::ZlibEncoder};
    let mut encoder = ZlibEncoder::new(Vec::new(), Level::fast());
    encoder.write_all(&vec![7; 65536]).unwrap();
    let registry = decoder::registry(Compression::Deflate, 32);
    assert!(
        registry.as_ref()[&Compression::Deflate]
            .decode_tile(
                encoder.finish().unwrap().into(),
                PhotometricInterpretation::BlackIsZero,
                None,
                1,
                8,
                None
            )
            .is_err()
    );
    let registry = decoder::registry(Compression::LZW, 32);
    assert!(
        registry.as_ref()[&Compression::LZW]
            .decode_tile(
                bytes::Bytes::from_static(&[255, 255, 255]),
                PhotometricInterpretation::BlackIsZero,
                None,
                1,
                8,
                None
            )
            .is_err()
    );
}

#[test]
fn tiff_directory_allocation_limits() {
    // Malformed directory counts have no real-raster analogue.
    for (directories, kind, values) in [(u64::MAX, 1u16, 1u64), (1, 1, u64::MAX), (1, 99, 1)] {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("invalid.tif");
        let mut bytes = b"II".to_vec();
        bytes.extend(43u16.to_le_bytes());
        bytes.extend(8u16.to_le_bytes());
        bytes.extend(0u16.to_le_bytes());
        bytes.extend(16u64.to_le_bytes());
        bytes.extend(directories.to_le_bytes());
        bytes.extend(256u16.to_le_bytes());
        bytes.extend(kind.to_le_bytes());
        bytes.extend(values.to_le_bytes());
        bytes.extend(0u64.to_le_bytes());
        bytes.extend(0u64.to_le_bytes());
        std::fs::write(&path, bytes).unwrap();
        assert!(Backend::new(true, Duration::ZERO).source(&path).is_none());
    }
}

#[test]
fn tiff_range_retention_is_bounded_and_source_owned() {
    let mut ranges = ranges::Ranges::new();
    ranges.limit(8);
    let a = Arc::new(());
    let b = Arc::new(());
    ranges.insert(a.clone(), 0..8, bytes::Bytes::from_static(b"abcdefgh"));
    assert_eq!(ranges.get(&a, &(2..4)).unwrap().as_ref(), b"cd");
    assert!(ranges.get(&b, &(2..4)).is_none());
    ranges.insert(b.clone(), 0..4, bytes::Bytes::from_static(b"1234"));
    assert!(ranges.get(&a, &(0..8)).is_none());
    ranges.remove(&b);
    assert!(ranges.get(&b, &(0..4)).is_none());
    ranges.insert(a.clone(), 0..8, bytes::Bytes::from_static(b"abcdefgh"));
    ranges.limit(0);
    assert!(ranges.get(&a, &(0..8)).is_none());
}

#[test]
fn tiff_invalid_block_layouts_require_fallback() {
    use async_tiff::{TagValue, reader::Endianness, tags::Tag};
    // Invalid dimensions and absent block tables have no real-raster analogue.
    for (width, height) in [(0, 16), (16, 0), (1, 16), (16, 16)] {
        let tags = std::collections::HashMap::from([
            (Tag::ImageWidth, TagValue::Unsigned(16)),
            (Tag::ImageLength, TagValue::Unsigned(16)),
            (Tag::BitsPerSample, TagValue::Short(8)),
            (Tag::SamplesPerPixel, TagValue::Short(1)),
            (Tag::PhotometricInterpretation, TagValue::Short(1)),
            (Tag::TileWidth, TagValue::Unsigned(width)),
            (Tag::TileLength, TagValue::Unsigned(height)),
        ]);
        let ifd = ImageFileDirectory::from_tags(tags, Endianness::LittleEndian).unwrap();
        assert!(check_pixels(&ifd).is_err());
    }
}
