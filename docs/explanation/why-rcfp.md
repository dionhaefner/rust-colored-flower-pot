# Why use RCFP?

RCFP lets you choose native serving while keeping supported Terracotta catalogs,
raster locations, and HTTP clients. Its named compatibility profile supports
documented Python/CLI/WSGI workflows with a one-time namespace change. Native APIs
provide a smaller path for new applications. Begin with
[the migration guide](../how-to/upgrade-terracotta.md) to assess your application.

## A native service with several integration choices

Use the standalone executable when you need a raster server without a Python
application host. Use the wheel's Python/WSGI APIs when your application needs
that integration, or the custom Lambda runtime for its matching deployment
model. These adapters share the Rust core rather than separate implementations
of raster algorithms. Python remains a dependency of the wheel/facade, while
native bundles have their own native-runtime requirements.

## A bundled raster runtime

A matching wheel includes GDAL/PROJ libraries, data, and the executable. Operators
do not need to create a conda environment or install Rasterio to load RCFP.
The source build downloads locked native archives; prebuilt wheels avoid that
compiler/build step. The tradeoff is platform-specific artifacts and bundled
library size. [Installation](../how-to/install.md) explains which requirements
apply to a wheel versus a source build.

## Capacity controls you can inspect

RCFP exposes worker counts, queue limits, cache budgets, deadlines, and opt-in
metrics. This makes saturation and capacity choices explicit. Application-owned
workers/caches also give Python integrations a reusable lifetime. Response
limits and deadlines have concrete behavior: a full queue can return 503 and
expired waits can return 504 while submitted native work continues. Review
[the operational limits](../reference/errors.md) when choosing budgets.

## Performance opportunities with a defined scope

The native implementation has opt-in fast paths and idle-worker neighbor
prefetch. They can help repeated tile navigation under the matching source and
projection conditions. First views, remote data, general GDAL fallback, memory,
and loaded tail latency are separate measurements. The UTM sampler changes
some pixel values and is disabled in the compatibility profile. Read
[performance and benchmarking](performance.md), including a reproducible local
preview comparison with exact decoded-image checks, before enabling it or applying
someone else's speedup to your deployment.

## Understand the tradeoffs before switching

Use the [support matrix](../reference/support.md) for actual API and platform
scope. Private Terracotta internals and Rasterio object utilities are not public
compatibility promises. Debugging/profiling behavior, cache accounting, and
application shutdown also differ. Keep a catalog backup and the previous
environment until staging and rollback checks succeed. There is no required
raster conversion or catalog migration for a supported current format.
