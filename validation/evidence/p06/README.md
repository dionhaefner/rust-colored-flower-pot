# P06 — storage backends and migrations

Completed locally on 2026-09-24 against frozen Python commit
`6db38d5873cde5ebabe47338a1f61ddc788eada6`. The reference repository was read-only;
all SQL mutations used disposable databases and historical fixture copies.
[Run manifest](run-manifest.json), [plan](../../../execution/plans/P06.md),
[handoff](../../../execution/handoffs/P06.md), and
[reproduction commands](../../README.md#p06-metadata-backends-and-migrations).

## Contracts and results

| Surface | Retained evidence | Actual result |
| --- | --- | --- |
| Native SQLite/MySQL/PostgreSQL store facade; schemas, native float32 blobs, key descriptions, Unicode/quoted keys, both write directions, stale skip-metadata overwrite, engine collation/order/paging, atomic insert/delete/batches, lazy publication and HTTP | [Engines](engines.json), [portable run](portable-engine.log) | 123 assertions pass; actual MySQL 8.4.11 and PostgreSQL 16.10; six concurrent processes publish metadata once |
| URI/provider detection, normalization, credentials/redaction, SELECT-only accounts, stale connection recovery, delete rollback and CLI ingestion | [Paths](paths.json), [final run](release-paths.log) | 43 assertions pass on final portable executable |
| Historical migration chains and injected DDL failures | [Migrations](migrations.json), [engine/SQLite cases](engines.json), [original fixture](fixtures/db-v075.sqlite) | 19 engine migration assertions plus SQLite chains/CLI cases within the 123 above; exact oracle schema/data comparison; original fixture remains intact |
| PostgreSQL disable/prefer/require/verify-ca/verify-full, trusted/untrusted roots and host mismatch; preserved MySQL query failure | [TLS](tls.json), [run](portable-tls.log) | 8 assertions pass against real engines with disposable certificates |
| S3 SQLite read-only HTTP/writes, cached TTL, changed keys, concurrent refresh, invalid/missing downloads, retry, retained valid snapshot and exact local/remote image | [Remote](remote.json), [run](remote.log) | 18 assertions pass against a local Moto S3 service; 24 successful concurrent requests and 8 concurrent corrupt refresh errors |
| Generation leases/retirement, shared-owner single-flight download, private file cleanup, named profile/shared config, region/session signing and credential rotation; SQL unwind and nested savepoint recovery | [Rust integration](rust-integration.log), `tests/p06_remote.rs`, `tests/p06_sql.rs` | Both explicit integration tests pass; no integration test is silently skipped |
| Reopened relational singleband numeric/partial/percentile stretches, categorical, RGB default/explicit, compute and batch metadata | [Surfaces](surfaces.json), [original pairs](images/p06-surfaces/) | 14 assertions pass; decoded images exact; legacy MySQL Decimal failures explicitly expected |
| Packaged in-process SQL preparation, Python→oracle, rollback, render and delete | [Package](package.json), [final run](release-python.log) | 10 assertions pass on final package |
| Existing HTTP/rendering | [HTTP regression](http-regression.json), [run](portable-http.log) | 526/526 pass on portable build |
| Existing SQLite/preparation workflows | [SQLite](sqlite-regression.json), [workflows](workflows.json) | 9 database and 13 packaged workflow assertions pass |
| Final CPython/WSGI/Lambda-event adapters | [Adapters](adapters.json), [run](release-adapters.log) | 46/46 pass |
| Final clean Debian container and pinned official AL2023 runtime image | [Container](container.json), [Lambda runtime](lambda-runtime.json), [build](target-final.log) | Both load and render exact decoded real-raster RGBA; maximum required glibc 2.34 |
| Rust static/unit checks and frozen upstream tests | [Final units](unit-final.log), [Clippy](clippy.log), [oracle](upstream.log), [JUnit](upstream.xml) | 19 unit tests pass; rustfmt and warning-denied Clippy pass; 69 unchanged upstream driver/path/remote/migration tests pass, zero skipped |

The 69 upstream tests qualify the frozen oracle, not Rust. Normal Rust tests
exclude the two socket integration targets unless `storage-integration` is
explicitly enabled; the integration log proves they were actually run.

Final artifacts are `validation/artifacts/p06-release`, `p06-release-python`, and
`p06-release-lambda/terracotta-lambda.zip`. Their native code includes the final
URI scheme-case/IPv6 fixes. The final unit/path/package/adapter/container/runtime
checks postdate those fixes. Broader engine/TLS/HTTP/surface and Rust integration
checks used the preceding implementation, differing only in those URI fixes;
they were not needlessly repeated. The 13 preparation workflow checks used the
host build before the portable rebuild; final portable adapters and SQL preparation
were checked separately. Hashes identify both the broad HTTP binary and final
artifacts. No skipped environment is counted as passing.

## Compatibility decisions and repaired failures

D-008 records the shared storage design. Relational filtering, sorting and
pagination execute together on the engine, preserving collation and tied-row
selection (RW-026/RW-029). Bound values and quoted identifiers preserve arbitrary
valid keys. Connections are health-checked before operations; failed mutations
are not automatically replayed. Row locks coordinate lazy metadata publication.

RW-028 preserves reopened MySQL ten-decimal HTTP metadata conversion and the
oracle's Decimal/NumPy default-stretch failure. Complete numeric/percentile
stretches and compute work. [Engine pairs](images/p06/) and
[surface pairs](images/p06-surfaces/) retain decoded exactness; the ordinary-size
explicit MySQL RGB image was inspected. No visual tolerance was broadened.
Full Python Decimal/context return fidelity is P07.

RW-030 preserves the broken MySQL query suffix as an explicit error. PostgreSQL
TLS options are tested. RW-027 records that MySQL historical 0.7 SQL already fails
upstream and DDL commits cannot roll back the whole chain. Failed migrations do
not advance their format marker; restore a pre-migration copy before retrying a
partially changed MySQL schema. SQLite/PostgreSQL failed chains roll back.

RW-007 is addressed by immutable private snapshots, validation before publication
and leases: old connections retain old files. A failed refresh propagates its
error, preserves the valid snapshot and leaves TTL unchanged. The next request
retries. Native owner-drop cleanup is directly tested; killing a test service
with SIGTERM can leave its temporary cache files in ignored artifacts.

The first HTTP regression failed one path-sort case because Rust Path component
ordering differs from literal path-string ordering; the repaired comparator passes
all 526 cases. The first clean-container run failed on host glibc 2.38/2.39 symbols
([initial log](container.log)). RW-031 fixes packaging through the pinned AL2023
build, vendored OpenSSL build prerequisites and retained dependency licenses.
The final container/runtime checks close this regression. Earlier diagnostic logs
are retained and are not passing exit evidence.

## Provenance, boundaries and resumption

Raster pixels come from the existing P00 checksum-pinned corpus; no synthetic
image replaces real render evidence. The original `db-v075.sqlite` is copied
verbatim from the frozen reference and hashed in the manifest. Corrupt snapshots,
DDL triggers and disposable credentials are deliberate failure fixtures. SQL
engine image digests are pinned in `validation/tools/p06-services.sh`; public
TLS certificates are temporary test fixtures, not deployment credentials.

Real S3 is **not run**: no authorized bucket/account was supplied
([availability](external-availability.json)). Emulator tests establish local
protocol/signing/cache behavior, not AWS service compatibility. Environment,
session and rotating named-profile credentials are locally verified; role/SSO
service credentials remain external. This distinction is explicitly allowed by
P06's exit criteria. An actual deployed Lambda invocation, other architectures/
platforms, browser E2E and physical multi-GiB BigTIFF remain release gaps.

For reproduction, start only the pinned disposable SQL containers, wait for
readiness, create fresh TLS fixtures, and use the commands in the validation
README. `p06_validate.py` must precede `p06_surfaces.py`/`p06_package.py` because
they consume its generated database/image evidence. `TC_P06_BINARY` selects the
native artifact; `TC_P06_PACKAGE` selects the Python package. The handoff records
cleanup and the next phase. No production mutation, publishing or cloud deployment
was performed.
