//! Per-adapter settings, inherited explicitly by native workers. No env mutation.
use serde_json::{Map, Value};
use std::cell::RefCell;
use std::ffi::{OsStr, OsString};
thread_local! { static SETTINGS: RefCell<Map<String, Value>> = RefCell::new(Map::new()); }
pub fn snapshot() -> Map<String, Value> {
    SETTINGS.with(|s| s.borrow().clone())
}
pub fn compatibility_profile() -> crate::compatibility::CompatibilityProfile {
    SETTINGS.with(|settings| {
        settings
            .borrow()
            .get("COMPAT_PROFILE")
            .and_then(Value::as_str)
            .and_then(|value| crate::compatibility::CompatibilityProfile::parse(value).ok())
            .unwrap_or_default()
    })
}
pub fn scope<T>(values: Map<String, Value>, operation: impl FnOnce() -> T) -> T {
    struct Restore(Map<String, Value>);
    impl Drop for Restore {
        fn drop(&mut self) {
            SETTINGS.with(|s| *s.borrow_mut() = std::mem::take(&mut self.0));
        }
    }
    let previous = SETTINGS.with(|s| s.replace(values));
    let _restore = Restore(previous);
    operation()
}
pub fn var(name: impl AsRef<OsStr>) -> Result<String, std::env::VarError> {
    let name = name.as_ref();
    if name == "TC_RUST_UTM_FAST"
        && crate::compatibility::CompatibilityProfile::current()
            == crate::compatibility::CompatibilityProfile::Terracotta0101
    {
        return Ok("0".into());
    }
    if let Some(key) = name.to_str().and_then(|s| s.strip_prefix("TC_")) {
        if let Some(value) = SETTINGS.with(|s| s.borrow().get(key).cloned()) {
            return match value {
                Value::Null => Err(std::env::VarError::NotPresent),
                Value::String(s) => Ok(s),
                other => Ok(other.to_string()),
            };
        }
    }
    std::env::var(name)
}
pub fn var_os(name: impl AsRef<OsStr>) -> Option<OsString> {
    var(name).ok().map(Into::into)
}

pub(crate) fn flag(name: &str, default: bool) -> crate::Result<bool> {
    let Ok(value) = var(name) else {
        return Ok(default);
    };
    match value.to_ascii_lowercase().as_str() {
        "true" | "1" | "yes" | "on" | "t" | "y" => Ok(true),
        "false" | "0" | "no" | "off" | "f" | "n" => Ok(false),
        _ => Err(format!("invalid boolean setting {name}")),
    }
}

/// Parse native CLI settings with the same defaults and explicit > environment precedence.
pub fn defaults() -> Map<String, Value> {
    defaults_for(crate::compatibility::CompatibilityProfile::current())
}

fn defaults_for(profile: crate::compatibility::CompatibilityProfile) -> Map<String, Value> {
    let mut defaults: Map<String, Value> =
        serde_json::from_str(include_str!("assets/settings.json")).expect("settings defaults");
    defaults.insert(
        "REMOTE_DB_CACHE_DIR".into(),
        Value::String(
            std::env::temp_dir()
                .join("terracotta")
                .to_string_lossy()
                .into(),
        ),
    );
    if !profile.is_terracotta() {
        defaults.insert("PUBLIC_URL".into(), Value::String(String::new()));
        defaults.insert("REMOTE_SOURCE_CACHE_TTL_SECONDS".into(), 60.into());
    }
    defaults
}

