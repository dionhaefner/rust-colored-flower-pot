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
    let result: crate::Result<crate::http::AppResponse> = (|| {
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
    })();
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
    let result = c_string(request, "request").and_then(|text| {
        let request: serde_json::Value = serde_json::from_str(&text).map_err(|e| e.to_string())?;
        crate::settings::scope(
            request["settings"].as_object().cloned().unwrap_or_default(),
            || crate::cli::data_operation(&request),
        )
    });
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
        drop(unsafe { Box::from_raw(session) });
    }
}
