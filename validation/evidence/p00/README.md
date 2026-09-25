# P00 evidence record

- Run/date/phase/coverage: 2026-09-22; P00; C01–C36 inventory, C35, and P00
  portion of C36.
- Reference: commit `6db38d5873cde5ebabe47338a1f61ddc788eada6`,
  release identity 0.10.1. `validation/reference/environment.json` contains the
  exact source archive hash, Python/package/native-library/data hashes, platform,
  endianness, GDAL/PROJ/GEOS/SQLite and codec versions. The sibling working tree
  had a modified README and was never changed or used as executable source.
- Rewrite state: complete uncommitted workspace source snapshot; Rust 1.85.1,
  edition 2024, dependency-free P00 crate. `Cargo.lock` is retained.
- Inputs: `validation/reference/assets.json`, `subsets.json`, and `corpus.json`
  contain pinned URLs/ETags/lengths/SHA-256, attribution/terms, exact windows,
  raster metadata, TIFF variants/offsets, overview/block/codec data and hashes.
- Artifact retention: parent/subset rasters and generated runs are ignored under
  `.cache/corpus` and `validation/artifacts`; reacquire using the documented
  commands. The 2 MB reference capture, contact sheet, exact settings, arrays,
  masks, and structured/storage data are retained under
  `validation/reference/capture`.

## Passed checks

| Check | Actual result |
| --- | --- |
| Frozen source | Sibling and exported commit resolved to the full designated hash; archive recorded in environment manifest |
| Rust skeleton | `validation/tools/cargo.sh build --locked --offline`; `test --locked --offline`; CLI `--version` all exit 0 |
| Focused baseline | 61 settings/app/expression tests passed |
| Broad unchanged baseline | `env -u NO_COLOR .venv-reference/bin/python -m pytest .cache/reference/tests --ignore=.cache/reference/tests/client`: 479 passed, 54 skipped |
| Acquisition | Six pinned assets, 501,570,928 bytes, downloaded and SHA-256 verified; offline recheck passed |
| Native subsets | Seven parent-window comparisons passed for pixels, masks, CRS, transform, dtype, scale and offset |
| Oracle capture | 45 HTTP cases, 26 PNGs, seven typed arrays/masks, SQLite values/blobs, errors and warm timing context |
| Reproduction | Fresh capture compared with retained reference: 26 images and seven arrays, zero differences |
| Comparator proof | 19 tests passed; controlled status/key/order/storage/type/membership/dimension/mask/pixel/shift/seam/category/file/array mutations were detected; PNG recompression passed |
| Stress | Full 36,000 × 36,000 WorldCover parent broad preview and z15 detail passed under 512 MiB watchdog; 104,165,376-byte reported peak RSS and 103,321,600-byte sampled peak |
| Visual inspection | Contact sheet inspected at native 256 px display: Sentinel footprint/transparency, RGB coastline, DEM relief, WorldCover classes, NDVI and derived-mask view were recognizable with no capture artifact observed |

The broad suite first produced one collection error because sandboxed UDP socket
creation was denied. Rerunning with local sockets enabled progressed normally.
The source wheel then failed `/apidoc` because it omitted the template; the
unchanged editable source install fixed the environment (RW-009). Two color-log
assertions failed only while ambient `NO_COLOR=1` suppressed ANSI codes; the
final baseline removed that test-environment variable (RW-010). No application
source was patched.

The 54 broad-suite skips are recorded in the JUnit XML. They comprise external
MySQL/PostgreSQL server cases unavailable without explicit test server arguments;
the relevant backends remain scheduled for P06. Browser-client end-to-end tests
were excluded because they require a browser/runtime stack and belong to P08.

## Performance and limits

The full stress COG is 85,040,674 bytes but represents 1.296 GB decoded values,
2.592 GB with a byte mask, and a 5.184 GB float32 work array. Its overview-backed
preview took 0.024 s and z15 native-detail read 0.007 s in this warm local run.
Those figures are smoke context, not production performance. Application raster
cache was disabled; a checksum warmed the OS cache, and Linux `read_bytes` was
zero while `read_chars` recorded read syscalls. The ordinary preview timing file
is likewise Flask test-client context only.

This evidence does not prove a Rust renderer, production performance, remote
range behavior, cloud services, native alpha, all expression/API cases, or
physical multi-GiB/BigTIFF offsets. The selected parent is classic TIFF and has
no block beyond 4 GiB. Those gaps remain in COVERAGE.md.
