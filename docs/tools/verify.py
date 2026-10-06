"""Verify documented examples against an installed artifact and live HTTP/WSGI."""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import io
import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from importlib.metadata import version
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import urlopen
from wsgiref.simple_server import WSGIRequestHandler, make_server

from check_links import check
from PIL import Image

DOCS = Path(__file__).resolve().parents[1]


def run(*arguments, env=None):
    return subprocess.run(
        [str(value) for value in arguments],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )


def load(name):
    spec = importlib.util.spec_from_file_location(
        name, DOCS / "examples" / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def decoded(body):
    image = Image.open(io.BytesIO(body)).convert("RGBA")
    assert image.size == (256, 256), image.size
    assert len(image.getcolors(maxcolors=65536)) > 1, "Image has no data variation"
    return hashlib.sha256(image.tobytes()).hexdigest()


def wait_for(url, process):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=1):
                return
        except (URLError, TimeoutError):
            if process.poll() is not None:
                raise RuntimeError(f"Server exited with {process.returncode}")
            time.sleep(0.05)
    raise TimeoutError(url)


def port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def reference_roundtrip(python, folder):
    import rcfp

    catalog = folder / "upstream.sqlite"
    red = DOCS / "examples/data/red.tif"
    result = run(
        python,
        "-c",
        "import sys,terracotta as tc; assert tc.__version__ == '0.10.1'; d=tc.get_driver(sys.argv[1]); d.create(['scene','band']); d.insert(['lagoon','red'],sys.argv[2]); print(d.db_version)",
        catalog,
        red,
    )
    assert "0.10" in result.stdout
    run(
        sys.executable,
        DOCS / "examples/compatibility_trial.py",
        catalog,
        env=dict(os.environ, RCFP_COMPAT_PROFILE="terracotta-0.10.1"),
    )
    store = rcfp.DataStore(catalog)
    store.insert(["lagoon", "copy"], red)
    run(
        python,
        "-c",
        "import sys,terracotta as tc; d=tc.get_driver(sys.argv[1]); assert ('lagoon','copy') in d.get_datasets(); assert d.get_metadata(['lagoon','copy'])['valid_percentage'] > 0",
        catalog,
    )
    before = hashlib.sha256(red.read_bytes()).hexdigest()
    older = folder / "older.sqlite"
    # The 0.9 to 0.10 chain changes the marker without altering the schema.
    load("backup_catalog").backup(catalog, older)
    with sqlite3.connect(older) as db:
        db.execute("UPDATE terracotta SET version='0.9.0'")
    baseline = folder / "older-backup.sqlite"
    load("backup_catalog").backup(older, baseline)
    digest = hashlib.sha256(older.read_bytes()).hexdigest()
    run(
        sys.executable,
        "-m",
        "rcfp_cli",
        "migrate",
        older,
        "--to",
        "0.10.1",
        "--dry-run",
    )
    assert hashlib.sha256(older.read_bytes()).hexdigest() == digest, (
        "Dry run changed the catalog"
    )
    run(sys.executable, "-m", "rcfp_cli", "migrate", older, "--to", "0.10.1", "--yes")
    run(
        python,
        "-c",
        "import sys,terracotta as tc; d=tc.get_driver(sys.argv[1]); assert d.db_version.startswith('0.10'); assert ('lagoon','red') in d.get_datasets()",
        older,
    )
    assert hashlib.sha256(red.read_bytes()).hexdigest() == before
    with sqlite3.connect(baseline) as db:
        assert db.execute("SELECT version FROM terracotta").fetchone() == ("0.9.0",)
    return {
        "upstream_create_rcfp_read": True,
        "rcfp_write_upstream_read": True,
        "historical_dry_run_and_upgrade": True,
        "backup_preserves_old_format": True,
        "raster_unchanged": True,
    }


