"""Qualify publication headers, revision guards, real pixels and nginx/browser reuse.

The Mercator fixture retains checksum-pinned Sentinel samples and assigns an
explicit three-by-three tile footprint to isolate delivery from reprojection.
Expected imagery comes from Rasterio and NumPy, never from candidate renders.
"""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import contextlib
import functools
import io
import json
import os
import socket
import sqlite3
import subprocess
import threading
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

import numpy as np
import rasterio
from navigation_benchmark import ROOT, digest, native, service
from PIL import Image
from rasterio.enums import Resampling
from rasterio.transform import from_bounds
from rasterio.warp import reproject

EXTENT = 20037508.342789244
ZOOM = 8
TILES = [(x, y) for y in range(90, 93) for x in range(134, 137)]


def bounds(x, y):
    width = 2 * EXTENT / 2**ZOOM
    return [
        -EXTENT + x * width,
        EXTENT - (y + 1) * width,
        -EXTENT + (x + 1) * width,
        EXTENT - y * width,
    ]


def fetch(url, method="GET", headers=None):
    try:
        response = urlopen(
            Request(url, method=method, headers=headers or {}), timeout=30
        )
    except HTTPError as error:
        response = error
    with response:
        return (
            response.status,
            {k.lower(): v for k, v in response.headers.items()},
            response.read(),
        )


def pinned(url, revision):
    return url + ("&" if "?" in url else "?") + urlencode({"revision": revision})


def rgba(content):
    return np.asarray(Image.open(io.BytesIO(content)).convert("RGBA"))


def metrics(origin):
    status, _, body = fetch(origin + "/__terracotta/metrics")
    assert status == 200
    return json.loads(body)


