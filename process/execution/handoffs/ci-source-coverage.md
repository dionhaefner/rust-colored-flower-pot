# CI source coverage handoff

The native and installed-wheel suites already cover different interfaces. This
change makes their structure visible in `tests/README.md` and publishes two
separate source coverage reports:

- Correctness services instruments Rust 1.85.1 with cargo-llvm-cov 0.8.5. It runs
  all Rust targets with `storage-integration`, runs the native storage/HTTP
  contract script against the instrumented debug executable, and uploads LCOV.
  The script terminates HTTP server subprocesses, so their counters may not be
  flushed; catalog CLI operations exit normally and are counted.
  The normal release executable still runs the existing storage contracts.
- The Linux x86_64 / CPython 3.12 installed-wheel cell runs the 53-case contract
  suite under coverage.py 7.10.7 and uploads JSON/XML for `rcfp` Python source.
  The other 14 installed-wheel cells keep the same uninstrumented suite.
- GitHub summaries show separate totals and ten low-coverage files. No threshold
  is imposed before a hosted baseline is available. Passing contracts, reference
  comparison, browser, Lambda, and platform gates remain independent of the
  percentages.

Local validation: both workflow YAML files parse, the summary program compiles
and renders synthetic LCOV/coverage.py inputs, and `git diff --check` passes.
The cargo-llvm-cov executable is absent locally, and the user has exhausted
GitHub Actions minutes, so no hosted coverage artifact exists yet. Leave the
branch unpushed. When budget returns, run the PR workflows, inspect the LCOV and
JSON/XML artifacts, verify nonzero source files and expected modules, then choose
an evidence-based threshold if one is useful. The current branch also contains
other in-progress changes; stage only the coverage files when committing.
