use std::path::{Path, PathBuf};
use std::process::ExitCode;

fn main() -> ExitCode {
    reexec_with_gdal_dependencies();
    configure_gdal_runtime();
    let args: Vec<_> = std::env::args().skip(1).collect();
    match configured_run(&args) {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("error: {error}");
            ExitCode::from(2)
        }
    }
}

#[cfg(unix)]
fn reexec_with_gdal_dependencies() {
    use std::os::unix::process::CommandExt;
    if std::env::var_os("TERRACOTTA_GDAL_REEXEC").is_some() {
        return;
    }
    let Some(directory) = bundled_lib_dir() else {
        return;
    };
    let mut library_path = directory.into_os_string();
    if let Some(existing) = std::env::var_os("LD_LIBRARY_PATH") {
        library_path.push(":");
        library_path.push(existing);
    }
    let error = std::process::Command::new(std::env::current_exe().expect("current executable"))
        .args(std::env::args_os().skip(1))
        .env("LD_LIBRARY_PATH", library_path)
        .env("TERRACOTTA_GDAL_REEXEC", "1")
        .exec();
    eprintln!("error: could not initialize local GDAL runtime: {error}");
}

#[cfg(not(unix))]
fn reexec_with_gdal_dependencies() {}

fn configured_run(args: &[String]) -> rcfp_core::Result<()> {
    let mut config = serde_json::Map::new();
    let mut index = 0;
    while index < args.len() {
        match args[index].as_str() {
            "-c" | "--config" if index + 1 < args.len() => {
                let text = std::fs::read_to_string(&args[index + 1]).map_err(|e| e.to_string())?;
                let parsed: toml::Value = toml::from_str(&text)
                    .map_err(|e| format!("Could not parse configuration: {e}"))?;
                config = serde_json::to_value(parsed)
                    .map_err(|e| e.to_string())?
                    .as_object()
                    .ok_or("configuration must be a table")?
                    .clone();
                index += 2;
            }
            "--loglevel" if index + 1 < args.len() => {
                config.insert("LOGLEVEL".into(), args[index + 1].clone().into());
                index += 2;
            }
            _ => break,
        }
    }
    let config = rcfp_core::settings::parse(config)?;
    rcfp_core::settings::scope(config, || run(&args[index..]))
}

fn run(args: &[String]) -> rcfp_core::Result<()> {
    match args {
        [flag] if flag == "--version" => {
            println!(
                "rcfp {} (protocol {}; database {})",
                rcfp_core::IMPLEMENTATION_VERSION,
                rcfp_core::TERRACOTTA_COMPATIBILITY_VERSION,
                rcfp_core::DATABASE_COMPATIBILITY
            );
            Ok(())
        }
        [flag] if flag == "--version-json" => {
            println!(
                "{}",
                serde_json::json!({
                    "implementation": rcfp_core::IMPLEMENTATION_VERSION,
                    "protocol_compatibility": rcfp_core::TERRACOTTA_COMPATIBILITY_VERSION,
                    "database_compatibility": rcfp_core::DATABASE_COMPATIBILITY
                })
            );
            Ok(())
        }
        [flag] if flag == "--help" || flag == "-h" => {
            print_help();
            Ok(())
        }
        [command, rest @ ..] if command == "serve" => serve(rest),
        [command, rest @ ..] if command == "connect" => rcfp_core::client::command(rest),
        [command, rest @ ..] if command == "migrate" => {
            rcfp_core::storage::migrations::command(rest)
        }
        [command, rest @ ..] if command == "ingest" => rcfp_core::cli::ingest_command(rest),
        [command, rest @ ..] if command == "optimize-rasters" => {
            rcfp_core::cli::optimize_command(rest)
        }
        [command, file] if command == "data-operation" => {
            let request = serde_json::from_slice(&std::fs::read(file).map_err(|e| e.to_string())?)
                .map_err(|e| e.to_string())?;
            println!("{}", rcfp_core::cli::data_operation(&request)?);
            Ok(())
        }
        [command, rest @ ..] if command == "lambda-event" => lambda_event(rest),
        [command] if command == "lambda-runtime" => lambda_runtime(),
        [] => {
            print_help();
            Ok(())
        }
        _ => Err("unsupported command; use --help".into()),
    }
}

