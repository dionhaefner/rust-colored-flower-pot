//! C ABI used by the CPython extension.

use crate::http::Application;
use std::ffi::{CStr, CString, c_char};
use std::path::Path;
use std::ptr;

#[repr(C)]
pub struct TcResponse {
    pub status: u16,
    pub content_type: *mut c_char,
    pub body: *mut u8,
    pub body_len: usize,
    pub cache: bool,
    pub error: *mut c_char,
    pub headers_json: *mut c_char,
}

/// Execute one request through the shared native application.
///
/// # Safety
///
/// Every argument must be a live NUL-terminated C string for the duration of
/// the call. The returned pointer must be released exactly once with
/// [`tc_response_free`].
#[unsafe(no_mangle)]
pub unsafe extern "C" fn tc_http_request(
    database: *const c_char,
    method: *const c_char,
    url: *const c_char,
) -> *mut TcResponse {
    // SAFETY: same string requirements as this entry point, null empty body.
    unsafe { tc_http_request_full(database, method, url, c"{}".as_ptr(), ptr::null(), 0) }
}

/// Request with headers and a binary body, shared by WSGI and direct clients.
///
/// # Safety
/// Strings must be live NUL-terminated strings. `body` must point to `body_len`
/// readable bytes, or be null when length is zero. Free the returned response once.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn tc_http_request_full(
    database: *const c_char,
    method: *const c_char,
    url: *const c_char,
    headers: *const c_char,
    body: *const u8,
    body_len: usize,
) -> *mut TcResponse {
    let result: crate::Result<crate::http::AppResponse> = std::panic::catch_unwind(|| {
        let database = c_string(database, "database")?;
        let method = c_string(method, "method")?;
        let url = c_string(url, "URL")?;
        let application = Application::open(Path::new(&database))?;
        let headers: std::collections::BTreeMap<String, String> =
            serde_json::from_str(&c_string(headers, "headers")?).map_err(|e| e.to_string())?;
        if body_len > 1024 * 1024 {
            return Err("request body exceeds 1 MiB".into());
        }
        let body = if body_len == 0 {
            &[]
        } else {
            if body.is_null() {
                return Err("body pointer is null".into());
            }
            // SAFETY: caller supplies readable body_len bytes.
            unsafe { std::slice::from_raw_parts(body, body_len) }
        };
        Ok(application.request_full(&method, &url, &headers, body))
    })
    .unwrap_or_else(|_| Err("native request panic".into()));
    response_pointer(result)
}

/// Execute one request on an existing Python-owned HTTP session.
/// # Safety
/// Session must remain live during this call. Strings and body obey the same
/// requirements as tc_http_request_full. Free the response once.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn tc_session_http_request_full(
    session: *const crate::python::Session,
    method: *const c_char,
    url: *const c_char,
    headers: *const c_char,
    body: *const u8,
    body_len: usize,
) -> *mut TcResponse {
    let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        if session.is_null() {
            return Err("null session".into());
        }
        let method = c_string(method, "method")?;
        let url = c_string(url, "URL")?;
        let headers: std::collections::BTreeMap<String, String> =
            serde_json::from_str(&c_string(headers, "headers")?).map_err(|e| e.to_string())?;
        if body_len > 1024 * 1024 {
            return Err("request body exceeds 1 MiB".into());
        }
        let body = if body_len == 0 {
            &[]
        } else {
            if body.is_null() {
                return Err("body pointer is null".into());
            }
            unsafe { std::slice::from_raw_parts(body, body_len) }
        };
        unsafe { &*session }.http_request(&method, &url, &headers, body)
    }))
    .unwrap_or_else(|_| Err("native session panic".into()));
    response_pointer(result)
}

fn response_pointer(result: crate::Result<crate::http::AppResponse>) -> *mut TcResponse {
    match result {
        Ok(response) => {
            let mut body = response.body.into_boxed_slice();
            let body_len = body.len();
            let body_ptr = body.as_mut_ptr();
            std::mem::forget(body);
            Box::into_raw(Box::new(TcResponse {
                status: response.status,
                content_type: owned_c_string(response.content_type),
                body: body_ptr,
                body_len,
                cache: response.cache,
                error: ptr::null_mut(),
                headers_json: owned_c_string(
                    &serde_json::to_string(&response.headers).expect("string headers"),
                ),
            }))
        }
        Err(error) => Box::into_raw(Box::new(TcResponse {
            status: 0,
            content_type: ptr::null_mut(),
            body: ptr::null_mut(),
            body_len: 0,
            cache: false,
            error: owned_c_string(&error),
            headers_json: ptr::null_mut(),
        })),
    }
}

