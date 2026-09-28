# Rust-colored flower pot (RCFP)

RCFP is a Rust implementation of the Terracotta API. The objective is full
feature coverage and practical drop-in compatibility, with the visual-fidelity
policy in [REWRITE.md](REWRITE.md).

P00 through P07 are complete within the recorded local scope. The repository contains a frozen Python oracle, a
real-raster corpus, retained reference captures, an executable differential
comparator, and a native Rust server that reads Python-created SQLite databases
and serves verified bounded local/remote singleband, RGB and compute rendering,
all built-in/custom palettes, color transforms, batch metadata, discovery, CORS
and API documentation. Native metadata, transactional SQLite writes, ingestion,
lazy pattern serving and COG optimization are verified. Native MySQL/PostgreSQL
read/write stores, safe S3 SQLite snapshot refresh and historical migrations
are implemented and locally qualified; real S3 remains unverified. It also has
a relocatable Linux bundle, clean container, Lambda custom-runtime artifact,
historical legacy Python/Flask/WSGI/CLI integration evidence, an unchanged
browser client, and an installable RCFP wheel. The current RCFP Python package
exposes native request, data-store and WSGI APIs; the original Terracotta
driver/Flask interface has not been ported under the RCFP namespace. Platform CI is prepared; nonlocal results are pending.
P08 adds release qualification, shutdown and dateline-preview repairs, physical
BigTIFF/antimeridian checks, measured performance and a tested return to Python.
See the [candidate release notes](packaging/RELEASE_NOTES.md) and
[P08 evidence](validation/evidence/p08/README.md). Actual AWS services, deferred
platform runs and external basemap integration remain release gates.
See [operator instructions](packaging/OPERATIONS.md) for installation/configuration. See
[validation/README.md](validation/README.md) for exact commands and
[execution/STATUS.md](execution/STATUS.md) for the current handoff.

## Install from source and try the CLI

The repository is a Python source package. On the locally qualified Linux x86_64
setup, use CPython 3.12, the pinned Rust 1.85.1 toolchain (`rustup` reads
`rust-toolchain.toml`), a C compiler, and `pip`. The build downloads Python and
Cargo dependencies on first use. Rasterio supplies the bundled GDAL/PROJ runtime;
the resulting wheel is specific to the building Python ABI and platform. The
previously qualified P08 Linux artifact targets glibc 2.34 or newer; a
locally built wheel inherits the build host's native-library requirements.

From this repository's root, in a fresh environment:

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install .
rcfp --help
python -c "import rcfp; print(rcfp.__version__)"
```

That single `pip install .` builds the Rust executable and shared core, compiles
the CPython extension, packages the native runtime and browser assets, installs
the pinned Rasterio dependency, and creates the `rcfp` console command. The installed distribution and import
package are both `rcfp`. The CLI launches the bundled Rust executable; Python
exposes native request, data-store, and WSGI APIs. The original Terracotta
Python source in `python/terracotta` remains unchanged for comparison and is
excluded from the wheel. RCFP lives in `python/rcfp`; the obsolete `python/terracotta_rust` adapter has been
removed.

For a first interactive check against a catalog you already have:

```sh
rcfp serve -d /absolute/path/catalog.sqlite
# In another terminal: curl http://127.0.0.1:5000/keys
```

To build a reusable wheel and install that exact file in another compatible
CPython 3.12 environment:

```sh
python -m pip wheel --no-deps . --wheel-dir dist
python -m pip install dist/rcfp-0.1.0-*.whl
rcfp --version
```

The source build uses the existing Linux standalone bundle path. Set
`TERRACOTTA_CARGO_OFFLINE=1` only when the Cargo registry is already cached.
For an offline local build with Python build requirements already installed, add
`--no-build-isolation` to `pip wheel` or `pip install`. macOS and Windows builds
use the existing platform stage and require GDAL/PROJ installed in the build and
run environment; their CI qualification is still pending. A wheel is installed
with one `pip install` command, but it is not yet published to an index. See
[operator instructions](packaging/OPERATIONS.md) for configuration and the
existing prebuilt artifact flow.

## Resume qualification

Open this directory as the agent's workspace. Give it the following assignment:

> Resume P08 from execution/STATUS.md and execution/handoffs/P08.md, following
> AGENTS.md and execution/prompts/IMPLEMENT.md. Inspect completed local evidence
> first. Resolve the explicitly assigned remaining release gates with the
> supplied platform/account authorization; preserve passing work and do not
> convert unrun external checks into release claims.

The prompt file also supports bounded tasks within a phase. To coordinate several explicitly assigned
phases, use [COORDINATE.md](execution/prompts/COORDINATE.md). To review a completed
slice, use [REVIEW.md](execution/prompts/REVIEW.md).

## Context map

| Document | Purpose |
| --- | --- |
| [AGENTS.md](AGENTS.md) | Agent entry point and required reading. |
| [REWRITE.md](REWRITE.md) | Objectives, compatibility policy, trade-offs, and pitfalls. |
| [REWRITE_LOG.md](REWRITE_LOG.md) | Legacy findings, accepted deviations, and later opportunities. |
| [WORKFLOW.md](execution/WORKFLOW.md) | Planning, execution, clarification, blockers, and handoffs. |
| [PHASES.md](execution/PHASES.md) | Ordered delivery phases with concrete outputs and exit evidence. |
| [VALIDATION.md](execution/VALIDATION.md) | Reference capture, comparison rules, and validation discipline. |
| [DATASETS.md](execution/DATASETS.md) | Small real-world raster corpus, subset recipes, visual examples, and large-file stress tier. |
| [STATUS.md](execution/STATUS.md) | Current progress, ownership, blockers, and next action. |
| [COVERAGE.md](execution/COVERAGE.md) | Feature inventory and implementation/evidence status. |
| [DECISIONS.md](execution/DECISIONS.md) | Architectural choices and reasons to revisit them. |
| [TEMPLATES.md](execution/TEMPLATES.md) | Brief, plan, evidence, blocker, and handoff formats. |

## Reference repository

The initial local reference is `../terracotta`, with source baseline
`6db38d5873cde5ebabe47338a1f61ddc788eada6`. P00 confirmed and exported that exact
commit without reading the checkout's modified working tree. Recreate the
isolated snapshot and pinned environment with
`validation/tools/bootstrap-reference.sh`; do not modify or clean the sibling.

Existing discovery-log source links use this sibling checkout layout. They aid
navigation but are not immutable reference evidence. P00 must associate captures
with the full source revision and dependency manifest. If the repositories move,
make the reference path configurable and update navigation links without changing
the baseline implicitly.

Actual P00 commands and evidence are documented under `validation/`. Do not infer
that later implementation or deployment passed from its presence in a plan.
