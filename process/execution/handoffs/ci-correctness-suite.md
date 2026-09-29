# CI correctness implementation handoff

The implementation follows [the plan](../plans/ci-correctness-suite.md). The
case inventory is [ci-case-map.json](../ci-case-map.json); portable case IDs are
also enforced by `tests/fixtures/contract-cases.json`. The current working branch
is `codex/ci-correctness`, with a draft PR. The latest pushed commit is
`7c128fd`; the Linux wheel builder, upstream baseline, large-raster/resource
checks, documentation and related workflow edits after that commit are local.
Do not publish or mark the PR ready until the hosted matrix for these edits
passes and PyPI trusted publishing is configured.

## Implemented lanes

- The portable installed-wheel suite has 53 required cases covering real raster
  fixtures, SQLite catalog operations, HTTP/Python/WSGI/CLI adapters, render
  pixels/masks, metadata, errors and package identity. It writes wheel SHA-256,
  fixture hash, JSON and JUnit results. It runs on five OS/architectures and
  CPython 3.12, 3.13 and 3.14. The separate loopback HTTP range test runs on
  each installed wheel.
- The pinned Terracotta 0.10.1 differential lane compares 26 HTTP/image cases
  in separate processes. The full upstream reference test baseline is checked
  independently against disposable MySQL/PostgreSQL services: 526 expected
  passes, two classified ANSI failures, six explicitly excluded in-process
  remote-SQLite mocks. This checks the reference, not candidate parity.
- Native SQL and S3 tests, live SQLite/MySQL/PostgreSQL catalog and HTTP
  contracts, and a Chromium client workflow run on PRs and tags. The browser
  test uses an offline basemap and retains image/metadata/report artifacts.
- Scheduled, manual and tag qualification writes a 5.29 GB uint32 BigTIFF from
  full, checksum-pinned WorldCover values, verifies all 5,041 blocks and masks,
  renders local/remote tiles with an actual >4 GiB HTTP range, and runs a
  180-second concurrent raster/resource check.
- Native and wheel workflows are release dependencies. Linux wheel builds use
  the Ubuntu runner's glibc baseline with auditwheel's ISA check enabled;
  AL2023 builds only the Lambda bundle. Full-history checkouts preserve Hatch
  VCS versioning for release wheels and sdist.

## Local evidence and commands

- Strict auditwheel repair of a local runner-built bundle produced a
  `manylinux_2_39_x86_64` wheel. Installing that exact artifact in a clean venv
  passed `tests/contract_suite.py` 53/53 and `tests/remote_contracts.py` with
  two ranged requests. Its SHA-256 is
  `7cfce3e183d84af1f6ab040cfe62e7e6f8dd09f05d0440a85ae400a40adea568`.
- Building a source distribution and building a wheel from its extracted source
  preserved the Hatch VCS development version locally; exact release-tag
  versioning remains a hosted release gate.
- `process/validation/tools/ci_differential.py` passed 26/26 against the pinned
  reference.
- `tests/storage_contracts.py --sqlite-only` passed 11 cases; the hosted service
  job passed before the new upstream baseline step was added. The pure pinned
  reference suite locally collected 528 cases with 472 passes, the two expected
  ANSI failures, and 54 MySQL/PostgreSQL skips because no servers were started.
  CI must turn those 54 skips into passes.
- `tests/remote_contracts.py` and `tests/browser_contracts.py` passed locally.
- `tests/large_raster_contracts.py --source .cache/corpus/worldcover.tif` passed:
  5,285,932,652 bytes, 5,041 blocks, selected block offset 4,295,028,332,
  highest HTTP range start 4,295,016,448; report in ignored
  `build/local-large-3/report.json`.
- `tests/resource_contracts.py --seconds 5` passed 5,674 uncached requests,
  peak in-flight raster jobs 3, RSS 46.5→120.6 MB and FDs 5→9 (peak 17);
  report in ignored `build/local-resource-4`.
- The latest pushed hosted run passed both Linux wheel builds, both macOS
  desktop wheels, native lanes, Lambda, differential, services and browser.
  Windows desktop wheel failed after Cargo downloaded crates without producing
  artifacts; the installed-wheel matrix was therefore skipped. The local edit
  delays installation of the Python `rustup` helper until after Cargo builds.

## Qualification still required

1. Run the revised Linux wheel builders on hosted Ubuntu x86_64/aarch64 and
   install the resulting artifacts on the five operating-system/architecture
   cells with CPython 3.12–3.14. The local `manylinux_2_39` result does not
   qualify the hosted arm64 builder or other interpreter versions.
2. Run the revised Windows wheel job. Confirm native Cargo artifacts exist before
   installing Python `rustup`, and inspect the repaired wheel and import path.
   Confirm the Windows installed-wheel cells on the new artifact.
3. Run the new full upstream reference baseline and scheduled/tag large-raster
   and 180-second resource jobs on hosted Linux. A skipped scheduled-depth job
   is not evidence for release qualification.
4. Confirm sdist and tag builds derive exactly the tag version using Hatch VCS,
   and configure the PyPI trusted publisher/protected environment. Only publish
   after all dependencies pass for a tag on main.
5. Keep external AWS and Mapbox checks and per-record compatibility gaps open;
   this CI suite does not convert historical local P08 evidence into hosted
   passes. The full candidate-side 386-record parity matrix remains broader
   than the portable case suite. Do not claim complete parity from case counts.