def verify(args):
    import rcfp
    from rcfp.wsgi import Application

    if rcfp.compatibility_profile != "native":
        raise RuntimeError(
            "Run the docs verification in native mode; it starts compatibility children itself"
        )
    folder = args.out.resolve()
    folder.mkdir(parents=True, exist_ok=False)
    report = {
        "version": version("rcfp"),
        "package": str(Path(rcfp.__file__).resolve()),
        "checks": {},
    }
    checks = report["checks"]
    if args.html:
        checks["local_links"] = check(args.html)
    database = folder / "catalog.sqlite"
    run(sys.executable, DOCS / "examples/create_catalog.py", database)
    store = rcfp.DataStore(database)
    metadata = store.get_metadata(["lagoon", "red"])
    assert metadata["range"] == (127.0, 5928.0)
    assert metadata["valid_percentage"] == 100.0
    checks["catalog_metadata"] = True
    png = database.with_suffix(".png").read_bytes()
    expected = decoded(png)
    assert expected == decoded((DOCS / "_static/red.png").read_bytes())
    checks["first_preview"] = expected
    run(sys.executable, DOCS / "examples/render_images.py", database, folder / "images")
    run(sys.executable, DOCS / "examples/stac_map.py", "--out", folder / "stac")
    assert decoded((folder / "stac/observation-0.png").read_bytes())
    stac_metadata = json.loads((folder / "stac/observations.json").read_text())
    assert len(stac_metadata["assets"]) == 1
    assert stac_metadata["assets"][0]["item"] == "lagoon"
    checks["stac_item"] = stac_metadata["id"]
    bind = port()
    with (folder / "stac-catalog.log").open("w") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "rcfp_cli",
                "serve",
                "--source",
                str(DOCS / "examples/data/lagoon-catalog.json"),
                "--bind",
                f"127.0.0.1:{bind}",
            ],
            stdout=log,
            stderr=log,
        )
        try:
            origin = f"http://127.0.0.1:{bind}"
            wait_for(origin + "/keys", process)
            datasets = json.load(urlopen(origin + "/datasets", timeout=10))["datasets"]
            assert [dataset["asset"] for dataset in datasets] == ["red", "green"]
            from urllib.parse import quote

            images = [
                decoded(
                    urlopen(
                        origin
                        + "/singleband/"
                        + "/".join(
                            quote(dataset[key], safe="")
                            for key in ("collection", "datetime", "item", "asset")
                        )
                        + "/preview.png?stretch_range=[0,2000]",
                        timeout=10,
                    ).read()
                )
                for dataset in datasets
            ]
            assert images[0] != images[1]
            checks["stac_catalog_cli"] = json.load(
                urlopen(origin + "/__terracotta/identity", timeout=10)
            )["source_revision"]
        finally:
            process.terminate()
            process.wait(timeout=30)
    hashes = {}
    for name in ("rgb", "ndvi", "classes", "gamma"):
        image = (folder / "images" / f"{name}.png").read_bytes()
        hashes[name] = decoded(image)
        assert hashes[name] == decoded((DOCS / "_static" / f"{name}.png").read_bytes())
    assert hashes["rgb"] != hashes["gamma"]
    colors = {
        color
        for _, color in Image.open(folder / "images/classes.png")
        .convert("RGBA")
        .getcolors(maxcolors=65536)
    }
    assert colors.issubset(
        {
            (0, 100, 0, 255),
            (255, 255, 76, 255),
            (240, 150, 255, 255),
            (250, 0, 0, 255),
            (0, 100, 200, 255),
            (0, 0, 0, 0),
        }
    ), colors
    checks["rendered_images"] = hashes
    with sqlite3.connect(database) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE documentation_backup_probe(value TEXT)")
        db.execute(
            "INSERT INTO documentation_backup_probe VALUES ('committed WAL data')"
        )
        db.commit()
        run(
            sys.executable,
            DOCS / "examples/backup_catalog.py",
            database,
            folder / "backup.sqlite",
        )
        with sqlite3.connect(folder / "backup.sqlite") as restored:
            assert restored.execute(
                "SELECT value FROM documentation_backup_probe"
            ).fetchone() == ("committed WAL data",)
        assert (
            decoded(
                rcfp.render_preview(
                    folder / "backup.sqlite", ["lagoon", "red"], stretch_range=[0, 3000]
                )
            )
            == expected
        )
    checks["wal_backup_and_restore"] = True
    try:
        store.batch(
            [
                {
                    "operation": "insert",
                    "keys": ["lagoon", "temporary"],
                    "raster": str(DOCS / "examples/data/red.tif"),
                },
                {
                    "operation": "insert",
                    "keys": ["lagoon", "missing"],
                    "raster": str(folder / "missing.tif"),
                },
            ]
        )
    except rcfp.RcfpError:
        pass
    else:
        raise AssertionError("Invalid batch succeeded")
    status, _, _, _ = rcfp.request(database, "GET", "/metadata/lagoon/temporary")
    assert status == 404
    checks["atomic_batch_rollback"] = True
    preview = "/singleband/lagoon/red/preview.png?" + urlencode(
        {"stretch_range": "[0,3000]"}
    )
    bind = port()
    with (folder / "http.log").open("w") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "rcfp_cli",
                "serve",
                "-d",
                str(database),
                "--bind",
                f"127.0.0.1:{bind}",
            ],
            stdout=log,
            stderr=log,
        )
        try:
            origin = f"http://127.0.0.1:{bind}"
            wait_for(origin + "/keys", process)
            keys = json.load(urlopen(origin + "/keys", timeout=10))
            assert [row["key"] for row in keys["keys"]] == ["scene", "band"]
            assert decoded(urlopen(origin + preview, timeout=10).read()) == expected
            served = json.load(urlopen(origin + "/swagger.json", timeout=10))
            assert served == json.loads((DOCS / "_generated/swagger.json").read_text())
            assert b"RCFP" in urlopen(origin + "/apidoc", timeout=10).read()
            checks["live_native_http_spec_and_preview"] = True
        finally:
            process.terminate()
            process.wait(timeout=30)

    class Quiet(WSGIRequestHandler):
        def log_message(self, format, *values):
            pass

    server = make_server(
        "127.0.0.1", 0, Application(str(database)), handler_class=Quiet
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert (
            decoded(
                urlopen(
                    f"http://127.0.0.1:{server.server_port}" + preview, timeout=10
                ).read()
            )
            == expected
        )
        checks["live_wsgi_preview"] = True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)
    run(
        sys.executable,
        DOCS / "examples/compatibility_trial.py",
        database,
        env=dict(os.environ, RCFP_COMPAT_PROFILE="terracotta-0.10.1"),
    )
    checks["compatibility_driver_and_native_trial"] = True
    event = folder / "event.json"
    event.write_text(
        json.dumps(
            {
                "version": "2.0",
                "rawPath": preview.split("?")[0],
                "rawQueryString": preview.split("?")[1],
                "requestContext": {"http": {"method": "GET"}},
            }
        )
    )
    response = json.loads(
        run(
            sys.executable,
            "-m",
            "rcfp_cli",
            "lambda-event",
            "-d",
            database,
            "--event",
            event,
        ).stdout
    )
    assert response["statusCode"] == 200 and response["isBase64Encoded"]
    assert decoded(base64.b64decode(response["body"])) == expected
    checks["lambda_event_binary_png"] = True
    palette = folder / "palettes"
    run(sys.executable, DOCS / "examples/custom_palette.py", palette)
    query = "/singleband/lagoon/red/preview.png?colormap=graycustom&stretch_range=%5B0%2C3000%5D"
    run(
        sys.executable,
        "-c",
        "import sys,rcfp; s,m,b,_=rcfp.request(sys.argv[1],'GET',sys.argv[2]); assert s==200 and m=='image/png'; open(sys.argv[3],'wb').write(b)",
        database,
        query,
        folder / "custom.png",
        env=dict(os.environ, TC_EXTRA_CMAP_FOLDER=str(palette)),
    )
    decoded((folder / "custom.png").read_bytes())
    checks["custom_palette"] = True
    optimized = folder / "optimized"
    run(
        sys.executable,
        "-m",
        "rcfp_cli",
        "optimize-rasters",
        DOCS / "examples/data/red.tif",
        "-o",
        optimized,
        "--compression",
        "deflate",
    )
    assert rcfp.compute_metadata(optimized / "red.tif")["range"] == metadata["range"]
    cli_catalog = folder / "ingested.sqlite"
    run(
        sys.executable,
        "-m",
        "rcfp_cli",
        "ingest",
        str(optimized / "{band}.tif"),
        "-o",
        cli_catalog,
    )
    status, _, body, _ = rcfp.request(cli_catalog, "GET", "/datasets")
    assert status == 200 and "red" in body.decode()
    checks["optimization_and_cli_ingestion"] = True
    if args.reference_python:
        checks["terracotta_roundtrip"] = reference_roundtrip(
            args.reference_python, folder
        )
    report["passed"] = True
    (folder / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--html", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--reference-python", type=Path)
    verify(parser.parse_args())
