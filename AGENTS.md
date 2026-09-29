# Working on RCFP

For rewrite work, follow [process/AGENTS.md](process/AGENTS.md).

Keep development plans, phase labels, experiment notes, reference acquisition,
and implementation history under `process/`. Files outside that directory must
describe the current product and supported build, test, and release behavior.
Keep process metadata out of CLI output, HTTP responses, Python APIs, package
metadata, embedded assets, logs, source comments, and tests. Before finishing,
search outside `process/` for stale phase names, reference workspace paths,
and obsolete package identities. Hatch VCS versioning is part of the supported
package build.
