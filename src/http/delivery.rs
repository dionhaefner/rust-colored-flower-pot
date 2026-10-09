//! Native publication and conditional response policy shared by every adapter.
use super::{AppResponse, json_error};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;

pub(super) fn update_spec(spec: &mut serde_json::Value) {
    use serde_json::json;
    spec["paths"]["/layers/{id}/render.png"] = json!({"get": {
        "summary": "Render a revision-pinnable layer image",
        "parameters": [
            {"name":"id","in":"path","required":true,"schema":{"type":"string"}},
            {"name":"spec","in":"query","description":"JSON NativeRenderSpec; omitted uses the stored default","schema":{"type":"string"}}
        ],
        "responses":{"200":{"description":"PNG with ETag and X-RCFP-Revision","content":{"image/png":{"schema":{"type":"string","format":"binary"}}}},"400":{"description":"Invalid render"},"404":{"description":"Unknown layer"},"503":{"description":"Capacity is full"},"504":{"description":"Raster deadline elapsed"}}
    }});
    for (path, entry) in spec["paths"].as_object_mut().expect("paths") {
        if ["/singleband/", "/rgb/", "/compute/", "/tiles/"]
            .iter()
            .any(|p| path.starts_with(p))
            || path == "/layers/{id}/render.png"
        {
            entry["get"]["parameters"].as_array_mut().expect("render parameters").push(json!({
                "name":"revision","in":"query","description":"X-RCFP-Revision of this exact rendered image; mismatch returns 409","schema":{"type":"string","pattern":"^[0-9a-f]{64}$"}
            }));
            entry["get"]["responses"]["304"] =
                json!({"description":"Current ETag matches If-None-Match; no body"});
            entry["get"]["responses"]["409"] = json!({"description":"Render revision changed or source changed during rendering; no-store"});
            entry["get"]["responses"]["412"] =
                json!({"description":"If-Match precondition failed; no-store"});
            entry["get"]["responses"]["200"]["headers"] = json!({
                "ETag":{"description":"Strong rendered representation validator","schema":{"type":"string"}},
                "X-RCFP-Revision":{"description":"Opaque render digest for the revision query","schema":{"type":"string"}},
                "Cache-Control":{"description":"Private by default; mutable URLs revalidate, matching pinned URLs are immutable","schema":{"type":"string"}}
            });
        }
    }
}

pub(super) fn apply(
    response: &mut AppResponse,
    method: &str,
    url: &str,
    headers: &BTreeMap<String, String>,
    public: bool,
) {
    if crate::compatibility::CompatibilityProfile::current().is_terracotta() {
        if response.cache {
            response
                .headers
                .insert("Cache-Control".into(), "no-cache".into());
        }
        return;
    }
    let retrieval = method == "GET" || method == "HEAD";
    let sensitive = headers.contains_key("authorization") || headers.contains_key("cookie");
    // Even denied CORS origins must partition shared responses.
    let vary = response.headers.entry("Vary".into()).or_default();
    for name in ["Origin", "Authorization", "Cookie"] {
        if !vary.split(',').any(|v| v.trim().eq_ignore_ascii_case(name)) {
            if !vary.is_empty() {
                vary.push_str(", ");
            }
            vary.push_str(name);
        }
    }
    let rendered = matches!(response.content_type, "image/png" | "image/webp")
        && url.split('?').next().is_some_and(|path| {
            ["/singleband/", "/rgb/", "/compute/", "/layers/", "/tiles/"]
                .iter()
                .any(|p| path.starts_with(p))
        });
    let revision = url
        .split_once('?')
        .and_then(|(_, query)| super::parse_query(query).remove("revision"));
    if response.status == 200 && rendered && retrieval {
        let tag = response
            .headers
            .get("ETag")
            .cloned()
            .unwrap_or_else(|| validator(response));
        if revision
            .as_ref()
            .is_some_and(|r| r != tag.trim_matches('"'))
        {
            let cors = std::mem::take(&mut response.headers);
            *response = json_error(409, "render revision is no longer available");
            for (k, v) in cors {
                if k.starts_with("Access-Control-") || k == "Vary" {
                    response.headers.insert(k, v);
                }
            }
        } else {
            response
                .headers
                .insert("X-RCFP-Revision".into(), tag.trim_matches('"').into());
            response.headers.insert("ETag".into(), tag);
        }
    }
    let cacheable = response.status == 200
        && retrieval
        && !sensitive
        && !url.starts_with("/__terracotta/metrics");
    let policy = if cacheable {
        let scope = if public { "public" } else { "private" };
        if rendered && revision.is_some() {
            format!("{scope}, max-age=31536000, immutable")
        } else {
            format!("{scope}, no-cache")
        }
    } else {
        "no-store".into()
    };
    response.headers.insert("Cache-Control".into(), policy);
    if cacheable {
        let tag = if let Some(tag) = response.headers.get("ETag") {
            tag.clone()
        } else {
            let tag = validator(response);
            response.headers.insert("ETag".into(), tag.clone());
            tag
        };
        if headers
            .get("if-match")
            .is_some_and(|value| value.trim() != "*" && !value.split(',').any(|v| v.trim() == tag))
        {
            response.status = 412;
            response.body.clear();
            response
                .headers
                .insert("Cache-Control".into(), "no-store".into());
            response.headers.remove("ETag");
            response.headers.remove("X-RCFP-Revision");
            response.cache = false;
        } else if headers
            .get("if-none-match")
            .is_some_and(|value| matches(value, &tag))
        {
            response.status = 304;
            response.body.clear();
            response.headers.remove("Content-Length");
        }
    } else {
        response.headers.remove("ETag");
        response.headers.remove("X-RCFP-Revision");
    }
    response.cache = cacheable && response.status != 412;
    if response.headers.contains_key("Access-Control-Allow-Origin") {
        response.headers.insert(
            "Access-Control-Expose-Headers".into(),
            "ETag, X-RCFP-Revision, Cache-Control".into(),
        );
    }
}

fn validator(response: &AppResponse) -> String {
    let mut hash = Sha256::new();
    hash.update(response.content_type);
    hash.update(&response.body);
    format!("\"{:x}\"", hash.finalize())
}

fn matches(value: &str, tag: &str) -> bool {
    value.trim() == "*"
        || value
            .split(',')
            .any(|v| v.trim().strip_prefix("W/").unwrap_or(v.trim()) == tag)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn weak_lists_and_wildcards_use_weak_comparison() {
        assert!(matches("W/\"abc\", \"def\"", "\"abc\""));
        assert!(matches(" * ", "\"abc\""));
        assert!(!matches("\"abcd\"", "\"abc\""));
    }
}
