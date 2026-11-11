//! Read-only ingestion planning and bounded raster diagnostics.
use crate::{Result, ingest, raster::Gdal, settings, storage::SqliteStore};
use serde_json::{Map, Value, json};
use std::{
    io::{Read, Seek, Write},
    path::{Path, PathBuf},
    process::{Command, Stdio},
    time::{Duration, Instant},
};

const HELP: &str = "Usage: rcfp inspect [--raster FILE | -r PATTERN | -d DATABASE] [-o DESTINATION] [--rgb-key KEY] [--bands 1,2,3] [--render-probe] [--probe-remote] [--json]\nWithout a source, inspect uses DRIVER_PATH or reports configuration only.\nInspection never creates or updates catalogs. Raster headers are inspected locally; remote requests require --probe-remote. Render probes sample each selected band into a 256x256 grayscale PNG in memory.\nLimits: 100 datasets, 256 bands/file, 10 seconds/file; HTTP probe 5 seconds and 1024 body bytes, no redirects.\nExit 0: no errors (warnings/skips allowed). Exit 2: invalid input or failed checks.";

pub fn is_inspection(args: &[String]) -> bool {
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "-c" | "--config" | "--loglevel" | "--compat-profile" => i += 2,
            s if s.starts_with("--compat-profile=") => i += 1,
            s => return s == "inspect",
        }
    }
    false
}

pub fn configuration_error(args: &[String]) {
    let mut report = base_report(Map::new());
    issue(
        &mut report,
        "error",
        "configuration",
        "Configuration could not be loaded or validated.",
        "Check the TOML path, unprefixed setting names and accepted values in the configuration reference; global options precede inspect.",
        None,
    );
    emit(&report, args.iter().any(|a| a == "--json"));
}

fn base_report(config: Map<String, Value>) -> Value {
    json!({"schema_version":1, "ok":true, "configuration":config,
        "source":null, "destination":null, "ordered_keys":[], "datasets":[], "issues":[],
        "limits":{"datasets":100,"bands_per_file":256,"seconds_per_file":10,
            "render_size":[256,256],"remote_seconds":5,"remote_body_bytes":1024}})
}

fn issue(
    report: &mut Value,
    severity: &str,
    code: &str,
    message: &str,
    next: &str,
    dataset: Option<usize>,
) {
    report["issues"].as_array_mut().unwrap().push(json!({
        "severity":severity, "code":code, "message":message, "next_step":next, "dataset":dataset
    }));
    if severity == "error" {
        report["ok"] = false.into();
    }
}

fn location(value: &str) -> String {
    let prefix = ["/vsicurl/", "/vsis3/"]
        .into_iter()
        .find(|p| value.starts_with(p));
    if let Some(prefix) = prefix {
        return format!("{prefix}{}", location(&value[prefix.len()..]));
    }
    if value.contains("://") {
        if let Ok(mut url) = url::Url::parse(value) {
            let _ = url.set_username("");
            let _ = url.set_password(None);
            url.set_query(None);
            url.set_fragment(None);
            return url.to_string();
        }
        return "[redacted location]".into();
    }
    value.split(['?', '#']).next().unwrap_or("").into()
}

