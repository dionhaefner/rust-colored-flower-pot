"""Extract reference snapshots from the installed RCFP distribution."""

from __future__ import annotations

import argparse
import importlib
import inspect
import json
import os
import re
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

DOCS = Path(__file__).resolve().parents[1]


def python_reference(profile):
    import rcfp

    objects = {
        name: getattr(rcfp, name)
        for name in (
            "request",
            "request_full",
            "render_preview",
            "identity",
            "compute_metadata",
            "data_operation",
            "DataStore",
            "NativeApplication",
        )
    }
    modules = ["rcfp.wsgi"]
    if profile == "native":
        objects.update(
            {
                name: getattr(rcfp, name)
                for name in ("LayerStore", "LayerApplication", "inspect_asset")
            }
        )
    if profile == "native":
        modules += ["rcfp.stac"]
    if profile == "terracotta-0.10.1":
        objects.update(
            {
                name: getattr(rcfp, name)
                for name in ("get_driver", "get_settings", "update_settings")
            }
        )
        modules += [
            "rcfp.drivers.terracotta_driver",
            "rcfp.drivers.sqlite_meta_store",
            "rcfp.drivers.sqlite_remote_meta_store",
            "rcfp.drivers.mysql_meta_store",
            "rcfp.drivers.postgresql_meta_store",
            "rcfp.config",
            "rcfp.exceptions",
        ]
    lines = []
    for module_name in modules:
        module = importlib.import_module(module_name)
        for name, value in vars(module).items():
            if (
                not name.startswith("_")
                and (inspect.isfunction(value) or inspect.isclass(value))
                and getattr(value, "__module__", "").startswith(module_name)
            ):
                objects[f"{module_name}.{name}"] = value
    for name, value in sorted(objects.items()):
        try:
            signature = str(inspect.signature(value))
        except (ValueError, TypeError):
            signature = ""
        lines += [
            f"### `{name}{signature}`",
            "",
            (
                inspect.getdoc(value)
                or "See the interface and lifecycle descriptions on this page."
            ),
            "",
        ]
        if inspect.isclass(value) and not issubclass(value, BaseException):
            for member_name, member in sorted(vars(value).items()):
                if not member_name.startswith("_") and inspect.isfunction(member):
                    lines += [f"- `{member_name}{inspect.signature(member)}`"]
            lines += [""]
    return "\n".join(lines) + "\n"


def snapshots():
    import rcfp

    data = {
        "version.json": json.dumps(
            {"version": version("rcfp"), "protocol": rcfp.__protocol_version__},
            indent=2,
        )
        + "\n"
    }
    defaults = rcfp.data_operation({"operation": "default_settings"})
    defaults["REMOTE_DB_CACHE_DIR"] = "<system temporary directory>/terracotta"
    rows = ["| TOML key | Default |", "| --- | --- |"]
    for key, value in defaults.items():
        rows.append(f"| `{key}` | `{json.dumps(value).replace('|', '&#124;')}` |")
    data["settings.md"] = "\n".join(rows) + "\n"
    spec = rcfp.data_operation({"operation": "spec"})
    data["swagger.json"] = json.dumps(spec, indent=2, sort_keys=True) + "\n"
    rows = ["| Route | Methods | Purpose |", "| --- | --- | --- |"]
    for route, methods in sorted(spec["paths"].items()):
        for method, entry in methods.items():
            if method not in ("get", "post", "head", "options", "put", "delete"):
                continue
            description = (
                entry.get("summary", entry.get("description", ""))
                .replace("\n", " ")
                .replace("|", "\\|")
            )
            rows.append(f"| `{route}` | `{method.upper()}` | {description} |")
    data["routes.md"] = "\n".join(rows) + "\n"
    data["palettes.txt"] = (
        "\n".join(rcfp.data_operation({"operation": "palette"})) + "\n"
    )
    clean_env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("TC_", "RCFP_COMPAT_"))
    }
    for profile in ("native", "terracotta-0.10.1"):
        env = dict(clean_env, RCFP_COMPAT_PROFILE=profile)
        worker = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--python-worker", profile],
            env=env,
            check=True,
            text=True,
            capture_output=True,
        )
        data[f"python-{profile}.md"] = re.sub(r" at 0x[0-9a-fA-F]+", "", worker.stdout)
        for command in (
            "main",
            "serve",
            "ingest",
            "optimize-rasters",
            "connect",
            "migrate",
        ):
            args = [] if command == "main" else [command]
            result = subprocess.run(
                [sys.executable, "-m", "rcfp_cli", *args, "--help"],
                env=env,
                check=True,
                text=True,
                capture_output=True,
            )
            data[f"cli-{profile}-{command}.txt"] = result.stdout
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--python-worker", choices=("native", "terracotta-0.10.1"))
    args = parser.parse_args()
    if args.python_worker:
        print(python_reference(args.python_worker), end="")
        return
    DOCS.joinpath("_generated").mkdir(exist_ok=True)
    changed = []
    for name, content in snapshots().items():
        path = DOCS / "_generated" / name
        if not path.exists() or path.read_text() != content:
            changed.append(name)
            if not args.check:
                path.write_text(content)
    if args.check and changed:
        raise SystemExit(
            f"Reference snapshots differ from installed artifact: {', '.join(changed)}"
        )
    print(
        f"Reference snapshots {'verified' if args.check else 'generated'} from installed RCFP"
    )


if __name__ == "__main__":
    main()
