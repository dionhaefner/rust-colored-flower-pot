# Deployment artifact requirements

## Python wheel

The distribution contains `rcfp`, its limited-API CPython extension, native
executable/shared core, browser assets, GDAL/PROJ runtime/data, and applicable
licenses. It installs the `rcfp` console entry. Wheels are platform-specific.
`abi3` covers CPython 3.12–3.14 on that platform. Use the repaired wheel's tag for
Linux glibc requirements. A local source wheel may require newer system libraries
than a different release artifact.

Both CLI profiles dispatch to the bundled Rust executable. The Python API uses
small native bindings and declares NumPy for its masked-array return type. Flask,
Click, Pillow, Python SQL adapters and Rasterio are not runtime requirements. See [installation](../how-to/install.md).

## Native bundle

Keep `bin`, `lib`, `share`, and `licenses` together. Copying only `bin/rcfp` loses
its native dependencies and GDAL/PROJ data. The Linux standalone baseline is
bundle-specific. The established AL2023 bundle targets glibc 2.34 or newer.
Desktop/wheel artifacts and Lambda-targeted bundles may have different baselines.
`TERRACOTTA_RUNTIME_ROOT` can select a bundle. An external GDAL library needs its
own correctly installed dependencies/data.

## Container and Lambda

The container Dockerfile consumes `bundle/` and `fixture/` in its build context.
Stored paths need to resolve inside the deployed filesystem. The Lambda ZIP
contains executable `bootstrap` and `runtime/`, built for the function's runtime
OS and architecture. Do not substitute a Python wheel for that runtime ZIP.

## Source archive

The source archive includes the supported build inputs, tests, and documentation
with its examples and attributed data. Hatch VCS writes the package version file
for archive builds. Documentation build dependencies are separate from package
runtime dependencies. Generated HTML is not bundled in the wheel or source archive.