fn configuration(origins: &Map<String, Value>) -> Result<Map<String, Value>> {
    let mut values = Map::new();
    for (key, value) in settings::snapshot() {
        let origin = origins.get(&key).cloned().unwrap_or_else(|| {
            let variable = if key == "COMPAT_PROFILE" {
                "RCFP_COMPAT_PROFILE".into()
            } else {
                format!("TC_{key}")
            };
            if std::env::var_os(variable).is_some() {
                "environment".into()
            } else {
                "default".into()
            }
        });
        let safe = if value.is_number()
            || value.is_boolean()
            || value.is_null()
            || [
                "DEFAULT_TILE_SIZE",
                "LAZY_LOADING_MAX_SHAPE",
                "COMPAT_PROFILE",
                "LOGLEVEL",
                "RESAMPLING_METHOD",
                "REPROJECTION_METHOD",
            ]
            .contains(&key.as_str())
        {
            value
        } else if ["DRIVER_PATH", "REMOTE_DB_CACHE_DIR"].contains(&key.as_str()) {
            location(value.as_str().unwrap_or("")).into()
        } else {
            "[redacted]".into()
        };
        values.insert(key, json!({"value":safe, "origin":origin}));
    }
    // Validate the same environment-only resource controls used by applications.
    let execution = crate::execution::ExecutionConfig::from_env()?;
    for (key, value, fallback) in [
        (
            "TC_RASTER_WORKERS",
            json!(execution.workers),
            "USE_MULTIPROCESSING",
        ),
        (
            "TC_RASTER_QUEUE_CAPACITY",
            json!(execution.queue_capacity),
            "default",
        ),
        (
            "TC_RASTER_TIMEOUT_MS",
            json!(execution.wait_timeout.as_millis()),
            "default",
        ),
        (
            "TC_RASTER_CACHE_BYTES",
            json!(execution.cache_bytes),
            "RASTER_CACHE_SIZE",
        ),
        (
            "TC_REMOTE_RASTER_CACHE_TTL_SECONDS",
            json!(execution.remote_cache_ttl.as_secs()),
            "default",
        ),
    ] {
        values.insert(key.into(), json!({"value":value, "origin":if std::env::var_os(key).is_some(){"environment"}else{fallback}}));
    }
    for key in [
        "TC_GDAL_OPTIONS",
        "TC_EXTRA_CMAP_FOLDER",
        "AWS_PROFILE",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_WEB_IDENTITY_TOKEN_FILE",
        "AWS_ROLE_ARN",
        "AWS_ENDPOINT_URL_S3",
        "GDAL_DATA",
        "PROJ_DATA",
        "TERRACOTTA_GDAL_LIBRARY",
    ] {
        if std::env::var_os(key).is_some() {
            values.insert(
                key.into(),
                json!({"value":"[redacted]", "origin":"environment"}),
            );
        }
    }
    let legacy = crate::compatibility::CompatibilityProfile::current().is_terracotta();
    for key in ["ASYNC_TIFF", "NATIVE_DIRECT_READ"] {
        values
            .get_mut(key)
            .unwrap()
            .as_object_mut()
            .unwrap()
            .insert(
                "active".into(),
                json!(!legacy && settings::flag(&format!("TC_{key}"), true)?),
            );
    }
    Ok(values)
}

pub fn command(args: &[String], origins: &Map<String, Value>) -> Result<()> {
    if args.iter().any(|a| a == "--help" || a == "-h") {
        println!("{HELP}");
        return Ok(());
    }
    let json_output = args.iter().any(|a| a == "--json");
    let mut report = base_report(Map::new());
    let _ = inspect(args, origins, &mut report);
    emit(&report, json_output);
    if report["ok"] == true {
        Ok(())
    } else {
        Err("inspection found errors; follow the reported next steps".into())
    }
}

