"""Verify documentation and versioning boundaries in package artifacts."""

import argparse
import email
import tarfile
import tomllib
import zipfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdist", type=Path, required=True)
    parser.add_argument("--wheel", type=Path, required=True)
    args = parser.parse_args()
    with tarfile.open(args.sdist) as archive:
        names = archive.getnames()
        prefix = names[0].split("/")[0] + "/"
        required = (
            "docs/conf.py",
            "docs/index.md",
            "docs/how-to/upgrade-terracotta.md",
            "docs/examples/create_catalog.py",
            "docs/examples/data/red.tif",
            "docs/examples/data/manifest.json",
            "docs/tools/verify.py",
            "docs/tools/generate_reference.py",
            "docs/_generated/version.json",
            "docs/_static/architecture.svg",
            "docs/_static/TERRACOTTA-LICENSE.txt",
            "python/rcfp/_version.py",
        )
        for name in required:
            assert prefix + name in names, name
        assert not any(
            "/process/" in name
            or "/design/" in name
            or "/docs/_build/" in name
            or "/.cache/" in name
            for name in names
        )
        metadata = tomllib.loads(
            archive.extractfile(prefix + "pyproject.toml").read().decode()
        )
        assert metadata["tool"]["hatch"]["version"]["source"] == "vcs"
        assert any(
            name.startswith("hatch-vcs")
            for name in metadata["build-system"]["requires"]
        )
        for target in ("wheel", "sdist"):
            assert (
                metadata["tool"]["hatch"]["build"]["targets"][target][
                    "core-metadata-version"
                ]
                == "2.4"
            )
        package = email.message_from_bytes(
            archive.extractfile(prefix + "PKG-INFO").read()
        )
        assert package["Metadata-Version"] == "2.4"
    with zipfile.ZipFile(args.wheel) as archive:
        assert not any(
            name.startswith(
                ("docs/", "design/", "process/", "terracotta/", "terracotta_rust/")
            )
            for name in archive.namelist()
        )
        assert "rcfp/_version.py" in archive.namelist()
    print("Source docs, wheel boundary, and Hatch VCS requirements verified")


if __name__ == "__main__":
    main()