fn lambda_event(args: &[String]) -> rcfp_core::Result<()> {
    let mut database = None;
    let mut event = None;
    let mut index = 0;
    while index < args.len() {
        match args[index].as_str() {
            "-d" | "--database" if index + 1 < args.len() => {
                database = Some(PathBuf::from(&args[index + 1]));
                index += 2;
            }
            "--event" if index + 1 < args.len() => {
                event = Some(PathBuf::from(&args[index + 1]));
                index += 2;
            }
            option => return Err(format!("unsupported lambda-event option: {option}")),
        }
    }
    let database = database.ok_or_else(|| "lambda-event requires -d/--database".to_string())?;
    let event = event.ok_or_else(|| "lambda-event requires --event".to_string())?;
    let event: serde_json::Value = serde_json::from_slice(
        &std::fs::read(&event)
            .map_err(|error| format!("could not read event {}: {error}", event.display()))?,
    )
    .map_err(|error| format!("invalid event JSON: {error}"))?;
    let response = rcfp_core::lambda::handle_event(&database, &event)?;
    println!(
        "{}",
        serde_json::to_string(&response).map_err(|error| error.to_string())?
    );
    Ok(())
}

fn lambda_runtime() -> rcfp_core::Result<()> {
    let database = rcfp_core::settings::var_os("TC_DRIVER_PATH")
        .map(PathBuf::from)
        .ok_or_else(|| "lambda-runtime requires TC_DRIVER_PATH".to_string())?;
    let runtime_api = std::env::var("AWS_LAMBDA_RUNTIME_API")
        .map_err(|_| "lambda-runtime requires AWS_LAMBDA_RUNTIME_API".to_string())?;
    rcfp_core::lambda::run_runtime(&database, &runtime_api)
}

fn serve(args: &[String]) -> rcfp_core::Result<()> {
    let mut database = None;
    let mut bind = "127.0.0.1:5000".to_owned();
    let mut pattern = None;
    let mut rgb = None;
    let mut allow_all = false;
    let mut settings = rcfp_core::settings::snapshot();
    let mut explicit_bind = false;
    let mut index = 0;
    while index < args.len() {
        match args[index].as_str() {
            "-d" | "--database" if index + 1 < args.len() => {
                database = Some(PathBuf::from(&args[index + 1]));
                index += 2;
            }
            "-r" | "--raster-pattern" if index + 1 < args.len() => {
                pattern = Some(args[index + 1].clone());
                index += 2;
            }
            "--database-provider" if index + 1 < args.len() => {
                // CLI startup is single-threaded; workers start below.
                settings.insert("DRIVER_PROVIDER".into(), args[index + 1].clone().into());
                index += 2;
            }
            "--rgb-key" if index + 1 < args.len() => {
                rgb = Some(args[index + 1].clone());
                index += 2;
            }
            "--port" if index + 1 < args.len() => {
                bind = format!("127.0.0.1:{}", args[index + 1]);
                explicit_bind = true;
                index += 2;
            }
            "--debug" | "--profile" => {
                settings.insert(
                    if args[index] == "--debug" {
                        "DEBUG"
                    } else {
                        "FLASK_PROFILE"
                    }
                    .into(),
                    true.into(),
                );
                index += 1;
            }
            "--allow-all-ips" => {
                allow_all = true;
                index += 1;
            }
            "--bind" if index + 1 < args.len() => {
                bind = args[index + 1].clone();
                explicit_bind = true;
                index += 2;
            }
            "--help" | "-h" => {
                print_help();
                return Ok(());
            }
            option => return Err(format!("unsupported serve option: {option}")),
        }
    }
    if database.is_some() == pattern.is_some() {
        return Err("Either --database or --raster-pattern must be given".into());
    }
    let temporary = pattern.as_ref().map(|_| {
        std::env::temp_dir().join(format!(
            "terracotta-serve-{}-{}.sqlite",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ))
    });
    if let (Some(pattern), Some(path)) = (pattern, temporary.as_ref()) {
        rcfp_core::ingest::ingest(&pattern, path, rgb.as_deref(), true, false)?;
        database = Some(path.clone());
    }
    let database = database.unwrap();
    if !explicit_bind {
        let host = if allow_all { "0.0.0.0" } else { "127.0.0.1" };
        bind = (5000..5100)
            .map(|port| format!("{host}:{port}"))
            .find(|address| std::net::TcpListener::bind(address).is_ok())
            .ok_or("No free port between 5000 and 5099")?;
    } else if allow_all {
        bind = bind.replace("127.0.0.1", "0.0.0.0");
    }
    let application =
        rcfp_core::settings::scope(settings, || rcfp_core::http::Application::open(&database))?;
    eprintln!("GDAL {}", application.gdal_version());
    let result = application.serve(&bind);
    if let Some(path) = temporary {
        let _ = std::fs::remove_file(path);
    }
    result
}