fn inspect(
    args: &[String],
    origins: &Map<String, Value>,
    report: &mut Value,
) -> std::result::Result<(), ()> {
    macro_rules! fail {
        ($code:expr, $message:expr, $next:expr) => {{
            issue(report, "error", $code, $message, $next, None);
            return Err(());
        }};
    }
    let mut pattern = None;
    let mut raster = None;
    let mut database = None;
    let mut destination = None;
    let mut rgb = None;
    let mut bands = None;
    let mut render = false;
    let mut remote = false;
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--raster" | "-r" | "--raster-pattern" | "-d" | "--database" | "-o"
            | "--output-file" | "--rgb-key" | "--bands" => {
                let Some(value) = args.get(i + 1) else {
                    fail!("arguments", "An option requires a value.", HELP);
                };
                if value.starts_with('-') {
                    fail!(
                        "arguments",
                        "An option requires a value before the next flag.",
                        HELP
                    );
                }
                match args[i].as_str() {
                    "--raster" => raster = Some(value.as_str()),
                    "-r" | "--raster-pattern" => pattern = Some(value.as_str()),
                    "-d" | "--database" => database = Some(value.as_str()),
                    "-o" | "--output-file" => destination = Some(value.as_str()),
                    "--rgb-key" => rgb = Some(value.as_str()),
                    _ => {
                        let parsed = value
                            .split(',')
                            .map(str::parse::<usize>)
                            .collect::<std::result::Result<Vec<_>, _>>();
                        let Ok(parsed) = parsed else {
                            fail!(
                                "bands",
                                "Invalid band selection.",
                                "Use one-based indices, for example --bands 1,2,3."
                            );
                        };
                        if parsed.is_empty() || parsed.len() > 256 || parsed.contains(&0) {
                            fail!(
                                "bands",
                                "Invalid band selection.",
                                "Select between 1 and 256 one-based bands."
                            );
                        }
                        bands = Some(parsed);
                    }
                }
                i += 1;
            }
            "--json" => {}
            "--render-probe" => render = true,
            "--probe-remote" => remote = true,
            _ => fail!("arguments", "Unknown inspection argument.", HELP),
        }
        i += 1;
    }
    report["configuration"] = match configuration(origins) {
        Ok(c) => json!(c),
        Err(_) => {
            fail!(
                "configuration",
                "Invalid execution or raster setting.",
                "Check the accepted resource-control ranges in the configuration reference."
            );
        }
    };
    if [pattern, raster, database]
        .iter()
        .filter(|v| v.is_some())
        .count()
        > 1
    {
        fail!(
            "arguments",
            "Choose one inspection source.",
            "Use --raster, -r PATTERN or -d DATABASE."
        );
    }
    if rgb.is_some() && pattern.is_none()
        || destination.is_some() && pattern.is_none()
        || bands.is_some() && raster.is_none() && pattern.is_none()
    {
        fail!(
            "arguments",
            "Options do not apply to this source.",
            "Use --rgb-key and -o with -r; use --bands with --raster or -r. Catalog bindings select their own physical bands."
        );
    }
    let configured = settings::var("TC_DRIVER_PATH").unwrap_or_default();
    let database = database.or_else(|| {
        (!configured.is_empty() && pattern.is_none() && raster.is_none())
            .then_some(configured.as_str())
    });
    if (render || remote) && database.is_none() && pattern.is_none() && raster.is_none() {
        fail!(
            "arguments",
            "A probe requires an inspection source.",
            "Use --raster, -r PATTERN or -d DATABASE, or configure DRIVER_PATH."
        );
    }
    let mut entries: Vec<(Vec<String>, PathBuf, Vec<usize>)> = Vec::new();
    if let Some(pattern) = pattern {
        report["source"] = json!({"kind":"pattern", "location":location(pattern), "origin":"cli"});
        match ingest::raster_pattern(pattern, rgb) {
            Ok((keys, data)) => {
                report["ordered_keys"] = json!(keys.iter().map(|k| &k.key).collect::<Vec<_>>());
                entries = data
                    .into_iter()
                    .map(|d| (d.keys, d.path, bands.clone().unwrap_or_else(|| vec![1])))
                    .collect();
            }
            Err(_) => {
                fail!(
                    "pattern",
                    "Filename pattern could not produce unique datasets.",
                    "Quote the pattern, include named placeholders such as '{band}.tif', check files exist, and use distinct key values; --rgb-key must name a placeholder."
                );
            }
        }
        if let Some(destination) = destination {
            let provider = crate::storage::provider::Provider::detect(
                destination,
                settings::snapshot()["DRIVER_PROVIDER"].as_str(),
            );
            if !matches!(provider, Ok(crate::storage::provider::Provider::Sqlite))
                || (destination.contains("://") && !destination.starts_with("sqlite:///"))
            {
                fail!(
                    "destination",
                    "Destination requires a remote or unsupported catalog provider.",
                    "Inspect a local SQLite destination; validate remote SQL/S3 connections in the deployment environment."
                );
            }
            let local = match crate::storage::provider::local_path(destination) {
                Ok(p) => p,
                Err(_) => {
                    fail!(
                        "destination",
                        "Destination location could not be resolved.",
                        "Use a local SQLite path or sqlite:/// URL."
                    );
                }
            };
            let Some(destination) = local.to_str() else {
                fail!(
                    "destination",
                    "Destination is not a supported text path.",
                    "Choose a catalog path with valid Unicode."
                );
            };
            report["destination"] = json!({"location":absolute(destination), "exists":Path::new(destination).exists(), "origin":"cli", "raster_storage":"paths only; imagery is not copied"});
            if is_remote(destination) || destination.contains("://") {
                fail!(
                    "destination",
                    "Destination cannot be checked without a remote catalog connection.",
                    "Inspect a local SQLite destination; use deployment-specific connection checks for SQL/S3."
                );
            }
            let path = Path::new(destination);
            if path.exists() {
                match SqliteStore::open_read_only(path) {
                    Ok(store) if json!(store.key_names()) == report["ordered_keys"] => {}
                    Ok(_) => {
                        fail!(
                            "destination_keys",
                            "Existing catalog has different ordered keys.",
                            "Use a new catalog path or match the existing key order before ingestion."
                        );
                    }
                    Err(_) => {
                        fail!(
                            "destination",
                            "Existing destination is not a readable supported catalog.",
                            "Choose a new SQLite file, or check catalog permissions and format."
                        );
                    }
                }
            } else if !path
                .parent()
                .filter(|p| !p.as_os_str().is_empty())
                .unwrap_or(Path::new("."))
                .is_dir()
            {
                fail!(
                    "destination",
                    "Destination parent directory does not exist.",
                    "Create the parent directory or choose a path in an existing writable directory."
                );
            }
        }
    } else if let Some(raster) = raster {
        report["source"] = json!({"kind":"raster", "location":absolute(raster), "origin":"cli"});
        entries.push((
            Vec::new(),
            PathBuf::from(raster),
            bands.unwrap_or_else(|| vec![1]),
        ));
    } else if let Some(database) = database {
        report["source"] = json!({"kind":"catalog", "location":absolute(database), "origin":if args.iter().any(|a|a=="-d"||a=="--database"){json!("cli")}else{report["configuration"]["DRIVER_PATH"]["origin"].clone()}});
        let provider = crate::storage::provider::Provider::detect(
            database,
            settings::snapshot()["DRIVER_PROVIDER"].as_str(),
        );
        if !matches!(provider, Ok(crate::storage::provider::Provider::Sqlite))
            || (database.contains("://") && !database.starts_with("sqlite:///"))
        {
            fail!(
                "catalog",
                "Remote catalog inspection is unavailable in read-only local inspection.",
                "Inspect a local SQLite snapshot; configure database credentials and validate remote catalog connectivity in its deployment environment."
            );
        }
        let local = match crate::storage::provider::local_path(database) {
            Ok(p) => p,
            Err(_) => {
                fail!(
                    "catalog",
                    "Catalog location could not be resolved.",
                    "Use a local SQLite path or sqlite:/// URL."
                );
            }
        };
        report["source"]["location"] = json!(absolute(&local.to_string_lossy()));
        let store = match SqliteStore::open_read_only(&local) {
            Ok(s) => s,
            Err(_) => {
                fail!(
                    "catalog",
                    "Catalog is missing, unreadable or has an unsupported format.",
                    "Check -d/DRIVER_PATH, file permissions and catalog format; ingest into a new catalog if needed."
                );
            }
        };
        report["source"]["format"] = match store.db_version() {
            Ok(v) => json!(v),
            Err(_) => {
                fail!(
                    "catalog",
                    "Catalog format could not be read.",
                    "Check catalog integrity and format."
                );
            }
        };
        report["ordered_keys"] = json!(store.key_names());
        let datasets = match store.datasets() {
            Ok(v) => v,
            Err(_) => {
                fail!(
                    "catalog",
                    "Catalog datasets could not be read.",
                    "Check catalog integrity and format."
                );
            }
        };
        for data in datasets {
            let definition = match store.definition(&data.keys) {
                Ok(v) => v,
                Err(_) => {
                    fail!(
                        "binding",
                        "Native dataset binding could not be read.",
                        "Check catalog integrity and re-register the dataset if needed."
                    );
                }
            };
            match definition.as_ref().and_then(|d| d["kind"].as_str()) {
                None => entries.push((data.keys, data.path, vec![1])),
                Some("band") => {
                    let d = definition.unwrap();
                    let Some(path) = d["asset"]["location"].as_str() else {
                        fail!(
                            "binding",
                            "Native dataset has an invalid raster binding.",
                            "Re-register the logical dataset with a physical asset and one-based band."
                        );
                    };
                    entries.push((
                        data.keys,
                        PathBuf::from(path),
                        vec![d["band"].as_u64().unwrap_or(0) as usize],
                    ));
                }
                Some(kind) => {
                    report["datasets"]
                        .as_array_mut()
                        .unwrap()
                        .push(json!({"keys":data.keys,"kind":kind,"inspection":"skipped"}));
                    issue(
                        report,
                        if render { "error" } else { "warning" },
                        "logical_source",
                        "Expression or stored source requires logical source resolution.",
                        "Inspect its bound physical rasters with --raster; validate the complete expression/STAC render through the serving API.",
                        None,
                    );
                }
            }
        }
    }
    if entries.len() + report["datasets"].as_array().unwrap().len() > 100 {
        fail!(
            "limit",
            "Inspection exceeds the 100-dataset limit.",
            "Use a narrower filename pattern or inspect individual physical rasters."
        );
    }
    for (keys, path, bands) in entries {
        let index = report["datasets"].as_array().unwrap().len();
        let mut data = json!({"keys":keys,"kind":"raster","path":absolute(&path.to_string_lossy()),"selected_bands":bands,"inspection":"pending","raster":null,"render_probe":null,"remote_probe":null});
        if is_remote(&path.to_string_lossy()) {
            data["inspection"] = "skipped".into();
            if remote {
                match remote_probe(&path.to_string_lossy()) {
                    Ok(result) => {
                        if result["status"] == 401 || result["status"] == 403 {
                            issue(
                                report,
                                "error",
                                "credentials",
                                "Remote raster access was denied.",
                                "Refresh signed URLs or configure provider credentials in the deployment environment; metadata and raster credentials have different consumers.",
                                Some(index),
                            );
                        } else if result["reachable"] != true {
                            issue(
                                report,
                                "error",
                                "remote",
                                "Remote raster is not reachable.",
                                "Check the object path, endpoint, TLS trust and network access.",
                                Some(index),
                            );
                        } else if result["range_supported"] != true {
                            issue(
                                report,
                                "warning",
                                "range",
                                "Origin did not demonstrate byte-range support.",
                                "Use a range-capable object store or serve an optimized local raster; an Accept-Ranges header alone is insufficient.",
                                Some(index),
                            );
                        }
                        data["remote_probe"] = result;
                    }
                    Err(_) => issue(
                        report,
                        "error",
                        "remote",
                        "Remote probe failed or its protocol is unsupported.",
                        "Use an HTTP(S) URL for the bounded range probe. For S3, verify the instance role/profile and raster-specific credentials in the deployment environment.",
                        Some(index),
                    ),
                }
            } else {
                issue(
                    report,
                    "warning",
                    "remote_skipped",
                    "Remote raster reachability and layout were not checked.",
                    "Use --probe-remote with an HTTP(S) raster URL to test access and byte ranges; no network request is made by default.",
                    Some(index),
                );
            }
            if render {
                issue(
                    report,
                    "error",
                    "render_remote",
                    "Remote rendering is outside the bounded local render probe.",
                    "Download a trusted raster to local storage and inspect it with --render-probe, or use the serving API in the deployment environment.",
                    Some(index),
                );
            }
        } else {
            match local_probe(&path, &bands, render) {
                Ok(result) => {
                    data["inspection"] = "passed".into();
                    data["raster"] = result["raster"].clone();
                    data["render_probe"] = result["render_probe"].clone();
                    for band in result["raster"]["layout"].as_array().unwrap() {
                        if !bands.contains(&(band["index"].as_u64().unwrap() as usize)) {
                            continue;
                        }
                        let size = result["raster"]["size"].as_array().unwrap();
                        if band["overviews"].as_array().unwrap().is_empty()
                            && size.iter().any(|s| s.as_u64().unwrap_or(0) > 256)
                        {
                            issue(
                                report,
                                "warning",
                                "overviews",
                                "Selected band has no overviews for a 256-pixel preview.",
                                "Run optimize-rasters into a separate output directory and register the optimized paths; large-area reads may otherwise decode full-resolution blocks.",
                                Some(index),
                            );
                        } else if band["overviews"]
                            .as_array()
                            .unwrap()
                            .last()
                            .is_some_and(|s| {
                                s.as_array()
                                    .unwrap()
                                    .iter()
                                    .any(|n| n.as_u64().unwrap_or(0) > 256)
                            })
                        {
                            issue(
                                report,
                                "warning",
                                "overview_coverage",
                                "Smallest overview still exceeds the preview size.",
                                "Prepare additional overview levels in a separate optimized raster if broad zoomed-out views are important.",
                                Some(index),
                            );
                        }
                        if band["block_size"][1] == size[1]
                            && band["block_size"][0].as_u64().unwrap_or(0)
                                < size[0].as_u64().unwrap_or(0)
                        {
                            issue(
                                report,
                                "warning",
                                "strips",
                                "Selected band uses full-width strips.",
                                "For tile serving, optimize into a tiled raster and compare representative reads before replacing catalog paths.",
                                Some(index),
                            );
                        }
                    }
                    if result["render_probe"]
                        .as_array()
                        .is_some_and(|p| p.iter().any(|b| b["valid_pixels"] == 0))
                    {
                        issue(
                            report,
                            "warning",
                            "empty_render",
                            "A render probe contains no valid samples.",
                            "Check selected bands, nodata and mask/alpha declarations; an empty probe does not establish usable imagery.",
                            Some(index),
                        );
                    }
                }
                Err(code) => {
                    data["inspection"] = "failed".into();
                    let (message, next) = match code.as_str() {
                        "path" => (
                            "Raster path is missing or unreadable.",
                            "Check the stored absolute path and mount the imagery at the same path inside the container or Python/WSGI host.",
                        ),
                        "format" => (
                            "Bounded inspection requires a local TIFF.",
                            "Use a self-contained TIFF; VRTs and other formats may access unbounded external dependencies. Validate those through GDAL in their deployment environment.",
                        ),
                        "bands" => (
                            "Selected band does not exist.",
                            "Choose a one-based band index listed in the raster, or correct the native catalog binding.",
                        ),
                        "georeferencing" => (
                            "Raster is missing its CRS or affine geotransform.",
                            "Obtain the correct source CRS/georeferencing from the imagery provider and prepare a georeferenced TIFF; assigning the map CRS without knowing the source CRS changes its location.",
                        ),
                        "timeout" => (
                            "Raster probe exceeded ten seconds and was terminated.",
                            "Check the source layout and filesystem responsiveness; optimize expensive layouts before serving.",
                        ),
                        _ => (
                            "Raster headers or render probe could not be read.",
                            "Check TIFF integrity, embedded CRS/geotransform, masks and GDAL/PROJ runtime installation; use optimize-rasters on a supported georeferenced source.",
                        ),
                    };
                    issue(report, "error", &code, message, next, Some(index));
                }
            }
        }
        report["datasets"].as_array_mut().unwrap().push(data);
    }
    Ok(())
}

