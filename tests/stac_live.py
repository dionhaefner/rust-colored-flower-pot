"""Opt-in HTTPS provider qualification with independently pinned Sentinel TIFFs.

Requires an installed wheel, Rasterio, Pillow and the native-rendering reference
helpers. Add --browser for the packaged Chromium map. Network failures are
failures, and this runner is separate from deterministic offline contracts.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import socket
import subprocess
import time
import traceback
import urllib.error
import urllib.request
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import numpy as np
from native_rendering import bounds_for_tile, expected
from navigation_benchmark import tile
from PIL import Image
from rasterio.enums import Resampling
from viewer_helpers import select_layer, view_link

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests/fixtures/stac-live.json"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def request(url, data=None):
    headers = {"User-Agent": "RCFP-STAC-verification"}
    if data is not None:
        data = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    return urllib.request.urlopen(
        urllib.request.Request(url, data=data, headers=headers), timeout=30
    )


def read_json(url, data=None):
    with request(url, data) as response:
        return json.loads(response.read(8 * 1024 * 1024 + 1))


def sign(href):
    return read_json(
        "https://planetarycomputer.microsoft.com/api/sas/v1/sign?"
        + urlencode({"href": href})
    )["href"]


def acquire(item, cache, href):
    path = cache / item["file"]
    if not path.exists():
        temporary = path.with_suffix(".download")
        try:
            with request(href) as response, temporary.open("wb") as destination:
                count = 0
                while chunk := response.read(1024 * 1024):
                    count += len(chunk)
                    assert count <= item["bytes"], "reference exceeds pinned byte size"
                    destination.write(chunk)
            assert digest(temporary) == item["sha256"], "reference checksum mismatch"
            temporary.rename(path)
        finally:
            temporary.unlink(missing_ok=True)
    assert path.stat().st_size == item["bytes"]
    assert digest(path) == item["sha256"], "cached reference checksum mismatch"
    return path


def available_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return str(sock.getsockname()[1])


@contextlib.contextmanager
def process(binary, arguments, url, log_path):
    with log_path.open("w") as log:
        child = subprocess.Popen(
            [str(binary), *arguments],
            stdout=log,
            stderr=log,
            env={
                **os.environ,
                "RCFP_COMPAT_PROFILE": "native",
                "TC_RASTER_WORKERS": "2",
                "TC_ALLOWED_ORIGINS_METADATA": '["*"]',
                "TC_ALLOWED_ORIGINS_TILES": '["*"]',
                "TC_RASTER_RESAMPLING_METHOD": "bilinear",
            },
        )
        try:
            deadline = time.monotonic() + 30
            while True:
                assert child.poll() is None, log_path.read_text()
                try:
                    with request(url) as response:
                        assert response.status == 200
                    break
                except (urllib.error.URLError, TimeoutError):
                    assert time.monotonic() < deadline, "service startup deadline"
                    time.sleep(0.05)
            yield
        finally:
            child.terminate()
            child.wait(timeout=30)


def compare(png, source, bounds, band, display_range, destination):
    image = Image.open(io.BytesIO(png))
    assert image.mode == "P", "expected indexed PNG"
    values, valid = expected(
        source, bounds, [image.height, image.width], Resampling.bilinear, destination
    )
    values = values * band["scale"] + band["offset"]
    low, high = display_range
    indices = np.round(1 + np.clip((values - low) / (high - low), 0, 1) * 254).astype(
        np.uint8
    )
    indices[~valid] = 0
    actual = np.asarray(image)
    assert np.array_equal(actual == 0, ~valid), "coverage differs from GDAL reference"
    difference = np.abs(actual.astype(int) - indices.astype(int))
    assert difference.max() <= 1, "display samples differ from GDAL reference"
    assert np.count_nonzero(actual) > actual.size // 2, "empty display"
    return {
        "valid_pixels": int(valid.sum()),
        "max_display_index_difference": int(difference.max()),
    }


def browse(binary, base, folder, ids):
    from playwright.sync_api import expect, sync_playwright

    client = subprocess.run(
        [str(binary), "connect", base, "--no-browser"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            external, errors = [], []

            def local_only(route):
                if route.request.url.startswith(
                    (base + "/", client + "/", "data:", "blob:")
                ):
                    route.continue_()
                else:
                    external.append(route.request.url)
                    route.abort()

            context.route("**/*", local_only)
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(client)
            expect(page.locator(".layer-row")).to_have_count(len(ids))
            for index, item_id in enumerate(ids):
                select_layer(page, index=index)
                expect(page.locator("#metadata")).to_contain_text(item_id)
                expect(page.locator(".leaflet-tile-loaded").first).to_be_visible()
                expect(page.locator("#share")).to_be_hidden()
                keys = json.loads(
                    page.locator(".layer-row[aria-pressed=true]").get_attribute(
                        "data-layer-id"
                    )
                )
                page.goto(
                    view_link(
                        page,
                        {
                            "mode": "singleband",
                            "keys": keys,
                            "params": {
                                "stretch_range": [
                                    float(page.locator(f"#{name}").input_value())
                                    for name in ("minimum", "maximum")
                                ],
                            },
                        },
                        center=[12.35, 45.45],
                        zoom=13,
                    )
                )
                expect(page.locator("#workspace")).to_have_attribute(
                    "aria-busy", "false"
                )
                expect(page.locator("#metadata")).to_contain_text(item_id)
                expect(page.locator(".leaflet-tile-loaded").first).to_be_visible()
                inspect = page.get_by_role("button", name="Inspect", exact=True)
                if inspect.get_attribute("aria-pressed") == "false":
                    inspect.click()
                page.locator("#map").press("Enter")
                expect(page.locator("#inspection")).to_contain_text("Display RGBA:")
                page.screenshot(path=str(folder / f"observation-{index}.png"))
            assert not external and not errors, (external, errors)
            context.close()
        finally:
            browser.close()


def verify(provider, manifest, args, folder):
    import rcfp
    from rcfp.stac import SearchClient

    rows = provider["items"]
    ids = [row["id"] for row in rows]
    asset_key = provider["asset"]
    revisions = {
        f"{manifest['collection']}/{row['id']}/{asset_key}": row["sha256"]
        for row in rows
    }
    query = {
        "collections": [manifest["collection"]],
        "ids": ids,
        "bbox": manifest["bbox"],
        "datetime": manifest["datetime"],
        "limit": 1,
    }
    paths, urls = {}, {}
    for row in rows:
        location = (
            sign(row["href"])
            if provider["name"] == "planetary-computer"
            else row["href"]
        )
        paths[row["id"]] = acquire(row, args.cache, location)
        urls[f"{manifest['collection']}/{row['id']}/{asset_key}"] = location
    signer_calls = []

    def signer(asset):
        signer_calls.append(asset["item"])
        location = urls[f"{manifest['collection']}/{asset['item']}/{asset_key}"]
        if len(signer_calls) == 1:
            url = urlsplit(location)
            parameters = dict(parse_qsl(url.query))
            parameters["sig"] = "invalid"
            return urlunsplit(url._replace(query=urlencode(parameters)))
        return location

    client = SearchClient(
        provider["endpoint"],
        signer=signer if provider["name"] == "planetary-computer" else None,
    )
    arguments = {
        "assets": [asset_key],
        "bbox": query["bbox"],
        "datetime": query["datetime"],
        "collections": query["collections"],
        "ids": ids,
        "revisions": revisions,
    }
    started = time.monotonic()
    with client.search(**arguments, page_size=1) as selection:
        public = selection.metadata
        assert [asset["item"] for asset in public["assets"]] == ids
        assert (
            public["metrics"]["pages"] >= 2 and public["metrics"]["returned_items"] == 2
        )
        for asset, row in zip(public["assets"], rows, strict=True):
            west, south, east, north = asset["bbox"]
            qwest, qsouth, qeast, qnorth = query["bbox"]
            assert (
                west <= qeast and east >= qwest and south <= qnorth and north >= qsouth
            )
            assert asset["revision"] == row["sha256"]
            assert datetime.fromisoformat(asset["datetime"]) == datetime.fromisoformat(
                row["datetime"]
            )
        selection_wall_ms = (time.monotonic() - started) * 1000
        with client.search(**arguments, page_size=2) as repeated:
            assert repeated.id == selection.id
            assert repeated.metadata["search_id"] != public["search_id"]
        empty_args = dict(arguments, bbox=[-1, -1, 0, 0])
        with client.search(**empty_args, page_size=1) as empty:
            assert empty.metadata["assets"] == []
        day = rows[0]["datetime"][:10]
        with client.search(
            **dict(arguments, datetime=f"{day}T00:00:00Z/{day}T23:59:59Z"), page_size=1
        ) as narrowed:
            assert [asset["item"] for asset in narrowed.metadata["assets"]] == [ids[0]]
        try:
            client.search(**arguments, max_items=1, page_size=1)
        except rcfp.RcfpError as error:
            assert "limit" in str(error)
        else:
            raise AssertionError("result limit returned a partial selection")
        x, y = tile(12.35, 45.45, 13)
        bounds = bounds_for_tile(x, y, 13)
        display_range = [0, 0.2] if asset_key == "coastal" else [0, 2000]
        render_rows = []
        for index, asset in enumerate(public["assets"]):
            started = time.monotonic()
            png = selection.render(index, bounds_mercator=bounds, range=display_range)
            first_ms = (time.monotonic() - started) * 1000
            started = time.monotonic()
            assert (
                selection.render(index, bounds_mercator=bounds, range=display_range)
                == png
            )
            repeat_ms = (time.monotonic() - started) * 1000
            (folder / f"python-{index}.png").write_bytes(png)
            metrics = compare(
                png,
                paths[asset["item"]],
                bounds,
                asset["bands"][0],
                display_range,
                folder / f"reference-{index}.vrt",
            )
            render_rows.append(
                {
                    "item": asset["item"],
                    "first_with_download_ms": first_ms,
                    "repeat_ms": repeat_ms,
                    **metrics,
                }
            )
        if provider["name"] == "planetary-computer":
            assert len(signer_calls) == 5, "signed URL retry was not exercised"
        (folder / "selection.json").write_text(json.dumps(public, indent=2) + "\n")

    (folder / "revisions.json").write_text(json.dumps(revisions, indent=2) + "\n")
    options = {"assets": [asset_key], "revisions": "revisions.json"}
    if provider["name"] == "planetary-computer":
        private = folder / "asset-urls.json"
        descriptor = os.open(private, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as file:
            json.dump(urls, file)
        options["asset_urls"] = private.name
    source = folder / "source.json"
    source.write_text(
        json.dumps(
            {
                "type": "rcfp-source",
                "source": provider["endpoint"],
                "search": query,
                "options": options,
            },
            indent=2,
        )
        + "\n"
    )
    if provider["name"] == "earth-search":
        source = ROOT / "docs/examples/data/earth-search-source.json"
        documented = json.loads(source.read_text())
        assert (
            documented["source"] == provider["endpoint"]
            and documented["search"] == query
        )
        assert (
            json.loads((source.parent / documented["options"]["revisions"]).read_text())
            == revisions
        )
    base = "http://127.0.0.1:" + available_port()
    with process(
        args.binary,
        ["serve", "--source", str(source), "--bind", base.removeprefix("http://")],
        base + "/keys",
        folder / "service.log",
    ):
        datasets = read_json(base + "/datasets")["datasets"]
        assert [dataset["item"] for dataset in datasets] == ids
        assert (
            read_json(base + "/__terracotta/identity")["source_revision"]
            == public["id"]
        )
        for index, dataset in enumerate(datasets):
            key = "/".join(
                quote(dataset[k], safe="")
                for k in ("collection", "datetime", "item", "asset")
            )
            metadata = read_json(base + "/metadata/" + key)
            assert metadata["metadata"]["stac"]["revision"] == rows[index]["sha256"]
            url = (
                base
                + f"/singleband/{key}/13/{x}/{y}.png?"
                + urlencode(
                    {"stretch_range": json.dumps(display_range), "colormap": "viridis"}
                )
            )
            with request(url) as response:
                png = response.read()
            assert png == (folder / f"python-{index}.png").read_bytes()
            (folder / f"http-{index}.png").write_bytes(png)
        if args.browser:
            browse(args.binary, base, folder, ids)
        exposed = (
            json.dumps(public)
            + json.dumps(datasets)
            + (folder / "service.log").read_text()
        )
        for location in urls.values():
            assert location not in exposed
            if "?" in location:
                for name, value in parse_qsl(urlsplit(location).query):
                    if name == "sig":
                        assert value not in exposed
    return {
        "selection_id": public["id"],
        "search_id": public["search_id"],
        "metrics": public["metrics"],
        "selection_wall_ms": selection_wall_ms,
        "render": render_rows,
        "signed_url_refresh": bool(signer_calls),
        "browser": args.browser,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cache", type=Path, default=ROOT / ".cache/stac-live")
    parser.add_argument("--browser", action="store_true")
    args = parser.parse_args()
    args.binary, args.wheel, args.out, args.cache = (
        path.resolve() for path in (args.binary, args.wheel, args.out, args.cache)
    )
    args.out.mkdir(parents=True, exist_ok=False)
    args.cache.mkdir(parents=True, exist_ok=True)
    import rcfp

    assert Path(rcfp.__file__).resolve().is_relative_to(Path(os.sys.prefix).resolve())
    with zipfile.ZipFile(args.wheel) as archive:
        binary_name = "rcfp/runtime/bin/" + args.binary.name
        assert hashlib.sha256(archive.read(binary_name)).hexdigest() == digest(
            args.binary
        )
        for name in archive.namelist():
            if name.startswith("rcfp/runtime/lib/") and "rcfp_core" in name:
                installed = Path(rcfp.__file__).parent / name.removeprefix("rcfp/")
                assert hashlib.sha256(archive.read(name)).hexdigest() == digest(
                    installed
                )
    manifest = json.loads(MANIFEST.read_text())
    records = []
    for provider in manifest["providers"]:
        folder = args.out / provider["name"]
        folder.mkdir()
        record = {
            "provider": provider["name"],
            "endpoint": provider["endpoint"],
            "status": "passed",
        }
        try:
            record.update(verify(provider, manifest, args, folder))
        except Exception as error:  # noqa: BLE001 - retain failure without signed URLs
            frame = traceback.extract_tb(error.__traceback__)[-1]
            record.update(
                status="failed",
                error_type=type(error).__name__,
                location=f"{Path(frame.filename).name}:{frame.lineno}",
                check=frame.line,
            )
            if isinstance(error, (AssertionError, rcfp.RcfpError)) or type(
                error
            ).__module__.startswith("playwright."):
                record["message"] = str(error)
            print(f"failed: {provider['name']} ({type(error).__name__})", flush=True)
        else:
            print(f"passed: {provider['name']}", flush=True)
        records.append(record)
        report = {
            "utc_time": datetime.now(UTC).isoformat(),
            "wheel_sha256": digest(args.wheel),
            "binary_sha256": digest(args.binary),
            "manifest_sha256": digest(MANIFEST),
            "cases": records,
            "skips": [] if args.browser else ["Chromium was not requested"],
            "excluded": [
                "Provider availability or WAN performance guarantees",
                "Cloud SDK credential chains",
            ],
        }
        (args.out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    assert all(record["status"] == "passed" for record in records), (
        "live provider qualification failed"
    )


if __name__ == "__main__":
    main()
