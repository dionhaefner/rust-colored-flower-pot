# P01 validation evidence

P01 was completed on 2026-09-22 against frozen reference commit
`6db38d5873cde5ebabe47338a1f61ddc788eada6`, its pinned Python environment, and
GDAL 3.9.3. All commands ran from the repository root.

## Build and focused checks

- `process/validation/tools/check.sh`: passed. Offline asset/subset verification passed;
  all 19 comparator mutation tests and all eight Rust unit tests passed.
- `packaging/cargo.sh clippy --locked --offline --all-targets -- -D warnings`:
  passed after adding the Clippy component for the pinned 1.85.1 toolchain.
- `packaging/cargo.sh build --locked --offline`: passed.

## Python-to-Rust differential

Command:

```bash
.venv-reference/bin/python process/validation/tools/p01_validate.py \
  --out process/validation/evidence/p01/fast --port 5091
```

Result: 26/26 retained P01 cases passed. The run used the unchanged
Python-created `reference.sqlite`. It compared ordered key/dataset results,
filters/paging, complete and selected-column metadata, single-band previews,
adjacent tiles, grayscale, viridis and explicit categorical palettes,
reprojection, nodata/internal masks, non-square output, the legacy empty tile,
percentile behavior, CORS/headers, and selected errors. All retained images were
exactly equal after RGBA decoding; PNG stream bytes were intentionally ignored.
See [report.json](fast/report.json) and the visually inspected
[side-by-side.png](fast/side-by-side.png).

## Full-parent resource smoke

Command:

```bash
.venv-reference/bin/python process/validation/tools/p01_stress.py \
  --out process/validation/evidence/p01/stress --port 5092 --budget-mib 512
```

Result: passed with 41,926,656 bytes sampled peak server RSS under the
536,870,912-byte budget. The source was the checksum-pinned 85,040,674-byte
classic-TIFF publisher COG at 36,000 × 36,000 pixels with 2/4/8/16/32/64
overviews and a 1.296 GB decoded value plane. Both the broad preview and z15
detail (about 4.78 m/pixel, finer than the source's 10 m pixels) decoded exactly
like the P00 Python results. This establishes overview-backed and native-block
local reads, not physical multi-GiB/BigTIFF or remote range behavior. See
[report.json](stress/report.json).

No external service, remote range, cloud, RGB, compute, write-path, or packaged
deployment claim is made by this evidence.
