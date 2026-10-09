# Contribute and maintain documentation

Install the source or the exact wheel you want to document in a virtual
environment. Install the pinned documentation dependencies separately:

```sh
python -m pip install -r docs/requirements.txt -r docs/requirements-test.txt
python docs/tools/generate_reference.py
python -m sphinx -n -W --keep-going -b html docs docs/_build/html
python docs/tools/check_links.py docs/_build/html
python docs/tools/verify.py --html docs/_build/html --out build/docs-checks
python -m http.server --directory docs/_build/html 8000
```

The last command serves a local preview at `http://127.0.0.1:8000`. Reference
extraction uses the installed artifact, including Hatch VCS package metadata.
The HTML build uses the extracted snapshots and does not import native RCFP,
start services, require credentials, or download imagery. After dependencies are
installed, that rendering step works offline. Re-extract snapshots whenever
you change the documented artifact.

`generate_reference.py --check` fails if CLI help, defaults, public Python
signatures, palettes, HTTP specification, or package version differ from the
installed artifact. Example checks exercise real images, HTTP, WSGI, catalog
backup/restore, compatibility/native workflows, and a Lambda event. Optional
`--reference-python` adds Terracotta read/write/migration rehearsal. It must point
to an environment with Terracotta 0.10.1 installed. Browser checks live in
`docs/tools/browser_check.py` and require the pinned browser-test dependencies:

```sh
python -m pip install -r docs/requirements-test.txt
python -m playwright install chromium
python docs/tools/browser_check.py --html docs/_build/html --out build/docs-browser
```

Use fresh output directories for verification runs. The installed-wheel platform
matrix also runs the example checks on its matching operating systems and Python
versions. This covers the scripts behind the POSIX and PowerShell instructions.

Write each page for one reader need: guided learning in tutorials, task completion
in how-to guides, exact interfaces in reference, understanding in explanation.
Use [Diátaxis](https://diataxis.fr/) to choose a form, not to fill directories with
placeholders. Include canonical runnable files instead of maintaining duplicate
scripts in prose. Use fresh temporary outputs, meaningful expected results,
complete prerequisites, alt text, and explicit data attribution.

Update reference and migration differences in the same change as a public API
change. Keep release notes about observable behavior. Version/profile/catalog
identities must remain separate. State evidence scope beside performance and
support claims. Review both the first-use journey and Terracotta migration when
changing installation, settings, rendering, storage, or deployment.

## Repository checks

For code or test contributions, start with a focused issue and a small
reproduction. Read the repository `AGENTS.md`, `design/README.md`, and relevant
compatibility/decision notes in a checkout. `tests/README.md` maps each behavior to
its maintained runner. Keep acquired data and generated reports in ignored
storage. Include exact artifact/input identities and separate unavailable checks
from passes. The [public corpus](explanation/raster-corpus.md) describes fixture
provenance, independent expectations, and contribution requirements. Support and
ownership expectations are in the [maintenance policy](support-policy.md).

Install `pre-commit` and run `pre-commit install`, then
`pre-commit run --all-files`. Hooks format Rust, Python, C, shell, maintained
Markdown/YAML/JSON/HTML/CSS/JavaScript and lint applicable source. Rust hooks use
`rust-toolchain.toml` with rustfmt/clippy components. See the repository's
`tests/README.md` for product-test commands. Docs smoke complements those suites.

## CI and hosting

The documentation workflow builds an installed wheel, extracts reference, runs
examples and browser checks, and uploads the HTML and verification results.
Release assembly requires that workflow. External-link checking runs separately
from deterministic local link checking.

The root `.readthedocs.yaml` configures a versioned Sphinx build from the matching
source/artifact, using [Read the Docs configuration](https://docs.readthedocs.com/platform/stable/config-file/v2.html).
An owner connects the repository, enables release tags and development previews,
and chooses the canonical URL in the hosting service. Preserve immutable
release versions and mark development documentation clearly. Configuring builds
locally does not itself create an account connection or publish a site.
