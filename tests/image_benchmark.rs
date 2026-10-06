//! Isolate lossless PNG encoding using captured, provenance-recorded navigation tiles.

use rcfp_core::{image, settings};
use std::{fs, io::Cursor, path::PathBuf, time::Instant};

fn decode(bytes: &[u8]) -> (usize, usize, Vec<u8>) {
    let mut decoder = png::Decoder::new(Cursor::new(bytes));
    decoder.set_transformations(png::Transformations::EXPAND);
    let mut reader = decoder.read_info().unwrap();
    let mut pixels = vec![0; reader.output_buffer_size()];
    let info = reader.next_frame(&mut pixels).unwrap();
    let mut rgba = Vec::new();
    for pixel in pixels[..info.buffer_size()].chunks_exact(info.color_type.samples()) {
        match info.color_type {
            png::ColorType::Rgb => rgba.extend_from_slice(&[pixel[0], pixel[1], pixel[2], 255]),
            png::ColorType::Rgba => rgba.extend_from_slice(pixel),
            png::ColorType::Grayscale => {
                rgba.extend_from_slice(&[pixel[0], pixel[0], pixel[0], 255])
            }
            png::ColorType::GrayscaleAlpha => {
                rgba.extend_from_slice(&[pixel[0], pixel[0], pixel[0], pixel[1]])
            }
            _ => panic!("unexpanded PNG"),
        }
    }
    (info.width as usize, info.height as usize, rgba)
}

#[test]
#[ignore = "requires captured navigation images and an explicit timing run"]
fn png_encoding_from_navigation_images() {
    let manifest =
        PathBuf::from(std::env::var_os("RCFP_IMAGE_BENCHMARK_INPUTS").expect("input manifest"));
    let paths: Vec<PathBuf> = serde_json::from_slice(&fs::read(manifest).unwrap()).unwrap();
    let frames = paths
        .iter()
        .map(|path| decode(&fs::read(path).unwrap()))
        .collect::<Vec<_>>();
    assert!(!frames.is_empty());
    let inputs = paths
        .iter()
        .map(|path| {
            serde_json::json!({"file": path, "crc32": crc32fast::hash(&fs::read(path).unwrap())})
        })
        .collect::<Vec<_>>();
    for level in [1, 6] {
        for native in [false, true] {
            if level == 1 && native {
                continue;
            }
            let config = serde_json::json!({"PNG_LIBDEFLATE":native,"PNG_COMPRESS_LEVEL":level})
                .as_object()
                .unwrap()
                .clone();
            settings::scope(config, || {
                let mut total_ns = 0;
                let mut total_bytes = 0;
                for (width, height, rgba) in &frames {
                    let channels: [Vec<u8>; 3] = std::array::from_fn(|channel| {
                        rgba.chunks_exact(4)
                            .map(|pixel| if pixel[3] == 0 { 0 } else { pixel[channel] })
                            .collect()
                    });
                    let encode = || {
                        image::rgb_png(*width, *height, [&channels[0], &channels[1], &channels[2]])
                            .unwrap()
                    };
                    let encoded = encode();
                    let (_, _, actual) = decode(&encoded);
                    for (i, pixel) in actual.chunks_exact(4).enumerate() {
                        let expected = [channels[0][i], channels[1][i], channels[2][i]];
                        assert_eq!(&pixel[..3], &expected);
                        assert_eq!(pixel[3], if expected == [0, 0, 0] { 0 } else { 255 });
                    }
                    let started = Instant::now();
                    for _ in 0..100 {
                        total_bytes += std::hint::black_box(encode()).len();
                    }
                    total_ns += started.elapsed().as_nanos();
                }
                println!(
                    "{}",
                    serde_json::json!({"libdeflate_requested":native,"level":level,"frames":frames.len(),"inputs":inputs,"iterations":100,"nanoseconds":total_ns,"bytes":total_bytes})
                );
            });
        }
    }
}
