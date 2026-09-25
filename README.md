# Rust-colored flower pot (RCFP)

Execution workspace for the Terracotta Rust rewrite. The objective is full
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
complete legacy Python/Flask/WSGI/CLI integration, an unchanged browser client,
and an installable wheel. Platform CI is prepared; nonlocal results are pending.
P08 adds release qualification, shutdown and dateline-preview repairs, physical
BigTIFF/antimeridian checks, measured performance and a tested return to Python.
See the [candidate release notes](packaging/RELEASE_NOTES.md) and
[P08 evidence](validation/evidence/p08/README.md). Actual AWS services, deferred
platform runs and external basemap integration remain release gates.
See [operator instructions](packaging/OPERATIONS.md) for installation/configuration. See
[validation/README.md](validation/README.md) for exact commands and
[execution/STATUS.md](execution/STATUS.md) for the current handoff.

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
