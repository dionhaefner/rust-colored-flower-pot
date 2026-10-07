# Configure and run the native server

Start with a catalog and a TOML file. TOML keys are unprefixed setting names;
environment equivalents use `TC_` for legacy/core settings.

```toml
DEFAULT_TILE_SIZE = [256, 256]
USE_MULTIPROCESSING = false
ALLOWED_ORIGINS_METADATA = ["*"]
ALLOWED_ORIGINS_TILES = ["https://maps.example.com"]
```

Save as `settings.toml`:

```sh
rcfp -c settings.toml --loglevel info serve -d catalog.sqlite --bind 127.0.0.1:5000
```

Global options precede the command. Explicit CLI settings override TOML,
which overrides the environment and then defaults. Unknown TOML settings fail
at startup. Native resource controls such as `TC_RASTER_QUEUE_CAPACITY` are
separate environment variables; do not assume every one is accepted as a TOML key.
The [configuration reference](../reference/configuration.md) separates these groups.

Metadata and tile CORS settings are independent. Set only the tile origins your
frontend needs, and test its `Origin` and preflight requests. CORS is a browser
policy; deploy authentication/access control through your application or proxy
if needed. Bind `0.0.0.0:5000` only when the deployment should accept external
connections, and configure its public routing/HTTPS accordingly.

Use `rcfp -c settings.toml inspect --json` to report configuration values and
origins before starting workers. See [diagnostics](diagnostics.md) for local
raster/catalog checks and bounded probes.

For native diagnostics use `serve --debug`; for JSON request timing use
`serve --profile`. Native debug does not supply a Python interactive debugger or
reloader. Settings are captured when an application/session starts. Restart the
service or construct a new application to apply changes rather than mutating
the environment during concurrent requests.
