"""Exercise the public SDK, packaged viewer and independent maps in real Chromium."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests/fixtures/corpus"
ENCODED_SCENE = "lagoon / +% #? & ü {x}"
REVISION = "public-fixtures-v1"


def ports() -> tuple[int, int]:
    with socket.socket() as first, socket.socket() as second:
        first.bind(("127.0.0.1", 0))
        second.bind(("127.0.0.1", 0))
        return first.getsockname()[1], second.getsockname()[1]


def ready(url: str, process: subprocess.Popen) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1).close()
            return
        except (urllib.error.URLError, TimeoutError):
            if process.poll() is not None:
                raise RuntimeError(f"service exited while starting: {url}") from None
            time.sleep(0.1)
    raise RuntimeError(f"service did not start: {url}")


def operation(binary: Path, folder: Path, request: dict) -> None:
    instruction = folder / "operation.json"
    instruction.write_text(json.dumps(request))
    subprocess.run(
        [str(binary), "data-operation", str(instruction)],
        check=True,
        capture_output=True,
    )


def selected(scene="lagoon", date="2024-01-01", band="red"):
    return json.dumps([scene, date, band], ensure_ascii=False, separators=(",", ":"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    binary, out = args.binary.resolve(), args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    manifest = json.loads((CORPUS / "manifest.json").read_text())
    for row in manifest["fixtures"]:
        path = CORPUS / row["file"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
            raise RuntimeError(f"fixture changed: {path}")
    for row in json.loads(
        (ROOT / "src/assets/client/vendor/manifest.json").read_text()
    ):
        data = (ROOT / "src/assets/client/vendor" / row["file"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == row["sha256"], row["file"]
    database = out / "catalog.sqlite"
    operation(
        binary,
        out,
        {
            "operation": "create",
            "path": str(database),
            "keys": [
                {"key": key, "description": ""} for key in ("scene", "time", "band")
            ],
        },
    )
    # Time labels exercise explicit key selection; both use the same pinned scene.
    for scene in ("lagoon", ENCODED_SCENE):
        for date in ("2024-01-01", "2024-01-02"):
            for band in ("red", "green", "blue", "nir"):
                operation(
                    binary,
                    out,
                    {
                        "operation": "insert",
                        "path": str(database),
                        "keys": [scene, date, band],
                        "raster": str(CORPUS / (band + ".tif")),
                    },
                )
    reports = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--use-angle=swiftshader",
                "--enable-unsafe-swiftshader",
            ],
        )
        for profile in ("native", "terracotta-0.10.1"):
            checks, responses, errors, external, expected_failures = [], [], [], [], []
            backend_port, client_port = ports()
            backend_url = f"http://127.0.0.1:{backend_port}"
            client_url = f"http://127.0.0.1:{client_port}"
            log = (out / f"{profile}-services.log").open("w")
            backend = subprocess.Popen(
                [
                    str(binary),
                    "--compat-profile",
                    profile,
                    "serve",
                    "-d",
                    str(database),
                    "--bind",
                    f"127.0.0.1:{backend_port}",
                ],
                stdout=log,
                stderr=log,
                env={
                    **os.environ,
                    "TC_RASTER_QUEUE_CAPACITY": "64",
                    "TC_ALLOWED_ORIGINS_METADATA": json.dumps([client_url]),
                    "TC_ALLOWED_ORIGINS_TILES": json.dumps([client_url]),
                },
            )
            client = None
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            page = None
            try:
                ready(backend_url + "/keys", backend)
                client = subprocess.Popen(
                    [
                        str(binary),
                        "connect",
                        backend_url,
                        "--port",
                        str(client_port),
                        "--source-revision",
                        REVISION,
                        "--no-browser",
                    ],
                    stdout=log,
                    stderr=log,
                )
                ready(client_url, client)
                for filename in (
                    "index.js",
                    "index.d.ts",
                    "integrations.js",
                    "integrations.d.ts",
                    "package.json",
                    "LICENSE",
                ):
                    with urllib.request.urlopen(
                        client_url + "/sdk/" + filename
                    ) as response:
                        assert (
                            response.read() == (ROOT / "js/sdk" / filename).read_bytes()
                        ), filename
                for filename in ("viewer.js", "viewer.css"):
                    with urllib.request.urlopen(
                        client_url + "/" + filename
                    ) as response:
                        assert (
                            response.read()
                            == (ROOT / "src/assets/client" / filename).read_bytes()
                        ), filename
                checks.append("embedded-public-assets-match-source")
                page = context.new_page()
                page.on(
                    "pageerror", lambda error, errors=errors: errors.append(str(error))
                )
                page.on(
                    "response",
                    lambda response, responses=responses, backend_url=backend_url: (
                        responses.append(
                            {"url": response.url, "status": response.status}
                        )
                        if response.url.startswith(backend_url)
                        else None
                    ),
                )

                def only_local(
                    route,
                    request,
                    backend_url=backend_url,
                    client_url=client_url,
                    external=external,
                ):
                    if route.request.url.startswith(
                        (backend_url, client_url, "data:", "blob:")
                    ):
                        route.continue_()
                    else:
                        external.append(route.request.url)
                        route.abort()

                context.route("**/*", only_local)
                page.goto(client_url)
                page.get_by_label("Dataset", exact=True).select_option(selected())
                expect(page.locator("#status")).to_contain_text(
                    "lagoon / 2024-01-01 / red"
                )
                expect(page.locator(".leaflet-tile-loaded").first).to_be_visible()
                assert page.locator("#legend .swatch").count() == 16
                metadata = json.loads(page.locator("#metadata").text_content())
                assert "bounds" in metadata and "percentiles" in metadata
                page.wait_for_timeout(350)
                page.screenshot(path=str(out / f"{profile}-singleband.png"))
                checks.append("offline-discovery-singleband-metadata-legend")

                page.get_by_label("Time key", exact=True).select_option("time")
                assert (
                    page.get_by_label("Time", exact=True).locator("option").count() == 2
                )
                page.get_by_label("Time", exact=True).select_option("2024-01-02")
                expect(page.locator("#status")).to_contain_text("2024-01-02 / red")
                assert page.locator("#dataset").input_value() == selected(
                    date="2024-01-02"
                )
                checks.append("explicit-time-browsing")

                page.get_by_label("Rendering", exact=True).select_option("rgb")
                for label, band in (
                    ("Red", "red"),
                    ("Green", "green"),
                    ("Blue", "blue"),
                ):
                    page.get_by_label(label, exact=True).select_option(band)
                with page.expect_response(
                    lambda response: "/rgb/" in response.url and response.status == 200
                ):
                    page.get_by_role("button", name="Apply rendering").click()
                expect(page.locator("#status")).to_contain_text("2024-01-02 / red")
                page.wait_for_timeout(350)
                page.screenshot(path=str(out / f"{profile}-rgb.png"))
                checks.append("rgb")

                page.get_by_label("Rendering", exact=True).select_option("compute")
                page.get_by_label("v1", exact=True).select_option("nir")
                page.get_by_label("v2", exact=True).select_option("red")
                page.get_by_label("Stretch minimum").fill("-1")
                page.get_by_label("Stretch maximum").fill("1")
                with page.expect_response(
                    lambda response, responses=responses, backend_url=backend_url: (
                        "/compute/" in response.url and response.status == 200
                    )
                ):
                    page.get_by_role("button", name="Apply rendering").click()
                checks.append("expression")
                page.get_by_role("button", name="Inspect map center").click()
                expect(page.locator("#inspection")).to_contain_text("Display RGBA:")
                checks.append("keyboard-display-point-inspection")

                page.get_by_role("button", name="Create share link").click()
                expect(page.locator("#share-link")).not_to_have_value("")
                shared_url = page.locator("#share-link").input_value()
                assert "sourceRevision" in shared_url and "renderRevision" in shared_url
                page.reload()
                expect(page.locator("#status")).to_contain_text("2024-01-02")
                assert page.locator("#mode").input_value() == "compute"
                assert page.locator("#time").input_value() == "2024-01-02"
                assert page.locator("#minimum").input_value() == "-1"
                checks.append("revisioned-state-restores-on-reload")
                page.get_by_role("button", name="Create share link").click()
                expect(page.locator("#share-link")).not_to_have_value("")
                page.evaluate(
                    """async ({service,profile,revision}) => {
                    const sdk = await import('/sdk/index.js');
                    const state = await sdk.createState({baseUrl:service,profile,sourceRevision:revision,
                        render:{mode:'singleband',keys:['lagoon','2024-01-01','blue'],params:{stretch_range:[0,3000]}},
                        view:{center:[12.36,45.46],zoom:12},timeKey:'time'});
                    location.hash = sdk.encodeState(state);
                }""",
                    {"service": backend_url, "profile": profile, "revision": REVISION},
                )
                expect(page.locator("#status")).to_contain_text("2024-01-01 / blue")
                assert page.locator("#mode").input_value() == "singleband"
                checks.append("hash-navigation-uses-current-state")

                page.get_by_label("Dataset", exact=True).select_option(
                    selected(scene=ENCODED_SCENE)
                )
                expect(page.locator("#status")).to_contain_text(ENCODED_SCENE)
                checks.append("encoded-path-components-live-server")

                page.get_by_label("Rendering", exact=True).select_option("singleband")
                page.get_by_label("Stretch minimum").fill("0")
                page.get_by_label("Stretch maximum").fill("3000")
                page.get_by_role("button", name="Apply rendering").click()
                expect(page.locator("#status")).to_contain_text(ENCODED_SCENE)
                page.get_by_label("Dataset", exact=True).select_option(selected())
                expect(page.locator("#status")).to_contain_text(
                    "lagoon / 2024-01-01 / red"
                )

                sdk_checks = page.evaluate(
                    """async ({ service, profile, scene }) => {
                    const sdk = await import('/sdk/index.js');
                    const client = new sdk.Client(service, { profile });
                    const identity = await client.connect();
                    if (identity.compatibility_profile !== profile) throw Error('profile');
                    const keys = [scene, '2024-01-01', 'red'];
                    const batch = await client.batchMetadata([keys]);
                    if (!batch[0].bounds) throw Error('batch metadata');
                    const render = {mode:'singleband', keys, params:{stretch_range:[0,3000], tile_size:[128,192]}};
                    const image = await client.preview(render);
                    const sample = await sdk.inspectImage(image, [.5,.5]);
                    if (sample.size[0] !== 128 || sample.size[1] !== 192) throw Error('preview size');
                    let missing, mismatch;
                    try { await client.metadata(['missing','date','band']); }
                    catch (e) { missing = e.kind === 'http' && e.status === 404; }
                    try { await new sdk.Client(service,{profile:profile === 'native' ? 'terracotta-0.10.1' : 'native'}).connect(); }
                    catch (e) { mismatch = e.kind === 'profile'; }
                    if (!missing || !mismatch) throw Error('error/profile behavior');
                    const state = await sdk.createState({baseUrl:service, profile, render, sourceRevision:'v1', view:{center:[12,45],zoom:12}});
                    try { await sdk.restoreState(sdk.encodeState(state),{baseUrl:service,profile,sourceRevision:'v2'}); throw Error('accepted stale revision'); }
                    catch (e) { if (e.kind !== 'state') throw e; }
                    let secret = false;
                    try { await sdk.createState({...state,baseUrl:service+'?token=private'}); }
                    catch (e) { secret = true; }
                    if (!secret) throw Error('secret accepted');
                    return {sample, batch:true, errors:true, profile:true, revision:true, secret:true};
                    }""",
                    {
                        "service": backend_url,
                        "profile": profile,
                        "scene": ENCODED_SCENE,
                    },
                )
                checks.append("cors-json-post-preview-errors-profile-state-secrets")
                expected_failures.append("/metadata/missing/date/band")

                denied_url = client_url.replace("127.0.0.1", "localhost")
                context.route(denied_url + "/**", lambda route: route.continue_())
                public_version = page.evaluate(
                    """async url => (await import(url + '/sdk/index.js')).VERSION""",
                    denied_url,
                )
                assert public_version == "0.1.0"
                checks.append("cross-origin-public-module-import")
                denied = context.new_page()
                denied.goto(denied_url)
                expect(denied.locator("#status")).to_contain_text("CORS")
                denied_result = denied.evaluate(
                    """async ({service,profile}) => {
                    const {Client} = await import('/sdk/index.js');
                    try { await new Client(service,{profile}).metadata(['lagoon','2024-01-01','red']); return false; }
                    catch(e) { return e.kind === 'network'; }
                }""",
                    {"service": backend_url, "profile": profile},
                )
                assert denied_result
                denied.close()
                checks.append("real-browser-denied-origin")

                def slow_metadata(route):
                    time.sleep(0.25)
                    try:
                        route.continue_()
                    except PlaywrightError as error:
                        print(f"Aborted delayed metadata request: {error}")

                page.route("**/metadata/lagoon/2024-01-01/red", slow_metadata)
                abort_result = page.evaluate(
                    """async ({service,profile}) => {
                    const {Client} = await import('/sdk/index.js');
                    const c = new Client(service,{profile}), controller = new AbortController();
                    const pending = c.metadata(['lagoon','2024-01-01','red'],{signal:controller.signal});
                    setTimeout(() => controller.abort(), 20);
                    try {await pending; return false;} catch(e) {return e.name === 'AbortError';}
                    }""",
                    {"service": backend_url, "profile": profile},
                )
                assert abort_result
                page.evaluate(
                    """() => {
                    const select = document.getElementById('dataset');
                    select.value = JSON.stringify(['lagoon','2024-01-01','red']);
                    select.dispatchEvent(new Event('change'));
                    setTimeout(() => {select.value = JSON.stringify(['lagoon','2024-01-02','blue']); select.dispatchEvent(new Event('change'));},20);
                    }"""
                )
                expect(page.locator("#status")).to_contain_text("2024-01-02 / blue")
                page.wait_for_timeout(350)
                expect(page.locator("#status")).to_contain_text("2024-01-02 / blue")
                assert (
                    json.loads(page.locator("#metadata").text_content())["keys"]["band"]
                    == "blue"
                )
                page.unroute("**/metadata/lagoon/2024-01-01/red", slow_metadata)
                checks.append("real-fetch-abort-and-stale-view-suppression")

                page.get_by_label("Rendering", exact=True).select_option("compute")
                page.get_by_label("Expression", exact=True).fill("unknown(")
                page.get_by_role("button", name="Apply rendering").click()
                expect(page.locator("#status")).to_contain_text("failed")
                page.get_by_label("Rendering", exact=True).select_option("singleband")
                page.get_by_role("button", name="Apply rendering").click()
                expect(page.locator("#status")).to_contain_text("2024-01-02 / blue")
                expected_failures.append("expression=unknown")
                checks.append("render-error-and-recovery")

                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate(
                    "document.documentElement.scrollWidth <= innerWidth + 1"
                )
                page.get_by_label("Dataset", exact=True).focus()
                assert page.locator("#dataset").evaluate(
                    "e => e === document.activeElement"
                )
                page.screenshot(path=str(out / f"{profile}-mobile.png"), full_page=True)
                checks.append("mobile-layout-and-keyboard-controls")
                page.set_viewport_size({"width": 1440, "height": 1000})

                for example in ("leaflet", "maplibre", "openlayers"):
                    before = len(responses)
                    page.goto(client_url + f"/examples/{example}.html")
                    page.get_by_label("Dataset", exact=True).select_option(
                        label="lagoon / 2024-01-01 / red"
                    )
                    expect(page.locator("#status")).to_contain_text(
                        "lagoon / 2024-01-01 / red"
                    )
                    tile_prefix = (
                        "/tiles/" if profile == "native" else "/singleband/lagoon/"
                    )
                    page.wait_for_function(
                        "prefix => performance.getEntriesByType('resource').some(r=>r.name.includes(prefix))",
                        arg=tile_prefix,
                    )
                    page.wait_for_timeout(500)
                    assert any(
                        tile_prefix in row["url"] and row["status"] == 200
                        for row in responses[before:]
                    ), example
                    if profile == "native":
                        assert any(
                            "/tilesets?" in row["url"] and row["status"] == 200
                            for row in responses[before:]
                        ), example
                        assert any(
                            "/tilejson/" in row["url"] and row["status"] == 200
                            for row in responses[before:]
                        ), example
                    page.screenshot(path=str(out / f"{profile}-{example}.png"))
                    checks.append(f"independent-{example}-public-sdk-offline")
                assert not external, external
                assert not errors, errors
                unexpected = [
                    row
                    for row in responses
                    if row["status"] >= 400
                    and not any(part in row["url"] for part in expected_failures)
                ]
                assert not unexpected, unexpected
                reports.append(
                    {
                        "profile": profile,
                        "checks": checks,
                        "sdk": sdk_checks,
                        "responses": responses,
                        "page_errors": errors,
                        "external_requests": external,
                        "expected_failure_cases": expected_failures,
                    }
                )
            except (AssertionError, PlaywrightError, ValueError) as failure:
                (out / f"{profile}-failure.json").write_text(
                    json.dumps(
                        {
                            "failure": str(failure),
                            "responses": responses,
                            "errors": errors,
                            "status": page.locator("#status").inner_text()
                            if page is not None
                            else None,
                            "external": external,
                        },
                        indent=2,
                    )
                    + "\n"
                )
                if page is not None:
                    page.screenshot(
                        path=str(out / f"{profile}-failure.png"), full_page=True
                    )
                raise
            finally:
                context.close()
                if client is not None:
                    client.terminate()
                    client.wait(timeout=10)
                backend.terminate()
                backend.wait(timeout=10)
                log.close()
        native_database = out / "native.sqlite"
        operation(
            binary, out, {"operation": "layer_create", "path": str(native_database)}
        )
        operation(
            binary,
            out,
            {
                "operation": "layer_register",
                "path": str(native_database),
                "id": "lagoon",
                "raster": str(CORPUS / "red.tif"),
                "stretch": [0, 3000],
            },
        )
        backend_port, client_port = ports()
        backend_url, client_url = (
            f"http://127.0.0.1:{backend_port}",
            f"http://127.0.0.1:{client_port}",
        )
        log = (out / "native-layer-services.log").open("w")
        backend = subprocess.Popen(
            [
                str(binary),
                "serve",
                "-d",
                str(native_database),
                "--bind",
                f"127.0.0.1:{backend_port}",
            ],
            stdout=log,
            stderr=log,
        )
        client = None
        context = browser.new_context()
        try:
            ready(backend_url + "/layers", backend)
            client = subprocess.Popen(
                [
                    str(binary),
                    "connect",
                    backend_url,
                    "--port",
                    str(client_port),
                    "--no-browser",
                ],
                stdout=log,
                stderr=log,
            )
            ready(client_url, client)
            page = context.new_page()
            page.goto(client_url)
            expect(page.locator("#status")).to_contain_text(
                "native: lagoon / image / 1"
            )
            page.wait_for_function(
                """() => [...document.querySelectorAll('.leaflet-tile-loaded')]
                    .some(image => image.complete && image.naturalWidth > 0)"""
            )
            page.get_by_role("button", name="Inspect map center").click()
            expect(page.locator("#inspection")).to_contain_text("Display RGBA:")
            page.screenshot(path=str(out / "native-layer.png"))
            reports.append(
                {
                    "profile": "native-layers",
                    "checks": [
                        "native-layer-public-discovery-render-display-inspection"
                    ],
                }
            )
        finally:
            context.close()
            if client is not None:
                client.terminate()
                client.wait(timeout=10)
            backend.terminate()
            backend.wait(timeout=10)
            log.close()
        report = {
            "passed": True,
            "browser": browser.version,
            "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
            "fixtures": manifest,
            "profiles": reports,
            "exclusions": [
                "External provider basemaps, tokens and WAN",
                "Raw source-value point queries (not a current server contract)",
                "True temporal acquisition correctness; labels reuse a pinned raster",
            ],
        }
        (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(
            json.dumps(
                {"passed": True, "checks": sum(len(r["checks"]) for r in reports)}
            )
        )
        browser.close()


if __name__ == "__main__":
    main()
