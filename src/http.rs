//! Shared HTTP contract and bounded raster orchestration.

use crate::Result;
use crate::execution::{ExecutionConfig, OVERLOADED, RasterExecutor, TIMED_OUT};
use crate::image::{self, Rgba};
use crate::raster::{self, Raster, ReadRequest};
use crate::storage::{Dataset, Metadata};
use crate::{color, expressions, palettes::Palettes};
use serde_json::{Map, Value, json};
use std::collections::{BTreeMap, HashMap};
use std::path::Path;
use std::sync::{
    Mutex,
    atomic::{AtomicUsize, Ordering},
};
use std::time::Instant;
use tiny_http::Method;

mod cache;
use crate::catalog::{Catalog, Opened};
mod transport;

pub struct Application {
    settings: Map<String, Value>,
    layers: Option<Mutex<crate::layers::catalog::Catalog>>,
    palettes: Palettes,
    default_size: [usize; 2],
    max_metadata: usize,
    executor: RasterExecutor,
    render_cache: cache::RenderCache,
    http_workers: usize,
    http_queue_capacity: usize,
    render_capacity: usize,
    render_in_flight: AtomicUsize,
    fused_render: bool,
    request_details: bool,
    store: Option<Mutex<Catalog>>,
}

struct RenderPermit<'a>(&'a AtomicUsize);
impl Drop for RenderPermit<'_> {
    fn drop(&mut self) {
        self.0.fetch_sub(1, Ordering::Release);
    }
}

pub struct AppResponse {
    pub status: u16,
    pub content_type: &'static str,
    pub body: Vec<u8>,
    pub cache: bool,
    pub headers: BTreeMap<String, String>,
}

impl Application {
    pub fn open(database: &Path) -> Result<Self> {
        let mut settings = crate::settings::snapshot();
        crate::compatibility::CompatibilityProfile::resolve(&mut settings)?;
        crate::settings::scope(settings, || Self::open_scoped(database))
    }

    fn open_scoped(database: &Path) -> Result<Self> {
        match Catalog::open(database)? {
            Opened::Datasets(catalog) => Self::configured(Some(Mutex::new(*catalog)), None),
            Opened::Layers(catalog) => Self::configured(
                Some(Mutex::new(Catalog::layers(database)?)),
                Some(Mutex::new(catalog)),
            ),
        }
    }

    pub fn from_stac(service: crate::stac::serve::Service) -> Result<Self> {
        crate::compatibility::CompatibilityProfile::current().require_native()?;
        Self::configured(Some(Mutex::new(Catalog::stac(service)?)), None)
    }

    fn configured(
        store: Option<Mutex<Catalog>>,
        layers: Option<Mutex<crate::layers::catalog::Catalog>>,
    ) -> Result<Self> {
        let config = ExecutionConfig::from_env()?;
        // Enough bounded HTTP workers must be able to reach raster admission
        // concurrently for a full raster queue to produce an immediate 503.
        let http_workers = config
            .workers
            .saturating_add(config.queue_capacity)
            .saturating_add(1);
        let http_queue_capacity = config.queue_capacity;
        let render_capacity = config.workers.saturating_add(config.queue_capacity);
        let render_bytes = crate::settings::var("TC_RENDER_CACHE_BYTES")
            .unwrap_or_else(|_| "0".into())
            .parse::<usize>()
            .map_err(|_| "invalid render cache size")?;
        if render_bytes > 512 * 1024 * 1024 {
            return Err("render cache exceeds 512 MiB".into());
        }
        let render_ttl = crate::settings::var("TC_REMOTE_RENDER_CACHE_TTL_SECONDS")
            .unwrap_or_else(|_| "0".into())
            .parse::<u64>()
            .map_err(|_| "invalid remote render cache TTL")?;
        if render_ttl > 86400 {
            return Err("remote render cache TTL exceeds 86400 seconds".into());
        }
        let render_cache = cache::RenderCache::new(
            render_bytes,
            std::time::Duration::from_secs(render_ttl),
            config.wait_timeout,
        );
        Ok(Self {
            fused_render: crate::settings::flag("TC_FUSED_RENDER", true)?,
            request_details: ["TC_XRAY_PROFILE", "TC_FLASK_PROFILE", "TC_DEBUG"]
                .iter()
                .any(|name| crate::settings::var(name).is_ok_and(|s| s == "true"))
                || crate::settings::var("TC_LOGLEVEL")
                    .is_ok_and(|s| ["debug", "info"].contains(&s.as_str())),
            settings: crate::settings::snapshot(),
            store,
            layers,
            palettes: Palettes::load()?,
            default_size: crate::settings::var("TC_DEFAULT_TILE_SIZE")
                .ok()
                .map(|v| parse_size(&v))
                .transpose()?
                .unwrap_or([256, 256]),
            max_metadata: crate::settings::var("TC_MAX_POST_METADATA_KEYS")
                .ok()
                .map(|v| {
                    v.parse::<usize>()
                        .map_err(|_| "invalid TC_MAX_POST_METADATA_KEYS")
                })
                .transpose()?
                .unwrap_or(100),
            executor: RasterExecutor::start(config)?,
            render_cache,
            http_workers,
            http_queue_capacity,
            render_capacity,
            render_in_flight: AtomicUsize::new(0),
        })
    }

