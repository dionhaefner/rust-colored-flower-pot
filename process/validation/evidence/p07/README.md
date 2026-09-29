# P07 integration evidence — 2026-09-24

P07 is complete within the user's revised platform scope (D-010): prepare GitHub
Actions and continue locally; run other architectures/platforms later. This
directory does **not** qualify unrun platforms, AWS Lambda service invocation,
or real S3. [Manifest](run-manifest.json) identifies the final source/artifacts,
frozen oracle and dependency lock. [Operations](../../../../packaging/OPERATIONS.md)
documents installation, settings, contexts, resource ownership and profiling.

## Results

| Check | Actual result | Evidence |
| --- | --- | --- |
| Final native unit tests / formatting / warning-denied Clippy | 19 passed; fmt/Clippy passed | `unit-final.log`, `fmt.log`, `clippy-final.log` |
| Final full HTTP differential matrix | 526/526 passed | [http.json](http.json), `http-final.log` |
| Final packaged Python/WSGI/Lambda-event adapters | 46/46 passed | [adapters.json](adapters.json) |
| Unchanged applicable Python suite on final package | 526 passed, 2 failed; no skips; six remote in-process mock tests explicitly excluded | `upstream-final.xml`, `upstream-final.log`; classification below |
| Native-backed SQLite/MySQL/PostgreSQL public contexts and migrations | 121/121 passed, no skips, including unchanged invalid remote URL case | `contexts-fixed.xml` |
| Real-corpus public Python, arrays, metadata, futures, nested SQLAlchemy contexts, settings isolation, persistent caches, WSGI, profiler and local X-Ray | 27/27 passed | [integration.json](integration.json), `integration-final-package.log` |
| All settings/defaults, env/config/CLI precedence, deprecated aliases, process/validation/coercion | 46/46 native/oracle comparisons | [settings.json](settings.json), `settings-final2.log` |
| Public remote driver via socket S3 emulator | 13/13 passed | [remote.json](remote.json), `remote-final.log` |
| Application lifetime and real TCP client disconnect | 20 owner create/render/drop cycles; threads 1→1, descriptors 4→5; disconnected native job drained; next request 200 | [resources.json](resources.json), `resources-final.log` |
| Final overload/deadline/drain/error recovery | passed; observed 503/504; bounded descriptors and healthy recovery | [failure.json](failure.json), `failure-final.log` |
| Real Chromium client exploration | passed: singleband/RGB tiles, previews and metadata | [browser report](browser/report.json), [singleband](browser/singleband.png), [RGB](browser/rgb.png) |
| Fresh CPython 3.12 wheel install | native ingest, exact golden image, future uint16 array, WSGI and Flask pass | [install.json](install.json), `wheel-install.log`, `final-wheel-install.log`, [installed dependencies](installed-requirements.txt) |
| Final Debian standalone and official AL2023 zip | exact decoded real-raster RGBA; glibc ceiling 2.34 | [container.json](container.json), [lambda-runtime.json](lambda-runtime.json), `glibc.txt` |
| Authored CI platform-wheel staging path on local Linux | built, installed with external GDAL, smoke passed | [ci-platform.json](ci-platform.json), `ci-stage-final.log`, `ci-platform-wheel.log` |
| Authored CI Lambda Docker build on local x86_64 | built final source; extracted bundle and zip render exactly in official AL2023 | [ci-lambda.json](ci-lambda.json), `ci-lambda-final-build.log` |
| GitHub Actions workflow validation | actionlint 1.7.12 passed | `actionlint-final.log`, `actionlint-version.log` |

Counts overlap; do not add the rows into a unique-test total. Empty fmt/lint logs
mean successful silent checks, not omitted checks. The initial wheel smoke had
an incorrect float32 assertion; the frozen oracle returns uint16. The corrected
test requires uint16 and retains its original failure log.

## Failure classification and compatibility findings

The first full unchanged Python run had 526 passes and eight failures
(`upstream-full.xml`). Two ANSI-capture failures are identical in the frozen
oracle (`oracle-cli.xml`, RW-036). Log messages and level filtering work; neither
failure is relabeled a pass. The final applicable suite retains these two
failures. They do not represent a replacement-specific regression.

Six remote tests use boto's in-process `moto.mock_aws` and private Python cache
fields. Rust's client cannot be intercepted by that mock. Inspection exposed
two actual defects (late invalid URL/missing-object errors), which are repaired.
The invalid URL test passes unchanged; missing-object behavior and the remaining
public contracts are validated through moto's **real socket server** with fake
credentials. The remote test file is explicitly excluded from the final upstream
run; no excluded or mocked case is reported as passing. The 13 replacement
assertions cover eager errors, read-only operations, public metadata/connection
types, TTL, context generation pinning, retirement and destructor cleanup.
This still does not qualify AWS service credentials (RW-037).

Other repaired port regressions: direct whole-raster bounds, computed binary64
percentiles versus stored float32 lists (RW-035), settings coercion/writable-parent
validation (RW-038), SVG MIME for the retained browser, SQL connection failure
visibility and public nested-context return identity. Intermediate failing logs
remain for chronology; final reports above supersede them.