fn absolute(value: &str) -> String {
    if is_remote(value) || value.contains("://") {
        return location(value);
    }
    let path = Path::new(value);
    location(
        &if path.is_absolute() {
            path.to_owned()
        } else {
            std::env::current_dir().unwrap_or_default().join(path)
        }
        .to_string_lossy(),
    )
}
fn is_remote(value: &str) -> bool {
    value.contains("://") || value.starts_with("/vsi")
}

fn local_probe(path: &Path, bands: &[usize], render: bool) -> Result<Value> {
    let mut output = tempfile::tempfile().map_err(|_| "runtime")?;
    let mut child = Command::new(std::env::current_exe().map_err(|_| "runtime")?)
        .arg("inspect-raster-worker")
        .stdin(Stdio::piped())
        .stdout(output.try_clone().map_err(|_| "runtime")?)
        .stderr(Stdio::null())
        .spawn()
        .map_err(|_| "runtime")?;
    let request =
        json!({"path":path,"bands":bands,"render":render,"settings":settings::snapshot()});
    let sent = child
        .stdin
        .take()
        .unwrap()
        .write_all(request.to_string().as_bytes());
    if sent.is_err() {
        let _ = child.kill();
        let _ = child.wait();
        return Err("runtime".into());
    }
    let started = Instant::now();
    loop {
        match child.try_wait() {
            Ok(Some(_)) => break,
            Ok(None) if started.elapsed() < Duration::from_secs(10) => {
                std::thread::sleep(Duration::from_millis(10))
            }
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                return Err("timeout".into());
            }
        }
    }
    output.rewind().map_err(|_| "runtime")?;
    let result: Value = serde_json::from_reader(output.take(1024 * 1024)).map_err(|_| "runtime")?;
    if let Some(code) = result["error"].as_str() {
        Err(code.into())
    } else {
        Ok(result)
    }
}

