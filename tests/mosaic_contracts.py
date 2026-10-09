"""Indexed mosaic contracts and bounded viewport measurements on pinned real pixels.

Mercator/cropped/decimated copies isolate seams and mixed grids. Repeated date
labels test selection without establishing acquisition history.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import copy
import importlib.util
import io
import json
import os
import socket
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
import rasterio
from navigation_benchmark import range_server
from PIL import Image
from rasterio.transform import from_bounds
from rasterio.warp import reproject, transform_bounds

ROOT = Path(__file__).resolve().parents[1]
module_spec = importlib.util.spec_from_file_location(
    "prepare_mosaic", ROOT / "packaging/prepare-mosaic.py"
)
prepare = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(prepare)


def same(actual, expected):
    assert np.array_equal(np.ma.getmaskarray(actual), np.ma.getmaskarray(expected))
    np.testing.assert_allclose(
        actual.compressed(), expected.compressed(), rtol=0, atol=1e-12
    )


def reference(path, spec, band=1):
    grid = spec["grid"]
    with rasterio.open(path) as src:
        values = src.read(band).astype("float64")
        values[(src.read_masks(band) == 0) | ~np.isfinite(values)] = np.nan
        if src.nodatavals[band - 1] is not None:
            values[values == src.nodatavals[band - 1]] = np.nan
        output = np.full(grid["size"], np.nan)
        reproject(
            values,
            output,
            src_transform=src.transform,
            src_crs=src.crs,
            src_nodata=np.nan,
            dst_transform=from_bounds(
                *grid["bounds"], grid["size"][1], grid["size"][0]
            ),
            dst_crs=grid["crs"],
            dst_nodata=np.nan,
            resampling=rasterio.enums.Resampling.nearest,
        )
    return np.ma.masked_invalid(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--wheel", type=Path)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    binary = args.binary.resolve()
    cli = prepare.Cli(binary)
    rows, measurements = [], []
    corpus = ROOT / "tests/fixtures/corpus"
    for fixture in json.loads((corpus / "manifest.json").read_text())["fixtures"]:
        assert prepare.digest(corpus / fixture["file"]) == fixture["sha256"]
    with rasterio.open(corpus / "red.tif") as src:
        bounds = list(
            transform_bounds(src.crs, "EPSG:3857", *src.bounds, densify_pts=21)
        )
    transform = from_bounds(*bounds, 512, 512)
    real = {}
    for name in ("red", "green", "nir", "classes", "edge"):
        with rasterio.open(corpus / f"{name}.tif") as src:
            data = np.full((512, 512), 0, dtype=src.dtypes[0])
            reproject(
                src.read(1),
                data,
                src_transform=src.transform,
                src_crs=src.crs,
                src_nodata=src.nodata,
                dst_transform=transform,
                dst_crs="EPSG:3857",
                dst_nodata=0,
                resampling=rasterio.enums.Resampling.nearest,
            )
            real[name] = data
    paths = {}

    def write(name, channels, start=0, end=512, step=1, masked=False):
        path = out / f"{name}.tif"
        data = np.stack([real[n][::step, start:end:step] for n in channels])
        profile = {
            "driver": "GTiff",
            "height": data.shape[1],
            "width": data.shape[2],
            "count": len(channels),
            "dtype": data.dtype,
            "nodata": 0,
            "crs": "EPSG:3857",
            "transform": transform
            * rasterio.Affine.translation(start, 0)
            * rasterio.Affine.scale(step),
            "tiled": True,
            "blockxsize": 128,
            "blockysize": 128,
            "compress": "deflate",
        }
        with rasterio.open(path, "w", **profile) as dst:
            dst.write(data)
            if masked:
                with rasterio.open(corpus / "edge.tif") as edge:
                    mask = np.zeros((512, 512), dtype="uint8")
                    reproject(
                        edge.read_masks(1),
                        mask,
                        src_transform=edge.transform,
                        src_crs=edge.crs,
                        dst_transform=transform,
                        dst_crs="EPSG:3857",
                        resampling=rasterio.enums.Resampling.nearest,
                    )
                dst.write_mask(mask[::step, start:end:step])
        paths[name] = path

    with rasterio.Env(GDAL_TIFF_INTERNAL_MASK=True):
        write("left", ["red", "nir"], end=320, masked=True)
        write("right", ["green", "nir"], start=256, step=2)
        write("category-left", ["classes"], end=320, masked=True)
        write("category-right", ["classes"], start=256, step=2)
    budget = {
        "max_sources": 4,
        "max_reads": 8,
        "max_buffer_bytes": 268435456,
        "max_source_bytes": 134217728,
    }
    observations = [
        {
            "id": n,
            "assets": {
                "image": {
                    "location": str(paths[n]),
                    "revision": prepare.digest(paths[n]),
                }
            },
            "attributes": {
                "date": "2021-06-29",
                "nested": {"a": 1, "b": 2},
                "offset": 0.0,
            },
        }
        for n in ("left", "right")
    ]
    spec = {
        "bindings": {"v": {"asset": "image", "band": 1}},
        "values": "raw",
        "channels": [{"expression": "v", "stretch": [0, 3000]}],
        "coverage": "any_valid",
        "grid": {
            "crs": "EPSG:3857",
            "bounds": bounds,
            "size": [512, 512],
            "resampling": "nearest",
        },
        "encoding": "png",
    }
    definition = {
        "observations": observations,
        "overlap": "first_valid",
        "mosaic": budget,
        "default_render": spec,
    }
    database = out / "mosaics.sqlite"
    cli("layer_create", path=str(database))
    cli("layer_put", path=str(database), id="mosaic", layer=definition)
    expected_left, expected_right = (
        reference(paths["left"], spec),
        reference(paths["right"], spec),
    )
    expected = np.ma.where(~expected_left.mask, expected_left, expected_right)

    def case(name, fn):
        try:
            fn()
            row = {"case": name, "status": "passed"}
        except Exception as error:  # noqa: BLE001 - retain independent failures
            row = {"case": name, "status": "failed", "error": repr(error)}
        rows.append(row)
        print(row, flush=True)

    def samples(name, request=spec):
        result = cli(
            "layer_samples", path=str(database), id=name, spec=request, provenance=True
        )
        arrays = [
            np.ma.array(
                np.array(s["values"]).reshape(s["shape"]),
                mask=np.array(s["mask"]).reshape(s["shape"]),
            )
            for s in result["samples"]
        ]
        return arrays, result

    def seam():
        arrays, result = samples("mosaic")
        same(arrays[0], expected)
        owners = np.array(
            [-1 if i is None else i for i in result["provenance"][0]]
        ).reshape(spec["grid"]["size"])
        assert result["contributors"] == ["left", "right"]
        wanted = np.where(~expected_left.mask, 0, np.where(~expected_right.mask, 1, -1))
        assert np.array_equal(owners, wanted)
        assert np.any(owners[:, 319] >= 0) and np.any(owners[:, 320] >= 0)

    case("adjacency-overlap-seams-mixed-resolution-mask-provenance", seam)

    def last_valid():
        layer = copy.deepcopy(definition)
        layer["overlap"] = "last_valid"
        cli("layer_put", path=str(database), id="last", layer=layer)
        same(
            samples("last")[0][0],
            np.ma.where(~expected_right.mask, expected_right, expected_left),
        )

    case("last-valid-order", last_valid)

    def mixed_crs():
        layer = copy.deepcopy(definition)
        layer["observations"][0]["assets"]["image"]["location"] = str(
            corpus / "red.tif"
        )
        cli("layer_put", path=str(database), id="mixed", layer=layer)
        first = reference(corpus / "red.tif", spec)
        same(samples("mixed")[0][0], np.ma.where(~first.mask, first, expected_right))

    case("mixed-source-crs", mixed_crs)

    def temporal():
        layer = copy.deepcopy(definition)
        other = copy.deepcopy(observations[1])
        other["id"] = "later"
        other["attributes"]["date"] = "2022-06-29"
        layer["observations"].append(other)
        cli("layer_put", path=str(database), id="dated", layer=layer)
        request = copy.deepcopy(spec)
        request["selection"] = {
            "date": "2022-06-29",
            "nested": {"b": 2, "a": 1},
            "offset": -0.0,
        }
        arrays, result = samples("dated", request)
        same(arrays[0], expected_right)
        assert result["contributors"] == ["later"]
        request["selection"]["date"] = "absent"
        assert samples("dated", request)[0][0].mask.all()
        layer["selection"] = {"date": "2021-06-29"}
        cli("layer_put", path=str(database), id="restricted", layer=layer)
        request["selection"]["date"] = "2022-06-29"
        assert samples("restricted", request)[0][0].mask.all()

    case("temporal-narrowing-empty-and-canonical-attributes", temporal)

    def cli_selection():
        layer = copy.deepcopy(definition)
        layer["mosaic"]["max_sources"] = 1
        for observation in layer["observations"]:
            observation["attributes"]["scene"] = observation["id"]
        layer["default_render"]["selection"] = {"scene": "right"}
        cli("layer_put", path=str(database), id="cli-selected", layer=layer)

        def render(request=None):
            image = out / "cli-selected.png"
            command = [
                str(binary),
                "--compat-profile",
                "native",
                "layers",
                "render",
                str(database),
                "cli-selected",
                "-o",
                str(image),
            ]
            if request is not None:
                request_path = out / "cli-selected-spec.json"
                request_path.write_text(json.dumps(request))
                command.extend(["--spec", str(request_path)])
            subprocess.run(command, check=True, capture_output=True, timeout=60)
            return np.array(Image.open(image).convert("RGBA"))

        def check(actual, expected):
            values = np.where(
                expected.mask,
                0,
                np.rint(1 + np.clip(expected.filled(0) / 3000, 0, 1) * 254),
            ).astype("uint8")
            wanted = np.stack(
                [values, values, values, np.where(expected.mask, 0, 255)], axis=-1
            ).astype("uint8")
            assert np.array_equal(actual, wanted)

        check(render(), expected_right)
        request = copy.deepcopy(spec)
        request["selection"] = {"scene": "left"}
        check(render(request), expected_left)
        try:
            render(spec)
        except subprocess.CalledProcessError as error:
            assert b"mosaic budget exceeded" in error.stderr
        else:
            raise AssertionError("unselected CLI mosaic exceeded its source budget")

    case("cli-default-and-explicit-selection-before-source-budget", cli_selection)

    category_definition = copy.deepcopy(definition)
    for o, n in zip(
        category_definition["observations"], ("category-left", "category-right")
    ):
        o["assets"]["image"].update(location=str(paths[n]), categorical_bands=[1])
    category_spec = copy.deepcopy(spec)
    category_spec["channels"] = [
        {
            "expression": "v",
            "stretch": None,
            "categories": [
                {"value": int(v), "color": [int(v), 255 - int(v), 77, 255]}
                for v in np.unique(real["classes"])
                if v
            ],
        }
    ]
    category_definition["default_render"] = category_spec
    cli("layer_put", path=str(database), id="categories", layer=category_definition)
    category_expected = np.ma.where(
        ~reference(paths["category-left"], category_spec).mask,
        reference(paths["category-left"], category_spec),
        reference(paths["category-right"], category_spec),
    )
    case(
        "categorical-mosaic-identity",
        lambda: same(samples("categories", category_spec)[0][0], category_expected),
    )

    expression_spec = copy.deepcopy(spec)
    expression_spec.update(
        bindings={
            "r": {"asset": "image", "band": 1},
            "n": {"asset": "image", "band": 2},
        },
        values="scaled",
        channels=[{"expression": "(n-r)/(n+r)", "stretch": [-1, 1]}],
    )

    def index_reference(path):
        r, n = reference(path, expression_spec, 1), reference(path, expression_spec, 2)
        return (n - r) / (n + r)

    index_expected = np.ma.where(
        ~index_reference(paths["left"]).mask,
        index_reference(paths["left"]),
        index_reference(paths["right"]),
    )
    case(
        "expression-before-overlap",
        lambda: same(samples("mosaic", expression_spec)[0][0], index_expected),
    )

    def product():
        manifest = prepare.prepare(
            cli,
            database,
            "mosaic",
            expression_spec,
            out / "product",
            factor=4,
            block=32,
        )
        with rasterio.open(out / "product/mosaic.tif") as src:
            actual = src.read(1, masked=True)
        values = index_expected.filled(0).reshape(128, 4, 128, 4)
        count = (~index_expected.mask).reshape(128, 4, 128, 4).sum(axis=(1, 3))
        reduced = np.divide(
            values.sum(axis=(1, 3)),
            count,
            out=np.full((128, 128), np.nan),
            where=count > 0,
        )
        same(actual, np.ma.masked_invalid(reduced))
        assert {
            key for block in manifest["blocks"] for key in block["contributors"]
        } == {"left", "right"}
        prepare.check(cli, out / "product/manifest.json")
        prepare.prepare(
            cli,
            database,
            "mosaic",
            expression_spec,
            out / "product-repeat",
            factor=4,
            block=32,
        )
        assert prepare.digest(out / "product/mosaic.tif") == prepare.digest(
            out / "product-repeat/mosaic.tif"
        )
        cli(
            "layer_put",
            path=str(database),
            id="broad",
            layer=json.loads((out / "product/layer.json").read_text()),
        )

    case("reproducible-expression-then-overlap-then-average-product", product)

    def freshness():
        changed = copy.deepcopy(definition)
        changed["overlap"] = "last_valid"
        cli("layer_put", path=str(database), id="mosaic", layer=changed)
        try:
            prepare.check(cli, out / "product/manifest.json")
        except ValueError as error:
            assert "stale" in str(error)
        else:
            raise AssertionError("stale definition accepted")
        cli("layer_put", path=str(database), id="mosaic", layer=definition)
        with rasterio.open(paths["left"], "r+") as dst:
            pixel = dst.read(1, window=Window(0, 0, 1, 1))
            dst.write(pixel + 1, 1, window=Window(0, 0, 1, 1))
        try:
            prepare.check(cli, out / "product/manifest.json")
        except ValueError as error:
            assert "stale" in str(error)
        else:
            raise AssertionError("changed parent bytes accepted")
        with rasterio.open(paths["left"], "r+") as dst:
            dst.write(pixel, 1, window=Window(0, 0, 1, 1))
        try:
            samples("mosaic")
        except subprocess.CalledProcessError as error:
            assert "mosaic source changed" in error.stderr
        else:
            raise AssertionError("changed source generation admitted")
        cli("layer_put", path=str(database), id="mosaic", layer=definition)

    from rasterio.windows import Window

    case("product-definition-and-parent-byte-freshness", freshness)

    def aggregation():
        try:
            prepare.prepare(
                cli,
                database,
                "categories",
                category_spec,
                out / "bad-average",
                factor=4,
                block=32,
            )
        except ValueError as error:
            assert "categorical" in str(error)
        else:
            raise AssertionError("category average accepted")
        assert not (out / "bad-average").exists()
        plain = copy.deepcopy(category_spec)
        plain["channels"] = [{"expression": "v", "stretch": [0, 255]}]
        prepare.prepare(
            cli,
            database,
            "categories",
            plain,
            out / "category-product",
            factor=4,
            block=32,
            aggregation="nearest",
        )
        with rasterio.open(out / "category-product/mosaic.tif") as src:
            same(src.read(1, masked=True), category_expected[2::4, 2::4])
        snapshot = json.loads((out / "category-product/layer.json").read_text())
        assert snapshot["observations"][0]["assets"]["image"]["categorical_bands"] == [
            1
        ]
        cli("layer_put", path=str(database), id="category-snapshot", layer=snapshot)
        request = snapshot["default_render"]
        request["grid"].update(size=[55, 61], resampling="bilinear")
        same(
            samples("category-snapshot", request)[0][0],
            reference(out / "category-product/mosaic.tif", request),
        )

    case("category-average-rejection-and-center-nearest-product", aggregation)

    def cleanup():
        too_big = copy.deepcopy(spec)
        too_big["bindings"] = {str(n): {"asset": "image", "band": 1} for n in range(16)}
        try:
            prepare.prepare(
                cli,
                database,
                "mosaic",
                too_big,
                out / "failed-product",
                factor=4,
                block=32,
            )
        except (subprocess.CalledProcessError, ValueError):
            pass
        else:
            raise AssertionError("invalid preparation succeeded")
        assert not (out / "failed-product/layer.json").exists()
        assert not (out / "failed-product/mosaic.tif").exists()

    case("failed-preparation-publishes-no-product", cleanup)

    def index_cleanup():
        with sqlite3.connect(database) as conn:
            plan = conn.execute(
                "EXPLAIN QUERY PLAN SELECT o.definition FROM rcfp_footprints f CROSS JOIN rcfp_observations o ON o.rowid=f.rowid WHERE o.layer=? AND f.west<? AND f.east>? AND f.south<? AND f.north>? ORDER BY o.ordinal LIMIT ?",
                ("mosaic", bounds[2], bounds[0], bounds[3], bounds[1], 5),
            ).fetchall()
            assert "VIRTUAL TABLE INDEX" in str(plan)
            assert "SCAN o" not in str(plan)
            (out / "query-plan.json").write_text(json.dumps(plan, indent=2))
        cli("layer_put", path=str(database), id="delete", layer=definition)
        cli("layer_delete", path=str(database), id="delete")
        with sqlite3.connect(database) as conn:
            assert (
                conn.execute(
                    "SELECT count(*) FROM rcfp_observations WHERE layer='delete'"
                ).fetchone()[0]
                == 0
            )
            assert (
                conn.execute(
                    "SELECT count(*) FROM rcfp_footprints WHERE rowid NOT IN (SELECT rowid FROM rcfp_observations)"
                ).fetchone()[0]
                == 0
            )

    case("rtree-plan-and-atomic-index-cleanup", index_cleanup)

    # The high-cardinality input is a placement derivative of real source pixels.
    # Registration includes every real TIFF. It is outside timed requests.
    many = copy.deepcopy(definition)
    many["observations"] = []
    with rasterio.open(paths["right"]) as src:
        data, profile = src.read(), src.profile
    for i in range(100):
        path = out / f"cell-{i}.tif"
        shifted = profile["transform"] * rasterio.Affine.translation(
            i * profile["width"], 0
        )
        with rasterio.open(path, "w", **dict(profile, transform=shifted)) as dst:
            dst.write(data)
        many["observations"].append(
            {
                "id": f"cell-{i}",
                "assets": {"image": {"location": str(path)}},
                "attributes": {"date": "2021-06-29"},
            }
        )
    full = list(
        rasterio.transform.array_bounds(
            profile["height"], profile["width"] * 100, profile["transform"]
        )
    )
    many["default_render"] = copy.deepcopy(spec)
    many["default_render"]["grid"].update(bounds=full, size=[256, 1024])
    cli("layer_put", path=str(database), id="many", layer=many)
    broad_base = copy.deepcopy(many["default_render"])
    broad_base["grid"]["size"] = [256, 12800]

    def prepare_broad():
        prepare.prepare(
            cli, database, "many", broad_base, out / "many-product", factor=16, block=8
        )
        cli(
            "layer_put",
            path=str(database),
            id="many-broad",
            layer=json.loads((out / "many-product/layer.json").read_text()),
        )

    case("bounded-broad-view-preparation", prepare_broad)

    origin_paths = {f"/cell-{i}.tif": out / f"cell-{i}.tif" for i in range(100)}
    if (out / "many-product/mosaic.tif").exists():
        origin_paths["/broad.tif"] = out / "many-product/mosaic.tif"
    origin_context = range_server(origin_paths, 2)
    origin, origin_counts = origin_context.__enter__()
    remote = copy.deepcopy(many)
    for i, observation in enumerate(remote["observations"]):
        observation["assets"]["image"]["location"] = origin + f"/cell-{i}.tif"
    cli("layer_put", path=str(database), id="many-http", layer=remote)
    if (out / "many-product/layer.json").exists():
        remote_broad = json.loads((out / "many-product/layer.json").read_text())
        remote_broad["observations"][0]["assets"]["image"]["location"] = (
            origin + "/broad.tif"
        )
        cli("layer_put", path=str(database), id="many-broad-http", layer=remote_broad)

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = dict(
        os.environ,
        RCFP_COMPAT_PROFILE="native",
        TC_RASTER_WORKERS="2",
        TC_RASTER_QUEUE_CAPACITY="16",
        TC_RASTER_CACHE_BYTES="0",
        TC_RENDER_CACHE_BYTES="0",
        TC_EXPOSE_METRICS="true",
        TC_ASYNC_TIFF="false",
    )
    process = subprocess.Popen(
        [str(binary), "serve", "-d", str(database), "--bind", f"127.0.0.1:{port}"],
        env=env,
        stdout=(out / "server.stdout").open("w"),
        stderr=(out / "server.stderr").open("w"),
    )
    base = f"http://127.0.0.1:{port}"

    def http(method, path, body=None):
        request = urllib.request.Request(
            base + path,
            data=None if body is None else json.dumps(body).encode(),
            method=method,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer mosaic-test",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    try:
        for _ in range(200):
            try:
                if http("GET", "/layers")[0] == 200:
                    break
            except OSError:
                if process.poll() is not None:
                    raise RuntimeError((out / "server.stderr").read_text())
                time.sleep(0.05)
        else:
            raise RuntimeError("startup timeout")

        def plan_check():
            status, data = http("POST", "/layers/mosaic/plan", spec)
            assert status == 200, data
            plan = json.loads(data)
            assert (
                plan["observations"] == ["left", "right"]
                and plan["reads"] == 2
                and plan["source_count"] == 2
            )
            outside = copy.deepcopy(spec)
            outside["grid"]["bounds"] = [
                bounds[2] + 10000,
                bounds[1],
                bounds[2] + 20000,
                bounds[3],
            ]
            assert (
                json.loads(http("POST", "/layers/mosaic/plan", outside)[1])[
                    "source_count"
                ]
                == 0
            )
            assert (
                np.array(
                    Image.open(
                        io.BytesIO(http("POST", "/layers/mosaic/render", outside)[1])
                    ).convert("RGBA")
                )[:, :, 3].max()
                == 0
            )

        case("http-plan-transparent-empty-extent", plan_check)

        def invalid_empty():
            request = copy.deepcopy(spec)
            request["selection"] = {"date": "absent"}
            request["channels"][0]["expression"] = "unknown + 1"
            before = json.loads(http("GET", "/__terracotta/metrics")[1])
            assert http("POST", "/layers/mosaic/render", request)[0] == 400
            assert http("POST", "/layers/mosaic/plan", request)[0] == 400
            after = json.loads(http("GET", "/__terracotta/metrics")[1])
            assert before["submitted"] == after["submitted"]

        case("invalid-expression-rejected-for-empty-selection", invalid_empty)

        def budgets():
            before = json.loads(http("GET", "/__terracotta/metrics")[1])
            source_before = dict(origin_counts)
            for field in budget:
                restricted = copy.deepcopy(definition)
                restricted["mosaic"][field] = 1
                cli("layer_put", path=str(database), id="budget", layer=restricted)
                status, data = http("POST", "/layers/budget/render", spec)
                assert status == 422, (field, status, data)
                assert json.loads(data)["message"] == "mosaic budget exceeded"
            assert http("POST", "/layers/many/render", {})[0] == 422
            assert http("POST", "/layers/many-http/render", {})[0] == 422
            after = json.loads(http("GET", "/__terracotta/metrics")[1])
            assert before["submitted"] == after["submitted"]
            assert (
                before["cost"]["raster_backend_reads"]
                == after["cost"]["raster_backend_reads"]
            )
            assert source_before == origin_counts

        case("every-budget-and-broad-coverage-rejected-before-read", budgets)

        def rss():
            return (
                int(
                    next(
                        line.split()[1]
                        for line in Path(f"/proc/{process.pid}/status")
                        .read_text()
                        .splitlines()
                        if line.startswith("VmRSS:")
                    )
                )
                * 1024
            )

        def measure(name, layer, requests):
            plans = [
                json.loads(http("POST", f"/layers/{layer}/plan", r)[1])
                for r in requests
            ]
            before = json.loads(http("GET", "/__terracotta/metrics")[1])
            source_before = dict(origin_counts)
            start, peak = time.perf_counter(), rss()
            responses = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
                futures = [
                    executor.submit(http, "POST", f"/layers/{layer}/render", r)
                    for r in requests
                ]
                while any(not f.done() for f in futures):
                    peak = max(peak, rss())
                    time.sleep(0.001)
                responses = [f.result() for f in futures]
            elapsed = time.perf_counter() - start
            after = json.loads(http("GET", "/__terracotta/metrics")[1])
            traffic = {
                key: value - source_before[key] for key, value in origin_counts.items()
            }
            for (status, data), request in zip(responses, requests):
                assert status == 200, data
                decoded = np.array(Image.open(io.BytesIO(data)).convert("RGBA"))
                assert (
                    np.count_nonzero(decoded[:, :, 3])
                    > decoded.shape[0] * decoded.shape[1] // 2
                )
                if "broad" in layer:
                    expected = reference(out / "many-product/mosaic.tif", request)
                else:
                    expected = np.ma.masked_all(request["grid"]["size"])
                    for i in range(100):
                        source = reference(out / f"cell-{i}.tif", request)
                        expected = np.ma.where(~expected.mask, expected, source)
                lo, hi = request["channels"][0]["stretch"]
                colors = np.where(
                    expected.mask,
                    0,
                    np.rint(
                        1 + np.clip((expected.filled(lo) - lo) / (hi - lo), 0, 1) * 254
                    ),
                ).astype("uint8")
                wanted = np.stack(
                    [
                        colors,
                        colors,
                        colors,
                        np.where(expected.mask, 0, 255).astype("uint8"),
                    ],
                    axis=-1,
                )
                assert np.array_equal(decoded, wanted)
            measurements.append(
                {
                    "workload": name,
                    "source_counts": [p["source_count"] for p in plans],
                    "planned_reads": [p["reads"] for p in plans],
                    "source_uncompressed_bytes": [p["source_bytes"] for p in plans],
                    "response_bytes": sum(len(d) for _, d in responses),
                    "viewport_seconds": elapsed,
                    "peak_service_rss_bytes": peak,
                    "statuses": [s for s, _ in responses],
                    "metrics_before": before,
                    "metrics_after": after,
                    "source_http": traffic,
                    "pixel_reads": {
                        key: value - before["cost"]["raster_backend_reads"][key]
                        for key, value in after["cost"]["raster_backend_reads"].items()
                    },
                }
            )

        high = copy.deepcopy(spec)
        high["grid"].update(
            bounds=list(
                rasterio.transform.array_bounds(
                    profile["height"], profile["width"], profile["transform"]
                )
            ),
            size=[256, 128],
        )
        many_broad = (
            json.loads((out / "many-product/layer.json").read_text())["default_render"]
            if (out / "many-product/layer.json").exists()
            else spec
        )

        def viewport(request):
            west, south, east, north = request["grid"]["bounds"]
            tiles = []
            for y in range(2):
                for x in range(2):
                    tile = copy.deepcopy(request)
                    tile["grid"].update(
                        bounds=[
                            west + (east - west) * x / 2,
                            south + (north - south) * y / 2,
                            west + (east - west) * (x + 1) / 2,
                            south + (north - south) * (y + 1) / 2,
                        ],
                        size=[max(1, n // 2) for n in request["grid"]["size"]],
                    )
                    tiles.append(tile)
            return tiles

        case(
            "measured-high-detail-four-request-viewport",
            lambda: measure("high-detail", "many", viewport(high)),
        )
        case(
            "measured-broad-four-request-viewport",
            lambda: measure("broad-product", "many-broad", viewport(many_broad)),
        )
        case(
            "measured-http-high-detail-viewport",
            lambda: measure("http-high-detail", "many-http", viewport(high)),
        )
        case(
            "measured-http-broad-product-viewport",
            lambda: measure(
                "http-broad-product", "many-broad-http", viewport(many_broad)
            ),
        )
    finally:
        process.terminate()
        process.wait(timeout=30)
        origin_context.__exit__(None, None, None)
    exclusions = [
        "WAN/cloud providers and other platforms",
        "Physically cold disk and browser paint",
        "Date labels reuse real pixels; acquisition correctness is outside this suite",
    ]
    if not args.wheel:
        exclusions.append(
            "Installed Python/WSGI mosaic additions (no --wheel supplied)"
        )
    else:

        def python_contract():
            import rcfp

            app = rcfp.LayerApplication(database)
            arrays, contributors, owners = app.samples("mosaic", spec, provenance=True)
            same(arrays[0], expected)
            assert contributors == ["left", "right"]
            assert owners[0].shape == tuple(spec["grid"]["size"])
            assert app.plan("mosaic", spec)["reads"] == 2

        case("installed-python-provenance-and-plan", python_contract)
    report = {
        "binary_sha256": prepare.digest(binary),
        "wheel_sha256": prepare.digest(args.wheel) if args.wheel else None,
        "fixtures": {p.name: prepare.digest(p) for p in paths.values()},
        "cases": rows,
        "measurements": measurements,
        "exclusions": exclusions,
        "evidence": "Independent Rasterio/NumPy samples; no service output serves as a golden",
    }
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    raise SystemExit(any(r["status"] != "passed" for r in rows))


if __name__ == "__main__":
    main()
