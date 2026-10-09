//! Owned sessions for Python. Capsules own one session, never a process-global registry.
use crate::{
    Result, cli,
    execution::{ExecutionConfig, RasterExecutor},
    http::Application,
    raster::ReadRequest,
    storage::Store,
};
use serde_json::{Map, Value, json};
use std::{
    path::Path,
    sync::{Arc, Mutex},
};
mod driver;
pub struct Session {
    settings: Map<String, Value>,
    raster: Mutex<Option<Arc<RasterExecutor>>>,
    kind: Kind,
}
enum Kind {
    Store(Mutex<Store>),
    Catalog(Mutex<Box<crate::catalog::Catalog>>),
    Http(Box<Application>),
    Raster(RasterExecutor),
    Stac(Box<crate::stac::Selection>),
}
impl Session {
    pub fn open(request: &Value) -> Result<Self> {
        let mut settings = request["settings"].as_object().cloned().unwrap_or_default();
        crate::compatibility::CompatibilityProfile::resolve(&mut settings)?;
        let kind = crate::settings::scope(settings.clone(), || -> Result<Kind> {
            let path = request["path"].as_str().unwrap_or("");
            match request["kind"].as_str() {
                Some("http") => Ok(Kind::Http(Box::new(Application::open(Path::new(path))?))),
                Some("catalog") => {
                    let catalog = match crate::catalog::Catalog::open(Path::new(path))? {
                        crate::catalog::Opened::Datasets(c) => c,
                        crate::catalog::Opened::Layers(_) => {
                            Box::new(crate::catalog::Catalog::layers(Path::new(path))?)
                        }
                    };
                    Ok(Kind::Catalog(Mutex::new(catalog)))
                }
                Some("raster") => Ok(Kind::Raster(RasterExecutor::start(
                    ExecutionConfig::from_env()?,
                )?)),
                Some("store") => Ok(Kind::Store(Mutex::new(Store::open_writable(
                    path,
                    request["provider"].as_str(),
                )?))),
                Some("stac") => {
                    if crate::compatibility::CompatibilityProfile::current().is_terracotta() {
                        return Err("STAC requires the native profile".into());
                    }
                    let options = serde_json::from_value(
                        request.get("options").cloned().unwrap_or(json!({})),
                    )
                    .map_err(|_| "invalid STAC options")?;
                    let selection = if let Some(endpoint) = request["endpoint"].as_str() {
                        let headers = serde_json::from_value(
                            request.get("headers").cloned().unwrap_or(json!({})),
                        )
                        .map_err(|_| "invalid STAC credential headers")?;
                        crate::stac::Selection::search(
                            endpoint,
                            request["query"].clone(),
                            &headers,
                            options,
                        )?
                    } else {
                        let items = request["items"].as_array().ok_or("STAC items required")?;
                        crate::stac::Selection::from_items(items, options)?
                    };
                    Ok(Kind::Stac(Box::new(selection)))
                }
                _ => Err("unknown session kind".into()),
            }
        })?;
        Ok(Self {
            settings,
            kind,
            raster: Mutex::new(None),
        })
    }
    pub fn read_raster(&self, request: &Value) -> Result<Arc<crate::raster::Raster>> {
        crate::settings::scope(self.settings.clone(), || {
            let mut logical_source = None;
            let path = match &self.kind {
                Kind::Catalog(catalog) => {
                    let mut catalog = catalog.lock().map_err(|_| "catalog lock poisoned")?;
                    catalog.refresh()?;
                    let keys = if let Some(mapping) = request["keys"].as_object() {
                        if mapping.len() != catalog.keys().len() {
                            return Err("must specify every catalog key".into());
                        }
                        catalog
                            .key_names()
                            .iter()
                            .map(|key| {
                                mapping
                                    .get(*key)
                                    .and_then(Value::as_str)
                                    .map(str::to_owned)
                                    .ok_or("must specify every catalog key".to_owned())
                            })
                            .collect::<Result<Vec<_>>>()?
                    } else {
                        serde_json::from_value(request["keys"].clone())
                            .map_err(|e| e.to_string())?
                    };
                    let source = catalog.descriptor(&keys)?.ok_or("No dataset found")?.2;
                    let path = source.cache_path();
                    logical_source = Some(source);
                    path
                }
                Kind::Store(store) => {
                    let mut store = store.lock().map_err(|_| "store lock poisoned")?;
                    if request["refresh"] != false {
                        store.refresh()?;
                    }
                    let keys = driver::keys(&store, &request["keys"])?;
                    store.dataset(&keys)?.ok_or("No dataset found")?.path
                }
                Kind::Raster(_) => request["path"].as_str().ok_or("path required")?.into(),
                _ => return Err("session does not support raster reads".into()),
            };
            let read = raster_request(request)?;
            if let Kind::Raster(executor) = &self.kind {
                return executor.read(&path, read);
            }
            let executor = self.raster_executor()?;
            if let Some(source) = logical_source {
                source.read(&executor, read)
            } else {
                executor.read(&path, read)
            }
        })
    }
    fn raster_executor(&self) -> Result<Arc<RasterExecutor>> {
        let mut raster = self.raster.lock().map_err(|_| "raster lock poisoned")?;
        if raster.is_none() {
            *raster = Some(Arc::new(RasterExecutor::start(
                ExecutionConfig::from_env()?
            )?));
        }
        Ok(Arc::clone(
            raster.as_ref().expect("initialized raster executor"),
        ))
    }

