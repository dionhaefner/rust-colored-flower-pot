# STAC selection adapter

STAC discovery lives in `src/stac.rs`. `src/stac/serve.rs` resolves provider
options and supplies lazy immutable raster descriptors. `src/catalog.rs`
adapts SQL stores and STAC selections to one read-only key-catalog interface.
`src/catalog/source.rs` detects SQL, native layer and STAC catalogs for the shared
application constructor. CLI `--source`, Python, WSGI and Lambda all use it.
Local `rcfp-source` JSON definitions hold provider queries, credentials and limits.
Relative locations resolve against the definition directory. Remote documents
cannot supply deployment configuration. Source detection consumes each metadata
document once. SQL and STAC use the same dataset query validation and ordering.
Keys map collection, UTC start time, Item ID and asset. Assets remain last for
RGB and arithmetic selection. The HTTP renderers, cache keys, SDK discovery and
viewer controls are shared. Source calibration wraps the bounded executor's
raw arrays without altering cached samples. RGB batches and deduplicates raw
reads before applying each source's calibration independently.
`src/stac/catalog.rs` resolves local/remote Catalog and Collection graphs through
`child`/`item` links. Breadth-first discovery canonicalizes local file URLs,
visits each document once, and bounds documents, Items and total metadata bytes.
Root/parent/self links are ignored. Metadata references stay local for a local
entry point or on one HTTP origin for a remote entry point. Credentials cannot
follow document links across origins. Item assets resolve against the consumed
document URL, including when a self link points at a different published copy.
This traversal and POST API searches finish before the listener opens.
Private header and signed-URL mappings are reread from JSON files with one
401/403 retry. External processes own credential generation and rotation.

Python's `rcfp.stac` supplies credential
and signing callbacks plus bytes/object representations. Owned Python sessions
retain private native selections and lazily start the existing bounded raster
executor. Spatial/time matching stays at a POST STAC API provider, including
existing PgSTAC-backed APIs. Static catalog traversal is a separate metadata-only
startup source. Direct sources are read-only catalog adapters. Managed ingestion stores their
resolved snapshots and bindings in the native keyed database. Neither hosts a
STAC API.
Explicit Item consumption shares the same native asset resolver.

Search definition and selection content have independent canonical SHA-256
identities. Bounded complete pagination precedes deterministic UTC start-time,
collection/Item/asset ordering. Duplicate occurrences count against ingress
limits before equivalent Items collapse. Conflicting revisions fail atomically.
The public descriptors exclude source URLs, upstream arbitrary properties and
headers. Only explicit field mappings are exposed. HTTPS, same-origin pagination,
no redirects and sanitized error categories keep refresh credentials private.
Python retries a whole search once after 401/403 with new credentials. It retries
asset signing once on download 401/403. These callbacks are application-owned.
RCFP does not install a cloud authentication SDK or provider-specific signer.

The initial revision contract is a verified SHA-256 digest, supplied by the STAC
File extension or explicitly by the caller. Time/ETag/signature cannot guarantee
immutability of mutable object bytes. Therefore assets are copied/downloaded only
when metadata or rendering needs them and published to selection-owned temporary files after verification.
Staging is cumulative-budget-bounded and downloads serialize within a selection.
Source handles/array keys see private immutable filenames without credentials.
Workers drain before selection files disappear. Failed downloads/checksums leave
no partial publication or charged staging bytes. Distinct selections never share
source handles or retained arrays, including equal original URLs with new revisions.

This trades remote first-render range efficiency for a provable immutable content
boundary without changing the raster worker/source-generation contract. First-use
whole-file costs are explicit in user guidance. Later support for version-addressed
range reads needs provider/version authentication and mutable/download-cache tests.
TTL or URL query stripping alone would not establish correctness. The initial
adapter accepts self-contained TIFFs, not nested VRTs or external sidecar graphs.

STAC projection/footprint fields are discovery metadata. Embedded georeferencing
and mask/alpha/nodata remain authoritative in the renderer. Declared nodata must
match before interpolation. Scale/offset apply after alignment. The common request selects logical datasets, map tiles, palette and display range.
Physical bands resolve during discovery/ingestion. Selected-band metadata statistics are measured from the verified raster and
calibrated into physical units, including reversed ranges/quantiles for negative
scales. Metadata uses the shared lazy sampling shape and retains at most 256 descriptors,
independently of raw and encoded render results. Native layer/mosaic
policy remains independent. This selection does not silently choose overlap,
blend observations or encode a catalog layer. SQL store serialization and compatibility behavior are preserved. The adapter is native-only.

`tests/stac_contracts.py` exercises the real HTTP wire protocol, indexed provider
size behavior, credential refresh and pinned raster output through an installed
wheel. Its local provider is a supported-contract simulator, not hosted PgSTAC
qualification. Selection timing excludes GDAL and raster transfer. First/repeated
render timings are recorded separately. No native timing ratio is inferred for
WAN or remote hosted providers.

`tests/stac_live.py` separately qualifies HTTPS searches, pagination, selection
identity and calibrated XYZ rendering against Earth Search and Microsoft
Planetary Computer. Its original Sentinel TIFFs are pinned independently in
`tests/fixtures/stac-live.json`. It checks the shared CLI source configuration and
Chromium viewer, including provider-issued signed assets and an invalid-signature
403 retry. This opt-in network runner is separate from deterministic contracts.
Its timings do not establish provider availability or WAN performance guarantees.
