# RCFP documentation

Build from the repository or source archive root after installing the RCFP
artifact being documented:

```sh
python -m pip install -r docs/requirements.txt
python docs/tools/generate_reference.py
python -m sphinx -n -W --keep-going -b html docs docs/_build/html
python docs/tools/check_links.py docs/_build/html
python docs/tools/verify.py --html docs/_build/html --out build/docs-checks
python -m http.server --directory docs/_build/html 8000
```

Open http://127.0.0.1:8000. The site provides [tutorials](tutorials/index.md),
[how-to guides](how-to/index.md), [reference](reference/index.md), and
[explanation](explanation/index.md), with prominent
[Terracotta migration](how-to/upgrade-terracotta.md) and
[RCFP advantages](explanation/why-rcfp.md).

[Contributor instructions](contributing.md) describe snapshots, example checks,
CI, versioned hosting, and authoring. HTML rendering uses local extracted
snapshots; it does not need native libraries or external data/services.