fn print_help() {
    println!(
        "Usage:\n  rcfp [-c CONFIG.toml] [--loglevel LEVEL] COMMAND\n  rcfp connect HOSTNAME [--no-browser] [--port PORT]\n  rcfp --version[|-json]\n  rcfp serve (-d DATABASE | -r PATTERN) [--rgb-key KEY] [--port PORT | --bind HOST:PORT]\n  rcfp ingest PATTERN -o DATABASE [--skip-metadata] [--skip-existing] [-q]\n  rcfp migrate DATABASE [--from VERSION] [--to VERSION] [-y]\n  rcfp optimize-rasters FILES... -o FOLDER [--help]\n  rcfp lambda-event -d DATABASE --event EVENT.json\n  rcfp lambda-runtime\n\nRCFP supports MySQL/PostgreSQL, read-only S3 SQLite snapshots and historical migrations. It supports discovery, metadata, singleband, RGB, compute, colormaps and API documentation through native HTTP, packaged container, Lambda-event, Python, and WSGI adapters.\nExecution limits are configured with TC_RASTER_WORKERS, TC_RASTER_QUEUE_CAPACITY, TC_RASTER_TIMEOUT_MS, TC_RASTER_CACHE_BYTES, and TC_REMOTE_RASTER_CACHE_TTL_SECONDS."
    );
}

fn configure_gdal_runtime() {
    if let Some(root) = bundled_runtime_root() {
        let library_dir = root.join("lib");
        if let Some(library) = find_named_file(&library_dir, "libgdal") {
            set_default_env_os("TERRACOTTA_GDAL_LIBRARY", library.as_os_str());
        }
        set_default_env_os("GDAL_DATA", root.join("share/gdal").as_os_str());
        set_default_env_os("PROJ_DATA", root.join("share/proj").as_os_str());
        set_default_env_os("TERRACOTTA_RUNTIME_ROOT", root.as_os_str());
    }
}

fn bundled_runtime_root() -> Option<PathBuf> {
    let executable = std::env::current_exe().ok()?;
    let root = executable.parent()?.parent()?.to_path_buf();
    root.join("lib").is_dir().then_some(root)
}

fn bundled_lib_dir() -> Option<PathBuf> {
    bundled_runtime_root().map(|root| root.join("lib"))
}

fn find_named_file(directory: &Path, prefix: &str) -> Option<PathBuf> {
    std::fs::read_dir(directory)
        .ok()?
        .flatten()
        .map(|entry| entry.path())
        .find(|path| {
            path.is_file()
                && path
                    .file_name()
                    .is_some_and(|name| name.to_string_lossy().starts_with(prefix))
        })
}

fn set_default_env_os(name: &str, value: &std::ffi::OsStr) {
    if std::env::var_os(name).is_none() && !value.is_empty() {
        // SAFETY: called at process startup before worker threads exist.
        unsafe { std::env::set_var(name, value) };
    }
}