/// Release a response allocated by [`tc_http_request`].
///
/// # Safety
///
/// `response` must be null or a pointer returned by `tc_http_request` that has
/// not previously been freed.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn tc_response_free(response: *mut TcResponse) {
    if response.is_null() {
        return;
    }
    // SAFETY: the pointer is returned by tc_http_request and consumed once.
    let response = unsafe { Box::from_raw(response) };
    if !response.content_type.is_null() {
        // SAFETY: allocated by CString::into_raw above.
        drop(unsafe { CString::from_raw(response.content_type) });
    }
    if !response.headers_json.is_null() {
        // SAFETY: allocated by CString::into_raw above.
        drop(unsafe { CString::from_raw(response.headers_json) });
    }
    if !response.error.is_null() {
        // SAFETY: allocated by CString::into_raw above.
        drop(unsafe { CString::from_raw(response.error) });
    }
    if !response.body.is_null() {
        // SAFETY: ownership and exact length came from a boxed slice above.
        let slice = std::ptr::slice_from_raw_parts_mut(response.body, response.body_len);
        drop(unsafe { Box::from_raw(slice) });
    }
}

/// Release an error string returned by another function in this module.
///
/// # Safety
///
/// `value` must be null or a pointer returned by this module that has not
/// previously been freed.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn tc_string_free(value: *mut c_char) {
    if !value.is_null() {
        // SAFETY: allocated by CString::into_raw in this module.
        drop(unsafe { CString::from_raw(value) });
    }
}

fn c_string(pointer: *const c_char, name: &str) -> crate::Result<String> {
    if pointer.is_null() {
        return Err(format!("{name} cannot be null"));
    }
    // SAFETY: callers promise a live NUL-terminated string for this call.
    unsafe { CStr::from_ptr(pointer) }
        .to_str()
        .map(str::to_owned)
        .map_err(|_| format!("{name} must be UTF-8"))
}

fn owned_c_string(value: &str) -> *mut c_char {
    let clean = value.replace('\0', "\\0");
    CString::new(clean).expect("NUL removed").into_raw()
}

/// Structured data preparation operation for the Python adapter.
/// # Safety
/// `request` must be a live NUL-terminated UTF-8 JSON string. Free the returned
/// string exactly once with `tc_string_free`.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn tc_data_operation(request: *const c_char) -> *mut c_char {
    let result = std::panic::catch_unwind(|| {
        c_string(request, "request").and_then(|text| {
            let request: serde_json::Value =
                serde_json::from_str(&text).map_err(|e| e.to_string())?;
            let mut settings = request["settings"].as_object().cloned().unwrap_or_default();
            crate::compatibility::CompatibilityProfile::resolve(&mut settings)?;
            crate::settings::scope(settings, || crate::cli::data_operation(&request))
        })
    })
    .unwrap_or_else(|_| Err("native data operation panic".into()));
    let response = match result {
        Ok(value) => serde_json::json!({"ok":value}),
        Err(error) => serde_json::json!({"error":error}),
    };
    owned_c_string(&response.to_string())
}

