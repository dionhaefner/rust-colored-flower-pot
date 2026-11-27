# Rust-colored flower pot

RCFP serves raster imagery from your existing GeoTIFFs and catalogs. It offers a
native tile server, a browser client, Python and WSGI interfaces, and a named
Terracotta 0.10.1 compatibility profile. Catalogs describe your data. The original
raster files stay where you put them.

- [Create your first raster map](tutorials/first-map.md) with the bundled imagery.
- <a href="explore/index.html">Open the explorer</a> and connect to an RCFP or Terracotta server.
- <a href="demo/index.html">Browse the live demo</a> for Sentinel-2 imagery and terrain.
- [Upgrade from Terracotta](how-to/upgrade-terracotta.md) with a migration and rollback plan.
- [Read why RCFP was built](explanation/why-rcfp.md) for deployment and performance tradeoffs.

Matching wheels bundle the native executable and GDAL/PROJ runtime. Native
serving and Python integrations share the Rust core. Explicit worker, cache, and
queue settings help you operate the service under load. Read the
[support matrix](reference/support.md) before choosing a platform or deployment.

```{toctree}
:maxdepth: 2
:caption: Documentation

tutorials/index
how-to/index
reference/index
explanation/index
```

```{toctree}
:maxdepth: 1
:caption: Project

project
```
