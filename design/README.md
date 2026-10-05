# Maintainer context

This directory explains the current design, compatibility constraints, and
validation boundaries needed to maintain RCFP. It is developer context and is
excluded from source archives, wheels, and container build contexts.

| Document                                      | Purpose                                                                                                                |
| --------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| [Decisions](decisions.md)                     | Current architectural choices, rationale, and conditions for changing them                                             |
| [Compatibility notes](compatibility-notes.md) | Preserved quirks, narrow accepted differences, and native safety constraints                                           |
| [Maintenance](maintenance.md)                 | Source map, reference discipline, documentation, build and release practices                                           |
| [Validation handoff](human-validation.md)     | Completed local evidence, raster correctness results, retained scope decisions, and remaining qualification procedures |
| [Performance knobs](performance.md)           | Implemented/discarded native optimizations and measured single-VM SQLite navigation from local storage and S3          |

Public behavior and procedures belong in [product documentation](../docs/README.md).
Test entry points and fixture provenance belong in [tests](../tests/README.md),
including the [pinned reference checks](../tests/reference/README.md). Update those
documents together with the relevant design notes when a contract changes.

Maintain current-design documents as explanations of the implementation that exists.
Keep proposed directions explicitly identified and distinguish measurements from expectations.
Replace superseded choices rather than accumulating an implementation diary.
Keep experiments and generated check reports in ignored storage or CI/release
artifacts. Put repeatable assertions and pinned reference expectations in `tests/`;
keep consequential design rationale and validation limits here. Establish support
claims with fresh results for the exact source and artifacts being qualified.
