# Rust-colored flower pot

Documented artifact: **{{ version }}**. Use the docs matching your installed release.

RCFP serves raster imagery from your existing GeoTIFFs and catalogs. It offers a
native tile server, a browser client, Python and WSGI interfaces, and a named
Terracotta 0.10.1 compatibility profile. Catalogs describe your data. The original
raster files stay where you put them.

- **[Get started](tutorials/first-map.md)**: create a map from supplied real imagery.
- **<a href="demo/index.html">Live demo</a>**: explore the supplied Sentinel-2 imagery in your browser.
- **[Upgrade from Terracotta](how-to/upgrade-terracotta.md)**: choose a migration
  path, compare behavior, and rehearse rollback.
- **[Why RCFP?](explanation/why-rcfp.md)**: understand deployment benefits,
  performance opportunities, and tradeoffs.

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

contributing
maintaining
support-policy
releases
notices
```
