# Maintenance practices

The [application validation plan](adoption.md) assigns ownership and review
cadence. Public expectations are in the [support policy](../docs/support-policy.md);
application evidence uses the [evaluation record](../docs/examples/application-evaluation.md).

## Source and supported commands

| Area                             | Entry points                                                                                                       |
| -------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| Native execution and adapters    | `src/execution.rs`, `src/http/`, `src/cli.rs`, `src/lambda.rs`, `src/ffi.rs`, `src/python.rs`                      |
| Raster and image semantics       | `src/raster/`, `src/image.rs`, `src/color.rs`, `src/expressions.rs`, `src/statistics.rs`                           |
| Catalogs and remote generations  | `src/storage.rs`, `src/storage/`, `python/rcfp/drivers/`                                                           |
| Python profiles and facade       | `python/rcfp/_profile.py`, `_bridge.py`, `drivers/`, `server/app.py`, `src/python/driver.rs`, `python/rcfp_cli.py` |
| Runtime packaging and versioning | `packaging/`, `pyproject.toml`, `rust-toolchain.toml`, `.github/workflows/`                                        |
| Product assertions and reference | [Test guide](../tests/README.md), [reference guide](../tests/reference/README.md)                                  |
| User/operator documentation      | [Documentation maintenance](../docs/contributing.md), [how-to guides](../docs/how-to/index.md)                     |

Use `packaging/cargo.sh` for the local cached toolchain when needed. Ordinary
`cargo test --locked` is offline-capable and needs no SQL services. The explicit
`storage-integration` feature requires configured disposable engines and sockets;
see the test guide. Rust fmt/Clippy and applicable pre-commit hooks are maintained
repository checks. Standalone Python runners are scripts, not pytest collections.

## Reference and evidence discipline

Use the SHA-256-pinned Terracotta source release and dependency lock from
`tests/reference/`. Keep it isolated from installed RCFP and the user's upstream
checkout. Acquire inputs explicitly, cache them for offline use, and retain source
URLs/checksums, native windows, CRS/transforms, values/masks, and attribution.
Large source rasters and derivatives stay outside Git. Golden outputs must come
from the oracle, never from the candidate being judged.

For a comparison, record source/artifact hashes, package and native versions,
OS/architecture, settings with credentials redacted, commands, return codes,
inputs, and exclusions. Use fresh output directories. Missing/skipped checks do
not pass, and a reference baseline pass does not prove candidate behavior.

Store generated reports and logs in ignored `build/` or `.cache/` directories;
CI uploads its run outputs as artifacts. Retain release qualification alongside
the exact release artifacts. Keep repeatable assertions and pinned oracle
expectations in maintained tests, and record rationale and unresolved validation
limits in design notes. Do not check generated pass/fail reports into the source
tree.

Reproduce a mismatch under equivalent dependency versions and settings. Check
HTTP statuses/JSON/header meaning and ordering; decoded imagery, dimensions,
coverage, masks, classes, seams and placement; Python types/exceptions/contexts;
and cross-language storage reads/writes. Inspect ordinary-scale views where
numerical deviations are proposed. A shape-only check or amplified difference
image cannot establish user-visible equivalence. Comparator changes need
controlled failures proving they detect the affected contract.

Preserve material failure reports with the run artifacts and explain accepted
differences in [compatibility notes](compatibility-notes.md) with a reason and
regression bound. Do not spend unbounded effort reproducing invisible rounding
or normalize away dataset identity. Unavailable cloud/platform checks remain
explicit tasks in the [validation handoff](human-validation.md).

## Documentation, packaging, and release

Keep tutorials, task guides, reference, and explanation focused on their reader
needs. Reference extraction runs against the installed artifact and includes its
Hatch VCS version. Static HTML rendering uses extracted snapshots without native
imports, cloud credentials, acquired references, or development context. Run
`docs/tools/generate_reference.py --check` and the strict HTML/link checks when
changing documented interfaces. Examples, actual browser behavior, and migration
rehearsals complement source/unit checks.

Keep current installation, backup, migration, deployment and recovery instructions
in `docs/`; do not maintain duplicate historical candidate/rollback procedures.
Update both the Terracotta trial and native-difference guides with contract changes.
Artifact tests must install the exact repaired wheel, check contents/RECORD,
metadata and entry points, and record its hash. A source-archive wheel must work
without developer context. Identical archive hashes from identical inputs prove
packaging repeatability, not compiler reproducibility.

Tag releases require the native, wheel, correctness, documentation, and sdist
gates; publishing uses the protected PyPI environment and trusted publisher. A
tag must be on main with matching package metadata. Hosted workflow configuration,
local Lambda events/runtime images, socket emulators, and offline basemaps each
have limited scope. They do not substitute for the current platform matrix or
actual cloud/credential/trace/basemap checks. Publishing and deployment still
follow session authorization.
