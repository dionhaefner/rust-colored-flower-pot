use std::io::IsTerminal;

const ROOT: &str = "Usage: rcfp [OPTIONS] COMMAND [ARGS]...

Serve GeoTIFFs and raster catalogs as interactive maps.

Get started:
  rcfp sample -o sample
  rcfp serve -d sample/map.sqlite
  rcfp connect localhost:5000       # in a second terminal

Map commands:
  sample            Create an offline sample catalog, imagery and preview.
  serve             Start a tile API from a catalog, raster pattern or STAC.
  connect           Open the web viewer for a running tile server.

Data commands:
  inspect           Check rasters, catalogs and configuration with next steps.
  ingest            Register rasters or STAC assets in a catalog.
  optimize-rasters  Write tiled, compressed rasters with overviews.
  layers            Register, inspect and render native layers and mosaics.
  migrate           Upgrade a Terracotta catalog after backing it up.

Deployment commands:
  lambda-event      Handle a saved Lambda request event.
  lambda-runtime    Run inside an AWS Lambda execution environment.

Options (place before COMMAND):
  -c, --config FILE          Load settings from a TOML file.
  --compat-profile PROFILE  native or terracotta. Default: native.
  --loglevel LEVEL          debug, info, warning, error or critical.
  --version                 Print the installed version and exit.
  --version-json            Print version and protocol identities as JSON.
  -h, --help                Show help and exit.

Run rcfp COMMAND --help for options and examples.";

const SERVE: &str = "Usage: rcfp serve [OPTIONS]

Start a raster tile API. Choose exactly one source below.

Sources:
  -d, --database SOURCE      Catalog path or database URL.
  --source SOURCE           Catalog or local source-configuration file.
  -r, --raster-pattern GLOB  Build a temporary catalog from {key} filenames.
  --stac-item ITEM           Local file or HTTPS STAC Item.
  --stac-catalog CATALOG     Traverse a static STAC Catalog or Collection.
  --stac-search URL          Resolve a bounded STAC API Item Search.

Server options:
  --port PORT               Loopback port. Default: first free port 5000-5099.
  --bind HOST:PORT          Explicit listening address, e.g. 0.0.0.0:5000.
  --allow-all-ips           Listen on all interfaces.
  --database-provider NAME  Explicit sqlite, mysql or postgresql provider.
  --rgb-key KEY             Final key used for RGB channel selection.
  --debug                   Enable debug configuration.
  --profile                 Enable request profiling configuration.
  -h, --help                Show this help and exit.

STAC selection options:
  --stac-assets KEYS        Comma-separated asset keys.
  --stac-bbox W,S,E,N       Geographic search bounds.
  --stac-datetime INTERVAL  RFC3339 time or start/end interval.
  --stac-collections IDS    Comma-separated collection IDs.
  --stac-revisions FILE     Asset SHA-256 revisions as JSON.
  --stac-headers FILE       Private request headers as JSON.
  --stac-asset-urls FILE    Refreshed asset locations as JSON.
  --stac-max-items N        Bound resolved Items.
  --stac-max-documents N    Bound traversed metadata documents.
  --stac-max-staged-bytes N Bound verified raster staging bytes.

Examples:
  rcfp serve -d sample/map.sqlite
  rcfp serve -r 'imagery/{date}/{band}.tif' --rgb-key band
  rcfp --compat-profile terracotta serve -d catalog.sqlite --port 5001

Open the web viewer in another terminal: rcfp connect localhost:5000
Press Ctrl+C to stop the server and drain outstanding raster work.";

const CONNECT: &str = "Usage: rcfp connect HOSTNAME [OPTIONS]

Open the bundled web viewer for a running RCFP tile server.
Choose datasets, render singleband/RGB/expressions, inspect pixels and share views.
The viewer's map assets work offline. Raster access depends on the server.

Arguments:
  HOSTNAME                  Server host:port or HTTP(S) URL, including a prefix.

Options:
  --port PORT               Local viewer port. Default: first free 5100-5199.
  --no-browser              Print the viewer URL without opening a browser.
  --source-revision TEXT    Revision used in share links. Default: unversioned.
  -h, --help                Show this help and exit.

Examples:
  rcfp connect localhost:5000
  rcfp connect https://maps.example.com/imagery --no-browser --port 5100

Leave both the server and viewer running. Press Ctrl+C to stop the viewer.";

const SAMPLE: &str = "Usage: rcfp sample -o DIRECTORY

Create a ready-to-serve map using bundled Sentinel-2 and WorldCover imagery.
Writes five checksum-verified rasters, attribution, map.sqlite and map.png.
Keep the data directory beside the catalog at its original location.