    pub fn http_request(
        &self,
        method: &str,
        url: &str,
        headers: &std::collections::BTreeMap<String, String>,
        body: &[u8],
    ) -> Result<crate::http::AppResponse> {
        match &self.kind {
            Kind::Http(app) => {
                let response = app.request_full(method, url, headers, body);
                app.response_ready(url, response.status);
                Ok(response)
            }
            _ => Err("session is not an HTTP application".into()),
        }
    }

    pub fn compute_binary(&self, request: &Value, body: &[u8]) -> Result<crate::http::AppResponse> {
        match &self.kind {
            Kind::Http(app) => app.compute_binary(request, body),
            _ => Err("compute requires an HTTP application".into()),
        }
    }

    pub fn operation(&self, r: &Value) -> Result<Value> {
        if r["operation"] == "settings" {
            return Ok(json!(self.settings));
        }
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
                if r["driver"] == true {
                    driver::operation(&store, r)
                } else {
                    cli::store_operation(&store, r)
                }
            }
            Kind::Catalog(catalog) => {
                let mut catalog = catalog.lock().map_err(|_| "catalog lock poisoned")?;
                catalog.refresh()?;
                catalog.operation(r)
            }
            Kind::Http(app) => {
                if r["operation"] == "compute_url" {
                    let headers =
                        serde_json::from_value(r["headers"].clone()).map_err(|e| e.to_string())?;
                    return Ok(json!(app.compute_url(&headers, r["absolute"] == true)?));
                }
                if r["operation"] == "compute_style" {
                    let parameters =
                        serde_json::from_value(r["style"].clone()).map_err(|e| e.to_string())?;
                    app.compute_style(&parameters)?;
                    return Ok(Value::Null);
                }
                if r["operation"] == "compute_prepare" {
                    return app.compute_prepare(r);
                }
                if r["operation"] == "compute_grid" {
                    let matrix = crate::tile_matrix::TileMatrixSet::parse(
                        r["matrix"].as_str().ok_or("matrix required")?,
                    )?;
                    let coords: [u32; 3] = serde_json::from_value(r["coordinates"].clone())
                        .map_err(|e| e.to_string())?;
                    return Ok(
                        json!({"crs":format!("EPSG:{}",matrix.epsg()),"bounds":matrix.bounds(coords[0],coords[1],coords[2])?,"size":[256,256],"resampling":r["resampling"]}),
                    );
                }
                if r["operation"] == "layer_samples" {
                    let spec =
                        serde_json::from_value(r["spec"].clone()).map_err(|e| e.to_string())?;
                    let samples = app.layer_sample_result(
                        r["id"].as_str().ok_or("layer id required")?,
                        &spec,
                        r["provenance"] == true,
                    )?;
                    return Ok(samples.json(r["provenance"] == true));
                }
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
                app.response_ready(r["url"].as_str().unwrap_or("/"), result.status);
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
                let request = raster_request(r)?;
                let raster = executor.read(
                    Path::new(r["path"].as_str().ok_or("path required")?),
                    request,
                )?;
                Ok(
                    json!({"dtype":raster.data_type,"shape":[raster.height,raster.width],"values":raster.values,"mask":raster.mask}),
                )
            }
            Kind::Stac(selection) => match r["operation"].as_str() {
                Some("describe") => Ok(selection.describe()),
                Some("render") => {
                    let executor = self.raster_executor()?;
                    Ok(json!(selection.render(&executor, r)?))
                }
                _ => Err("unknown STAC operation".into()),
            },
        })
    }
}

fn raster_request(request: &Value) -> Result<ReadRequest> {
    let read = ReadRequest {
        grid: None,
        band: 1,
        bounds_mercator: serde_json::from_value(
            request.get("tile_bounds").cloned().unwrap_or(Value::Null),
        )
        .map_err(|e| e.to_string())?,
        size: serde_json::from_value(
            request
                .get("tile_size")
                .cloned()
                .unwrap_or(json!([256, 256])),
        )
        .map_err(|e| e.to_string())?,
        preserve_values: request["preserve_values"].as_bool().unwrap_or(false),
    };
    if read.size.contains(&0)
        || read.size.iter().any(|size| *size > 4096)
        || read.size[0]
            .checked_mul(read.size[1])
            .is_none_or(|pixels| pixels > 1024 * 1024)
    {
        return Err("invalid tile dimensions".into());
    }
    Ok(read)
}
