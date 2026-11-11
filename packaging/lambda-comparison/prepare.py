"""Prepare one upstream-generated SQLite catalog for both Lambda backends."""

import argparse
import hashlib
import json
import shutil
import sqlite3
import subprocess
from pathlib import Path

import rasterio
import terracotta

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--corpus", type=Path, default=ROOT / ".cache/corpus")
    parser.add_argument(
        "--reference-source", type=Path, default=ROOT / ".cache/reference/source.tar.gz"
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--upload", action="store_true")
    parser.add_argument("--region")
    parser.add_argument(
        "--navigation-controls",
        action="store_true",
        help="include pinned elevation and categorical land-cover sources",
    )
    args = parser.parse_args()
    if args.upload and not args.region:
        parser.error("--upload requires --region")
    if terracotta.__version__ != "0.10.1" or rasterio.__gdal_version__ != "3.10.3":
        parser.error("use Terracotta 0.10.1 with Rasterio GDAL 3.10.3")
    if (
        digest(args.reference_source)
        != "e5d2ac9b78a36d0f28414b1a4890c961dede50d1f9ce33b8d23eee643f5e2348"
    ):
        raise ValueError("Terracotta source archive checksum mismatch")
    args.out.mkdir(parents=True, exist_ok=False)
    terracotta.update_settings(USE_MULTIPROCESSING=False)
    database = args.out / "catalog.sqlite"
    driver = terracotta.get_driver(str(database.resolve()))
    driver.create(["scene", "band"])
    rows = []
    demo = json.loads((ROOT / "docs/examples/data/manifest.json").read_text())[
        "fixtures"
    ]
    navigation = json.loads((ROOT / "tests/fixtures/navigation.json").read_text())[
        "sources"
    ]
    sources = [
        ("lagoon", row["id"], ROOT / "docs/examples/data" / row["file"], row)
        for row in demo
        if row["id"] in ("red", "green", "blue", "nir")
    ]
    sources += [
        (
            "sentinel",
            band,
            args.corpus / navigation["s2-" + band]["file"],
            navigation["s2-" + band],
        )
        for band in ("red", "green", "blue")
    ]
    if args.navigation_controls:
        sources += [
            (name, "red", args.corpus / navigation[name]["file"], navigation[name])
            for name in ("dem", "worldcover")
        ]
    for scene, band, source, row in sources:
        if digest(source) != row["sha256"]:
            raise ValueError(f"raster checksum mismatch: {source.name}")
        key = f"rasters/{row['sha256']}/{source.name}"
        driver.insert(
            [scene, band],
            str(source.resolve()),
            override_path=f"s3://{args.bucket}/{key}",
        )
        rows.append(
            {
                "scene": scene,
                "band": band,
                "local_path": str(source.resolve()),
                "sha256": row["sha256"],
                "key": key,
                "bytes": source.stat().st_size,
            }
        )
    uploads = rows
    rows = [
        {key: value for key, value in row.items() if key != "local_path"}
        for row in rows
    ]
    manifest = {
        "terracotta": terracotta.__version__,
        "gdal": rasterio.__gdal_version__,
        "catalog_sha256": digest(database),
        "bucket": args.bucket,
        "sources": rows,
    }
    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (args.out / "upload-manifest.json").write_text(json.dumps(uploads, indent=2) + "\n")
    for name in ("requirements.lock", "handler.py"):
        shutil.copy2(HERE / name, args.out / name)
    shutil.copy2(HERE / "context.dockerignore", args.out / ".dockerignore")
    shutil.copy2(args.reference_source, args.out / "terracotta.tar.gz")
    with (
        sqlite3.connect(database) as source,
        sqlite3.connect(args.out / "catalog-local.sqlite") as target,
    ):
        source.backup(target)
        for row in uploads:
            folder = "demo" if row["scene"] == "lagoon" else "full"
            target.execute(
                "UPDATE datasets SET path=? WHERE scene=? AND band=?",
                (
                    f"/reference/{folder}/{Path(row['local_path']).name}",
                    row["scene"],
                    row["band"],
                ),
            )
    if args.upload:
        for row in uploads:
            subprocess.run(
                [
                    "aws",
                    "--region",
                    args.region,
                    "s3",
                    "cp",
                    row["local_path"],
                    f"s3://{args.bucket}/{row['key']}",
                    "--only-show-errors",
                ],
                check=True,
            )
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