/// Isolated native worker with a parent-enforced deadline and suppressed raw GDAL errors.
pub fn raster_worker() -> Result<()> {
    let request: Value = serde_json::from_reader(std::io::stdin().take(1024 * 1024))
        .map_err(|_| "invalid probe input")?;
    let config = request["settings"]
        .as_object()
        .ok_or("invalid probe settings")?
        .clone();
    let result = settings::scope(config, || raster_details(&request));
    println!("{}", result.unwrap_or_else(|code| json!({"error":code})));
    Ok(())
}

fn raster_details(request: &Value) -> Result<Value> {
    let path = Path::new(request["path"].as_str().ok_or("path")?);
    if !path.is_file() {
        return Err("path".into());
    }
    let mut magic = [0; 4];
    std::fs::File::open(path)
        .and_then(|mut f| f.read_exact(&mut magic))
        .map_err(|_| "path")?;
    if ![b"II*\0", b"MM\0*", b"II+\0", b"MM\0+"].contains(&&magic) {
        return Err("format".into());
    }
    let gdal = Gdal::load().map_err(|_| "runtime")?;
    let layout = gdal.inspect_layout(path).map_err(|_| "raster")?;
    let asset = gdal.inspect_asset(path).map_err(|error| {
        if error.contains("no CRS") || error.contains("no affine") {
            "georeferencing"
        } else {
            "raster"
        }
    })?;
    let bands = request["bands"].as_array().ok_or("bands")?;
    if bands.iter().any(|b| {
        b.as_u64()
            .is_none_or(|n| n == 0 || n > asset.bands.len() as u64)
    }) {
        return Err("bands".into());
    }
    let mut probes = Vec::new();
    if request["render"] == true {
        for band in bands {
            let band_index = band.as_u64().unwrap() as usize;
            let mut read = crate::raster::ReadRequest {
                band: band_index,
                ..Default::default()
            };
            if band_index != 1 {
                read.grid = Some(crate::layers::GridRead {
                    matrix: None,
                    point: None,
                    epsg: 3857,
                    resampling: match settings::var("TC_REPROJECTION_METHOD").as_deref() {
                        Ok("nearest") => crate::layers::Resampling::Nearest,
                        Ok("cubic") => crate::layers::Resampling::Cubic,
                        Ok("average") => crate::layers::Resampling::Average,
                        _ => crate::layers::Resampling::Bilinear,
                    },
                    revision: [0; 32],
                });
                read.bounds_mercator = Some(gdal.asset_map_bounds(path).map_err(|_| "render")?);
            }
            let raster = gdal.read(path, read).map_err(|_| "render")?;
            let valid = raster
                .values
                .iter()
                .zip(&raster.mask)
                .filter(|(v, m)| !**m && v.is_finite())
                .map(|(v, _)| *v)
                .collect::<Vec<_>>();
            let min = valid.iter().copied().reduce(f64::min).unwrap_or(0.);
            let max = valid.iter().copied().reduce(f64::max).unwrap_or(1.);
            let pixels = crate::image::normalized_pixels(&raster, [min, max]);
            let png = crate::image::grayscale_png(raster.width, raster.height, &pixels)
                .map_err(|_| "render")?;
            probes.push(json!({"band":band,"size":[raster.height,raster.width],"valid_pixels":valid.len(),"range":[min,max],"png_bytes":png.len()}));
        }
    }
    Ok(
        json!({"raster":{"size":asset.size,"crs":asset.crs,"transform":asset.transform,
        "bands":asset.bands.iter().map(|b|json!({"index":b.index,"dtype":b.dtype,"nodata":b.nodata,"mask_flags":b.mask_flags,"color_interpretation":b.color_interpretation,"scale":b.scale,"offset":b.offset})).collect::<Vec<_>>(),"layout":layout},
        "render_probe":if request["render"]==true {json!(probes)}else{Value::Null}}),
    )
}

