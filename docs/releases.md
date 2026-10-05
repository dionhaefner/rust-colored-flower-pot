# Release notes

Use the package version in the documentation title and `rcfp --version` to
identify your installed release. The protocol identity is independently 0.10.1.
Published release notes describe changes relevant to application users and
operators; match them to the wheel/bundle validation results for your release.

## Documented behavior

- Native serving, Python/WSGI, browser exploration, and Lambda event/runtime
  adapters share the Rust raster/storage core.
- Matching wheels bundle the native runtime; the compatibility facade is included
  in the ordinary install and selected explicitly before import or process launch.
- The Terracotta 0.10.1 profile preserves supported public workflows with RCFP
  package/import/command names. Native APIs have their own differences.
- Catalog format upgrades are explicit, with dry-run inspection; profile changes
  do not migrate rasters or catalogs.
- Request sizes, worker/queue budgets, cache accounting, verified remote SQL TLS,
  and opt-in metrics have documented operational behavior.

Read [upgrade guidance](how-to/upgrade-terracotta.md),
[the supported-surface matrix](reference/support.md), and
[operational limits](reference/errors.md) before deploying a different artifact.
