//! CLI adapters for native raster and catalog operations.
use crate::{
    Result, ingest,
    raster::{Gdal, MetadataOptions, OptimizeOptions},
    storage::{Dataset, KeyDefinition, Metadata, Store},
};
use serde_json::{Value, json};
use std::path::{Path, PathBuf};

pub fn ingest_command(args: &[String]) -> Result<()> {
    let (mut pattern, mut output, mut rgb, mut source) = (None, None, None, None);
    let (mut skip, mut existing, mut quiet, mut native) = (false, false, false, false);
    let mut bands = None;
    let mut band_key = "band";
    let mut values = "raw";
    let mut search = None;
    let mut mapping = json!({});
    let mut options = json!({});
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "-o" | "--output-file" => output = Some(next(args, &mut i)?),
            "--rgb-key" => rgb = Some(next(args, &mut i)?),
            "--format" => {
                native = match next(args, &mut i)? {
                    "native" => true,
                    "terracotta" => false,
                    _ => return Err("catalog format must be native or terracotta".into()),
                };
            }
            "--bands" => {
                bands = Some(next(args, &mut i)?);
                native = true;
            }
            "--band-key" => band_key = next(args, &mut i)?,
            "--values" => {
                values = next(args, &mut i)?;
                native = true;
            }
            "--source" => source = Some(next(args, &mut i)?),
            "--search" => {
                search = Some(
                    serde_json::from_str::<Value>(next(args, &mut i)?)
                        .map_err(|e| e.to_string())?,
                )
            }
            "--key-mapping" => {
                mapping = serde_json::from_str(next(args, &mut i)?).map_err(|e| e.to_string())?
            }
            "--source-options" => {
                options = serde_json::from_str(next(args, &mut i)?).map_err(|e| e.to_string())?
            }
            "--skip-metadata" => skip = true,
            "--skip-existing" => existing = true,
            "-q" | "--quiet" => quiet = true,
            "--help" | "-h" => {
                println!(
                    "Usage: rcfp ingest PATTERN -o DATABASE [--format native|terracotta] [--bands red=3,green=2,blue=1] [--band-key band] [--values raw|scaled] [--skip-metadata] [--skip-existing] [--rgb-key KEY] [-q]\n       rcfp ingest --source STAC -o DATABASE [--search JSON] [--source-options JSON] [--key-mapping JSON] [--skip-metadata] [-q]"
                );
                return Ok(());
            }
            p if !p.starts_with('-') && pattern.is_none() => pattern = Some(p),
            p => return Err(format!("unknown ingest argument: {p}")),
        }
        i += 1;
    }
    let output = output.ok_or("ingest requires -o/--output-file")?;
    let n = if let Some(source) = source {
        if pattern.is_some() || bands.is_some() || rgb.is_some() || existing {
            return Err(
                "source ingestion cannot combine raster pattern, bands, rgb-key or skip-existing"
                    .into(),
            );
        }
        let store = if Path::new(output).exists() {
            Store::open_writable(output, None)?
        } else {
            let keys = if mapping.as_object().is_none_or(|m| m.is_empty()) {
                vec!["collection", "datetime", "item", "asset"]
            } else {
                mapping
                    .as_object()
                    .ok_or("key mapping must be an object")?
                    .keys()
                    .map(String::as_str)
                    .collect()
            };
            Store::create_native(
                output,
                None,
                &keys
                    .into_iter()
                    .map(|k| KeyDefinition {
                        key: k.into(),
                        description: String::new(),
                    })
                    .collect::<Vec<_>>(),
            )?
        };
        crate::catalog::Catalog::sql(store)?.operation(&json!({"operation":"ingest_source","source":source,"search":search,"options":options,"key_mapping":mapping,"skip_metadata":skip}))?.as_u64().ok_or("invalid ingestion result")? as usize
    } else {
        let pattern = pattern.ok_or("ingest requires raster pattern or --source")?;
        if native
            || (Path::new(output).exists()
                && Store::open_with(output, None, true)?.db_version()?
                    == crate::catalog::dataset::FORMAT)
        {
            ingest::ingest_native(
                pattern,
                Path::new(output),
                ingest::NativeIngest {
                    rgb,
                    bands,
                    band_key,
                    values,
                    skip_metadata: skip,
                    skip_existing: existing,
                },
            )?
        } else {
            ingest::ingest(pattern, Path::new(output), rgb, skip, existing)?
        }
    };
    if !quiet {
        let noun = if native || source.is_some() {
            "datasets"
        } else {
            "raster files"
        };
        println!("Ingested {n} {noun}");
    }
    Ok(())
}
fn next<'a>(args: &'a [String], i: &mut usize) -> Result<&'a str> {
    *i += 1;
    args.get(*i)
        .map(String::as_str)
        .ok_or_else(|| "missing option value".into())
}
pub fn optimize_command(args: &[String]) -> Result<()> {
    let mut options = OptimizeOptions::default();
    let mut output = None;
    let mut patterns = Vec::new();
    let (mut overwrite, mut skip, mut quiet) = (false, false, false);
    let mut nproc = 1i32;
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "-o" | "--output-folder" => output = Some(PathBuf::from(next(args, &mut i)?)),
            "--compression" => options.compression = next(args, &mut i)?.into(),
            "--resampling-method" => options.resampling_method = next(args, &mut i)?.into(),
            "--reproject" => options.reproject = true,
            "--web-mercator-grid" => options.web_mercator_grid = true,
            "--all-bands" => options.all_bands = true,
            "--block-size" => {
                options.block_size = next(args, &mut i)?
                    .parse()
                    .map_err(|_| "invalid block size")?
            }
            "--in-memory" => options.in_memory = Some(true),
            "--no-in-memory" => options.in_memory = Some(false),
            "--overwrite" => overwrite = true,
            "--skip-existing" => skip = true,
            "-q" | "--quiet" => quiet = true,
            "--nproc" => nproc = next(args, &mut i)?.parse().map_err(|_| "invalid nproc")?,
            "--help" | "-h" => {
                println!(
                    "Usage: rcfp optimize-rasters FILES... -o FOLDER [--overwrite|--skip-existing] [--reproject|--web-mercator-grid [--all-bands] [--block-size 256|512|1024]] [--resampling-method average|nearest|bilinear|cubic] [--in-memory|--no-in-memory] [--compression auto|zstd|deflate|lzw|none] [--nproc N] [-q]"
                );
                return Ok(());
            }
            p if !p.starts_with('-') => patterns.push(p),
            p => return Err(format!("unknown optimize argument: {p}")),
        }
        i += 1;
    }
    if !["auto", "deflate", "lzw", "zstd", "none"].contains(&options.compression.as_str()) {
        return Err("invalid compression".into());
    }
    if !["average", "nearest", "bilinear", "cubic"].contains(&options.resampling_method.as_str()) {
        return Err("invalid resampling method".into());
    }
    if overwrite && skip {
        return Err(
            "Both --overwrite and --skip-existing flags are provided. These are mutually exclusive"
                .into(),
        );
    }
    if patterns.is_empty() {
        return Err("optimize-rasters requires input files".into());
    }
    let output = output.ok_or("optimize-rasters requires -o/--output-folder")?;
    let mut files = Vec::new();
    for pattern in patterns {
        files.extend(ingest::glob(pattern)?);
    }
    files.sort();
    files.dedup();
    if files.is_empty() {
        println!("No files given");
        return Ok(());
    }
    let mut jobs = Vec::new();
    let inspector = Gdal::load()?;
    for file in files {
        if !file.is_file() {
            return Err(format!("Input raster {} is not a file", file.display()));
        }
        if inspector.band_count(&file)? > 1 && !quiet && !options.all_bands {
            eprintln!(
                "Warning: raster file {} has more than one band. Only the first one will be used.",
                file.display()
            );
        }
        let dest = output.join(file.with_extension("tif").file_name().unwrap());
        if dest.is_file() {
            if !skip && !overwrite {
                return Err(format!(
                    "Output file {} exists (use --overwrite or --skip-existing)",
                    dest.display()
                ));
            }
            if skip {
                continue;
            }
        }
        if file == dest || dest.canonicalize().ok() == file.canonicalize().ok() {
            return Err("input and output must differ".into());
        }
        jobs.push((file, dest));
    }
    std::fs::create_dir_all(&output).map_err(|e| e.to_string())?;
    let workers = if nproc == -1 {
        std::thread::available_parallelism()
            .map(usize::from)
            .unwrap_or(1)
    } else {
        nproc.max(1) as usize
    }
    .min(jobs.len().max(1));
    if !quiet {
        println!("Optimizing {} files on {workers} workers", jobs.len());
    }
    let queue = std::sync::Mutex::new(jobs.into_iter());
    std::thread::scope(|scope| {
        let mut handles = Vec::new();
        for _ in 0..workers {
            let queue = &queue;
            let options = &options;
            handles.push(scope.spawn(move || -> Result<()> {
                let g = Gdal::load()?;
                loop {
                    let job = queue
                        .lock()
                        .map_err(|_| "optimization queue poisoned")?
                        .next();
                    let Some((src, dst)) = job else {
                        break;
                    };
                    if !quiet {
                        println!("{}", src.display());
                    }
                    g.optimize(&src, &dst, options)?;
                }
                Ok(())
            }));
        }
        let mut result = Ok(());
        for handle in handles {
            if let Err(e) = handle
                .join()
                .map_err(|_| "optimization worker panicked".to_string())
                .and_then(|r| r)
            {
                result = Err(e);
            }
        }
        result
    })
}

