# Ingest existing rasters

Use CLI ingestion when filenames encode your keys. Use Python when metadata
comes from a manifest, directory hierarchy, or external database. Ingestion
registers paths and computes metadata; it does not upload or duplicate raster
files. Make those paths reachable by the serving process.

## Match filenames

```sh
rcfp ingest '/data/{scene}_{band}.tif' -o catalog.sqlite --rgb-key band
rcfp serve -d catalog.sqlite
```

Quote the pattern so the shell does not expand it. Named braces create ordered
keys; `{}` ignores a field. `--rgb-key` places the band key last for RGB/compute
selection. Key matches are alphanumeric: a filename `S2_2021-06-29_red.tif` will
not match `{scene}_{date}_{band}.tif`. Split the date into keys, normalize filenames,
or use Python. Inspect `/keys` and `/datasets` before serving production traffic.

`--skip-existing` preserves already registered key tuples. `--skip-metadata`
defers metadata computation and can make the first render slower; writable
catalogs may fill missing metadata later. Precompute metadata before publishing
a read-only remote snapshot. Use [CLI reference](../reference/cli.md) for all flags.

In native mode, the ordered key names must match an existing catalog, and all
matched inserts commit together. A failure rolls back the ingest; a new catalog
can remain empty. Replacing a registration with `--skip-metadata` removes its old
metadata, so lazy fill describes the new raster. The write transaction can remain
open during metadata computation. The `terracotta-0.10.1` profile instead warns
on key-name mismatches, writes positionally, commits each successful insert, and
can retain previous metadata when replacing without new metadata.

## Register arbitrary paths

```python
from pathlib import Path
import rcfp

store = rcfp.DataStore("new-catalog.sqlite")
store.create(["scene", "band"])
store.insert(["lagoon", "red"], Path("docs/examples/data/red.tif").resolve())
metadata = store.get_metadata(["lagoon", "red"])
```

Create only a new catalog. Individual calls commit separately. Use
`DataStore.batch` for an atomic insert/delete batch; each operation has its own
`operation`, `keys`, and `raster` fields as described in
[the native Python reference](../reference/python.md). A compatibility driver
connection context does not make all of its operations one atomic transaction.