Options:
  -o, --output-directory DIRECTORY  Required new directory in an existing parent.
  -h, --help                       Show this help and exit.

Example:
  rcfp sample -o sample
  rcfp serve -d sample/map.sqlite
  rcfp connect localhost:5000       # in another terminal

An existing directory is never overwritten.";

const INGEST: &str = "Usage: rcfp ingest PATTERN -o DATABASE [OPTIONS]
       rcfp ingest --source STAC -o DATABASE [OPTIONS]

Register rasters and their metadata. Quote patterns to preserve {key} fields.

Options:
  -o, --output-file DATABASE Required destination catalog.
  --format FORMAT           terracotta or native. Default: terracotta.
  --rgb-key KEY             Final key for RGB channel selection.
  --bands NAME=N,...        Bind logical names to one-based physical bands.
  --band-key KEY            Logical band key. Default: band.
  --values raw|scaled       Source values. Default: raw. Selects native format.
  --skip-metadata           Defer metadata computation until first use.
  --skip-existing           Keep datasets already registered.
  --source STAC             Ingest a resolved STAC source into native SQLite.
  --search JSON             STAC API search parameters.
  --source-options JSON     STAC discovery, revision and staging options.
  --key-mapping JSON        Map native catalog keys to STAC fields.
  -q, --quiet               Suppress the completion summary.
  -h, --help                Show this help and exit.

Examples:
  rcfp ingest 'imagery/{scene}/{band}.tif' -o catalog.sqlite
  rcfp ingest 'imagery/{scene}.tif' -o bands.sqlite --bands red=3,green=2,blue=1

Native ingestion validates key order and commits the complete ingest atomically.";

const INSPECT: &str = "Usage: rcfp inspect [OPTIONS]

Check raster layout, catalog references and effective settings without writes.
Each reported issue includes a next step. With no source, use DRIVER_PATH or
report configuration only.

Sources (choose one):
  --raster FILE             Inspect one TIFF.
  -r, --raster-pattern GLOB Inspect a {key} filename pattern before ingestion.
  -d, --database DATABASE   Inspect an existing local SQLite catalog read-only.

Options:
  -o, --output-file TARGET  Check a proposed ingest destination without creating it.
  --rgb-key KEY             Validate the final RGB key.
  --bands N,...             Inspect selected one-based bands.
  --render-probe            Sample a 256x256 preview in an isolated process.
  --probe-remote            Explicitly allow bounded HTTP range/access probes.
  --json                    Write a versioned machine-readable report.
  -h, --help                Show this help and exit.

Examples:
  rcfp inspect --raster sample/data/red.tif --render-probe
  rcfp inspect -d sample/map.sqlite --json

Limits: 100 datasets, 256 bands/file, 10 seconds/file. HTTP probes allow
5 seconds and 1024 body bytes, with no redirects.
Exit 0: no errors (warnings/skips allowed). Exit 2: invalid input or failed checks.";

const OPTIMIZE: &str = "Usage: rcfp optimize-rasters FILES... -o FOLDER [OPTIONS]

Write tiled raster copies and overviews for efficient map access.

Options:
  -o, --output-folder DIR   Required destination directory.
  --compression METHOD     auto, zstd, deflate, lzw or none. Default: auto.
  --resampling-method NAME average, nearest, bilinear or cubic. Default: average.
  --reproject              Reproject to Web Mercator.
  --web-mercator-grid       Write a map-aligned Web Mercator grid.
  --all-bands              Preserve all bands on that grid.
  --block-size N           256, 512 or 1024 for map-aligned output. Default: 256.
  --in-memory              Use in-memory intermediate storage.
  --no-in-memory           Use on-disk intermediate storage.
  --overwrite              Replace existing output rasters.
  --skip-existing          Keep existing output rasters.
  --nproc N                Requested processing count. Default: 1.
  -q, --quiet               Suppress progress output.
  -h, --help                Show this help and exit.

Examples:
  rcfp optimize-rasters sample/data/red.tif -o optimized
  rcfp optimize-rasters 'classes/*.tif' -o optimized --resampling-method nearest

Use nearest resampling for categories. --overwrite and --skip-existing conflict.";

const LAYERS: &str = "Usage: rcfp layers COMMAND [ARGS]...

Manage advanced native layers, explicit grids and indexed virtual mosaics.
These commands require the native profile.

