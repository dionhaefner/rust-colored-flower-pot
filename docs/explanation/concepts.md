# Rasters, keys, and metadata

A raster is a grid of values plus the information needed to locate it on Earth:
a coordinate system, transform, and nodata/mask semantics. Values might represent
reflectance, elevation, or class labels. RCFP serves one registered raster band
per dataset. RGB and compute combine datasets that share the relevant grid.

A catalog associates an ordered tuple of key values with a raster path and its
metadata. For example, keys `scene, band` and values `lagoon, red` identify one
source. The same hierarchy can represent a sensor/date/product/band collection.
Key names and values belong to your application. Filenames are one convenient
way to extract them, not a required organization.

The final key is the band selector for RGB and compute. Those endpoints select
the preceding keys in their URL and bands through query parameters. Singleband
and metadata select all keys. The `/keys` and `/datasets` discovery endpoints let
a frontend learn this structure rather than hardcoding your directory layout.

Metadata includes geographic bounds, valid coverage, range, percentiles, and
statistical/footprint information needed for discovery and display. It may also
contain application-supplied fields such as category legends. Precomputing it
at ingestion makes first requests more predictable. A writable catalog can
compute missing metadata lazily, so a metadata request can write records. Read-only snapshots need complete metadata in advance.

Catalog storage and raster storage are independent: local SQLite can describe
remote COGs, while a SQL catalog can describe files reachable through a shared
filesystem. A catalog backup does not back up imagery. See
[ingestion](../how-to/ingest.md) and [catalog formats](../reference/catalogs.md).
