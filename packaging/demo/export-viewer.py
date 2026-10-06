"""Export the bundled viewer beneath a GitHub Pages project path."""

from __future__ import annotations

import argparse
import html
import shutil
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]


def export(out: Path, api_url: str = "", revision: str = "unversioned") -> None:
    out.mkdir(parents=True, exist_ok=True)
    if not api_url:
        (out / "index.html").write_text(
            '<!doctype html><html lang="en"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            "<title>RCFP demo</title><h1>RCFP demo</h1>"
            "<p>The live demo is not configured yet.</p>"
            '<p><a href="../how-to/demo.html">Deployment instructions</a></p></html>\n'
        )
        return
    url = urlsplit(api_url)
    if (
        url.scheme not in ("https", "http")
        or not url.hostname
        or (url.scheme == "http" and url.hostname != "127.0.0.1")
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise ValueError("API URL must be HTTPS without credentials, query or fragment")
    client = ROOT / "src/assets/client"
    for folder in ("vendor", "examples"):
        shutil.copytree(client / folder, out / folder, dirs_exist_ok=True)
    shutil.copytree(ROOT / "js/sdk", out / "sdk", dirs_exist_ok=True)
    shutil.copy2(
        ROOT / "packaging/licenses/CLIENT-LICENSE.txt", out / "CLIENT-LICENSE.txt"
    )
    for name in ("viewer.js", "viewer.css"):
        shutil.copy2(client / name, out / name)
    shutil.copy2(client / "templates/index.html", out / "index.html")
    for path in [out / "index.html", out / "viewer.js", *out.glob("examples/*.html")]:
        prefix = "../" if path.parent.name == "examples" else "./"
        text = path.read_text()
        for folder in ("vendor/", "sdk/", "examples/", "viewer."):
            text = text.replace(f'"/{folder}', f'"{prefix}{folder}')
        text = (
            text.replace("{{ hostname }}", html.escape(api_url.rstrip("/"), quote=True))
            .replace("{{ profile }}", "native")
            .replace("{{ source_revision }}", html.escape(revision, quote=True))
        )
        if path.name == "index.html":
            text = text.replace(
                "Offline map · no basemap token required",
                "Contains modified Copernicus Sentinel data 2021",
            )
        path.write_text(text)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--api-url", default="")
    parser.add_argument("--revision", default="unversioned")
    args = parser.parse_args()
    export(args.out, args.api_url, args.revision)