    fn catalog(&self) -> Result<std::sync::MutexGuard<'_, Catalog>> {
        self.store
            .as_ref()
            .ok_or("this database uses native layers; use /layers")?
            .lock()
            .map_err(|_| "store lock poisoned".into())
    }

    pub fn layer_samples(&self, id: &str, spec: &crate::layers::RenderSpec) -> Result<Vec<Raster>> {
        crate::settings::scope(self.settings.clone(), || {
            let layer = self
                .layers
                .as_ref()
                .ok_or("not a native layer database")?
                .lock()
                .map_err(|_| "layer catalog lock poisoned")?
                .get(id)?
                .ok_or("No layer found")?;
            layer.samples(&self.executor, spec)
        })
    }

    fn layer_response(
        &self,
        method: &Method,
        path: &str,
        body: &[u8],
        content_type: Option<&str>,
        parameters: &BTreeMap<String, String>,
    ) -> AppResponse {
        let Some(catalog) = &self.layers else {
            return html_error(404);
        };
        let render = path.ends_with("/render");
        let allow = if render {
            "POST, OPTIONS"
        } else {
            "GET, HEAD, OPTIONS"
        };
        if method == &Method::Options
            || (render && method != &Method::Post)
            || (!render && ![Method::Get, Method::Head].contains(method))
        {
            let mut response = html_error(if method == &Method::Options { 200 } else { 405 });
            response.headers.insert("Allow".into(), allow.into());
            return response;
        }
        let result = (|| -> Result<AppResponse> {
            let selection = crate::telemetry::timer(crate::telemetry::Stage::Selection);
            if !parameters.is_empty() {
                return Err("layer routes do not accept query parameters".into());
            }
            let catalog = catalog.lock().map_err(|_| "layer catalog lock poisoned")?;
            if path == "/layers" {
                return json_response(
                    200,
                    &json!({"format":crate::layers::catalog::FORMAT,"layers":catalog.ids()?}),
                );
            }
            let parts = path
                .trim_start_matches("/layers/")
                .split('/')
                .collect::<Vec<_>>();
            if parts.len() != if render { 2 } else { 1 } {
                return Err("No layer found".into());
            }
            let layer = catalog
                .get(&percent_decode_path(parts[0])?)?
                .ok_or("No layer found")?;
            drop(catalog);
            if !render {
                return json_response(
                    200,
                    &serde_json::to_value(layer).map_err(|e| e.to_string())?,
                );
            }
            if content_type.is_none_or(|value| {
                !value
                    .split(';')
                    .next()
                    .unwrap_or("")
                    .trim()
                    .eq_ignore_ascii_case("application/json")
            }) {
                return Err("layer rendering requires application/json".into());
            }
            let value = if body.is_empty() {
                json!({})
            } else {
                serde_json::from_slice(body)
                    .map_err(|e| format!("invalid render specification: {e}"))?
            };
            let spec = if value.as_object().is_some_and(|object| object.is_empty()) {
                layer
                    .default_render
                    .clone()
                    .ok_or("layer has no default rendering specification")?
            } else {
                serde_json::from_value(value)
                    .map_err(|e| format!("invalid render specification: {e}"))?
            };
            drop(selection);
            Ok(bytes_response(
                "image/png",
                layer.render(&self.executor, &spec)?,
            ))
        })();
        match result {
            Ok(response) => response,
            Err(error) if error == "No layer found" => json_error(404, &error),
            Err(error) if error == OVERLOADED => json_error(503, &error),
            Err(error) if error == TIMED_OUT => json_error(504, &error),
            Err(error) => json_error(400, &error),
        }
    }

    pub fn gdal_version(&self) -> String {
        self.executor.version().to_owned()
    }

    /// Execute a request without binding a socket. Deployment adapters use
    /// this entry point so they cannot drift onto a second rendering path.
    pub fn request(&self, method: &str, url: &str) -> AppResponse {
        self.request_full(method, url, &BTreeMap::new(), &[])
    }

    pub(crate) fn response_ready(&self, url: &str, status: u16) {
        if status == 200 && (url.starts_with("/singleband/") || url.starts_with("/rgb/")) {
            self.executor.wake_prefetch();
        }
    }

    pub fn request_full(
        &self,
        method: &str,
        url: &str,
        headers: &BTreeMap<String, String>,
        body: &[u8],
    ) -> AppResponse {
        crate::settings::scope(self.settings.clone(), || {
            crate::telemetry::scope(
                Some(self.executor.cost().context(self.request_details)),
                || self.request_scoped(method, url, headers, body),
            )
        })
    }

    fn request_scoped(
        &self,
        method: &str,
        url: &str,
        headers: &BTreeMap<String, String>,
        body: &[u8],
    ) -> AppResponse {
        let request_timer = crate::telemetry::timer(crate::telemetry::Stage::Request);
        let started = std::time::Instant::now();
        let trace_start = std::time::SystemTime::now();
        let response = self.request_response(method, url, headers, body);
        let path = url.split('?').next().unwrap_or(url);
        drop(request_timer);
        let stages = crate::telemetry::current()
            .map(|context| context.trace())
            .unwrap_or_default();
        crate::tracing::request(
            headers,
            method,
            route_label(path),
            response.status,
            trace_start,
            &stages,
        );
        let profile = crate::settings::var("TC_FLASK_PROFILE").is_ok_and(|s| s == "true");
        let debug = crate::settings::var("TC_DEBUG").is_ok_and(|s| s == "true");
        let level = crate::settings::var("TC_LOGLEVEL").unwrap_or_else(|_| "warning".into());
        if profile || debug || ["debug", "info"].contains(&level.as_str()) {
            // Query strings may contain user data; log route and timing only.
            eprintln!(
                "{}",
                json!({"event":"http_request", "method":safe_method(method), "path":route_label(path), "status":response.status, "elapsed_ms":started.elapsed().as_secs_f64()*1000.0, "profile":profile,"stages_seconds":stages})
            );
        }
        response
    }

    fn request_response(
        &self,
        method: &str,
        url: &str,
        headers: &BTreeMap<String, String>,
        body: &[u8],
    ) -> AppResponse {
        if body.len() > 1024 * 1024
            || url.len() > 8192
            || method.len() > 16
            || headers
                .iter()
                .try_fold(0usize, |total, (name, value)| {
                    total.checked_add(name.len())?.checked_add(value.len())
                })
                .is_none_or(|total| total > 16 * 1024)
        {
            return json_error(413, "Request is too large");
        }
        let raw_path = url.split('?').next().unwrap_or(url);
        let decoded_path = percent_decode_path(raw_path).unwrap_or_else(|_| raw_path.to_owned());
        let _render_permit = if ["/singleband/", "/rgb/", "/compute/", "/layers/"]
            .iter()
            .any(|prefix| decoded_path.starts_with(prefix))
        {
            if self
                .render_in_flight
                .fetch_update(Ordering::AcqRel, Ordering::Acquire, |current| {
                    (current < self.render_capacity).then_some(current + 1)
                })
                .is_err()
            {
                self.executor
                    .cost()
                    .render_overloaded
                    .fetch_add(1, Ordering::Relaxed);
                return json_error(503, "render capacity is full");
            }
            Some(RenderPermit(&self.render_in_flight))
        } else {
            None
        };
        let parsed_method = method.parse::<Method>().unwrap_or(Method::Post);
        let mut response = self.handle(
            &parsed_method,
            url,
            body,
            headers.get("content-type").map(String::as_str),
        );
        let path = url.split('?').next().unwrap_or(url);
        let tile = ["/singleband/", "/rgb/", "/compute/", "/layers/"]
            .iter()
            .any(|p| path.starts_with(p));
        let metadata = [
            "/keys",
            "/datasets",
            "/colormap",
            "/metadata",
            "/__terracotta/identity",
        ]
        .contains(&path)
            || path.starts_with("/metadata/")
            || path == "/layers"
            || path.starts_with("/layers/");
        if (tile || metadata)
            && response.status != 405
            && (render_route(path) || metadata || path.starts_with("/layers/"))
        {
            let setting = if tile {
                "TC_ALLOWED_ORIGINS_TILES"
            } else {
                "TC_ALLOWED_ORIGINS_METADATA"
            };
            let allowed = crate::settings::var(setting).unwrap_or_else(|_| {
                if tile {
                    r"http[s]?://(localhost|127\.0\.0\.1):*".into()
                } else {
                    "*".into()
                }
            });
            let origins: Vec<String> =
                serde_json::from_str(&allowed).unwrap_or_else(|_| vec![allowed]);
            let origin = headers.get("origin");
            if origins.iter().any(|s| {
                (origin.is_none() && !s.contains(['*', '?', '(', '[', '\\']))
                    || s == "*"
                    || origin.is_some_and(|o| {
                        o.eq_ignore_ascii_case(s)
                            || regex::RegexBuilder::new(&format!("^(?:{s})"))
                                .case_insensitive(true)
                                .build()
                                .is_ok_and(|r| r.is_match(o))
                    })
            }) {
                response.headers.insert(
                    "Access-Control-Allow-Origin".into(),
                    origin.cloned().unwrap_or_else(|| {
                        origins
                            .iter()
                            .find(|s| s.as_str() == "*" || !s.contains(['*', '?', '(', '[', '\\']))
                            .cloned()
                            .unwrap_or_default()
                    }),
                );
                if origin.is_some()
                    && (origins.len() > 1
                        || origins
                            .iter()
                            .any(|s| s == "*" || s.contains(['*', '?', '(', '['])))
                {
                    response.headers.insert("Vary".into(), "Origin".into());
                }
                if method == "OPTIONS" && headers.contains_key("access-control-request-method") {
                    response.headers.insert(
                        "Access-Control-Allow-Methods".into(),
                        "DELETE, GET, HEAD, OPTIONS, PATCH, POST, PUT".into(),
                    );
                    if let Some(value) = headers.get("access-control-request-headers") {
                        response
                            .headers
                            .insert("Access-Control-Allow-Headers".into(), value.clone());
                    }
                }
            }
        }
        if path.starts_with("/static/") && response.status == 200 {
            response.cache = true;
        }
        if method == "HEAD" {
            response
                .headers
                .insert("Content-Length".into(), response.body.len().to_string());
            response.body.clear();
        }
        response
    }

    pub fn serve(self, address: &str) -> Result<()> {
        transport::serve(self, address, false)
    }

    /// Install process signal handlers for the standalone server and drain on shutdown.
    pub fn serve_with_signals(self, address: &str) -> Result<()> {
        transport::serve(self, address, true)
    }

    fn handle(
        &self,
        method: &Method,
        url: &str,
        body: &[u8],
        content_type: Option<&str>,
    ) -> AppResponse {
        let (raw_path, query) = url.split_once('?').unwrap_or((url, ""));
        // Decode each key after splitting the route so encoded slashes stay in their component.
        let path = raw_path;
        let parameters = parse_query(query);
        if path == "/layers" || path.starts_with("/layers/") {
            return self.layer_response(method, path, body, content_type, &parameters);
        }
        let known = [
            "/keys",
            "/datasets",
            "/metadata",
            "/colormap",
            "/swagger.json",
            "/apidoc",
            "/static/redoc.standalone.js",
            "/static/favicon.ico",
            "/static/redoc.standalone.js.LICENSE.txt",
            "/__terracotta/identity",
        ]
        .contains(&path)
            || path.starts_with("/metadata/")
            || render_route(path)
            || (["/__terracotta/metrics", "/__terracotta/metrics/prometheus"].contains(&path)
                && crate::settings::var("TC_EXPOSE_METRICS").is_ok_and(|value| value == "true"));
        if !known {
            return html_error(404);
        }
        if method == &Method::Options {
            let mut r = bytes_response("text/html; charset=utf-8", Vec::new());
            r.headers.insert(
                "Allow".into(),
                if path == "/metadata" {
                    "OPTIONS, POST"
                } else {
                    "GET, HEAD, OPTIONS"
                }
                .into(),
            );
            return r;
        }
        if (path == "/metadata" && method != &Method::Post)
            || (path != "/metadata" && ![Method::Get, Method::Head].contains(method))
        {
            let mut r = html_error(405);
            r.headers.insert(
                "Allow".into(),
                if path == "/metadata" {
                    "OPTIONS, POST"
                } else {
                    "GET, HEAD, OPTIONS"
                }
                .into(),
            );
            return r;
        }
        if render_route(path) {
            if let Err(error) = self.validate_render_options(path, &parameters) {
                return if error == "legacy internal error" {
                    html_error(500)
                } else {
                    json_error(400, &error)
                };
            }
        }
        let result = match path {
            "/keys" => self.keys(),
            "/datasets" => self.datasets(&parameters),
            "/metadata" => self.batch_metadata(&parameters, body, content_type),
            "/colormap" => self.colormap(&parameters),
            "/swagger.json" => {
                let mut spec = api_spec();
                update_palette_enums(&mut spec, &self.palettes);
                json_response(200, &spec)
            }
            "/static/redoc.standalone.js.LICENSE.txt" => Ok(bytes_response(
                "text/plain; charset=utf-8",
                include_bytes!("assets/static/redoc.standalone.js.LICENSE.txt").to_vec(),
            )),
            "/apidoc" => Ok(bytes_response(
                "text/html; charset=utf-8",
                include_bytes!("assets/apidoc.html").to_vec(),
            )),
            "/static/redoc.standalone.js" => Ok(bytes_response(
                "text/javascript; charset=utf-8",
                include_bytes!("assets/static/redoc.standalone.js").to_vec(),
            )),
            "/static/favicon.ico" => Ok(bytes_response(
                "image/vnd.microsoft.icon",
                include_bytes!("assets/static/favicon.ico").to_vec(),
            )),
            "/__terracotta/identity" => self.identity(),
            "/__terracotta/metrics" => self.metrics(),
            "/__terracotta/metrics/prometheus" => self.prometheus(),
            _ if path.starts_with("/metadata/") => self.metadata(path, &parameters),
            _ if path.starts_with("/singleband/") => self.singleband(path, &parameters),
            _ if path.starts_with("/rgb/") => self.rgb(path, &parameters),
            _ if path.starts_with("/compute/") => self.compute(path, &parameters),
            _ => return html_error(404),
        };
        match result {
            Ok(response) => response,
            Err(error) if error == "No dataset found" => json_error(404, &error),
            Err(error) if error == "tile out of bounds" => self
                .empty_tile(&parameters, path.starts_with("/rgb/"))
                .unwrap_or_else(|e| json_error(400, &e)),
            Err(error) if error == OVERLOADED => json_error(503, &error),
            Err(error) if error == TIMED_OUT => json_error(504, &error),
            Err(error)
                if error.contains("not writable") || error.starts_with("Lazy loading requires") =>
            {
                json_error(403, &error)
            }
            Err(error)
                if error.starts_with("Could not retrieve database from S3")
                    || error.starts_with("not a Terracotta database")
                    || error.starts_with("Version conflict")
                    || error.starts_with("invalid remote SQLite")
                    || error.starts_with("Could not connect to database")
                    || error.starts_with("database operation failed") =>
            {
                html_error(500)
            }
            Err(error) if error == "legacy internal error" => html_error(500),
            Err(error) if error == "unsupported media type" => html_error(415),
            Err(error) if error == "bad JSON body" => html_error(400),
            Err(error) => json_error(400, &error),
        }
    }

    fn validate_render_options(
        &self,
        path: &str,
        parameters: &BTreeMap<String, String>,
    ) -> Result<()> {
        if let Some(size) = parameters.get("tile_size") {
            parse_size(size)?;
        }
        let rgb = path.starts_with("/rgb/");
        let compute = path.starts_with("/compute/");
        if !rgb {
            if let Some(name) = parameters.get("colormap") {
                if name != "explicit" {
                    self.palettes.get(name)?;
                }
            }
        }
        if !compute {
            if let Some(transform) = parameters.get("color_transform") {
                color::parse(transform, if rgb { 3 } else { 1 })?;
            }
        }
        let ranges = if rgb {
            vec!["r_range", "g_range", "b_range"]
        } else {
            vec!["stretch_range"]
        };
        for key in ranges {
            if let Some(raw) = parameters.get(key) {
                let value: Value = serde_json::from_str(raw)
                    .map_err(|_| format!("Could not decode value {raw} for {key} as JSON"))?;
                if value.is_null() && !compute {
                    continue;
                }
                let items = value
                    .as_array()
                    .filter(|v| v.len() == 2)
                    .ok_or_else(|| format!("{key} must contain two values"))?;
                for value in items {
                    if value.is_null() || value.is_number() {
                        continue;
                    }
                    if !compute
                        && value.as_str().is_some_and(|s| {
                            s.starts_with('p')
                                && s.len() > 1
                                && s[1..].bytes().all(|b| b.is_ascii_digit())
                        })
                    {
                        continue;
                    }
                    if compute && value.as_str().is_some_and(|s| s.parse::<f64>().is_ok()) {
                        continue;
                    }
                    return Err(format!("Invalid {key}: percentile format is p<digits>"));
                }
            }
        }
        if rgb {
            for channel in ["r", "g", "b"] {
                if !parameters.contains_key(channel) {
                    return Err(format!("Missing data for required field: {channel}"));
                }
            }
        }
        if compute {
            let missing = ["expression", "stretch_range"]
                .into_iter()
                .filter(|key| !parameters.contains_key(*key))
                .collect::<Vec<_>>();
            if !missing.is_empty() {
                return Err(format!(
                    "Missing data for required fields: {}",
                    missing.join(", ")
                ));
            }
        }
        if !rgb && !compute {
            let palette = parameters
                .get("explicit_color_map")
                .map(|v| parse_palette(v))
                .transpose()?;
            if parameters.get("colormap").is_some_and(|s| s == "explicit")
                && palette.as_ref().is_none_or(Vec::is_empty)
            {
                return Err(
                    "explicit_color_map argument must be given for colormap=explicit".into(),
                );
            }
            if parameters.get("colormap").is_none_or(|s| s != "explicit")
                && palette.as_ref().is_some_and(|p| !p.is_empty())
            {
                return Err("explicit_color_map can only be given for colormap=explicit".into());
            }
        }
        Ok(())
    }

    fn keys(&self) -> Result<AppResponse> {
        let mut store = self.catalog()?;
        store.refresh()?;
        json_response(
            200,
            &json!({"keys": store.keys().iter().map(|k| if k.description.is_empty() {json!({"key":k.key})} else {json!(k)}).collect::<Vec<_>>()}),
        )
    }

    fn identity(&self) -> Result<AppResponse> {
        let mut identity = json!({
            "implementation": crate::IMPLEMENTATION_VERSION,
            "compatibility_profile": crate::compatibility::CompatibilityProfile::current().name(),
            "protocol_compatibility": crate::TERRACOTTA_COMPATIBILITY_VERSION,
            "database_compatibility": if self.layers.is_some() {crate::layers::catalog::FORMAT} else {crate::DATABASE_COMPATIBILITY}
        });
        if let Some(store) = &self.store {
            let store = store.lock().map_err(|_| "catalog lock poisoned")?;
            identity["database_compatibility"] = json!(store.format());
            if let Some(revision) = store.revision() {
                identity["source_revision"] = json!(revision);
            }
        }
        json_response(200, &identity)
    }

    fn datasets(&self, parameters: &BTreeMap<String, String>) -> Result<AppResponse> {
        let mut store = self.catalog()?;
        store.refresh()?;
        let known = store.key_names();
        for key in parameters.keys() {
            if !known.contains(&key.as_str())
                && !["limit", "page", "order_by", "ascending"].contains(&key.as_str())
            {
                return Err(format!(
                    "Encountered unrecognized keys {{'{key}'}} (available keys: ('{}'))",
                    known.join("', '")
                ));
            }
        }
        let page = parse_usize(parameters.get("page"), 0, "page")?;
        let limit = parse_usize(parameters.get("limit"), 100, "limit")?;
        if limit > 1000 || page.checked_mul(limit).is_none() {
            return Err("invalid dataset page or limit".into());
        }
        let order = parameters
            .get("order_by")
            .map(|value| {
                if value.starts_with('[') {
                    list_value(value)
                } else if !crate::compatibility::CompatibilityProfile::current().is_terracotta() {
                    vec![value.clone()]
                } else {
                    value.chars().map(|c| c.to_string()).collect()
                }
            })
            .unwrap_or_else(|| known.iter().map(|key| (*key).to_owned()).collect());
        if order
            .iter()
            .any(|key| key != "path" && !known.contains(&key.as_str()))
        {
            let mut unknown: Vec<_> = order
                .iter()
                .filter(|k| k.as_str() != "path" && !known.contains(&k.as_str()))
                .cloned()
                .collect();
            unknown.sort();
            unknown.dedup();
            return Err(format!(
                "Encountered unrecognized keys: {} (available keys: ('{}'{}))",
                unknown.join(", "),
                known.join("', '"),
                if known.len() == 1 { "," } else { "" }
            ));
        }
        let ascending = match parameters.get("ascending").map(String::as_str) {
            None
            | Some(
                "true" | "True" | "TRUE" | "1" | "yes" | "Yes" | "YES" | "on" | "On" | "ON" | "t"
                | "T" | "y" | "Y",
            ) => true,
            Some(
                "false" | "False" | "FALSE" | "0" | "no" | "No" | "NO" | "off" | "Off" | "OFF"
                | "f" | "F" | "n" | "N",
            ) => false,
            _ => return Err("{'ascending': ['Not a valid boolean.']}".into()),
        };
        let filters = known
            .iter()
            .filter_map(|key| {
                parameters
                    .get(*key)
                    .map(|v| (key.to_string(), list_value(v)))
            })
            .collect();
        let datasets = store.query_datasets(&filters, &order, ascending, Some((page, limit)))?;
        let items = datasets
            .into_iter()
            .map(|dataset| dataset_json(&known, &dataset))
            .collect::<Vec<_>>();
        json_response(
            200,
            &json!({"datasets": items, "limit": limit, "page": page}),
        )
    }

    fn metadata(&self, path: &str, parameters: &BTreeMap<String, String>) -> Result<AppResponse> {
        let mut store = self.catalog()?;
        store.refresh()?;
        self.metadata_from_catalog(&store, path, parameters)
    }
    fn metadata_from_catalog(
        &self,
        store: &Catalog,
        path: &str,
        parameters: &BTreeMap<String, String>,
    ) -> Result<AppResponse> {
        if parameters.keys().any(|key| key != "columns") {
            return Err("unrecognized metadata option".into());
        }
        let keys = split_keys(path.trim_start_matches("/metadata/"));
        let mut value = store
            .metadata_json(&keys)?
            .ok_or_else(|| "No dataset found".to_string())?;
        if let Some(columns) = parameters.get("columns") {
            let columns: Vec<String> =
                serde_json::from_str(columns).map_err(|_| "Invalid columns JSON")?;
            if columns.is_empty()
                || columns
                    .iter()
                    .any(|c| !value.as_object().unwrap().contains_key(c) || c == "keys")
            {
                return Err(
                    if crate::compatibility::CompatibilityProfile::current().is_terracotta() {
                        "legacy internal error"
                    } else {
                        "columns must be a nonempty list of known metadata columns"
                    }
                    .into(),
                );
            }
            let object = value.as_object_mut().expect("metadata object");
            object.retain(|key, _| key == "keys" || columns.contains(key));
        }
        json_response(200, &value)
    }

    fn singleband(&self, path: &str, parameters: &BTreeMap<String, String>) -> Result<AppResponse> {
        let selection = crate::telemetry::timer(crate::telemetry::Stage::Selection);
        let started = Instant::now();
        let components = path
            .trim_start_matches("/singleband/")
            .split('/')
            .collect::<Vec<_>>();
        let mut store = self.catalog()?;
        store.refresh()?;
        let key_count = store.keys().len();
        if components.len() != key_count + 1 && components.len() != key_count + 3 {
            return Err("invalid singleband route".into());
        }
        let keys = components[..key_count]
            .iter()
            .map(|item| percent_decode_path(item))
            .collect::<Result<Vec<_>>>()?;
        let (dataset, metadata, source) = store
            .descriptor(&keys)?
            .ok_or_else(|| "No dataset found".to_string())?;
        let legacy_decimal = store.legacy_decimal_stretch();
        drop(store);
        drop(selection);
        let metadata_at = Instant::now();
        let size = parameters
            .get("tile_size")
            .map(|value| parse_size(value))
            .transpose()?
            .unwrap_or(self.default_size);
        let bounds = if components.len() == key_count + 1 {
            if components[key_count] != "preview.png" {
                return Err("invalid singleband preview route".into());
            }
            // Terracotta 0.10.1 frames previews from the raster's transformed
            // source bounds, not by projecting the metadata bounding box.
            None
        } else {
            let z = components[key_count].parse::<u32>().unwrap_or(u32::MAX);
            let x = components[key_count + 1]
                .parse::<f64>()
                .map_err(|_| "invalid tile x".to_string())?;
            let y = components[key_count + 2]
                .trim_end_matches(".png")
                .parse::<f64>()
                .map_err(|_| "invalid tile y".to_string())?;
            if !raster::tile_exists(metadata.bounds, x, y, z) {
                return self.empty_tile(parameters, false);
            }
            Some(raster::mercator_tile_bounds(x, y, z))
        };
        let operations = parameters
            .get("color_transform")
            .map(|v| color::parse(v, 1))
            .transpose()?
            .unwrap_or_default();
        let colormap = parameters
            .get("colormap")
            .map(String::as_str)
            .or_else(|| metadata.metadata["defaults"]["colormap"].as_str());
        if let Some(other) = colormap {
            if !self.palettes.0.contains_key(other) && other != "explicit" {
                return Err(format!("Encountered invalid color map {other}"));
            }
        }
        let explicit = parameters
            .get("explicit_color_map")
            .map(|value| parse_palette(value))
            .transpose()?;
        if colormap == Some("explicit") && explicit.as_ref().is_none_or(Vec::is_empty) {
            return Err("explicit_color_map argument must be given for colormap=explicit".into());
        }
        if colormap != Some("explicit") && explicit.is_some() {
            return Err("explicit_color_map can only be given for colormap=explicit".into());
        }
        let preserve_values = explicit.is_some();
        let band = source_band(parameters, "band", "stretch_range")?;
        if band != 1 && bounds.is_none() {
            return Err("source band selectors are supported on tiles".into());
        }
        let key = self
            .render_cache
            .key(path, parameters, [(dataset.path.as_path(), &metadata)]);
        let remote = crate::execution::is_remote(&dataset.path);
        let png = self.render_cache.render(key, remote, || {
            let raster = source.read(
                &self.executor,
                ReadRequest {
                    grid: None,
                    band,
                    bounds_mercator: bounds,
                    size,
                    preserve_values,
                },
            )?;
            let raster_at = Instant::now();
            let png = if let Some(palette) = explicit {
                let pixels = image::label_values(&raster, &palette);
                let colors = palette.iter().map(|(_, color)| *color).collect::<Vec<_>>();
                image::palette_png(raster.width, raster.height, &pixels, &colors)?
            } else {
                let range = parameters
                    .get("stretch_range")
                    .map(|value| parse_stretch(value, &metadata))
                    .transpose()?
                    .unwrap_or(default_range(&metadata));
                if legacy_decimal && uses_metadata_range(parameters.get("stretch_range")) {
                    return Err("legacy internal error".into());
                }
                let pixels = if operations.is_empty() && self.fused_render {
                    image::normalized_pixels(&raster, range)
                } else {
                    let mut channels = vec![color::normalize(&raster, range)];
                    color::apply(&mut channels, &operations);
                    image::stretch_to_u8(&channels[0], [0.0, 1.0])
                };
                if let Some(name) = colormap {
                    image::palette_png(
                        raster.width,
                        raster.height,
                        &pixels,
                        &self.palettes.get(name)?,
                    )?
                } else {
                    image::grayscale_png(raster.width, raster.height, &pixels)?
                }
            };
            if std::env::var_os("TC_PROFILE_HTTP").is_some() {
                eprintln!(
                    "http_profile mode=singleband metadata_us={} raster_us={} render_us={} path={}",
                    (metadata_at - started).as_micros(),
                    (raster_at - metadata_at).as_micros(),
                    raster_at.elapsed().as_micros(),
                    route_label(path),
                );
            }
            Ok(png)
        })?;
        Ok(AppResponse {
            status: 200,
            content_type: "image/png",
            body: png,
            cache: true,
            headers: BTreeMap::new(),
        })
    }

    fn rgb(&self, path: &str, parameters: &BTreeMap<String, String>) -> Result<AppResponse> {
        let selection = crate::telemetry::timer(crate::telemetry::Stage::Selection);
        let started = Instant::now();
        let format = match parameters
            .get("format")
            .map(String::as_str)
            .unwrap_or("png")
        {
            "png" => image::RgbFormat::Png,
            "webp" if !crate::compatibility::CompatibilityProfile::current().is_terracotta() => {
                let quality = parameters
                    .get("quality")
                    .map(|v| v.parse::<f32>())
                    .transpose()
                    .map_err(|_| "WebP quality must be a number from 0 to 100")?
                    .unwrap_or(85.0);
                if !quality.is_finite() || !(0.0..=100.0).contains(&quality) {
                    return Err("WebP quality must be a number from 0 to 100".into());
                }
                image::RgbFormat::Webp(quality)
            }
            _ => return Err("RGB format must be png, or webp in the native profile".into()),
        };
        let operations = parameters
            .get("color_transform")
            .map(|v| color::parse(v, 3))
            .transpose()?
            .unwrap_or_default();
        let size = parameters
            .get("tile_size")
            .map(|value| parse_size(value))
            .transpose()?
            .unwrap_or(self.default_size);
        let mut store = self.catalog()?;
        store.refresh()?;
        let key_count = store.keys().len();
        let some_key_count = key_count.saturating_sub(1);
        let components = path
            .trim_start_matches("/rgb/")
            .split('/')
            .filter(|component| !component.is_empty())
            .collect::<Vec<_>>();
        let preview = components.len() == some_key_count + 1
            && components.last().copied() == Some("preview.png");
        let tiled = components.len() == some_key_count + 3;
        if !preview && !tiled {
            return Err("invalid RGB route".into());
        }
        let some_keys = components[..some_key_count]
            .iter()
            .map(|item| percent_decode_path(item))
            .collect::<Result<Vec<_>>>()?;
        let channel_names = ["r", "g", "b"];
        let band_values = channel_names
            .map(|channel| {
                parameters
                    .get(channel)
                    .cloned()
                    .ok_or_else(|| format!("Missing data for required field: {channel}"))
            })
            .into_iter()
            .collect::<Result<Vec<_>>>()?;

        let mut sources = Vec::with_capacity(3);
        let mut paths = Vec::with_capacity(3);
        let mut metadata = Vec::with_capacity(3);
        for band in &band_values {
            let mut keys = some_keys.clone();
            keys.push(band.clone());
            let (dataset, descriptor, source) = store
                .descriptor(&keys)?
                .ok_or_else(|| "No dataset found".to_string())?;
            paths.push(dataset.path);
            metadata.push(descriptor);
            sources.push(source);
        }
        let legacy_decimal = store.legacy_decimal_stretch();
        drop(store);
        drop(selection);
        let metadata_at = Instant::now();

        let tile_bounds = if preview {
            None
        } else {
            let z = components[some_key_count]
                .parse::<u32>()
                .unwrap_or(u32::MAX);
            let x = components[some_key_count + 1]
                .parse::<f64>()
                .map_err(|_| "invalid tile x".to_string())?;
            let y = components[some_key_count + 2]
                .trim_end_matches(".png")
                .parse::<f64>()
                .map_err(|_| "invalid tile y".to_string())?;
            if metadata
                .iter()
                .any(|item| !raster::tile_exists(item.bounds, x, y, z))
            {
                return self.empty_tile(parameters, true);
            }
            Some(raster::mercator_tile_bounds(x, y, z))
        };
        let preview_grid = if preview && sources.iter().any(|source| source.mapped()) {
            Some(shared_bounds(sources.iter())?)
        } else {
            None
        };
        let channel_bounds = metadata
            .iter()
            // None independently frames each source inside the raster reader,
            // including different RGB extents and antimeridian wrapping.
            .map(|_| tile_bounds.or(preview_grid))
            .collect::<Vec<_>>();
        let bands = channel_names
            .iter()
            .map(|name| {
                source_band(
                    parameters,
                    &format!("{name}_band"),
                    &format!("{name}_range"),
                )
            })
            .collect::<Result<Vec<_>>>()?;

        let mut ranges = Vec::with_capacity(3);
        for (index, channel) in channel_names.iter().enumerate() {
            let name = format!("{channel}_range");
            let range = parameters
                .get(&name)
                .map(|value| parse_stretch(value, &metadata[index]))
                .transpose()?
                .unwrap_or(default_range(&metadata[index]));
            if range[1] < range[0] {
                return Err("Upper stretch bound must be higher than lower bound".into());
            }
            if legacy_decimal && uses_metadata_range(parameters.get(&name)) {
                return Err("legacy internal error".into());
            }
            ranges.push(range);
        }

        if tile_bounds.is_none() && bands.iter().any(|&band| band != 1) {
            return Err("source band selectors are supported on tiles".into());
        }
        let key = self.render_cache.key(
            path,
            parameters,
            paths.iter().zip(&metadata).map(|(p, m)| (p.as_path(), m)),
        );
        let remote = paths.iter().any(|p| crate::execution::is_remote(p));
        let png = self.render_cache.render(key, remote, || {
            for (source, band) in sources.iter().zip(&bands) {
                source.validate(*band)?;
            }
            let channels = if sources.iter().any(|source| source.mapped()) {
                sources
                    .iter()
                    .enumerate()
                    .map(|(i, source)| {
                        source.read(
                            &self.executor,
                            ReadRequest {
                                grid: None,
                                band: bands[i],
                                bounds_mercator: channel_bounds[i],
                                size,
                                preserve_values: false,
                            },
                        )
                    })
                    .collect::<Result<Vec<_>>>()?
            } else {
                // A repeated band is read once and independently stretched into each
                // requested output channel.
                let mut unique_requests = Vec::new();
                let mut channel_sources = Vec::with_capacity(3);
                let mut identities = HashMap::new();
                for (channel, path) in paths.into_iter().enumerate() {
                    let identity = (
                        path.to_string_lossy().into_owned(),
                        channel_bounds[channel].map(|bounds| bounds.map(f64::to_bits)),
                        bands[channel],
                    );
                    let index = if let Some(index) = identities.get(&identity) {
                        *index
                    } else {
                        let index = unique_requests.len();
                        identities.insert(identity, index);
                        unique_requests.push((path, channel_bounds[channel], bands[channel]));
                        index
                    };
                    channel_sources.push(index);
                }
                let requests = unique_requests
                    .into_iter()
                    .map(|(path, bounds, band)| {
                        (
                            path,
                            ReadRequest {
                                grid: None,
                                band,
                                bounds_mercator: bounds,
                                size,
                                preserve_values: false,
                            },
                        )
                    })
                    .collect::<Vec<_>>();
                let rasters = self.executor.read_many(&requests)?;
                channel_sources
                    .iter()
                    .map(|index| rasters[*index].clone())
                    .collect::<Vec<_>>()
            };
            let first = &channels[0];
            if channels
                .iter()
                .any(|r| r.width != first.width || r.height != first.height)
            {
                return Err("RGB bands produced different output dimensions".into());
            }
            let raster_at = Instant::now();
            let png = if operations.is_empty() && self.fused_render {
                image::stretched_rgb_image(
                    [&channels[0], &channels[1], &channels[2]],
                    [ranges[0], ranges[1], ranges[2]],
                    format,
                )?
            } else {
                let mut normalized = channels
                    .iter()
                    .enumerate()
                    .map(|(channel, raster)| color::normalize(raster, ranges[channel]))
                    .collect::<Vec<_>>();
                color::apply(&mut normalized, &operations);
                let channels = normalized
                    .iter()
                    .map(|r| image::stretch_to_u8(r, [0.0, 1.0]))
                    .collect::<Vec<_>>();
                image::rgb_image(
                    first.width,
                    first.height,
                    [&channels[0], &channels[1], &channels[2]],
                    format,
                )?
            };
            if std::env::var_os("TC_PROFILE_HTTP").is_some() {
                eprintln!(
                    "http_profile mode=rgb metadata_us={} raster_us={} render_us={} path={}",
                    (metadata_at - started).as_micros(),
                    (raster_at - metadata_at).as_micros(),
                    raster_at.elapsed().as_micros(),
                    route_label(path),
                );
            }
            Ok(png)
        })?;
        Ok(AppResponse {
            status: 200,
            content_type: format.content_type(),
            body: png,
            cache: true,
            headers: BTreeMap::new(),
        })
    }

    fn batch_metadata(
        &self,
        parameters: &BTreeMap<String, String>,
        body: &[u8],
        content_type: Option<&str>,
    ) -> Result<AppResponse> {
        if !content_type.is_some_and(|s| {
            s.split(';')
                .next()
                .is_some_and(|s| s == "application/json" || s.ends_with("+json"))
        }) {
            return Err("unsupported media type".into());
        }
        let body: Value = serde_json::from_slice(body).map_err(|_| "bad JSON body")?;
        let object = body
            .as_object()
            .ok_or("Request body must be a JSON object")?;
        if object.keys().any(|k| k != "keys") {
            return Err("Unknown field in request body".into());
        }
        let keys: Vec<Vec<String>> = serde_json::from_value(
            object
                .get("keys")
                .ok_or("{'keys': ['Missing data for required field.']}")?
                .clone(),
        )
        .map_err(|_| "keys must be an array of string arrays")?;
        if keys.len() > self.max_metadata {
            return Err(format!(
                "Maximum number of keys exceeded ({}). This limit can be configured in the server settings.",
                self.max_metadata
            ));
        }
        let mut store = self.catalog()?;
        store.refresh()?;
        let mut result = Vec::new();
        for keys in keys {
            // Encode key components so literal plus/percent/slash survive exactly once.
            let path = format!(
                "/metadata/{}",
                keys.iter()
                    .map(|s| encode_path(s))
                    .collect::<Vec<_>>()
                    .join("/")
            );
            let response = self.metadata_from_catalog(&store, &path, parameters)?;
            result
                .push(serde_json::from_slice::<Value>(&response.body).map_err(|e| e.to_string())?);
        }
        json_response(200, &json!(result))
    }

    fn colormap(&self, parameters: &BTreeMap<String, String>) -> Result<AppResponse> {
        let range = parse_numeric_range(
            parameters
                .get("stretch_range")
                .ok_or("{'stretch_range': ['Missing data for required field.']}")?,
        )?;
        let count = parameters
            .get("num_values")
            .map(|v| {
                v.parse::<usize>()
                    .map_err(|_| "Number of samples must be non-negative")
            })
            .transpose()?
            .unwrap_or(255);
        if count > 4096 {
            return Err("num_values exceeds response resource limit".into());
        }
        let colors = parameters
            .get("colormap")
            .map(|n| self.palettes.get(n))
            .transpose()?;
        let mut result = Vec::with_capacity(count);
        for i in 0..count {
            let value = if count <= 1 {
                range[0]
            } else if i == count - 1 {
                range[1]
            } else {
                range[0] + i as f64 * ((range[1] - range[0]) / (count - 1) as f64)
            };
            let r = Raster {
                data_type: 7,
                width: 1,
                height: 1,
                values: vec![value],
                mask: vec![false],
            };
            let index = image::stretch_to_u8(&r, range)[0];
            let rgba = colors
                .as_ref()
                .map(|c| c[index.wrapping_sub(1) as usize])
                .unwrap_or([index, index, index, 255]);
            result.push(json!({"value":value,"rgba":rgba}));
        }
        json_response(200, &json!({"colormap":result}))
    }

    fn compute(&self, path: &str, parameters: &BTreeMap<String, String>) -> Result<AppResponse> {
        let selection = crate::telemetry::timer(crate::telemetry::Stage::Selection);
        let expression = parameters
            .get("expression")
            .ok_or("{'expression': ['Missing data for required field.']}")?;
        let range = parse_numeric_range(
            parameters
                .get("stretch_range")
                .ok_or("{'stretch_range': ['Missing data for required field.']}")?,
        )?;
        if range[1] < range[0]
            || (range[1] == range[0]
                && crate::compatibility::CompatibilityProfile::current().is_terracotta())
        {
            return Err("Upper stretch bounds must be larger than lower bounds".into());
        }
        let palette = parameters
            .get("colormap")
            .map(|n| self.palettes.get(n))
            .transpose()?;
        let size = parameters
            .get("tile_size")
            .map(|v| parse_size(v))
            .transpose()?
            .unwrap_or(self.default_size);
        let parts = path
            .trim_start_matches("/compute/")
            .split('/')
            .collect::<Vec<_>>();
        let preview = parts.last() == Some(&"preview.png");
        let tail = if preview { 1 } else { 3 };
        let mut store = self.catalog()?;
        store.refresh()?;
        let n = store.keys().len() - 1;
        if parts.len() != n + tail {
            return Err("must specify all keys except last one".into());
        }
        let keys = parts[..n]
            .iter()
            .map(|s| percent_decode_path(s))
            .collect::<Result<Vec<_>>>()?;
        let xyz = if preview {
            None
        } else {
            Some((
                parts[n + 1].parse::<f64>().map_err(|_| "invalid tile x")?,
                parts[n + 2]
                    .trim_end_matches(".png")
                    .parse::<f64>()
                    .map_err(|_| "invalid tile y")?,
                parts[n].parse::<u32>().unwrap_or(u32::MAX),
            ))
        };
        let mut requests = Vec::new();
        for i in 1..=5 {
            let var = format!("v{i}");
            if let Some(band) = parameters.get(&var) {
                let mut keys = keys.clone();
                keys.push(band.clone());
                let (_, metadata, source) = store.descriptor(&keys)?.ok_or("No dataset found")?;
                let bounds = if let Some((x, y, z)) = xyz {
                    if !raster::tile_exists(metadata.bounds, x, y, z) {
                        return self.empty_tile(parameters, false);
                    }
                    Some(raster::mercator_tile_bounds(x, y, z))
                } else {
                    None
                };
                requests.push((var, source, bounds));
            }
        }
        drop(store);
        drop(selection);
        let preview_grid = if preview && requests.iter().any(|(_, source, _)| source.mapped()) {
            Some(shared_bounds(requests.iter().map(|(_, source, _)| source))?)
        } else {
            None
        };
        let mut rasters = BTreeMap::new();
        for (var, source, bounds) in requests {
            rasters.insert(
                var,
                source.read(
                    &self.executor,
                    ReadRequest {
                        grid: None,
                        band: 1,
                        bounds_mercator: bounds.or(preview_grid),
                        size,
                        preserve_values: false,
                    },
                )?,
            );
        }
        let operands = rasters
            .iter()
            .map(|(k, v)| (k.clone(), v.as_ref()))
            .collect();
        let result = expressions::evaluate(expression, &operands)
            .map_err(|e| format!("error while executing expression: {e}"))?;
        let pixels = image::stretch_to_u8(&result, range);
        let png = if let Some(colors) = palette {
            image::palette_png(result.width, result.height, &pixels, &colors)?
        } else {
            image::grayscale_png(result.width, result.height, &pixels)?
        };
        let mut response = bytes_response("image/png", png);
        response.cache = true;
        Ok(response)
    }
    fn empty_tile(&self, parameters: &BTreeMap<String, String>, rgb: bool) -> Result<AppResponse> {
        let legacy = crate::compatibility::CompatibilityProfile::current().is_terracotta();
        let size = if legacy {
            self.default_size
        } else {
            parameters
                .get("tile_size")
                .map(|v| parse_size(v))
                .transpose()?
                .unwrap_or(self.default_size)
        };
        let [width, height] = if legacy { size } else { [size[1], size[0]] };
        let mut response = if rgb
            && parameters.get("format").is_some_and(|v| v == "webp")
            && !crate::compatibility::CompatibilityProfile::current().is_terracotta()
        {
            let pixels = vec![0; width * height];
            bytes_response(
                "image/webp",
                image::rgb_image(width, height, [&pixels; 3], image::RgbFormat::Webp(85.0))?,
            )
        } else {
            bytes_response("image/png", image::empty_png(width, height)?)
        };
        response.cache = true;
        Ok(response)
    }

    fn prometheus(&self) -> Result<AppResponse> {
        use std::fmt::Write;
        let mut output = self.executor.cost().prometheus();
        let metrics = serde_json::to_value(self.executor.metrics()).map_err(|e| e.to_string())?;
        for name in [
            "submitted",
            "completed",
            "failed",
            "cancelled_queued",
            "overloaded",
            "timed_out",
            "cache_hits",
            "cache_misses",
            "remote_jobs",
            "prefetch_completed",
            "prefetch_failed",
        ] {
            let _ = writeln!(
                output,
                "# TYPE rcfp_raster_{name}_total counter\nrcfp_raster_{name}_total {}",
                metrics[name]
            );
        }
        for name in [
            "workers",
            "queue_capacity",
            "queued",
            "in_flight",
            "prefetch_in_flight",
            "cache_capacity_bytes",
            "cache_bytes",
            "cache_entries",
        ] {
            let _ = writeln!(
                output,
                "# TYPE rcfp_raster_{name} gauge\nrcfp_raster_{name} {}",
                metrics[name]
            );
        }
        let cache = self.render_cache.metrics();
        for name in ["hits", "misses", "coalesced"] {
            let _ = writeln!(
                output,
                "# TYPE rcfp_render_{name}_total counter\nrcfp_render_{name}_total {}",
                cache[name]
            );
        }
        for name in ["bytes", "capacity_bytes", "entries", "in_flight"] {
            let _ = writeln!(
                output,
                "# TYPE rcfp_render_{name} gauge\nrcfp_render_{name} {}",
                cache[name]
            );
        }
        let _ = writeln!(
            output,
            "# TYPE rcfp_render_admitted gauge\nrcfp_render_admitted {}",
            self.render_in_flight.load(Ordering::Relaxed)
        );
        Ok(bytes_response(
            "text/plain; version=0.0.4; charset=utf-8",
            output.into_bytes(),
        ))
    }

    fn metrics(&self) -> Result<AppResponse> {
        let mut value = serde_json::to_value(self.executor.metrics())
            .map_err(|error| format!("could not encode metrics: {error}"))?;
        let object = value.as_object_mut().expect("metrics object");
        object.insert("render_cache".into(), self.render_cache.metrics());
        object.insert(
            "render_admitted".into(),
            json!(self.render_in_flight.load(Ordering::Relaxed)),
        );
        object.insert("rss_bytes".into(), json!(process_rss_bytes()));
        object.insert(
            "open_file_descriptors".into(),
            json!(open_file_descriptors()),
        );
        object.insert("remote_traffic_note".into(), json!("remote_jobs counts admitted GDAL URL/S3 reads; exact HTTP request/byte traffic is measured at the range server"));
        json_response(200, &value)
    }
}

