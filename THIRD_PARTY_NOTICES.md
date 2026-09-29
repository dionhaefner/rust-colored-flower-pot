# Third-party notices for packaged artifacts

Linux bundles extract their GDAL/PROJ/codec runtime and data from hash-locked
conda-forge archives downloaded directly. Package versions, source URLs,
license identifiers, and
available license texts are retained under `licenses/conda/`. The bundle also
retains this project's upstream MIT notice as
`licenses/TERRACOTTA-LICENSE.txt`. Platform repair may add further native
libraries; audit their notices in each final artifact.

The copied runtime contains GDAL, PROJ, GEOS, libcurl, OpenSSL and raster codec
libraries. Their upstream projects and license families include:

- GDAL: MIT/X-style license, with optional format drivers retaining their own
  compatible notices.
- PROJ: MIT license.
- GEOS: LGPL-2.1-or-later.
- OpenSSL: OpenSSL/Apache-2.0 terms depending on bundled version.
- libcurl: curl license.

The bundle contains artifact-specific dependency-license inventories under
`licenses/cargo/` and `licenses/conda/`.

RCFP embeds Terracotta 0.10.1 palette NPY files, generated API specification,
rendered documentation template, favicon and ReDoc JavaScript. The project's
upstream MIT license covers Terracotta assets; ReDoc's bundled notices are
retained verbatim in `src/assets/static/redoc.standalone.js.LICENSE.txt` and in
packaged licenses. The Rust color transforms implement the mathematical formulas
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

SQL and S3 support uses the Rust mysql/postgres clients, native-tls with
vendored OpenSSL, the AWS Rust credential provider chain and rust-s3. Distributed
bundles include `licenses/cargo/manifest.json` and dependency license/notice files,
including `openssl-src-*/OPENSSL-LICENSE.txt`. Versions and checksums are pinned
in Cargo.lock; no Python dependency is required by the native metadata engines.

## Browser client

RCFP embeds Terracotta browser JavaScript, CSS, and image assets in `src/assets/client` under the upstream MIT license
(retained in LICENSE). The browser JavaScript's upstream notice is retained in
`packaging/licenses/CLIENT-LICENSE.txt`; source maps are omitted.