fn remote_probe(location: &str) -> Result<Value> {
    let location = location.strip_prefix("/vsicurl/").unwrap_or(location);
    let url = url::Url::parse(location).map_err(|_| "remote")?;
    if !["http", "https"].contains(&url.scheme()) {
        return Err("remote".into());
    }
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .map_err(|_| "remote")?;
    runtime.block_on(async {
        let client = reqwest::Client::builder()
            .timeout(Duration::from_secs(5))
            .redirect(reqwest::redirect::Policy::none())
            .build()
            .map_err(|_| "remote")?;
        let mut response = client
            .get(url)
            .header("Range", "bytes=0-1023")
            .header("Accept-Encoding", "identity")
            .send()
            .await
            .map_err(|_| "remote")?;
        let status = response.status().as_u16();
        let expected_bytes = response
            .headers()
            .get("content-range")
            .and_then(|h| h.to_str().ok())
            .and_then(|range| range.strip_prefix("bytes 0-"))
            .and_then(|range| range.split_once('/'))
            .and_then(|(end, total)| {
                Some((end.parse::<usize>().ok()?, total.parse::<usize>().ok()?))
            })
            .filter(|(end, total)| *end <= 1023 && total > end)
            .map(|(end, _)| end + 1);
        // Do not download a body when the origin ignores Range or sends an invalid range.
        let mut read = 0;
        if status == 206 && expected_bytes.is_some() {
            while read < expected_bytes.unwrap() {
                let Some(chunk) = response.chunk().await.map_err(|_| "remote")? else {
                    break;
                };
                if chunk.len() > 1024 - read {
                    return Err("remote".into());
                }
                read += chunk.len();
            }
        }
        Ok(json!({"status":status,"reachable":status==200||status==206,
            "range_supported":status==206 && expected_bytes==Some(read),"body_bytes":read}))
    })
}

