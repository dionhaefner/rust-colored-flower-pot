//! Shared HTTP contract and bounded raster orchestration.

use crate::Result;
use crate::execution::{ExecutionConfig, OVERLOADED, RasterExecutor, TIMED_OUT};
use crate::image::{self, Rgba};
use crate::raster::{self, Raster, ReadRequest};
use crate::storage::{Dataset, Metadata, Store};
use crate::{color, expressions, palettes::Palettes};
use serde_json::{Map, Value, json};
use std::collections::{BTreeMap, HashMap};
use std::path::Path;
use std::sync::mpsc::{self, TrySendError};
use std::sync::{Arc, Mutex};
use std::time::Instant;
use tiny_http::{Header, Method, Response, Server, StatusCode};

pub struct Application {
    settings: Map<String, Value>,
    store: Mutex<Store>,
    palettes: Palettes,
    default_size: [usize; 2],
    max_metadata: usize,
    executor: RasterExecutor,
    http_workers: usize,
    http_queue_capacity: usize,
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
        let config = ExecutionConfig::from_env()?;
        // Enough bounded HTTP workers must be able to reach raster admission
        // concurrently for a full raster queue to produce an immediate 503.
        let http_workers = config
            .workers
            .saturating_add(config.queue_capacity)
            .saturating_add(1);
        let http_queue_capacity = config.queue_capacity;
        Ok(Self {
            settings: crate::settings::snapshot(),
            store: Mutex::new(Store::open(database)?),
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
            http_workers,
            http_queue_capacity,
        })
    }

    pub fn gdal_version(&self) -> String {
        self.executor.version().to_owned()
    }

    /// Execute a request without binding a socket. Deployment adapters use
    /// this entry point so they cannot drift onto a second rendering path.
    pub fn request(&self, method: &str, url: &str) -> AppResponse {
        self.request_full(method, url, &BTreeMap::new(), &[])
    }

    pub fn request_full(
        &self,
        method: &str,
        url: &str,
        headers: &BTreeMap<String, String>,
        body: &[u8],
    ) -> AppResponse {
        crate::settings::scope(self.settings.clone(), || {
            self.request_scoped(method, url, headers, body)
        })
    }

    fn request_scoped(
        &self,
        method: &str,
        url: &str,
        headers: &BTreeMap<String, String>,
        body: &[u8],
    ) -> AppResponse {
        let started = std::time::Instant::now();
        let trace_start = std::time::SystemTime::now();
        if body.len() > 1024 * 1024 {
            return json_error(413, "Request body is too large");
        }
        let parsed_method = method.parse::<Method>().unwrap_or(Method::Post);
        let mut response = self.handle(
            &parsed_method,
            url,
            body,
            headers.get("content-type").map(String::as_str),
        );
        let path = url.split('?').next().unwrap_or(url);
        let tile = ["/singleband/", "/rgb/", "/compute/"]
            .iter()
            .any(|p| path.starts_with(p));
        let metadata = ["/keys", "/datasets", "/colormap", "/metadata"].contains(&path)
            || path.starts_with("/metadata/");
        if (tile || metadata) && response.status != 405 && (render_route(path) || metadata) {
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
        crate::tracing::request(headers, method, path, response.status, trace_start);
        let profile = crate::settings::var("TC_FLASK_PROFILE").is_ok_and(|s| s == "true");
        let debug = crate::settings::var("TC_DEBUG").is_ok_and(|s| s == "true");
        let level = crate::settings::var("TC_LOGLEVEL").unwrap_or_else(|_| "warning".into());
        if profile || debug || ["debug", "info"].contains(&level.as_str()) {
            // Query strings may contain user data; log route and timing only.
            eprintln!(
                "{}",
                json!({"event":"http_request", "method":method, "path":path, "status":response.status, "elapsed_ms":started.elapsed().as_secs_f64()*1000.0, "profile":profile})
            );
        }
        response
    }

    pub fn serve(self, address: &str) -> Result<()> {
        let server =
            Server::http(address).map_err(|error| format!("could not bind {address}: {error}"))?;
        let http_workers = self.http_workers;
        let http_queue_capacity = self.http_queue_capacity;
        let application = Arc::new(self);
        let (sender, receiver) = mpsc::sync_channel(http_queue_capacity);
        let receiver = Arc::new(Mutex::new(receiver));
        for worker_id in 0..http_workers {
            let application = application.clone();
            let receiver = receiver.clone();
            std::thread::Builder::new()
                .name(format!("terracotta-http-{worker_id}"))
                .spawn(move || {
                    loop {
                        let request = {
                            let receiver = receiver.lock().expect("HTTP receiver lock");
                            receiver.recv()
                        };
                        let Ok(request) = request else {
                            return;
                        };
                        application.respond(request);
                    }
                })
                .map_err(|error| format!("could not start HTTP worker: {error}"))?;
        }
        eprintln!(
            "RCFP listening on http://{address} (HTTP workers {}, raster workers {}, queue {})",
            http_workers,
            application.executor.metrics().workers,
            http_queue_capacity
        );
        for request in server.incoming_requests() {
            match sender.try_send(request) {
                Ok(()) => {}
                Err(TrySendError::Full(request)) => {
                    respond(request, json_error(503, "HTTP request queue is full"));
                }
                Err(TrySendError::Disconnected(request)) => {
                    respond(request, json_error(503, "HTTP workers are unavailable"));
                    return Err("HTTP worker pool stopped".into());
                }
            }
        }
        Ok(())
    }

    fn respond(&self, mut request: tiny_http::Request) {
        let headers = request
            .headers()
            .iter()
            .map(|h| {
                (
                    h.field.as_str().as_str().to_ascii_lowercase(),
                    h.value.as_str().to_owned(),
                )
            })
            .collect();
        let mut body = Vec::new();
        use std::io::Read;
        if request
            .as_reader()
            .take(1024 * 1024 + 1)
            .read_to_end(&mut body)
            .is_err()
        {
            respond(request, json_error(400, "Could not read request body"));
            return;
        }
        if body.len() > 1024 * 1024 {
            respond(request, json_error(413, "Request body is too large"));
            return;
        }
        let is_tile =
            request.url().starts_with("/singleband/") || request.url().starts_with("/rgb/");
        let response = self.request_full(request.method().as_str(), request.url(), &headers, &body);
        let delivered = response.status == 200 && is_tile;
        respond(request, response);
        if delivered {
            self.executor.wake_prefetch();
        }
    }

    fn handle(
        &self,
        method: &Method,
        url: &str,
        body: &[u8],
        content_type: Option<&str>,
    ) -> AppResponse {
        let (raw_path, query) = url.split_once('?').unwrap_or((url, ""));
        let decoded_path = percent_decode_path(raw_path).unwrap_or_else(|_| raw_path.into());
        let decoded_path = decoded_path.replace('%', "%25");
        let path = decoded_path.as_str();
        let parameters = parse_query(query);
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
            "/__terracotta/metrics",
        ]
        .contains(&path)
            || path.starts_with("/metadata/")
            || render_route(path);
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
                let mut spec: Value = serde_json::from_str(include_str!("assets/swagger.json"))
                    .expect("embedded spec");
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
            _ if path.starts_with("/metadata/") => self.metadata(path, &parameters),
            _ if path.starts_with("/singleband/") => self.singleband(path, &parameters),
            _ if path.starts_with("/rgb/") => self.rgb(path, &parameters),
            _ if path.starts_with("/compute/") => self.compute(path, &parameters),
            _ => return html_error(404),
        };
        match result {
            Ok(response) => response,
            Err(error) if error == "No dataset found" => json_error(404, &error),
            Err(error) if error == "tile out of bounds" => {
                self.empty_tile().unwrap_or_else(|e| json_error(500, &e))
            }
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
        let mut store = self.store.lock().expect("store lock");
        store.refresh()?;
        json_response(
            200,
            &json!({"keys": store.keys().iter().map(|k| if k.description.is_empty() {json!({"key":k.key})} else {json!(k)}).collect::<Vec<_>>()}),
        )
    }

    fn identity(&self) -> Result<AppResponse> {
        json_response(
            200,
            &json!({
                "implementation": crate::IMPLEMENTATION_VERSION,
                "protocol_compatibility": crate::TERRACOTTA_COMPATIBILITY_VERSION,
                "database_compatibility": crate::DATABASE_COMPATIBILITY
            }),
        )
    }

    fn datasets(&self, parameters: &BTreeMap<String, String>) -> Result<AppResponse> {
        let mut store = self.store.lock().expect("store lock");
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
        let order = parameters
            .get("order_by")
            .map(|value| {
                if value.starts_with('[') {
                    list_value(value)
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
        let mut store = self.store.lock().expect("store lock");
        store.refresh()?;
        self.metadata_from_store(&store, path, parameters)
    }
    fn metadata_from_store(
        &self,
        store: &Store,
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
                return Err("legacy internal error".into());
            }
            let object = value.as_object_mut().expect("metadata object");
            object.retain(|key, _| key == "keys" || columns.contains(key));
        }
        json_response(200, &value)
    }

    fn singleband(&self, path: &str, parameters: &BTreeMap<String, String>) -> Result<AppResponse> {
        let started = Instant::now();
        let components = path
            .trim_start_matches("/singleband/")
            .split('/')
            .collect::<Vec<_>>();
        let mut store = self.store.lock().expect("store lock");
        store.refresh()?;
        let key_count = store.keys().len();
        if components.len() != key_count + 1 && components.len() != key_count + 3 {
            return Err("invalid singleband route".into());
        }
        let keys = components[..key_count]
            .iter()
            .map(|item| percent_decode_path(item))
            .collect::<Result<Vec<_>>>()?;
        let dataset = store
            .dataset(&keys)?
            .ok_or_else(|| "No dataset found".to_string())?;
        let metadata = store
            .metadata(&keys)?
            .ok_or_else(|| "No dataset found".to_string())?;
        let legacy_decimal = store.legacy_decimal_stretch();
        drop(store);
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
                return Ok(AppResponse {
                    status: 200,
                    content_type: "image/png",
                    body: image::empty_png(self.default_size[0], self.default_size[1])?,
                    cache: true,
                    headers: BTreeMap::new(),
                });
            }
            Some(raster::mercator_tile_bounds(x, y, z))
        };
        let operations = parameters
            .get("color_transform")
            .map(|v| color::parse(v, 1))
            .transpose()?
            .unwrap_or_default();
        let colormap = parameters.get("colormap").map(String::as_str);
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
        let raster = self.executor.read(
            &dataset.path,
            ReadRequest {
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
                .unwrap_or(metadata.range);
            if legacy_decimal && uses_metadata_range(parameters.get("stretch_range")) {
                return Err("legacy internal error".into());
            }
            let mut channels = vec![color::normalize(&raster, range)];
            color::apply(&mut channels, &operations);
            let pixels = image::stretch_to_u8(&channels[0], [0.0, 1.0]);
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
                path,
            );
        }
        Ok(AppResponse {
            status: 200,
            content_type: "image/png",
            body: png,
            cache: true,
            headers: BTreeMap::new(),
        })
    }

    fn rgb(&self, path: &str, parameters: &BTreeMap<String, String>) -> Result<AppResponse> {
        let started = Instant::now();
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
        let mut store = self.store.lock().expect("store lock");
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

        let mut paths = Vec::with_capacity(3);
        let mut metadata = Vec::with_capacity(3);
        for band in &band_values {
            let mut keys = some_keys.clone();
            keys.push(band.clone());
            paths.push(
                store
                    .dataset(&keys)?
                    .ok_or_else(|| "No dataset found".to_string())?
                    .path,
            );
            metadata.push(
                store
                    .metadata(&keys)?
                    .ok_or_else(|| "No dataset found".to_string())?,
            );
        }
        let legacy_decimal = store.legacy_decimal_stretch();
        drop(store);
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
                return Ok(AppResponse {
                    status: 200,
                    content_type: "image/png",
                    body: image::empty_png(self.default_size[0], self.default_size[1])?,
                    cache: true,
                    headers: BTreeMap::new(),
                });
            }
            Some(raster::mercator_tile_bounds(x, y, z))
        };
        let channel_bounds = metadata
            .iter()
            // None independently frames each source inside the raster reader,
            // including different RGB extents and antimeridian wrapping.
            .map(|_| tile_bounds)
            .collect::<Vec<_>>();

        let mut ranges = Vec::with_capacity(3);
        for (index, channel) in channel_names.iter().enumerate() {
            let name = format!("{channel}_range");
            let range = parameters
                .get(&name)
                .map(|value| parse_stretch(value, &metadata[index]))
                .transpose()?
                .unwrap_or(metadata[index].range);
            if range[1] < range[0] {
                return Err("Upper stretch bound must be higher than lower bound".into());
            }
            if legacy_decimal && uses_metadata_range(parameters.get(&name)) {
                return Err("legacy internal error".into());
            }
            ranges.push(range);
        }

        // A repeated band is read once and independently stretched into each
        // requested output channel.
        let mut unique_requests = Vec::new();
        let mut channel_sources = Vec::with_capacity(3);
        let mut identities = HashMap::new();
        for (channel, path) in paths.into_iter().enumerate() {
            let identity = (
                path.to_string_lossy().into_owned(),
                channel_bounds[channel].map(|bounds| bounds.map(f64::to_bits)),
            );
            let index = if let Some(index) = identities.get(&identity) {
                *index
            } else {
                let index = unique_requests.len();
                identities.insert(identity, index);
                unique_requests.push((path, channel_bounds[channel]));
                index
            };
            channel_sources.push(index);
        }
        let results: Vec<Result<Arc<Raster>>> = std::thread::scope(|scope| {
            let handles = unique_requests
                .iter()
                .map(|(path, bounds)| {
                    scope.spawn(|| {
                        self.executor.read(
                            path,
                            ReadRequest {
                                bounds_mercator: *bounds,
                                size,
                                preserve_values: false,
                            },
                        )
                    })
                })
                .collect::<Vec<_>>();
            handles
                .into_iter()
                .map(|handle| {
                    handle
                        .join()
                        .unwrap_or_else(|_| Err("raster worker request panicked".into()))
                })
                .collect()
        });
        let rasters = results.into_iter().collect::<Result<Vec<_>>>()?;
        let raster_at = Instant::now();
        let mut normalized = channel_sources
            .iter()
            .enumerate()
            .map(|(channel, source)| color::normalize(&rasters[*source], ranges[channel]))
            .collect::<Vec<_>>();
        color::apply(&mut normalized, &operations);
        let channels = normalized
            .iter()
            .map(|r| image::stretch_to_u8(r, [0.0, 1.0]))
            .collect::<Vec<_>>();
        let first = &rasters[channel_sources[0]];
        if channel_sources.iter().any(|source| {
            rasters[*source].width != first.width || rasters[*source].height != first.height
        }) {
            return Err("RGB bands produced different output dimensions".into());
        }
        let png = image::rgb_png(
            first.width,
            first.height,
            [&channels[0], &channels[1], &channels[2]],
        )?;
        if std::env::var_os("TC_PROFILE_HTTP").is_some() {
            eprintln!(
                "http_profile mode=rgb metadata_us={} raster_us={} render_us={} path={}",
                (metadata_at - started).as_micros(),
                (raster_at - metadata_at).as_micros(),
                raster_at.elapsed().as_micros(),
                path,
            );
        }
        Ok(AppResponse {
            status: 200,
            content_type: "image/png",
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
        let mut store = self.store.lock().expect("store lock");
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
            let response = self.metadata_from_store(&store, &path, parameters)?;
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
        if count > 1_000_000 {
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
        let expression = parameters
            .get("expression")
            .ok_or("{'expression': ['Missing data for required field.']}")?;
        let range = parse_numeric_range(
            parameters
                .get("stretch_range")
                .ok_or("{'stretch_range': ['Missing data for required field.']}")?,
        )?;
        if range[1] <= range[0] {
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
        let mut store = self.store.lock().expect("store lock");
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
                let dataset = store.dataset(&keys)?.ok_or("No dataset found")?;
                let metadata = store.metadata(&keys)?.ok_or("No dataset found")?;
                let bounds = if let Some((x, y, z)) = xyz {
                    if !raster::tile_exists(metadata.bounds, x, y, z) {
                        return self.empty_tile();
                    }
                    Some(raster::mercator_tile_bounds(x, y, z))
                } else {
                    None
                };
                requests.push((var, dataset.path, bounds));
            }
        }
        drop(store);
        let mut rasters = BTreeMap::new();
        for (var, path, bounds) in requests {
            rasters.insert(
                var,
                self.executor.read(
                    &path,
                    ReadRequest {
                        bounds_mercator: bounds,
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
    fn empty_tile(&self) -> Result<AppResponse> {
        let mut response = bytes_response(
            "image/png",
            image::empty_png(self.default_size[0], self.default_size[1])?,
        );
        response.cache = true;
        Ok(response)
    }

    fn metrics(&self) -> Result<AppResponse> {
        let mut value = serde_json::to_value(self.executor.metrics())
            .map_err(|error| format!("could not encode metrics: {error}"))?;
        let object = value.as_object_mut().expect("metrics object");
        object.insert("rss_bytes".into(), json!(process_rss_bytes()));
        object.insert(
            "open_file_descriptors".into(),
            json!(open_file_descriptors()),
        );
        object.insert("remote_traffic_note".into(), json!("remote_jobs counts admitted GDAL URL/S3 reads; exact HTTP request/byte traffic is measured at the range server"));
        json_response(200, &value)
    }
}

fn respond(request: tiny_http::Request, response: AppResponse) {
    let mut outgoing =
        Response::from_data(response.body).with_status_code(StatusCode(response.status));
    outgoing.add_header(
        Header::from_bytes("Content-Type", response.content_type).expect("valid header"),
    );
    if response.cache {
        outgoing.add_header(Header::from_bytes("Cache-Control", "no-cache").unwrap());
    }
    for (key, value) in response.headers {
        if let Ok(header) = Header::from_bytes(key, value) {
            outgoing.add_header(header);
        }
    }
    if let Err(error) = request.respond(outgoing) {
        eprintln!("response error: {error}");
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
    {
        return Err("tile_size must contain two positive dimensions no larger than 4096".into());
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
    Ok([
        stretch_value(&items[0], metadata, 0)?,
        stretch_value(&items[1], metadata, 1)?,
    ])
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
        return metadata
            .percentiles
            .get(percentile)
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

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn decodes_query_values() {
        assert_eq!(percent_decode("%5B128%2C+256%5D").unwrap(), "[128, 256]");
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