Profiler compatibility is explicit: maintained Werkzeug middleware replaces its
removed legacy import, while native profiling emits request/resource timings
(RW-032, D-009). Futures preserve values/types but use spawn (RW-034). Public
method commits retain the reference's per-operation behavior (RW-033).

Chromium 140.0.7339.16 used the unchanged compiled client. Its external Mapbox
basemap request was replaced with a plain offline background; raster/metadata
requests went to the real native server and real Sentinel pixels. Screenshots
were visually inspected. No external basemap/token workflow is claimed.

## Reproduction

Use the frozen source/environment and corpus established in P00, not the sibling
repository's working tree. Sockets and Docker must be permitted for these local
checks. SQL credentials below are disposable test inputs.

```sh
packaging/cargo.sh build --locked --offline
packaging/cargo.sh test --locked --offline
packaging/cargo.sh clippy --locked --offline --all-targets -- -D warnings
packaging/cargo.sh fmt --check

TERRACOTTA_REUSE_TARGET=1 process/validation/tools/build-lambda-target.sh process/validation/artifacts/p07-native-target
TERRACOTTA_NATIVE_TARGET=process/validation/artifacts/p07-native-target packaging/build-bundle.sh process/validation/artifacts/NEW-bundle
packaging/build-python.sh process/validation/artifacts/NEW-bundle process/validation/artifacts/NEW-python
packaging/build-wheel.sh process/validation/artifacts/NEW-bundle process/validation/artifacts/NEW-wheels
packaging/build-lambda.sh process/validation/artifacts/NEW-bundle process/validation/artifacts/NEW-lambda

process/validation/tools/p06-services.sh start
PYTHONPATH=process/validation/artifacts/NEW-python .venv-reference/bin/python -m pytest .cache/reference/tests \
  --ignore=.cache/reference/tests/drivers/test_sqlite_remote.py \
  --mysql-server root:p06-disposable@127.0.0.1:53316 \
  --postgresql-server postgres:p06-disposable@127.0.0.1:55436
process/validation/tools/p06-services.sh stop

PYTHONPATH=process/validation/artifacts/NEW-python .venv-reference/bin/python process/validation/tools/p07_validate.py
PYTHONPATH=process/validation/artifacts/NEW-python .venv-reference/bin/python process/validation/tools/p07_remote.py
PYTHONPATH=process/validation/artifacts/NEW-python .venv-reference/bin/python process/validation/tools/p07_resources.py
PYTHONPATH=.cache/reference .venv-reference/bin/python process/validation/tools/p07_settings.py
TC_P04_BINARY=process/validation/artifacts/NEW-bundle/bin/terracotta .venv-reference/bin/python process/validation/tools/p04_validate.py --out process/validation/artifacts/NEW-http
.venv-reference/bin/python process/validation/tools/p04_adapters.py --bundle process/validation/artifacts/NEW-bundle --package process/validation/artifacts/NEW-python --out process/validation/artifacts/NEW-adapters
.venv-reference/bin/python process/validation/tools/p03_container.py --bundle process/validation/artifacts/NEW-bundle --out process/validation/artifacts/NEW-container --tag terracotta-p07-recheck:local
.venv-reference/bin/python process/validation/tools/p03_lambda_runtime.py --bundle process/validation/artifacts/NEW-bundle --lambda-zip process/validation/artifacts/NEW-lambda/terracotta-lambda.zip --out process/validation/artifacts/NEW-runtime
```

The integration/remote/resource/settings runners write their named `p07-*`
reports; copy evidence before rerunning if preserving a previous chronology.
For browser reproduction, install Playwright in an isolated directory, install
its Chromium, set `PYTHONPATH` to that directory and `PLAYWRIGHT_BROWSERS_PATH` to
the browser cache, then run `process/validation/tools/p07_browser.py` (uses the debug
binary). The browser script owns and stops its two local services.

CI reproduction is encoded directly in
[platforms.yml](../../../../.github/workflows/platforms.yml). Its final Lambda
Docker build and platform-wheel staging were exercised locally; the workflow
was linted, not dispatched or pushed. The user explicitly deferred macOS,
Windows, Linux arm64 and Lambda arm64 execution. Keep those C32 checks pending
until uploaded CI reports establish success.

## Retained state

Final deliverables are `process/validation/artifacts/p07-final`, `p07-final-python`,
`p07-final-wheels`, and `p07-final-lambda/terracotta-lambda.zip`. Older `p07-release`
artifacts predate the last connection/settings fixes. The portable target remains
at `p07-native-target`. The clean installed wheel environment is `p07-install`.
Local Docker images and build/browser caches remain reusable. Disposable SQL
containers/volumes were removed (`services-cleanup.log`); test-owned HTTP/range/
browser services exit through cleanup handlers. No phase daemon is intentionally
left running, and no commits, pushes, publishing or cloud deployment occurred.