fn emit(report: &Value, json_output: bool) {
    if json_output {
        println!("{}", serde_json::to_string_pretty(report).unwrap());
        return;
    }
    println!(
        "Inspection: {}",
        if report["ok"] == true { "ok" } else { "failed" }
    );
    if !report["source"].is_null() {
        println!(
            "Source: {} ({})",
            report["source"]["location"].as_str().unwrap_or(""),
            report["source"]["kind"].as_str().unwrap_or("")
        );
    }
    if !report["destination"].is_null() {
        println!(
            "Ingestion destination: {}\nRaster storage: paths only; imagery is not copied",
            report["destination"]["location"].as_str().unwrap_or("")
        );
    }
    println!("Ordered keys: {}", report["ordered_keys"]);
    for data in report["datasets"].as_array().unwrap() {
        println!(
            "Dataset {}: {} | selected bands {} | {}",
            data["keys"],
            data["path"].as_str().unwrap_or("logical source"),
            data["selected_bands"],
            data["inspection"].as_str().unwrap_or("")
        );
        if !data["raster"].is_null() {
            println!(
                "  Raster: {}\n  Render probe: {}",
                data["raster"], data["render_probe"]
            );
        }
        if !data["remote_probe"].is_null() {
            println!("  Remote probe: {}", data["remote_probe"]);
        }
    }
    println!("Configuration (value, origin):");
    for (key, value) in report["configuration"].as_object().unwrap() {
        println!(
            "  {key}: {} ({}){}",
            value["value"],
            value["origin"].as_str().unwrap_or(""),
            value
                .get("active")
                .map(|v| format!(", active={v}"))
                .unwrap_or_default()
        );
    }
    for item in report["issues"].as_array().unwrap() {
        println!(
            "{} [{}] dataset {}: {}\n  Next: {}",
            item["severity"].as_str().unwrap(),
            item["code"].as_str().unwrap(),
            item["dataset"],
            item["message"].as_str().unwrap(),
            item["next_step"].as_str().unwrap()
        );
    }
}

