"""Installed STAC provider, revision, credential and real-raster rendering checks.

The local provider implements the supported STAC API wire contract. Its indexed
SQLite size experiment measures selection separately; it does not qualify a
hosted PgSTAC service. Raster bytes are checksum-pinned Sentinel/WorldCover crops.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import os
import shutil
import sqlite3
import statistics
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import numpy as np
import rasterio
from native_rendering import expected
from PIL import Image
from rasterio.enums import Resampling
from rasterio.warp import transform_bounds

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--wheel", type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    import rcfp
    from rcfp.stac import SearchClient, Selection

    assert Path(rcfp.__file__).resolve().is_relative_to(Path(os.sys.prefix).resolve())
    rows = json.loads((ROOT / "docs/examples/data/manifest.json").read_text())[
        "fixtures"
    ]
    fixtures = {r["id"]: ROOT / "docs/examples/data" / r["file"] for r in rows}
    for row in rows:
        assert (
            hashlib.sha256(fixtures[row["id"]].read_bytes()).hexdigest()
            == row["sha256"]
        )
    report = []
    performance = []

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def respond(self, status, value):
            self.send_response(status)
            self.send_header("Content-Type", "application/geo+json")
            self.end_headers()
            self.wfile.write(json.dumps(value).encode())

        def do_GET(self):
            parsed = urlsplit(self.path)
            if parsed.path.startswith("/asset/"):
                state["asset_gets"] += 1
                token = parse_qs(parsed.query).get("token", [""])[0]
                if token != "fresh-private":
                    self.respond(403, {"secret": "expired-private"})
                    return
                data = fixtures[parsed.path.rsplit("/", 1)[1]].read_bytes()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(data)
                return
            self.search(parse_qs(parsed.query).get("page", ["2"])[0])

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["queries"].append(body)
            self.search(body.get("page", 1))

        def search(self, page):
            page = int(page)
            state["searches"] += 1
            mode = state["mode"]
            if (
                mode == "auth"
                and self.headers.get("Authorization") != "Bearer fresh-private"
            ):
                self.respond(401, {"secret": "expired-private"})
                return
            if (
                mode == "auth-page"
                and page == 2
                and self.headers.get("Authorization") != "Bearer fresh-private"
            ):
                self.respond(401, {"secret": "expired-private"})
                return
            if mode == "error" and page == 2:
                self.respond(503, {"secret": "private-upstream-detail"})
                return
            if mode == "redirect":
                self.send_response(302)
                self.send_header("Location", "https://example.org/private")
                self.end_headers()
                return
            if mode == "malformed":
                self.respond(200, {"features": "bad"})
                return
            if mode == "big":
                self.respond(
                    200,
                    {
                        "type": "FeatureCollection",
                        "features": [],
                        "links": [],
                        "padding": "x" * (8 * 1024 * 1024),
                    },
                )
                return
            if mode == "empty":
                features, links = [], []
            elif mode == "indexed":
                with sqlite3.connect(state["database"]) as db:
                    ids = [
                        r[0]
                        for r in db.execute(
                            "SELECT id FROM observations WHERE time >= 400 AND time <= 402 ORDER BY time"
                        )
                    ]
                features, links = [make_item(str(i)) for i in ids], []
            else:
                features = [make_item("b" if page == 1 else "a")]
                links = (
                    []
                    if page == 2
                    else [
                        {
                            "rel": "next",
                            "href": "/search",
                            "method": "POST",
                            "body": {"page": 2},
                            "merge": True,
                        }
                    ]
                )
                if mode == "get" and page == 1:
                    links = [{"rel": "next", "href": "?page=2"}]
                if mode == "cycle":
                    links = [
                        {
                            "rel": "next",
                            "href": "/search",
                            "method": "POST",
                            "body": state["queries"][0],
                        }
                    ]
                if mode == "cross":
                    links = [
                        {
                            "rel": "next",
                            "href": "https://example.org/search?secret=private",
                        }
                    ]
                if mode == "headers":
                    links[0]["headers"] = {"Authorization": "private"}
                if mode == "conflict" and page == 2:
                    features = [make_item("b", revision="f" * 64)]
                if mode == "duplicate":
                    features = [make_item("a")]
            self.respond(
                200, {"type": "FeatureCollection", "features": features, "links": links}
            )

    state = {"mode": "normal", "asset_gets": 0, "searches": 0, "queries": []}
    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    base = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    red_row = next(r for r in rows if r["id"] == "red")
    bounds = transform_bounds("EPSG:4326", "EPSG:3857", *red_row["geographic_bounds"])

    def make_item(
        item_id="a", revision=None, href=None, key="red", scale=0.1, offset=-2
    ):
        row = next(r for r in rows if r["id"] == key)
        west, south, east, north = row["geographic_bounds"]
        return {
            "type": "Feature",
            "stac_version": "1.0.0",
            "id": item_id,
            "collection": "sentinel",
            "properties": {
                "datetime": "2021-06-29T10:00:00Z",
                "proj:epsg": int(row["crs"].split(":")[1]),
            },
            "bbox": row["geographic_bounds"],
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [west, south],
                        [east, south],
                        [east, north],
                        [west, north],
                        [west, south],
                    ]
                ],
            },
            "assets": {
                key: {
                    "href": href or f"{base}/asset/{key}?token=expired-private",
                    "file:checksum": "1220" + (revision or row["sha256"]),
                    "raster:bands": [
                        {"scale": scale, "offset": offset, "nodata": row["nodata"]}
                    ],
                    "eo:bands": [{"name": key, "common_name": key}],
                }
            },
            "links": [],
        }

    def run(name, fn):
        state.update(mode="normal", asset_gets=0, searches=0, queries=[])
        started = time.monotonic()
        try:
            fn()
            result = {"name": name, "status": "passed"}
        except Exception as error:  # noqa: BLE001 - retain all case failures
            result = {
                "name": name,
                "status": "failed",
                "error": str(error),
                "traceback": traceback.format_exc(),
            }
        result["seconds"] = time.monotonic() - started
        report.append(result)
        print(f"{result['status']}: {name}", flush=True)

    def error_contains(fn, message):
        try:
            fn()
        except rcfp.RcfpError as error:
            assert message in str(error), str(error)
            assert not any(s in str(error) for s in ("private", "http://", "https://"))
        else:
            raise AssertionError("expected failure")

    def search(**kwargs):
        return SearchClient(base + "/search").search(
            assets=["red"],
            bbox=[12, 45, 13, 46],
            datetime="2021-06-29T00:00:00Z/2021-06-30T00:00:00Z",
            collections=["sentinel"],
            **kwargs,
        )

    def pagination(mode="normal"):
        state["mode"] = mode
        with search() as s:
            assert [a["item"] for a in s.metadata["assets"]] == ["a", "b"]
            assert s.metadata["metrics"]["pages"] == 2
            assert s.metadata["metrics"]["returned_items"] == 2
            assert state["asset_gets"] == 0
            assert state["queries"][0]["bbox"] == [12, 45, 13, 46]
            if mode == "normal":
                assert state["queries"][1]["collections"] == ["sentinel"]
                assert state["queries"][1]["page"] == 2
            assert "private" not in json.dumps(s.metadata)
            assert len(s.id) == 64 and s.id != s.metadata["search_id"]

    def provider_error(mode, message, **kwargs):
        state["mode"] = mode
        error_contains(lambda: search(**kwargs), message)

    def credential_refresh(mode):
        state["mode"] = mode
        calls = []

        def credentials():
            calls.append(True)
            return {
                "Authorization": "Bearer "
                + ("expired-private" if len(calls) == 1 else "fresh-private")
            }

        with SearchClient(base + "/search", credentials=credentials).search(
            assets=["red"]
        ) as s:
            assert len(calls) == 2
            assert len(s.metadata["assets"]) == 2
            assert "private" not in json.dumps(s.metadata)
        assert state["searches"] == (3 if mode == "auth" else 4)

    def render_and_refresh():
        calls = []

        def signer(asset):
            assert "href" not in asset
            calls.append(asset)
            return f"{base}/asset/red?token=" + (
                "expired-private" if len(calls) == 1 else "fresh-private"
            )

        with Selection.from_items([make_item()], assets=["red"], signer=signer) as s:
            start = time.monotonic()
            png = s.render(0, bounds_mercator=bounds, range=[-2, 198], size=[96, 128])
            first_ms = (time.monotonic() - start) * 1000
            start = time.monotonic()
            repeat = s.render(
                0, bounds_mercator=bounds, range=[-2, 198], size=[96, 128]
            )
            repeat_ms = (time.monotonic() - start) * 1000
            assert png == repeat and len(calls) == 3 and state["asset_gets"] == 2
            values, valid = expected(
                fixtures["red"],
                bounds,
                [96, 128],
                Resampling.bilinear,
                out / "reference.vrt",
            )
            values = values * 0.1 - 2
            indices = np.round(1 + np.clip((values + 2) / 200, 0, 1) * 254).astype(
                np.uint8
            )
            indices[~valid] = 0
            image = Image.open(io.BytesIO(png))
            actual = np.asarray(image)
            assert actual.shape == (96, 128)
            assert np.array_equal(actual == 0, ~valid), "coverage mismatch"
            assert np.max(np.abs(actual.astype(int) - indices.astype(int))) <= 1, (
                "display sample mismatch"
            )
            assert np.count_nonzero(actual) > 96 * 128 // 2
            performance.append(
                {
                    "kind": "render",
                    "first_with_download_ms": first_ms,
                    "repeat_ms": repeat_ms,
                    "asset_gets": state["asset_gets"],
                }
            )
        error_contains(
            lambda: s.render(0, bounds_mercator=bounds, range=[0, 1]), "closed"
        )

    def changed_revision():
        path = out / "mutable.tif"
        shutil.copyfile(fixtures["red"], path)
        a = make_item(href=str(path))
        with Selection.from_items([a], assets=["red"]) as old:
            first = old.render(0, bounds_mercator=bounds, range=[0, 200])
            shutil.copyfile(fixtures["green"], path)
            assert old.render(0, bounds_mercator=bounds, range=[0, 200]) == first
            b = copy.deepcopy(a)
            b["assets"]["red"]["file:checksum"] = "1220" + next(
                r["sha256"] for r in rows if r["id"] == "green"
            )
            with Selection.from_items([b], assets=["red"]) as new:
                assert old.id != new.id
                assert new.render(0, bounds_mercator=bounds, range=[0, 200]) != first
            with Selection.from_items([a], assets=["red"]) as mismatch:
                error_contains(
                    lambda: mismatch.render(0, bounds_mercator=bounds, range=[0, 200]),
                    "checksum mismatch",
                )

    def explicit_revision():
        a = make_item(href=fixtures["red"].as_uri())
        del a["assets"]["red"]["file:checksum"]
        error_contains(
            lambda: Selection.from_items([a], assets=["red"]), "require SHA-256"
        )
        with Selection.from_items(
            [a], assets=["red"], revisions={"sentinel/a/red": red_row["sha256"]}
        ) as s:
            assert len(s.render(0, bounds_mercator=bounds, range=[-2, 198])) > 100

    def interval_relative_and_isolation():
        a = make_item(href="red.tif")
        a["properties"].update(
            datetime=None,
            start_datetime="2021-06-29T12:00:00+02:00",
            end_datetime="2021-06-29T13:00:00+02:00",
        )
        with Selection.from_items(
            [a], assets=["red"], base_url=fixtures["red"].parent.as_uri() + "/"
        ) as s:
            assert s.metadata["assets"][0]["datetime"] == "2021-06-29T10:00:00Z"
            assert s.metadata["assets"][0]["end_datetime"] == "2021-06-29T11:00:00Z"
            metadata = s.metadata
            metadata["assets"][0]["bands"][0]["scale"] = 100
            assert s.metadata["assets"][0]["bands"][0]["scale"] == 0.1
            assert s.render(0, bounds_mercator=bounds, range=[-2, 198])

    def empty_and_duplicates():
        state["mode"] = "empty"
        with search() as s:
            assert s.metadata["assets"] == []
            error_contains(
                lambda: s.render(0, bounds_mercator=bounds, range=[0, 1]),
                "out of range",
            )
        state["mode"] = "duplicate"
        with search() as s:
            assert len(s.metadata["assets"]) == 1
        error_contains(lambda: search(max_items=1), "result limit")

    def render_limits():
        with Selection.from_items(
            [make_item(href=str(fixtures["red"]))], assets=["red"], max_staged_bytes=2
        ) as s:
            error_contains(
                lambda: s.render(0, bounds_mercator=bounds, range=[0, 1]), "byte limit"
            )
            error_contains(
                lambda: s.render(0, bounds_mercator=bounds, range=[0, 1], size=[0, 2]),
                "grid or range",
            )
            error_contains(
                lambda: s.render(0, bounds_mercator=bounds, range=[0, 1], band=2),
                "band out of range",
            )
        a = make_item(href=str(fixtures["red"]))
        a["assets"]["red"]["raster:bands"][0]["nodata"] = -9999
        with Selection.from_items([a], assets=["red"]) as s:
            error_contains(
                lambda: s.render(0, bounds_mercator=bounds, range=[0, 1]),
                "nodata differs",
            )

    def identity_changes():
        a = make_item()
        with Selection.from_items([a], assets=["red"]) as first:
            b = copy.deepcopy(a)
            b["assets"]["red"]["href"] = base + "/asset/red?token=refreshed-private"
            with Selection.from_items([b], assets=["red"]) as signed:
                assert first.id == signed.id
            for field, value in (("scale", 0.2), ("offset", 2), ("nodata", -9999)):
                c = copy.deepcopy(a)
                c["assets"]["red"]["raster:bands"][0][field] = value
                with Selection.from_items([c], assets=["red"]) as changed:
                    assert first.id != changed.id
        with search(page_size=1) as first, search(page_size=2) as second:
            assert (
                first.id == second.id
                and first.metadata["search_id"] != second.metadata["search_id"]
            )

    def size_measurement():
        state["mode"] = "indexed"
        database = out / "indexed-provider.sqlite"
        state["database"] = database
        with sqlite3.connect(database) as db:
            db.execute(
                "CREATE TABLE observations(id INTEGER PRIMARY KEY, time INTEGER)"
            )
            db.execute("CREATE INDEX time_index ON observations(time)")
            previous = 0
            for count in (1000, 100000, 1000000):
                db.executemany(
                    "INSERT INTO observations VALUES (?,?)",
                    ((i, i) for i in range(previous, count)),
                )
                db.commit()
                plan = str(
                    db.execute(
                        "EXPLAIN QUERY PLAN SELECT id FROM observations WHERE time >= 400 AND time <= 402 ORDER BY time"
                    ).fetchall()
                )
                assert "time_index" in plan
                timings = []
                for _ in range(5):
                    with search() as s:
                        assert (
                            len(s.metadata["assets"]) == 3
                            and s.metadata["metrics"]["pages"] == 1
                        )
                        timings.append(s.metadata["metrics"]["selection_ms"])
                performance.append(
                    {
                        "kind": "selection",
                        "provider_rows": count,
                        "selected": 3,
                        "pages": 1,
                        "median_ms": statistics.median(timings),
                        "trials_ms": timings,
                        "query_plan": plan,
                    }
                )
                previous = count
        assert state["asset_gets"] == 0

    def callback_failures_and_bounded_retry():
        def broken(*_):
            raise RuntimeError("private-credential-or-signature")

        client = SearchClient(base + "/search", credentials=broken)
        error_contains(lambda: client.search(assets=["red"]), "callback failed")
        state["mode"] = "auth"
        client = SearchClient(
            base + "/search",
            credentials=lambda: {"Authorization": "Bearer expired-private"},
        )
        error_contains(lambda: client.search(assets=["red"]), "HTTP 401")
        assert state["searches"] == 2
        with Selection.from_items([make_item()], assets=["red"], signer=broken) as s:
            error_contains(
                lambda: s.render(0, bounds_mercator=bounds, range=[0, 1]),
                "signer failed",
            )
        with Selection.from_items(
            [make_item()],
            assets=["red"],
            signer=lambda _: base + "/asset/red?token=expired-private",
        ) as s:
            error_contains(
                lambda: s.render(0, bounds_mercator=bounds, range=[0, 1]), "HTTP 403"
            )
        assert state["asset_gets"] == 2

    def multiband_asset():
        # Real source samples, preserved without resampling, extend packed-band coverage.
        path = out / "packed-real.tif"
        with (
            rasterio.open(fixtures["red"]) as red,
            rasterio.open(fixtures["green"]) as green,
        ):
            profile = dict(red.profile, count=2)
            with rasterio.open(path, "w", **profile) as target:
                target.write(red.read(1), 1)
                target.write(green.read(1), 2)
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        a = make_item(href=path.as_uri(), revision=checksum, scale=1, offset=0)
        a["assets"]["red"]["raster:bands"].append(
            {"scale": 0.1, "offset": -2, "nodata": 0}
        )
        a["assets"]["red"]["eo:bands"].append({"name": "green", "common_name": "green"})
        with (
            Selection.from_items([a], assets=["red"]) as packed,
            Selection.from_items(
                [make_item(href=fixtures["green"].as_uri(), key="green")],
                assets=["green"],
            ) as separate,
        ):
            assert packed.metadata["assets"][0]["bands"][1]["band"] == 2
            assert packed.render(
                0, band=2, bounds_mercator=bounds, range=[-2, 198]
            ) == separate.render(0, bounds_mercator=bounds, range=[-2, 198])
            assert packed.render(
                0, band=1, bounds_mercator=bounds, range=[0, 2000]
            ) != packed.render(0, band=2, bounds_mercator=bounds, range=[-2, 198])
        performance.append(
            {
                "kind": "fixture",
                "sha256": checksum,
                "recipe": "Packed bands from pinned Sentinel red/green crops; no sample, grid or nodata modification",
            }
        )

    def invalid_inputs_and_private_error_details():
        error_contains(
            lambda: SearchClient("http://example.org/search").search(assets=["red"]),
            "HTTPS",
        )
        error_contains(
            lambda: SearchClient(base + "/search").search(
                assets=["red"], datetime="bad"
            ),
            "invalid STAC datetime",
        )

        a = make_item(href=fixtures["red"].as_uri())
        error_contains(
            lambda: Selection.from_items(
                [a], assets=["red"], revisions={"sentinel/a/red": "f" * 64}
            ),
            "conflicts",
        )
        path = out / "not-a-raster.vrt"
        path.write_text(
            "<VRTDataset><SourceFilename>https://example.org/?private=secret</SourceFilename></VRTDataset>"
        )
        a = make_item(
            href=path.as_uri(), revision=hashlib.sha256(path.read_bytes()).hexdigest()
        )
        with Selection.from_items([a], assets=["red"]) as s:
            error_contains(
                lambda: s.render(0, bounds_mercator=bounds, range=[0, 1]), "GeoTIFF"
            )

    try:
        run("post-pagination-order-private-metadata", pagination)
        run("get-pagination", lambda: pagination("get"))
        run("empty-duplicates-counted-against-limits", empty_and_duplicates)
        for name, message in (
            ("cycle", "pagination cycle"),
            ("cross", "provider origin"),
            ("headers", "headers are unsupported"),
            ("conflict", "conflicting"),
            ("error", "HTTP 503"),
            ("redirect", "HTTP 302"),
            ("malformed", "FeatureCollection"),
            ("big", "byte limit"),
        ):
            run(
                "provider-" + name,
                lambda name=name, message=message: provider_error(name, message),
            )
        run(
            "result-limit",
            lambda: provider_error("normal", "result limit", max_items=1),
        )
        run("page-limit", lambda: provider_error("normal", "page limit", max_pages=1))
        run("credential-refresh", lambda: credential_refresh("auth"))
        run("credential-refresh-on-later-page", lambda: credential_refresh("auth-page"))
        run("signed-url-refresh-verified-render-and-reuse", render_and_refresh)
        run("mutable-source-revision-isolation", changed_revision)
        run("explicit-revision", explicit_revision)
        run(
            "interval-relative-links-metadata-isolation",
            interval_relative_and_isolation,
        )
        run("render-limits-and-nodata-validation", render_limits)
        run("search-selection-style-identities", identity_changes)
        run(
            "callback-sanitization-and-bounded-retries",
            callback_failures_and_bounded_retry,
        )
        run("packed-real-multiband-selectors", multiband_asset)
        run(
            "invalid-inputs-and-nested-source-rejection",
            invalid_inputs_and_private_error_details,
        )
        run("indexed-catalog-size-selection-measurement", size_measurement)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    wheel = args.wheel.resolve()
    data = {
        "wheel": str(wheel),
        "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "cases": report,
        "measurements": performance,
        "exclusions": [
            "Hosted PgSTAC/provider infrastructure",
            "WAN throughput and first-asset download performance",
            "Cloud SDK credential-chain integration",
        ],
        "skips": [],
    }
    (out / "report.json").write_text(json.dumps(data, indent=2) + "\n")
    assert all(r["status"] == "passed" for r in report), "STAC contracts failed"
    print(f"Passed {len(report)} STAC contracts")


if __name__ == "__main__":
    main()
