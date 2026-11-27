"""Read-only diagnostics and installed first-use checks against real pinned imagery."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

import numpy as np
import rasterio
from PIL import Image
from viewer_helpers import select_layer, settled

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--browser", action="store_true")
    args = parser.parse_args()
    binary, out = args.binary.resolve(), args.out.resolve()
    if os.name == "nt" and binary.suffix != ".exe":
        binary = binary.with_suffix(".exe")
    out.mkdir(parents=True, exist_ok=False)
    env = {
        k: v for k, v in os.environ.items() if not k.startswith(("TC_", "RCFP_COMPAT_"))
    }
    manifest = json.loads((ROOT / "docs/examples/data/manifest.json").read_text())
    for row in manifest["fixtures"]:
        assert digest(ROOT / "docs/examples/data" / row["file"]) == row["sha256"]

    def command(*argv, environment=None, cwd=out):
        return subprocess.run(
            [str(binary), *map(str, argv)],
            cwd=cwd,
            env=environment or env,
            check=False,
            capture_output=True,
            text=True,
            timeout=35,
        )

    sample = out / "sample"
    exported = command("demo", "-o", sample)
    assert exported.returncode == 0, exported.stderr
    red = sample / "data/red.tif"
    database = out / "map.sqlite"
    created = subprocess.run(
        [sys.executable, str(sample / "create_catalog.py"), str(database)],
        cwd=out,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert created.returncode == 0, created.stderr

    with rasterio.open(red) as src:
        profile = src.profile.copy()
        values = src.read(1)
        expected = {
            "size": [src.height, src.width],
            "transform": list(src.transform.to_gdal()),
            "mask_flags": 8,
            "dtype": "UInt16",
            "block_size": list(src.block_shapes[0]),
        }
    # Retain real pixels and georeferencing while changing storage and band count
    # to isolate layout and physical-band diagnostics.
    with rasterio.open(sample / "data/green.tif") as src:
        green = src.read(1)
    # An explicit Mercator affine isolates one-based band selection from
    # projection differences. Both channels retain their pinned real pixels.
    profile.update(
        count=2,
        tiled=True,
        blockxsize=256,
        blockysize=256,
        crs="EPSG:3857",
        transform=rasterio.transform.from_origin(0, 5120, 10, 10),
    )
    packed = out / "packed.tif"
    with rasterio.open(packed, "w", **profile) as dst:
        dst.write(values, 1)
        dst.write(green, 2)
        dst.build_overviews([2], rasterio.enums.Resampling.nearest)
    native = out / "native.sqlite"
    ingested = command(
        "ingest",
        str(packed).replace("packed.tif", "{scene}.tif"),
        "-o",
        native,
        "--bands",
        "second=2",
        "--skip-metadata",
    )
    assert ingested.returncode == 0, ingested.stderr

    class Contracts(unittest.TestCase):
        def inspect(self, *argv, code=0, environment=None):
            result = command("inspect", *argv, "--json", environment=environment)
            self.assertEqual(result.returncode, code, result.stderr + result.stdout)
            report = json.loads(result.stdout)
            self.assertEqual(report["schema_version"], 1)
            self.assertEqual(report["ok"], code == 0)
            return report

        def codes(self, report):
            return {issue["code"] for issue in report["issues"]}

        def test_supplied_samples(self):
            for row in manifest["fixtures"]:
                self.assertEqual(digest(sample / "data" / row["file"]), row["sha256"])
            self.assertEqual(
                (sample / "data/manifest.json").read_bytes(),
                (ROOT / "docs/examples/data/manifest.json").read_bytes(),
            )
            before = digest(red)
            result = command("demo", "-o", sample)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(digest(red), before)

        def test_independent_raster_declarations(self):
            report = self.inspect("--raster", red)
            raster = report["datasets"][0]["raster"]
            self.assertEqual(raster["size"], expected["size"])
            self.assertEqual(raster["transform"], expected["transform"])
            self.assertEqual(raster["bands"][0]["dtype"], expected["dtype"])
            self.assertEqual(raster["bands"][0]["mask_flags"], expected["mask_flags"])
            self.assertEqual(raster["layout"][0]["block_size"], expected["block_size"])
            self.assertIn("32632", raster["crs"])
            self.assertIsNone(report["datasets"][0]["render_probe"])

        def test_bounded_render(self):
            report = self.inspect("--raster", red, "--render-probe")
            probe = report["datasets"][0]["render_probe"][0]
            self.assertEqual(probe["band"], 1)
            self.assertEqual(probe["size"], [256, 256])
            self.assertGreater(probe["valid_pixels"], 60000)
            self.assertGreater(probe["png_bytes"], 1000)
            self.assertLess(probe["range"][0], probe["range"][1])

        def test_layout_and_overviews(self):
            missing = self.inspect("--raster", red)
            self.assertIn("overviews", self.codes(missing))
            tiled = self.inspect("--raster", packed, "--bands", "2")
            self.assertNotIn("overviews", self.codes(tiled))
            self.assertNotIn("strips", self.codes(tiled))
            self.assertEqual(tiled["datasets"][0]["selected_bands"], [2])
            self.assertEqual(
                tiled["datasets"][0]["raster"]["layout"][1]["overviews"], [[256, 256]]
            )

        def test_no_catalog_or_sidecar_write(self):
            before = {
                str(p.relative_to(out)): digest(p)
                for p in out.rglob("*")
                if p.is_file()
            }
            report = self.inspect("-d", database, "--render-probe")
            self.assertEqual(report["ordered_keys"], ["scene", "band"])
            after = {
                str(p.relative_to(out)): digest(p)
                for p in out.rglob("*")
                if p.is_file()
            }
            self.assertEqual(before, after)
            target = out / "planned.sqlite"
            self.inspect("-r", sample / "data/{band}.tif", "-o", target)
            self.assertFalse(target.exists())

        def test_native_selected_band_without_lazy_statistics(self):
            before = digest(native)
            report = self.inspect(
                "-d",
                native,
                "--render-probe",
                environment=dict(env, TC_REPROJECTION_METHOD="nearest"),
            )
            with rasterio.open(packed) as source:
                # The explicit 20 m output grid samples source cell centers at
                # indices 1,3,... . The isolated-band VRT has no stored overviews.
                selected = source.read(2, masked=True)[1::2, 1::2]
            self.assertEqual(
                report["datasets"][0]["render_probe"][0]["range"],
                [float(selected.min()), float(selected.max())],
            )
            self.assertEqual(report["source"]["format"], "rcfp-native-2")
            self.assertEqual(report["datasets"][0]["selected_bands"], [2])
            self.assertEqual(digest(native), before)
            with sqlite3.connect(native) as connection:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM metadata").fetchone()[0], 0
                )

        def test_pattern_and_destination(self):
            pattern = sample / "data/{band}.tif"
            target = out / "new.sqlite"
            report = self.inspect("-r", pattern, "-o", target)
            self.assertEqual(report["ordered_keys"], ["band"])
            self.assertEqual(report["destination"]["location"], str(target))
            self.assertFalse(report["destination"]["exists"])
            self.assertEqual(len(report["datasets"]), 5)
            mismatch = self.inspect("-r", pattern, "-o", database, code=2)
            self.assertIn("destination_keys", self.codes(mismatch))
            missing = self.inspect(
                "-r", pattern, "-o", out / "missing/new.sqlite", code=2
            )
            self.assertIn("destination", self.codes(missing))

        def test_sqlite_url_and_catalog_provider(self):
            report = self.inspect("-d", "sqlite:///" + str(database))
            self.assertEqual(report["source"]["location"], str(database))
            target = out / "url-destination.sqlite"
            report = self.inspect(
                "-r", sample / "data/{band}.tif", "-o", "sqlite:///" + str(target)
            )
            self.assertEqual(report["destination"]["location"], str(target))
            self.assertFalse(target.exists())
            report = self.inspect(
                "-d",
                database,
                environment=dict(env, TC_DRIVER_PROVIDER="mysql"),
                code=2,
            )
            self.assertIn("catalog", self.codes(report))

        def test_bad_patterns_are_actionable(self):
            for pattern in (
                sample / "data/*.tif",
                sample / "none/{band}.tif",
                sample / "data/{band.tif",
            ):
                report = self.inspect("-r", pattern, code=2)
                self.assertIn("pattern", self.codes(report))
                self.assertTrue(report["issues"][0]["next_step"])
            report = self.inspect(
                "-r", sample / "data/{band}.tif", "--rgb-key", "scene", code=2
            )
            self.assertIn("pattern", self.codes(report))

        def test_missing_path_and_bands(self):
            report = self.inspect("--raster", out / "missing.tif", code=2)
            self.assertIn("path", self.codes(report))
            for bands in ("0", "no", "2"):
                report = self.inspect("--raster", red, "--bands", bands, code=2)
                self.assertIn("bands", self.codes(report))

        def test_arguments_and_catalog_failure(self):
            for options in (
                ("--unknown",),
                ("-o", out / "new.sqlite"),
                ("-r", "pattern", "--raster", red),
                ("--raster",),
            ):
                report = self.inspect(*options, code=2)
                self.assertIn("arguments", self.codes(report))
            report = self.inspect("-d", out / "missing.sqlite", code=2)
            self.assertIn("catalog", self.codes(report))
            self.assertFalse((out / "missing.sqlite").exists())

        def test_config_origins_and_redaction(self):
            config = out / "settings.toml"
            config.write_text(
                'RESAMPLING_METHOD = "nearest"\nSQL_PASSWORD = "toml-secret"\nCOMPAT_PROFILE = "terracotta"\n'
            )
            environment = dict(
                env,
                TC_RESAMPLING_METHOD="cubic",
                TC_SQL_USER="private-user",
                TC_RASTER_WORKERS="2",
                RCFP_COMPAT_PROFILE="native",
            )
            result = command(
                "-c",
                config,
                "--compat-profile",
                "native",
                "--loglevel",
                "info",
                "inspect",
                "--json",
                environment=environment,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            for secret in ("toml-secret", "private-user"):
                self.assertNotIn(secret, result.stdout + result.stderr)
            values = json.loads(result.stdout)["configuration"]
            self.assertEqual(values["COMPAT_PROFILE"]["origin"], "cli")
            self.assertEqual(
                values["RESAMPLING_METHOD"], {"value": "nearest", "origin": "toml"}
            )
            self.assertEqual(values["SQL_USER"]["origin"], "environment")
            self.assertEqual(values["SQL_PASSWORD"]["value"], "[redacted]")
            self.assertEqual(
                values["TC_RASTER_WORKERS"], {"value": 2, "origin": "environment"}
            )
            self.assertEqual(values["LOGLEVEL"], {"value": "info", "origin": "cli"})

        def test_profile_gates_and_configured_catalog(self):
            environment = dict(
                env,
                TC_DRIVER_PATH=str(database),
                RCFP_COMPAT_PROFILE="terracotta",
            )
            report = self.inspect(environment=environment)
            self.assertEqual(report["source"]["location"], str(database))
            self.assertEqual(report["source"]["origin"], "environment")
            self.assertFalse(report["configuration"]["ASYNC_TIFF"]["active"])
            self.assertFalse(report["configuration"]["NATIVE_DIRECT_READ"]["active"])

        def test_bad_config_json_does_not_expose_values(self):
            config = out / "bad.toml"
            for value in (
                'SQL_PASSWORD = "syntax-secret"\nBROKEN = ',
                'RESAMPLING_METHOD = "invalid-secret"',
                'unknown_secret = "secret"',
            ):
                config.write_text(value)
                result = command("-c", config, "inspect", "--json")
                self.assertEqual(result.returncode, 2)
                report = json.loads(result.stdout)
                self.assertFalse(report["ok"])
                self.assertIn("configuration", self.codes(report))
                self.assertNotIn("secret", result.stdout + result.stderr)
            report = self.inspect(code=2, environment=dict(env, TC_RASTER_WORKERS="0"))
            self.assertIn("configuration", self.codes(report))

        def test_non_tiff_dependency_is_not_opened(self):
            path = out / "external.vrt"
            path.write_text(
                '<VRTDataset rasterXSize="1" rasterYSize="1"><VRTRasterBand dataType="Byte" band="1"><SimpleSource><SourceFilename>/vsicurl/http://127.0.0.1:1/secret</SourceFilename></SimpleSource></VRTRasterBand></VRTDataset>'
            )
            report = self.inspect("--raster", path, code=2)
            self.assertIn("format", self.codes(report))
            self.assertNotIn("secret", json.dumps(report))

        def test_text_output_and_all_settings(self):
            result = command("inspect", "--raster", red)
            self.assertEqual(result.returncode, 0)
            for marker in (
                "Configuration",
                "Dataset",
                "selected bands",
                "Next:",
                "[overviews]",
            ):
                self.assertIn(marker, result.stdout)
            json_report = self.inspect()
            self.assertGreater(len(json_report["configuration"]), 35)

        def test_remote_explicit_ranges_credentials_and_redaction(self):
            requests = []

            class Origin(BaseHTTPRequestHandler):
                def do_GET(self):
                    requests.append((self.path, self.headers.get("Range")))
                    route = self.path.split("?")[0]
                    status = {
                        "/range": 206,
                        "/no-range": 200,
                        "/denied": 403,
                        "/missing": 404,
                        "/redirect": 302,
                        "/bad-range": 206,
                    }.get(route, 401)
                    self.send_response(status)
                    if status == 302:
                        self.send_header("Location", "/range")
                    if status == 206:
                        self.send_header(
                            "Content-Range",
                            "bytes 0-1023/4096"
                            if route == "/range"
                            else "bytes 1-1024/4096",
                        )
                    self.send_header("Content-Length", "1024")
                    self.end_headers()
                    try:
                        self.wfile.write(b"x" * 1024)
                    except (BrokenPipeError, ConnectionResetError):
                        pass

                def log_message(self, *_):
                    pass

            server = ThreadingHTTPServer(("127.0.0.1", 0), Origin)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://user:password-secret@127.0.0.1:{server.server_port}"
            try:
                skipped = self.inspect("--raster", base + "/range?token=query-secret")
                self.assertEqual(requests, [])
                self.assertIn("remote_skipped", self.codes(skipped))
                for route, code, issue in (
                    ("range", 0, None),
                    ("no-range", 0, "range"),
                    ("bad-range", 0, "range"),
                    ("denied", 2, "credentials"),
                    ("missing", 2, "remote"),
                    ("redirect", 2, "remote"),
                ):
                    count = len(requests)
                    report = self.inspect(
                        "--raster",
                        base + f"/{route}?token=query-secret",
                        "--probe-remote",
                        code=code,
                    )
                    self.assertEqual(len(requests), count + 1)
                    self.assertEqual(requests[-1][1], "bytes=0-1023")
                    probe = report["datasets"][0]["remote_probe"]
                    self.assertLessEqual(probe["body_bytes"], 1024)
                    self.assertEqual(probe["range_supported"], route == "range")
                    if issue:
                        self.assertIn(issue, self.codes(report))
                    for secret in ("password-secret", "query-secret", "user:"):
                        self.assertNotIn(secret, json.dumps(report))
                report = self.inspect(
                    "--raster", base + "/range", "--render-probe", code=2
                )
                self.assertIn("render_remote", self.codes(report))
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

        @unittest.skipUnless(
            os.name != "nt" and shutil.which("cc"),
            "Native initialization deadline edge requires a POSIX C compiler",
        )
        def test_local_probe_deadline(self):
            # A blocked shared-library initializer isolates a native hang while
            # retaining real raster inputs and the production child deadline.
            source = out / "blocked-runtime.c"
            source.write_text(
                "#include <unistd.h>\n__attribute__((constructor)) static void wait_on_load(void) { sleep(20); }\n"
            )
            library = out / "blocked-runtime.so"
            subprocess.run(
                ["cc", "-shared", "-fPIC", str(source), "-o", str(library)],
                check=True,
                capture_output=True,
            )
            started = time.monotonic()
            report = self.inspect(
                "--raster",
                red,
                code=2,
                environment=dict(env, TERRACOTTA_GDAL_LIBRARY=str(library)),
            )
            self.assertIn("timeout", self.codes(report))
            self.assertLess(time.monotonic() - started, 12)

        def test_remote_timeout(self):
            class Slow(BaseHTTPRequestHandler):
                def do_GET(self):
                    time.sleep(6)

                def log_message(self, *_):
                    pass

            server = ThreadingHTTPServer(("127.0.0.1", 0), Slow)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                started = time.monotonic()
                report = self.inspect(
                    "--raster",
                    f"http://127.0.0.1:{server.server_port}/raster",
                    "--probe-remote",
                    code=2,
                )
                self.assertLess(time.monotonic() - started, 6)
                self.assertIn("remote", self.codes(report))
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(Contracts)
    )
    (out / "contracts.json").write_text(
        json.dumps(
            {
                "tests": result.testsRun,
                "passed": result.testsRun
                - len(result.failures)
                - len(result.errors)
                - len(result.skipped),
                "failures": len(result.failures),
                "errors": len(result.errors),
                "skipped": [
                    {"case": str(case), "reason": reason}
                    for case, reason in result.skipped
                ],
            },
            indent=2,
        )
        + "\n"
    )
    if not result.wasSuccessful():
        raise SystemExit(1)
    trial(binary, out, env, args.browser, command)


def trial(binary, out, env, browser, command):
    """Run the published first-use steps in a fresh directory outside the checkout."""
    folder = out / "first-use"
    folder.mkdir()
    started = time.monotonic()
    steps = []

    def step(name, operation):
        begin = time.monotonic()
        operation()
        steps.append({"step": name, "seconds": round(time.monotonic() - begin, 3)})

    def run(*argv):
        result = command(*argv, cwd=folder)
        assert result.returncode == 0, result.stderr + result.stdout
        return result

    step(
        "create sample catalog and preview",
        lambda: run("demo", "-o", "demo-data"),
    )
    report = json.loads(
        run(
            "inspect", "--raster", "demo-data/data/red.tif", "--render-probe", "--json"
        ).stdout
    )
    steps.append({"step": "inspect imagery", "issues": report["issues"]})

    step(
        "inspect catalog",
        lambda: run("inspect", "-d", "demo-data/map.sqlite", "--json"),
    )
    golden = ROOT / "tests/fixtures/demo/preview.png"
    manifest = json.loads((ROOT / "tests/fixtures/demo/manifest.json").read_text())
    assert digest(golden) == manifest["preview_sha256"]
    expected = np.asarray(Image.open(golden).convert("RGBA"))
    assert np.array_equal(
        np.asarray(Image.open(folder / "demo-data/map.png").convert("RGBA")), expected
    )
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    log = (folder / "server.log").open("w")
    server = subprocess.Popen(
        [str(binary), "serve", "-d", "demo-data/map.sqlite", "--port", str(port)],
        cwd=folder,
        env=env,
        stdout=log,
        stderr=log,
    )
    try:
        deadline = time.monotonic() + 20
        while True:
            try:
                with urlopen(url + "/keys", timeout=1) as response:
                    assert [row["key"] for row in json.load(response)["keys"]] == [
                        "scene",
                        "band",
                    ]
                break
            except OSError:
                assert server.poll() is None, "server exited"
                assert time.monotonic() < deadline, "server startup timeout"
                time.sleep(0.05)
        preview = url + "/singleband/lagoon/red/preview.png?stretch_range=%5B0,3000%5D"
        pages = "https://dionhaefner.github.io"
        with urlopen(
            Request(preview, headers={"Origin": pages}), timeout=10
        ) as response:
            actual = np.asarray(Image.open(io.BytesIO(response.read())).convert("RGBA"))
            assert response.headers["Access-Control-Allow-Origin"] == pages
        with urlopen(
            Request(preview, headers={"Origin": pages + ".example"}), timeout=10
        ) as response:
            assert response.headers["Access-Control-Allow-Origin"] is None
        assert np.array_equal(actual, expected), "independent Terracotta preview pixels"
        steps.append(
            {
                "step": "serve useful map",
                "decoded_pixels": "equal to pinned independent Terracotta reference",
            }
        )
        if browser:
            from playwright.sync_api import expect, sync_playwright

            client_url = run("connect", url, "--local", "--no-browser").stdout.strip()
            assert client_url == url + "/explore/"
            with sync_playwright() as playwright:
                chromium = playwright.chromium.launch(
                    headless=True, args=["--no-sandbox"]
                )
                page = chromium.new_page(viewport={"width": 1440, "height": 1000})
                external = []

                def route(request):
                    if request.request.url.startswith(
                        (url, client_url, "data:", "blob:")
                    ):
                        request.continue_()
                    else:
                        external.append(request.request.url)
                        request.abort()

                page.route("**/*", route)
                page.on(
                    "request",
                    lambda request: (
                        external.append(request.url)
                        if request.url.startswith(("http://", "https://"))
                        and not request.url.startswith((url, client_url))
                        else None
                    ),
                )
                page.goto(client_url)
                select_layer(page, '["lagoon","red"]')
                expect(page.locator("#status")).to_contain_text("lagoon / red")
                expect(page.locator(".leaflet-tile-loaded").first).to_be_visible(
                    timeout=15000
                )
                page.screenshot(path=str(folder / "map.png.browser.png"))
                failures = []
                page.on(
                    "response",
                    lambda response: (
                        failures.append(
                            {"url": response.url, "status": response.status}
                        )
                        if "/rgb/" in response.url and response.status != 200
                        else None
                    ),
                )
                expect(page.get_by_role("button", name="Share view")).to_be_hidden()
                page.get_by_label("Display mode", exact=True).select_option("rgb")
                expect(page.locator("#header-layer")).to_contain_text("RGB")
                settled(page)
                for name in ("Zoom in", "Zoom in", "Zoom out"):
                    page.get_by_role("button", name=name, exact=True).click()
                    # Leaflet's zoom animation completes before the new tile grid settles.
                    page.wait_for_timeout(350)
                    page.wait_for_function(
                        """() => {
                        const tiles = [...document.querySelectorAll('.leaflet-tile')];
                        return tiles.length > 0 && tiles.every(tile =>
                            tile.classList.contains('leaflet-tile-loaded') &&
                            tile.naturalWidth > 0);
                    }""",
                        timeout=15000,
                    )
                page.screenshot(path=str(folder / "map.rgb-zoom.png"))
                assert failures == [], failures
                steps.append(
                    {
                        "step": "RGB navigation at default capacity",
                        "failed_tiles": failures,
                    }
                )
                page.goto(url + "/apidoc")
                expect(page.locator("body")).to_contain_text(
                    "RCFP raster tile service.", timeout=15000
                )
                page.wait_for_timeout(500)
                assert external == [], external
                chromium.close()
                steps.append({"step": "offline Chromium map", "external_requests": 0})
    finally:
        server.terminate()
        server.wait(timeout=15)
        log.close()
    report = {
        "kind": "automated independent-reference first-use trial",
        "binary_sha256": digest(binary),
        "seconds": round(time.monotonic() - started, 3),
        "steps": steps,
        "obstacles": [
            issue["message"]
            for issue in report["issues"]
            if issue["severity"] == "warning"
        ],
        "human_participant": False,
        "browser": "passed" if browser else "not run",
    }
    (out / "first-use.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"First-use trial passed in {report['seconds']} seconds", flush=True)


if __name__ == "__main__":
    main()
