"""Export the bundled viewer beneath a GitHub Pages project path."""

from __future__ import annotations

import argparse
import html
import shutil
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]


def export(
    out: Path, api_url: str | None = None, revision: str = "unversioned"
) -> None:
    out.mkdir(parents=True, exist_ok=True)
    if api_url == "":
        (out / "index.html").write_text(
            '<!doctype html><html lang="en"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            "<title>RCFP demo</title><h1>RCFP demo</h1>"
            "<p>The live demo is not configured yet.</p>"
            '<p><a href="../how-to/demo.html">Deployment instructions</a></p></html>\n'
        )
        return
    if api_url is not None:
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
            raise ValueError(
                "API URL must be HTTPS without credentials, query or fragment"
            )
    client = ROOT / "src/assets/client"
    for folder in ("vendor", "examples"):
        shutil.copytree(client / folder, out / folder, dirs_exist_ok=True)
    shutil.copytree(ROOT / "js/sdk", out / "sdk", dirs_exist_ok=True)
    shutil.copy2(
        ROOT / "packaging/licenses/CLIENT-LICENSE.txt", out / "CLIENT-LICENSE.txt"
    )
    for name in (
        "viewer.js",
        "viewer.css",
        "viewer-connection.js",
        "natural-earth-land.geojson",
    ):
        shutil.copy2(client / name, out / name)
    shutil.copy2(client / "templates/index.html", out / "index.html")
    for path in [out / "index.html", *out.glob("examples/*.html")]:
        text = (
            path.read_text()
            .replace(
                "{{ hostname }}", html.escape((api_url or "").rstrip("/"), quote=True)
            )
            .replace("{{ profile }}", "native" if api_url else "")
            .replace("{{ source_revision }}", html.escape(revision, quote=True))
        )
        if api_url and path.name == "index.html":
            text = text.replace(
                'data-attribution=""',
                'data-attribution="Contains modified Copernicus Sentinel data 2021"',
            )
        path.write_text(text)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--api-url")
    parser.add_argument("--revision", default="unversioned")
    args = parser.parse_args()
    export(args.out, args.api_url, args.revision)
