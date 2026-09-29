# Release smoke fixtures

`platform_smoke.py` exercises the native binary and installed wheel against a
real Sentinel-2 red-band crop. `fixtures/platform-smoke.json` pins the source URL,
byte count, SHA-256, native pixel window, CRS, transform, decoded pixel SHA-256,
and attribution. The parent TIFF is downloaded only if the ignored local crop is
missing. Every run verifies the crop's decoded pixels and geometry.

`fixtures/lagoon-red-preview.png` is a Terracotta 0.10.1 rendering of that
crop at the smoke's explicit stretch. The smoke checks decoded RGBA pixels, not
PNG compression bytes. It also exercises installed RCFP discovery, metadata,
RGB, compute, invalid-query, CORS, WSGI, CLI and HTTP paths. The native lane
covers catalog creation, insertion and Lambda event rendering.

`lambda_runtime_smoke.py` uses the SQLite catalog created by the native smoke
and verifies the packaged Lambda runtime in the official local image. It
requires Docker and a built bundle and zip; a Python syntax pass is not a
runtime-image pass. The Rust expression vectors in `fixtures/` are retained
contract examples used by the unit suite. Rust storage tests create disposable
SQLite databases rather than depending on a preexisting database.


The optional `storage-integration` Cargo feature runs disposable engine tests.
Set `RCFP_TEST_MYSQL_URL` and `RCFP_TEST_POSTGRESQL_URL` to database server
URLs before enabling it; the tests create their own database names.
