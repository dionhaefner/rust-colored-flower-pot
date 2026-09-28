# Third-party notices for packaged artifacts

The Linux P03 bundle copies the native GDAL/PROJ/codec runtime and data shipped
by the pinned Rasterio 1.4.3 manylinux wheel recorded in
`validation/reference/environment.json`. Its supplied `LICENSE.txt` is retained
as `licenses/RASTERIO-LICENSE.txt` in every bundle. The bundle also retains this
project's upstream MIT notice as `licenses/TERRACOTTA-LICENSE.txt`.

New wheel builds use Rasterio 1.4.4; its license is copied from the wheel's
`.dist-info/licenses` directory. Platform repair may add more native libraries,
whose licenses require a final artifact-specific audit before publication.

The copied runtime contains GDAL, PROJ, GEOS, libcurl, OpenSSL and raster codec
libraries. Their upstream projects and license families are documented in the
Rasterio binary-distribution project and GDAL dependency license inventory:

- Rasterio / rasterio-wheels: BSD-3-Clause and binary build provenance.
- GDAL: MIT/X-style license, with optional format drivers retaining their own
  compatible notices.
- PROJ: MIT license.
- GEOS: LGPL-2.1-or-later.
- OpenSSL: OpenSSL/Apache-2.0 terms depending on bundled version.
- libcurl: curl license.

This notice records the provenance of the feasibility artifacts; a release
candidate must regenerate and audit the complete dependency-license inventory
for its final platform-specific build (P08).

P04 embeds the frozen Terracotta palette NPY files, generated API specification,
rendered documentation template, favicon and ReDoc JavaScript. The project's
upstream MIT license covers Terracotta assets; ReDoc's bundled notices are
retained verbatim in `src/assets/static/redoc.standalone.js.LICENSE.txt` and in
packaged licenses. The Rust color transforms port the mathematical formulas
from color-operations 0.4.0 (Copyright 2015 Mapbox, MIT); its complete notice is
retained in `src/assets/COLOR_OPERATIONS_LICENSE.txt` and packaged licenses.

## Crick statistics and merging t-digest

`src/statistics.rs` is a Rust adaptation of Crick 0.0.8's merging t-digest and
second-moment recurrence. It retains compression 100, the 42-value input buffer,
stable sorting/merge order, and quantile interpolation. Crick is BSD-3-Clause,
copyright Jim Crist-Harif (2016) and Dask Developers (2023); the originating
t-digest algorithm implementation is Apache-2.0. See `src/assets/CRICK_LICENSE.txt`
and `src/assets/TDIGEST_LICENSE.txt`, also copied into distributions.
Upstream source: https://github.com/dask/crick/tree/0.0.8 .

P06 SQL and S3 support uses the Rust mysql/postgres clients, native-tls with
vendored OpenSSL, the AWS Rust credential provider chain and rust-s3. Distributed
bundles include `licenses/cargo/manifest.json` and dependency license/notice files,
including `openssl-src-*/OPENSSL-LICENSE.txt`. Versions and checksums are pinned
in Cargo.lock; no Python dependency is required by the native metadata engines.

## P07 Python compatibility boundary and browser client

`python/terracotta` reuses settings/schema, exception, NumPy convenience,
Flask/Click boundary, documentation and browser assets from Terracotta commit
`6db38d5873cde5ebabe47338a1f61ddc788eada6` under its MIT license (retained in
LICENSE). Driver storage and raster/preparation operations are adapted to the
shared Rust core. The browser JavaScript includes its upstream license file;
source maps and the frontend development tree are omitted. No frontend redesign.
