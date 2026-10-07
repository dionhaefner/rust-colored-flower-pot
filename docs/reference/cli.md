# Command-line interface

RCFP installs `rcfp`. Global options precede the subcommand. Native mode is the
default; select `--compat-profile terracotta-0.10.1` for the pinned contract.
Both profiles use the Rust parser and executable.
The following help text is extracted from the installed distribution, not copied
from Terracotta's Click definitions.

## Native commands

```{literalinclude} ../_generated/cli-native-main.txt
:language: text
```

### Serve and connect

`serve -d DATABASE` opens a catalog; `serve -r PATTERN` builds an exploration
catalog from matching rasters. `--rgb-key KEY` controls the final selection key.
Native `--bind HOST:PORT` defaults to local serving; `--port PORT` uses a local
address. `--debug` adds diagnostics; `--profile` emits request timing JSON.
`connect HOSTNAME --no-browser --port PORT` starts the bundled client without
launching a browser, useful for a remote shell or controlled deployment.

### Inspect and supplied samples

`sample -o NEW_DIRECTORY` exports checksum-pinned imagery, attribution and the
Python catalog helper for [offline first use](../tutorials/first-map.md).
`inspect` reports configuration, local raster/catalog checks and an optional
ingestion destination. See [diagnostics](../how-to/diagnostics.md) for its
JSON/exit-status contract and explicit probe limits.

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

`data-operation FILE.json` accepts a structured native operation and prints JSON.
`lambda-event -d DATABASE --event FILE.json` adapts an API Gateway event locally.
`lambda-runtime` runs inside the custom Lambda runtime; it expects the service
Runtime API environment. See [Lambda packaging](../how-to/lambda.md).
Native parsing/operation errors exit with status 2. Successful commands exit 0.

## Compatibility commands

Rust retains the supported Terracotta commands, flags and configuration. Click
command objects and Python CLI extensions are outside the documented contract.

```{literalinclude} ../_generated/cli-terracotta-0.10.1-main.txt
:language: text
```

```{literalinclude} ../_generated/cli-terracotta-0.10.1-serve.txt
:language: text
```

```{literalinclude} ../_generated/cli-terracotta-0.10.1-ingest.txt
:language: text
```

```{literalinclude} ../_generated/cli-terracotta-0.10.1-optimize-rasters.txt
:language: text
```

```{literalinclude} ../_generated/cli-terracotta-0.10.1-connect.txt
:language: text
```

```{literalinclude} ../_generated/cli-terracotta-0.10.1-migrate.txt
:language: text
```

Parsing and operation failures exit 2 in both profiles. Native
`--version-json` reports protocol/database identity separately from the RCFP
package metadata returned by `rcfp --version`. Check
[configuration precedence](configuration.md) and [migration](../how-to/upgrade-terracotta.md)
when converting scripts.
