//! P05 CLI adapters; core operations are independently callable.
use crate::{
    Result, ingest,
    raster::{Gdal, MetadataOptions, OptimizeOptions},
    storage::{Dataset, KeyDefinition, Metadata, Store},
};
use serde_json::{Value, json};
use std::path::{Path, PathBuf};

pub fn ingest_command(args: &[String]) -> Result<()> {
    let (mut pattern, mut output, mut rgb) = (None, None, None);
    let (mut skip, mut existing, mut quiet) = (false, false, false);
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "-o" | "--output-file" => output = Some(next(args, &mut i)?),
            "--rgb-key" => rgb = Some(next(args, &mut i)?),
            "--skip-metadata" => skip = true,
            "--skip-existing" => existing = true,
            "-q" | "--quiet" => quiet = true,
            "--help" | "-h" => {
                println!(
                    "Usage: terracotta ingest PATTERN -o DATABASE [--skip-metadata] [--skip-existing] [--rgb-key KEY] [-q]"
                );
                return Ok(());
            }
            p if !p.starts_with('-') && pattern.is_none() => pattern = Some(p),
            p => return Err(format!("unknown ingest argument: {p}")),
        }
        i += 1;
    }
    let n = ingest::ingest(
        pattern.ok_or("ingest requires raster pattern")?,
        Path::new(output.ok_or("ingest requires -o/--output-file")?),
        rgb,
        skip,
        existing,
    )?;
    if !quiet {
        println!("Ingested {n} raster files");
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
            "--in-memory" => options.in_memory = Some(true),
            "--no-in-memory" => options.in_memory = Some(false),
            "--overwrite" => overwrite = true,
            "--skip-existing" => skip = true,
            "-q" | "--quiet" => quiet = true,
            "--nproc" => nproc = next(args, &mut i)?.parse().map_err(|_| "invalid nproc")?,
            "--help" | "-h" => {
                println!(
                    "Usage: terracotta optimize-rasters FILES... -o FOLDER [--overwrite|--skip-existing] [--reproject] [--resampling-method average|nearest|bilinear|cubic] [--in-memory|--no-in-memory] [--compression auto|zstd|deflate|lzw|none] [--nproc N] [-q]"
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
        if inspector.band_count(&file)? > 1 && !quiet {
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

/// Structured core adapter used by Python ingestion and differential tooling.
/// A batch is a single transaction; each operation also stands alone.
pub fn data_operation(request: &Value) -> Result<Value> {
    let op = request["operation"].as_str().ok_or("operation required")?;
    let path = || {
        request["path"]
            .as_str()
            .ok_or_else(|| "path required".to_string())
    };
    match op {
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
            Store::create(path()?, request["provider"].as_str(), &keys)?;
            Ok(Value::Null)
        }
        _ => {
            let store = if request["read_only"] == true {
                Store::open_with(path()?, request["provider"].as_str(), true)?
            } else {
                Store::open_writable(path()?, request["provider"].as_str())?
            };
            store_operation(&store, request)
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
