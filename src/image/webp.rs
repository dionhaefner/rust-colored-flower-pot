//! Optional display encoding through libwebp's stable simple C API.
use crate::Result;
use libloading::Library;
use std::{ffi::c_void, path::PathBuf, sync::OnceLock};

type Encode = unsafe extern "C" fn(*const u8, i32, i32, i32, f32, *mut *mut u8) -> usize;
type Free = unsafe extern "C" fn(*mut c_void);
struct Codec {
    _library: Library,
    encode: Encode,
    free: Free,
}
static CODEC: OnceLock<Result<Codec>> = OnceLock::new();

fn load() -> Result<Codec> {
    let mut paths = Vec::new();
    if let Some(gdal) = crate::settings::var_os("TERRACOTTA_GDAL_LIBRARY") {
        if let Some(directory) = PathBuf::from(gdal).parent() {
            if let Ok(entries) = std::fs::read_dir(directory) {
                paths.extend(
                    entries
                        .filter_map(|entry| entry.ok())
                        .map(|entry| entry.path())
                        .filter(|path| {
                            let name = path.file_name().unwrap_or_default().to_string_lossy();
                            (name.starts_with("libwebp.")
                                || name.starts_with("libwebp-")
                                || name == "webp.dll"
                                || name == "libwebp.dll")
                                && path.is_file()
                        }),
                );
            }
        }
    }
    paths.sort();
    paths.push(libloading::library_filename("webp").into());
    for path in paths {
        unsafe {
            if let Ok(library) = Library::new(path) {
                let encode = *library
                    .get::<Encode>(b"WebPEncodeRGBA\0")
                    .map_err(|e| e.to_string())?;
                let free = *library
                    .get::<Free>(b"WebPFree\0")
                    .map_err(|e| e.to_string())?;
                return Ok(Codec {
                    _library: library,
                    encode,
                    free,
                });
            }
        }
    }
    Err("WebP output requires a libwebp encoder in the GDAL runtime".into())
}

pub(super) fn encode(width: usize, height: usize, pixels: &[u8], quality: f32) -> Result<Vec<u8>> {
    if width == 0
        || height == 0
        || width > 16383
        || height > 16383
        || pixels.len() != width * height * 3
        || !quality.is_finite()
        || !(0.0..=100.0).contains(&quality)
    {
        return Err("invalid WebP dimensions, pixels or quality".into());
    }
    let codec = CODEC.get_or_init(load).as_ref().map_err(Clone::clone)?;
    let mut rgba = Vec::with_capacity(width * height * 4);
    for rgb in pixels.chunks_exact(3) {
        rgba.extend_from_slice(rgb);
        rgba.push(if rgb == [0, 0, 0] { 0 } else { 255 });
    }
    struct Buffer<'a> {
        pointer: *mut u8,
        codec: &'a Codec,
    }
    impl Drop for Buffer<'_> {
        fn drop(&mut self) {
            unsafe {
                (self.codec.free)(self.pointer.cast());
            }
        }
    }
    let mut output = Buffer {
        pointer: std::ptr::null_mut(),
        codec,
    };
    let length = unsafe {
        (codec.encode)(
            rgba.as_ptr(),
            width as i32,
            height as i32,
            (width * 4) as i32,
            quality,
            &mut output.pointer,
        )
    };
    if length == 0 || output.pointer.is_null() {
        return Err("could not encode WebP".into());
    }
    Ok(unsafe { std::slice::from_raw_parts(output.pointer, length) }.to_vec())
}
