//! Optional lossless compression through the bundled libdeflate C API.

use libloading::Library;
use std::{ffi::c_void, sync::OnceLock};

type Allocate = unsafe extern "C" fn(i32) -> *mut c_void;
type Free = unsafe extern "C" fn(*mut c_void);
type Bound = unsafe extern "C" fn(*mut c_void, usize) -> usize;
type Compress = unsafe extern "C" fn(*mut c_void, *const u8, usize, *mut u8, usize) -> usize;

struct Codec {
    _library: Library,
    allocate: Allocate,
    free: Free,
    bound: Bound,
    compress: Compress,
}
static CODEC: OnceLock<Option<Codec>> = OnceLock::new();

fn load() -> Option<Codec> {
    let mut paths = Vec::new();
    if let Ok(gdal) = crate::raster::find_gdal_library() {
        if let Some(directory) = gdal.parent() {
            if let Ok(entries) = std::fs::read_dir(directory) {
                paths.extend(
                    entries
                        .filter_map(|entry| entry.ok())
                        .map(|entry| entry.path())
                        .filter(|path| {
                            let name = path.file_name().unwrap_or_default().to_string_lossy();
                            (name.starts_with("libdeflate.")
                                || name.starts_with("libdeflate-")
                                || name == "deflate.dll"
                                || name == "libdeflate.dll")
                                && path.is_file()
                        }),
                );
            }
        }
    }
    paths.sort();
    paths.push(libloading::library_filename("deflate").into());
    for path in paths {
        unsafe {
            let Ok(library) = Library::new(path) else {
                continue;
            };
            let symbols = (|| {
                Some((
                    *library
                        .get::<Allocate>(b"libdeflate_alloc_compressor\0")
                        .ok()?,
                    *library.get::<Free>(b"libdeflate_free_compressor\0").ok()?,
                    *library
                        .get::<Bound>(b"libdeflate_zlib_compress_bound\0")
                        .ok()?,
                    *library
                        .get::<Compress>(b"libdeflate_zlib_compress\0")
                        .ok()?,
                ))
            })();
            if let Some((allocate, free, bound, compress)) = symbols {
                return Some(Codec {
                    _library: library,
                    allocate,
                    free,
                    bound,
                    compress,
                });
            }
        }
    }
    None
}

pub(super) fn compress(input: &[u8], level: u32) -> Option<Vec<u8>> {
    let codec = CODEC.get_or_init(load).as_ref()?;
    struct Compressor<'a> {
        pointer: *mut c_void,
        codec: &'a Codec,
    }
    impl Drop for Compressor<'_> {
        fn drop(&mut self) {
            unsafe { (self.codec.free)(self.pointer) }
        }
    }
    unsafe {
        // Each call owns its compressor, avoiding shared mutable codec state.
        let pointer = (codec.allocate)(level as i32);
        if pointer.is_null() {
            return None;
        }
        let compressor = Compressor { pointer, codec };
        let mut output = vec![0; (codec.bound)(compressor.pointer, input.len())];
        let length = (codec.compress)(
            compressor.pointer,
            input.as_ptr(),
            input.len(),
            output.as_mut_ptr(),
            output.len(),
        );
        if length == 0 || length > output.len() {
            return None;
        }
        output.truncate(length);
        Some(output)
    }
}
