# Command-line interface

RCFP installs `rcfp`. Global options precede the subcommand. Native mode is the
default. Select `--compat-profile terracotta` for the pinned contract.
Both profiles use the Rust parser and executable.
The following help text is extracted from the installed distribution, not copied
from Terracotta's Click definitions.

## Native commands

```{literalinclude} ../_generated/cli-native-main.txt
:language: text
```

### Serve and connect

```{literalinclude} ../_generated/cli-native-serve.txt
:language: text
```

```{literalinclude} ../_generated/cli-native-connect.txt
:language: text
```

`serve -d DATABASE` opens a catalog. `serve -r PATTERN` builds an exploration
catalog from matching rasters. `--rgb-key KEY` controls the final selection key.
Native `--bind HOST:PORT` defaults to local serving. `--port PORT` uses a local
address. `--debug` adds diagnostics. `--profile` emits request timing JSON.
`connect HOSTNAME` opens the GitHub Pages explorer for an RCFP or Terracotta API
and exits. Run `connect` without a hostname to open the server picker.
Use `--local` with a loopback RCFP server to open its bundled explorer
at `/explore/`. Use `--no-browser` to print the selected URL or `--port PORT` to
run a standalone bundled viewer on loopback. See [Connect to a tile server](../how-to/demo.md#connect-to-a-tile-server)
for deployment and CORS settings.

### Inspect and create demo data

`demo -o NEW_DIRECTORY` writes checksum-pinned imagery, attribution, a ready-to-serve
`map.sqlite` catalog and `map.png` preview for [offline first use](../tutorials/first-map.md).
`inspect` reports configuration, local raster/catalog checks and an optional
ingestion destination. See [diagnostics](../how-to/diagnostics.md) for its
JSON/exit-status contract and explicit probe limits.

```{literalinclude} ../_generated/cli-native-demo.txt
:language: text
```

```{literalinclude} ../_generated/cli-native-inspect.txt
:language: text
```

### Ingest

```{literalinclude} ../_generated/cli-native-ingest.txt
:language: text
```

### Optimize

```{literalinclude} ../_generated/cli-native-optimize-rasters.txt
:language: text
```

### Migrate

```{literalinclude} ../_generated/cli-native-migrate.txt
:language: text
```

### Native layers

```{literalinclude} ../_generated/cli-native-layers.txt
:language: text
```

`data-operation FILE.json` accepts a structured native operation and prints JSON.
`lambda-event -d DATABASE --event FILE.json` adapts an API Gateway event locally.
`lambda-runtime` runs inside the custom Lambda runtime. It expects the service
Runtime API environment. See [Lambda packaging](../how-to/lambda.md).
Native parsing/operation errors exit with status 2. Successful commands exit 0.

## Compatibility commands

Rust retains the supported Terracotta commands, flags and configuration. Click
command objects and Python CLI extensions are outside the documented contract.

```{literalinclude} ../_generated/cli-terracotta-main.txt
:language: text
```

```{literalinclude} ../_generated/cli-terracotta-serve.txt
:language: text
```

```{literalinclude} ../_generated/cli-terracotta-ingest.txt
:language: text
```

```{literalinclude} ../_generated/cli-terracotta-optimize-rasters.txt
:language: text
```

```{literalinclude} ../_generated/cli-terracotta-connect.txt
:language: text
```

```{literalinclude} ../_generated/cli-terracotta-migrate.txt
:language: text
```

Parsing and operation failures exit 2 in both profiles. Native
`--version-json` reports protocol/database identity separately from the RCFP
package metadata returned by `rcfp --version`. Check
[configuration precedence](configuration.md) and [migration](../how-to/upgrade-terracotta.md)
when converting scripts.
