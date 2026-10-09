"""Qualify installed Python compute layers with pinned Sentinel samples.

Rasterio independently aligns real pixels. Derivatives alter calibration, nodata
and resolution; model and external weather inputs are explicit test declarations.
No RCFP output is used as a raster or image oracle.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import math
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from socketserver import ThreadingMixIn
from urllib.parse import quote, urlencode
from wsgiref.simple_server import WSGIServer, make_server

import jsonschema
import numpy as np
import rasterio
import rcfp
from PIL import Image
from rasterio.enums import Resampling
from rasterio.transform import from_bounds
from rasterio.warp import reproject, transform_bounds
from rcfp.wsgi import Application as WSGI

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests/fixtures/corpus"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def decoded(response):
    assert response[0] == 200, response[:3]
    return np.asarray(Image.open(io.BytesIO(response[2])).convert("RGBA"))


def expected_gray(values, valid, stretch):
    pixels = np.clip((values - stretch[0]) / (stretch[1] - stretch[0]), 0, 1) * 254 + 1
    pixels = np.rint(pixels).astype("uint8")
    pixels[~valid] = 0
    return np.stack([pixels, pixels, pixels, valid.astype("uint8") * 255], axis=-1)


def reference(path, band, grid, *, scaled=False):
    shape = tuple(grid["size"])
    result = np.full(shape, np.nan, dtype="float64")
    with rasterio.open(path) as src:
        reproject(
            source=np.where(src.read_masks(band) > 0, src.read(band), np.nan).astype(
                "float64"
            ),
            destination=result,
            src_transform=src.transform,
            src_crs=src.crs,
            src_nodata=np.nan,
            dst_transform=from_bounds(*grid["bounds"], shape[1], shape[0]),
            dst_crs=grid["crs"],
            dst_nodata=np.nan,
            resampling=Resampling[grid["resampling"]],
        )
        valid = np.isfinite(result)
        if np.dtype(src.dtypes[band - 1]).kind in "iu":
            # Native GDAL warps retain the source band's integer sample precision.
            typed = np.zeros(shape, dtype=src.dtypes[band - 1])
            reproject(
                source=rasterio.band(src, band),
                destination=typed,
                src_transform=src.transform,
                src_crs=src.crs,
                src_nodata=src.nodatavals[band - 1],
                dst_transform=from_bounds(*grid["bounds"], shape[1], shape[0]),
                dst_crs=grid["crs"],
                dst_nodata=src.nodatavals[band - 1],
                resampling=Resampling[grid["resampling"]],
            )
            result = np.where(valid, typed, np.nan)
        if scaled:
            result = result * src.scales[band - 1] + src.offsets[band - 1]
    return np.nan_to_num(result), valid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--browser", action="store_true")
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    rows = []
    assert Path(rcfp.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()), (
        "Use an independently installed wheel"
    )
    manifest = json.loads((CORPUS / "manifest.json").read_text())
    for item in manifest["fixtures"]:
        assert digest(CORPUS / item["file"]) == item["sha256"]

    def case(name, check):
        try:
            check()
            row = {"case": name, "status": "passed"}
        except Exception as error:  # noqa: BLE001 - retain each independent outcome
            row = {
                "case": name,
                "status": "failed",
                "error": repr(error),
                "traceback": traceback.format_exc(),
            }
        rows.append(row)
        print(row, flush=True)

    with (
        rasterio.open(CORPUS / "red.tif") as red,
        rasterio.open(CORPUS / "nir.tif") as nir,
    ):
        packed = out / "satellite.tif"
        with rasterio.open(packed, "w", **dict(red.profile, count=2)) as dst:
            dst.write(red.read(1), 1)
            dst.write(nir.read(1), 2)
            dst.scales = (0.001, 0.002)
            dst.offsets = (-0.1, -0.2)
            dst.units = ("reflectance", "reflectance")
        bounds = transform_bounds(red.crs, "EPSG:3857", *red.bounds, densify_pts=21)
        coarse = out / "nir-coarse.tif"
        data = nir.read(1)[::2, ::2]
        with rasterio.open(
            coarse,
            "w",
            **dict(
                nir.profile,
                width=data.shape[1],
                height=data.shape[0],
                transform=nir.transform * rasterio.Affine.scale(2),
                nodata=int(data[0, 0]),
                dtype="float64",
            ),
        ) as dst:
            dst.write(data, 1)
        independent = out / "red-nodata.tif"
        with rasterio.open(
            independent,
            "w",
            **dict(red.profile, nodata=int(red.read(1)[0, 0]), dtype="float64"),
        ) as dst:
            dst.write(red.read(1), 1)
    grid = {
        "crs": "EPSG:3857",
        "bounds": list(bounds),
        "size": [64, 96],
        "resampling": "nearest",
    }
    database = out / "observations.sqlite"
    store = rcfp.LayerStore(database)
    store.create()
    layer = {
        "observations": [
            {
                "id": "2021-06-29",
                "attributes": {"date": "2021-06-29", "crop": "wheat"},
                "assets": {
                    "spectral": {"location": str(packed)},
                    "coarse": {"location": str(coarse)},
                    "red": {"location": str(independent)},
                },
            },
            {
                "id": "other-date",
                "attributes": {"date": "other-date"},
                "assets": {
                    "spectral": {"location": str(CORPUS / "red.tif")},
                    "coarse": {"location": str(coarse)},
                    "red": {"location": str(independent)},
                },
            },
        ],
        "overlap": "first_valid",
    }
    layer["default_render"] = {
        "bindings": {"red": {"asset": "spectral", "band": 1}},
        "values": "raw",
        "channels": [{"expression": "red", "stretch": [0, 3000]}],
        "coverage": "all_valid",
        "grid": grid,
        "encoding": "png",
    }
    store.put("satellite", layer)
    inputs = {
        "red": {"asset": "spectral", "band": 1, "values": "scaled"},
        "nir": {"asset": "spectral", "band": 2, "values": "scaled"},
    }
    registration = {
        "source": "satellite",
        "observation": "2021-06-29",
        "inputs": inputs,
        "version": "stress-1",
        "preview_grid": grid,
        "parameters": {
            "gain": {"type": "number", "default": 1, "minimum": 0, "maximum": 4}
        },
        "dependency_revisions": {
            "weather": "weather-2021-06-29-r1",
            "crop": "wheat-r1",
        },
        "display": {"stretch_range": [-1, 3]},
    }
    calls = []
    weather = {"value": 0.25}
    retained = []

    def model(values, valid, context):
        assert threading.current_thread().name.startswith("rcfp-compute")
        assert context.observation["id"] == "2021-06-29"
        assert context.dependency_revisions["weather"].startswith("weather-")
        for name in ("red", "nir"):
            assert (
                values[name].dtype == np.dtype("<f8")
                and values[name].flags.c_contiguous
            )
            assert (
                valid[name].dtype == np.bool_
                and not values[name].flags.writeable
                and not valid[name].flags.writeable
            )
            assert values[name].shape == tuple(context.grid["size"])
        assert context.inputs["nir"]["scale"] == 0.002
        mask = valid["red"] & valid["nir"] & (values["red"] + values["nir"] != 0)
        result = np.zeros(values["red"].shape)
        np.divide(
            values["nir"] - values["red"],
            values["nir"] + values["red"],
            out=result,
            where=mask,
        )
        result = result * context.parameters["gain"] + weather["value"]
        calls.append(context)
        retained.append(values["red"])
        return result, mask

    def independent_model(request_grid=grid, gain=1):
        red, rm = reference(packed, 1, request_grid, scaled=True)
        nir, nm = reference(packed, 2, request_grid, scaled=True)
        valid = rm & nm & (red + nir != 0)
        result = np.zeros(red.shape)
        np.divide(nir - red, nir + red, out=result, where=valid)
        return result * gain + weather["value"], valid

    application = rcfp.ComputeApplication(
        database,
        settings={
            "RASTER_WORKERS": 2,
            "RASTER_CACHE_BYTES": 8 * 1024 * 1024,
            "EXPOSE_METRICS": True,
        },
        workers=4,
        queue_capacity=8,
    )
    application.register("stress", model, **registration)
    keys = ["stress", "2021-06-29", "value"]
    preview = "/singleband/" + "/".join(map(quote, keys)) + "/preview.png"

    def discovery():
        result = json.loads(application.request("GET", "/tilesets?limit=1")[2])
        assert result["tilesets"][0]["keys"] == keys
        second = json.loads(application.request("GET", "/tilesets?page=1&limit=1")[2])
        assert second["tilesets"] and second["tilesets"][0]["keys"] != keys
        descriptor = result["tilesets"][0]
        tilejson = json.loads(
            application.request(
                "GET", descriptor["tilejson"], {"host": "127.0.0.1:9876"}
            )[2]
        )
        assert (
            tilejson["rcfp"]["keys"] == keys
            and tilejson["rcfp"]["compute"]["version"] == "stress-1"
        )
        assert tilejson["bounds"][0] > 12 and tilejson["bounds"][2] < 13
        assert "satellite.tif" not in json.dumps(tilejson)
        assert application.request("OPTIONS", preview)[0] == 204
        assert application.request("POST", preview, body=b"print('unsafe')")[0] == 405
        assert application.request("GET", preview + "?expression=unsafe")[0] == 400
        assert application.request("GET", "/keys")[0] == 200
        datasets = json.loads(application.request("GET", "/datasets?layer=stress")[2])
        assert datasets["datasets"] == [
            {"layer": "stress", "observation": "2021-06-29", "channel": "value"}
        ]
        spec = json.loads(application.request("GET", "/swagger.json")[2])
        for name, value in (("Tilesets", result), ("TileJSON", tilejson)):
            jsonschema.validate(
                value,
                {
                    "$ref": "#/components/schemas/" + name,
                    "components": spec["components"],
                },
            )
        assert descriptor["id"] in spec["x-rcfp-python-compute"]

    case("discovery, TileJSON and ordinary HTTP methods", discovery)

    def publication_urls():
        identity = json.loads(application.request("GET", "/tilesets")[2])["tilesets"][
            0
        ]["id"]
        path = "/tilejson/" + identity + ".json"
        assert application.request("GET", path)[0] == 400
        for host in (
            "user:password@private.test",
            "private.test/path",
            "private.test?q=1",
        ):
            assert application.request("GET", path, {"host": host})[0] == 400
        direct = application.request(
            "GET",
            path,
            {"Host": "private.test:9876", "x-forwarded-host": "attacker.test"},
        )
        assert direct[0] == 200
        assert json.loads(direct[2])["tiles"][0].startswith(
            "http://private.test:9876/tiles/"
        )
        prefix = "https://maps.example.test/imagery/"
        with rcfp.ComputeApplication(database, settings={"PUBLIC_URL": prefix}) as host:
            host.register("stress", model, **registration)
            listing = host.request("GET", "/tilesets")
            assert listing[0] == 200
            for descriptor in json.loads(listing[2])["tilesets"]:
                assert descriptor["tilejson"].startswith(prefix + "tilejson/")
                for matrix in descriptor["tileMatrixSets"]:
                    assert matrix["definition"].startswith(prefix + "tileMatrixSets/")
                    assert matrix["tiles"].startswith(prefix + "tiles/")
            response = host.request(
                "GET",
                path + "?gain=2&colormap=viridis",
                {"host": "private.test", "x-forwarded-host": "attacker.test"},
            )
            assert response[0] == 200
            template = json.loads(response[2])["tiles"][0]
            assert template.startswith(prefix + "tiles/")
            assert "gain=2" in template and "colormap=viridis" in template
            assert host.request("GET", path)[0] == 200

        with make_server("127.0.0.1", 0, None) as server:
            prefix = f"http://127.0.0.1:{server.server_port}/imagery"
            with rcfp.ComputeApplication(
                database, settings={"PUBLIC_URL": prefix}
            ) as host:
                host.register(
                    "stress",
                    lambda values, valid, context: (
                        values["red"] * context.parameters["gain"],
                        valid["red"],
                    ),
                    **registration,
                )
                app = WSGI(application=host)

                def mounted(environ, start_response):
                    assert environ["PATH_INFO"].startswith("/imagery/")
                    environ = dict(environ)
                    environ["SCRIPT_NAME"] = "/imagery"
                    environ["PATH_INFO"] = environ["PATH_INFO"][len("/imagery") :]
                    return app(environ, start_response)

                server.set_app(mounted)
                thread = threading.Thread(target=server.serve_forever)
                thread.start()
                try:
                    listing = json.load(urllib.request.urlopen(prefix + "/tilesets"))
                    tilejson = json.load(
                        urllib.request.urlopen(listing["tilesets"][0]["tilejson"])
                    )
                    west, south, east, north = tilejson["bounds"]
                    longitude, latitude = (west + east) / 2, (south + north) / 2
                    x = int((longitude + 180) / 360 * 2**13)
                    y = int(
                        (1 - math.asinh(math.tan(math.radians(latitude))) / math.pi)
                        / 2
                        * 2**13
                    )
                    tile = tilejson["tiles"][0]
                    for name, value in (("z", 13), ("x", x), ("y", y)):
                        tile = tile.replace("{" + name + "}", str(value))
                    with urllib.request.urlopen(tile) as response:
                        image = np.asarray(
                            Image.open(io.BytesIO(response.read())).convert("RGBA")
                        )
                    assert image.shape == (256, 256, 4) and image[..., 3].any()
                finally:
                    server.shutdown()
                    thread.join(timeout=10)
                    assert not thread.is_alive()

        for public_url in (
            "ftp://maps.example.test/imagery",
            "https://user:password@maps.example.test",
            "https://maps.example.test?token=secret",
            "https://maps.example.test#fragment",
        ):
            with rcfp.ComputeApplication(
                database, settings={"PUBLIC_URL": public_url}
            ) as host:
                host.register("stress", model, **registration)
                assert host.request("GET", path)[0] == 400
                assert host.request("GET", "/tilesets")[0] == 400

    case(
        "shared native publication URLs, deployment prefixes and proxy isolation",
        publication_urls,
    )

    def samples():
        response = application.request("GET", preview)
        data, valid = independent_model()
        np.testing.assert_array_equal(
            decoded(response), expected_gray(data, valid, [-1, 3])
        )
        assert len(calls) == 1
        assert retained[0].shape == (64, 96)
        assert response[4]["Cache-Control"] == "no-store"

    case("calibrated aligned binary inputs and independently rendered stress", samples)

    def caches():
        before = application.compute_metrics()
        first = application.request("GET", preview)
        second = application.request("GET", preview)
        after = application.compute_metrics()
        assert first[2] == second[2] and after["callbacks"] == before["callbacks"]
        assert after["images"]["hits"] > before["images"]["hits"]
        assert after["computed"]["hits"] > before["computed"]["hits"]
        native = json.loads(application.request("GET", "/__terracotta/metrics")[2])
        styled = application.request(
            "GET",
            preview
            + "?"
            + urlencode(
                {
                    "stretch_range": "[-1,2]",
                    "colormap": "viridis",
                    "color_transform": "gamma 1 0.8",
                }
            ),
        )
        assert styled[0] == 200 and styled[2] != first[2]
        assert application.compute_metrics()["callbacks"] == after["callbacks"]
        assert (
            json.loads(application.request("GET", "/__terracotta/metrics")[2])[
                "submitted"
            ]
            == native["submitted"]
        )
        assert styled[4]["ETag"] != first[4]["ETag"]
        assert (
            application.request("GET", preview, {"if-none-match": first[4]["ETag"]})[0]
            == 304
        )
        head = application.request("HEAD", preview)
        assert (
            head[0] == 200
            and head[2] == b""
            and int(head[4]["Content-Length"]) == len(first[2])
        )
        assert application.request("GET", preview + "?revision=obsolete")[0] == 409
        assert application.request("GET", preview + "?gain=8")[0] == 400
        assert application.request("GET", preview + "?colormap=unknown")[0] == 400
        assert application.request("GET", preview + "?gain=1&gain=2")[0] == 400

    case("separate raster, computed and encoded caches; style and validators", caches)

    def parameters_and_dependencies():
        before = len(calls)
        response = application.request("GET", preview + "?gain=2")
        data, valid = independent_model(gain=2)
        np.testing.assert_array_equal(
            decoded(response), expected_gray(data, valid, [-1, 3])
        )
        assert len(calls) == before + 1
        weather["value"] = 0.5
        changed = copy.deepcopy(registration)
        changed["dependency_revisions"]["weather"] = "weather-2021-06-29-r2"
        application.register("stress", model, **changed)
        assert application.request("GET", preview)[0] == 200
        assert len(calls) == before + 2
        changed["version"] = "stress-2"
        application.register("stress", model, **changed)
        assert (
            application.request("GET", preview)[0] == 200 and len(calls) == before + 3
        )
        application.register(
            "other-model", lambda v, m, c: (v["red"] * 0, m["red"]), **changed
        )
        other = application.request(
            "GET", "/singleband/other-model/2021-06-29/value/preview.png"
        )
        assert other[0] == 200 and not np.array_equal(decoded(other), decoded(response))

    case(
        "compute parameters, declared dependencies, versions and layer identity",
        parameters_and_dependencies,
    )

    def validity_and_resampling():
        variant = copy.deepcopy(registration)
        variant["inputs"] = {
            "red": {"asset": "red", "band": 1, "values": "raw"},
            "nir": {"asset": "coarse", "band": 1, "values": "raw"},
        }
        for method in ("nearest", "bilinear"):
            variant["preview_grid"]["resampling"] = method
            observations = []
            differences = []

            def inspect(
                values,
                valid,
                context,
                observations=observations,
                differences=differences,
            ):
                try:
                    for name, path in (("red", independent), ("nir", coarse)):
                        data, mask = reference(path, 1, variant["preview_grid"])
                        np.testing.assert_array_equal(valid[name], mask)
                        np.testing.assert_allclose(
                            values[name][mask], data[mask], rtol=0, atol=1e-7
                        )
                except AssertionError as error:
                    differences.append(str(error))
                    raise
                observations.append((valid["red"].copy(), valid["nir"].copy()))
                return values["red"] - values["nir"], valid["red"] & valid["nir"]

            application.register("masks", inspect, **variant)
            assert (
                application.request(
                    "GET", "/singleband/masks/2021-06-29/value/preview.png"
                )[0]
                == 200
            ), differences
            assert observations and np.any(observations[0][0] != observations[0][1])
        invalid = copy.deepcopy(registration)
        invalid["observation"] = "other-date"
        try:
            application.register("incoherent", model, **invalid)
        except ValueError:
            pass
        else:
            raise AssertionError("missing band from selected observation accepted")
        resized = application.request("GET", preview + "?tile_size=[32,48]")
        assert decoded(resized).shape == (32, 48, 4)
        assert application.request("GET", preview + "?tile_size=[2048,2048]")[0] == 400

    case(
        "independent per-band masks, mixed resolutions, resampling before compute and observation coherence",
        validity_and_resampling,
    )

    def output_errors():
        failures = [
            lambda v, m, c: (np.zeros((1, 1)), m["red"]),
            lambda v, m, c: (v["red"], m["red"].astype("uint8")),
            lambda v, m, c: (np.full(v["red"].shape, np.inf), m["red"]),
            lambda v, m, c: (v["red"].astype("complex128"), m["red"]),
            lambda v, m, c: v["red"],
        ]

        def raises(_v, _m, _c):
            raise ValueError("private-model-token-should-not-escape")

        failures.append(raises)
        for index, callback in enumerate(failures):
            changed = dict(registration, version=str(index))
            application.register("failed", callback, **changed)
            response = application.request(
                "GET", "/singleband/failed/2021-06-29/value/preview.png"
            )
            assert response[0] == 500 and b"private-model-token" not in response[2]
        changed = dict(registration, version="recovered")
        application.register("failed", lambda v, m, c: (v["red"], m["red"]), **changed)
        assert (
            application.request(
                "GET", "/singleband/failed/2021-06-29/value/preview.png"
            )[0]
            == 200
        )

        def masked_nonfinite(v, m, _c):
            result = v["red"].copy()
            result[:] = np.nan
            return result, np.zeros(result.shape, dtype=bool)

        application.register("empty", masked_nonfinite, **registration)
        assert not decoded(
            application.request("GET", "/singleband/empty/2021-06-29/value/preview.png")
        )[..., 3].any()

    case(
        "shape, dtype, boolean validity and finite-output failures are sanitized and recover",
        output_errors,
    )

    # The dynamic callback does not require a declared weather revision.
    def dynamic_model(values, valid, _context):
        calls.append(None)
        return values["red"], valid["red"]

    def uncached():
        application.register(
            "dynamic", dynamic_model, **dict(registration, dependency_revisions=None)
        )
        before = len(calls)
        for _ in range(2):
            assert (
                application.request(
                    "GET", "/singleband/dynamic/2021-06-29/value/preview.png"
                )[0]
                == 200
            )
        assert len(calls) == before + 2

    case("undeclared dynamic dependencies disable automatic result caching", uncached)

    def typed_parameters_and_bindings():
        variant = copy.deepcopy(registration)
        variant["parameters"].update(
            {
                "crop": {
                    "type": "string",
                    "default": "wheat",
                    "enum": ["wheat", "corn"],
                },
                "irrigated": {"type": "boolean", "default": False},
            }
        )
        variant["inputs"] = {
            "red": {"asset": "spectral", "band": "red", "values": "raw"},
            "nir": {"asset": "spectral", "band": "nir", "values": "scaled"},
        }
        declared = copy.deepcopy(layer)
        for observation in declared["observations"]:
            observation["assets"]["spectral"]["band_names"] = {"red": 1}
            if observation["id"] == "2021-06-29":
                observation["assets"]["spectral"]["band_names"]["nir"] = 2
        store.put("satellite", declared)
        observed = []

        def typed(values, valid, context):
            observed.append(context.parameters)
            return values["red"], valid["red"]

        application.register("typed", typed, **variant)
        variant["parameters"]["gain"]["default"] = 99
        url = "/singleband/typed/2021-06-29/value/preview.png"
        assert application.request("GET", url + "?crop=corn&irrigated=true")[0] == 200
        assert (
            observed[0]["gain"] == 1
            and observed[0]["crop"] == "corn"
            and observed[0]["irrigated"] is True
        )
        assert application.request("GET", url + "?crop=barley")[0] == 400
        assert application.request("GET", url + "?irrigated=1")[0] == 400
        assert application.request("GET", url + "?gain=NaN")[0] == 400

        async def asynchronous(_values, _valid, _context):
            return None

        try:
            application.register("async", asynchronous, **registration)
        except ValueError:
            pass
        else:
            raise AssertionError("asynchronous callback accepted")

    case(
        "named physical bands, typed query parameters and immutable registration",
        typed_parameters_and_bindings,
    )

    def budgets_and_native_reuse():
        with rcfp.ComputeApplication(
            database,
            settings={"EXPOSE_METRICS": True, "RASTER_CACHE_BYTES": 1048576},
            compute_cache_bytes=0,
            image_cache_bytes=0,
        ) as host:
            host.register(
                "uncached", lambda v, m, _c: (v["red"], m["red"]), **registration
            )
            url = "/singleband/uncached/2021-06-29/value/preview.png"
            assert host.request("GET", url)[0] == 200
            before = json.loads(host.request("GET", "/__terracotta/metrics")[2])
            assert host.request("GET", url + "?gain=2")[0] == 200
            after = json.loads(host.request("GET", "/__terracotta/metrics")[2])
            assert (
                before["submitted"] == after["submitted"]
                and after["cache_hits"] > before["cache_hits"]
            )
            assert (
                host.compute_metrics()["callbacks"] == 2
                and host.compute_metrics()["computed"]["bytes"]
                == host.compute_metrics()["images"]["bytes"]
                == 0
            )
        with rcfp.ComputeApplication(
            database, compute_cache_bytes=57600, image_cache_bytes=1
        ) as host:
            host.register(
                "bounded", lambda v, m, _c: (v["red"], m["red"]), **registration
            )
            url = "/singleband/bounded/2021-06-29/value/preview.png"
            for shape in ("[64,96]", "[65,96]", "[64,96]"):
                assert host.request("GET", url + "?tile_size=" + shape)[0] == 200
            metrics = host.compute_metrics()
            assert (
                metrics["computed"]["bytes"] <= 57600
                and metrics["computed"]["evictions"] == 2
                and metrics["callbacks"] == 3
            )
            assert metrics["images"]["bytes"] == 0

    case(
        "independent cache budgets, LRU eviction and native raster cache reuse",
        budgets_and_native_reuse,
    )

    def declared_source_revision():
        # Revision declarations change independently of the raster's local stamps.
        initial = application.request("GET", preview)
        before = len(calls)
        updated = copy.deepcopy(layer)
        updated["observations"][0]["assets"]["spectral"]["revision"] = (
            "satellite-revision-2"
        )
        store.put("satellite", updated)
        current = application.request("GET", preview)
        assert (
            current[0] == 200
            and len(calls) == before + 1
            and current[4]["ETag"] != initial[4]["ETag"]
        )

    case(
        "declared source revisions invalidate compute and image identity",
        declared_source_revision,
    )

    def categorical_bindings():
        declared = copy.deepcopy(layer)
        declared["default_render"] = None
        for observation in declared["observations"]:
            observation["assets"]["spectral"]["categorical_bands"] = [1]
        store.put("categories", declared)
        variant = copy.deepcopy(registration)
        variant["source"] = "categories"
        variant["inputs"] = {"label": {"asset": "spectral", "band": 1, "values": "raw"}}
        variant["display"] = {
            "colormap": "explicit",
            "explicit_color_map": {"1": [200, 20, 10, 128]},
        }

        def labels(values, valid, _context):
            return (values["label"] > 0).astype("uint8"), valid["label"]

        application.register("category-model", labels, **variant)
        image = decoded(
            application.request(
                "GET", "/singleband/category-model/2021-06-29/value/preview.png"
            )
        )
        assert np.array_equal(
            np.unique(image[image[..., 3] > 0], axis=0), [[200, 20, 10, 128]]
        )
        for values, resampling in (("scaled", "nearest"), ("raw", "bilinear")):
            variant["inputs"]["label"]["values"] = values
            variant["preview_grid"]["resampling"] = resampling
            try:
                application.register("bad-categories", labels, **variant)
            except ValueError:
                pass
            else:
                raise AssertionError(
                    "categorical calibration or interpolation accepted"
                )

    case(
        "categorical inputs require raw nearest values; shared palettes preserve alpha",
        categorical_bindings,
    )

    def remote_revisions():
        from request_cost import Origin

        origin = Origin()
        thread = threading.Thread(target=origin.serve_forever, daemon=True)
        thread.start()
        variant = copy.deepcopy(registration)
        variant["source"] = "remote-source"
        variant["inputs"] = {"red": {"asset": "spectral", "band": 1, "values": "raw"}}
        observed = []

        def remote(values, valid, _context):
            observed.append(1)
            return values["red"], valid["red"]

        def publish(revision, filename):
            store.put(
                "remote-source",
                {
                    "observations": [
                        {
                            "id": "2021-06-29",
                            "assets": {
                                "spectral": {
                                    "location": f"http://127.0.0.1:{origin.server_port}/{filename}",
                                    "revision": revision,
                                }
                            },
                        }
                    ],
                    "overlap": "first_valid",
                },
            )

        try:
            publish("", "unrevisioned.tif")
            with rcfp.ComputeApplication(database) as host:
                host.register("remote", remote, **variant)
                url = "/singleband/remote/2021-06-29/value/preview.png"
                for _ in range(2):
                    assert host.request("GET", url)[0] == 200
                assert (
                    len(observed) == 2
                    and host.compute_metrics()["computed"]["bytes"]
                    == host.compute_metrics()["images"]["bytes"]
                    == 0
                )
                publish("real-sentinel-red-v1", "immutable-red-v1.tif")
                first = host.request("GET", url)
                assert (
                    first[0] == 200
                    and host.request("GET", url)[0] == 200
                    and len(observed) == 3
                )
                assert host.compute_metrics()["computed"]["entries"] == 1
                publish("real-sentinel-red-v2", "immutable-red-v2.tif")
                second = host.request("GET", url)
                assert (
                    second[0] == 200
                    and len(observed) == 4
                    and second[4]["ETag"] != first[4]["ETag"]
                )
                assert first[2] == second[2]
            assert origin.requests > 0 and origin.bytes > 0
        finally:
            origin.shutdown()
            origin.server_close()
            thread.join()

    case(
        "remote unrevisioned inputs disable derived caching; immutable URLs and revisions separate results",
        remote_revisions,
    )

    def source_invalidation():
        before = len(calls)
        first = application.request("GET", preview)
        prior = retained[0].copy()
        with rasterio.open(packed, "r+") as dst:
            data = dst.read(2)
            data = np.minimum(data.astype("uint32") + 1000, 65535).astype("uint16")
            dst.write(data, 2)
        response = application.request("GET", preview)
        assert (
            response[0] == 200
            and len(calls) == before + 1
            and response[4]["ETag"] != first[4]["ETag"]
        )
        data, mask = independent_model()
        np.testing.assert_array_equal(
            decoded(response), expected_gray(data, mask, [-1, 3])
        )
        assert np.array_equal(prior, retained[0])

    case(
        "local source generation invalidates values and images while retained arrays stay alive",
        source_invalidation,
    )

    def concurrent():
        barrier = threading.Event()
        entered = threading.Event()
        counter = []

        def slow(v, m, _c):
            counter.append(1)
            entered.set()
            assert barrier.wait(5)
            return v["red"], m["red"]

        application.register("shared", slow, **registration)
        url = "/singleband/shared/2021-06-29/value/preview.png"
        with ThreadPoolExecutor(4) as clients:
            futures = [
                clients.submit(application.request, "GET", url) for _ in range(4)
            ]
            assert entered.wait(5)
            time.sleep(0.1)
            barrier.set()
            responses = [f.result(10) for f in futures]
        assert all(r[0] == 200 for r in responses) and len(counter) == 1
        assert len({r[2] for r in responses}) == 1

    case("concurrent identical computations share one callback", concurrent)

    def lifecycle():
        held = threading.Event()
        release = threading.Event()
        seen = []
        host = rcfp.ComputeApplication(
            database, workers=1, queue_capacity=1, timeout=0.35
        )

        def blocked(v, m, _c):
            seen.append(1)
            held.set()
            assert release.wait(10)
            return v["red"], m["red"]

        host.register("blocked", blocked, **registration)
        url = "/singleband/blocked/2021-06-29/value/preview.png"
        try:
            with ThreadPoolExecutor(3) as clients:
                first = clients.submit(host.request, "GET", url)
                assert held.wait(5)
                second = clients.submit(host.request, "GET", url + "?gain=2")
                for _ in range(100):
                    if host.compute_metrics()["admitted"] == 2:
                        break
                    time.sleep(0.005)
                assert host.request("GET", url)[0] == 503
                assert first.result(5)[0] == 504 and second.result(5)[0] == 504
                assert host.compute_metrics()["admitted"] == 2 and len(seen) == 1
                assert all(host.request("GET", url)[0] == 503 for _ in range(20))
                closing = clients.submit(host.close)
                time.sleep(0.05)
                assert not closing.done()
                assert host.request("GET", url)[0] == 503
                release.set()
                closing.result(5)
                assert host.compute_metrics()["admitted"] == 0
        finally:
            release.set()
            host.close()

    case(
        "bounded admission, queued expiry, active deadlines and draining shutdown",
        lifecycle,
    )

    def queued_shutdown():
        entered, release = threading.Event(), threading.Event()
        host = rcfp.ComputeApplication(
            database, workers=1, queue_capacity=1, timeout=10
        )

        def blocked(values, valid, _context):
            entered.set()
            assert release.wait(10)
            return values["red"], valid["red"]

        host.register("shutdown", blocked, **registration)
        url = "/singleband/shutdown/2021-06-29/value/preview.png"
        try:
            with ThreadPoolExecutor(3) as clients:
                active = clients.submit(host.request, "GET", url)
                assert entered.wait(5)
                queued = clients.submit(host.request, "GET", url + "?gain=2")
                for _ in range(100):
                    if host.compute_metrics()["admitted"] == 2:
                        break
                    time.sleep(0.005)
                closing = clients.submit(host.close)
                assert queued.result(5)[0] == 503
                assert not closing.done()
                release.set()
                assert active.result(5)[0] == 200
                closing.result(5)
                assert host.compute_metrics()["admitted"] == 0
        finally:
            release.set()
            host.close()

    case(
        "shutdown rejects queued callers and drains active results before native release",
        queued_shutdown,
    )

    def source_race():
        entered, release = threading.Event(), threading.Event()

        def race(v, m, _c):
            entered.set()
            assert release.wait(5)
            return v["red"], m["red"]

        application.register("race", race, **registration)
        with ThreadPoolExecutor(1) as clients:
            future = clients.submit(
                application.request,
                "GET",
                "/singleband/race/2021-06-29/value/preview.png",
            )
            assert entered.wait(5)
            with rasterio.open(packed, "r+") as dst:
                data = dst.read(1)
                data[0, 0] = int(data[0, 0]) + 1
                dst.write(data, 1)
            release.set()
            assert future.result(10)[0] == 409
        assert (
            application.request("GET", "/singleband/race/2021-06-29/value/preview.png")[
                0
            ]
            == 200
        )

    case(
        "source mutation during a callback cannot publish a stale model result",
        source_race,
    )

    class Server(ThreadingMixIn, WSGIServer):
        daemon_threads = True

    server = make_server(
        "127.0.0.1", 0, WSGI(application=application), server_class=Server
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"

    def http_workflow():
        tilesets = json.load(urllib.request.urlopen(base + "/tilesets"))
        descriptor = next(row for row in tilesets["tilesets"] if row["keys"] == keys)
        tilejson = json.load(urllib.request.urlopen(base + descriptor["tilejson"]))
        west, south, east, north = tilejson["bounds"]
        lon, lat = (west + east) / 2, (south + north) / 2
        zoom = 13
        x = int((lon + 180) / 360 * 2**zoom)
        y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * 2**zoom)
        template = tilejson["tiles"][0]
        url = (
            template.replace("{z}", str(zoom))
            .replace("{x}", str(x))
            .replace("{y}", str(y))
        )
        with ThreadPoolExecutor(4) as clients:
            images = list(
                clients.map(lambda _: urllib.request.urlopen(url).read(), range(4))
            )
        assert all(image == images[0] for image in images)
        edge = math.pi * 6378137
        delta = 2 * edge / 2**zoom
        requested = {
            "crs": "EPSG:3857",
            "bounds": [
                -edge + x * delta,
                edge - (y + 1) * delta,
                -edge + (x + 1) * delta,
                edge - y * delta,
            ],
            "size": [256, 256],
            "resampling": "nearest",
        }
        values, valid = independent_model(requested)
        np.testing.assert_array_equal(
            np.asarray(Image.open(io.BytesIO(images[0])).convert("RGBA")),
            expected_gray(values, valid, [-1, 3]),
        )
        assert np.asarray(Image.open(io.BytesIO(images[0])).convert("RGBA"))[
            ..., 3
        ].any()
        changed = (
            url
            + "?"
            + urlencode({"gain": 2, "stretch_range": "[-1,2]", "colormap": "viridis"})
        )
        styled = urllib.request.urlopen(changed).read()
        assert styled != images[0]
        legacy = base + "/singleband/" + "/".join(keys) + f"/{zoom}/{x}/{y}.png"
        assert urllib.request.urlopen(legacy).read() == images[0]

    case(
        "independently installed WSGI host serves concurrent satellite model tiles and style changes",
        http_workflow,
    )

    def documented_host_example():
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        command = [
            sys.executable,
            str(ROOT / "docs/examples/python_compute_host.py"),
            "--database",
            str(out / "example.sqlite"),
            "--red",
            str(CORPUS / "red.tif"),
            "--nir",
            str(CORPUS / "nir.tif"),
            "--date",
            "2021-06-29",
            "--bounds",
            *map(str, grid["bounds"]),
            "--weather",
            "0.25",
            "--weather-revision",
            "weather-2021-06-29-r1",
            "--port",
            str(port),
        ]
        with (out / "example.log").open("w") as log:
            process = subprocess.Popen(command, stdout=log, stderr=log)
            try:
                base = f"http://127.0.0.1:{port}"
                for _ in range(100):
                    try:
                        urllib.request.urlopen(base + "/keys", timeout=1).close()
                        break
                    except OSError:
                        assert process.poll() is None
                        time.sleep(0.05)
                preview = base + "/singleband/stress/2021-06-29/value/preview.png"
                with ThreadPoolExecutor(4) as clients:
                    images = list(
                        clients.map(
                            lambda _: urllib.request.urlopen(preview).read(), range(4)
                        )
                    )
                assert len(set(images)) == 1
                assert np.asarray(Image.open(io.BytesIO(images[0])).convert("RGBA"))[
                    ..., 3
                ].any()
            finally:
                process.terminate()
                process.wait(10)

    case(
        "published host example runs with installed public APIs and concurrent model requests",
        documented_host_example,
    )

    if args.browser:

        def browser():
            from playwright.sync_api import sync_playwright

            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            viewer_url = f"http://127.0.0.1:{port}"
            with (out / "viewer.log").open("w") as log:
                viewer = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "rcfp.cli",
                        "connect",
                        base,
                        "--port",
                        str(port),
                        "--no-browser",
                    ],
                    stdout=log,
                    stderr=log,
                )
                try:
                    for _ in range(100):
                        try:
                            urllib.request.urlopen(viewer_url, timeout=1).close()
                            break
                        except OSError:
                            assert viewer.poll() is None
                            time.sleep(0.05)
                    with sync_playwright() as playwright:
                        chromium = playwright.chromium.launch(
                            headless=True,
                            args=[
                                "--no-sandbox",
                                "--use-angle=swiftshader",
                                "--enable-unsafe-swiftshader",
                            ],
                        )
                        page = chromium.new_page()
                        for library in ("leaflet", "maplibre", "openlayers"):
                            responses = []
                            page.on(
                                "response",
                                lambda response, responses=responses: responses.append(
                                    (response.url, response.status)
                                ),
                            )
                            page.goto(viewer_url + "/examples/" + library + ".html")
                            page.wait_for_function(
                                "document.querySelector('#dataset')?.options.length > 0"
                            )
                            options = page.locator("#dataset option").evaluate_all(
                                "options => options.map(o => ({value:o.value,text:o.textContent}))"
                            )
                            selected = next(
                                option["value"]
                                for option in options
                                if option["text"] == "stress"
                            )
                            page.select_option("#dataset", selected)
                            page.wait_for_function(
                                "document.querySelector('#status')?.textContent === 'stress'",
                                timeout=30000,
                            )
                            page.wait_for_function(
                                "performance.getEntriesByType('resource').some(r=>r.name.includes('/tiles/'))"
                            )
                            page.wait_for_timeout(500)
                            assert any(
                                "/tiles/" in url and status == 200
                                for url, status in responses
                            )
                            page.screenshot(path=str(out / (library + ".png")))
                        page.goto(viewer_url)
                        page.get_by_label("Dataset", exact=True).select_option(
                            label="stress / 2021-06-29 / value"
                        )
                        page.wait_for_function(
                            "document.querySelector('#status')?.textContent.includes('2021-06-29 / value')"
                        )
                        chromium.close()
                finally:
                    viewer.terminate()
                    viewer.wait(10)

        case(
            "existing Leaflet, MapLibre and OpenLayers clients display discovered Python layers",
            browser,
        )
    server.shutdown()
    server.server_close()
    thread.join()
    metrics = application.compute_metrics()
    application.close()
    report = {
        "wheel_sha256": digest(args.wheel),
        "python": sys.version,
        "package": str(Path(rcfp.__file__).resolve()),
        "cases": rows,
        "passed": sum(row["status"] == "passed" for row in rows),
        "failed": sum(row["status"] == "failed" for row in rows),
        "metrics": metrics,
        "derivatives": {
            "packed": digest(packed),
            "coarse": digest(coarse),
            "independent_validity": digest(independent),
        },
        "fixtures": {item["file"]: item["sha256"] for item in manifest["fixtures"]},
        "exclusions": [
            "Remote WAN/provider revision qualification",
            "Spatial neighborhood padding and multiple-observation compositing",
            "GPU, subprocess, async callbacks and multiprocess cache sharing",
        ],
        "skips": [] if args.browser else ["Browser clients (use --browser)"],
        "unavailable": [],
    }
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    assert report["failed"] == 0, report


if __name__ == "__main__":
    main()