/// Open a Python-owned session; errors are returned separately.
/// # Safety
/// `request` is a live C string and `error` a writable pointer. Free the session once.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn tc_session_open(
    request: *const c_char,
    error: *mut *mut c_char,
) -> *mut crate::python::Session {
    let result = std::panic::catch_unwind(|| {
        let value = serde_json::from_str(&c_string(request, "session request")?)
            .map_err(|e| e.to_string())?;
        crate::python::Session::open(&value)
    })
    .unwrap_or_else(|_| Err("native session panic".into()));
    match result {
        Ok(session) => Box::into_raw(Box::new(session)),
        Err(message) => {
            if !error.is_null() {
                unsafe {
                    *error = owned_c_string(&message);
                }
            }
            ptr::null_mut()
        }
    }
}
/// Execute on an owned session.
/// # Safety
/// Session remains live until this call returns; request is a live C string.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn tc_session_operation(
    session: *const crate::python::Session,
    request: *const c_char,
) -> *mut c_char {
    let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        if session.is_null() {
            return Err("null session".into());
        }
        let value =
            serde_json::from_str(&c_string(request, "request")?).map_err(|e| e.to_string())?;
        unsafe { &*session }.operation(&value)
    }))
    .unwrap_or_else(|_| Err("native session panic".into()));
    let value = match result {
        Ok(v) => serde_json::json!({"ok":v}),
        Err(e) => serde_json::json!({"error":e}),
    };
    owned_c_string(&value.to_string())
}
/// Release session; worker queues drain independently.
/// # Safety
/// Pointer must have been returned here and must not have been freed; no concurrent calls.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn tc_session_free(session: *mut crate::python::Session) {
    if !session.is_null() {
        let _ = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            drop(unsafe { Box::from_raw(session) });
        }));
    }
}

#[repr(C)]
pub struct TcRaster {
    pub data_type: i32,
    pub height: usize,
    pub width: usize,
    pub values: *const f64,
    pub mask: *const u8,
    pub error: *mut c_char,
    owner: *const crate::raster::Raster,
}

/// Read through the shared executor without serializing samples as JSON.
/// # Safety
/// Session and request remain live during the call. Free the result exactly once.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn tc_session_raster(
    session: *const crate::python::Session,
    request: *const c_char,
) -> *mut TcRaster {
    let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        if session.is_null() {
            return Err("null session".into());
        }
        let request =
            serde_json::from_str(&c_string(request, "request")?).map_err(|e| e.to_string())?;
        unsafe { &*session }.read_raster(&request)
    }))
    .unwrap_or_else(|_| Err("native raster panic".into()));
    let response = match result {
        Ok(raster) => TcRaster {
            data_type: raster.data_type,
            height: raster.height,
            width: raster.width,
            values: raster.values.as_ptr(),
            mask: raster.mask.as_ptr().cast(),
            error: ptr::null_mut(),
            owner: std::sync::Arc::into_raw(raster),
        },
        Err(error) => TcRaster {
            data_type: 0,
            height: 0,
            width: 0,
            values: ptr::null_mut(),
            mask: ptr::null_mut(),
            error: owned_c_string(&error),
            owner: ptr::null(),
        },
    };
    Box::into_raw(Box::new(response))
}

/// Release a raster returned by tc_session_raster.
/// # Safety
/// The pointer must be null or an unfreed result returned by tc_session_raster.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn tc_raster_free(raster: *mut TcRaster) {
    if raster.is_null() {
        return;
    }
    let raster = unsafe { Box::from_raw(raster) };
    if !raster.owner.is_null() {
        // SAFETY: the owned Arc was transferred by tc_session_raster.
        drop(unsafe { std::sync::Arc::from_raw(raster.owner) });
    }
    if !raster.error.is_null() {
        drop(unsafe { CString::from_raw(raster.error) });
    }
}

/// Transfer compute inputs or render a scalar output through a Python-owned application.
/// # Safety
/// Session and NUL-terminated request remain live during the call. Body points to
/// body_len readable bytes, or is null for zero length. Free the response once.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn tc_session_compute(
    session: *const crate::python::Session,
    request: *const c_char,
    body: *const u8,
    body_len: usize,
) -> *mut TcResponse {
    let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        if session.is_null() || (body_len != 0 && body.is_null()) {
            return Err("null compute pointer".into());
        }
        if body_len > 9 * 1024 * 1024 {
            return Err("compute buffer exceeds 9 MiB".into());
        }
        let request =
            serde_json::from_str(&c_string(request, "request")?).map_err(|e| e.to_string())?;
        let body = if body_len == 0 {
            &[]
        } else {
            unsafe { std::slice::from_raw_parts(body, body_len) }
        };
        unsafe { &*session }.compute_binary(&request, body)
    }))
    .unwrap_or_else(|_| Err("native compute boundary panic".into()));
    response_pointer(result)
}