/// Execute preparation operations individually or as a single-transaction batch.
pub fn data_operation(request: &Value) -> Result<Value> {
    let op = request["operation"].as_str().ok_or("operation required")?;
    let path = || {
        request["path"]
            .as_str()
            .ok_or_else(|| "path required".to_string())
    };
    match op {
        "inspect_asset" => serde_json::to_value(crate::layers::inspect(Path::new(path()?))?)
            .map_err(|e| e.to_string()),
        "layer_create" => {
            crate::layers::catalog::Catalog::create(Path::new(path()?))?;
            Ok(Value::Null)
        }
        "layer_samples" => {
            let spec =
                serde_json::from_value(request["spec"].clone()).map_err(|e| e.to_string())?;
            let app = crate::http::Application::open(Path::new(path()?))?;
            Ok(app
                .layer_sample_result(
                    request["id"].as_str().ok_or("layer id required")?,
                    &spec,
                    request["provenance"] == true,
                )?
                .json(request["provenance"] == true))
        }
        "layer_ids" => Ok(json!(
            crate::layers::catalog::Catalog::open(Path::new(path()?), false)?.ids()?
        )),
        "layer_get" => Ok(json!(
            crate::layers::catalog::Catalog::open(Path::new(path()?), false)?
                .get(request["id"].as_str().ok_or("layer id required")?)?
        )),
        "layer_delete" => {
            crate::layers::catalog::Catalog::open(Path::new(path()?), true)?
                .delete(request["id"].as_str().ok_or("layer id required")?)?;
            Ok(Value::Null)
        }
        "layer_register" => {
            let bands = request
                .get("bands")
                .filter(|v| !v.is_null())
                .map(|v| serde_json::from_value(v.clone()).map_err(|e| e.to_string()))
                .transpose()?;
            let stretch = request
                .get("stretch")
                .filter(|v| !v.is_null())
                .map(|v| serde_json::from_value(v.clone()).map_err(|e| e.to_string()))
                .transpose()?;
            let layer = crate::layers::raster_layer(
                Path::new(request["raster"].as_str().ok_or("raster required")?),
                bands,
                stretch,
            )?;
            crate::layers::catalog::Catalog::open(Path::new(path()?), true)?
                .put(request["id"].as_str().ok_or("layer id required")?, layer)?;
            Ok(Value::Null)
        }
        "layer_put" => {
            let layer =
                serde_json::from_value(request["layer"].clone()).map_err(|e| e.to_string())?;
            crate::layers::catalog::Catalog::open(Path::new(path()?), true)?
                .put(request["id"].as_str().ok_or("layer id required")?, layer)?;
            Ok(Value::Null)
        }
        "layer_import" => {
            let mut source = Store::open(Path::new(
                request["source"]
                    .as_str()
                    .ok_or("source catalog required")?,
            ))?;
            let bindings =
                serde_json::from_value(request["bindings"].clone()).map_err(|e| e.to_string())?;
            let layer = crate::layers::Layer::from_catalog(&mut source, &bindings)?;
            crate::layers::catalog::Catalog::open(Path::new(path()?), true)?
                .put(request["id"].as_str().ok_or("layer id required")?, layer)?;
            Ok(Value::Null)
        }
        "raster_pattern" => {
            let (keys, datasets) = ingest::raster_pattern(path()?, request["rgb_key"].as_str())?;
            Ok(
                json!({"keys": keys.iter().map(|key| &key.key).collect::<Vec<_>>(),
                "datasets": datasets.iter().map(|dataset| json!({"keys": dataset.keys,
                    "path": dataset.path})).collect::<Vec<_>>()}),
            )
        }
        "spec" => Ok(crate::http::api_spec()),
        "apidoc" => Ok(json!(include_str!("assets/apidoc.html"))),
        "parse_settings" => Ok(json!(crate::settings::parse(
            request["config"].as_object().cloned().unwrap_or_default(),
        )?)),
        "default_settings" => Ok(json!(crate::settings::defaults())),
        "palette" => {
            let palettes = crate::palettes::Palettes::load()?;
            match request["name"].as_str() {
                Some(name) => Ok(json!(palettes.get(name)?.as_ref())),
                None => Ok(json!(palettes.0.keys().collect::<Vec<_>>())),
            }
        }
        "settings" => Ok(json!(crate::settings::snapshot())),
        "provider_info" => {
            use crate::storage::provider::{Provider, SqlPath, local_path};
            let p = Provider::detect(path()?, request["provider"].as_str())?;
            let identity = match p {
                Provider::Sqlite => local_path(path()?)?.to_string_lossy().into_owned(),
                Provider::Remote => {
                    let u = url::Url::parse(path()?).map_err(|_| "Expected s3:// URL")?;
                    format!(
                        "{}://{}:{}{}",
                        u.scheme(),
                        u.host_str().ok_or("missing host")?,
                        u.port().unwrap_or(80),
                        u.path().trim_end_matches('/')
                    )
                }
                _ => SqlPath::parse(path()?, p)?.identity(),
            };
            Ok(json!({"provider":p.name(),"identity":identity}))
        }

        "compute_metadata" => {
            let options: MetadataOptions =
                serde_json::from_value(request.get("options").cloned().unwrap_or(json!({})))
                    .map_err(|e| e.to_string())?;
            serde_json::to_value(Gdal::load()?.compute_metadata(Path::new(path()?), &options)?)
                .map_err(|e| e.to_string())
        }
        "optimize" => {
            let options: OptimizeOptions =
                serde_json::from_value(request.get("options").cloned().unwrap_or(json!({})))
                    .map_err(|e| e.to_string())?;
            Gdal::load()?.optimize(
                Path::new(path()?),
                Path::new(request["output"].as_str().ok_or("output required")?),
                &options,
            )?;
            Ok(Value::Null)
        }
        "create" => {
            let keys: Vec<KeyDefinition> =
                serde_json::from_value(request["keys"].clone()).map_err(|e| e.to_string())?;
            match request["format"].as_str().unwrap_or("terracotta") {
                "native" => {
                    Store::create_native(path()?, request["provider"].as_str(), &keys)?;
                }
                "terracotta" => {
                    Store::create(path()?, request["provider"].as_str(), &keys)?;
                }
                _ => return Err("catalog format must be native or terracotta".into()),
            }
            Ok(Value::Null)
        }
        "driver_create" => {
            let keys: Vec<String> =
                serde_json::from_value(request["keys"].clone()).map_err(|e| e.to_string())?;
            let descriptions = request["key_descriptions"]
                .as_object()
                .cloned()
                .unwrap_or_default();
            if descriptions.keys().any(|key| !keys.contains(key)) {
                return Err("key description dict contains unknown keys".into());
            }
            let definitions = keys
                .into_iter()
                .map(|key| KeyDefinition {
                    description: descriptions
                        .get(&key)
                        .and_then(Value::as_str)
                        .unwrap_or("")
                        .into(),
                    key,
                })
                .collect::<Vec<_>>();
            crate::storage::validate_definitions(&definitions).map_err(|_| {
                if definitions.iter().any(|key| {
                    ["limit", "page", "order_by", "ascending"].contains(&key.key.as_str())
                }) {
                    "key names cannot be one of limit, page, order_by, ascending".to_owned()
                } else {
                    "key names must be alphanumeric and unique".to_owned()
                }
            })?;
            Store::create(path()?, request["provider"].as_str(), &definitions)?;
            Ok(Value::Null)
        }
        "keys" | "datasets" | "metadata" if request["provider"].is_null() => {
            let catalog = match crate::catalog::Catalog::open(Path::new(path()?))? {
                crate::catalog::Opened::Datasets(c) => c,
                crate::catalog::Opened::Layers(_) => {
                    Box::new(crate::catalog::Catalog::layers(Path::new(path()?))?)
                }
            };
            catalog.operation(request)
        }
        _ => {
            let store = if request["read_only"] == true {
                Store::open_with(path()?, request["provider"].as_str(), true)?
            } else {
                Store::open_writable(path()?, request["provider"].as_str())?
            };
            if store.db_version()? == crate::catalog::dataset::FORMAT {
                crate::catalog::Catalog::sql(store)?.operation(request)
            } else {
                if request.get("band").is_some()
                    || request.get("values").is_some()
                    || request.get("style").is_some()
                    || ["define", "ingest_source"].contains(&op)
                {
                    return Err("logical bands, expressions and stored sources require a native keyed catalog".into());
                }
                store_operation(&store, request)
            }
        }
    }
}
pub(crate) fn store_operation(store: &Store, request: &Value) -> Result<Value> {
    let op = request["operation"].as_str().ok_or("operation required")?;
    if op == "batch" {
        return store.transaction(|s| {
            let mut result = Vec::new();
            for r in request["operations"]
                .as_array()
                .ok_or("operations required")?
            {
                result.push(store_operation(s, r)?);
            }
            Ok(json!(result))
        });
    }
    if op == "version" {
        return Ok(json!(store.db_version()?));
    }
    if op == "keys" {
        return Ok(json!(store.keys()));
    }
    if op == "datasets" {
        let filters = serde_json::from_value(request.get("where").cloned().unwrap_or(json!({})))
            .map_err(|e| e.to_string())?;
        let order: Vec<String> = request
            .get("order_by")
            .map(|v| serde_json::from_value(v.clone()).map_err(|e| e.to_string()))
            .transpose()?
            .unwrap_or_else(|| store.key_names().iter().map(|k| k.to_string()).collect());
        let data = store.query_datasets(
            &filters,
            &order,
            request["ascending"].as_bool().unwrap_or(true),
            request["limit"].as_u64().map(|limit| {
                (
                    request["page"].as_u64().unwrap_or(0) as usize,
                    limit as usize,
                )
            }),
        )?;
        return Ok(json!(
            data.into_iter()
                .map(|d| json!({"keys":d.keys,"path":d.path}))
                .collect::<Vec<_>>()
        ));
    }
    let keys: Vec<String> =
        serde_json::from_value(request["keys"].clone()).map_err(|e| e.to_string())?;
    match op {
        "metadata_stored" => {
            Ok(serde_json::to_value(store.metadata_stored(&keys)?).map_err(|e| e.to_string())?)
        }
        "metadata" => Ok(serde_json::to_value(store.metadata(&keys)?).map_err(|e| e.to_string())?),
        "delete" => {
            store.delete(&keys)?;
            Ok(Value::Null)
        }
        "insert" => {
            let path = request["raster"].as_str().ok_or("raster required")?;
            let metadata = if let Some(m) = request.get("metadata").filter(|m| !m.is_null()) {
                Some(serde_json::from_value::<Metadata>(m.clone()).map_err(|e| e.to_string())?)
            } else if request["skip_metadata"] == true {
                None
            } else {
                let options: MetadataOptions =
                    serde_json::from_value(request.get("options").cloned().unwrap_or(json!({})))
                        .map_err(|e| e.to_string())?;
                Some(Gdal::load()?.compute_metadata(Path::new(path), &options)?)
            };
            store.insert(
                &Dataset {
                    keys,
                    path: PathBuf::from(
                        request["override_path"]
                            .as_str()
                            .filter(|s| !s.is_empty())
                            .unwrap_or(path),
                    ),
                },
                metadata.as_ref(),
            )?;
            Ok(Value::Null)
        }
        _ => Err(format!("unknown data operation: {op}")),
    }
}

