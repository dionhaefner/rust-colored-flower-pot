# CI/GDAL wheel packaging, local Linux check

Date: 2026-09-28. Platform: Linux x86_64, CPython 3.12. No hosted
platform job or PyPI upload was run. This record is for the current RCFP
package boundary; older P08 package archives have different contents.

## Performed

- Selected Rasterio 1.4.4, whose Linux wheel reports GDAL 3.10.3, for the
  pinned build input and private GDAL/PROJ payload. A local native smoke using
  that Rasterio version passed the retained Sentinel preview golden.
- Built the RCFP native bundle and CPython 3.12 wheel. Explicit wheel metadata
  reports `Requires-Python: >=3.12,<3.13` and no runtime dependencies.
  An earlier iteration declared `Requires-Dist: rasterio==1.4.4`; that
  duplicated the vendored native runtime and has been removed.
- Installed a locally built wheel into a fresh venv with pip, without conda GDAL
  or a repository Python package on its import path. The expanded
  `p07_platform_smoke.py` passed exact decoded Sentinel preview pixels,
  SQLite create/insert, discovery, metadata, RGB, compute, invalid-query,
  CORS, WSGI and `rcfp --help` checks. This used a real Sentinel raster crop
  under three catalog band names for the RGB/compute paths.
- `auditwheel repair` initially failed to locate Rasterio's nested
  `libnghttp2-168869a9.so.14.28.4` despite its presence in the staged runtime.
  Setting `LD_LIBRARY_PATH` to the bundle's `lib` made repair succeed; this is
  now part of the Linux build image, which also pins the required `patchelf`
  utility. The repaired manylinux wheel passed
  `verify-wheel.py` including RECORD hashes and the same installed-wheel smoke.
  The local host produced a `manylinux_2_39_x86_64` tag; the AL2023 builder
  has a different glibc baseline and still requires a hosted run.
- `MANIFEST.in` was narrowed after the first sdist unexpectedly included
  retained Terracotta source and validation evidence. The corrected archive
  includes Cargo targets, build scripts, the client license and RCFP source;
  it excludes those retained reference/evidence trees. Building a wheel from
  the corrected unpacked source archive succeeded both without and with PEP 517
  build isolation. The isolated build used Rasterio 1.4.4/GDAL 3.10.3. That
  wheel was repaired, verified and installed with pip in a second fresh venv;
  all eight smoke groups passed, including a live `rcfp serve` HTTP render.
- `twine check --strict` first found missing PyPI description metadata. After
  staging the README and declaring Markdown content type, the rebuilt sdist
  and final repaired wheel both passed strict metadata checks. The exact
  rebuilt repaired wheel passed the eight-group smoke in a fresh pip venv.
- A release-verifier negative test with a Linux wheel renamed as macOS/Windows
  correctly failed on the filename versus embedded WHEEL tag mismatch.
- Python syntax compilation, YAML parsing and `git diff --check` passed.
  [Wheel verification](wheel-verification.json), [installed-wheel report](repaired-wheel-smoke.json),
  [dependency-free runtime report](no-rasterio-runtime.json), and
  [source/archive manifest](build-verification.json) retain the results and hashes.
- The final repaired wheel was installed into a new venv without Rasterio.
  After the real-raster smoke ran with test-only Rasterio/NumPy/Pillow, those
  packages were uninstalled. RCFP still returned catalog JSON and rendered
  a 256×256 PNG through its bundled GDAL, and its CLI loaded successfully.

## Limits

Local Docker is unavailable, so the AL2023 `auditwheel` step is validated here
with a local Linux wheel and the same repair command, not the exact hosted
container. macOS, Windows, Linux arm64, PyPI trusted publishing, real AWS,
and the full frozen-oracle/SQL/stress/browser CI matrix remain unrun. The old
P08 evidence remains historical; this compact package smoke does not replace
its broader compatibility claims.
