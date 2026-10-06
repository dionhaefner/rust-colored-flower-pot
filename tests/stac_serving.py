"""Verify CLI STAC sources, tile HTTP contracts and the packaged map.

Raster expectations use checksum-pinned Sentinel pixels and independent GDAL
reads. Protocol responses and alternate time labels exercise selection behavior.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import copy
import hashlib
import io
import json
import math
import os
import shutil
import socket
import subprocess
import threading
import time
import traceback
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlencode, urlsplit

import numpy as np
from native_rendering import expected
from PIL import Image
from rasterio.enums import Resampling

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "docs/examples/data"


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def fetch(url, method="GET", headers=None):
    request = urllib.request.Request(url, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--browser", action="store_true")
    args = parser.parse_args()
    if args.wheel:
        import rcfp

        assert (
            Path(rcfp.__file__).resolve().is_relative_to(Path(os.sys.prefix).resolve())
        )
    binary, out = args.binary.resolve(), args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    rows = {
        row["id"]: row
        for row in json.loads((DATA / "manifest.json").read_text())["fixtures"]
    }
    for row in rows.values():
        assert (
            hashlib.sha256((DATA / row["file"]).read_bytes()).hexdigest()
            == row["sha256"]
        )
    fixture = json.loads((DATA / "lagoon-stac.json").read_text())
    records = []
    state = {}
    active_case = None

    def item(name="lagoon", key="red", href=None):
        value = copy.deepcopy(fixture)
        value["id"] = name
        asset = value["assets"].pop("red")
        asset["href"] = href or (DATA / rows[key]["file"]).as_uri()
        asset["file:checksum"] = "1220" + rows[key]["sha256"]
        value["assets"][key] = asset
        return value

    def write(name, value):
        path = active_case / name
        path.write_text(json.dumps(value))
        return str(path)

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, status, value):
            self.send_response(status)
            self.end_headers()
            self.wfile.write(json.dumps(value).encode())

        def do_POST(self):
            state["queries"].append(
                json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            )
            if (
                state.get("headers_file")
                and self.headers.get("Authorization") != "Bearer fresh-private"
            ):
                Path(state["headers_file"]).write_text(
                    json.dumps({"Authorization": "Bearer fresh-private"})
                )
                self.reply(401, {"secret": "private-upstream-body"})
                return
            if state.get("fail"):
                self.reply(500, {"secret": "private-upstream-body"})
                return
            page = state["queries"][-1].get("page", 1)
            values = state.get("items", [item("z"), item("a")])
            self.reply(
                200,
                {
                    "type": "FeatureCollection",
                    "features": values[:1] if page == 1 else values[1:],
                    "links": [
                        {
                            "rel": "next",
                            "href": "/search",
                            "method": "POST",
                            "body": {"page": 2},
                            "merge": True,
                        }
                    ]
                    if page == 1 and len(values) > 1
                    else [],
                },
            )

        def do_GET(self):
            parsed = urlsplit(self.path)
            if parsed.path in state.get("catalog_docs", {}):
                state.setdefault("catalog_gets", []).append(parsed.path)
                if (
                    state.get("catalog_auth") == parsed.path
                    and self.headers.get("Authorization") != "Bearer fresh-private"
                ):
                    Path(state["headers_file"]).write_text(
                        json.dumps({"Authorization": "Bearer fresh-private"})
                    )
                    self.reply(401, {"secret": "private-upstream-body"})
                    return
                self.reply(200, state["catalog_docs"][parsed.path])
                return
            if parsed.path == "/item.json":
                state["documents"] += 1
                self.reply(200, item(href="asset/red"))
            elif parsed.path.startswith("/asset/"):
                state["downloads"] += 1
                if (
                    state.get("asset_urls_file")
                    and parsed.query != "token=fresh-private"
                ):
                    Path(state["asset_urls_file"]).write_text(
                        json.dumps(
                            {
                                "sentinel/lagoon/red": provider_url
                                + "/asset/red?token=fresh-private"
                            }
                        )
                    )
                    self.reply(403, {"secret": "private-upstream-body"})
                else:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(
                        (DATA / (parsed.path.rsplit("/", 1)[1] + ".tif")).read_bytes()
                    )
            else:
                self.reply(404, {})

    provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    provider_url = f"http://127.0.0.1:{provider.server_port}"
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()

    @contextlib.contextmanager
    def serve(source, *options):
        address = f"127.0.0.1:{port()}"
        log_path = active_case / f"service-{address.split(':')[1]}.log"
        with log_path.open("w") as log:
            process = subprocess.Popen(
                [
                    str(binary),
                    "--compat-profile",
                    "native",
                    "serve",
                    *source,
                    "--bind",
                    address,
                    *options,
                ],
                stdout=log,
                stderr=log,
                env={
                    **os.environ,
                    "TC_RASTER_WORKERS": "2",
                    "TC_ALLOWED_ORIGINS_TILES": '["*"]',
                    "TC_REPROJECTION_METHOD": "linear",
                },
            )
            try:
                deadline = time.monotonic() + 20
                while True:
                    if process.poll() is not None:
                        raise AssertionError(log_path.read_text())
                    try:
                        if fetch(f"http://{address}/keys")[0] == 200:
                            break
                    except (urllib.error.URLError, TimeoutError):
                        pass
                    if time.monotonic() > deadline:
                        raise AssertionError("server did not start")
                    time.sleep(0.05)
                yield f"http://{address}"
            finally:
                process.terminate()
                process.wait(timeout=10)

    def describe(base):
        status, _, body = fetch(base + "/datasets")
        assert status == 200
        value = json.loads(body)
        assert "href" not in body.decode() and "private" not in body.decode()
        assets = []
        for dataset in value["datasets"]:
            keys = "/".join(
                quote(dataset[key], safe="")
                for key in ("collection", "datetime", "item", "asset")
            )
            assets.append(
                {
                    **dataset,
                    "metadata_url": "/metadata/" + keys,
                    "tile_url": "/singleband/"
                    + keys
                    + "/{z}/{x}/{y}.png?colormap=viridis&stretch_range=[0,2000]",
                    "preview_url": "/singleband/"
                    + keys
                    + "/preview.png?colormap=viridis&stretch_range=[0,2000]",
                }
            )
        return {
            "id": json.loads(fetch(base + "/__terracotta/identity")[2])[
                "source_revision"
            ],
            "assets": assets,
        }

    def metadata(base, index=0):
        status, _, body = fetch(base + describe(base)["assets"][index]["metadata_url"])
        assert status == 200, body
        assert "href" not in body.decode() and "private" not in body.decode()
        return json.loads(body)

    def preview(base, index=0):
        value = describe(base)
        status, headers, body = fetch(base + value["assets"][index]["preview_url"])
        assert status == 200, body
        assert headers["Content-Type"] == "image/png"
        return body

    def shared_preview(png):
        database = active_case / "preview.sqlite"
        subprocess.run(
            [
                str(binary),
                "ingest",
                str(DATA / "{asset}.tif"),
                "-o",
                str(database),
                "--format",
                "native",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        with serve(["--database", str(database)]) as base:
            status, _, sql_png = fetch(
                base
                + "/singleband/red/preview.png?colormap=viridis&stretch_range=[0,2000]"
            )
            assert status == 200 and png == sql_png
        assert np.count_nonzero(np.asarray(Image.open(io.BytesIO(png)))) > 100

    def reference(png, source, bounds, name):
        image = Image.open(io.BytesIO(png))
        values, valid = expected(
            source,
            bounds,
            [image.height, image.width],
            Resampling.bilinear,
            active_case / f"{name}.vrt",
        )
        indices = np.round(1 + np.clip(values / 2000, 0, 1) * 254).astype(np.uint8)
        indices[~valid] = 0
        actual = np.asarray(image)
        assert actual.shape == indices.shape and image.mode == "P"
        assert np.array_equal(actual == 0, ~valid), "coverage mismatch"
        assert np.max(np.abs(actual.astype(int) - indices.astype(int))) <= 1, (
            "display samples differ from independent GDAL read"
        )
        assert np.count_nonzero(actual) > 100

    def automatic_sources():
        documents = [
            item(),
            {"type": "FeatureCollection", "features": [item()]},
            catalog_document([{"rel": "item", "href": "observation"}]),
        ]
        write("observation", item())
        for index, document in enumerate(documents):
            source = write(f"source-{index}", document)
            with serve(["--source", source]) as base:
                assert len(describe(base)["assets"]) == 1
                shared_preview(preview(base))
        with serve(["--source", provider_url + "/item.json"]) as base:
            assert state["documents"] == 1 and state["downloads"] == 0
            shared_preview(preview(base))
            assert state["documents"] == 1 and state["downloads"] == 1

    def configured_search():
        state["items"] = [item("a"), item("z", "green")]
        for observation in state["items"]:
            next(iter(observation["assets"].values())).pop("file:checksum")
        revisions = {
            f"sentinel/{observation['id']}/{asset}": rows[asset]["sha256"]
            for observation in state["items"]
            for asset in observation["assets"]
        }
        write("revisions.json", revisions)
        state["headers_file"] = write(
            "headers.json", {"Authorization": "Bearer expired-private"}
        )
        query = {"bbox": [12, 45, 13, 46], "collections": ["sentinel"]}
        source = write(
            "source.json",
            {
                "type": "rcfp-source",
                "source": provider_url + "/search",
                "search": query,
                "options": {"headers": "headers.json", "revisions": "revisions.json"},
            },
        )
        with serve(["--source", source]) as base:
            assert state["queries"] == [
                {**query, "limit": 100},
                {**query, "limit": 100},
                {**query, "limit": 100, "page": 2},
            ]
            assert len(describe(base)["assets"]) == 2
            status, _, body = fetch(
                base + "/datasets?item=z&order_by=item&ascending=false"
            )
            assert status == 200 and json.loads(body)["datasets"][0]["item"] == "z"
            assert preview(base, 0) != preview(base, 1)
            assert len(state["queries"]) == 3
        state.pop("headers_file")
        state["queries"].clear()
        source = write(
            "all.json",
            {
                "type": "rcfp-source",
                "source": provider_url + "/search",
                "search": {},
                "options": {"revisions": "revisions.json"},
            },
        )
        with serve(["--source", source]) as base:
            assert len(describe(base)["assets"]) == 2
            assert state["queries"] == [{"limit": 100}, {"limit": 100, "page": 2}]

    def configured_assets():
        state["asset_urls_file"] = write(
            "asset-urls.json",
            {"sentinel/lagoon/red": provider_url + "/asset/red?token=expired-private"},
        )
        write("item.json", item(href="s3://private-bucket/red.tif"))
        source = write(
            "source.json",
            {
                "type": "rcfp-source",
                "source": "item.json",
                "options": {"asset_urls": "asset-urls.json", "assets": ["red"]},
            },
        )
        with serve(["--source", source]) as base:
            shared_preview(preview(base))
            assert state["downloads"] == 2
            assert preview(base) == preview(base) and state["downloads"] == 2

    def sql_source_detection():
        database = active_case / "catalog.json"
        subprocess.run(
            [
                str(binary),
                "ingest",
                str(DATA / "{asset}.tif"),
                "-o",
                str(database),
                "--format",
                "native",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        source = write("source.json", {"type": "rcfp-source", "source": "catalog.json"})
        original = None
        for location in (str(database), database.as_uri(), source):
            with serve(["--source", location]) as base:
                status, _, body = fetch(base + "/datasets?asset=red")
                assert status == 200 and json.loads(body)["datasets"] == [
                    {"asset": "red"}
                ]
                status, _, body = fetch(
                    base
                    + "/singleband/red/preview.png?stretch_range=[0,2000]&colormap=viridis"
                )
                assert status == 200
                original = original or body
                assert body == original

    def shared_application_adapters():
        import rcfp
        from rcfp.wsgi import Application

        source = write(
            "source.json",
            {
                "type": "rcfp-source",
                "source": str(DATA / "lagoon-stac.json"),
            },
        )
        settings = {"RASTER_RESAMPLING_METHOD": "bilinear"}
        app = rcfp.NativeApplication(source, settings=settings)
        status, _, body, _, _ = app.request("GET", "/datasets")
        assert status == 200
        dataset = json.loads(body)["datasets"][0]
        keys = [dataset[key] for key in ("collection", "datetime", "item", "asset")]
        path = (
            "/singleband/"
            + "/".join(quote(key, safe="") for key in keys)
            + "/preview.png"
        )
        query = urlencode({"stretch_range": "[0,2000]", "colormap": "viridis"})
        status, _, expected_png, _, _ = app.request("GET", path + "?" + query)
        assert status == 200
        assert rcfp.request(source, "GET", "/datasets")[2] == body
        assert rcfp.request_full(source, "GET", "/datasets")[2] == body
        assert (
            rcfp.render_preview(
                source, keys, stretch_range=[0, 2000], colormap="viridis"
            )
            == expected_png
        )
        response = []
        wsgi = Application(source, settings=settings)
        png = b"".join(
            wsgi(
                {
                    "REQUEST_METHOD": "GET",
                    "PATH_INFO": unquote(path),
                    "QUERY_STRING": query,
                    "wsgi.input": io.BytesIO(),
                },
                lambda status, headers: response.append((status, headers)),
            )
        )
        assert response[0][0] == "200 OK", (response, png)
        assert png == expected_png
        event = write(
            "event.json", {"httpMethod": "GET", "path": path, "rawQueryString": query}
        )
        result = subprocess.run(
            [str(binary), "lambda-event", "--source", source, "--event", event],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "TC_REPROJECTION_METHOD": "linear"},
        )
        value = json.loads(result.stdout)
        assert value["statusCode"] == 200 and value["isBase64Encoded"]
        assert base64.b64decode(value["body"]) == expected_png

    def invalid_configurations():
        documents = [
            (
                {"type": "rcfp-source", "source": "missing", "secret": "private"},
                "configuration",
            ),
            (
                {
                    "type": "rcfp-source",
                    "source": "missing",
                    "options": {"secret": "private"},
                },
                "configuration",
            ),
            (
                {
                    "type": "rcfp-source",
                    "source": provider_url + "/search",
                    "search": [],
                },
                "JSON object",
            ),
            ({"type": "rcfp-source", "source": "source.json"}, "nesting limit"),
            (
                {
                    "type": "rcfp-source",
                    "source": str(DATA / "lagoon-stac.json"),
                    "options": {"max_items": 0},
                },
                "limits",
            ),
        ]
        for document, message in documents:
            source = write("source.json", document)
            result = subprocess.run(
                [str(binary), "serve", "--source", source],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert result.returncode != 0 and message in result.stderr, result.stderr
            assert "private" not in result.stderr
        source = (DATA / "lagoon-stac.json").as_uri() + "?secret=private"
        result = subprocess.run(
            [str(binary), "serve", "--source", source],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode != 0 and "queries or fragments" in result.stderr
        assert "private" not in result.stderr
        state["catalog_docs"] = {
            "/config.json": {"type": "rcfp-source", "source": "private"}
        }
        result = subprocess.run(
            [str(binary), "serve", "--source", provider_url + "/config.json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode != 0 and "source document" in result.stderr
        assert "private" not in result.stderr

    def local_item():
        with serve(["--stac-item", str(DATA / "lagoon-stac.json")]) as base:
            value = describe(base)
            assert len(value["assets"]) == 1
            identity = json.loads(fetch(base + "/__terracotta/identity")[2])
            assert identity["source_revision"] == value["id"]
            shared_preview(preview(base))
            # A z=13 tile intersects the same real crop and includes transparent coverage.
            tile = (
                value["assets"][0]["tile_url"]
                .replace("{z}", "13")
                .replace("{x}", "4377")
                .replace("{y}", "2932")
            )
            status, _, png = fetch(base + tile)
            assert status == 200
            half = math.pi * 6378137
            span = 2 * half / 2**13
            bounds = [
                -half + 4377 * span,
                half - 2933 * span,
                -half + 4378 * span,
                half - 2932 * span,
            ]
            reference(png, DATA / "red.tif", bounds, "tile")

    def item_collection():
        collection = write(
            "items.json",
            {"type": "FeatureCollection", "features": [item("z", "green"), item("a")]},
        )
        with serve(["--stac-item", collection]) as base:
            value = describe(base)
            assert [a["item"] for a in value["assets"]] == ["a", "z"]
            assert preview(base, 0) != preview(base, 1)

    def search_filters_and_reuse():
        with serve(
            ["--stac-search", provider_url + "/search"],
            "--stac-bbox",
            "12.3,45.4,12.4,45.5",
            "--stac-datetime",
            "2021-06-29T00:00:00Z/2021-06-30T00:00:00Z",
            "--stac-collections",
            "sentinel",
        ) as base:
            value = describe(base)
            assert [a["item"] for a in value["assets"]] == ["a", "z"]
            details = metadata(base)["metadata"]
            assert details["search_id"] and details["selection_metrics"]["pages"] == 2
            assert state["queries"][0]["bbox"] == [12.3, 45.4, 12.4, 45.5]
            assert state["queries"][0]["collections"] == ["sentinel"]
            assert state["queries"][0]["datetime"].startswith("2021-06-29")
            preview(base)
            preview(base, 1)
            assert len(state["queries"]) == 2

    def catalog_document(links, kind="Catalog", name="catalog"):
        return {
            "type": kind,
            "stac_version": "1.0.0",
            "id": name,
            "description": "Test discovery graph",
            "links": links,
        }

    def local_catalog():
        (active_case / "nested").mkdir()
        (active_case / "items").mkdir()
        write("items/a.json", item("a"))
        write("items/z.json", item("z", "green"))
        write(
            "nested/collection.json",
            catalog_document(
                [
                    {"rel": "item", "href": "../items/z.json"},
                    {"rel": "item", "href": "../items/a.json"},
                    {"rel": "child", "href": "../catalog.json"},
                    {"rel": "parent", "href": "unavailable.json"},
                ],
                "Collection",
                "sentinel",
            ),
        )
        source = write(
            "catalog.json",
            catalog_document(
                [
                    {"rel": "child", "href": "nested/collection.json"},
                    {"rel": "child", "href": "./nested/../nested/collection.json"},
                    {"rel": "item", "href": "items/z.json"},
                ]
            ),
        )
        with serve(["--source", source]) as base:
            value = describe(base)
            assert [a["item"] for a in value["assets"]] == ["a", "z"]
            details = metadata(base)["metadata"]["selection_metrics"]
            assert details["pages"] == 4
            assert details["returned_items"] == 2
            original = preview(base)
            # Catalog edits affect new startup selections, never resolved tile requests.
            Path(source).write_text("invalid-json")
            assert preview(base) == original
            assert preview(base, 1) != original

    def remote_catalog():
        remote_item = item(href="../asset/red")
        remote_item["links"] = [
            {"rel": "self", "href": "https://published.example/item.json"}
        ]
        state["catalog_docs"] = {
            "/catalog.json": catalog_document(
                [{"rel": "child", "href": "nested/collection.json"}]
            ),
            "/nested/collection.json": catalog_document(
                [{"rel": "item", "href": "../items/a.json"}], "Collection"
            ),
            "/items/a.json": remote_item,
        }
        with serve(["--source", provider_url + "/catalog.json"]) as base:
            assert state["downloads"] == 0 and len(state["catalog_gets"]) == 3
            shared_preview(preview(base))
            assert preview(base) == preview(base)
            assert state["downloads"] == 1 and len(state["catalog_gets"]) == 3

    def catalog_credential_refresh():
        state["headers_file"] = write(
            "headers.json", {"Authorization": "Bearer expired-private"}
        )
        state["catalog_auth"] = "/items/a.json"
        state["catalog_docs"] = {
            "/catalog.json": catalog_document(
                [{"rel": "item", "href": "items/a.json"}]
            ),
            "/items/a.json": item(),
        }
        with serve(
            ["--stac-catalog", provider_url + "/catalog.json"],
            "--stac-headers",
            state["headers_file"],
        ) as base:
            assert state["catalog_gets"] == [
                "/catalog.json",
                "/items/a.json",
                "/catalog.json",
                "/items/a.json",
            ]
            assert len(preview(base)) > 100
        for path in active_case.glob("service*.log"):
            assert "private" not in path.read_text()

    def catalog_errors_and_limits():
        source = write(
            "catalog.json",
            catalog_document(
                [{"rel": "item", "href": "a.json"}, {"rel": "item", "href": "b.json"}]
            ),
        )
        write("a.json", item("a"))
        write("b.json", item("b"))
        standard = [
            "serve",
            "--stac-catalog",
            source,
            "--stac-assets",
            "red",
        ]
        for options, message in [
            (["--stac-max-items", "1"], "result limit"),
            (["--stac-max-documents", "2"], "document limit"),
            (["--stac-max-documents", "0"], "between 1 and 20000"),
            (["--stac-max-documents", "20001"], "between 1 and 20000"),
            (["--stac-item", str(DATA / "lagoon-stac.json")], "Choose one"),
            (["--stac-bbox", "1,2,3,4"], "require --stac-search"),
        ]:
            result = subprocess.run(
                [str(binary), *standard, *options],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert result.returncode != 0 and message in result.stderr, result.stderr
        for value, message in [
            (
                catalog_document([{"rel": "item", "href": "missing.json"}]),
                "unavailable",
            ),
            (catalog_document([{"rel": "child", "href": "a.json"}]), "document type"),
            (
                catalog_document(
                    [
                        {
                            "rel": "item",
                            "href": "a.json",
                            "headers": {"Authorization": "private"},
                        }
                    ]
                ),
                "link-supplied headers",
            ),
            (item(), "document type"),
            (
                {
                    "type": "Catalog",
                    "stac_version": "2.0.0",
                    "id": "unsupported",
                    "links": [],
                },
                "version",
            ),
        ]:
            Path(source).write_text(json.dumps(value))
            result = subprocess.run(
                [str(binary), *standard],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert result.returncode != 0 and message in result.stderr, result.stderr
            assert "private" not in result.stderr

    def catalog_origin_and_failure():
        endpoint = provider_url + "/catalog.json"
        standard = [
            "serve",
            "--stac-catalog",
            endpoint,
            "--stac-assets",
            "red",
        ]
        for href, message in [
            ("https://elsewhere.example/item.json?token=private", "crosses origin"),
            ((DATA / "lagoon-stac.json").as_uri(), "HTTPS"),
            ("/missing.json", "HTTP 404"),
        ]:
            state["catalog_docs"] = {
                "/catalog.json": catalog_document([{"rel": "item", "href": href}])
            }
            result = subprocess.run(
                [str(binary), *standard],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert result.returncode != 0 and message in result.stderr, result.stderr
            assert state["downloads"] == 0 and "private" not in result.stderr

    def catalog_empty_and_relative_assets():
        root = write("empty.json", catalog_document([]))
        with serve(["--stac-catalog", root]) as base:
            assert describe(base)["assets"] == []
        shutil.copyfile(DATA / "red.tif", active_case / "relative.tif")
        write("a.json", item(href="relative.tif"))
        root = write(
            "catalog.json", catalog_document([{"rel": "item", "href": "a.json"}])
        )
        with serve(["--stac-catalog", root]) as base:
            assert len(preview(base)) > 100

    def catalog_byte_limits():
        root = write("catalog.json", catalog_document([]))
        standard = [
            str(binary),
            "serve",
            "--stac-catalog",
            root,
            "--stac-assets",
            "red",
        ]
        # JSON whitespace exercises ingress budgets without synthetic raster samples.
        Path(root).write_text(" " * (8 * 1024 * 1024 + 1))
        result = subprocess.run(
            standard, check=False, capture_output=True, text=True, timeout=10
        )
        assert result.returncode != 0 and "document exceeds byte limit" in result.stderr
        Path(root).write_text(
            json.dumps(
                catalog_document(
                    [
                        {"rel": "child", "href": f"child-{index}.json"}
                        for index in range(4)
                    ]
                )
            )
        )
        document = json.dumps(catalog_document([]))
        for index in range(4):
            (active_case / f"child-{index}.json").write_text(
                document + " " * (8 * 1024 * 1024 - len(document))
            )
        result = subprocess.run(
            standard, check=False, capture_output=True, text=True, timeout=10
        )
        assert result.returncode != 0 and "catalog response byte limit" in result.stderr

    def search_entire_catalog():
        with serve(["--stac-search", provider_url + "/search"]) as base:
            value = describe(base)
            assert len(value["assets"]) == 2
            assert not any(
                key in state["queries"][0]
                for key in ("bbox", "datetime", "collections")
            )
            assert preview(base, 0) == preview(base, 1)
            assert len(state["queries"]) == 2

    def remote_item():
        with serve(["--stac-item", provider_url + "/item.json"]) as base:
            assert state["documents"] == 1 and state["downloads"] == 0
            assert preview(base) == preview(base)
            assert state["documents"] == 1 and state["downloads"] == 1

    def private_refresh():
        state["headers_file"] = write(
            "headers.json", {"Authorization": "Bearer expired-private"}
        )
        state["asset_urls_file"] = write(
            "urls.json",
            {"sentinel/lagoon/red": provider_url + "/asset/red?token=expired-private"},
        )
        state["items"] = [item(href="s3://private-bucket/red")]
        with serve(
            ["--stac-search", provider_url + "/search"],
            "--stac-headers",
            state["headers_file"],
            "--stac-asset-urls",
            state["asset_urls_file"],
        ) as base:
            assert len(state["queries"]) == 2 and state["downloads"] == 0
            first = preview(base)
            assert state["downloads"] == 2
            Path(state["asset_urls_file"]).write_text("invalid-json")
            assert preview(base) == first and state["downloads"] == 2
        for path in active_case.glob("service*.log"):
            assert "private" not in path.read_text()

    def route_contracts():
        with serve(["--stac-item", str(DATA / "lagoon-stac.json")]) as base:
            asset = describe(base)["assets"][0]
            assert (
                metadata(base)["metadata"]["stac"]["revision"] == rows["red"]["sha256"]
            )
            assert [
                key["key"] for key in json.loads(fetch(base + "/keys")[2])["keys"]
            ] == ["collection", "datetime", "item", "asset"]
            assert fetch(base + asset["metadata_url"] + "?unknown=1")[0] == 400
            assert fetch(base + asset["metadata_url"], "POST")[0] == 405
            assert fetch(base + asset["metadata_url"], "OPTIONS")[0] == 200
            assert fetch(base + "/datasets", "HEAD")[2] == b""
            status, headers, body = fetch(base + asset["preview_url"], "HEAD")
            assert (
                status == 200 and body == b"" and int(headers["Content-Length"]) > 100
            )
            for path in ("/keys", asset["metadata_url"], asset["preview_url"]):
                assert (
                    fetch(base + path, headers={"Origin": "http://localhost:9999"})[1][
                        "Access-Control-Allow-Origin"
                    ]
                    == "http://localhost:9999"
                )
            spec = json.loads(fetch(base + "/swagger.json")[2])
            assert any(route.startswith("/singleband/") for route in spec["paths"])
            keys = {
                key: asset[key] for key in ("collection", "datetime", "item", "asset")
            }
            request = urllib.request.Request(
                base + "/metadata",
                data=json.dumps({"keys": [list(keys.values())]}).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                assert json.load(response)[0] == metadata(base)

    def empty_selection():
        source = write("empty.json", {"type": "FeatureCollection", "features": []})
        with serve(["--stac-item", source]) as base:
            assert describe(base)["assets"] == []

    def revision_override():
        value = item()
        del value["assets"]["red"]["file:checksum"]
        source = write("item.json", value)
        revisions = write(
            "revisions.json", {"sentinel/lagoon/red": rows["red"]["sha256"]}
        )
        with serve(["--stac-item", source], "--stac-revisions", revisions) as base:
            assert len(preview(base)) > 100

    def immutable_revision():
        source = active_case / "mutable.tif"
        shutil.copyfile(DATA / "red.tif", source)
        document = write("item.json", item(href=source.as_uri()))
        with serve(["--stac-item", document]) as old:
            original, identity = preview(old), describe(old)["id"]
            shutil.copyfile(DATA / "green.tif", source)
            assert preview(old) == original
            write("item.json", item(key="green", href=source.as_uri()))
            with serve(["--stac-item", document]) as new:
                assert describe(new)["id"] != identity and preview(new) != original

    def rendering_failures():
        bad = item()
        bad["assets"]["red"]["file:checksum"] = "1220" + "0" * 64
        for source, options, message in [
            (write("bad.json", bad), [], "checksum mismatch"),
            (
                str(DATA / "lagoon-stac.json"),
                ["--stac-max-staged-bytes", "100"],
                "byte limit",
            ),
        ]:
            with serve(["--stac-item", source], *options) as base:
                path = describe(base)["assets"][0]["preview_url"]
                status, _, body = fetch(base + path)
                assert status == 400 and message in body.decode(), (status, body)

    def invalid_startup():
        standard = [
            "serve",
            "--stac-item",
            str(DATA / "lagoon-stac.json"),
            "--stac-assets",
            "red",
        ]
        for options, message in [
            (standard + ["-d", "unused.sqlite"], "Choose one"),
            (standard + ["--stac-bbox", "1,2,3,4"], "require --stac-search"),
            (standard + ["--stac-assets", "red,red"], "duplicate"),
            (standard + ["--stac-max-items", "0"], "limits"),
            (["--compat-profile", "terracotta-0.10.1", *standard], "native"),
            (standard + ["--stac-search", provider_url + "/search"], "Choose one"),
        ]:
            result = subprocess.run(
                [str(binary), *options],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert result.returncode != 0 and message in result.stderr, result.stderr

    def discovery_failures():
        source = [
            "serve",
            "--stac-search",
            provider_url + "/search",
            "--stac-assets",
            "red",
        ]
        for options, mode, message in [
            (["--stac-max-items", "1"], False, "limit"),
            ([], True, "HTTP 500"),
        ]:
            state["fail"] = mode
            result = subprocess.run(
                [str(binary), *source, *options],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert result.returncode != 0 and message in result.stderr
            assert "private" not in result.stderr

    def common_rendering():
        import rasterio
        from native_rendering import bounds_for_tile

        with serve(
            ["--stac-item", str(DATA / "lagoon-multispectral-stac.json")]
        ) as base:
            assert len(describe(base)["assets"]) == 4
            route = "/rgb/sentinel/2021-06-29T10%3A00%3A00Z/lagoon/preview.png?r=red&g=green&b=blue"
            status, _, png = fetch(base + route)
            assert status == 200 and Image.open(io.BytesIO(png)).width == 256
        value = item()
        for key in ("green", "blue", "nir"):
            value["assets"].update(item(key=key)["assets"])
        value["assets"]["thumbnail"] = {"href": "thumbnail.png", "type": "image/png"}
        value["assets"]["red"]["raster:bands"][0].update(scale=0.1, offset=-2)
        value["assets"]["green"]["raster:bands"][0].update(scale=-0.1, offset=198)
        source = write("multispectral.json", value)
        prefix = "/sentinel/2021-06-29T10%3A00%3A00Z/lagoon"
        bounds = bounds_for_tile(4377, 2932, 13)
        with serve(["--stac-item", source]) as base:
            datasets = json.loads(fetch(base + "/datasets")[2])["datasets"]
            assert [d["asset"] for d in datasets] == ["blue", "green", "nir", "red"]
            filtered = json.loads(
                fetch(
                    base
                    + "/datasets?"
                    + urlencode(
                        {
                            "asset": "[red,green]",
                            "order_by": "asset",
                            "ascending": "false",
                            "limit": "1",
                            "page": "1",
                        }
                    )
                )[2]
            )["datasets"]
            assert [d["asset"] for d in filtered] == ["green"]
            m = json.loads(fetch(base + "/metadata" + prefix + "/red")[2])
            with rasterio.open(DATA / "red.tif") as src:
                samples = src.read(1, masked=True).compressed().astype(float) * 0.1 - 2
                assert np.allclose(m["range"], [samples.min(), samples.max()])
                assert np.isclose(m["mean"], samples.mean())
                assert np.isclose(m["stdev"], samples.std())
            negative = json.loads(fetch(base + "/metadata" + prefix + "/green")[2])
            assert negative["range"][0] <= negative["range"][1]
            assert negative["percentiles"] == sorted(negative["percentiles"])
            raw, valid = expected(
                DATA / "red.tif",
                bounds,
                [256, 256],
                Resampling.bilinear,
                active_case / "red.vrt",
            )
            scaled = raw * 0.1 - 2
            status, _, png = fetch(
                base
                + "/singleband"
                + prefix
                + "/red/13/4377/2932.png?"
                + urlencode({"stretch_range": "[-2,198]", "colormap": "viridis"})
            )
            assert status == 200, png
            indices = np.round(1 + np.clip((scaled + 2) / 200, 0, 1) * 254).astype(
                np.uint8
            )
            indices[~valid] = 0
            assert (
                np.max(
                    np.abs(
                        np.asarray(Image.open(io.BytesIO(png))).astype(int)
                        - indices.astype(int)
                    )
                )
                <= 1
            )
            query = {
                "r": "red",
                "g": "green",
                "b": "blue",
                "r_range": "[-2,198]",
                "g_range": "[-2,198]",
                "b_range": "[0,2000]",
            }
            status, _, png = fetch(
                base + "/rgb" + prefix + "/13/4377/2932.png?" + urlencode(query)
            )
            assert status == 200, png
            rgb = np.asarray(Image.open(io.BytesIO(png)).convert("RGBA"))
            expected_channels = []
            coverage = valid.copy()
            for key, scale, offset, stretch in [
                ("red", 0.1, -2, [-2, 198]),
                ("green", -0.1, 198, [-2, 198]),
                ("blue", 1, 0, [0, 2000]),
            ]:
                pixels, mask = expected(
                    DATA / (key + ".tif"),
                    bounds,
                    [256, 256],
                    Resampling.bilinear,
                    active_case / (key + "-rgb.vrt"),
                )
                coverage &= mask
                expected_channels.append(
                    np.round(
                        1
                        + np.clip(
                            (pixels * scale + offset - stretch[0])
                            / (stretch[1] - stretch[0]),
                            0,
                            1,
                        )
                        * 254
                    ).astype(np.uint8)
                )
            assert np.array_equal(rgb[:, :, 3] != 0, coverage)
            assert (
                np.max(
                    np.abs(
                        rgb[:, :, :3][coverage].astype(int)
                        - np.stack(expected_channels, axis=2)[coverage].astype(int)
                    )
                )
                <= 1
            )
            status, _, png = fetch(
                base
                + "/compute"
                + prefix
                + "/13/4377/2932.png?"
                + urlencode(
                    {
                        "v1": "red",
                        "expression": "v1",
                        "stretch_range": "[-2,198]",
                        "colormap": "viridis",
                    }
                )
            )
            assert status == 200, png
            assert (
                np.max(
                    np.abs(
                        np.asarray(Image.open(io.BytesIO(png))).astype(int)
                        - indices.astype(int)
                    )
                )
                <= 1
            )
            status, _, default = fetch(
                base + "/singleband" + prefix + "/red/preview.png"
            )
            status2, _, explicit = fetch(
                base
                + "/singleband"
                + prefix
                + "/red/preview.png?"
                + urlencode({"stretch_range": json.dumps(m["range"])})
            )
            assert status == status2 == 200 and default == explicit
        with serve(["--stac-item", source], "--stac-assets", "red") as base:
            assert [
                d["asset"] for d in json.loads(fetch(base + "/datasets")[2])["datasets"]
            ] == ["red"]

        value["assets"]["green"] = copy.deepcopy(value["assets"]["red"])
        value["assets"]["green"]["raster:bands"][0].update(scale=-0.1, offset=198)
        source = write("shared-revision.json", value)
        with serve(["--stac-item", source]) as base:
            single = (
                base
                + "/singleband"
                + prefix
                + "/red/13/4377/2932.png?"
                + urlencode({"stretch_range": "[-2,198]"})
            )
            before = fetch(single)[2]
            status, _, png = fetch(
                base + "/rgb" + prefix + "/13/4377/2932.png?" + urlencode(query)
            )
            assert status == 200
            rgb = np.asarray(Image.open(io.BytesIO(png)).convert("RGBA"))
            visible = rgb[:, :, 3] != 0
            assert (
                np.max(
                    np.abs(
                        rgb[:, :, 0][visible].astype(int)
                        + rgb[:, :, 1][visible].astype(int)
                        - 256
                    )
                )
                <= 1
            )
            assert fetch(single)[2] == before

    def multiband_requests():
        import rasterio

        path = active_case / "packed.tif"
        with (
            rasterio.open(DATA / "red.tif") as red,
            rasterio.open(DATA / "green.tif") as green,
            rasterio.open(path, "w", **dict(red.profile, count=2)) as target,
        ):
            target.write(red.read(1), 1)
            target.write(green.read(1), 2)
        value = item(href=path.as_uri())
        value["assets"]["red"]["file:checksum"] = (
            "1220" + hashlib.sha256(path.read_bytes()).hexdigest()
        )
        value["assets"]["red"]["raster:bands"].append(
            {"nodata": 0, "scale": 0.1, "offset": -2}
        )
        value["assets"]["red"]["eo:bands"] = [
            {"common_name": "red"},
            {"common_name": "green"},
        ]
        source = write("packed.json", value)
        prefix = "/sentinel/2021-06-29T10%3A00%3A00Z/lagoon"
        with serve(["--stac-item", source]) as base:
            path = "/singleband" + prefix + "/green/13/4377/2932.png?"
            query = {"stretch_range": "[-2,198]", "colormap": "viridis"}
            status, _, png = fetch(base + path + urlencode(query))
            assert status == 200, png
            from native_rendering import bounds_for_tile

            reference(
                png, DATA / "green.tif", bounds_for_tile(4377, 2932, 13), "packed-band"
            )
            assert fetch(base + path + urlencode({**query, "band": 3}))[0] == 400
            assert fetch(base + path + "band=2")[0] == 400
            status, _, png = fetch(
                base
                + "/rgb"
                + prefix
                + "/13/4377/2932.png?"
                + urlencode(
                    {
                        "r": "red",
                        "g": "green",
                        "b": "red",
                        "r_range": "[0,2000]",
                        "g_range": "[-2,198]",
                        "b_range": "[0,2000]",
                    }
                )
            )
            assert status == 200 and Image.open(io.BytesIO(png)).mode == "RGB", status

    def collection_keys():
        values = [item("a"), item("b"), item("c")]
        del values[0]["collection"]
        values[1]["collection"] = "~"
        values[2]["collection"] = "c / with space"
        values[2]["id"] = "item/with space"
        source = write("items.json", {"type": "FeatureCollection", "features": values})
        with serve(["--stac-item", source]) as base:
            assets = describe(base)["assets"]
            assert {a["collection"] for a in assets} == {"~", "~~", "c / with space"}
            for index in range(3):
                assert len(preview(base, index)) > 100

    def browser_workflow():
        from playwright.sync_api import expect, sync_playwright

        observation = item("a")
        for key in ("green", "blue"):
            observation["assets"].update(item("a", key)["assets"])
        write("a.json", observation)
        write("b.json", item("b", "green"))
        source = write(
            "catalog.json",
            catalog_document(
                [{"rel": "item", "href": "a.json"}, {"rel": "item", "href": "b.json"}]
            ),
        )
        with serve(["--source", source]) as base, sync_playwright() as playwright:
            client_url = f"http://127.0.0.1:{port()}"
            with (active_case / "client.log").open("w") as log:
                process = subprocess.Popen(
                    [
                        str(binary),
                        "connect",
                        base,
                        "--port",
                        client_url.rsplit(":", 1)[1],
                        "--no-browser",
                    ],
                    stdout=log,
                    stderr=log,
                )
                browser = playwright.chromium.launch(
                    headless=True, args=["--no-sandbox"]
                )
                try:
                    deadline = time.monotonic() + 20
                    while True:
                        try:
                            if fetch(client_url)[0] == 200:
                                break
                        except urllib.error.URLError:
                            pass
                        assert process.poll() is None and time.monotonic() < deadline
                        time.sleep(0.05)
                    context = browser.new_context(
                        viewport={"width": 1440, "height": 1000}
                    )
                    external, errors, responses = [], [], []

                    def only_local(route):
                        if route.request.url.startswith(
                            (base, client_url, "data:", "blob:")
                        ):
                            route.continue_()
                        else:
                            external.append(route.request.url)
                            route.abort()

                    context.route("**/*", only_local)
                    page = context.new_page()
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.on(
                        "response",
                        lambda response: (
                            responses.append(
                                {"url": response.url, "status": response.status}
                            )
                            if response.url.startswith(base)
                            else None
                        ),
                    )
                    page.goto(client_url)
                    expect(page.locator("#metadata")).to_contain_text("selection_id")
                    expect(page.locator(".leaflet-tile-loaded").first).to_be_visible()
                    assert page.locator("#dataset option").count() == 4
                    page.get_by_label("Dataset", exact=True).select_option(index=2)
                    expect(page.locator("#metadata")).to_contain_text(
                        rows["red"]["sha256"]
                    )
                    assert page.locator("#render-form").is_visible()
                    assert (
                        json.loads(page.locator("#metadata").text_content())[
                            "metadata"
                        ]["stac"]["revision"]
                        == rows["red"]["sha256"]
                    )
                    page.get_by_role("button", name="Inspect map center").click()
                    expect(page.locator("#inspection")).to_contain_text("Display RGBA:")
                    page.screenshot(path=str(active_case / "red.png"))
                    page.get_by_label("Dataset", exact=True).select_option(index=3)
                    expect(page.locator("#metadata")).to_contain_text("green")
                    expect(page.locator(".leaflet-tile-loaded").first).to_be_visible()
                    page.locator(".leaflet-control-zoom-in").click()
                    expect(page.locator(".leaflet-tile-loaded").first).to_be_visible()
                    page.screenshot(path=str(active_case / "green.png"))
                    page.get_by_label("Dataset", exact=True).select_option(index=2)
                    page.get_by_label("Rendering", exact=True).select_option("rgb")
                    for label, key in (
                        ("Red", "red"),
                        ("Green", "green"),
                        ("Blue", "blue"),
                    ):
                        page.get_by_label(label, exact=True).select_option(key)
                    with page.expect_response(
                        lambda response: (
                            "/rgb/" in response.url and response.status == 200
                        )
                    ):
                        page.get_by_role("button", name="Apply rendering").click()
                    page.get_by_role("button", name="Create share link").click()
                    expect(page.locator("#share-link")).not_to_have_value("")
                    page.reload()
                    expect(page.locator("#mode")).to_have_value("rgb")
                    expect(page.locator(".leaflet-tile-loaded").first).to_be_visible()
                    page.screenshot(path=str(active_case / "rgb.png"))
                    assert not errors and not external
                    assert all(response["status"] == 200 for response in responses), (
                        responses
                    )
                    assert any(
                        "/singleband/" in response["url"] for response in responses
                    )
                    (active_case / "requests.json").write_text(
                        json.dumps(responses, indent=2) + "\n"
                    )
                    context.close()
                finally:
                    browser.close()
                    process.terminate()
                    process.wait(timeout=10)

    try:
        cases = [
            automatic_sources,
            configured_search,
            configured_assets,
            sql_source_detection,
            shared_application_adapters,
            invalid_configurations,
            common_rendering,
            multiband_requests,
            collection_keys,
            local_item,
            item_collection,
            local_catalog,
            remote_catalog,
            catalog_credential_refresh,
            catalog_errors_and_limits,
            catalog_origin_and_failure,
            catalog_empty_and_relative_assets,
            catalog_byte_limits,
            search_entire_catalog,
            search_filters_and_reuse,
            remote_item,
            private_refresh,
            route_contracts,
            empty_selection,
            revision_override,
            immutable_revision,
            rendering_failures,
            invalid_startup,
            discovery_failures,
        ]
        if args.browser:
            cases.append(browser_workflow)
        for case in cases:
            active_case = out / case.__name__
            active_case.mkdir()
            state.clear()
            state.update(queries=[], downloads=0, documents=0)
            started = time.monotonic()
            record = {"name": case.__name__, "status": "passed"}
            try:
                case()
            except Exception as error:  # noqa: BLE001 - retain failures with evidence
                record.update(
                    status="failed", error=str(error), traceback=traceback.format_exc()
                )
            record["seconds"] = time.monotonic() - started
            records.append(record)
            print(f"{record['status']}: {case.__name__}", flush=True)
    finally:
        provider.shutdown()
        provider.server_close()
        thread.join()
    report = {
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "wheel_sha256": hashlib.sha256(args.wheel.read_bytes()).hexdigest()
        if args.wheel
        else None,
        "cases": records,
        "skips": [] if args.browser else ["Chromium viewer workflow was not requested"],
        "excluded": [
            "Hosted provider and WAN performance",
            "Cloud SDK authentication/signing",
            "Other operating systems and architectures",
        ],
    }
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    if any(record["status"] != "passed" for record in records):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
