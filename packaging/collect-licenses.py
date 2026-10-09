"""Retain Rust dependency notices alongside the native distribution."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
output = Path(sys.argv[1])
cargo = "cargo" if sys.platform == "win32" else str(root / "packaging/cargo.sh")
host = next(
    line.removeprefix("host: ")
    for line in subprocess.check_output(
        [cargo, "--version", "--verbose"], text=True
    ).splitlines()
    if line.startswith("host: ")
)
# Collect the native build's dependencies without fetching other platforms' crates.
command = [
    cargo,
    "metadata",
    "--locked",
    "--filter-platform",
    host,
    "--format-version",
    "1",
]
if os.environ.get("RCFP_CARGO_OFFLINE") == "1":
    command.append("--offline")
metadata = json.loads(subprocess.check_output(command, cwd=root))
manifest = []
for package in metadata["packages"]:
    if package["name"] == "rcfp":
        continue
    source = Path(package["manifest_path"]).parent
    candidates = [
        p
        for p in sorted(source.iterdir())
        if p.is_file() and p.name.upper().startswith(("LICENSE", "COPYING", "NOTICE"))
    ]
    if package["name"] == "openssl-src":
        candidates.append(source / "openssl/LICENSE.txt")
    target = output / (package["name"] + "-" + package["version"])
    target.mkdir(parents=True, exist_ok=True)
    retained = []
    for path in candidates:
        if path.is_file():
            name = "OPENSSL-LICENSE.txt" if path.parent.name == "openssl" else path.name
            shutil.copyfile(path, target / name)
            retained.append(name)
    manifest.append(
        {
            "name": package["name"],
            "version": package["version"],
            "license": package["license"],
            "source": package["source"],
            "notices": retained,
        }
    )
(output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