pub fn sample_command(args: &[String]) -> Result<()> {
    if args == ["--help"] || args == ["-h"] {
        println!(
            "Usage: rcfp sample -o NEW_DIRECTORY\nWrite checksum-pinned Sentinel-2 and WorldCover samples and attribution for offline first use. Refuses to replace an existing directory."
        );
        return Ok(());
    }
    let [option, path] = args else {
        return Err("sample requires -o NEW_DIRECTORY; use sample --help".into());
    };
    if option != "-o" && option != "--output-directory" {
        return Err("sample requires -o NEW_DIRECTORY".into());
    }
    let path = Path::new(path);
    std::fs::create_dir(path)
        .map_err(|_| "choose a new sample directory in an existing writable parent")?;
    let data = path.join("data");
    std::fs::create_dir(&data).map_err(|_| "could not create sample data directory")?;
    let manifest = include_str!("../docs/examples/data/manifest.json");
    let parsed: Value = serde_json::from_str(manifest).map_err(|_| "invalid sample manifest")?;
    use sha2::Digest;
    for (name, bytes) in [
        (
            "red.tif",
            include_bytes!("../docs/examples/data/red.tif").as_slice(),
        ),
        (
            "green.tif",
            include_bytes!("../docs/examples/data/green.tif").as_slice(),
        ),
        (
            "blue.tif",
            include_bytes!("../docs/examples/data/blue.tif").as_slice(),
        ),
        (
            "nir.tif",
            include_bytes!("../docs/examples/data/nir.tif").as_slice(),
        ),
        (
            "classes.tif",
            include_bytes!("../docs/examples/data/classes.tif").as_slice(),
        ),
    ] {
        let expected = parsed["fixtures"]
            .as_array()
            .unwrap()
            .iter()
            .find(|v| v["file"] == name)
            .unwrap();
        if format!("{:x}", sha2::Sha256::digest(bytes)) != expected["sha256"].as_str().unwrap() {
            return Err("sample checksum mismatch".into());
        }
        std::fs::write(data.join(name), bytes).map_err(
            |_| "could not write sample raster; check destination permissions and free space",
        )?;
    }
    std::fs::write(data.join("manifest.json"), manifest)
        .map_err(|_| "could not write sample attribution")?;
    std::fs::write(
        data.join("lagoon-stac.json"),
        include_bytes!("../docs/examples/data/lagoon-stac.json"),
    )
    .map_err(|e| e.to_string())?;
    std::fs::write(
        path.join("create_catalog.py"),
        include_bytes!("../docs/examples/create_catalog.py"),
    )
    .map_err(|_| "could not write sample catalog helper")?;
    let database = path.join("map.sqlite");
    let gdal = crate::raster::Gdal::load()?;
    let store = crate::storage::Store::create(
        &database.to_string_lossy(),
        None,
        &["scene", "band"]
            .into_iter()
            .map(|key| crate::storage::KeyDefinition {
                key: key.into(),
                description: if key == "scene" {
                    "Sample scene"
                } else {
                    "Raster band"
                }
                .into(),
            })
            .collect::<Vec<_>>(),
    )?;
    for band in ["red", "green", "blue", "nir", "classes"] {
        let raster = data
            .join(format!("{band}.tif"))
            .canonicalize()
            .map_err(|e| e.to_string())?;
        let mut metadata = gdal.compute_metadata(&raster, &Default::default())?;
        if band == "classes" {
            metadata.metadata = serde_json::json!({"categories": {
                "trees": 10, "grassland": 30, "cropland": 40, "built": 50, "water": 80
            }});
        }
        store.insert(
            &crate::storage::Dataset {
                keys: vec![
                    if band == "classes" {
                        "worldcover"
                    } else {
                        "lagoon"
                    }
                    .into(),
                    band.into(),
                ],
                path: raster,
            },
            Some(&metadata),
        )?;
    }
    drop(store);
    let app = crate::http::Application::open(&database)?;
    let image = app.request(
        "GET",
        "/singleband/lagoon/red/preview.png?stretch_range=%5B0,3000%5D",
    );
    if image.status != 200 {
        return Err("could not render sample preview".into());
    }
    std::fs::write(path.join("map.png"), image.body).map_err(|e| e.to_string())?;
    println!(
        "Created five datasets and a preview in {}\n\nCatalog: {}/map.sqlite\nPreview: {}/map.png\nAttribution and checksums: {}/data/manifest.json\n\nStart the tile server:\n  rcfp serve -d {}/map.sqlite\n\nThen open the web viewer in a second terminal:\n  rcfp connect localhost:5000",
        absolute(&path.to_string_lossy()),
        path.display(),
        path.display(),
        path.display(),
        path.display()
    );
    Ok(())
}