fn bytes_response(content_type: &'static str, body: Vec<u8>) -> AppResponse {
    AppResponse {
        status: 200,
        content_type,
        body,
        cache: false,
        headers: BTreeMap::new(),
    }
}
fn html_error(status: u16) -> AppResponse {
    let (title, message) = match status {
        400 => (
            "Bad Request",
            "The browser (or proxy) sent a request that this server could not understand.",
        ),
        404 => (
            "Not Found",
            "The requested URL was not found on the server. If you entered the URL manually please check your spelling and try again.",
        ),
        405 => (
            "Method Not Allowed",
            "The method is not allowed for the requested URL.",
        ),
        415 => (
            "Unsupported Media Type",
            "Did not attempt to load JSON data because the request Content-Type was not &#39;application/json&#39;.",
        ),
        _ => (
            "Internal Server Error",
            "The server encountered an internal error and was unable to complete your request. Either the server is overloaded or there is an error in the application.",
        ),
    };
    let mut r=bytes_response("text/html; charset=utf-8",format!("<!doctype html>\n<html lang=en>\n<title>{status} {title}</title>\n<h1>{title}</h1>\n<p>{message}</p>\n").into_bytes());
    r.status = status;
    r
}
fn render_route(path: &str) -> bool {
    if !["/singleband/", "/rgb/", "/compute/"]
        .iter()
        .any(|s| path.starts_with(s))
    {
        return false;
    }
    if path.ends_with("/preview.png") {
        return path != "/singleband/preview.png";
    }
    let parts = path
        .trim_end_matches(".png")
        .rsplit('/')
        .take(3)
        .collect::<Vec<_>>();
    path.ends_with(".png")
        && (!path.starts_with("/singleband/") || path.matches('/').count() >= 5)
        && parts.len() == 3
        && parts
            .iter()
            .all(|p| !p.is_empty() && p.bytes().all(|b| b.is_ascii_digit()))
}
pub(crate) fn api_spec() -> Value {
    let mut spec: Value =
        serde_json::from_str(include_str!("assets/swagger.json")).expect("embedded spec");
    if !crate::compatibility::CompatibilityProfile::current().is_terracotta() {
        spec["paths"]["/metadata"] = json!({"post": {
            "summary": "/metadata (batch)",
            "description": "Retrieve metadata for a list of dataset key combinations. The configured MAX_POST_METADATA_KEYS limits the batch size.",
            "parameters": [spec["paths"]["/metadata/{keys}"]["get"]["parameters"][1].clone()],
            "requestBody": {"required": true, "content": {"application/json": {"schema": {
                "type": "object", "required": ["keys"], "additionalProperties": false,
                "properties": {"keys": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}}}
            }}}},
            "responses": {
                "200": {"description": "Metadata in request order", "content": {"application/json": {"schema": {
                    "type": "array", "items": {"$ref": "#/components/schemas/Metadata"}
                }}}},
                "400": {"description": "Invalid body, columns, or batch size"},
                "404": {"description": "No dataset found for a key combination"},
                "415": {"description": "JSON content type required"}
            }
        }});
        spec["paths"]["/layers"] = json!({"get":{"summary":"List database-backed native layers","responses":{"200":{"description":"Native format marker and layer IDs"},"404":{"description":"Not a native layer database"}}}});
        spec["paths"]["/layers/{id}"] = json!({"get":{"summary":"Describe observations, assets, bands and default rendering","parameters":[{"name":"id","in":"path","required":true,"schema":{"type":"string"}}],"responses":{"200":{"description":"Complete persisted layer definition"},"404":{"description":"Unknown layer"}}}});
        spec["paths"]["/layers/{id}/render"] = json!({"post":{
            "summary":"Render selected native asset bands or expressions on an explicit grid",
            "description":"An empty object uses the rendering specification stored in the database. File locations cannot be supplied in requests.",
            "parameters":[{"name":"id","in":"path","required":true,"schema":{"type":"string"}}],
            "requestBody":{"required":false,"content":{"application/json":{"schema":{"$ref":"#/components/schemas/NativeRenderSpec"}}}},
            "responses":{"200":{"description":"PNG with explicit coverage","content":{"image/png":{"schema":{"type":"string","format":"binary"}}}},"400":{"description":"Invalid bands, expressions, grid, stretch or content type"},"404":{"description":"Unknown layer"},"503":{"description":"Render or raster capacity is full"},"504":{"description":"Raster deadline elapsed; active work continues"}}
        }});
        spec["components"]["schemas"]["NativeRenderSpec"] = json!({
            "type":"object","additionalProperties":false,
            "required":["bindings","values","channels","coverage","grid","encoding"],
            "properties":{
                "bindings":{"type":"object","minProperties":1,"maxProperties":16,"additionalProperties":{"type":"object","additionalProperties":false,"required":["asset","band"],"properties":{"asset":{"type":"string"},"band":{"oneOf":[{"type":"integer","minimum":1},{"type":"string","minLength":1}]}}}},
                "values":{"type":"string","enum":["raw","scaled"]},
                "coverage":{"type":"string","enum":["any_valid","all_valid"]},
                "encoding":{"type":"string","enum":["png"]},
                "grid":{"type":"object","additionalProperties":false,"required":["crs","bounds","size","resampling"],"properties":{"crs":{"type":"string","pattern":"^EPSG:[1-9][0-9]*$"},"bounds":{"type":"array","minItems":4,"maxItems":4,"items":{"type":"number"}},"size":{"type":"array","minItems":2,"maxItems":2,"items":{"type":"integer","minimum":1,"maximum":2048}},"resampling":{"type":"string","enum":["nearest","bilinear","cubic","average"]}}},
                "channels":{"type":"array","minItems":1,"maxItems":3,"items":{"type":"object","additionalProperties":false,"required":["expression"],"properties":{"expression":{"type":"string","minLength":1},"stretch":{"type":"array","nullable":true,"minItems":2,"maxItems":2,"items":{"type":"number"}},"categories":{"type":"array","maxItems":255,"items":{"type":"object","additionalProperties":false,"required":["value","color"],"properties":{"value":{"type":"number"},"color":{"type":"array","minItems":4,"maxItems":4,"items":{"type":"integer","minimum":0,"maximum":255}}}}}}}}
            }
        });
        for (path, entry) in spec["paths"].as_object_mut().expect("paths") {
            if path.starts_with("/rgb/") || path.starts_with("/singleband/") {
                let parameters = entry["get"]["parameters"]
                    .as_array_mut()
                    .expect("parameters");
                if !path.ends_with("preview.png") {
                    for name in if path.starts_with("/rgb/") {
                        vec!["r_band", "g_band", "b_band"]
                    } else {
                        vec!["band"]
                    } {
                        parameters.push(json!({"name":name,"in":"query","required":false,
                            "description":"One-based source TIFF band. Bands other than 1 require an explicit numeric stretch.",
                            "schema":{"type":"integer","minimum":1,"maximum":65535,"default":1}}));
                    }
                }
                if path.starts_with("/rgb/") {
                    parameters.push(json!({"name":"format","in":"query","required":false,
                        "description":"PNG, or lossy WebP for display with the same coverage mask.",
                        "schema":{"type":"string","enum":["png","webp"],"default":"png"}}));
                    parameters.push(json!({"name":"quality","in":"query","required":false,
                        "description":"WebP display quality. Does not change analysis values.",
                        "schema":{"type":"number","minimum":0,"maximum":100,"default":85}}));
                    entry["get"]["responses"]["200"]["content"] =
                        json!({"image/png":{}, "image/webp":{}});
                }
            }
        }
    }
    spec
}

