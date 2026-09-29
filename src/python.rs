//! Owned sessions for Python. Capsules own one session, never a process-global registry.
use crate::{
    Result, cli,
    execution::{ExecutionConfig, RasterExecutor},
    http::Application,
    raster::ReadRequest,
    storage::Store,
};
use serde_json::{Map, Value, json};
use std::{path::Path, sync::Mutex};
pub struct Session {
    settings: Map<String, Value>,
    kind: Kind,
}
enum Kind {
    Store(Mutex<Store>),
    Http(Application),
    Raster(RasterExecutor),
}
impl Session {
    pub fn open(request: &Value) -> Result<Self> {
        let settings = request["settings"].as_object().cloned().unwrap_or_default();
        let kind = crate::settings::scope(settings.clone(), || -> Result<Kind> {
            let path = request["path"].as_str().unwrap_or("");
            match request["kind"].as_str() {
                Some("http") => Ok(Kind::Http(Application::open(Path::new(path))?)),
                Some("raster") => Ok(Kind::Raster(RasterExecutor::start(
                    ExecutionConfig::from_env()?,
                )?)),
                Some("store") => Ok(Kind::Store(Mutex::new(Store::open_writable(
                    path,
                    request["provider"].as_str(),
                )?))),
                _ => Err("unknown session kind".into()),
            }
        })?;
        Ok(Self { settings, kind })
    }
    pub fn http_request(
        &self,
        method: &str,
        url: &str,
        headers: &std::collections::BTreeMap<String, String>,
        body: &[u8],
    ) -> Result<crate::http::AppResponse> {
        match &self.kind {
            Kind::Http(app) => Ok(app.request_full(method, url, headers, body)),
            _ => Err("session is not an HTTP application".into()),
        }
    }

    pub fn operation(&self, r: &Value) -> Result<Value> {
        crate::settings::scope(self.settings.clone(), || match &self.kind {
            Kind::Store(store) => {
                let mut store = store.lock().map_err(|_| "store lock poisoned")?;
                if r["refresh"] != false {
                    store.refresh()?;
                }
                if r["operation"] == "snapshot_path" {
                    return match &*store {
                        Store::Remote { generation, .. } => Ok(json!(generation.path)),
                        _ => Err("snapshot_path requires a remote store".into()),
                    };
                }
                cli::store_operation(&store, r)
            }
            Kind::Http(app) => {
                let headers =
                    serde_json::from_value(r.get("headers").cloned().unwrap_or(json!({})))
                        .map_err(|e| e.to_string())?;
                let body: Vec<u8> =
                    serde_json::from_value(r.get("body").cloned().unwrap_or(json!([])))
                        .map_err(|e| e.to_string())?;
                let result = app.request_full(
                    r["method"].as_str().unwrap_or("GET"),
                    r["url"].as_str().unwrap_or("/"),
                    &headers,
                    &body,
                );
                Ok(json!([
                    result.status,
                    result.content_type,
                    result.body,
                    result.cache,
                    result.headers
                ]))
            }
            Kind::Raster(executor) => {
                if r["operation"] == "metrics" {
                    return Ok(json!(executor.metrics()));
                }
                let request = ReadRequest {
                    bounds_mercator: serde_json::from_value(
                        r.get("tile_bounds").cloned().unwrap_or(Value::Null),
                    )
                    .map_err(|e| e.to_string())?,
                    size: serde_json::from_value(
                        r.get("tile_size").cloned().unwrap_or(json!([256, 256])),
                    )
                    .map_err(|e| e.to_string())?,
                    preserve_values: r["preserve_values"].as_bool().unwrap_or(false),
                };
                if request.size.contains(&0)
                    || request.size.iter().any(|s| *s > 4096)
                    || request.size[0]
                        .checked_mul(request.size[1])
                        .is_none_or(|pixels| pixels > 1024 * 1024)
                {
                    return Err("invalid tile dimensions".into());
                }
                let raster = executor.read(
                    Path::new(r["path"].as_str().ok_or("path required")?),
                    request,
                )?;
                Ok(
                    json!({"dtype":raster.data_type,"shape":[raster.height,raster.width],"values":raster.values,"mask":raster.mask}),
                )
            }
        })
    }
}
