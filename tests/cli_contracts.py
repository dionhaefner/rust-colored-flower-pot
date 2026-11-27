"""Check installed CLI help, import coexistence and ready-to-serve sample output."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    binary, out = args.binary.resolve(), args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("TC_", "RCFP_"))}
    checks = []

    def command(*args, code=0):
        result = subprocess.run(
            [str(binary), *args],
            cwd=out,
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == code, result.stderr
        return result

    for profile in ("native", "terracotta"):
        for name in (
            "serve",
            "connect",
            "demo",
            "inspect",
            "ingest",
            "optimize-rasters",
            "migrate",
            "layers",
            "lambda-event",
            "lambda-runtime",
        ):
            for flag in ("--help", "-h"):
                result = command("--compat-profile", profile, name, flag)
                assert f"Usage: rcfp {name}" in result.stdout
                assert "\x1b[" not in result.stdout, "ANSI escapes in redirected help"
                assert "\n\n" in result.stdout, "missing help structure"
        identity = json.loads(
            command("--compat-profile", profile, "--version-json").stdout
        )
        assert identity["compatibility_profile"] == profile
    assert list(out.iterdir()) == [], "help unexpectedly wrote files"
    checks.append(
        "both profiles: command help, short flags, clean pipes and identities"
    )
    for subcommand in (
        "create",
        "list",
        "describe",
        "delete",
        "put",
        "register",
        "render",
        "import",
    ):
        assert (
            f"Usage: rcfp layers {subcommand}"
            in command("layers", subcommand, "--help").stdout
        )
    command("layers", "unknown", "--help", code=2)
    command("unknown", code=2)
    command("serve", "--port", code=2)
    command("--compat-profile", "unknown", "--help", code=2)
    assert "Get started:" in command("--help").stdout
    checks.append("nested help and invalid commands")

    result = command(
        "connect",
        "maps.example.invalid/imagery",
        "--no-browser",
        "--source-revision",
        "r/2",
    )
    viewer = urlsplit(result.stdout.strip())
    assert viewer.scheme == "https" and viewer.path.endswith("/explore/")
    assert parse_qs(viewer.query) == {
        "service": ["https://maps.example.invalid/imagery"],
        "source-revision": ["r/2"],
    }
    viewer = urlsplit(
        command("connect", "localhost:5000", "--no-browser").stdout.strip()
    )
    assert viewer.netloc == "dionhaefner.github.io"
    assert parse_qs(viewer.query) == {"service": ["http://localhost:5000"]}
    assert command("connect", "--no-browser").stdout.strip() == (
        "https://dionhaefner.github.io/rust-colored-flower-pot/explore/"
    )
    local = command("connect", "localhost:5000", "--local", "--no-browser")
    assert local.stdout.strip() == "http://localhost:5000/explore/"
    command(
        "connect", "https://maps.example.invalid/?token=private", "--no-browser", code=2
    )
    command("sample", "--help", code=2)
    checks.append("hosted viewer launch without backend preflight")

    for imports in (
        "import hashlib; import rcfp; import ssl",
        "import ssl; import hashlib; import rcfp",
        "import rcfp; import hashlib; import ssl",
    ):
        subprocess.run(
            [sys.executable, "-c", imports + "; assert ssl.RAND_status()"],
            cwd=out,
            env=env,
            check=True,
            capture_output=True,
            timeout=30,
        )
    checks.append("hashlib/ssl/rcfp import orders")

    result = command("demo", "-o", "demo-data")
    assert "rcfp serve -d demo-data/map.sqlite" in result.stdout
    assert "rcfp connect localhost:5000" in result.stdout
    manifest = json.loads((out / "demo-data/data/manifest.json").read_text())
    for row in manifest["fixtures"]:
        assert (
            hashlib.sha256(
                (out / "demo-data/data" / row["file"]).read_bytes()
            ).hexdigest()
            == row["sha256"]
        )
    golden = ROOT / "tests/fixtures/demo/preview.png"
    golden_manifest = json.loads((golden.parent / "manifest.json").read_text())
    assert (
        hashlib.sha256(golden.read_bytes()).hexdigest()
        == golden_manifest["preview_sha256"]
    )
    assert np.array_equal(
        np.asarray(Image.open(out / "demo-data/map.png").convert("RGBA")),
        np.asarray(Image.open(golden).convert("RGBA")),
    ), "sample preview differs from pinned independent Terracotta pixels"
    command("inspect", "-d", "demo-data/map.sqlite", "--json")
    before = hashlib.sha256((out / "demo-data/map.sqlite").read_bytes()).hexdigest()
    command("demo", "-o", "demo-data", code=2)
    assert (
        hashlib.sha256((out / "demo-data/map.sqlite").read_bytes()).hexdigest()
        == before
    )
    checks.append("checksum-pinned ready-to-serve sample and independent preview")
    (out / "report.json").write_text(
        json.dumps({"passed": True, "checks": checks}, indent=2) + "\n"
    )
    print(json.dumps({"passed": True, "checks": checks}, indent=2))


if __name__ == "__main__":
    main()