fn update_palette_enums(value: &mut Value, palettes: &Palettes) {
    match value {
        Value::Object(obj) => {
            if let Some(values) = obj.get_mut("enum").and_then(Value::as_array_mut) {
                if values.iter().any(|v| v == "viridis") {
                    let nullable = values.iter().any(Value::is_null);
                    let explicit = values.iter().any(|v| v == "explicit");
                    *values = palettes.0.keys().map(|k| json!(k)).collect();
                    if explicit {
                        values.insert(0, json!("explicit"));
                    }
                    if nullable {
                        values.push(Value::Null);
                    }
                }
            }
            for v in obj.values_mut() {
                update_palette_enums(v, palettes);
            }
        }
        Value::Array(arr) => {
            for v in arr {
                update_palette_enums(v, palettes);
            }
        }
        _ => {}
    }
}

fn process_rss_bytes() -> Option<u64> {
    let status = std::fs::read_to_string("/proc/self/status").ok()?;
    let line = status.lines().find(|line| line.starts_with("VmRSS:"))?;
    let kib = line.split_whitespace().nth(1)?.parse::<u64>().ok()?;
    Some(kib * 1024)
}

fn open_file_descriptors() -> Option<usize> {
    std::fs::read_dir("/proc/self/fd")
        .ok()
        .map(|entries| entries.count())
}

