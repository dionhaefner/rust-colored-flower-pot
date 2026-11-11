"""Qualify logical keyed datasets across raster layouts and persisted STAC sources.

Raster derivatives copy checksum-pinned Sentinel crops. Calibration declarations
and band ordering exercise layout differences without generating expected pixels
from RCFP. References use rasterio/GDAL and NumPy independently.
"""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import io
import json
import os
import sqlite3
import subprocess
from pathlib import Path
from urllib.parse import quote, unquote, urlencode

import numpy as np
import rasterio
from PIL import Image
from rasterio.enums import Resampling
from rasterio.transform import from_bounds
from rasterio.warp import reproject

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "docs/examples/data"
NAMES = ["red", "green", "blue", "nir"]
TIME = "2021-06-29T10:00:00Z"
KEYS = ["collection", "datetime", "item", "asset"]
PREFIX = ["sentinel", TIME, "lagoon"]
SCALE, OFFSET = 0.01, -2.0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    binary, out = args.binary.resolve(), args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    os.environ["RCFP_COMPAT_PROFILE"] = "native"
    os.environ["TC_REPROJECTION_METHOD"] = "average"
    import rcfp

    assert Path(rcfp.__file__).resolve().is_relative_to(Path(os.sys.prefix).resolve())
    rows = []

    def case(name, function):
        try:
            function()
            row = {"case": name, "status": "passed"}
        except Exception as error:  # noqa: BLE001 - retain independent check outcomes
            row = {"case": name, "status": "failed", "error": repr(error)}
        rows.append(row)
        print(row, flush=True)

    def fails(function):
        try:
            function()
        except (rcfp.RcfpError, RuntimeError, ValueError):
            return
        raise AssertionError("invalid operation succeeded")

    manifest = json.loads((DATA / "manifest.json").read_text())
    for row in manifest["fixtures"]:
        assert (
            hashlib.sha256((DATA / row["file"]).read_bytes()).hexdigest()
            == row["sha256"]
        )
    separate = out / "separate"
    separate.mkdir()
    packed = out / "packed.tif"
    with rasterio.open(DATA / "red.tif") as source:
        profile = dict(source.profile)
    with rasterio.open(packed, "w", **dict(profile, count=4)) as target:
        for index, name in enumerate(NAMES, 1):
            with rasterio.open(DATA / f"{name}.tif") as source:
                pixels = source.read(1)
            target.write(pixels, index)
            target.set_band_description(index, name)
            with rasterio.open(separate / f"{name}.tif", "w", **profile) as single:
                single.write(pixels, 1)
                single.set_band_description(1, name)
                single.scales, single.offsets = [SCALE], [OFFSET]
        target.scales, target.offsets = [SCALE] * 4, [OFFSET] * 4

    item = copy.deepcopy(
        json.loads((DATA / "lagoon-multispectral-stac.json").read_text())
    )
    for name, asset in item["assets"].items():
        location = separate / f"{name}.tif"
        asset["href"] = location.as_uri()
        asset["file:checksum"] = (
            "1220" + hashlib.sha256(location.read_bytes()).hexdigest()
        )
        asset["raster:bands"][0].update(scale=SCALE, offset=OFFSET)
    document = out / "item.json"
    document.write_text(json.dumps(item))
    packed_item = copy.deepcopy(item)
    packed_item["assets"] = {
        "image": {
            "href": packed.as_uri(),
            "type": "image/tiff",
            "file:checksum": "1220" + hashlib.sha256(packed.read_bytes()).hexdigest(),
            "raster:bands": [{"scale": SCALE, "offset": OFFSET, "nodata": 0}] * 4,
            "eo:bands": [{"name": name, "common_name": name} for name in NAMES],
        }
    }
    packed_document = out / "packed-item.json"
    packed_document.write_text(json.dumps(packed_item))
    stores = {}
    for layout in ("separate", "packed", "managed", "managed-packed"):
        store = rcfp.DataStore(out / f"{layout}.sqlite")
        store.create(KEYS, format="native")
        stores[layout] = store
    for name in NAMES:
        stores["separate"].insert(
            [*PREFIX, name], separate / f"{name}.tif", values="scaled"
        )
        stores["packed"].insert(
            [*PREFIX, name], packed, band=NAMES.index(name) + 1, values="scaled"
        )
    assert stores["managed"].ingest(document) == 4
    assert stores["managed-packed"].ingest(packed_document) == 4
    stores["direct"] = rcfp.DataStore(document)
    stores["direct-packed"] = rcfp.DataStore(packed_document)

    edge = 20037508.342789244
    span = 2 * edge / 2**13
    bounds = [
        -edge + 4377 * span,
        edge - 2933 * span,
        -edge + 4378 * span,
        edge - 2932 * span,
    ]
    size = (128, 128)
    arrays = {}

    def sample(store, name):
        return store.get_raster_tile(
            [*PREFIX, name], tile_bounds=bounds, tile_size=size
        )

    def reference(name):
        with rasterio.open(separate / f"{name}.tif") as source:
            result = np.zeros(size, dtype=source.dtypes[0])
            reproject(
                source.read(1),
                result,
                src_transform=source.transform,
                src_crs=source.crs,
                src_nodata=0,
                dst_transform=from_bounds(*bounds, size[1], size[0]),
                dst_crs="EPSG:3857",
                dst_nodata=0,
                resampling=Resampling.average,
            )
        return np.ma.array(result * SCALE + OFFSET, mask=result == 0)

    def compare(left, right):
        np.testing.assert_array_equal(
            np.ma.getmaskarray(left), np.ma.getmaskarray(right)
        )
        np.testing.assert_allclose(
            left.compressed(), right.compressed(), rtol=0, atol=1e-8
        )

    def discovery():
        for store in stores.values():
            assert [row["key"] for row in store.get_keys()] == KEYS
            assert {tuple(row["keys"]) for row in store.get_datasets()} == {
                (*PREFIX, name) for name in NAMES
            }

    case("shared-key-discovery", discovery)
    for layout, store in stores.items():

        def pixels(store=store, layout=layout):
            arrays[layout] = {name: sample(store, name) for name in NAMES}
            for name in NAMES:
                compare(arrays[layout][name], reference(name))

        case(f"independent-calibrated-arrays-{layout}", pixels)

    def statistics():
        for name in NAMES:
            expected = stores["separate"].get_metadata([*PREFIX, name])
            for store in stores.values():
                metadata = store.get_metadata([*PREFIX, name])
                for field in (
                    "range",
                    "mean",
                    "stdev",
                    "percentiles",
                    "valid_percentage",
                    "bounds",
                ):
                    np.testing.assert_allclose(
                        metadata[field], expected[field], rtol=0, atol=1e-5
                    )

    case("selected-band-metadata-parity", statistics)

    prefix = "/".join(quote(value, safe="") for value in PREFIX)
    single = f"/singleband/{prefix}/red/13/4377/2932.png?" + urlencode(
        {"tile_size": json.dumps(size), "stretch_range": "[-2,18]"}
    )
    rgb = f"/rgb/{prefix}/13/4377/2932.png?" + urlencode(
        {
            "tile_size": json.dumps(size),
            "r": "red",
            "g": "green",
            "b": "blue",
            **{f"{c}_range": "[-2,18]" for c in "rgb"},
        }
    )
    compute = f"/compute/{prefix}/13/4377/2932.png?" + urlencode(
        {
            "tile_size": json.dumps(size),
            "v1": "nir",
            "v2": "red",
            "expression": "(v1-v2)/(v1+v2)",
            "stretch_range": "[-1,1]",
        }
    )
    apps = {
        layout: rcfp.NativeApplication(store.path) for layout, store in stores.items()
    }

    def decoded(app, route):
        status, _, body, _, _ = app.request("GET", route)
        assert status == 200, body
        return np.asarray(Image.open(io.BytesIO(body)).convert("RGBA"))

    for label, route in (("singleband", single), ("rgb", rgb), ("compute", compute)):

        def images(route=route):
            expected = decoded(apps["separate"], route)
            for app in apps.values():
                np.testing.assert_array_equal(decoded(app, route), expected)

        case(f"identical-client-requests-{label}", images)

    def adapters():
        from rcfp.wsgi import Application

        for layout in ("packed", "managed"):
            app = Application(stores[layout].path)
            path, query = single.split("?", 1)
            statuses = []
            body = b"".join(
                app(
                    {
                        "REQUEST_METHOD": "GET",
                        "PATH_INFO": unquote(path),
                        "QUERY_STRING": query,
                        "wsgi.input": io.BytesIO(),
                    },
                    lambda status, headers, statuses=statuses: statuses.append(status),
                )
            )
            assert statuses == ["200 OK"]
            np.testing.assert_array_equal(
                np.asarray(Image.open(io.BytesIO(body)).convert("RGBA")),
                decoded(apps[layout], single),
            )
            event = out / f"{layout}-event.json"
            event.write_text(
                json.dumps(
                    {
                        "version": "2.0",
                        "rawPath": path,
                        "rawQueryString": query,
                        "requestContext": {"http": {"method": "GET"}},
                    }
                )
            )
            result = subprocess.run(
                [
                    str(binary),
                    "lambda-event",
                    "--source",
                    stores[layout].path,
                    "--event",
                    str(event),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            assert result.returncode == 0, result.stderr
            response = json.loads(result.stdout)
            assert response["statusCode"] == 200, response
            np.testing.assert_array_equal(
                np.asarray(
                    Image.open(io.BytesIO(base64.b64decode(response["body"]))).convert(
                        "RGBA"
                    )
                ),
                decoded(apps[layout], single),
            )

    case("shared-wsgi-and-lambda-keyed-requests", adapters)

    def derived():
        for layout in ("separate", "packed", "managed", "managed-packed"):
            store = stores[layout]
            store.define(
                [*PREFIX, "ndvi"],
                "(nir-red)/(nir+red)",
                {name: [*PREFIX, name] for name in ("red", "nir")},
                stretch=(-1, 1),
                colormap="viridis",
            )
            compare(
                sample(store, "ndvi"),
                (arrays[layout]["nir"] - arrays[layout]["red"])
                / (arrays[layout]["nir"] + arrays[layout]["red"]),
            )
            implicit = f"/singleband/{prefix}/ndvi/13/4377/2932.png?" + urlencode(
                {"tile_size": json.dumps(size)}
            )
            explicit = implicit + "&stretch_range=[-1,1]&colormap=viridis"
            np.testing.assert_array_equal(
                decoded(apps[layout], implicit), decoded(apps[layout], explicit)
            )

    case("persisted-expressions-and-display-defaults", derived)

    def raw_values():
        store = rcfp.DataStore(out / "raw.sqlite")
        store.create(["band"], format="native")
        store.insert(["red"], packed, band=1, values="raw")
        raw = store.get_raster_tile(["red"], tile_bounds=bounds, tile_size=size)
        assert raw.dtype == np.dtype("uint16")
        compare(
            np.ma.array(raw.astype("float64") * SCALE + OFFSET, mask=raw.mask),
            arrays["packed"]["red"],
        )
        store.define(["product"], "v*v", {"v": ["red"]}, stretch=[0, 65535])
        product = store.get_raster_tile(["product"], tile_bounds=bounds, tile_size=size)
        compare(product, raw * raw)
        assert product.dtype == raw.dtype
        store.define(["chained"], "v+v", {"v": ["product"]}, stretch=[0, 65535])
        chained = store.get_raster_tile(["chained"], tile_bounds=bounds, tile_size=size)
        compare(chained, (raw * raw) + (raw * raw))
        store.define(["boolean"], "v>3000", {"v": ["red"]}, stretch=[0, 1])
        boolean = store.get_raster_tile(["boolean"], tile_bounds=bounds, tile_size=size)
        assert boolean.dtype == np.dtype("bool")
        compare(boolean, raw > 3000)
        store.define(["inverse"], "~v", {"v": ["boolean"]}, stretch=[0, 1])
        inverse = store.get_raster_tile(["inverse"], tile_bounds=bounds, tile_size=size)
        compare(inverse, ~(raw > 3000))
        store.define(["half"], "sqrt(v)", {"v": ["boolean"]}, stretch=[0, 1])
        half = store.get_raster_tile(["half"], tile_bounds=bounds, tile_size=size)
        assert half.dtype == np.dtype("float16")
        compare(half, np.sqrt(raw > 3000))
        store.define(["half_sum"], "v+v", {"v": ["half"]}, stretch=[0, 2])
        half_sum = store.get_raster_tile(
            ["half_sum"], tile_bounds=bounds, tile_size=size
        )
        compare(half_sum, np.sqrt(raw > 3000) + np.sqrt(raw > 3000))
        store.insert(["red"], packed, band=2, values="raw", skip_metadata=True)
        np.testing.assert_allclose(
            store.get_metadata(["red"])["range"],
            (
                np.array(stores["separate"].get_metadata([*PREFIX, "green"])["range"])
                - OFFSET
            )
            / SCALE,
            rtol=0,
            atol=1e-9,
        )

    case("raw-dtype-and-deferred-metadata-rebinding", raw_values)

    def mapped_import():
        store = rcfp.DataStore(out / "mapped.sqlite")
        store.create(["scene", "band"], format="native")
        assert (
            store.ingest(
                packed_document,
                key_mapping={"scene": "item", "band": "asset"},
                skip_metadata=True,
            )
            == 4
        )
        compare(
            store.get_raster_tile(
                ["lagoon", "green"], tile_bounds=bounds, tile_size=size
            ),
            arrays["packed"]["green"],
        )
        bad = copy.deepcopy(packed_item)
        bad["assets"]["image"]["eo:bands"][1]["common_name"] = "red"
        broken = out / "ambiguous.json"
        broken.write_text(json.dumps(bad))
        fails(
            lambda: store.ingest(broken, key_mapping={"scene": "item", "band": "asset"})
        )
        assert len(store.get_datasets()) == 4
        bad = copy.deepcopy(packed_item)
        bad["assets"]["image"]["file:checksum"] = "1220" + "0" * 64
        broken.write_text(json.dumps(bad))
        fails(
            lambda: store.ingest(broken, key_mapping={"scene": "item", "band": "asset"})
        )
        assert len(store.get_datasets()) == 4

    case("key-mapped-import-and-atomic-source-failures", mapped_import)

    def independent_images():
        def pixels(values, limits):
            data = np.rint(
                1
                + np.clip((values.data - limits[0]) / (limits[1] - limits[0]), 0, 1)
                * 254
            ).astype("uint8")
            data[np.ma.getmaskarray(values)] = 0
            return data

        expected = pixels(reference("red"), (-2, 18))
        image = decoded(apps["packed"], single)
        np.testing.assert_array_equal(image[:, :, 0], expected)
        np.testing.assert_array_equal(image[:, :, 3], np.where(expected == 0, 0, 255))
        expression = (reference("nir") - reference("red")) / (
            reference("nir") + reference("red")
        )
        expected = pixels(expression, (-1, 1))
        np.testing.assert_array_equal(
            decoded(apps["packed"], compute)[:, :, 0], expected
        )
        rgb_image = decoded(apps["packed"], rgb)
        for index, name in enumerate(NAMES[:3]):
            np.testing.assert_array_equal(
                rgb_image[:, :, index], pixels(reference(name), (-2, 18))
            )

    case("independent-decoded-display-and-expression-values", independent_images)

    def restart():
        document.unlink()
        packed_document.unlink()
        for layout in ("managed", "managed-packed"):
            store = rcfp.DataStore(stores[layout].path)
            compare(sample(store, "red"), arrays[layout]["red"])
            assert store.get_metadata([*PREFIX, "ndvi"])["metadata"]["defaults"][
                "stretch"
            ] == [-1, 1]
            with sqlite3.connect(store.path) as connection:
                assert connection.execute(
                    "SELECT format FROM rcfp_catalog"
                ).fetchone() == ("rcfp-native-2",)
                snapshot = json.loads(
                    connection.execute(
                        "SELECT definition FROM rcfp_sources"
                    ).fetchone()[0]
                )
                assert len(snapshot["items"]) == 1 and snapshot["origin"]["source"]

    case("database-snapshot-survives-source-document-removal", restart)

    def rollback():
        store = stores["packed"]
        before = sample(store, "red")
        fails(
            lambda: store.batch(
                [
                    {
                        "operation": "insert",
                        "keys": [*PREFIX, "red"],
                        "raster": str(packed),
                        "band": 2,
                        "values": "scaled",
                    },
                    {
                        "operation": "insert",
                        "keys": [*PREFIX, "invalid"],
                        "raster": str(packed),
                        "band": 5,
                    },
                ]
            )
        )
        compare(sample(store, "red"), before)
        assert not store.get_datasets(where={"asset": ["invalid"]})

    case("atomic-band-and-metadata-rollback", rollback)

    def invalid():
        store = stores["packed"]
        for band in (0, -1, 5, 1.5):
            fails(
                lambda band=band: store.insert([*PREFIX, "invalid"], packed, band=band)
            )
        fails(lambda: store.define([*PREFIX, "bad"], "x.y", {"x": [*PREFIX, "red"]}))
        fails(lambda: store.define([*PREFIX, "red"], "v", {"v": [*PREFIX, "red"]}))
        fails(lambda: store.define([*PREFIX, "red"], "v", {"v": [*PREFIX, "ndvi"]}))
        assert apps["packed"].request("GET", single + "&band=2")[0] == 400

    case("invalid-bindings-expressions-and-physical-selectors", invalid)

    def cache():
        store, app = stores["packed"], apps["packed"]
        before, before_expr = decoded(app, single), sample(store, "ndvi")
        before_range = store.get_metadata([*PREFIX, "ndvi"])["range"]
        store.insert([*PREFIX, "red"], packed, band=2, values="scaled")
        after = decoded(app, single)
        assert np.any(after != before)
        after_expr = sample(store, "ndvi")
        assert np.any(
            np.ma.getmaskarray(after_expr) != np.ma.getmaskarray(before_expr)
        ) or not np.allclose(after_expr.filled(0), before_expr.filled(0))
        assert store.get_metadata([*PREFIX, "ndvi"])["range"] != before_range
        store.insert([*PREFIX, "red"], packed, band=1, values="scaled")
        np.testing.assert_array_equal(decoded(app, single), before)

    case("band-and-derived-cache-invalidation", cache)

    def cli_ingest():
        location = out / "cli.sqlite"
        result = subprocess.run(
            [
                str(binary),
                "ingest",
                str(packed),
                "-o",
                str(location),
                "--bands",
                "red=1,green=2,blue=3,nir=4",
                "--band-key",
                "asset",
                "--values",
                "scaled",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        # Patterns need a key to retain the catalog model.
        assert result.returncode != 0
        source = out / "scene-lagoon.tif"
        source.write_bytes(packed.read_bytes())
        result = subprocess.run(
            [
                str(binary),
                "ingest",
                str(out / "scene-{scene}.tif"),
                "-o",
                str(location),
                "--bands",
                "red=1,green=2,blue=3,nir=4",
                "--values",
                "scaled",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        store = rcfp.DataStore(location)
        assert [row["key"] for row in store.get_keys()] == ["scene", "band"]
        compare(
            store.get_raster_tile(
                ["lagoon", "green"], tile_bounds=bounds, tile_size=size
            ),
            arrays["packed"]["green"],
        )

    case("cli-packed-ingest-through-common-model", cli_ingest)

    def legacy():
        store = rcfp.DataStore(out / "legacy.sqlite")
        store.create(["band"])
        store.insert(["red"], DATA / "red.tif")
        assert store.get_metadata(["red"])["range"][0] > 0
        fails(lambda: store.insert(["green"], packed, band=2))
        request = out / "fence.json"
        request.write_text(
            json.dumps({"operation": "keys", "path": stores["packed"].path})
        )
        result = subprocess.run(
            [
                str(binary),
                "--compat-profile",
                "terracotta",
                "data-operation",
                str(request),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
        assert result.returncode != 0 and "native" in result.stderr

    case("legacy-format-preserved-and-native-marker-fenced", legacy)
    case(
        "logical-array-future-contract",
        lambda: compare(
            stores["separate"]
            .get_raster_tile(
                [*PREFIX, "green"],
                tile_bounds=bounds,
                tile_size=size,
                asynchronous=True,
            )
            .result(),
            arrays["separate"]["green"],
        ),
    )

    report = {
        "binary": str(binary),
        "wheel": str(args.wheel.resolve()),
        "cases": rows,
        "exclusions": [],
    }
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    assert all(row["status"] == "passed" for row in rows), report


if __name__ == "__main__":
    main()