Commands:
  create DATABASE                Create an empty native layer catalog.
  list DATABASE                  Print registered IDs as JSON.
  register DATABASE ID RASTER    Register a raster with display defaults.
    --bands N,...                One-based bands. Default: all data bands.
    --stretch MIN,MAX            Explicit display range.
  put DATABASE ID                Read a layer definition from standard input.
  describe DATABASE ID           Print the stored definition as JSON.
  delete DATABASE ID             Delete the definition, retaining source files.
  render DATABASE ID -o IMAGE    Write a rendered PNG using stored defaults.
    --spec FILE                  Override defaults with a JSON render spec.
  import SOURCE DEST ID BINDINGS Import keyed catalog bindings into a layer.
                                BINDINGS is JSON, not a filename.

Examples:
  rcfp layers create layers.sqlite
  rcfp layers register layers.sqlite red sample/data/red.tif --stretch 0,3000
  rcfp layers render layers.sqlite red -o red.png
  rcfp layers put layers.sqlite mosaic < mosaic.json

Run rcfp layers COMMAND --help for that command's arguments.";

const MIGRATE: &str = "Usage: rcfp migrate DATABASE [OPTIONS]

Upgrade a supported Terracotta catalog schema. Back up the catalog first.

Options:
  --from VERSION            Override the detected source version.
  --to VERSION              Target protocol version. Default: 0.10.1.
  --provider NAME           Explicit database provider.
  --dry-run                 Print the migration plan without writing.
  -y, --yes                 Apply without interactive confirmation.
  -h, --help                Show this help and exit.

Examples:
  rcfp migrate catalog.sqlite --dry-run
  rcfp migrate catalog.sqlite --yes

SQLite/PostgreSQL migrations are transactional. MySQL DDL can partially commit;
restore a backup before retrying a failed MySQL migration.";

fn render(text: &str) {
    let color = std::io::stdout().is_terminal() && std::env::var_os("NO_COLOR").is_none();
    for line in text.lines() {
        if color && (line.starts_with("Usage:") || line.ends_with(':')) {
            println!("\x1b[1;36m{line}\x1b[0m");
        } else {
            println!("{line}");
        }
    }
}

pub fn print_root() {
    render(ROOT);
}

pub fn show(args: &[String]) -> rcfp_core::Result<bool> {
    let help = args.iter().any(|a| a == "--help" || a == "-h");
    let Some(command) = args.first().map(String::as_str) else {
        return Ok(false);
    };
    if !(help || command == "layers" && args.len() == 1) {
        return Ok(false);
    }
    let text = match command {
        "--help" | "-h" => ROOT,
        "serve" => SERVE,
        "connect" => CONNECT,
        "sample" => SAMPLE,
        "ingest" => INGEST,
        "inspect" => INSPECT,
        "optimize-rasters" => OPTIMIZE,
        "layers" => {
            if let Some(subcommand) = args.get(1).filter(|a| !a.starts_with('-')) {
                let usage = match subcommand.as_str() {
                    "create" | "list" => format!("Usage: rcfp layers {subcommand} DATABASE"),
                    "describe" | "delete" => format!("Usage: rcfp layers {subcommand} DATABASE ID"),
                    "put" => "Usage: rcfp layers put DATABASE ID < definition.json".into(),
                    "register" => "Usage: rcfp layers register DATABASE ID RASTER [--bands N,...] [--stretch MIN,MAX]".into(),
                    "render" => "Usage: rcfp layers render DATABASE ID -o IMAGE [--spec FILE]".into(),
                    "import" => "Usage: rcfp layers import SOURCE DESTINATION ID BINDINGS_JSON".into(),
                    _ => return Err(format!("unknown layers command: {subcommand}; use rcfp layers --help")),
                };
                render(&usage);
                println!();
            }
            LAYERS
        }
        "migrate" => MIGRATE,
        "lambda-event" => {
            "Usage: rcfp lambda-event -d DATABASE --event FILE\n\nHandle one saved Lambda JSON event and print the response as JSON.\n\nOptions:\n  -d, --database, --source SOURCE  Required catalog or source definition.\n  --event FILE                    Required Lambda request JSON file.\n  -h, --help                       Show this help and exit.\n\nExample:\n  rcfp lambda-event -d sample/map.sqlite --event event.json"
        }
        "lambda-runtime" => {
            "Usage: rcfp lambda-runtime\n\nRun the AWS Lambda Runtime API loop inside a Lambda execution environment.\n\nRequired environment:\n  TC_DRIVER_PATH          Catalog or source definition.\n  AWS_LAMBDA_RUNTIME_API  Runtime API address supplied by AWS.\n\nFor local event handling, use rcfp lambda-event --help."
        }
        _ => return Ok(false),
    };
    render(text);
    Ok(true)
}