fn json_response(status: u16, value: &Value) -> Result<AppResponse> {
    let mut body = serde_json::to_vec(value).map_err(|error| error.to_string())?;
    body.push(b'\n');
    Ok(AppResponse {
        status,
        content_type: "application/json",
        body,
        cache: false,
        headers: BTreeMap::new(),
    })
}

fn json_error(status: u16, message: &str) -> AppResponse {
    json_response(status, &json!({"message": message})).expect("serializable error")
}

fn dataset_json(key_names: &[&str], dataset: &Dataset) -> Value {
    let object = key_names
        .iter()
        .zip(&dataset.keys)
        .map(|(key, value)| ((*key).to_owned(), Value::String(value.clone())))
        .collect::<Map<_, _>>();
    Value::Object(object)
}

fn split_keys(path: &str) -> Vec<String> {
    path.split('/')
        .filter(|item| !item.is_empty())
        .map(|item| percent_decode_path(item).unwrap_or_else(|_| item.to_owned()))
        .collect()
}

fn parse_query(query: &str) -> BTreeMap<String, String> {
    let mut output = BTreeMap::new();
    for part in query.split('&').filter(|s| !s.is_empty()) {
        let (key, value) = part.split_once('=').unwrap_or((part, ""));
        if let (Ok(key), Ok(value)) = (percent_decode(key), percent_decode(value)) {
            output.entry(key).or_insert(value);
        }
    }
    output
}

