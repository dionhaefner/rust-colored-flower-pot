"""Check raster correctness against independent expectations on pinned real inputs."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import io
import json
import os
import platform
import shutil
import subprocess
import tarfile
import warnings
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

import numpy as np
import rasterio
from PIL import Image
from rasterio.enums import ColorInterp, Resampling
from rasterio.transform import Affine
from rasterio.warp import calculate_default_transform, reproject, transform_bounds
from rasterio.windows import Window
from shapely.geometry import MultiPoint, shape

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "tests/fixtures/corpus"
METHODS = ("nearest", "linear", "cubic", "average")
PROFILES = ("native", "terracotta-0.10.1")
GDAL_TYPES = {
    "uint8": 1,
    "uint16": 2,
    "int16": 3,
    "uint32": 4,
    "int32": 5,
    "float32": 6,
    "float64": 7,
}


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, default=lambda x: x.tolist()) + "\n")


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def fixtures(out, cache, download):
    sources = json.loads((ROOT / "tests/fixtures/geography-sources.json").read_text())
    cache.mkdir(parents=True, exist_ok=True)
    for source in sources:
        path = cache / source["file"]
        if not path.exists():
            require(download, f"Missing {path}; acquire pinned inputs with --download")
            temporary = path.with_suffix(".download")
            try:
                with (
                    urlopen(source["url"], timeout=60) as response,
                    temporary.open("wb") as target,
                ):
                    shutil.copyfileobj(response, target)
                require(
                    temporary.stat().st_size == source["bytes"]
                    and digest(temporary) == source["sha256"],
                    "source checksum",
                )
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        require(
            path.stat().st_size == source["bytes"] and digest(path) == source["sha256"],
            "source checksum",
        )
    corpus = json.loads((CORPUS / "manifest.json").read_text())["fixtures"]
    for source in corpus:
        require(digest(CORPUS / source["file"]) == source["sha256"], "corpus checksum")
    rows = []

    def add(name, path, recipe, parents, rotated=False):
        with rasterio.open(path) as src:
            bounds = list(transform_bounds(src.crs, "EPSG:3857", *src.bounds))
            row = {
                "id": name,
                "path": str(path),
                "sha256": digest(path),
                "recipe": recipe,
                "parents": parents,
                "dtype": src.dtypes[0],
                "crs": str(src.crs),
                "bounds": bounds,
                "rotated": rotated,
                "overviews": src.overviews(1),
            }
        rows.append(row)
        return row

    def derivative(
        name,
        source,
        window=None,
        transform=None,
        mask=False,
        alpha=False,
        overview=None,
    ):
        path = out / f"{name}.tif"
        with rasterio.open(source) as src:
            window = window or Window(0, 0, 128, 128)
            values = src.read(1, window=window)
            valid = src.read_masks(1, window=window)
            profile = dict(
                src.profile,
                width=int(window.width),
                height=int(window.height),
                transform=transform or src.window_transform(window),
                count=4 if alpha else 1,
                tiled=True,
                blockxsize=32,
                blockysize=32,
            )
            if mask or alpha:
                profile["nodata"] = None
            if alpha:
                # Byte alpha preserves real source coverage. Its grayscale
                # conversion is declared in the fixture recipe.
                values = np.minimum(values // 16, 255).astype("uint8")
                profile["dtype"] = "uint8"
                profile["photometric"] = "RGB"
                profile["interleave"] = "pixel"
            with rasterio.open(path, "w", **profile) as dst:
                dst.write(values, 1)
                if mask:
                    dst.write_mask(valid)
                if alpha:
                    dst.write(values, 2)
                    dst.write(values, 3)
                    dst.write(
                        np.where(valid, np.iinfo(values.dtype).max, 0).astype(
                            values.dtype
                        ),
                        4,
                    )
                    dst.colorinterp = (
                        ColorInterp.red,
                        ColorInterp.green,
                        ColorInterp.blue,
                        ColorInterp.alpha,
                    )
                if overview is not None:
                    dst.build_overviews([2, 4, 8], overview)
            if mask or alpha:
                require(
                    0 < np.count_nonzero(valid) < valid.size,
                    "mask must contain valid and invalid pixels",
                )
                with rasterio.open(path) as check:
                    require(
                        np.array_equal(check.read_masks(1) > 0, valid > 0),
                        "reopened validity differs",
                    )
                    if alpha:
                        require(
                            check.colorinterp[-1] == ColorInterp.alpha,
                            "alpha must survive reopening",
                        )
            if overview is not None:
                with rasterio.open(path) as check:
                    require(
                        check.overviews(1) == [2, 4, 8],
                        "overviews must survive reopening",
                    )
        return add(
            name,
            path,
            "Native pixel crop; explicit mask/alpha copies source validity; "
            "optional affine rotation or declared overview resampling; "
            "RGBA channels are min(source // 16, 255), alpha copies source validity",
            [digest(source)],
            rotated=transform is not None,
        )

    for name in ("red", "edge", "elevation", "classes"):
        add(
            name,
            CORPUS / f"{name}.tif",
            "Unmodified pinned real corpus",
            [digest(CORPUS / f"{name}.tif")],
        )
    derivative(
        "rotated", CORPUS / "red.tif", transform=Affine(10, 3, 759960, 2, -10, 5042500)
    )
    derivative(
        "rotated-mask",
        CORPUS / "edge.tif",
        window=Window(256, 128, 256, 256),
        transform=Affine(10, 3, 759960, 2, -10, 5042500),
        mask=True,
    )
    derivative(
        "dataset-mask",
        CORPUS / "edge.tif",
        window=Window(256, 128, 256, 256),
        mask=True,
    )
    derivative(
        "alpha", CORPUS / "edge.tif", window=Window(256, 128, 256, 256), alpha=True
    )
    derivative(
        "overview-average",
        CORPUS / "red.tif",
        window=Window(0, 0, 512, 512),
        overview=Resampling.average,
    )
    derivative(
        "overview-nearest",
        CORPUS / "classes.tif",
        window=Window(0, 0, 512, 512),
        overview=Resampling.nearest,
    )
    for source in sources:
        path = cache / source["file"]
        with rasterio.open(path) as src:
            values = src.read(1)
            row, col = np.unravel_index(np.argmax(values), values.shape)
            w, h = min(768, src.width), min(768, src.height)
            window = Window(
                min(max(int(col) - w // 2, 0), src.width - w),
                min(max(int(row) - h // 2, 0), src.height - h),
                w,
                h,
            )
        derivative(source["id"], path, window=window)
    # Unwrap the western longitude of adjacent publisher tiles.
    mosaic = out / "dateline-mosaic.tif"
    with (
        rasterio.open(cache / sources[1]["file"]) as east,
        rasterio.open(cache / sources[2]["file"]) as west,
    ):
        require(
            east.shape == west.shape
            and abs(east.bounds.right - (west.bounds.left + 360)) < 1e-10,
            "adjacent dateline sources",
        )
        with rasterio.open(
            mosaic, "w", **dict(east.profile, width=east.width + west.width)
        ) as dst:
            dst.write(east.read(1), 1, window=Window(0, 0, east.width, east.height))
            dst.write(
                west.read(1), 1, window=Window(east.width, 0, west.width, west.height)
            )
    add(
        "dateline-mosaic",
        mosaic,
        "Concatenate adjacent native columns across +180; no resampling",
        [sources[1]["sha256"], sources[2]["sha256"]],
    )
    for name, parent, crs in (
        ("polar-stereographic", "polar", "EPSG:3413"),
        ("europe-equal-area", "elevation", "EPSG:3035"),
        ("web-mercator", "red", "EPSG:3857"),
    ):
        origin = next(row for row in rows if row["id"] == parent)
        path = out / f"{name}.tif"
        with rasterio.open(origin["path"]) as src:
            affine, width, height = calculate_default_transform(
                src.crs, crs, src.width, src.height, *src.bounds
            )
            profile = dict(
                src.profile,
                crs=crs,
                transform=affine,
                width=width,
                height=height,
                tiled=True,
                blockxsize=32,
                blockysize=32,
            )
            with rasterio.open(path, "w", **profile) as dst:
                reproject(
                    rasterio.band(src, 1),
                    rasterio.band(dst, 1),
                    resampling=Resampling.nearest,
                )
        add(
            name,
            path,
            f"Nearest GDAL reprojection of real {parent} to {crs}",
            [origin["sha256"]],
        )
    write_json(
        out / "fixtures.json",
        {"sources": sources, "corpus": corpus, "derivatives": rows},
    )
    return rows


def requests(row):
    left, bottom, right, top = row["bounds"]
    w, h = right - left, top - bottom
    result = [
        {"id": "preview", "tile_size": [48, 64], "tile_bounds": None},
        {"id": "overview", "tile_size": [24, 32], "tile_bounds": None},
        {
            "id": "edge",
            "tile_size": [48, 64],
            "tile_bounds": [left - w / 4, bottom - h / 4, left + w / 2, top - h / 4],
        },
        {
            "id": "detail",
            "tile_size": [48, 64],
            "tile_bounds": [left + w / 3, bottom + h / 3, right - w / 3, top - h / 3],
        },
        {
            "id": "categories",
            "tile_size": [48, 64],
            "tile_bounds": None,
            "preserve_values": True,
        },
    ]
    if row["id"].startswith("dateline"):
        # Real meridian-adjacent Web Mercator tiles on both longitude sides.
        unit = 40075016.68557849 / 1024
        for x in (0, 1023):
            tile_left = -20037508.342789244 + x * unit
            tile_top = 20037508.342789244 - 340 * unit
            result.append(
                {
                    "id": f"meridian-{x}",
                    "tile_size": [48, 64],
                    "tile_bounds": [
                        tile_left,
                        tile_top - unit,
                        tile_left + unit,
                        tile_top,
                    ],
                }
            )
    if row["id"] != "dateline-mosaic":
        result.append(
            {
                "id": "sparse-rejection",
                "tile_size": [48, 64],
                "tile_bounds": [
                    left - 100 * w,
                    bottom - 100 * h,
                    right + 100 * w,
                    top + 100 * h,
                ],
            }
        )
    return result


class Native:
    def __init__(self, library):
        self.lib = ctypes.CDLL(str(library))
        for name, arguments, result in (
            (
                "tc_session_open",
                [ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)],
                ctypes.c_void_p,
            ),
            (
                "tc_session_operation",
                [ctypes.c_void_p, ctypes.c_char_p],
                ctypes.c_void_p,
            ),
            ("tc_data_operation", [ctypes.c_char_p], ctypes.c_void_p),
            ("tc_string_free", [ctypes.c_void_p], None),
            ("tc_session_free", [ctypes.c_void_p], None),
        ):
            fn = getattr(self.lib, name)
            fn.argtypes, fn.restype = arguments, result

    def decode(self, pointer):
        require(pointer, "native returned null")
        try:
            result = json.loads(ctypes.string_at(pointer))
        finally:
            self.lib.tc_string_free(pointer)
        if "error" in result:
            raise RuntimeError(result["error"])
        return result["ok"]

    def open(self, **request):
        error = ctypes.c_void_p()
        handle = self.lib.tc_session_open(
            json.dumps(request).encode(), ctypes.byref(error)
        )
        if not handle:
            message = (
                ctypes.string_at(error).decode() if error.value else "null session"
            )
            self.lib.tc_string_free(error)
            raise RuntimeError(message)
        return handle

    def call(self, handle, **request):
        return self.decode(
            self.lib.tc_session_operation(handle, json.dumps(request).encode())
        )

    def metadata(self, path, options, settings):
        return self.decode(
            self.lib.tc_data_operation(
                json.dumps(
                    {
                        "operation": "compute_metadata",
                        "path": path,
                        "options": options,
                        "settings": settings,
                    }
                ).encode()
            )
        )


def capture_reference(manifest, out):
    import terracotta as tc
    from terracotta.server import create_app

    tc.update_settings(USE_MULTIPROCESSING=False, RASTER_CACHE_SIZE=0)
    require(tc.__version__ == "0.10.1", "reference version")
    write_json(
        out / "environment.json",
        {
            "version": tc.__version__,
            "package": tc.__file__,
            "gdal": rasterio.__gdal_version__,
            "rasterio": rasterio.__version__,
            "numpy": np.__version__,
        },
    )
    rows = json.loads(manifest.read_text())["derivatives"]
    driver = tc.get_driver(out / "catalog.sqlite")
    driver.create(["scene", "band"])
    errors = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", tc.exceptions.PerformanceWarning)
        for row in rows:
            name = row["id"]
            full = None
            for mode, options in (
                ("full", {"use_chunks": False}),
                ("sample", {"max_shape": [73, 91]}),
                ("chunk", {"use_chunks": True}),
            ):
                try:
                    metadata = driver.compute_metadata(row["path"], **options)
                    write_json(out / f"{name}-{mode}.json", metadata)
                    if mode == "full":
                        full = metadata
                except Exception as error:  # noqa: BLE001 - retain every independent failure
                    errors.append({"id": f"{name}-{mode}", "error": str(error)})
            if full is not None:
                driver.insert([name, "value"], row["path"], metadata=full)
                if name in {"rotated-mask", "dataset-mask", "alpha"}:
                    for band in ("green", "blue"):
                        driver.insert([name, band], str(CORPUS / f"{band}.tif"))
            for warp in METHODS:
                for read in METHODS:
                    tc.update_settings(REPROJECTION_METHOD=warp, RESAMPLING_METHOD=read)
                    for request in requests(row):
                        key = f"{name}-{warp}-{read}-{request['id']}"
                        try:
                            array = driver.get_raster_tile(
                                [name, "value"],
                                **{k: v for k, v in request.items() if k != "id"},
                            )
                            np.savez(
                                out / f"{key}.npz",
                                values=array.data,
                                mask=np.ma.getmaskarray(array),
                            )
                        except Exception as error:  # noqa: BLE001 - retain every independent failure
                            if request["id"] == "sparse-rejection" and isinstance(
                                error, tc.exceptions.TileOutOfBoundsError
                            ):
                                write_json(
                                    out / f"{key}.error.json",
                                    {"error": str(error), "type": type(error).__name__},
                                )
                            else:
                                errors.append(
                                    {
                                        "id": key,
                                        "error": str(error),
                                        "type": type(error).__name__,
                                    }
                                )
            print(f"reference: {name}", flush=True)
        tc.update_settings(
            DRIVER_PATH=str(out / "catalog.sqlite"),
            REPROJECTION_METHOD="linear",
            RESAMPLING_METHOD="average",
        )
        client = create_app().test_client()
        for row in rows:
            for kind, url in image_requests(row):
                response = client.get(url)
                write_json(
                    out / f"{row['id']}-{kind}-http.json",
                    {
                        "status": response.status_code,
                        "content_type": response.content_type,
                    },
                )
                (out / f"{row['id']}-{kind}.png").write_bytes(response.data)
    write_json(out / "errors.json", errors)


def image_requests(row):
    name = row["id"]
    query = urlencode({"stretch_range": "[0,4000]", "tile_size": "[48,64]"})
    result = [
        ("singleband", f"/singleband/{name}/value/preview.png?{query}"),
        ("compute", f"/compute/{name}/preview.png?expression=v1&v1=value&{query}"),
        (
            "rgb",
            f"/rgb/{name}/preview.png?"
            + urlencode(
                {
                    "r": "value",
                    "g": "value",
                    "b": "value",
                    "r_range": "[0,4000]",
                    "g_range": "[0,4000]",
                    "b_range": "[0,4000]",
                    "tile_size": "[48,64]",
                }
            ),
        ),
    ]
    if name in {"rotated-mask", "dataset-mask", "alpha"}:
        result.append(
            (
                "partial-rgb",
                f"/rgb/{name}/preview.png?"
                + urlencode(
                    {
                        "r": "value",
                        "g": "green",
                        "b": "blue",
                        "r_range": "[0,4000]",
                        "g_range": "[0,4000]",
                        "b_range": "[0,4000]",
                        "tile_size": "[48,64]",
                    }
                ),
            )
        )
    if name.startswith("dateline"):
        result.extend(
            (f"meridian-{x}", f"/singleband/{name}/value/10/{x}/340.png?{query}")
            for x in (0, 1023)
        )
    return result


def metadata_comparison(row, mode, expected, actual, path):
    checks = {}
    for field in ("bounds", "range", "valid_percentage"):
        checks[field] = bool(
            np.allclose(actual[field], expected[field], rtol=0, atol=1e-10)
        )
    checks["percentiles"] = bool(
        np.array_equal(
            np.asarray(actual["percentiles"], dtype="float32"),
            np.asarray(expected["percentiles"], dtype="float32"),
        )
    )
    for field in ("mean", "stdev"):
        value = expected[field]
        tolerance = (
            4 * abs(float(np.spacing(np.float32(value))))
            if row["dtype"] == "float32" and mode != "chunk"
            else 1e-10
        )
        if row["id"] == "dateline-mosaic" and mode == "full":
            tolerance = {"mean": 0.000005, "stdev": 0.00021}[field]
        checks[field] = bool(
            np.isclose(actual[field], value, rtol=2e-12, atol=tolerance)
        )
    a, b = shape(expected["convex_hull"]), shape(actual["convex_hull"])
    checks["convex_hull"] = a.symmetric_difference(b).area / a.union(b).area < 1e-10
    checks["extra_metadata"] = actual["metadata"] == expected["metadata"]
    write_json(path, {"expected": expected, "actual": actual, "checks": checks})
    return checks


def independent_metadata(row, actual, mode):
    with rasterio.open(row["path"]) as src:
        size = (
            (min(73, src.height), min(91, src.width)) if mode == "sample" else src.shape
        )
        data = np.ma.masked_invalid(src.read(1, out_shape=size, masked=True))
        transform = src.transform * Affine.scale(
            src.width / size[1], src.height / size[0]
        )
        values = data.compressed()
        valid = ~np.ma.getmaskarray(data)
        yy, xx = np.nonzero(valid)
        points = []
        # Row extrema determine the convex hull of valid pixel squares.
        for y in np.unique(yy):
            columns = xx[yy == y]
            lo, hi = int(columns.min()), int(columns.max()) + 1
            points.extend(
                transform * p for p in ((lo, y), (hi, y), (lo, y + 1), (hi, y + 1))
            )
        hull = MultiPoint(points).convex_hull
        from rasterio.warp import transform_geom

        expected_hull = shape(
            transform_geom(src.crs, "EPSG:4326", hull.__geo_interface__)
        )
        observed = shape(actual["convex_hull"])
        return {
            "range": actual["range"] == [float(values.min()), float(values.max())],
            "valid_percentage": bool(
                np.isclose(
                    actual["valid_percentage"], valid.mean() * 100, atol=1e-10, rtol=0
                )
            ),
            "convex_hull": expected_hull.symmetric_difference(observed).area
            / expected_hull.union(observed).area
            < 1e-10,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path)
    parser.add_argument("--reference-python", type=Path)
    parser.add_argument(
        "--reference-archive",
        type=Path,
        default=ROOT / ".cache/reference/source.tar.gz",
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cache", type=Path, default=ROOT / ".cache/raster-geography")
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--capture-reference", type=Path)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    if args.capture_reference:
        capture_reference(args.capture_reference, out)
        return
    require(
        args.library is not None and args.reference_python is not None,
        "library and reference-python required",
    )
    require(
        digest(args.reference_archive)
        == "e5d2ac9b78a36d0f28414b1a4890c961dede50d1f9ce33b8d23eee643f5e2348",
        "pinned reference archive checksum",
    )
    with tarfile.open(args.reference_archive) as archive:
        for member in archive:
            relative = member.name.partition("/")[2]
            if (
                member.isfile()
                and relative.startswith("terracotta/")
                and relative != "terracotta/_version.py"
            ):
                require(
                    (ROOT / ".cache/reference" / relative).read_bytes()
                    == archive.extractfile(member).read(),
                    f"reference source differs: {relative}",
                )
    library = out / args.library.name
    shutil.copy2(args.library.resolve(), library)
    source_files = [
        ROOT / "Cargo.toml",
        ROOT / "Cargo.lock",
        *sorted((ROOT / "src").rglob("*.rs")),
    ]
    write_json(
        out / "source-identities.json",
        {str(p.relative_to(ROOT)): digest(p) for p in source_files},
    )
    rows = fixtures(out, args.cache.resolve(), args.download)
    reference = out / "reference"
    env = dict(os.environ, PYTHONPATH=str(ROOT / ".cache/reference"))
    with (out / "reference.log").open("w") as log:
        subprocess.run(
            [
                str(args.reference_python.absolute()),
                str(Path(__file__).resolve()),
                "--capture-reference",
                str(out / "fixtures.json"),
                "--out",
                str(reference),
            ],
            env=env,
            stdout=log,
            stderr=log,
            check=True,
        )
    native = Native(library)
    results = []

    def run(profile, key, fn):
        checks = {}
        try:
            checks = fn()
            require(all(checks.values()), str(checks))
            results.append(
                {"profile": profile, "id": key, "status": "passed", "checks": checks}
            )
        except Exception as error:  # noqa: BLE001 - retain every independent failure
            results.append(
                {
                    "profile": profile,
                    "id": key,
                    "status": "failed",
                    "checks": checks,
                    "error": str(error),
                }
            )
            print(f"FAILED: {profile} {key}: {error}", flush=True)

    for profile in PROFILES:
        folder = out / profile
        folder.mkdir()
        settings = {
            "COMPAT_PROFILE": profile,
            "RASTER_WORKERS": 1,
            "RASTER_CACHE_BYTES": 0,
            "RUST_MERCATOR_FAST": False,
            "RUST_UTM_FAST": False,
            "NATIVE_DIRECT_READ": False,
            "PREFETCH_TILE_RING": 0,
        }
        for row in rows:
            name = row["id"]
            for mode, options in (
                ("full", {"use_chunks": False}),
                ("sample", {"max_shape": [73, 91]}),
                ("chunk", {"use_chunks": True}),
            ):

                def compare_metadata(
                    row=row,
                    mode=mode,
                    options=options,
                    name=name,
                    settings=settings,
                    folder=folder,
                    profile=profile,
                ):
                    expected = json.loads(
                        (reference / f"{name}-{mode}.json").read_text()
                    )
                    actual = native.metadata(row["path"], options, settings)
                    checks = metadata_comparison(
                        row, mode, expected, actual, folder / f"{name}-{mode}.json"
                    )
                    if profile == "native" and row["rotated"]:
                        checks.pop("convex_hull")
                        checks.update(independent_metadata(row, actual, mode))
                        write_json(
                            folder / f"{name}-{mode}.json",
                            {
                                "expected": expected,
                                "actual": actual,
                                "checks": checks,
                                "geometry_comparison": "independent affine pixel-square hull",
                            },
                        )
                    return checks

                run(profile, f"{name}-{mode}", compare_metadata)
            for warp in METHODS:
                for read in METHODS:
                    handle = native.open(
                        kind="raster",
                        settings=dict(
                            settings, REPROJECTION_METHOD=warp, RESAMPLING_METHOD=read
                        ),
                    )
                    try:
                        for request in requests(row):
                            key = f"{name}-{warp}-{read}-{request['id']}"

                            def compare_raster(
                                request=request,
                                key=key,
                                handle=handle,
                                row=row,
                                folder=folder,
                            ):
                                if (reference / f"{key}.error.json").exists():
                                    try:
                                        native.call(
                                            handle,
                                            path=row["path"],
                                            **{
                                                k: v
                                                for k, v in request.items()
                                                if k != "id"
                                            },
                                        )
                                    except RuntimeError as error:
                                        return {
                                            "sparse_tile_rejected": "out of bounds"
                                            in str(error).lower()
                                        }
                                    return {"sparse_tile_rejected": False}
                                result = native.call(
                                    handle,
                                    path=row["path"],
                                    **{k: v for k, v in request.items() if k != "id"},
                                )
                                expected = np.load(reference / f"{key}.npz")
                                # Mirror the supported Python adapter's dtype
                                # conversion from the core's f64 transfer buffer.
                                values = (
                                    np.asarray(result["values"])
                                    .astype(expected["values"].dtype)
                                    .reshape(result["shape"])
                                )
                                mask = np.asarray(result["mask"]).reshape(
                                    result["shape"]
                                )
                                np.savez(
                                    folder / f"{key}.npz", values=values, mask=mask
                                )
                                same_shape = (
                                    list(expected["values"].shape) == result["shape"]
                                )
                                if same_shape:
                                    common = ~(mask | expected["mask"])
                                    delta = np.abs(
                                        values.astype("float64")
                                        - expected["values"].astype("float64")
                                    )
                                    write_json(
                                        folder / f"{key}.json",
                                        {
                                            "different_mask_pixels": int(
                                                np.count_nonzero(
                                                    mask != expected["mask"]
                                                )
                                            ),
                                            "different_common_valid_values": int(
                                                np.count_nonzero(delta[common])
                                            ),
                                            "max_common_valid_difference": float(
                                                delta[common].max(initial=0)
                                            ),
                                            "reference_valid_pixels": int(
                                                np.count_nonzero(~expected["mask"])
                                            ),
                                            "native_valid_pixels": int(
                                                np.count_nonzero(~mask)
                                            ),
                                        },
                                    )
                                return {
                                    "shape": same_shape,
                                    "dtype": result["dtype"]
                                    == GDAL_TYPES[str(expected["values"].dtype)],
                                    "mask": same_shape
                                    and np.array_equal(mask, expected["mask"]),
                                    "values": same_shape
                                    and np.array_equal(
                                        values[~mask], expected["values"][~mask]
                                    ),
                                }

                            run(profile, key, compare_raster)
                    finally:
                        native.lib.tc_session_free(handle)
            print(f"candidate: {profile} {name}", flush=True)
        handle = native.open(
            kind="http", path=str(reference / "catalog.sqlite"), settings=settings
        )
        try:
            for row in rows:
                for kind, url in image_requests(row):

                    def compare_image(
                        row=row,
                        kind=kind,
                        url=url,
                        handle=handle,
                        folder=folder,
                        profile=profile,
                    ):
                        response = native.call(
                            handle, operation="request", method="GET", url=url
                        )
                        expected = json.loads(
                            (reference / f"{row['id']}-{kind}-http.json").read_text()
                        )
                        body = bytes(response[2])
                        (folder / f"{row['id']}-{kind}.png").write_bytes(body)
                        a = np.asarray(
                            Image.open(reference / f"{row['id']}-{kind}.png").convert(
                                "RGBA"
                            )
                        )
                        b = np.asarray(Image.open(io.BytesIO(body)).convert("RGBA"))
                        same_shape = a.shape == b.shape
                        write_json(
                            folder / f"{row['id']}-{kind}-image.json",
                            {
                                "reference_shape": list(a.shape),
                                "native_shape": list(b.shape),
                                "different_channels": int(np.count_nonzero(a != b))
                                if same_shape
                                else None,
                                "different_alpha_pixels": int(
                                    np.count_nonzero(a[:, :, 3] != b[:, :, 3])
                                )
                                if same_shape
                                else None,
                            },
                        )
                        if profile == "native" and not same_shape and np.all(a == 0):
                            return {
                                "status": response[0] == expected["status"] == 200,
                                "content_type": response[1] == expected["content_type"],
                                "native_empty_dimensions": list(b.shape) == [48, 64, 4],
                                "transparent": bool(np.all(b == 0)),
                            }
                        return {
                            "status": response[0] == expected["status"] == 200,
                            "content_type": response[1] == expected["content_type"],
                            "rgba": np.array_equal(a, b),
                        }

                    run(profile, f"{row['id']}-{kind}-http", compare_image)
        finally:
            native.lib.tc_session_free(handle)
    errors = json.loads((reference / "errors.json").read_text())
    report = {
        "library_sha256": digest(library),
        "reference_source_sha256": digest(args.reference_archive),
        "fixture_manifest_sha256": digest(out / "fixtures.json"),
        "source_manifest_sha256": digest(out / "source-identities.json"),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "numpy": np.__version__,
        "reference_environment": json.loads(
            (reference / "environment.json").read_text()
        ),
        "profiles": PROFILES,
        "methods": METHODS,
        "reader": "general padded VRT; native direct reads disabled",
        "runner_sha256": digest(Path(__file__)),
        "native_gdal_sha256": digest(Path(os.environ["TERRACOTTA_GDAL_LIBRARY"])),
        "required_cases": len(PROFILES)
        * sum(16 * len(requests(row)) + 3 + len(image_requests(row)) for row in rows),
        "cases": results,
        "reference_errors": errors,
        "passed": sum(r["status"] == "passed" for r in results),
        "failed": sum(r["status"] == "failed" for r in results),
        "source_changed_during_run": [
            name
            for name, identity in json.loads(
                (out / "source-identities.json").read_text()
            ).items()
            if digest(ROOT / name) != identity
        ],
    }
    write_json(out / "report.json", report)
    require(len(results) == report["required_cases"], "matrix case count differs")
    require(not report["source_changed_during_run"], "source changed during matrix")
    print(json.dumps({k: report[k] for k in ("passed", "failed", "reference_errors")}))
    if report["failed"] or errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