/// Database-backed native layer administration and rendering.
pub fn layers_command(args: &[String]) -> Result<()> {
    use crate::layers::catalog::Catalog;
    match args {
        [command, path] if command == "create" => {
            Catalog::create(Path::new(path))?;
            Ok(())
        }
        [command, path] if command == "list" => {
            println!("{}", json!(Catalog::open(Path::new(path), false)?.ids()?));
            Ok(())
        }
        [command, path, id] if command == "describe" => {
            let layer = Catalog::open(Path::new(path), false)?.get(id)?.ok_or("No layer found")?;
            println!("{}", json!(layer));
            Ok(())
        }
        [command, path, id] if command == "delete" => {
            Catalog::open(Path::new(path), true)?.delete(id)
        }
        [command, path, id] if command == "put" => {
            use std::io::Read;
            let mut text = String::new();
            std::io::stdin().take(1024 * 1024 + 1).read_to_string(&mut text).map_err(|e| e.to_string())?;
            if text.len() > 1024 * 1024 {
                return Err("layer definition exceeds 1 MiB".into());
            }
            let layer = serde_json::from_str(&text).map_err(|e| e.to_string())?;
            Catalog::open(Path::new(path), true)?.put(id, layer)
        }
        [command, path, id, raster, rest @ ..] if command == "register" => {
            let mut bands = None;
            let mut stretch = None;
            let mut i = 0;
            while i < rest.len() {
                match rest[i].as_str() {
                    "--bands" => {
                        bands = Some(next(rest, &mut i)?.split(',').map(|n| n.parse::<usize>().map_err(|_| "bands must be comma-separated integers".to_string())).collect::<Result<Vec<_>>>()?);
                    }
                    "--stretch" => {
                        let values = next(rest, &mut i)?.split(',').map(|n| n.parse::<f64>().map_err(|_| "stretch must be MIN,MAX".to_string())).collect::<Result<Vec<_>>>()?;
                        stretch = Some(values.try_into().map_err(|_| "stretch must be MIN,MAX")?);
                    }
                    _ => return Err("unknown layer registration option".into()),
                }
                i += 1;
            }
            Catalog::open(Path::new(path), true)?.put(id, crate::layers::raster_layer(Path::new(raster), bands, stretch)?)
        }
        [command, path, id, rest @ ..] if command == "render" => {
            let catalog = Catalog::open(Path::new(path), false)?;
            let mut spec = None;
            let mut output = None;
            let mut i = 0;
            while i < rest.len() {
                match rest[i].as_str() {
                    "-o" | "--output" => output = Some(next(rest, &mut i)?),
                    "--spec" => {
                        let bytes = std::fs::read(next(rest, &mut i)?).map_err(|e| e.to_string())?;
                        spec = Some(serde_json::from_slice(&bytes).map_err(|e| e.to_string())?);
                    }
                    _ => return Err("unknown layer render option".into()),
                }
                i += 1;
            }
            let (layer, spec) = catalog.for_render(id, spec)?.ok_or("No layer found")?;
            let executor = crate::execution::RasterExecutor::start(crate::execution::ExecutionConfig::from_env()?)?;
            let bytes = layer.render(&executor, &spec)?;
            std::fs::write(output.ok_or("layer render requires -o")?, bytes).map_err(|e| e.to_string())
        }
        [command, source, destination, id, bindings] if command == "import" => {
            let bindings = serde_json::from_str(bindings).map_err(|e| e.to_string())?;
            let layer = crate::layers::Layer::from_catalog(&mut Store::open(Path::new(source))?, &bindings)?;
            Catalog::open(Path::new(destination), true)?.put(id, layer)
        }
        _ => Err("Usage: rcfp layers create|list DATABASE; describe|delete|put DATABASE ID; register DATABASE ID RASTER [--bands 1,2,3] [--stretch MIN,MAX]; render DATABASE ID -o IMAGE [--spec SPEC]; import SOURCE DESTINATION ID BINDINGS_JSON".into()),
    }
}