fn percent_decode_path(value: &str) -> Result<String> {
    percent_decode(&value.replace('+', "%2B"))
}

fn percent_decode(value: &str) -> Result<String> {
    let bytes = value.as_bytes();
    let mut output = Vec::with_capacity(bytes.len());
    let mut index = 0;
    while index < bytes.len() {
        match bytes[index] {
            b'+' => output.push(b' '),
            b'%' if index + 2 < bytes.len() => {
                let hex = std::str::from_utf8(&bytes[index + 1..index + 3])
                    .map_err(|_| "invalid URL encoding".to_string())?;
                output.push(
                    u8::from_str_radix(hex, 16).map_err(|_| "invalid URL encoding".to_string())?,
                );
                index += 2;
            }
            byte => output.push(byte),
        }
        index += 1;
    }
    String::from_utf8(output).map_err(|_| "URL is not UTF-8".into())
}

fn source_band(parameters: &BTreeMap<String, String>, name: &str, range: &str) -> Result<usize> {
    let Some(raw) = parameters.get(name) else {
        return Ok(1);
    };
    if crate::compatibility::CompatibilityProfile::current().is_terracotta() {
        return Err("source band selection requires the native profile".into());
    }
    let band = raw
        .parse::<usize>()
        .map_err(|_| "source band must be a positive integer")?;
    if !(1..=65535).contains(&band) {
        return Err("source band must be between 1 and 65535".into());
    }
    if band != 1
        && !(name == "band"
            && parameters
                .get("colormap")
                .is_some_and(|name| name == "explicit"))
    {
        parse_numeric_range(
            parameters
                .get(range)
                .ok_or("selected source bands require explicit numeric stretch ranges")?,
        )?;
    }
    Ok(band)
}

