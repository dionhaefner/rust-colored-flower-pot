# P02 validation evidence

P02 was validated on 2026-09-22 against frozen reference commit
`6db38d5873cde5ebabe47338a1f61ddc788eada6`, its pinned Python environment,
GDAL 3.9.3, and the P00 checksum-pinned real raster corpus. Commands run from
the repository root.

## Fast RGB, range, cache, and concurrency differential

```bash
.venv-reference/bin/python validation/tools/p02_validate.py \
  --out validation/evidence/p02/fast --port 5094 --range-port 5194
```

This captures frozen-Python results and compares decoded RGBA for explicit,
default, percentile, repeated-band, partial-mask, non-square, tiled, and
out-of-bounds RGB cases. The partial-mask fixture is a script-generated,
narrowly justified derivative: it preserves real Sentinel-2 pixels and native
georeferencing while adding a red-only central internal-mask window. The run
also compares local and byte-range-served rasters, cold/warm concurrency, cache
traffic, RSS, handles, and worker metrics. See `fast/report.json` and the
ordinary-scale `fast/side-by-side.png`, which was visually inspected with no
visible Python/Rust difference.

## Overload, timeout, and recovery

```bash
.venv-reference/bin/python validation/tools/p02_failure.py \
  --out validation/evidence/p02/failure --port 5095 --range-port 5195
```

This uses one raster worker, one queued job, a 5 ms response wait, and a delayed
local range server. It requires both immediate 503 overload and 504 timeout
responses, waits for timed-out native work to drain, injects a missing-raster
failure, and verifies a subsequent cached raster response. See
`failure/report.json`. A 504 bounds response waiting; it does not claim native
GDAL cancellation.

## Full-parent local/range stress

```bash
.venv-reference/bin/python validation/tools/p02_stress.py \
  --out validation/evidence/p02/stress --port 5096 --range-port 5196 \
  --budget-mib 512
```

This uses the unaltered checksum-pinned 36,000 × 36,000 WorldCover publisher COG
for a broad preview and a z15 base-resolution detail. It compares local and
HTTP range results exactly with P00 Python outputs, records request/byte traffic,
proves warm cache reads add no traffic, and enforces the RSS budget. See
`stress/report.json`.

## Build and regressions

- `validation/tools/check.sh`: offline corpus/subset verification, 19 comparator
  mutation tests, and Rust tests.
- `validation/tools/cargo.sh clippy --locked --offline --all-targets -- -D warnings`.
- `validation/tools/cargo.sh build --locked --offline`.
- P01's 26-case exact differential rerun through `p01_validate.py`; retained at
  `p01-regression/report.json`.

No AWS/S3 credential variables were available, so no real S3 or custom endpoint
claim is made. The local range server is not a substitute. The corpus contains
no physical multi-GiB BigTIFF/offset-beyond-4-GiB asset; that distinct release
gap remains open.
