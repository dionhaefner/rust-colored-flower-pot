"""Qualify native discovery, point values and tile grids on an installed wheel.

Raster expectations come from checksum-pinned source windows and independent
Rasterio/NumPy reads. Calibration, fractional alpha, affine rotation and nodata
are narrow declaration derivatives of real samples. No RCFP output is a golden.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import math
import os
import socket
import subprocess
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlencode

import jsonschema
import numpy as np
import rasterio
import rcfp
from rasterio.enums import ColorInterp, Resampling
from rasterio.transform import Affine, from_bounds
from rasterio.warp import reproject, transform
from rcfp.wsgi import Application as WSGI

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests/fixtures/corpus"
GRIDS = ROOT / "tests/fixtures/tile-grids"
SCENE = "scene / +% #? ü {x}"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--browser", action="store_true")
    args = parser.parse_args()
    binary, out = args.binary.resolve(), args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    rows = []

    def case(name, check):
        try:
            check()
            row = {"case": name, "status": "passed"}
        except Exception as error:  # noqa: BLE001 - preserve every check in the report
            row = {"case": name, "status": "failed", "error": repr(error)}
        rows.append(row)
        print(row, flush=True)

    fixtures = {}
    for folder in (CORPUS, GRIDS):
        for row in json.loads((folder / "manifest.json").read_text())["fixtures"]:
            path = folder / row["file"]
            assert digest(path) == row["sha256"], path
            fixtures[row.get("id", path.stem)] = path
    assert digest(binary) == digest(Path(rcfp.__file__).parent / "runtime/bin/rcfp"), (
        "Installed wheel and executable must match"
    )
    settings = {
        "REPROJECTION_METHOD": "nearest",
        "RASTER_CACHE_BYTES": 1048576,
        "RENDER_CACHE_BYTES": 1048576,
        "EXPOSE_METRICS": True,
    }
    database = out / "catalog.sqlite"
    store = rcfp.DataStore(database)
    store.create(["scene", "band"], format="native")
    with rasterio.open(CORPUS / "red.tif") as red:
        profile = dict(red.profile, count=2)
        packed = out / "packed.tif"
        with rasterio.open(packed, "w", **profile) as dst:
            dst.write(red.read(1), 1)
            with rasterio.open(CORPUS / "nir.tif") as nir:
                dst.write(nir.read(1), 2)
            dst.scales = (0.001, 0.002)
            dst.offsets = (-1, -2)
            dst.units = ("reflectance", "reflectance")
        rotated = out / "rotated.tif"
        with rasterio.open(
            rotated,
            "w",
            **dict(red.profile, transform=red.transform * Affine.rotation(12)),
        ) as dst:
            dst.write(red.read())
        alpha = out / "alpha.tif"
        with rasterio.open(
            alpha, "w", **dict(red.profile, count=4, dtype="float64", nodata=None)
        ) as dst:
            data = red.read(1).astype(float)
            data[0, 2] = np.nan
            coverage = np.ones(data.shape)
            coverage[0, 0], coverage[0, 1] = 0, 0.25
            dst.write(data, 1)
            dst.write(data, 2)
            dst.write(data, 3)
            dst.write(coverage, 4)
            dst.colorinterp = (
                ColorInterp.red,
                ColorInterp.green,
                ColorInterp.blue,
                ColorInterp.alpha,
            )
    for name, band, values in (
        ("raw", 1, "raw"),
        ("scaled", 1, "scaled"),
        ("nir", 2, "scaled"),
    ):
        metadata = (
            rcfp.compute_metadata(
                packed,
                extra_metadata={
                    "attribution": "Contains modified Copernicus Sentinel data 2021"
                },
            )
            if name == "raw"
            else None
        )
        store.insert(
            [SCENE, name],
            packed,
            band=band,
            values=values,
            stretch=[0, 3],
            metadata=metadata,
        )
    store.define(
        [SCENE, "index"],
        "(nir-red)/(nir+red)",
        {"red": [SCENE, "scaled"], "nir": [SCENE, "nir"]},
        stretch=[-1, 1],
    )
    for name, path in {
        "red": CORPUS / "red.tif",
        "alpha": alpha,
        "rotated": rotated,
        **{
            key: GRIDS / f"{key}.tif"
            for key in ("arctic", "south", "dateline-east", "dateline-west")
        },
    }.items():
        store.insert([SCENE, name], path, stretch=[0, 3000])
    app = rcfp.NativeApplication(database, settings)

    def path(prefix, name, suffix=""):
        return f"/{prefix}/k-{json.dumps([SCENE, name], ensure_ascii=False, separators=(',', ':')).encode().hex()}{suffix}"

    def request(url, method="GET", headers=None, expected=200):
        response = app.request(
            method, url, {"host": "localhost:8000", **(headers or {})}
        )
        assert response[0] == expected, (url, response[0], response[2][:1000])
        return response

    def document(url, **kwargs):
        return json.loads(request(url, **kwargs)[2])

    def point(name, x, y):
        return document(path("point", name) + "?" + urlencode({"lon": x, "lat": y}))

    def native_cell(name, location, row=100, column=100, band=1, alpha_band=None):
        with rasterio.open(location) as src:
            x, y = src.xy(row, column)
            lon, lat = transform(src.crs, "EPSG:4326", [x], [y])
            result = point(name, lon[0], lat[0])
            raw = float(src.read(band)[row, column])
            valid = bool(src.read_masks(band)[row, column] > 0 and np.isfinite(raw))
            if src.nodatavals[band - 1] is not None:
                valid &= raw != src.nodatavals[band - 1]
            if alpha_band:
                valid = np.isfinite(raw) and src.read(alpha_band)[row, column] > 0
            assert result["valid"] == valid, result
            assert result["raw"] == (raw if valid else None), result
            if valid:
                assert (
                    result["scaled"]
                    == raw * src.scales[band - 1] + src.offsets[band - 1]
                )
            assert result["value"] == (
                result["scaled"] if result["values"] == "scaled" else result["raw"]
            )
            if location == packed:
                assert result["units"] == "reflectance"
            assert result["interpolation"] == "nearest"
            assert str(location) not in json.dumps(result)
            return result

    case("point-raw-scale-offset-units", lambda: native_cell("raw", packed))
    case("point-selected-scaled-band", lambda: native_cell("nir", packed, band=2))
    case("point-rotated-affine", lambda: native_cell("rotated", rotated))
    for column in range(3):
        case(
            f"point-alpha-nonfinite-{column}",
            lambda column=column: native_cell("alpha", alpha, 0, column, alpha_band=4),
        )
    for name in ("arctic", "south", "dateline-east", "dateline-west"):
        case(
            "point-" + name, lambda name=name: native_cell(name, GRIDS / f"{name}.tif")
        )

    def expression_point():
        with rasterio.open(packed) as src:
            x, y = src.xy(100, 100)
            lon, lat = transform(src.crs, "EPSG:4326", [x], [y])
            red = src.read(1)[100, 100] * 0.001 - 1
            nir = src.read(2)[100, 100] * 0.002 - 2
            result = point("index", lon[0], lat[0])
            assert math.isclose(
                result["value"], (nir - red) / (nir + red), abs_tol=1e-12
            ), result
            assert result["raw"] is None and result["units"] is None
            assert result["provenance"]["operands"]["red"]["raw"] == float(
                src.read(1)[100, 100]
            )

    case("point-expression-and-operand-provenance", expression_point)
    case("point-outside-source", lambda: equal(point("raw", 0, 0)["value"], None))
    for query in (
        "lon=nan&lat=0",
        "lon=181&lat=0",
        "lon=0&lat=-91",
        "lon=0",
        "lon=0&lat=0&interpolation=bilinear",
    ):
        case(
            "invalid-point-" + query,
            lambda query=query: request(
                path("point", "raw") + "?" + query, expected=400
            ),
        )

    def cell_boundaries():
        with rasterio.open(GRIDS / "dateline-west.tif") as source:
            west, south, east, north = source.bounds
            upper_left = point("dateline-west", west, north)
            assert upper_left["valid"] and upper_left["raw"] == float(
                source.read(1)[0, 0]
            )
            assert not point("dateline-west", east, (south + north) / 2)["valid"]
            assert not point("dateline-west", (west + east) / 2, south)["valid"]

    case("point-source-cell-half-open-boundaries", cell_boundaries)
    spec = document("/swagger.json")

    def schema(value, name):
        import copy

        components = copy.deepcopy(spec["components"])

        def nullable(value):
            if isinstance(value, dict):
                if value.pop("nullable", False):
                    value["type"] = [value["type"], "null"]
                for child in value.values():
                    nullable(child)
            elif isinstance(value, list):
                for child in value:
                    nullable(child)

        nullable(components)
        jsonschema.validate(
            value,
            {"$ref": f"#/components/schemas/{name}", "components": components},
        )

    discovery = document("/tilesets?limit=2")
    case("paginated-discovery-schema", lambda: schema(discovery, "Tilesets"))
    case(
        "discovery-pagination",
        lambda: equal(len(document("/tilesets?limit=2&page=1")["tilesets"]), 2),
    )
    case(
        "empty-discovery-page",
        lambda: equal(document("/tilesets?page=100")["tilesets"], []),
    )
    case(
        "discovery-does-not-expose-paths",
        lambda: equal(str(packed) in json.dumps(discovery), False),
    )
    case(
        "matrix-list-schema",
        lambda: schema(document("/tileMatrixSets"), "TileMatrixSets"),
    )
    case("point-schema", lambda: schema(point("raw", 0, 0), "PointInspection"))
    tilejson = document(
        path("tilejson", "raw", ".json") + "?stretch_range=[0,3000]&colormap=viridis"
    )
    case(
        "tilejson-schema-and-absolute-urls",
        lambda: (
            schema(tilejson, "TileJSON"),
            equal(tilejson["tilejson"], "3.0.0"),
            equal(tilejson["tiles"][0].split("/tiles/")[0], "http://localhost:8000"),
        ),
    )
    case(
        "tilejson-style-query",
        lambda: equal("colormap=viridis" in tilejson["tiles"][0], True),
    )
    case(
        "tilejson-bounds-attribution-zooms-and-defaults",
        lambda: (
            equal(
                tilejson["bounds"], list(store.get_metadata([SCENE, "raw"])["bounds"])
            ),
            equal(
                tilejson["attribution"],
                "Contains modified Copernicus Sentinel data 2021",
            ),
            equal([tilejson["minzoom"], tilejson["maxzoom"]], [0, 22]),
            equal(tilejson["tileSize"], 256),
            equal(tilejson["rcfp"]["defaults"]["stretch"], [0, 3]),
        ),
    )
    public = rcfp.NativeApplication(
        database, {**settings, "PUBLIC_URL": "https://maps.example.test/prefix/"}
    )
    case(
        "deployment-prefix-and-proxy-header-isolation",
        lambda: equal(
            json.loads(
                public.request(
                    "GET",
                    path("tilejson", "raw", ".json"),
                    {"host": "private:8000", "x-forwarded-host": "attacker.test"},
                )[2]
            )["tiles"][0].split("/tiles/")[0],
            "https://maps.example.test/prefix",
        ),
    )

    def prefixed_discovery():
        prefix = "https://maps.example.test/prefix/"
        listing = json.loads(public.request("GET", "/tilesets")[2])
        for entry in listing["tilesets"]:
            assert entry["tilejson"].startswith(prefix + "tilejson/")
            assert entry["point"].startswith(prefix + "point/")
            for grid in entry["tileMatrixSets"]:
                assert grid["tiles"].startswith(prefix + "tiles/")
                assert grid["definition"].startswith(prefix + "tileMatrixSets/")
        grids = json.loads(public.request("GET", "/tileMatrixSets")[2])
        assert all(row["href"].startswith(prefix) for row in grids["tileMatrixSets"])

    case("deployment-prefix-in-all-discovery-links", prefixed_discovery)
    case(
        "host-header-validation",
        lambda: request(
            path("tilejson", "raw", ".json"),
            headers={"host": "attacker.test/path"},
            expected=400,
        ),
    )
    case(
        "head-and-options",
        lambda: (
            equal(request("/tilesets", "HEAD")[2], b""),
            equal(request("/tilesets", "OPTIONS")[4]["Allow"], "GET, HEAD, OPTIONS"),
        ),
    )
    case(
        "discovery-and-point-cors",
        lambda: equal(
            request("/tilesets", headers={"origin": "https://app.example.test"})[4][
                "Access-Control-Allow-Origin"
            ],
            "https://app.example.test",
        ),
    )
    case("unsupported-method", lambda: request("/tilesets", "POST", expected=405))
    case(
        "missing-dataset",
        lambda: request(path("point", "missing") + "?lon=0&lat=0", expected=404),
    )
    for route in (
        "/tilesets?limit=0",
        "/tilesets?page=18446744073709551615",
        path("tiles", "raw", "/unknown/0/0/0.png"),
        path("tiles", "raw", "/WorldCRS84Quad/0/2/0.png"),
        path("tiles", "raw", "/WebMercatorQuad/23/0/0.png"),
        path("tiles", "raw", "/WebMercatorQuad/1/-1/0.png"),
        path("tiles", "raw", "/WebMercatorQuad/1/0.5/0.png"),
        path("tiles", "raw", "/WebMercatorQuad/1/0/0.png?tile_size=[512,512]"),
    ):
        case("invalid-grid-" + route, lambda route=route: request(route, expected=400))

    def geometry(matrix_id, name, zoom=14):
        definition = document("/tileMatrixSets/" + matrix_id)
        schema(definition, "TileMatrixSet")
        matrix = definition["tileMatrices"][zoom]
        epsg = (
            4326
            if matrix_id == "WorldCRS84Quad"
            else int(definition["crs"].split("/")[-1])
        )
        location = CORPUS / "red.tif" if name == "red" else GRIDS / f"{name}.tif"
        with rasterio.open(location) as src:
            cx, cy = src.xy(src.height // 2, src.width // 2)
            tx, ty = transform(src.crs, f"EPSG:{epsg}", [cx], [cy])
        ox, oy = matrix["pointOfOrigin"]
        span = matrix["cellSize"] * 256
        column, row = math.floor((tx[0] - ox) / span), math.floor((oy - ty[0]) / span)
        return (
            definition,
            location,
            epsg,
            zoom,
            column,
            row,
            [
                ox + column * span,
                oy - (row + 1) * span,
                ox + (column + 1) * span,
                oy - row * span,
            ],
        )

    grid_cases = [
        ("WebMercatorQuad", "red"),
        ("WorldCRS84Quad", "red"),
        ("RCFPArcticQuad", "arctic"),
        ("RCFPAntarcticQuad", "south"),
        ("WorldCRS84Quad", "dateline-east"),
        ("WorldCRS84Quad", "dateline-west"),
    ]

    def raster_grid(matrix_id, name):
        _, location, epsg, zoom, column, row, bounds = geometry(matrix_id, name)
        with rasterio.open(location) as src:
            source = src.read(1).astype(float)
            source[(src.read_masks(1) == 0) | ~np.isfinite(source)] = np.nan
            expected = np.full((256, 256), np.nan)
            reproject(
                source,
                expected,
                src_crs=src.crs,
                src_transform=src.transform,
                src_nodata=np.nan,
                dst_crs=f"EPSG:{epsg}",
                dst_transform=from_bounds(*bounds, 256, 256),
                dst_nodata=np.nan,
                resampling=Resampling.nearest,
            )
        url = path(
            "tiles",
            name,
            f"/{matrix_id}/{zoom}/{column}/{row}.png?stretch_range=[0,3000]",
        )
        image = np.array(Image(url))
        valid = np.isfinite(expected)
        assert valid.any(), (name, bounds)
        np.testing.assert_array_equal(image[:, :, 3] > 0, valid)
        color = np.rint(np.clip((expected[valid] / 3000) * 254 + 1, 1, 255)).astype(
            np.uint8
        )
        np.testing.assert_array_equal(image[:, :, 0][valid], color)
        np.testing.assert_array_equal(image[:, :, 1][valid], color)
        first = document("/__terracotta/metrics")
        second_image = np.array(Image(url))
        second = document("/__terracotta/metrics")
        np.testing.assert_array_equal(image, second_image)
        assert second["render_cache"]["hits"] > first["render_cache"]["hits"]
        return url

    def Image(url):
        from PIL import Image as PILImage

        response = request(url)
        assert response[1] == "image/png"
        image = PILImage.open(io.BytesIO(response[2])).convert("RGBA")
        assert image.size == (256, 256)
        return image

    for matrix_id, name in grid_cases:
        case(
            f"independent-{matrix_id}-{name}-pixels-masks-cache",
            lambda matrix_id=matrix_id, name=name: raster_grid(matrix_id, name),
        )
    case(
        "empty-tile-coverage",
        lambda: equal(
            np.asarray(Image(path("tiles", "red", "/WebMercatorQuad/0/0/0.png")))[
                0, 0, 3
            ],
            0,
        ),
    )

    def conditional_tiles():
        url = path("tiles", "red", "/WorldCRS84Quad/0/1/0.png")
        first = request(url)
        tag = first[4]["ETag"]
        revision = first[4]["X-RCFP-Revision"]
        assert request(url, headers={"If-None-Match": tag}, expected=304)[2] == b""
        pinned = request(url + "?revision=" + revision)
        assert pinned[2] == first[2]
        assert "immutable" in pinned[4]["Cache-Control"]
        request(url + "?revision=" + "0" * 64, expected=409)

    case("native-grid-conditional-and-pinned-responses", conditional_tiles)

    def adapters():
        wsgi = WSGI(str(database), settings)
        routes = (
            "/tilesets",
            path("tilejson", "raw", ".json"),
            path("point", "raw") + "?lon=0&lat=0",
            path("tiles", "red", "/WorldCRS84Quad/0/1/0.png"),
        )
        for route in routes:
            statuses = []
            raw_path, _, query = route.partition("?")
            from urllib.parse import unquote

            body = b"".join(
                wsgi(
                    {
                        "REQUEST_METHOD": "GET",
                        "PATH_INFO": unquote(raw_path)
                        .encode("utf-8")
                        .decode("latin-1"),
                        "QUERY_STRING": query,
                        "HTTP_HOST": "localhost:8000",
                        "wsgi.input": io.BytesIO(),
                    },
                    lambda status, headers, statuses=statuses: statuses.append(status),
                )
            )
            assert statuses == ["200 OK"], (route, statuses, body[:1000])
            assert body == request(route)[2]
            event = out / "event.json"
            event.write_text(
                json.dumps(
                    {
                        "rawPath": raw_path,
                        "rawQueryString": query,
                        "requestContext": {"http": {"method": "GET"}},
                        "headers": {"host": "localhost:8000"},
                    }
                )
            )
            result = subprocess.run(
                [
                    str(binary),
                    "lambda-event",
                    "-d",
                    str(database),
                    "--event",
                    str(event),
                ],
                env={**os.environ, "TC_REPROJECTION_METHOD": "nearest"},
                capture_output=True,
                check=True,
            )
            value = json.loads(result.stdout)
            assert value["statusCode"] == 200, value
            data = (
                base64.b64decode(value["body"])
                if value["isBase64Encoded"]
                else value["body"].encode()
            )
            assert data == body, (route, data[:1000], body[:1000])

    case("python-wsgi-lambda-shared-discovery-point-and-grids", adapters)

    def legacy():
        catalog = out / "legacy.sqlite"
        rcfp.DataStore(catalog).create(["band"])
        rcfp.DataStore(catalog).insert(["red"], CORPUS / "red.tif")
        instruction = out / "request.json"
        for route in (
            "/tilesets",
            "/tileMatrixSets",
            "/tilejson/red.json",
            "/point/red?lon=0&lat=0",
            "/tiles/red/WorldCRS84Quad/0/0/0.png",
        ):
            event = {
                "path": route.split("?")[0],
                "httpMethod": "GET",
                "queryStringParameters": {"lon": "0", "lat": "0"},
            }
            instruction.write_text(json.dumps(event))
            result = subprocess.run(
                [
                    str(binary),
                    "--compat-profile",
                    "terracotta",
                    "lambda-event",
                    "-d",
                    str(catalog),
                    "--event",
                    str(instruction),
                ],
                capture_output=True,
                check=True,
            )
            assert json.loads(result.stdout)["statusCode"] == 404

    case("compatibility-profile-fences-native-routes", legacy)

    def other_layouts():
        import copy

        legacy_store = rcfp.DataStore(out / "sql.sqlite")
        legacy_store.create(["band"])
        legacy_store.insert(["red"], CORPUS / "red.tif")
        advanced = rcfp.LayerStore(out / "advanced.sqlite")
        advanced.create()
        advanced.register("example", CORPUS / "red.tif", stretch=[0, 3000])
        item = copy.deepcopy(
            json.loads(
                (ROOT / "docs/examples/data/lagoon-multispectral-stac.json").read_text()
            )
        )
        for name, asset in item["assets"].items():
            source = CORPUS / f"{name}.tif"
            asset["href"] = source.as_uri()
            asset["file:checksum"] = "1220" + digest(source)
        item_path = out / "item.json"
        item_path.write_text(json.dumps(item))
        with rasterio.open(CORPUS / "red.tif") as source:
            x, y = source.xy(100, 100)
            longitude, latitude = transform(source.crs, "EPSG:4326", [x], [y])
            expected = float(source.read(1)[100, 100])
        for source in (legacy_store.path, advanced.path, item_path):
            service = rcfp.NativeApplication(source, settings)

            def get(url, service=service):
                status, _, body, _, _ = service.request(
                    "GET", url, {"host": "localhost:8000"}
                )
                assert status == 200, (url, status, body[:1000])
                return json.loads(body)

            entries = get("/tilesets")["tilesets"]
            selected = next(
                (entry for entry in entries if entry["keys"][-1] == "red"), entries[0]
            )
            descriptor = get(selected["tilejson"])
            assert descriptor["rcfp"]["keys"] == selected["keys"]
            result = get(
                selected["point"]
                + "?"
                + urlencode({"lon": longitude[0], "lat": latitude[0]})
            )
            assert result["valid"] and math.isclose(
                result["value"], expected, rel_tol=0, abs_tol=1e-12
            ), result
            template = selected["tileMatrixSets"][1]["tiles"]
            tile = (
                template.replace("{z}", "0").replace("{x}", "1").replace("{y}", "0")
                + "?stretch_range=[0,3000]"
            )
            status, content_type, body, _, _ = service.request("GET", tile)
            assert status == 200 and content_type == "image/png", body[:1000]

    case("shared-sql-stac-and-advanced-layer-interfaces", other_layouts)

    def matrix_cache_identity():
        before = document("/__terracotta/metrics")["render_cache"]
        Image(path("tiles", "red", "/WorldCRS84Quad/0/0/0.png"))
        after = document("/__terracotta/metrics")["render_cache"]
        assert after["misses"] > before["misses"], (before, after)

    case("distinct-matrix-cache-miss-at-identical-coordinates", matrix_cache_identity)

    if args.browser:
        case(
            "independent-openlayers-geographic-and-polar-grids",
            lambda: browser_grids(binary, database, out, geometry, path),
        )
    report = {
        "binary_sha256": digest(binary),
        "wheel_sha256": digest(args.wheel.resolve()),
        "cases": rows,
        "exclusions": [
            "Hosted deployments, remote providers, WAN and other platforms",
            "WMTS and OGC API service conformance is not claimed",
        ],
        "skips": []
        if args.browser
        else ["OpenLayers grid browser check requires --browser"],
    }
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    if any(row["status"] == "failed" for row in rows):
        raise SystemExit(1)


def equal(left, right):
    assert left == right, (left, right)


def browser_grids(binary, database, out, geometry, path):
    from playwright.sync_api import sync_playwright

    def port():
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    backend, client = port(), port()
    base = f"http://127.0.0.1:{backend}"
    processes = []
    log = (out / "browser.log").open("w")
    try:
        for command, url in (
            (
                [
                    str(binary),
                    "serve",
                    "-d",
                    str(database),
                    "--bind",
                    f"127.0.0.1:{backend}",
                ],
                base + "/tilesets",
            ),
            (
                [str(binary), "connect", base, "--no-browser", "--port", str(client)],
                f"http://127.0.0.1:{client}/",
            ),
        ):
            processes.append(
                subprocess.Popen(
                    command,
                    env={**os.environ, "TC_REPROJECTION_METHOD": "nearest"},
                    stdout=log,
                    stderr=log,
                )
            )
            for attempt in range(100):
                try:
                    urllib.request.urlopen(url, timeout=1).close()
                    break
                except OSError:
                    if attempt == 99:
                        raise
                    time.sleep(0.1)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 800, "height": 800})
            page.goto(f"http://127.0.0.1:{client}/examples/openlayers.html")
            page.wait_for_function("typeof ol !== 'undefined'")
            for matrix_id, name in (
                ("WorldCRS84Quad", "red"),
                ("RCFPArcticQuad", "arctic"),
                ("RCFPAntarcticQuad", "south"),
            ):
                definition, _, epsg, zoom, _column, _row, bounds = geometry(
                    matrix_id, name
                )
                template = base + path(
                    "tiles",
                    name,
                    f"/{matrix_id}/{{z}}/{{x}}/{{y}}.png?stretch_range=[0,3000]",
                )
                result = page.evaluate(
                    """async ({definition,epsg,zoom,bounds,template}) => {
                  const target = document.createElement('div'); target.style.cssText = 'width:512px;height:512px'; document.body.replaceChildren(target);
                  const projection = epsg===4326 ? ol.proj.get('EPSG:4326') : new ol.proj.Projection({code:`EPSG:${epsg}`,units:'m'});
                  const matrices = definition.tileMatrices;
                  const grid = new ol.tilegrid.TileGrid({origin:matrices[0].pointOfOrigin,resolutions:matrices.map(m=>m.cellSize),tileSize:256,sizes:matrices.map(m=>[m.matrixWidth,m.matrixHeight])});
                  const source = new ol.source.XYZ({url:template,projection,tileGrid:grid,wrapX:false,crossOrigin:'anonymous'});
                  let loaded=0,errors=0; source.on('tileloadend',()=>loaded++); source.on('tileloaderror',()=>errors++);
                  const map = new ol.Map({target,layers:[new ol.layer.Tile({source})],view:new ol.View({projection,center:[(bounds[0]+bounds[2])/2,(bounds[1]+bounds[3])/2],resolution:matrices[zoom].cellSize})});
                  await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error('grid did not finish rendering')),10000);map.once('rendercomplete',()=>{clearTimeout(timer);resolve();});});
                  map.renderSync(); window.gridMap=map;
                  const canvas=target.querySelector('canvas'), pixels=canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data;
                  let visible=0;for(let i=3;i<pixels.length;i+=4)if(pixels[i]>0)visible++;
                  return {loaded,errors,visible,projection:map.getView().getProjection().getCode()};
                }""",
                    {
                        "definition": definition,
                        "epsg": epsg,
                        "zoom": zoom,
                        "bounds": bounds,
                        "template": template,
                    },
                )
                assert result["loaded"] > 0 and result["errors"] == 0, result
                assert result["visible"] > 0, result
                assert result["projection"] == f"EPSG:{epsg}"
                page.screenshot(path=str(out / f"openlayers-{matrix_id}.png"))
                page.evaluate("window.gridMap.setTarget(undefined)")
            browser.close()
    finally:
        for process in processes:
            process.terminate()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        log.close()


if __name__ == "__main__":
    main()