fn parse_usize(value: Option<&String>, default: usize, name: &str) -> Result<usize> {
    value
        .map(|value| value.parse().map_err(|_| format!("invalid {name}")))
        .unwrap_or(Ok(default))
}

fn list_value(value: &str) -> Vec<String> {
    if let Some(inner) = value.strip_prefix('[').and_then(|v| v.strip_suffix(']')) {
        inner.split(',').map(str::to_owned).collect()
    } else {
        vec![value.into()]
    }
}

fn parse_size(value: &str) -> Result<[usize; 2]> {
    let values: Vec<Value> = serde_json::from_str(value).map_err(|_| {
        format!("{{'_schema': ['Could not decode value {value} for tile_size as JSON']}}")
    })?;
    let size = values
        .iter()
        .map(|v| {
            v.as_u64()
                .or_else(|| v.as_f64().filter(|v| *v >= 0.0).map(|v| v as u64))
                .or_else(|| v.as_str().and_then(|s| s.parse::<u64>().ok()))
                .and_then(|n| usize::try_from(n).ok())
                .ok_or_else(|| "tile_size must contain integers".to_string())
        })
        .collect::<Result<Vec<_>>>()?;
    if size.len() != 2
        || size
            .iter()
            .any(|dimension| *dimension == 0 || *dimension > 4096)
        || size[0]
            .checked_mul(size[1])
            .is_none_or(|pixels| pixels > 1024 * 1024)
    {
        return Err(
            "tile_size must contain two positive dimensions with at most 1048576 pixels".into(),
        );
    }
    Ok([size[0], size[1]])
}

