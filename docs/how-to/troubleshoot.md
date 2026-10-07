# Troubleshoot serving and migration

Start with `rcfp --version`, `rcfp --help`, the selected profile, and the server's
`/swagger.json`. Reproduce a failure with one dataset/request and retain the
status, sanitized logs, relevant configuration, and raster/catalog metadata.
Do not include passwords or signed URLs in an issue report.

`rcfp inspect -d catalog.sqlite` reports read-only path, band, raster-layout and
configuration checks. Add `--render-probe` for a bounded local preview or `--json`
for a report. [Diagnostics](diagnostics.md) describes next steps and skipped checks.

| Symptom                                       | Check                                              | Action                                                                                                           |
| --------------------------------------------- | -------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| Native library fails to load                  | Wheel platform tag or separated bundle directories | Install a matching wheel or restore the bundle's `bin/lib/share` layout; check its OS baseline.                  |
| `get_driver` is unavailable                   | `rcfp.compatibility_profile`                       | Select `terracotta-0.10.1` before Python import, or use the native `DataStore` interface.                        |
| Dataset is missing                            | Ordered keys, filename pattern, catalog path       | Inspect `/keys` and `/datasets`; verify pattern matching and raster access as the service user.                  |
| Transparent or misplaced image                | Raster bounds, CRS, mask/nodata, tile coordinates  | Check a preview and metadata first; compare decoded masks and use a tile inside the source bounds.               |
| Categories show blended colors                | Optimization resampling and request palette        | Use nearest preparation and an explicit category map.                                                            |
| First image is slow                           | Lazy metadata, cold blocks, projection setup       | Precompute metadata and compare cold first views separately from warm navigation.                                |
| 503 or 504                                    | Queue capacity, in-flight work, remote I/O         | Measure saturation, choose bounded budgets, and inspect the source service; deadlines do not cancel native work. |
| Read-only catalog error                       | S3 snapshot or unwritable filesystem               | Complete metadata before publication; make write trials against a writable catalog copy.                         |
| Custom basemap fails but offline viewer works | External provider token/network, frontend CORS     | Verify that provider integration separately from the bundled offline raster map.                                 |
| SQL certificate failure                       | Certificate chain, hostname, CA configuration      | Correct TLS/certificate configuration; remote plaintext is unsupported.                                          |
| Migration fails                               | Backend, source schema, printed SQL                | Restore the engine-consistent backup after partial MySQL DDL; do not retry on a partially upgraded catalog.      |

Native `serve --profile` provides request timing JSON. Native `--debug` supplies
diagnostics without an interactive debugger. Both profiles use these native
interfaces. Check [error/status reference](../reference/errors.md) for bounded
request failures and [migration recovery](backup-migrate.md) for rollback steps.
