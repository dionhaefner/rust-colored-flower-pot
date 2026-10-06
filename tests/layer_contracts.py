"""Native database-backed multiband contracts with independent raster references.

All ordinary data comes from checksum-pinned corpus windows. VRT band nodata,
scale/offset and alpha/mask derivatives isolate declarations absent from the real
corpus. No RCFP output is written as an expected result or reference golden.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
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
from xml.sax.saxutils import escape

import numpy as np
import rasterio
from PIL import Image
from rasterio.enums import ColorInterp, Resampling
from rasterio.transform import from_bounds
from rasterio.warp import reproject, transform_bounds

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests/fixtures/corpus"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--wheel",
        type=Path,
        help="Also check the installed Python/WSGI APIs of this wheel",
    )
    args = parser.parse_args()
    binary, out = args.binary.resolve(), args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    rows, exclusions = [], []
    env = dict(
        os.environ,
        RCFP_COMPAT_PROFILE="native",
        TC_RASTER_WORKERS="2",
        TC_RASTER_CACHE_BYTES="1048576",
    )

    def case(name, fn):
        try:
            fn()
            row = {"case": name, "status": "passed"}
        except Exception as error:  # noqa: BLE001 - retain every independent case outcome
            row = {"case": name, "status": "failed", "error": repr(error)}
        rows.append(row)
        print(row, flush=True)

    def cli(*command, profile="native", success=True):
        result = subprocess.run(
            [str(binary), "--compat-profile", profile, *map(str, command)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if success:
            assert result.returncode == 0, result.stderr
        else:
            assert result.returncode != 0, "invalid input succeeded"
        return result

    def operation(operation, **fields):
        instruction = out / "operation.json"
        instruction.write_text(json.dumps(dict(operation=operation, **fields)))
        return json.loads(cli("data-operation", instruction).stdout)

    for fixture in json.loads((CORPUS / "manifest.json").read_text())["fixtures"]:
        assert digest(CORPUS / fixture["file"]) == fixture["sha256"]
    with rasterio.open(CORPUS / "red.tif") as src:
        profile, bounds, crs, transform = (
            src.profile,
            list(src.bounds),
            src.crs,
            src.transform,
        )
    originals = {
        name: rasterio.open(CORPUS / f"{name}.tif")
        for name in ("red", "green", "blue", "nir", "classes", "elevation")
    }
    rgb = out / "packed.tif"
    with rasterio.open(rgb, "w", **dict(profile, count=4)) as dst:
        for i, name in enumerate(("red", "green", "blue", "nir"), 1):
            dst.write(originals[name].read(1), i)
            dst.set_band_description(i, name)
        dst.scales = (0.01, 0.02, 0.03, 0.04)
        dst.offsets = (1, 2, 3, 4)
        dst.units = ("reflectance",) * 4
        dst.colorinterp = (
            ColorInterp.red,
            ColorInterp.green,
            ColorInterp.blue,
            ColorInterp.undefined,
        )

    # Real per-band values with distinct nodata declarations (GTiff has one nodata tag).
    nodata = [int(originals[n].read(1)[0, 0]) for n in ("red", "green", "blue")]
    vrt = out / "per-band.vrt"
    bands = []
    for i, name in enumerate(("red", "green", "blue"), 1):
        bands.append(
            f'''<VRTRasterBand dataType="UInt16" band="{i}"><Description>{name}</Description><NoDataValue>{nodata[i - 1]}</NoDataValue><Scale>{i * 0.01}</Scale><Offset>{i}</Offset><UnitType>reflectance</UnitType><SimpleSource><SourceFilename relativeToVRT="0">{escape(str(CORPUS / f"{name}.tif"))}</SourceFilename><SourceBand>1</SourceBand><SrcRect xOff="0" yOff="0" xSize="512" ySize="512"/><DstRect xOff="0" yOff="0" xSize="512" ySize="512"/></SimpleSource></VRTRasterBand>'''
        )
    vrt.write_text(
        f'<VRTDataset rasterXSize="512" rasterYSize="512"><SRS>{escape(crs.to_wkt())}</SRS><GeoTransform>{",".join(map(str, transform.to_gdal()))}</GeoTransform>{"".join(bands)}</VRTDataset>'
    )

    alpha_path = out / "rgba.tif"
    with rasterio.open(CORPUS / "edge.tif") as src:
        alpha = src.read_masks(1)
    # A real edge mask supplies binary alpha; a fractional patch explicitly tests
    # positive Float32 alpha below one, unavailable in retained integer fixtures.
    fraction_path = out / "fractional-alpha.tif"
    for path, dtype, fractional in (
        (alpha_path, "uint16", False),
        (fraction_path, "float32", True),
    ):
        coverage = alpha.astype(dtype)
        if fractional:
            coverage[:32, :32] = 0.25
        with rasterio.open(
            path, "w", **dict(profile, count=4, dtype=dtype, nodata=None)
        ) as dst:
            for i, name in enumerate(("red", "green", "blue"), 1):
                dst.write(originals[name].read(1).astype(dtype), i)
            dst.write(coverage, 4)
            dst.colorinterp = (
                ColorInterp.red,
                ColorInterp.green,
                ColorInterp.blue,
                ColorInterp.alpha,
            )

    # Per-band VRT masks reference the real edge fixture's mask independently.
    masked_vrt = out / "per-band-mask.vrt"
    mask_source = f'<MaskBand><VRTRasterBand dataType="Byte"><SimpleSource><SourceFilename relativeToVRT="0">{escape(str(CORPUS / "edge.tif"))}</SourceFilename><SourceBand>mask,1</SourceBand><SrcRect xOff="0" yOff="0" xSize="512" ySize="512"/><DstRect xOff="0" yOff="0" xSize="512" ySize="512"/></SimpleSource></VRTRasterBand></MaskBand>'
    masked_vrt.write_text(
        vrt.read_text().replace("</VRTRasterBand>", mask_source + "</VRTRasterBand>", 1)
    )
    dataset_mask = out / "dataset-mask.tif"
    with (
        rasterio.Env(GDAL_TIFF_INTERNAL_MASK=False),
        rasterio.open(dataset_mask, "w", **dict(profile, count=3, nodata=None)) as dst,
    ):
        for i, name in enumerate(("red", "green", "blue"), 1):
            dst.write(originals[name].read(1), i)
        dst.write_mask(alpha)

    palette_path = out / "palette.tif"
    with rasterio.open(palette_path, "w", **originals["classes"].profile) as dst:
        dst.write(originals["classes"].read(1), 1)
        dst.write_colormap(
            1,
            {
                int(v): (int(v), 255 - int(v), 77, 255)
                for v in np.unique(originals["classes"].read(1))
            },
        )

    vertical_path = out / "three-axis.tif"
    with (
        rasterio.open(CORPUS / "elevation.tif") as src,
        rasterio.open(vertical_path, "w", **dict(src.profile, crs="EPSG:4979")) as dst,
    ):
        dst.write(src.read(1), 1)
        dst.units = ("ft",)

    # A lower-resolution real NIR window supplies deliberate grid alignment.
    low = out / "nir-low.tif"
    with rasterio.open(
        low,
        "w",
        **dict(
            profile,
            width=256,
            height=256,
            transform=transform * rasterio.Affine.scale(2),
        ),
    ) as dst:
        dst.write(originals["nir"].read(1)[::2, ::2], 1)
    byte_path = out / "byte.tif"
    with (
        rasterio.open(CORPUS / "classes.tif") as src,
        rasterio.open(byte_path, "w", **dict(src.profile, count=3, nodata=None)) as dst,
    ):
        for i in (1, 2, 3):
            dst.write(src.read(1), i)
        dst.colorinterp = (ColorInterp.red, ColorInterp.green, ColorInterp.blue)

    database = out / "layers.sqlite"
    cli("layers", "create", database)

    def asset(path, **fields):
        return dict(location=str(path), revision="original", **fields)

    def layer(assets):
        return {
            "observations": [
                {"id": "scene", "assets": assets, "attributes": {"date": "2021-06-29"}}
            ],
            "overlap": "first_valid",
        }

    def spec(
        bindings,
        expressions,
        *,
        ranges=None,
        values="raw",
        coverage="any_valid",
        epsg="EPSG:32632",
        grid_bounds=None,
        size=(64, 80),
        method="nearest",
        categories=None,
    ):
        if ranges is None:
            ranges = [[0, 3000]] * len(expressions)
        channels = [
            {
                "expression": e,
                "stretch": None if categories else r,
                "categories": categories or [],
            }
            for e, r in zip(expressions, ranges)
        ]
        return {
            "bindings": {k: {"asset": a, "band": b} for k, (a, b) in bindings.items()},
            "values": values,
            "coverage": coverage,
            "channels": channels,
            "grid": {
                "crs": epsg,
                "bounds": grid_bounds or bounds,
                "size": list(size),
                "resampling": method,
            },
            "encoding": "png",
        }

    packed = layer({"image": asset(rgb)})
    separate = layer(
        {n: asset(CORPUS / f"{n}.tif") for n in ("red", "green", "blue", "nir")}
    )
    rgb_spec = spec(
        {"r": ("image", "red"), "g": ("image", "green"), "b": ("image", "blue")},
        ["r", "g", "b"],
    )
    packed["default_render"] = rgb_spec
    for name, definition in (
        ("packed", packed),
        ("separate", separate),
        ("perband", layer({"image": asset(vrt)})),
        ("alpha", layer({"image": asset(alpha_path)})),
        ("fraction", layer({"image": asset(fraction_path)})),
        ("low", layer({"red": asset(CORPUS / "red.tif"), "nir": asset(low)})),
        (
            "classes",
            layer({"image": asset(CORPUS / "classes.tif", categorical_bands=[1])}),
        ),
        ("dem", layer({"image": asset(CORPUS / "elevation.tif")})),
        ("vertical", layer({"image": asset(vertical_path)})),
        ("permask", layer({"image": asset(masked_vrt)})),
        ("datasetmask", layer({"image": asset(dataset_mask)})),
        ("palette", layer({"image": asset(palette_path)})),
        ("aliases", layer({"image": asset(rgb, band_names={"infrared": 4})})),
    ):
        operation("layer_put", path=str(database), id=name, layer=definition)

    def samples(name, rendering):
        result = operation("layer_samples", path=str(database), id=name, spec=rendering)
        return [
            np.ma.array(
                np.array(r["values"]).reshape(r["shape"]),
                mask=np.array(r["mask"]).reshape(r["shape"]),
            )
            for r in result
        ]

    def reference(
        path,
        band,
        rendering,
        *,
        override_nodata=None,
        scale=1,
        offset=0,
        alpha_band=None,
    ):
        grid = rendering["grid"]
        with rasterio.open(path) as src:
            data = src.read(band)
            invalid = (src.read_masks(band) == 0) | ~np.isfinite(data)
            # Explicit GDAL masks can override nodata in read_masks. The layer
            # contract requires both declarations to contribute to coverage.
            nodata = src.nodatavals[band - 1]
            if nodata is not None:
                invalid |= data == nodata
            if override_nodata is not None:
                invalid |= data == override_nodata
            if alpha_band:
                invalid |= src.read(alpha_band) <= 0
            values = np.where(invalid, np.nan, data).astype("float64")
            destination = np.full(grid["size"], np.nan)
            reproject(
                values,
                destination,
                src_transform=src.transform,
                src_crs=src.crs,
                src_nodata=np.nan,
                dst_transform=from_bounds(
                    *grid["bounds"], grid["size"][1], grid["size"][0]
                ),
                dst_crs=grid["crs"],
                dst_nodata=np.nan,
                resampling=getattr(Resampling, grid["resampling"]),
            )
        return np.ma.array(destination * scale + offset, mask=~np.isfinite(destination))

    def same(actual, expected, atol=0):
        assert actual.shape == expected.shape
        np.testing.assert_array_equal(
            np.ma.getmaskarray(actual), np.ma.getmaskarray(expected)
        )
        np.testing.assert_allclose(
            actual.compressed(), expected.compressed(), rtol=0, atol=atol
        )

    case(
        "singleband",
        lambda: same(
            samples("packed", spec({"r": ("image", 1)}, ["r"]))[0],
            reference(CORPUS / "red.tif", 1, rgb_spec),
        ),
    )

    def rgb_values():
        for actual, name in zip(samples("packed", rgb_spec), ("red", "green", "blue")):
            same(actual, reference(CORPUS / f"{name}.tif", 1, rgb_spec))

    case("one-file-rgb-band-order", rgb_values)
    separate_spec = spec(
        {n: (n, 1) for n in ("red", "green", "blue")}, ["red", "green", "blue"]
    )
    case(
        "separate-file-rgb",
        lambda: [
            same(a, b)
            for a, b in zip(
                samples("packed", rgb_spec), samples("separate", separate_spec)
            )
        ],
    )
    ndvi_spec = spec(
        {"red": ("image", 1), "nir": ("image", 4)},
        ["(nir-red)/(nir+red)"],
        ranges=[[-1, 1]],
    )

    def expression():
        red = reference(CORPUS / "red.tif", 1, ndvi_spec)
        nir = reference(CORPUS / "nir.tif", 1, ndvi_spec)
        # UInt16 addition/subtraction retain the explicitly documented raw dtype.
        expected = (nir.astype("uint16") - red.astype("uint16")) / (
            nir.astype("uint16") + red.astype("uint16")
        )
        same(samples("packed", ndvi_spec)[0], expected)

    case("multispectral-raw-dtype-expression", expression)
    scaled_spec = copy.deepcopy(rgb_spec)
    scaled_spec["values"] = "scaled"
    case(
        "scale-offset-units",
        lambda: [
            same(
                a,
                reference(
                    CORPUS / f"{name}.tif", 1, scaled_spec, scale=i * 0.01, offset=i
                ),
                1e-12,
            )
            for i, (a, name) in enumerate(
                zip(samples("packed", scaled_spec), ("red", "green", "blue")), 1
            )
        ],
    )
    per_spec = spec(
        {"r": ("image", 1), "g": ("image", 2), "b": ("image", 3)},
        ["r", "g", "b"],
        size=(512, 512),
    )
    case(
        "per-band-nodata-channel-local",
        lambda: [
            same(
                a,
                reference(
                    CORPUS / f"{name}.tif", 1, per_spec, override_nodata=nodata[i]
                ),
            )
            for i, (a, name) in enumerate(
                zip(samples("perband", per_spec), ("red", "green", "blue"))
            )
        ],
    )
    case(
        "per-band-masks",
        lambda: [
            same(a, reference(masked_vrt, i + 1, per_spec))
            for i, a in enumerate(samples("permask", per_spec))
        ],
    )
    case(
        "external-dataset-mask",
        lambda: [
            same(a, reference(dataset_mask, i + 1, per_spec))
            for i, a in enumerate(samples("datasetmask", per_spec))
        ],
    )
    alias_spec = spec({"n": ("image", "infrared")}, ["n"])
    case(
        "explicit-band-alias",
        lambda: same(
            samples("aliases", alias_spec)[0],
            reference(CORPUS / "nir.tif", 1, alias_spec),
        ),
    )
    alpha_spec = spec({"r": ("image", 1)}, ["r"], size=(512, 512))
    case(
        "explicit-alpha",
        lambda: same(
            samples("alpha", alpha_spec)[0],
            reference(alpha_path, 1, alpha_spec, alpha_band=4),
        ),
    )
    case(
        "fractional-alpha",
        lambda: same(
            samples("fraction", alpha_spec)[0],
            reference(fraction_path, 1, alpha_spec, alpha_band=4),
        ),
    )
    low_spec = spec(
        {"red": ("red", 1), "nir": ("nir", 1)}, ["nir*1.0-red"], ranges=[[-3000, 3000]]
    )
    case(
        "different-resolutions-aligned-expression",
        lambda: same(
            samples("low", low_spec)[0],
            reference(low, 1, low_spec) - reference(CORPUS / "red.tif", 1, low_spec),
        ),
    )
    dem = originals["elevation"]
    dem_spec = spec(
        {"d": ("image", 1)},
        ["d"],
        ranges=[[-100, 1000]],
        epsg=str(dem.crs),
        grid_bounds=list(dem.bounds),
    )
    case(
        "float32-source-values",
        lambda: same(
            samples("dem", dem_spec)[0],
            reference(CORPUS / "elevation.tif", 1, dem_spec),
        ),
    )
    case(
        "raw-units-no-implicit-vertical-conversion",
        lambda: same(
            samples("vertical", dem_spec)[0],
            reference(CORPUS / "elevation.tif", 1, dem_spec),
        ),
    )
    for epsg in ("EPSG:3857", "EPSG:4326"):
        rendering = spec(
            {"r": ("image", 1)},
            ["r"],
            epsg=epsg,
            grid_bounds=list(transform_bounds(crs, epsg, *bounds)),
        )
        # The public GDAL warper uses its 0.125-pixel approximation; select a
        # small interior grid to avoid unstable nearest ties on geographic edges.
        case(
            "explicit-grid-" + epsg,
            lambda rendering=rendering: same(
                samples("packed", rendering)[0],
                reference(CORPUS / "red.tif", 1, rendering),
            ),
        )

    for method in ("nearest", "bilinear", "cubic", "average"):
        rendering = spec({"r": ("image", 1)}, ["r"], method=method)

        def resampled(rendering=rendering):
            expected = reference(CORPUS / "red.tif", 1, rendering)
            expected.data[:] = np.floor(expected.data + 0.5)
            same(samples("packed", rendering)[0], expected)

        case("source-dtype-resampling-" + method, resampled)

    def metadata():
        stored = operation("layer_get", path=str(database), id="packed")
        info = stored["observations"][0]["assets"]["image"]["metadata"]
        assert info["size"] == [512, 512]
        assert [b["name"] for b in info["bands"]] == ["red", "green", "blue", "nir"]
        assert info["bands"][3]["scale"] == 0.04
        assert info["bands"][3]["offset"] == 4
        assert info["bands"][0]["units"] == "reflectance"
        assert info["bands"][0]["dtype"] == "UInt16"
        np.testing.assert_array_equal(info["transform"], transform.to_gdal())
        with sqlite3.connect(database) as connection:
            assert connection.execute("SELECT format FROM rcfp_catalog").fetchone() == (
                "rcfp-native-1",
            )
            assert (
                connection.execute("SELECT count(*) FROM rcfp_layers").fetchone()[0]
                >= 8
            )

    case("database-metadata-and-marker", metadata)

    def rejected(rendering, message):
        instruction = out / "invalid.json"
        instruction.write_text(
            json.dumps(
                {
                    "operation": "layer_samples",
                    "path": str(database),
                    "id": "packed",
                    "spec": rendering,
                }
            )
        )
        assert message in cli("data-operation", instruction, success=False).stderr

    for band in (0, 5, -1, "missing"):
        invalid = copy.deepcopy(rgb_spec)
        invalid["bindings"]["r"]["band"] = band
        case(f"invalid-band-{band}", lambda invalid=invalid: rejected(invalid, "band"))
    for field, value, message in (
        ("crs", "AUTO", "EPSG"),
        ("size", [0, 10], "dimensions"),
        ("bounds", [1, 1, 0, 0], "bounds"),
    ):
        invalid = copy.deepcopy(rgb_spec)
        invalid["grid"][field] = value
        case(
            "invalid-grid-" + field,
            lambda invalid=invalid, message=message: rejected(invalid, message),
        )
    invalid = copy.deepcopy(rgb_spec)
    invalid["bindings"]["r"]["asset"] = "absent"
    case("invalid-asset", lambda: rejected(invalid, "unknown asset"))
    invalid_expression = copy.deepcopy(rgb_spec)
    invalid_expression["channels"][0]["expression"] = "missing"
    case(
        "invalid-expression", lambda: rejected(invalid_expression, "unrecognized name")
    )

    def rollback():
        before = digest(database)
        invalid = copy.deepcopy(packed)
        invalid["observations"][0]["assets"]["image"]["location"] = str(
            out / "absent.tif"
        )
        instruction = out / "invalid-put.json"
        instruction.write_text(
            json.dumps(
                {
                    "operation": "layer_put",
                    "path": str(database),
                    "id": "packed",
                    "layer": invalid,
                }
            )
        )
        cli("data-operation", instruction, success=False)
        assert digest(database) == before

    case("failed-registration-is-atomic", rollback)

    def profile():
        assert (
            "native profile"
            in cli(
                "layers", "list", database, profile="terracotta-0.10.1", success=False
            ).stderr
        )
        instruction = out / "legacy-native.json"
        instruction.write_text(json.dumps({"operation": "keys", "path": str(database)}))
        assert [
            row["key"] for row in json.loads(cli("data-operation", instruction).stdout)
        ] == ["layer", "observation", "channel"]
        assert (
            "native profile"
            in cli(
                "data-operation",
                instruction,
                profile="terracotta-0.10.1",
                success=False,
            ).stderr
        )
        instruction.write_text(
            json.dumps(
                {"operation": "keys", "path": str(database), "provider": "sqlite"}
            )
        )
        assert (
            "native layer catalog"
            in cli("data-operation", instruction, success=False).stderr
        )

    case("legacy-format-boundary", profile)

    def import_catalog():
        legacy = out / "legacy.sqlite"
        operation(
            "create",
            path=str(legacy),
            keys=[
                {"key": "arbitrary", "description": ""},
                {"key": "acquisition", "description": ""},
            ],
        )
        operation(
            "insert",
            path=str(legacy),
            keys=["red", "scene"],
            raster=str(CORPUS / "red.tif"),
            skip_metadata=True,
        )
        before = digest(legacy)
        operation(
            "layer_import",
            path=str(database),
            source=str(legacy),
            id="imported",
            bindings={"red": ["red", "scene"]},
        )
        assert digest(legacy) == before
        same(
            samples("imported", spec({"r": ("red", 1)}, ["r"]))[0],
            reference(CORPUS / "red.tif", 1, rgb_spec),
        )

    case("catalog-import-independent-of-final-key", import_catalog)

    def overlap():
        definition = layer({"image": asset(vrt)})
        definition["observations"][0]["attributes"]["selected"] = True
        other = copy.deepcopy(definition["observations"][0])
        other["id"] = "second"
        other["assets"]["image"]["location"] = str(rgb)
        other["attributes"]["selected"] = False
        definition["observations"].append(other)
        rendering = spec({"r": ("image", 1)}, ["r"], size=(512, 512))
        first = reference(CORPUS / "red.tif", 1, rendering, override_nodata=nodata[0])
        second = reference(CORPUS / "red.tif", 1, rendering)
        operation("layer_put", path=str(database), id="overlap", layer=definition)
        same(samples("overlap", rendering)[0], np.ma.where(~first.mask, first, second))
        definition["overlap"] = "last_valid"
        operation("layer_put", path=str(database), id="overlap", layer=definition)
        same(samples("overlap", rendering)[0], second)
        definition["selection"] = {"selected": True}
        operation("layer_put", path=str(database), id="overlap", layer=definition)
        same(samples("overlap", rendering)[0], first)

    case("selection-and-overlap", overlap)

    # Actual socket delivery also verifies method/content-type/error contracts.
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    server = subprocess.Popen(
        [str(binary), "serve", "-d", str(database), "--bind", f"127.0.0.1:{port}"],
        env=env,
        stdout=(out / "server.stdout").open("w"),
        stderr=(out / "server.stderr").open("w"),
    )
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(200):
            try:
                urllib.request.urlopen(base + "/layers", timeout=0.2).close()
                break
            except (OSError, urllib.error.URLError):
                if server.poll() is not None:
                    raise RuntimeError((out / "server.stderr").read_text())
                time.sleep(0.05)
        else:
            raise RuntimeError("service startup timeout")

        def http(method, route, body=None, content_type="application/json"):
            request = urllib.request.Request(
                base + route,
                data=None if body is None else json.dumps(body).encode(),
                headers={"Content-Type": content_type},
                method=method,
            )
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    return response.status, dict(response.headers), response.read()
            except urllib.error.HTTPError as response:
                return response.code, dict(response.headers), response.read()

        def image_check(name, rendering, expected):
            status, headers, data = http("POST", f"/layers/{name}/render", rendering)
            assert status == 200, data
            assert headers["Content-Type"] == "image/png"
            actual = np.array(Image.open(io.BytesIO(data)).convert("RGBA"))
            channels = []
            for r, channel in zip(expected, rendering["channels"]):
                lower, upper = channel["stretch"]
                pixels = np.rint(
                    1
                    + np.clip((r.filled(lower) - lower) * (1 / (upper - lower)), 0, 1)
                    * 254
                ).astype("uint8")
                pixels[r.mask] = 0
                channels.append(pixels)
            if len(channels) == 1:
                channels *= 3
            mask = (
                np.logical_and.reduce([np.ma.getmaskarray(r) for r in expected])
                if rendering["coverage"] == "any_valid"
                else np.logical_or.reduce([np.ma.getmaskarray(r) for r in expected])
            )
            wanted = np.stack(
                [*channels, np.where(mask, 0, 255).astype("uint8")], axis=-1
            )
            np.testing.assert_array_equal(actual, wanted)

        case(
            "http-decoded-rgb",
            lambda: image_check(
                "packed",
                rgb_spec,
                [
                    reference(CORPUS / f"{n}.tif", 1, rgb_spec)
                    for n in ("red", "green", "blue")
                ],
            ),
        )
        tiny_spec = spec({"r": ("image", 1)}, ["r*1e-12"], ranges=[[0, 3e-9]])
        case(
            "numeric-stretch-small-physical-interval",
            lambda: image_check(
                "packed",
                tiny_spec,
                [reference(CORPUS / "red.tif", 1, tiny_spec) * 1e-12],
            ),
        )
        for coverage in ("any_valid", "all_valid"):
            rendering = copy.deepcopy(per_spec)
            rendering["coverage"] = coverage
            case(
                "http-channel-coverage-" + coverage,
                lambda rendering=rendering: image_check(
                    "perband",
                    rendering,
                    [
                        reference(
                            CORPUS / f"{n}.tif", 1, rendering, override_nodata=nodata[i]
                        )
                        for i, n in enumerate(("red", "green", "blue"))
                    ],
                ),
            )

        def categories(name):
            source = originals["classes"]
            values = np.unique(source.read(1))
            colors = [
                {"value": int(v), "color": [int(v), 255 - int(v), 77, 255]}
                for v in values
                if v != source.nodata
            ]
            rendering = spec(
                {"c": ("image", 1)},
                ["c"],
                epsg=str(source.crs),
                grid_bounds=list(source.bounds),
                categories=colors,
                method="bilinear",
            )
            expected = reference(
                CORPUS / "classes.tif",
                1,
                dict(rendering, grid=dict(rendering["grid"], resampling="nearest")),
            )
            status, _, data = http("POST", f"/layers/{name}/render", rendering)
            assert status == 200, data
            actual = np.array(Image.open(io.BytesIO(data)).convert("RGBA"))
            for category in colors:
                valid = (~expected.mask) & (expected.data == category["value"])
                assert np.all(actual[valid] == category["color"])
            np.testing.assert_array_equal(actual[:, :, 3] == 0, expected.mask)
            rendering["values"] = "scaled"
            status, _, data = http("POST", f"/layers/{name}/render", rendering)
            assert status == 400 and b"raw" in data

        case("category-meaning-and-nearest", lambda: categories("classes"))
        case("embedded-palette-nearest", lambda: categories("palette"))
        case(
            "http-method",
            lambda: assert_equal(http("GET", "/layers/packed/render")[0], 405),
        )
        case(
            "http-content-type",
            lambda: assert_equal(
                http("POST", "/layers/packed/render", rgb_spec, "text/plain")[0], 400
            ),
        )
        case(
            "http-unknown-layer",
            lambda: assert_equal(http("POST", "/layers/absent/render", {})[0], 404),
        )
        case(
            "http-no-request-paths",
            lambda: assert_equal(
                http(
                    "POST",
                    "/layers/packed/render",
                    dict(rgb_spec, location="/tmp/other.tif"),
                )[0],
                400,
            ),
        )
        case(
            "http-stored-default",
            lambda: assert_equal(http("POST", "/layers/packed/render", {})[0], 200),
        )
        case(
            "http-head-discovery", lambda: assert_equal(http("HEAD", "/layers")[2], b"")
        )
        cli_image = out / "cli.png"
        cli("layers", "render", database, "packed", "-o", cli_image)
        case(
            "cli-render-default",
            lambda: assert_equal(
                np.array(Image.open(cli_image)).tolist(),
                np.array(
                    Image.open(io.BytesIO(http("POST", "/layers/packed/render", {})[2]))
                ).tolist(),
            ),
        )
        cli("layers", "register", database, "ordinary", byte_path)
        case(
            "cli-ordinary-rgb-registration",
            lambda: assert_equal(
                len(
                    operation("layer_get", path=str(database), id="ordinary")[
                        "default_render"
                    ]["channels"]
                ),
                3,
            ),
        )

        if args.wheel:
            import rcfp

            assert hasattr(rcfp, "LayerStore"), "install the tested wheel first"
            app = rcfp.LayerApplication(
                database,
                settings={"EXPOSE_METRICS": True, "RASTER_CACHE_BYTES": 1048576},
            )

            def python_api():
                assert "packed" in rcfp.LayerStore(database).list()
                assert rcfp.inspect_asset(rgb)["bands"][0]["dtype"] == "UInt16"
                for a, b in zip(
                    app.samples("packed", rgb_spec), samples("packed", rgb_spec)
                ):
                    same(a, b)
                image = np.array(
                    Image.open(io.BytesIO(app.render("packed", rgb_spec))).convert(
                        "RGBA"
                    )
                )
                assert image.shape == (64, 80, 4)

            case("installed-python-shared-core", python_api)

            def cache():
                def counters():
                    status, _, body, _, _ = app.request("GET", "/__terracotta/metrics")
                    assert status == 200
                    return json.loads(body)

                app.samples("packed", rgb_spec)
                before = counters()
                app.samples("packed", rgb_spec)
                cached = counters()
                assert cached["cache_hits"] >= before["cache_hits"] + 3
                alternate = copy.deepcopy(rgb_spec)
                alternate["bindings"]["r"]["band"] = 4
                same(
                    app.samples("packed", alternate)[0],
                    reference(CORPUS / "nir.tif", 1, alternate),
                )
                app.samples("packed", rgb_spec)
                updated = rcfp.LayerStore(database).get("packed")
                updated["observations"][0]["assets"]["image"]["revision"] = "new"
                rcfp.LayerStore(database).put("packed", updated)
                before = counters()
                same(
                    app.samples("packed", rgb_spec)[0],
                    reference(CORPUS / "red.tif", 1, rgb_spec),
                )
                after = counters()
                assert after["cache_misses"] >= before["cache_misses"] + 3
                scaled = copy.deepcopy(rgb_spec)
                scaled["values"] = "scaled"
                same(
                    app.samples("packed", scaled)[0],
                    reference(CORPUS / "red.tif", 1, scaled, scale=0.01, offset=1),
                )

            case("installed-cache-band-revision-scale", cache)

            def wsgi():
                from rcfp.wsgi import create_app

                application = create_app(database)
                stream = json.dumps(rgb_spec).encode()
                environ = {
                    "REQUEST_METHOD": "POST",
                    "PATH_INFO": "/layers/packed/render",
                    "QUERY_STRING": "",
                    "CONTENT_TYPE": "application/json",
                    "CONTENT_LENGTH": str(len(stream)),
                    "wsgi.input": io.BytesIO(stream),
                }
                statuses = []
                data = b"".join(
                    application(
                        environ, lambda status, headers: statuses.append(status)
                    )
                )
                assert statuses == ["200 OK"]
                assert Image.open(io.BytesIO(data)).size == (80, 64)

            case("installed-wsgi-layer-render", wsgi)
        else:
            exclusions.append(
                "Installed Python/WSGI/cache checks require --wheel and that exact wheel installed"
            )
    finally:
        server.terminate()
        try:
            server.wait(timeout=30)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()
        for src in originals.values():
            src.close()
    report = {
        "binary_sha256": digest(binary),
        "wheel_sha256": digest(args.wheel) if args.wheel else None,
        "fixtures": {
            p.name: digest(p)
            for p in (
                rgb,
                vrt,
                alpha_path,
                fraction_path,
                low,
                masked_vrt,
                dataset_mask,
                palette_path,
            )
        },
        "cases": rows,
        "exclusions": exclusions,
    }
    (out / "report.json").write_text(json.dumps(report, indent=2))
    assert all(row["status"] == "passed" for row in rows), "layer contracts failed"


def assert_equal(actual, expected):
    assert actual == expected, (actual, expected)


if __name__ == "__main__":
    main()
