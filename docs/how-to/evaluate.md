# Evaluate RCFP in an application

Use a staging copy of an actual application to decide whether RCFP meets its
needs. Start with [installation](install.md), [the support matrix](../reference/support.md),
and [support expectations](../support-policy.md). An automated tutorial trial can
find defects but cannot establish production integration or retained usage.

Download the {download}`evaluation record <../examples/application-evaluation.md>`, fill it
with measurements and links to retained artifacts, and keep confidential evidence
in your own storage. Use a separate record for each application and artifact pair.
Mark a field unavailable when it was not measured. Do not enter estimated effort
or local benchmark ratios as observed production results.

## Choose a representative workload

For a Terracotta migration, inventory scripts, HTTP clients, Python calls,
catalog versions/backends, metadata writes, and recovery requirements. Follow the
[migration trial](upgrade-terracotta.md) and compare discovery, metadata, decoded
images, masks, categories, errors, and supported writes before cutover.

For a remote/private mosaic or time series, record real acquisitions, overlapping
footprints, band layout, overview strategy, credentials, immutable revisions,
selection/index costs, and concurrent viewport patterns. Use the
[mosaic guide](mosaics.md) and [remote-data guide](remote-data.md). Repeated labels
on one acquisition test selection only. They are not a real time-series trial.
Qualify credentials and source traffic in the intended environment.

For a scientific application, identify required class labels, numerical accuracy,
physical bands/calibration, CRS and grid, nodata/alpha, seams, and point queries.
Compare raw values and masks against an independent implementation before
evaluating display differences. See [categorical maps](categorical.md) and
[native map services](../reference/native-services.md). A projection identifier
alone does not qualify a polar application.

## Measure integration and delivery

Start the first-use clock at a clean environment with the declared prerequisites
available. Stop it when the application displays useful, independently checked
data. Record download/setup time separately, the commands and obstacles, and the
person-hours needed to integrate with the application and deployment. An
automated elapsed time must be labeled automated.

Replay actual requests for first view, pan, zoom, revisit, band/date selection,
and numerical inspection. Retain the workload, warm/cold definitions, client
network, concurrency, cache/CPU budgets, p50/p95/p99, errors/timeouts, source
requests/bytes, process memory, and infrastructure/egress costs with currency,
measurement window, and billing basis. Measure browser delivery separately from
HTTP completion. The [cloud test guide](cloud-test.md) describes a disposable
deployment procedure. Deploying it requires an authorized account and budget.

## Rehearse upgrade and recovery

Preserve the prior wheel/bundle, application code, configuration, and an
engine-consistent catalog backup. In separate environments, run the old version,
upgrade the staging copy, exercise ordinary reads and writes, and restart the old
version against that copy when the format permits. Then restore the backup and
repeat discovery and independent imagery/value checks. Record write losses and
downtime. Explicit schema changes need their own restore procedure.

For local Terracotta-format SQLite, the maintained rehearsal can compare two
installed RCFP wheels without changing either environment:

```sh
python tests/upgrade_contracts.py \
  --previous-python /path/to/previous-env/bin/python \
  --previous-wheel /path/to/previous.whl \
  --candidate-python /path/to/candidate-env/bin/python \
  --candidate-wheel /path/to/candidate.whl \
  --out build/application-upgrade
```

Both environments need Pillow and the exact corresponding wheel installed.
CPython 3.12–3.14 is required. The rehearsal uses pinned real rasters, isolated
processes, decoded image comparisons, a committed WAL write, and a SQLite backup.
It checks the named compatibility profile. It does not qualify an application's
schema migration, native keyed catalogs, remote SQL, credentials, or deployment.
Terracotta-to-RCFP migration is a separate check in `docs/tools/verify.py` with
`--reference-python`. See [contribution instructions](../contributing.md).

## Review outcomes and continued use

After the trial and after 30 and 90 days of actual use, record whether the
application still depends on RCFP, why it was retained or abandoned, workload
changes, incidents, upgrade outcomes, and ongoing maintenance effort. These are
follow-up checkpoints, not a requirement to run a failing deployment for 90 days.

Turn an obstacle into a focused issue with the triggering workload, expected
behavior, exact artifact, reproduction, consequence, and independent check.
Prioritize repeated adoption blockers, fidelity failures, and recovery problems
before broad new formats or catalog features. A Zarr/xarray or full catalog
proposal needs a concrete application, access pattern, ownership, and acceptance
test.

Publish a case study only after the evidence owner explicitly approves its text,
images, measurements, and attribution. State artifact identities, observation
dates, limitations, and unavailable measurements beside the results. Record who
approved which version and when. Without that permission, keep the record private.
