# Python-hosted scalar computations

`rcfp.ComputeApplication` overlays trusted host registrations on a native
`rcfp-native-1` layer catalog. Each registration binds one explicit observation,
1–16 named physical inputs, an explicit model version, parameter declarations,
external dependency revisions and display defaults. Registration validates the
selected bindings and style before publication. No code is persisted or accepted
through HTTP. The core and standalone adapters never invoke Python callbacks.

## Native and host ownership

`src/http/python_compute.rs` prepares an inspected observation snapshot, reads
aligned inputs through the application's persistent `RasterExecutor`, and renders
scalar output through the same `Application::scalar_image` helper as ordinary
singleband images. The C extension releases the GIL around these native calls.
Metadata and plans use JSON. Pixel values and independent validity masks use
little-endian f64 and boolean byte planes. Rust/Python own separate copies, and
Python's immutable input buffers outlive any retained NumPy view.

`python/rcfp/compute.py` owns a fixed host thread pool and admission permits for
running plus queued pipelines. The permit stays occupied while a timed-out queued
entry remains in the executor, avoiding unbounded accumulation of cancelled queue
entries. A queued pipeline checks its deadline before inspection/reads and before
callback execution. Host response deadlines release callers without releasing
running work. Close removes queued entries, joins running pipelines and releases
the native session. Callback reentrancy, asynchronous callbacks, post-fork reuse
and cross-process cache sharing are outside this boundary.

Callbacks operate after requested-grid resampling and optional calibration. They
receive one coherent observation and choose output validity. Existing GDAL integer
resampling precision remains in effect before the f64 transfer. No padding,
pre-resampling model execution or per-band compositing is introduced. The initial
output is scalar with explicit numeric stretch, without inferred output statistics.

## Cache identity and discovery

Native raster caches retain their existing source/grid ownership. Host computed
buffers and encoded PNGs have independent bounded LRU byte budgets. Computation
identity includes the layer namespace, inspected selected assets, local raster
and sidecar generations, physical inputs/calibration, observation attributes,
requested grid, model version, parameters and dependency revisions. Image identity
adds style. The host rechecks source identity after callbacks before publication.
Input reads also check snapshot generations before and after native work.

No function-pointer identity enters caching. Missing dependency declarations or
unrevisioned remote inputs disable derived retention and concurrent callback
sharing. Revision-addressed remote locations remain the host's responsibility.
Opaque revision labels do not certify immutable bytes or flush GDAL's URL caches.

Registered scalar datasets use `[layer, observation, value]` keys and existing
opaque tileset IDs. `/datasets` and `/tilesets` prepend registered entries while
preserving native catalog filtering/paging within the catalog group. Each group
retains its own requested ordering. Source-layer IDs cannot collide with host
registrations. TileJSON, keyed previews/XYZ, Web Mercator matrix routes and the
existing viewer/SDK integrations use the ordinary image contract. Declared
preview bounds supply geographic discovery bounds. Responses use no-store and
content-qualified ETags. Native public rendered-caching settings do not publish
Python model results to shared caches.

Computed discovery and TileJSON reuse the native publication URL builder.
`PUBLIC_URL` controls HTTPS and deployment prefixes. The unconfigured TileJSON
origin comes from the validated Host header, without forwarded-header trust.

The public contract and lifecycle limits are in
[Python compute guidance](../docs/how-to/python-compute.md). The installed-wheel
[contract runner](../tests/python_compute_contracts.py) uses pinned real Sentinel
inputs, independent Rasterio warps and HTTP/browser clients. Qualification evidence
and platform exclusions belong in [validation](human-validation.md).