fn parse_stretch(value: &str, metadata: &Metadata) -> Result<[f64; 2]> {
    if value.trim() == "null" {
        return Ok(metadata.range);
    }
    let items: Vec<Value> = serde_json::from_str(value)
        .map_err(|_| format!("Could not decode value {value} for stretch_range as JSON"))?;
    if items.len() != 2 {
        return Err("stretch_range must contain two values".into());
    }
    let range = [
        stretch_value(&items[0], metadata, 0)?,
        stretch_value(&items[1], metadata, 1)?,
    ];
    if !crate::compatibility::CompatibilityProfile::current().is_terracotta() && range[1] < range[0]
    {
        return Err("Upper stretch bound must be higher than lower bound".into());
    }
    Ok(range)
}

fn stretch_value(value: &Value, metadata: &Metadata, endpoint: usize) -> Result<f64> {
    if value.is_null() {
        return Ok(metadata.range[endpoint]);
    }
    if let Some(number) = value.as_f64() {
        return Ok(number);
    }
    if let Some(percentile) = value
        .as_str()
        .and_then(|value| value.strip_prefix('p'))
        .and_then(|value| value.parse::<usize>().ok())
    {
        let index = if crate::compatibility::CompatibilityProfile::current().is_terracotta() {
            Some(percentile)
        } else {
            match percentile {
                0 => return Ok(metadata.range[0]),
                100 => return Ok(metadata.range[1]),
                1..=99 => Some(percentile - 1),
                _ => None,
            }
        };
        return index
            .and_then(|i| metadata.percentiles.get(i))
            .copied()
            .ok_or_else(|| format!("Invalid percentile, out of range: p{percentile}"));
    }
    Err(format!("Invalid scale value: {value}"))
}

fn parse_palette(value: &str) -> Result<Vec<(f64, Rgba)>> {
    let object: Map<String, Value> = serde_json::from_str(value)
        .map_err(|error| format!("invalid explicit_color_map: {error}"))?;
    if object.len() > 255 {
        return Err("Explicit color map must contain less than 256 values".into());
    }
    object
        .into_iter()
        .map(|(label, color)| {
            let label = label
                .parse::<f64>()
                .map_err(|_| "palette label must be numeric".to_string())?;
            let rgba = if let Some(hex) = color.as_str() {
                let hex = hex.trim_start_matches('#');
                if hex.len() < 6 || !hex.is_ascii() {
                    return Err("palette hex color must contain six digits".into());
                }
                [
                    u8::from_str_radix(&hex[0..2], 16).map_err(|_| "invalid palette hex color")?,
                    u8::from_str_radix(&hex[2..4], 16).map_err(|_| "invalid palette hex color")?,
                    u8::from_str_radix(&hex[4..6], 16).map_err(|_| "invalid palette hex color")?,
                    255,
                ]
            } else if let Some(items) = color.as_array() {
                if items.len() != 3 && items.len() != 4 {
                    return Err("palette colors require RGB or RGBA".into());
                }
                let mut rgba = [0, 0, 0, 255];
                for (index, item) in items.iter().enumerate() {
                    let value = item
                        .as_f64()
                        .or_else(|| item.as_str().and_then(|s| s.parse::<f64>().ok()))
                        .filter(|v| v.is_finite())
                        .ok_or("palette channels must be numbers")?;
                    if !(0.0..=255.0).contains(&value.trunc()) {
                        return Err("legacy internal error".into());
                    }
                    rgba[index] = value.trunc() as u8;
                }
                rgba
            } else {
                return Err("invalid palette color".into());
            };
            Ok((label, rgba))
        })
        .collect()
}

fn parse_numeric_range(value: &str) -> Result<[f64; 2]> {
    let values: Vec<Value> =
        serde_json::from_str(value).map_err(|_| "stretch_range must contain two numbers")?;
    if values.len() != 2 {
        return Err("stretch_range must contain two numbers".into());
    }
    let number = |value: &Value| {
        value
            .as_f64()
            .or_else(|| value.as_str().and_then(|s| s.parse::<f64>().ok()))
            .filter(|v| v.is_finite())
            .ok_or_else(|| "stretch_range must contain finite numbers".to_string())
    };
    Ok([number(&values[0])?, number(&values[1])?])
}

fn encode_path(value: &str) -> String {
    value
        .bytes()
        .map(|b| {
            if b.is_ascii_alphanumeric() || b"-_.~".contains(&b) {
                (b as char).to_string()
            } else {
                format!("%{b:02X}")
            }
        })
        .collect()
}

fn uses_metadata_range(raw: Option<&String>) -> bool {
    raw.and_then(|r| serde_json::from_str::<Value>(r).ok())
        .is_none_or(|v| v.as_array().is_none_or(|a| a.iter().any(Value::is_null)))
}

fn route_label(path: &str) -> &'static str {
    for (prefix, label) in [
        ("/singleband/", "/singleband"),
        ("/rgb/", "/rgb"),
        ("/compute/", "/compute"),
        ("/metadata/", "/metadata"),
        ("/static/", "/static"),
        ("/layers/", "/layers"),
    ] {
        if path.starts_with(prefix) {
            return label;
        }
    }
    match path {
        "/keys" => "/keys",
        "/datasets" => "/datasets",
        "/metadata" => "/metadata",
        "/colormap" => "/colormap",
        "/layers" => "/layers",
        "/__terracotta/metrics" | "/__terracotta/metrics/prometheus" => "/metrics",
        _ => "/other",
    }
}
pub(crate) fn safe_method(method: &str) -> &'static str {
    match method {
        "GET" => "GET",
        "HEAD" => "HEAD",
        "POST" => "POST",
        "OPTIONS" => "OPTIONS",
        _ => "OTHER",
    }
}

fn default_range(metadata: &Metadata) -> [f64; 2] {
    serde_json::from_value(metadata.metadata["defaults"]["stretch"].clone())
        .unwrap_or(metadata.range)
}

fn shared_bounds<'a>(
    mut sources: impl Iterator<Item = &'a crate::catalog::Source>,
) -> Result<[f64; 4]> {
    sources.try_fold(
        [
            f64::INFINITY,
            f64::INFINITY,
            f64::NEG_INFINITY,
            f64::NEG_INFINITY,
        ],
        |bounds, source| {
            let b = source.preview_bounds()?;
            Ok([
                bounds[0].min(b[0]),
                bounds[1].min(b[1]),
                bounds[2].max(b[2]),
                bounds[3].max(b[3]),
            ])
        },
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn decodes_query_values() {
        assert_eq!(percent_decode("%5B128%2C+256%5D").unwrap(), "[128, 256]");
    }
    #[test]
    fn rejects_tile_allocations_above_pixel_budget() {
        assert_eq!(parse_size("[1024,1024]").unwrap(), [1024, 1024]);
        assert!(parse_size("[4096,4096]").is_err());
        assert!(parse_size("[4096,256]").is_ok());
    }
    #[test]
    fn preserves_palette_order() {
        let palette = parse_palette(r##"{"10":"006400","20":"ffbb22","100":"fae6a0"}"##).unwrap();
        assert_eq!(
            palette.iter().map(|item| item.0).collect::<Vec<_>>(),
            vec![10.0, 20.0, 100.0]
        );
    }
}
