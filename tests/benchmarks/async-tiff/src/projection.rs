use libloading::Library;
use std::cell::RefCell;
use std::collections::HashMap;
use std::ffi::{CString, c_char, c_int, c_void};
use std::sync::OnceLock;

type Handle = *mut c_void;
#[repr(C)]
#[derive(Clone, Copy)]
struct Coord {
    values: [f64; 4],
}

struct Api {
    _library: Library,
    context_create: unsafe extern "C" fn() -> Handle,
    context_destroy: unsafe extern "C" fn(Handle),
    create: unsafe extern "C" fn(Handle, *const c_char, *const c_char, Handle) -> Handle,
    normalize: unsafe extern "C" fn(Handle, Handle) -> Handle,
    destroy: unsafe extern "C" fn(Handle) -> Handle,
    transform: unsafe extern "C" fn(Handle, c_int, usize, *mut Coord) -> c_int,
}

static API: OnceLock<Result<Api, String>> = OnceLock::new();
fn api() -> Result<&'static Api, String> {
    API.get_or_init(|| unsafe {
        let path = std::env::var("RCFP_PROBE_PROJ_LIBRARY")
            .map_err(|_| "RCFP_PROBE_PROJ_LIBRARY is required for projected tile reads")?;
        let library = Library::new(path).map_err(|e| e.to_string())?;
        Ok(Api {
            context_create: *library
                .get(b"proj_context_create\0")
                .map_err(|e| e.to_string())?,
            context_destroy: *library
                .get(b"proj_context_destroy\0")
                .map_err(|e| e.to_string())?,
            create: *library
                .get(b"proj_create_crs_to_crs\0")
                .map_err(|e| e.to_string())?,
            normalize: *library
                .get(b"proj_normalize_for_visualization\0")
                .map_err(|e| e.to_string())?,
            destroy: *library.get(b"proj_destroy\0").map_err(|e| e.to_string())?,
            transform: *library
                .get(b"proj_trans_array\0")
                .map_err(|e| e.to_string())?,
            _library: library,
        })
    })
    .as_ref()
    .map_err(Clone::clone)
}

struct Projection {
    context: Handle,
    transform: Handle,
    api: &'static Api,
}
impl Drop for Projection {
    fn drop(&mut self) {
        unsafe {
            (self.api.destroy)(self.transform);
            (self.api.context_destroy)(self.context);
        }
    }
}
thread_local! { static PLANS: RefCell<HashMap<u16, Projection>> = RefCell::new(HashMap::new()); }

pub fn coordinates(epsg: u16, bounds: [f64; 4], size: [usize; 2]) -> Result<Vec<[f64; 2]>, String> {
    let [height, width] = size;
    let mut coords = Vec::with_capacity(width * height);
    for y in 0..height {
        for x in 0..width {
            coords.push(Coord {
                values: [
                    bounds[0] + (x as f64 + 0.5) * (bounds[2] - bounds[0]) / width as f64,
                    bounds[3] - (y as f64 + 0.5) * (bounds[3] - bounds[1]) / height as f64,
                    0.,
                    0.,
                ],
            });
        }
    }
    if epsg != 3857 {
        let api = api()?;
        PLANS.with(|plans| -> Result<(), String> {
            let mut plans = plans.borrow_mut();
            if let std::collections::hash_map::Entry::Vacant(entry) = plans.entry(epsg) {
                unsafe {
                    let context = (api.context_create)();
                    if context.is_null() {
                        return Err("PROJ context allocation failed".into());
                    }
                    let from = c"EPSG:3857";
                    let to = CString::new(format!("EPSG:{epsg}")).unwrap();
                    let original =
                        (api.create)(context, from.as_ptr(), to.as_ptr(), std::ptr::null_mut());
                    if original.is_null() {
                        (api.context_destroy)(context);
                        return Err("PROJ could not create CRS transform".into());
                    }
                    let transform = (api.normalize)(context, original);
                    (api.destroy)(original);
                    if transform.is_null() {
                        (api.context_destroy)(context);
                        return Err("PROJ could not normalize CRS transform".into());
                    }
                    entry.insert(Projection {
                        context,
                        transform,
                        api,
                    });
                }
            }
            let plan = &plans[&epsg];
            unsafe {
                if (api.transform)(plan.transform, 1, coords.len(), coords.as_mut_ptr()) != 0 {
                    return Err("PROJ failed to transform requested grid".into());
                }
            }
            Ok(())
        })?;
    }
    Ok(coords
        .into_iter()
        .map(|p| [p.values[0], p.values[1]])
        .collect())
}