def rss(pid):
    for line in Path(f"/proc/{pid}/status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) * 1024
    return None


@contextlib.contextmanager
def proxy(nginx, origin, folder):
    folder.mkdir()
    for name in ["cache", "body", "temp", "fastcgi", "uwsgi", "scgi"]:
        (folder / name).mkdir()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    config = (ROOT / "docs/examples/render-cache.nginx.conf").read_text()
    for token, value in {
        "WORK": str(folder),
        "PORT": str(port),
        "ORIGIN": origin,
        "EXAMPLE": str(ROOT / "docs/examples/rendered-cache.html"),
    }.items():
        config = config.replace(f"@{token}@", value)
    path = folder / "nginx.conf"
    path.write_text(config)
    log = (folder / "process.log").open("w")
    process = subprocess.Popen(
        [str(nginx), "-p", str(folder), "-c", str(path)], stdout=log, stderr=log
    )
    try:
        address = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 10
        while True:
            assert process.poll() is None, (folder / "process.log").read_text()
            try:
                if fetch(address + "/example")[0] == 200:
                    break
            except OSError:
                pass
            assert time.monotonic() < deadline
            time.sleep(0.02)
        yield address
    finally:
        process.terminate()
        process.wait(timeout=10)
        log.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--nginx", type=Path)
    parser.add_argument("--browser", action="store_true")
    args = parser.parse_args()
    args.binary, args.out = args.binary.resolve(), args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=False)
    os.environ["RCFP_COMPAT_PROFILE"] = "native"
    os.environ["TC_RASTER_CACHE_BYTES"] = "0"
    os.environ["TC_RASTER_QUEUE_CAPACITY"] = "32"
    import rcfp
    from rcfp.wsgi import Application as Wsgi

    settings = {
        "HTTP_CACHE_PUBLIC": True,
        "RENDER_CACHE_BYTES": 4 * 1024**2,
        "REPROJECTION_METHOD": "nearest",
        "RESAMPLING_METHOD": "nearest",
        "EXPOSE_METRICS": True,
        "ALLOWED_ORIGINS_TILES": ["https://allowed.example"],
    }
    env = dict(
        os.environ,
        **{
            f"TC_{k}": json.dumps(v) if not isinstance(v, str) else v
            for k, v in settings.items()
        },
    )
    source = ROOT / "tests/fixtures/corpus/red.tif"
    manifest = json.loads((source.parent / "manifest.json").read_text())
    assert digest(source) == next(
        r["sha256"] for r in manifest["fixtures"] if r["id"] == "red"
    )
    raster = args.out / "scene.tif"
    footprint = [
        bounds(134, 92)[0],
        bounds(134, 92)[1],
        bounds(136, 90)[2],
        bounds(136, 90)[3],
    ]
    with rasterio.open(source) as dataset:
        profile, samples = dataset.profile.copy(), dataset.read(1)
    profile.update(
        crs="EPSG:3857",
        transform=from_bounds(*footprint, 512, 512),
        tiled=True,
        blockxsize=256,
        blockysize=256,
        compress="NONE",
    )
    with rasterio.open(raster, "w", **profile) as dataset:
        dataset.write(samples, 1)
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
    metadata = native(
        args.binary,
        args.out,
        env,
        {"operation": "compute_metadata", "path": str(raster)},
    )
    for band in ["red", "green", "blue"]:
        native(
            args.binary,
            args.out,
            env,
            {
                "operation": "insert",
                "path": str(database),
                "keys": ["scene", band],
                "raster": str(raster),
                "metadata": metadata,
            },
        )
    routes = [
        f"/singleband/scene/red/{ZOOM}/{x}/{y}.png?stretch_range=%5B0%2C3000%5D"
        for x, y in TILES
    ]
    route = routes[4]
    checks = []
    measurements = []

    def check(name, condition):
        assert condition, name
        checks.append(name)

    @functools.lru_cache
    def expected(x, y):
        data = np.zeros((256, 256), dtype=samples.dtype)
        reproject(
            samples,
            data,
            src_transform=profile["transform"],
            src_crs=profile["crs"],
            src_nodata=profile["nodata"],
            dst_transform=from_bounds(*bounds(x, y), 256, 256),
            dst_crs=profile["crs"],
            dst_nodata=profile["nodata"],
            resampling=Resampling.nearest,
        )
        values = np.rint(
            1 + np.clip(data.astype("float64") * (1 / 3000), 0, 1) * 254
        ).astype("uint8")
        mask = data == profile["nodata"]
        values[mask] = 0
        return np.dstack(
            [values, values, values, np.where(mask, 0, 255).astype("uint8")]
        )

    app = rcfp.NativeApplication(database, settings)
    wsgi = Wsgi(str(database), settings)

    def direct(method, url, headers=None):
        status, _, body, _, extra = app.request(method, url, headers)
        return status, {k.lower(): v for k, v in extra.items()}, body

    def through_wsgi(method, url, headers=None):
        parsed = urlsplit(url)
        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": parsed.path,
            "QUERY_STRING": parsed.query,
            "wsgi.input": io.BytesIO(),
            "CONTENT_LENGTH": "0",
        }
        environ.update(
            {
                "HTTP_" + k.upper().replace("-", "_"): v
                for k, v in (headers or {}).items()
            }
        )
        captured = []
        body = b"".join(
            wsgi(environ, lambda status, headers: captured.append((status, headers)))
        )
        return (
            int(captured[0][0].split()[0]),
            {k.lower(): v for k, v in captured[0][1]},
            body,
        )

    def through_lambda(method, url, headers=None):
        parsed = urlsplit(url)
        event = {
            "rawPath": parsed.path,
            "rawQueryString": parsed.query,
            "requestContext": {"http": {"method": method}},
            "headers": headers or {},
        }
        path = args.out / "event.json"
        path.write_text(json.dumps(event))
        result = json.loads(
            subprocess.check_output(
                [
                    str(args.binary),
                    "lambda-event",
                    "-d",
                    str(database),
                    "--event",
                    str(path),
                ],
                env=env,
            )
        )
        body = (
            base64.b64decode(result["body"])
            if result["isBase64Encoded"]
            else result["body"].encode()
        )
        return result["statusCode"], result["headers"], body

    with service(args.binary, database, env, args.out, []) as (origin, _process):
        adapters = {
            "python": direct,
            "wsgi": through_wsgi,
            "lambda": through_lambda,
            "http": lambda method, url, headers=None: fetch(
                origin + url, method, headers
            ),
        }
        tags = []
        for name, request in adapters.items():
            status, headers, body = request(
                "GET", route, {"Origin": "https://allowed.example"}
            )
            check(
                name + " exact independent pixels",
                status == 200 and np.array_equal(rgba(body), expected(*TILES[4])),
            )
            tag, revision = headers["etag"], headers["x-rcfp-revision"]
            tags.append(tag)
            check(
                name + " mutable policy", headers["cache-control"] == "public, no-cache"
            )
            status, conditional, content = request(
                "GET",
                route,
                {
                    "If-None-Match": '"other", W/' + tag,
                    "Origin": "https://allowed.example",
                },
            )
            check(
                name + " conditional CORS",
                status == 304
                and not content
                and conditional["etag"] == tag
                and conditional["access-control-allow-origin"]
                == "https://allowed.example"
                and "content-length" not in conditional,
            )
            check(
                name + " wildcard",
                request("GET", route, {"If-None-Match": "*"})[0] == 304,
            )
            check(
                name + " strong precondition",
                request("GET", route, {"If-Match": tag})[0] == 200,
            )
            mismatch = request(
                "GET", route, {"If-Match": "W/" + tag, "If-None-Match": "*"}
            )
            check(
                name + " precondition precedence",
                mismatch[0] == 412 and mismatch[1]["cache-control"] == "no-store",
            )
            head = request("HEAD", route)
            check(
                name + " HEAD length",
                head[0] == 200
                and not head[2]
                and int(head[1]["content-length"]) == len(body),
            )
            check(
                name + " conditional HEAD",
                request("HEAD", route, {"If-None-Match": tag})[0] == 304,
            )
            fixed = pinned(route, revision)
            check(
                name + " immutable policy",
                request("GET", fixed)[1]["cache-control"]
                == "public, max-age=31536000, immutable",
            )
            rejected = request("GET", pinned(route, "0" * 64), {"If-None-Match": "*"})
            check(
                name + " revision before conditional",
                rejected[0] == 409
                and rejected[1]["cache-control"] == "no-store"
                and "etag" not in rejected[1],
            )
            for field in ["Authorization", "Cookie"]:
                protected = request(
                    "GET", fixed, {field: "private", "If-None-Match": "*"}
                )
                check(
                    name + " " + field + " isolation",
                    protected[0] == 200
                    and protected[1]["cache-control"] == "no-store"
                    and "etag" not in protected[1],
                )
            for bad in ["/missing", route + "&tile_size=bad"]:
                response = request("GET", bad, {"If-None-Match": "*"})
                check(
                    name + " error " + bad.split("?")[0],
                    response[0] in [400, 404]
                    and response[1]["cache-control"] == "no-store"
                    and "etag" not in response[1],
                )
        check("all adapters share validators", len(set(tags)) == 1)
        status, denied, _ = fetch(
            origin + pinned(route, tags[0].strip('"')),
            headers={"Origin": "https://denied.example"},
        )
        check(
            "denied CORS still varies",
            status == 200
            and "access-control-allow-origin" not in denied
            and "Origin" in denied["vary"],
        )
        empty = "/singleband/scene/red/8/0/0.png"
        result = direct("GET", empty)
        check(
            "transparent tile validated",
            result[0] == 200
            and not rgba(result[2])[:, :, 3].any()
            and direct("GET", empty, {"If-None-Match": result[1]["etag"]})[0] == 304,
        )
        check(
            "empty discovery validated",
            direct("GET", "/datasets?scene=missing")[0] == 200,
        )
        reordered = (
            f"/singleband/scene/red/{ZOOM}/135/91.png?stretch_range=%5B0%2C%203000%5D"
        )
        check(
            "JSON formatting canonical", direct("GET", reordered)[1]["etag"] == tags[0]
        )
        for query in [
            "tile_size=%5B128%2C128%5D",
            "colormap=viridis",
            "stretch_range=%5B0%2C6000%5D",
            "color_transform=gamma%201%202",
        ]:
            response = direct("GET", route.split("?")[0] + "?" + query)
            check(
                "render option " + query.split("=")[0],
                response[0] == 200 and response[1]["etag"] != tags[0],
            )
        compute = f"/compute/scene/{ZOOM}/135/91.png?expression=v1&v1=red&stretch_range=%5B0%2C3000%5D"
        before = json.loads(direct("GET", "/__terracotta/metrics")[2])
        first = direct("GET", compute)
        second = direct("GET", compute)
        after = json.loads(direct("GET", "/__terracotta/metrics")[2])
        check(
            "compute encoded reuse",
            first == second
            and after["submitted"] == before["submitted"] + 1
            and after["render_cache"]["hits"] > before["render_cache"]["hits"],
        )
        rgb = f"/rgb/scene/{ZOOM}/135/91.png?r=red&g=green&b=blue"
        check(
            "RGB encoding separates revisions",
            direct("GET", rgb)[1]["etag"]
            != direct("GET", rgb + "&format=webp&quality=80")[1]["etag"],
        )
        for value in [False, True]:
            private = rcfp.NativeApplication(
                database, dict(settings, HTTP_CACHE_PUBLIC=value)
            )
            response = private.request("GET", route)
            check(
                "publication scope " + str(value),
                response[4]["Cache-Control"].startswith(
                    "public" if value else "private"
                ),
            )
        discovery = json.loads(direct("GET", "/tilesets", {"Host": "localhost"})[2])
        tileset = next(
            row for row in discovery["tilesets"] if row["keys"] == ["scene", "red"]
        )
        template = next(
            row["tiles"]
            for row in tileset["tileMatrixSets"]
            if row["id"] == "WebMercatorQuad"
        )
        native_url = (
            urlsplit(
                template.replace("{z}", str(ZOOM))
                .replace("{x}", "135")
                .replace("{y}", "91")
            ).path
            + "?stretch_range=%5B0%2C3000%5D"
        )
        for name, request in adapters.items():
            response = request("GET", native_url)
            check(
                name + " matrix tile pixels",
                response[0] == 200
                and np.array_equal(rgba(response[2]), expected(*TILES[4])),
            )
            check(
                name + " matrix tile conditional",
                request("GET", native_url, {"If-None-Match": response[1]["etag"]})[0]
                == 304,
            )
            fixed = pinned(native_url, response[1]["x-rcfp-revision"])
            check(
                name + " matrix tile immutable",
                request("GET", fixed)[1]["cache-control"]
                == "public, max-age=31536000, immutable",
            )
            check(
                name + " matrix tile revision guard",
                request("GET", pinned(native_url, "0" * 64))[0] == 409,
            )
        # Catalog changes invalidate even when the explicit output stretch stays equal.
        old_revision = tags[0].strip('"')
        with sqlite3.connect(database) as db:
            db.execute("UPDATE metadata SET max=max+1")
        check(
            "catalog generation rejects pinned identity",
            direct("GET", pinned(route, old_revision))[0] == 409,
        )
        current = direct("GET", route)[1]["x-rcfp-revision"]
        stat = raster.stat()
        with rasterio.open(raster, "r+") as d:
            d.write(
                np.clip(samples.astype("uint32") * 2, 0, 65535).astype(samples.dtype), 1
            )
        os.utime(raster, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        check(
            "write preserves size and modification time",
            raster.stat().st_size == stat.st_size
            and raster.stat().st_mtime_ns == stat.st_mtime_ns,
        )
        check(
            "source mutation rejects pinned identity",
            direct("GET", pinned(route, current))[0] == 409,
        )
        with sqlite3.connect(database) as db:
            db.execute("DELETE FROM metadata")
            db.execute("DELETE FROM datasets")
        check(
            "catalog deletion rejects retained imagery",
            direct("GET", pinned(route, current))[0] == 404,
        )
    # Restore original pixels/catalog before measuring.
    with rasterio.open(raster, "r+") as d:
        d.write(samples, 1)
    for band in ["red", "green", "blue"]:
        native(
            args.binary,
            args.out,
            env,
            {
                "operation": "insert",
                "path": str(database),
                "keys": ["scene", band],
                "raster": str(raster),
                "metadata": metadata,
            },
        )

    for xy in TILES:
        expected(*xy)
    for trial in range(3):
        for capacity in [0, 4 * 1024**2] if trial % 2 == 0 else [4 * 1024**2, 0]:
            folder = args.out / f"viewport-{trial}-{capacity}"
            folder.mkdir()
            variant = dict(env, TC_RENDER_CACHE_BYTES=str(capacity))
            with service(args.binary, database, variant, folder, []) as (
                origin,
                process,
            ):
                revisions = None
                for stage in ["first", "revisit", "conditional"]:
                    before = metrics(origin)
                    started = time.monotonic()

                    def load(item, stage=stage, revisions=revisions):
                        index, url = item
                        header = (
                            {"If-None-Match": revisions[index]}
                            if stage == "conditional"
                            else None
                        )
                        response = fetch(origin + url, headers=header)
                        assert response[0] == (304 if stage == "conditional" else 200)
                        if stage != "conditional":
                            assert np.array_equal(
                                rgba(response[2]), expected(*TILES[index])
                            )
                        return response

                    with concurrent.futures.ThreadPoolExecutor(max_workers=9) as pool:
                        responses = list(pool.map(load, enumerate(routes)))
                    elapsed = time.monotonic() - started
                    if stage == "first":
                        revisions = [r[1]["etag"] for r in responses]
                    after = metrics(origin)
                    assert after["render_cache"]["bytes"] <= capacity
                    measurements.append(
                        {
                            "trial": trial,
                            "capacity": capacity,
                            "stage": stage,
                            "viewport_seconds": elapsed,
                            "payload_bytes": sum(len(r[2]) for r in responses),
                            "raster_jobs": after["submitted"] - before["submitted"],
                            "rss_bytes": rss(process.pid),
                            "cache": after["render_cache"],
                        }
                    )
                    if capacity and stage != "first":
                        assert after["submitted"] == before["submitted"]
                if capacity and args.nginx:
                    fixed = [
                        pinned(url, revision.strip('"'))
                        for url, revision in zip(routes, revisions)
                    ]
                    with proxy(
                        args.nginx.resolve(), origin, folder / "proxy"
                    ) as address:
                        for stage in ["proxy-first", "proxy-revisit"]:
                            before = metrics(origin)
                            started = time.monotonic()
                            with concurrent.futures.ThreadPoolExecutor(
                                max_workers=9
                            ) as pool:
                                responses = list(
                                    pool.map(lambda url: fetch(address + url), fixed)
                                )
                            elapsed = time.monotonic() - started
                            assert all(r[0] == 200 for r in responses)
                            assert all(
                                r[1]["x-proxy-cache"]
                                == ("MISS" if stage == "proxy-first" else "HIT")
                                for r in responses
                            )
                            after = metrics(origin)
                            measurements.append(
                                {
                                    "trial": trial,
                                    "capacity": capacity,
                                    "stage": stage,
                                    "viewport_seconds": elapsed,
                                    "payload_bytes": sum(len(r[2]) for r in responses),
                                    "cache_hits": after["render_cache"]["hits"]
                                    - before["render_cache"]["hits"],
                                }
                            )
                            if stage == "proxy-revisit":
                                assert after["render_cache"] == before["render_cache"]
                        for header in ["Authorization", "Cookie"]:
                            assert (
                                fetch(address + fixed[0], headers={header: "private"})[
                                    1
                                ]["x-proxy-cache"]
                                == "BYPASS"
                            )
                        if args.browser and trial == 0:
                            from playwright.sync_api import sync_playwright

                            with sync_playwright() as p:
                                browser = p.chromium.launch()
                                page = browser.new_page()
                                page.goto(
                                    address
                                    + "/example?"
                                    + urlencode({"url": routes[4]})
                                )
                                page.click("#load")
                                page.wait_for_function(
                                    "document.querySelector('#image').naturalWidth === 256"
                                )
                                content = page.evaluate("""() => { const image = document.querySelector('#image');
                                  const canvas = document.createElement('canvas'); canvas.width=256; canvas.height=256;
                                  const ctx=canvas.getContext('2d'); ctx.drawImage(image,0,0);
                                  return Array.from(ctx.getImageData(0,0,256,256).data); }""")
                                check(
                                    "Chromium published pixels",
                                    np.array_equal(
                                        np.array(content, dtype="uint8").reshape(
                                            256, 256, 4
                                        ),
                                        expected(*TILES[4]),
                                    ),
                                )
                                page.screenshot(path=str(args.out / "browser.png"))
                                browser.close()
                    check(
                        "nginx immutable hits and access bypass trial " + str(trial),
                        True,
                    )
                # Duplicate delivery at zero retained budget still records work sharing.
                barrier = threading.Barrier(8)
                before = metrics(origin)

                def duplicate(_, barrier=barrier):
                    barrier.wait(timeout=10)
                    return fetch(origin + route + "&tile_size=%5B1024%2C1024%5D")

                with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                    responses = list(pool.map(duplicate, range(8)))
                after = metrics(origin)
                assert all(r[0] == 200 for r in responses)
                measurements.append(
                    {
                        "trial": trial,
                        "capacity": capacity,
                        "stage": "duplicates",
                        "raster_jobs": after["submitted"] - before["submitted"],
                        "coalesced": after["render_cache"]["coalesced"]
                        - before["render_cache"]["coalesced"],
                        "requests": 8,
                    }
                )
    layer_db = args.out / "layers.sqlite"
    native(
        args.binary, args.out, env, {"operation": "layer_create", "path": str(layer_db)}
    )
    native(
        args.binary,
        args.out,
        env,
        {
            "operation": "layer_register",
            "path": str(layer_db),
            "id": "image",
            "raster": str(raster),
            "bands": [1],
            "stretch": [0, 3000],
        },
    )
    layer_app = rcfp.NativeApplication(layer_db, settings)
    layer_url = "/layers/image/render.png"
    result = layer_app.request("GET", layer_url)
    check(
        "layer default GET",
        result[0] == 200 and result[4]["Cache-Control"] == "public, no-cache",
    )
    spec = json.loads(layer_app.request("GET", "/layers/image")[2])["default_render"]
    explicit = layer_url + "?" + urlencode({"spec": json.dumps(spec)})
    check(
        "layer default and explicit identity",
        layer_app.request("GET", explicit)[4]["ETag"] == result[4]["ETag"],
    )
    check(
        "layer conditional GET",
        layer_app.request("GET", layer_url, {"If-None-Match": result[4]["ETag"]})[0]
        == 304,
    )
    check(
        "layer pinned GET",
        layer_app.request("GET", pinned(layer_url, result[4]["X-RCFP-Revision"]))[0]
        == 200,
    )
    before = json.loads(layer_app.request("GET", "/__terracotta/metrics")[2])
    layer_app.request("GET", layer_url)
    after = json.loads(layer_app.request("GET", "/__terracotta/metrics")[2])
    check(
        "layer encoded reuse",
        before["submitted"] == after["submitted"]
        and after["render_cache"]["hits"] > before["render_cache"]["hits"],
    )
    spec["grid"]["size"] = [128, 128]
    revised = layer_app.request(
        "GET", layer_url + "?" + urlencode({"spec": json.dumps(spec)})
    )
    check(
        "layer grid identity",
        revised[0] == 200 and revised[4]["ETag"] != result[4]["ETag"],
    )
    posted = layer_app.request(
        "POST",
        "/layers/image/render",
        {"content-type": "application/json"},
        json.dumps(spec).encode(),
    )
    check(
        "layer POST no-store",
        posted[0] == 200
        and posted[4]["Cache-Control"] == "no-store"
        and "ETag" not in posted[4],
    )

    # Named profile headers and conditional behavior remain unchanged.
    folder = args.out / "compatibility"
    folder.mkdir()
    with service(
        args.binary,
        database,
        dict(env, RCFP_COMPAT_PROFILE="terracotta-0.10.1"),
        folder,
        [],
    ) as (origin, _):
        result = fetch(origin + route, headers={"If-None-Match": "*"})
        check(
            "Terracotta legacy delivery",
            result[0] == 200
            and result[1]["cache-control"] == "no-cache"
            and "etag" not in result[1],
        )
    report = {
        "binary_sha256": digest(args.binary),
        "core_sha256": digest(
            Path(rcfp.__file__).parent / "runtime/lib/librcfp_core.so"
        ),
        "checks": checks,
        "measurements": measurements,
        "excluded": [
            name
            for name, available in [("nginx", args.nginx), ("Chromium", args.browser)]
            if not available
        ],
    }
    (args.out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(
        f"Passed {len(checks)} delivery checks; {len(measurements)} measurement cells"
    )


if __name__ == "__main__":
    main()
