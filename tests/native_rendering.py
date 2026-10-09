"""Check direct native tiles against independent GDAL/Rasterio grid reads.

Pinned imagery covers UInt8/UInt16, Float32 and northern/southern UTM zones.
A reprojected southern DEM extends CRS coverage.
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import io
import json
import math
import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import urlopen

import numpy as np
import rasterio
from affine import Affine
from navigation_benchmark import ROOT, digest, native, service, tile
from PIL import Image
from rasterio.enums import ColorInterp, Resampling
from rasterio.transform import from_bounds
from rasterio.warp import calculate_default_transform, reproject, transform_bounds
from rasterio.windows import from_bounds as window_from_bounds


def bounds_for_tile(x, y, zoom):
    half = math.pi * 6378137
    size = 2 * half / 2**zoom
    return [
        -half + x * size,
        half - (y + 1) * size,
        -half + (x + 1) * size,
        half - y * size,
    ]


def warp_reference(source, destination, bounds, size, method):
    """Invoke GDAL's public warp utility independently of RCFP orchestration.

    GDALWarp AUTO selects source overviews that WarpedVRT alone would omit.
    A VRT avoids disk-warp chunk-dependent approximation. Check its grid first.
    """
    lib = ctypes.CDLL(os.environ["TERRACOTTA_GDAL_LIBRARY"])
    pointer = ctypes.c_void_p
    lib.GDALAllRegister()
    lib.GDALOpen.argtypes, lib.GDALOpen.restype = (
        [ctypes.c_char_p, ctypes.c_int],
        pointer,
    )
    lib.GDALWarpAppOptionsNew.argtypes = [ctypes.POINTER(ctypes.c_char_p), pointer]
    lib.GDALWarpAppOptionsNew.restype = pointer
    lib.GDALWarp.argtypes = [
        ctypes.c_char_p,
        pointer,
        ctypes.c_int,
        ctypes.POINTER(pointer),
        pointer,
        ctypes.POINTER(ctypes.c_int),
    ]
    lib.GDALWarp.restype = pointer
    lib.GDALWarpAppOptionsFree.argtypes = [pointer]
    lib.GDALClose.argtypes = [pointer]
    arguments = [
        "-of",
        "VRT",
        "-overwrite",
        "-t_srs",
        "EPSG:3857",
        "-te",
        *map(str, bounds),
        "-ts",
        str(size[1]),
        str(size[0]),
        "-r",
        "near" if method == Resampling.nearest else method.name,
        "-dstalpha",
        "-to",
        "INSERT_CENTER_LONG=FALSE",
    ]
    options = lib.GDALWarpAppOptionsNew(
        (ctypes.c_char_p * (len(arguments) + 1))(
            *[s.encode() for s in arguments], None
        ),
        None,
    )
    handle = lib.GDALOpen(str(source).encode(), 0)
    assert handle and options
    output = None
    try:
        error = ctypes.c_int()
        output = lib.GDALWarp(
            str(destination).encode(),
            None,
            1,
            (pointer * 1)(handle),
            options,
            ctypes.byref(error),
        )
        assert output and not error.value, "independent warp failed"
    finally:
        if output:
            lib.GDALClose(output)
        lib.GDALClose(handle)
        lib.GDALWarpAppOptionsFree(options)


def expected(source, bounds, size, method, destination, band=1):
    height, width = size
    with rasterio.open(source) as src:
        window = (
            window_from_bounds(*bounds, src.transform)
            if src.crs.to_epsg() == 3857 and src.transform.b == src.transform.d == 0
            else None
        )
        contained = (
            window is not None
            and window.col_off >= 0
            and window.row_off >= 0
            and window.col_off + window.width <= src.width
            and window.row_off + window.height <= src.height
        )
        if contained:
            values = src.read(
                band,
                out_shape=size,
                out_dtype="float64",
                window=window,
                resampling=method,
            )
            alpha = next(
                (
                    index
                    for index, interpretation in enumerate(src.colorinterp, 1)
                    if interpretation == ColorInterp.alpha
                ),
                None,
            )
            validity = (
                src.read(
                    alpha,
                    out_shape=size,
                    window=window,
                    resampling=Resampling.nearest,
                    out_dtype="float64",
                )
                > 0
                if alpha
                else (
                    src.read_masks(
                        band,
                        out_shape=size,
                        window=window,
                        resampling=Resampling.nearest,
                    )
                    != 0
                )
            )
            nodata = src.nodata
        else:
            warp_reference(source, destination, bounds, size, method)
            with rasterio.open(destination) as vrt:
                assert np.allclose(
                    tuple(vrt.transform),
                    tuple(from_bounds(*bounds, width, height)),
                    rtol=0,
                    atol=1e-8,
                )
                values = vrt.read(band, out_dtype="float64")
                validity = vrt.read(vrt.count) != 0
                nodata = vrt.nodata
        validity &= np.isfinite(values)
        if nodata is not None:
            validity &= values != nodata
        return values, validity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--resampling",
        choices=("bilinear", "nearest", "cubic", "average"),
        default="bilinear",
    )
    args = parser.parse_args()
    args.binary, args.out = args.binary.resolve(), args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=False)
    manifest = json.loads((ROOT / "tests/fixtures/navigation.json").read_text())
    sources = {
        key: ROOT / ".cache/corpus" / value["file"]
        for key, value in manifest["sources"].items()
    }
    for key, path in sources.items():
        assert digest(path) == manifest["sources"][key]["sha256"], key
    for item in json.loads(
        (ROOT / "tests/fixtures/geography-sources.json").read_text()
    ):
        path = ROOT / ".cache/raster-geography" / item["file"]
        assert digest(path) == item["sha256"], item["id"]
        sources[item["id"]] = path
    # Rotate the affine of real source pixels without resampling their values.
    with rasterio.open(sources["s2-red"]) as src:
        window = rasterio.windows.Window(8192, 4096, 512, 512)
        profile = dict(
            src.profile,
            width=512,
            height=512,
            transform=src.window_transform(window) * Affine.rotation(12),
        )
        with rasterio.open(args.out / "rotated-real.tif", "w", **profile) as dst:
            dst.write(src.read(window=window))
            dst.write_mask(src.read_masks(1, window=window))
    sources["rotated-real"] = args.out / "rotated-real.tif"
    # Real pixels with a shared mask and no per-band nodata exercise safe band pruning.
    with rasterio.open(sources["rgb-byte"]) as src:
        profile = dict(
            src.profile,
            count=4,
            nodata=None,
            tiled=True,
            blockxsize=256,
            blockysize=256,
        )
        with (
            rasterio.Env(GDAL_TIFF_INTERNAL_MASK=False),
            rasterio.open(args.out / "multiband-mask.tif", "w", **profile) as dst,
        ):
            dst.write(src.read(), indexes=[1, 2, 3])
            dst.write(src.read(1), 4)
            dst.write_mask(src.dataset_mask())
    sources["multiband-mask"] = args.out / "multiband-mask.tif"
    # Read-only GDAL overview creation writes an external .ovr beside real pixels.
    external = args.out / "external-overviews.tif"
    shutil.copyfile(sources["rgb-byte"], external)
    lib = ctypes.CDLL(os.environ["TERRACOTTA_GDAL_LIBRARY"])
    lib.GDALAllRegister()
    lib.GDALOpen.argtypes, lib.GDALOpen.restype = (
        [ctypes.c_char_p, ctypes.c_int],
        ctypes.c_void_p,
    )
    lib.GDALBuildOverviews.argtypes = [
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_int),
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_int),
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    lib.GDALClose.argtypes = [ctypes.c_void_p]
    handle = lib.GDALOpen(os.fsencode(external), 0)
    assert handle
    try:
        levels = (ctypes.c_int * 3)(2, 4, 8)
        assert (
            lib.GDALBuildOverviews(handle, b"NEAREST", 3, levels, 0, None, None, None)
            == 0
        )
    finally:
        lib.GDALClose(handle)
    assert Path(str(external) + ".ovr").is_file()
    sources["external-overviews"] = external
    # A three-axis CRS can trigger GDAL's vertical value/unit adjustments.
    with rasterio.open(ROOT / "tests/fixtures/corpus/elevation.tif") as src:
        profile = dict(src.profile, crs="EPSG:4979")
        with rasterio.open(args.out / "three-axis-dem.tif", "w", **profile) as dst:
            dst.write(src.read())
            dst.set_band_unit(1, "ft")
    sources["three-axis-dem"] = args.out / "three-axis-dem.tif"
    with rasterio.open(sources["dem-south"]) as src:
        transform, width, height = calculate_default_transform(
            src.crs, "EPSG:32734", src.width, src.height, *src.bounds
        )
        destination = args.out / "southern-utm.tif"
        profile = dict(
            src.profile,
            crs="EPSG:32734",
            transform=transform,
            width=width,
            height=height,
            nodata=-9999,
            tiled=True,
            blockxsize=256,
            blockysize=256,
        )
        with rasterio.open(destination, "w", **profile) as dst:
            reproject(
                rasterio.band(src, 1),
                rasterio.band(dst, 1),
                src_transform=src.transform,
                src_crs=src.crs,
                dst_transform=transform,
                dst_crs=dst.crs,
                dst_nodata=-9999,
                resampling=Resampling.bilinear,
            )
    sources["southern-utm"] = destination
    # Use a retained real crop for preparation checks and full scenes for performance.
    env = dict(
        os.environ,
        TC_NATIVE_DIRECT_READ="true",
        TC_REPROJECTION_METHOD="linear"
        if args.resampling == "bilinear"
        else args.resampling,
        TC_EXPOSE_METRICS="true",
        RCFP_COMPAT_PROFILE="native",
    )
    native(
        args.binary,
        args.out,
        env,
        {
            "operation": "optimize",
            "path": str(ROOT / "tests/fixtures/corpus/red.tif"),
            "output": str(args.out / "map-grid.tif"),
            "options": {"web_mercator_grid": True},
        },
    )
    sources["map-grid"] = args.out / "map-grid.tif"
    native(
        args.binary,
        args.out,
        env,
        {
            "operation": "optimize",
            "path": str(ROOT / "tests/fixtures/corpus/elevation.tif"),
            "output": str(args.out / "map-dem.tif"),
            "options": {"web_mercator_grid": True},
        },
    )
    sources["map-dem"] = args.out / "map-dem.tif"
    with rasterio.open(sources["map-dem"]) as src:
        values = src.read(1)
        valid = np.isfinite(values)
        profile = dict(src.profile, count=2, nodata=None)
    alpha = np.where(valid, 255.0, 0.0).astype("float32")
    # Fractional alpha is a targeted mask edge input over unchanged real DEM data.
    alpha[: alpha.shape[0] // 2][valid[: alpha.shape[0] // 2]] = 0.1
    with rasterio.open(args.out / "float-alpha.tif", "w", **profile) as dst:
        dst.write(np.where(valid, values, 0), 1)
        dst.write(alpha, 2)
        dst.colorinterp = (ColorInterp.gray, ColorInterp.alpha)
    sources["float-alpha"] = args.out / "float-alpha.tif"
    native(
        args.binary,
        args.out,
        env,
        {
            "operation": "optimize",
            "path": str(sources["rgb-byte"]),
            "output": str(args.out / "map-rgb.tif"),
            "options": {"web_mercator_grid": True, "all_bands": True},
        },
    )
    sources["map-rgb"] = args.out / "map-rgb.tif"
    # Ordinary projected assets need not use a map-aligned pixel lattice.
    with rasterio.open(sources["rgb-byte"]) as src:
        transform, width, height = calculate_default_transform(
            src.crs, "EPSG:3857", src.width, src.height, *src.bounds
        )
        profile = dict(
            src.profile,
            crs="EPSG:3857",
            transform=transform,
            width=width,
            height=height,
            tiled=True,
            blockxsize=256,
            blockysize=256,
        )
        with rasterio.open(args.out / "mercator-unaligned.tif", "w", **profile) as dst:
            reproject(
                source=rasterio.band(src, list(range(1, src.count + 1))),
                destination=rasterio.band(dst, list(range(1, src.count + 1))),
                resampling=Resampling.bilinear,
            )
    sources["mercator-unaligned"] = args.out / "mercator-unaligned.tif"
    with rasterio.open(sources["map-rgb"]) as src:
        assert src.count >= 3
    with rasterio.open(sources["map-grid"]) as src:
        assert src.crs.to_epsg() == 3857
        assert src.block_shapes[0] == (256, 256)
        resolution = src.transform.a
        half = math.pi * 6378137
        assert (
            abs(
                (src.transform.c + half) / resolution / 256
                - round((src.transform.c + half) / resolution / 256)
            )
            < 1e-7
        )
        assert (
            abs(
                (half - src.transform.f) / resolution / 256
                - round((half - src.transform.f) / resolution / 256)
            )
            < 1e-7
        )
    database = args.out / "catalog.sqlite"
    native(
        args.binary,
        args.out,
        env,
        {
            "operation": "driver_create",
            "path": str(database),
            "keys": ["scene", "band"],
        },
    )
    metadata = {}
    for key, path in sources.items():
        metadata[key] = native(
            args.binary,
            args.out,
            env,
            {
                "operation": "compute_metadata",
                "path": str(path),
                "options": {"max_shape": [512, 512]},
            },
        )
        native(
            args.binary,
            args.out,
            env,
            {
                "operation": "insert",
                "path": str(database),
                "keys": [key, "red"],
                "raster": str(path),
                "metadata": metadata[key],
            },
        )
    rows = []
    with service(args.binary, database, env, args.out, []) as (origin, _process):
        for key, path in sources.items():
            with rasterio.open(path) as src:
                west, south, east, north = transform_bounds(
                    src.crs, "EPSG:4326", *src.bounds
                )
                span = max(east - west, north - south)
                zoom = max(0, min(20, int(math.log2(360 / max(span, 1e-6)))))
            for position, lon, lat in (
                ("center", (west + east) / 2, (south + north) / 2),
                ("edge", west, north),
            ):
                for level in (zoom, zoom + 2):
                    x, y = tile(lon, lat, level)
                    bounds = bounds_for_tile(x, y, level)
                    for size in ((256, 256), (96, 192)):
                        labels = key == "worldcover"
                        method = (
                            Resampling.nearest
                            if labels
                            else Resampling[args.resampling]
                        )
                        values, valid = expected(
                            path, bounds, size, method, args.out / "oracle.vrt"
                        )
                        parameters = {
                            "tile_size": json.dumps(size),
                            "stretch_range": "[0,4000]",
                        }
                        if labels:
                            palette = {
                                v: [v, 255 - v, v, 255]
                                for v in (10, 20, 30, 40, 50, 60, 70, 80, 90, 95, 100)
                            }
                            parameters = {
                                "tile_size": json.dumps(size),
                                "colormap": "explicit",
                                "explicit_color_map": json.dumps(palette),
                            }
                            reference = np.zeros((*size, 4), dtype="uint8")
                            for label, color in palette.items():
                                reference[(values == label) & valid] = color
                        else:
                            pixels = np.where(
                                valid,
                                np.rint(1 + np.clip(values * (1 / 4000), 0, 1) * 254),
                                0,
                            ).astype("uint8")
                            reference = np.repeat(pixels[:, :, None], 4, axis=2)
                            reference[:, :, 3] = valid * 255
                        url = (
                            f"{origin}/singleband/{key}/red/{level}/{x}/{y}.png?"
                            + urlencode(parameters)
                        )
                        with urlopen(url, timeout=60) as response:
                            actual = np.asarray(
                                Image.open(io.BytesIO(response.read())).convert("RGBA")
                            )
                        assert actual.shape == reference.shape, (
                            key,
                            position,
                            level,
                            size,
                            "shape",
                        )
                        if not np.array_equal(actual[:, :, 3], reference[:, :, 3]):
                            Image.fromarray(actual).save(args.out / "actual.png")
                            Image.fromarray(reference).save(args.out / "reference.png")
                            print(
                                "mask mismatch",
                                key,
                                int(np.count_nonzero(actual[:, :, 3])),
                                int(np.count_nonzero(reference[:, :, 3])),
                                np.unique(values[valid], return_counts=True),
                            )
                        assert np.array_equal(actual[:, :, 3], reference[:, :, 3]), (
                            key,
                            position,
                            level,
                            size,
                            "alpha",
                        )
                        difference = np.abs(
                            actual[:, :, :3].astype("int16")
                            - reference[:, :, :3].astype("int16")
                        )
                        assert (
                            difference.max() == 0 if labels else difference.max() <= 1
                        ), (key, position, level, size, int(difference.max()))
                        rows.append(
                            {
                                "source": key,
                                "position": position,
                                "zoom": level,
                                "size": size,
                                "max_channel_difference": int(difference.max()),
                                "alpha": "exact",
                            }
                        )
                        if key in (
                            "rgb-byte",
                            "map-rgb",
                            "mercator-unaligned",
                            "multiband-mask",
                            "external-overviews",
                        ):
                            channels = []
                            for band in (1, 2, 3):
                                values, valid = expected(
                                    path,
                                    bounds,
                                    size,
                                    method,
                                    args.out / "oracle.vrt",
                                    band,
                                )
                                channels.append(
                                    np.where(
                                        valid,
                                        np.rint(
                                            1 + np.clip(values * (1 / 255), 0, 1) * 254
                                        ),
                                        0,
                                    ).astype("uint8")
                                )
                            reference = np.stack(channels, axis=2)
                            parameters = {
                                "tile_size": json.dumps(size),
                                "r": "red",
                                "g": "red",
                                "b": "red",
                                "r_band": 1,
                                "g_band": 2,
                                "b_band": 3,
                                "r_range": "[0,255]",
                                "g_range": "[0,255]",
                                "b_range": "[0,255]",
                            }
                            url = (
                                f"{origin}/rgb/{key}/{level}/{x}/{y}.png?"
                                + urlencode(parameters)
                            )
                            with urlopen(url, timeout=60) as response:
                                png_payload = response.read()
                                actual = np.asarray(
                                    Image.open(io.BytesIO(png_payload)).convert("RGBA")
                                )
                            assert np.array_equal(
                                actual[:, :, 3] != 0, np.any(reference != 0, axis=2)
                            ), (key, position, level, size, "RGB mask")
                            assert (
                                np.abs(
                                    actual[:, :, :3].astype("int16")
                                    - reference.astype("int16")
                                ).max()
                                <= 1
                            ), (key, position, level, size, "RGB samples")
                            webp_url = url + "&format=webp&quality=85"
                            with urlopen(webp_url, timeout=60) as response:
                                assert response.headers["Content-Type"] == "image/webp"
                                payload = response.read()
                                display = np.asarray(
                                    Image.open(io.BytesIO(payload)).convert("RGBA")
                                )
                            assert display.shape == actual.shape
                            assert np.array_equal(display[:, :, 3], actual[:, :, 3]), (
                                key,
                                position,
                                level,
                                size,
                                "WebP coverage",
                            )
                            error = np.abs(
                                display[:, :, :3].astype("int16")
                                - actual[:, :, :3].astype("int16")
                            )
                            rows.append(
                                {
                                    "source": key,
                                    "position": position,
                                    "zoom": level,
                                    "size": size,
                                    "format": "webp",
                                    "alpha": "exact",
                                    "max_display_channel_error": int(error.max()),
                                    "png_bytes": len(png_payload),
                                    "webp_bytes": len(payload),
                                }
                            )
                            rows.append(
                                {
                                    "source": key,
                                    "mode": "multiband-rgb",
                                    "position": position,
                                    "zoom": level,
                                    "size": size,
                                    "mask": "exact",
                                }
                            )
    event_url = urlsplit(webp_url)
    event = args.out / "lambda-event.json"
    event.write_text(
        json.dumps(
            {
                "version": "2.0",
                "rawPath": event_url.path,
                "rawQueryString": event_url.query,
                "requestContext": {"http": {"method": "GET"}},
            }
        )
    )
    result = subprocess.run(
        [str(args.binary), "lambda-event", "-d", str(database), "--event", str(event)],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    reply = json.loads(result.stdout)
    assert reply["statusCode"] == 200 and reply["isBase64Encoded"] is True
    assert reply["headers"]["content-type"] == "image/webp"
    adapted = np.asarray(
        Image.open(io.BytesIO(base64.b64decode(reply["body"]))).convert("RGBA")
    )
    assert adapted.shape == display.shape and np.array_equal(
        adapted[:, :, 3], display[:, :, 3]
    )
    rows.append({"adapter": "lambda-event", "format": "webp", "alpha": "exact"})
    (args.out / "report.json").write_text(
        json.dumps({"binary_sha256": digest(args.binary), "cases": rows}, indent=2)
        + "\n"
    )
    print(f"Passed {len(rows)} independent grid, mask, category and dimension checks")


if __name__ == "__main__":
    main()
