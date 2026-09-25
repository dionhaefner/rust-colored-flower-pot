# Real raster reference corpus

The first phase, P00, acquires this corpus and captures Python results. P01 uses
it to verify the Rust slice; later phases reuse it. This document is a selection
and acquisition brief, not evidence that any particular asset has been downloaded
or tested. Follow the visual compatibility policy in [REWRITE.md](../REWRITE.md).

## Keep the collection small and useful

Start with roughly three or four source scenes/products across several providers.
Select them for distinct coverage, not collection size. One source can provide
multiple bands, a recognizable crop, an edge/mask crop, and a stress case.
Prefer a shared geographic area for imagery, elevation, and land cover where
practical, plus one different projection or latitude if it adds useful coverage.

Real pixels are the default. Extract native-resolution spatial windows rather
than generate synthetic landscapes. Choose coastlines, towns, fields, mountains,
or recognizable river networks so visual mistakes are easy to identify. Keep
small manufactured microfixtures only for precisely defined edge cases such as
NaN arithmetic or all-invalid output that cannot be isolated reliably in a real
subset. Document their purpose; do not use them as the primary visual corpus.

## Source shortlist

These public discovery sources were checked when this plan was written. P00 must
select exact asset IDs/URLs, inspect actual metadata, confirm anonymous access and
reuse terms, and freeze them in a manifest. Do not assume every collection member
has the desired dtype, mask, size, or TIFF organization.

