# Process workspace

This directory contains the temporary process for the Rust rewrite: policy and
findings, execution plans and handoffs, frozen reference material, comparison
tools, and retained evidence. Commands in this directory run from the repository
root. Generated validation outputs remain ignored under `process/validation/artifacts`.

The distributable application and its enduring build and release files live at
the repository root in `src/`, `python/`, `packaging/`, and `.github/`. The source
archive and wheel exclude this directory. Product documentation begins in
[the root README](../README.md).

The enduring CI smoke, its pinned fixtures, and Rust unit tests now run from
`tests/` without this directory. When the remaining qualification records are
obsolete, delete `process/` as one unit, including this directory's `AGENTS.md`.

The current local candidate and release gates are recorded in
[the candidate notes](RELEASE_CANDIDATE.md).

## Resume qualification

Open the repository root as the agent's workspace. Give it the following assignment:

> Resume P08 from process/execution/STATUS.md and process/execution/handoffs/P08.md, following
> process/AGENTS.md and process/execution/prompts/IMPLEMENT.md. Inspect completed local evidence
> first. Resolve the explicitly assigned remaining release gates with the
> supplied platform/account authorization; preserve passing work and do not
> convert unrun external checks into release claims.

The prompt file also supports bounded tasks within a phase. To coordinate several explicitly assigned
phases, use [COORDINATE.md](execution/prompts/COORDINATE.md). To review a completed
slice, use [REVIEW.md](execution/prompts/REVIEW.md).

## Context map

| Document                                 | Purpose                                                                                      |
| ---------------------------------------- | -------------------------------------------------------------------------------------------- |
| [AGENTS.md](AGENTS.md)                   | Agent entry point and required reading.                                                      |
| [REWRITE.md](REWRITE.md)                 | Objectives, compatibility policy, trade-offs, and pitfalls.                                  |
| [REWRITE_LOG.md](REWRITE_LOG.md)         | Legacy findings, accepted deviations, and later opportunities.                               |
| [WORKFLOW.md](execution/WORKFLOW.md)     | Planning, execution, clarification, blockers, and handoffs.                                  |
| [PHASES.md](execution/PHASES.md)         | Ordered delivery phases with concrete outputs and exit evidence.                             |
| [VALIDATION.md](execution/VALIDATION.md) | Reference capture, comparison rules, and validation discipline.                              |
| [DATASETS.md](execution/DATASETS.md)     | Small real-world raster corpus, subset recipes, visual examples, and large-file stress tier. |
| [STATUS.md](execution/STATUS.md)         | Current progress, ownership, blockers, and next action.                                      |
| [COVERAGE.md](execution/COVERAGE.md)     | Feature inventory and implementation/evidence status.                                        |
| [DECISIONS.md](execution/DECISIONS.md)   | Architectural choices and reasons to revisit them.                                           |
| [Compatibility profile](execution/plans/compatibility-profile.md) | Proposed Terracotta trial contract, native adoption, and release gates.                    |
| [TEMPLATES.md](execution/TEMPLATES.md)   | Brief, plan, evidence, blocker, and handoff formats.                                         |

## Frozen Terracotta reference

`process/validation/tools/bootstrap-reference.sh` downloads the checksum-pinned
Terracotta 0.10.1 source release from PyPI into ignored `.cache/reference` and
installs its pinned dependencies in `.venv-reference`. The release was published
from commit `6db38d5873cde5ebabe47338a1f61ddc788eada6`, the P00 oracle
baseline. The script verifies the archive SHA-256 before extraction and refuses
to replace an existing snapshot. Set `TERRACOTTA_SOURCE_ARCHIVE` to a previously
downloaded `terracotta-0.10.1.tar.gz` for an offline bootstrap.

Historical source links in the discovery log still point to the upstream commit;
they are navigation aids, not a reason to keep a copy of Terracotta in RCFP.

Actual P00 commands and evidence are documented under `process/validation/`. Do not infer
that later implementation or deployment passed from its presence in a plan.
