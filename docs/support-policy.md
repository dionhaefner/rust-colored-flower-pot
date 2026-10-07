# Support and maintenance

RCFP is maintained by [Dion Häfner](https://github.com/dionhaefner). Support is
best effort through [repository issues](https://github.com/dionhaefner/rust-colored-flower-pot/issues).
There is no response-time agreement, on-call service, or long-term support branch.
The [support matrix](reference/support.md) describes interfaces and platform
qualification. A configured CI target does not establish support for an untested
release artifact.

## Report a problem

Include the package version, operating system and architecture, selected profile,
catalog format/provider, relevant settings, and a minimal reproduction. For a
rendering problem, include the CRS, source layout, nodata/mask/overview information,
request, and independently expected values or image. The
[diagnostics guide](how-to/diagnostics.md) helps collect these details. Remove
credentials, signed URLs, private source paths, and identifying application data
before posting. A public issue is unsuitable for confidential material.

For deployment or performance questions, record request patterns, concurrency,
worker/cache budgets, storage/network conditions, error counts, and artifact
hashes. The [application evaluation guide](how-to/evaluate.md) provides a reusable
record. Missing capabilities should describe the application and the obstacle;
that evidence helps prioritize work.

## Releases and compatibility

The current maintenance target is the latest published release and the main
branch. Fixes for older releases are considered individually; there is no promise
of backports. Development builds are for evaluation. Before upgrading, read the
release notes and matching documentation, retain the old artifact/configuration,
back up the catalog, and rehearse an upgrade and rollback in staging.

Release notes identify user-visible changes, supported catalog markers, deliberate
compatibility differences, dependency/runtime changes, and known qualification
limits. Breaking API or catalog changes require explicit migration and recovery
instructions. A profile switch does not perform a schema migration. The named
Terracotta profile stays pinned to its documented 0.10.1 contract; changing that
contract requires a separately named profile or an explicit compatibility decision.

A release requires passing native/platform, installed-wheel, reference
correctness, documentation, and source-archive checks for the exact tag and
artifacts. Unavailable checks remain unavailable. There is no fixed release date;
releases follow verified fixes and maintainer capacity. See
[release notes](releases.md) and [upgrade procedures](how-to/upgrade-terracotta.md).

## Dependency maintenance

The maintainer reviews dependency advisories and available updates monthly and
before a release. A reported security or correctness problem takes priority over
routine updates. This is a review cadence, not a remediation deadline. An update
must explain its need and retain the applicable regression, license, packaging,
and artifact results before release.

Rust updates preserve `Cargo.lock` and the supported toolchain. GDAL/PROJ updates
preserve checksum-pinned runtime acquisition and rerun geography, masks, categories,
numerical inspection, remote reads, and wheel repair checks. Python, JavaScript,
documentation, and benchmark environments keep their own dependency constraints
or locks. Update the independent reference environment only through an explicit
reference decision; do not silently move the oracle with the candidate.

## Contributing and continuity

Start with [contribution instructions](contributing.md) and the public
[raster corpus](explanation/raster-corpus.md). Useful contributions include small
reproductions, fixture provenance, documentation corrections, and focused fixes.
Include the checks actually run and separately list failures, skips, and exclusions.
Do not submit candidate renderings as reference goldens.

The repository owner currently handles triage, compatibility decisions, dependency
review, and releases. There is no designated backup maintainer. If capacity drops,
the owner should post a maintenance-status notice, stop making new support claims,
and leave existing artifacts, documentation, locks, and reproduction procedures
available. A successor needs repository and package-publisher access transferred
through the respective account controls, a release rehearsal, and the recorded
qualification evidence before publishing. Credentials do not belong in the repo.

## Funding and service options

There is currently no advertised paid support, sponsorship program, or managed
hosting service. Potential funding paths are sponsorship of general maintenance,
funded fixes with public regression tests, and separately agreed integration or
support work. None includes an implicit service-level agreement.

Before offering paid support, the owner needs to define the contact/contract
process, response scope, data handling, pricing, and backup coverage. Before
offering hosting, validate repeated demand from independent applications and
measure operating cost, isolation, credential handling, retention, recovery, and
staffing. Hosting remains a separate decision. Funding must not make an
unqualified platform or data format appear supported.