| Source | Why select it | Selection instruction |
| --- | --- | --- |
| [Sentinel-2 COGs / Earth Search](https://registry.opendata.aws/sentinel-2-l2a-cogs/) | Real optical imagery and separate spectral bands for RGB and compute. Public COG archive with STAC discovery. | Pin one scene and RGB/NIR assets; prefer recognizable terrain and retain a native footprint edge. Inspect band grids and any scale/offset metadata; do not silently apply radiometric corrections the legacy path does not apply. |
| [Copernicus DEM](https://registry.opendata.aws/copernicus-dem/) | Continuous elevation and geographic-coordinate COG input from a separate provider. | Pin a terrain-rich tile, inspect its dtype and valid-data behavior, and crop a legible region. Do not assume an absent ocean tile is an actual raster with nodata pixels. |
| [ESA WorldCover](https://esa-worldcover.org/en/data-access) | Categorical land cover, native geographic projection, and high pixel counts. Products are distributed as 3-degree COG tiles. | Pin product year/version and Map asset; preserve category values and use the published legend for inspection. A full tile is a candidate for decoded-memory stress; inspect actual file bytes before calling it a large-file test. |
| [USGS 3DEP raster products](https://www.usgs.gov/3d-elevation-program/about-3dep-products-services) | Alternative elevation source or additional native projection/resolution when the three core sources leave a gap. | Choose a downloadable DEM, not lidar point clouds or a rendered map service. Inspect the individual asset before selecting it for size, masks, or float coverage; do not fetch an entire regional archive. |

An accessible regional orthophoto can replace the optional fourth source when
it adds useful native byte imagery, masks, or storage diversity. Do not assume
all imagery in an open-data registry has anonymous downloads: the listed
[NAIP AWS buckets](https://registry.opendata.aws/naip/) are requester-pays. Prefer
a freely accessible alternative instead of introducing an account dependency
into the default corpus.

Choose different exact assets if these prove unsuitable. Record the coverage
reason rather than asking the user to choose a scene. No collection-wide syncs.

## Coverage dimensions

Use a small matrix, not a Cartesian product. Record which source/subset covers
each dimension, and which required cases remain absent.

| Dimension | Desired coverage across the corpus |
| --- | --- |
| Meaning and dtype | Recognizable imagery with RGB/compute bands, continuous values including native floating-point data, categorical integer data; byte and wider-integer inputs where available. |
| Geography | Native projected and geographic inputs; differing resolutions; nontrivial reprojection and boundary behavior. Add a high-latitude/antimeridian case only where it closes a real gap. |
| Validity | Actual nodata/footprint edges, valid zero values, and a native mask/alpha case where obtainable. Derived mask cases are explicitly labeled. |
| Storage | Preserve some source block/overview/codec characteristics; compare ordinary GeoTIFF and COG paths when supported. Avoid normalizing every input to the same generated format. |
| Scale | Quick visual crops plus a full/large real raster exceeding the chosen decoding memory budget; physical file size and BigTIFF offsets tracked separately. |
| Access | Cached local assets and range-capable remote reads of the same data; detailed reads and overview-backed previews. |

Multi-band source imagery does not imply new Terracotta multi-band dataset
semantics: keep the parent and extract bands as separate compatible datasets
where the legacy application expects them. Preserve their alignment and masks.

## Subsets, provenance, and visual checks

For each source, retain its original metadata and at least one reference to the
unaltered asset. Extract pixel windows without warping, resizing, stretching,
dtype conversion, or mask replacement by default. Validate the subset's values,
transform/CRS and validity against the parent window. Include a window crossing
a real footprint edge or several storage blocks where possible, not just a
uniform interior patch.

Cropping rewrites TIFF organization and often drops/rebuilds overviews. A crop
therefore cannot prove compatibility with the original encoding. Exercise the
unaltered parent through direct range reads or a local full download as well.
Any derived stripped/tiled/COG/BigTIFF, reprojected, or altered-mask variant must
have an explicit recipe and remain distinguished from native publisher coverage.
Use real imagery for these variants; do not pad or repeat pixels to fake scale.

Retain a machine-readable manifest containing source provider, collection/item/
asset/version, original URL, retrieval date, terms/attribution, byte length,
checksums where available, CRS/transform, dimensions, bands/dtypes, scale/offset,
nodata/masks, codec/block sizes, overviews, and TIFF variant. For derivatives add
parent identity, exact pixel window, bands, command/tool versions, creation
options, output checksums, and source properties lost or changed.

For range-only acquisition, retain source version/ETag and length plus hashes
of cached content/derived subsets. Do not describe an ETag as a SHA checksum or
claim a full-file hash when the file was not downloaded. Reject unexplained asset
changes; do not silently refresh reference captures to a new scene/version.

Build a contact sheet or browseable index of the captured Python previews, with
location, source/subset ID, tile coordinates/zoom, dimensions, band assignment,
stretch/palette, and settings. Include ordinary map views, adjacent tiles across
a seam, and an edge/mask view. Later Rust comparisons must reuse those exact
settings, not independently auto-stretch both outputs until they look similar.

## Fast and stress tiers

**Fast tier:** a handful of roughly 512–2048-pixel native-resolution crops, with
dimensions chosen to retain recognizable features and boundaries. Aim for a
combined local corpus around 100–250 MiB or less, adapting where evidence needs
more. Routine tests use the cache and never silently fetch large files. Retain
small derivatives in Git only if size and redistribution terms are suitable.

**Stress tier:** one or two full assets or much larger genuine spatial subsets.
Acquire at least one in P00 whose decoded working set exceeds the chosen test
memory budget. Record the source's actual file bytes, total pixels, dtype/bands,
estimated decoded arrays/masks/temporaries, and peak memory in the Python run.
Keep bulk data outside Git, cache it once, and make stress execution explicit.
Prefer range reads and bounded transfers; plan disk/transfer needs before fetching.

Keep these separate claims:

- Large decoded arrays test memory-bounded reading, statistics, and scheduling.
- A physically multi-GiB file tests real large-file I/O. A compressed image with
  billions of pixels need not be a multi-GiB file.
- Reading actual TIFF blocks beyond the 4 GiB boundary tests large offsets.
  A tiny file forced into BigTIFF format does not prove that behavior.

Target a real multi-GiB/BigTIFF case as part of the stress tier, with a pinned
large parent and remote ranges if a full download is wasteful. If no suitable
native asset is practical, a bounded large crop or contiguous real-data mosaic
is acceptable with explicit provenance and transformation details. Never count
a VRT referencing many small files, sparse padding, or repeated image data as
proof of physical TIFF large-offset handling. If this case cannot yet be obtained,
leave that specific coverage gap open; the decoded-memory case does not clear it.

Use the full source for both a broad preview and detailed windows touching
base-resolution blocks. In P01 compare visible results with Python under a
declared memory budget; in P02 measure local/remote bytes, request counts, cold/
warm behavior, memory, and concurrency. In P05 reuse it for chunked metadata and
disk-backed optimization. Avoid eagerly decoding the whole image just to generate
a thumbnail, calculate a checksum, or populate a fixture helper.

## Acquisition behavior and exit evidence

P00 implements documented acquisition/subsetting commands with exact asset
selection, local caching, integrity checks, resumable/atomic downloads where
appropriate, and clear failures. Report expected bytes and cache location before
large transfers; use configurable bounds and free-space checks rather than a
hard-coded enormous download. Ordinary public-data downloads needed for this
assigned phase require no additional project-level permission; tool permissions
and authorization for paid resources still apply.

Run downloads explicitly during corpus preparation, then run routine checks
offline. A changed or unavailable remote asset is a reproducibility issue to
record, not a reason to regenerate golden outputs silently. An alternate source
must preserve the intended coverage and receive its own provenance.

The P00 exit requires acquired inputs, reproducible subsets, an honest dimension
matrix, a visual index, and a Python large-raster smoke with measurements.
P01 must actually render that corpus. Listing promising download sources is
insufficient, and an optional stress job skipped in CI is not a successful run.
