"""Static documentation build; reference extraction is a separate command."""

import json
from pathlib import Path

project = "RCFP"
copyright = "RCFP contributors; adapted material © 2018 DHI GRAS"
author = "RCFP contributors"
release = json.loads((Path(__file__).parent / "_generated/version.json").read_text())[
    "version"
]
version = release
extensions = ["myst_parser"]
source_suffix = {".md": "markdown", ".rst": "restructuredtext"}
exclude_patterns = ["_build", "_generated", "examples", "tools", "README.md"]
myst_heading_anchors = 4
myst_enable_extensions = ["substitution"]
myst_substitutions = {"version": release}
html_theme = "furo"
html_title = "RCFP documentation"
html_short_title = "RCFP"
templates_path = ["_templates"]
html_static_path = ["_static"]
html_css_files = ["rcfp.css"]
html_theme_options = {
    "light_css_variables": {
        "color-brand-primary": "#9c442c",
        "color-brand-content": "#9c442c",
    }
}
if ".dev" in release:
    html_theme_options["announcement"] = "Development documentation"
html_last_updated_fmt = None
html_copy_source = False
html_show_sourcelink = False
nitpicky = True
linkcheck_ignore = [
    r"http://(localhost|127\.0\.0\.1).*",
    r"https?://example\.(com|invalid).*",
    r"https://catalog\.example.*",
]
