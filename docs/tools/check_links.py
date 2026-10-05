"""Check local HTML links, anchors, and accidental internal build metadata."""

from __future__ import annotations

import argparse
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.targets = set()
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id"):
            self.targets.add(attrs["id"])
        if tag == "a" and attrs.get("name"):
            self.targets.add(attrs["name"])
        for key in ("href", "src"):
            if attrs.get(key):
                self.links.append(attrs[key])


def check(folder):
    folder = folder.resolve()
    parsed = {}
    errors = []
    for path in folder.rglob("*.html"):
        text = path.read_text()
        parser = Links()
        parser.feed(text)
        parsed[path] = parser
        if re.search(r"/(?:home|Users)/[^\s<]+", text):
            errors.append(f"{path.relative_to(folder)} exposes a local workspace path")
    count = 0
    for path, parser in parsed.items():
        for link in parser.links:
            url = urlsplit(link)
            if url.scheme or url.netloc:
                continue
            target = (
                (folder / unquote(url.path).lstrip("/"))
                if url.path.startswith("/")
                else (path.parent / unquote(url.path))
                if url.path
                else path
            )
            target = target.resolve()
            if target.is_dir():
                target = target / "index.html"
            if not target.is_relative_to(folder) or not target.exists():
                errors.append(f"{path.relative_to(folder)}: missing target {link}")
            elif (
                url.fragment
                and target in parsed
                and unquote(url.fragment) not in parsed[target].targets
            ):
                errors.append(f"{path.relative_to(folder)}: missing anchor {link}")
            count += 1
    if errors:
        raise RuntimeError("\n".join(errors))
    return {"html_pages": len(parsed), "local_links": count}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("html", type=Path)
    print(check(parser.parse_args().html))