pub fn parse(mut explicit: Map<String, Value>) -> crate::Result<Map<String, Value>> {
    let profile = crate::compatibility::CompatibilityProfile::resolve(&mut explicit)?;
    explicit.remove("COMPAT_PROFILE");
    let mut defaults = defaults_for(profile);
    for key in explicit.keys() {
        if !defaults.contains_key(key) {
            return Err(format!(
                "Could not parse configuration: unknown setting {key}"
            ));
        }
    }
    for key in defaults.keys() {
        if !explicit.contains_key(key) {
            if let Ok(value) = std::env::var(format!("TC_{key}")) {
                explicit.insert(key.clone(), Value::String(value));
            }
        }
    }
    for old in [
        "MYSQL_USER",
        "MYSQL_PASSWORD",
        "POSTGRESQL_USER",
        "POSTGRESQL_PASSWORD",
    ] {
        if let Some(value) = explicit
            .get(old)
            .filter(|v| !v.is_null() && **v != "")
            .cloned()
        {
            eprintln!(
                "warning: Setting TC_{old} is deprecated; use TC_SQL_{} instead",
                old.rsplit('_').next().unwrap()
            );
            let new = format!("SQL_{}", old.rsplit('_').next().unwrap());
            if explicit.get(&new).is_none_or(|v| v.is_null() || *v == "") {
                explicit.insert(new, value);
            }
        }
    }
    for (key, mut value) in explicit {
        let default = &defaults[&key];
        let error = || format!("Could not parse configuration: invalid {key}");
        if default.is_boolean() {
            value = match value {
                Value::Bool(_) => value,
                Value::String(ref s)
                    if [
                        "true", "True", "TRUE", "1", "yes", "Yes", "YES", "on", "On", "ON", "t",
                        "T", "y", "Y",
                    ]
                    .contains(&s.as_str()) =>
                {
                    Value::Bool(true)
                }
                Value::String(ref s)
                    if [
                        "false", "False", "FALSE", "0", "no", "No", "NO", "off", "Off", "OFF", "f",
                        "F", "n", "N",
                    ]
                    .contains(&s.as_str()) =>
                {
                    Value::Bool(false)
                }
                Value::Number(ref n) if n.as_u64() == Some(0) => Value::Bool(false),
                Value::Number(ref n) if n.as_u64() == Some(1) => Value::Bool(true),
                _ => return Err(error()),
            };
        } else if default.is_number() {
            value = integer(&value).ok_or_else(error)?.into();
            let n = value.as_i64().ok_or_else(error)?;
            if n < 0
                || (key.ends_with("COMPRESS_LEVEL") && n > 9)
                || (key == "MAX_POST_METADATA_KEYS" && n == 0)
            {
                return Err(error());
            }
        } else if default.is_array() {
            if let Some(s) = value.as_str() {
                value = serde_json::from_str(s).map_err(|_| error())?;
            }
            let a = value.as_array_mut().ok_or_else(error)?;
            if key.ends_with("SHAPE") || key == "DEFAULT_TILE_SIZE" {
                for element in a.iter_mut() {
                    *element = integer(element).ok_or_else(error)?.into();
                }
                if a.len() != 2
                    || a.iter().any(|v| {
                        v.as_i64().is_none() || (key.ends_with("SHAPE") && v.as_i64().unwrap() < 0)
                    })
                {
                    return Err(error());
                }
            } else if a.iter().any(|v| !v.is_string()) {
                return Err(error());
            }
        } else if !(value.is_string() || default.is_null() && value.is_null()) {
            return Err(error());
        }
        if key == "LOGLEVEL"
            && !["debug", "info", "warning", "error", "critical"]
                .iter()
                .any(|s| value == *s)
        {
            return Err(error());
        }
        if key == "REMOTE_DB_CACHE_DIR" && !writable_parent(value.as_str().ok_or_else(error)?) {
            return Err(error());
        }
        if (key.ends_with("SAMPLING_METHOD") || key == "REPROJECTION_METHOD")
            && !["nearest", "linear", "cubic", "average"]
                .iter()
                .any(|s| value == *s)
        {
            return Err(error());
        }
        defaults.insert(key, value);
    }
    defaults.insert("COMPAT_PROFILE".into(), profile.name().into());
    Ok(defaults)
}

fn integer(value: &Value) -> Option<i64> {
    match value {
        Value::String(s) => s.trim().parse().ok(),
        Value::Number(n) => n.as_i64().or_else(|| {
            n.as_f64()
                .filter(|n| n.is_finite() && *n >= i64::MIN as f64 && *n < i64::MAX as f64)
                .map(|n| n as i64)
        }),
        _ => None,
    }
}

fn writable_parent(path: &str) -> bool {
    let parent = std::path::Path::new(path)
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or_else(|| std::path::Path::new("."));
    #[cfg(unix)]
    {
        use std::os::unix::ffi::OsStrExt;
        unsafe extern "C" {
            fn access(path: *const std::ffi::c_char, mode: std::ffi::c_int) -> std::ffi::c_int;
        }
        std::ffi::CString::new(parent.as_os_str().as_bytes())
            .is_ok_and(|p| unsafe { access(p.as_ptr(), 2) } == 0)
    }
    #[cfg(not(unix))]
    {
        parent.metadata().is_ok_and(|m| !m.permissions().readonly())
    }
}

/// Public Python protocol identity used for Terracotta-compatible verification.
pub(crate) fn protocol_version() -> String {
    snapshot()
        .get("PYTHON_PROTOCOL_VERSION")
        .and_then(Value::as_str)
        .unwrap_or(crate::TERRACOTTA_COMPATIBILITY_VERSION)
        .to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn source_lease_defaults_preserve_the_named_profile_and_explicit_zero() {
        use crate::compatibility::CompatibilityProfile;
        assert_eq!(
            defaults_for(CompatibilityProfile::Native)["REMOTE_SOURCE_CACHE_TTL_SECONDS"],
            60
        );
        assert_eq!(
            defaults_for(CompatibilityProfile::Terracotta0101)["REMOTE_SOURCE_CACHE_TTL_SECONDS"],
            0
        );
        for profile in ["native", "terracotta"] {
            let explicit =
                serde_json::json!({"COMPAT_PROFILE":profile, "REMOTE_SOURCE_CACHE_TTL_SECONDS":0})
                    .as_object()
                    .unwrap()
                    .clone();
            assert_eq!(
                parse(explicit).unwrap()["REMOTE_SOURCE_CACHE_TTL_SECONDS"],
                0
            );
        }
    }
}
