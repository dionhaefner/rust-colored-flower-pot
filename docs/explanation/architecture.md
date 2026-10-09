# Architecture and ownership

RCFP's native executable and Python adapters call the same Rust raster/storage
core. The core performs catalog access, metadata computation, expressions,
rendering, and HTTP-contract handling. GDAL/PROJ provide native raster access
and coordinate transformations. The wheel or bundle carries their libraries
and data together.

```{image} ../_static/architecture.svg
:alt: Native HTTP, Python or WSGI, and Lambda adapters feed a shared Rust catalog/rendering core. A bounded worker queue serves GDAL and PROJ workers and caches, which read catalogs and raster files.
```

A persistent native application owns workers, its queue, caches, and remote
snapshot leases. Requests carry an immutable settings/profile snapshot into
that ownership. Worker-local GDAL handles avoid sharing unsafe native state.
Bounded execution accounts for queued and outstanding operations. Python
`NativeApplication` and WSGI `Application` preserve this lifetime across requests.

The Terracotta profile adds documented Python driver objects and result
representations over the core. CLI parsing and execution use Rust in both profiles. That shared core makes rendering/storage behavior
consistent across adapters, but does not make every Python interface identical.
Native byte-oriented APIs and compatibility masked-array/driver APIs serve
different application needs.

Returning a response deadline does not cancel GDAL. Teardown drains and joins
owned workers, including native thread-local cleanup. Persistent ownership and
bounded remote access therefore matter to both performance and shutdown.
See [resource tuning](../how-to/resources.md) and
[deployment choices](deployment.md).
