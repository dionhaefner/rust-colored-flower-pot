"""Exercise the public SDK, packaged viewer and independent maps in real Chromium."""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import os
import runpy
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import expect, sync_playwright
from viewer_helpers import select_layer, settled, share_view, style_tab

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


def hosted_terracotta(playwright, binary, database, out, reference_python):
    site = out / "static-site"
    explorer = site / "project/explore"
    runpy.run_path(str(ROOT / "packaging/demo/export-viewer.py"))["export"](explorer)
    handler = functools.partial(SimpleHTTPRequestHandler, directory=site)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    upstream_port, _ = ports()
    origin = f"http://viewer.test:{server.server_port}"
    viewer = origin + "/project/explore/"
    api_origin = f"http://upstream.test:{upstream_port}"
    service = api_origin + "/imagery"
    with (out / "upstream-terracotta.log").open("w") as log:
        backend = subprocess.Popen(
            [
                str(reference_python.absolute()),
                "-c",
                (
                    "from werkzeug.middleware.dispatcher import DispatcherMiddleware; "
                    "from werkzeug.serving import run_simple; "
                    "from werkzeug.exceptions import NotFound; "
                    "import terracotta; from terracotta.server.flask_api import create_app; "
                    "assert terracotta.__version__ == '0.10.1'; "
                    "app = DispatcherMiddleware(NotFound(), {'/imagery': create_app()}); "
                    f"run_simple('127.0.0.1', {upstream_port}, app)"
                ),
            ],
            env={
                **os.environ,
                "TC_DRIVER_PATH": str(database),
                "TC_ALLOWED_ORIGINS_METADATA": json.dumps([origin]),
                "TC_ALLOWED_ORIGINS_TILES": json.dumps([origin]),
            },
            stdout=log,
            stderr=log,
        )
        try:
            ready(f"http://127.0.0.1:{upstream_port}/imagery/keys", backend)
            launch = subprocess.run(
                [str(binary), "connect", service, "--no-browser"],
                env={**os.environ, "RCFP_VIEWER_URL": viewer},
                text=True,
                capture_output=True,
                check=True,
                timeout=10,
            ).stdout.strip()
            assert launch == viewer + "?" + urlencode({"service": service})
            browser = playwright.chromium.launch(
                channel="chromium",
                headless=True,
                args=[
                    "--no-sandbox",
                    "--no-proxy-server",
                    "--host-resolver-rules=MAP *.test 127.0.0.1",
                    f"--unsafely-treat-insecure-origin-as-secure={origin},{api_origin}",
                ],
            )
            try:
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                failures = []
                page.on("pageerror", lambda error: failures.append(str(error)))
                page.goto(viewer)
                expect(
                    page.get_by_role("dialog", name="Open a tile server")
                ).to_be_visible()
                page.get_by_label("Tile server URL").fill(
                    api_origin + "/imagery?token=private"
                )
                page.get_by_role("button", name="Connect", exact=True).click()
                expect(page.locator("#server-error")).to_contain_text(
                    "without credentials"
                )
                page.get_by_label("Tile server URL").fill(service)
                page.get_by_role("button", name="Connect", exact=True).click()
                expect(page).to_have_url(launch)
                select_layer(page, selected())
                settled(page)
                assert (
                    page.locator("#api-docs").get_attribute("href")
                    == service + "/apidoc"
                )
                with page.expect_response(
                    lambda response: (
                        "/imagery/rgb/" in response.url and response.status == 200
                    )
                ):
                    page.get_by_label("Display mode", exact=True).select_option("rgb")
                settled(page)
                share_view(page)
                link = page.locator("#share-link").input_value()
                page.goto(link)
                settled(page)
                assert page.locator("#mode").input_value() == "rgb"
                example = page.locator('nav a[href*="leaflet.html"]').get_attribute(
                    "href"
                )
                page.goto(example)
                expect(page.locator("#status")).to_contain_text("lagoon")
                expect(page.locator(".leaflet-tile-loaded").first).to_be_visible()
                assert not failures, failures
            finally:
                browser.close()
        finally:
            backend.terminate()
            backend.wait(timeout=10)
            server.shutdown()
            server.server_close()
            thread.join(timeout=10)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--reference-python", type=Path)
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
    # Repeated observations retain pinned pixels while exercising catalog navigation.
    for scene in ("lagoon", ENCODED_SCENE):
        for date in (
            "2024-01-01",
            "2024-01-02",
            "2024-01-03",
            "2024-01-04",
            "2024-01-05",
        ):
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
    addresses = {profile: ports() for profile in ("native", "terracotta")}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            channel="chromium",
            headless=True,
            args=[
                "--no-sandbox",
                "--use-angle=swiftshader",
                "--enable-unsafe-swiftshader",
                "--no-proxy-server",
                "--host-resolver-rules=MAP api.test 127.0.0.1",
                "--unsafely-treat-insecure-origin-as-secure="
                + ",".join(
                    f"http://api.test:{port}"
                    for pair in addresses.values()
                    for port in pair
                ),
            ],
        )
        for profile in ("native", "terracotta"):
            checks, responses, errors, external, expected_failures = [], [], [], [], []
            backend_port, client_port = addresses[profile]
            listen_url = f"http://127.0.0.1:{backend_port}"
            backend_url = f"http://api.test:{backend_port}"
            client_url = backend_url + "/explore"
            legacy_url = f"http://127.0.0.1:{client_port}"
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
                    "TC_ALLOWED_ORIGINS_METADATA": json.dumps([legacy_url + "$"]),
                    "TC_ALLOWED_ORIGINS_TILES": json.dumps([legacy_url + "$"]),
                },
            )
            client = None
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            page = None
            try:
                ready(listen_url + "/keys", backend)
                launch = subprocess.run(
                    [
                        str(binary),
                        "connect",
                        listen_url,
                        "--no-browser",
                        "--source-revision",
                        REVISION,
                    ],
                    check=True,
                    text=True,
                    capture_output=True,
                    timeout=10,
                ).stdout.strip()
                assert launch == f"{listen_url}/explore/?source-revision={REVISION}"
                local = context.new_page()
                local.goto(launch)
                settled(local)
                expect(local.get_by_role("button", name="Share view")).to_be_hidden()
                local.close()
                checks.append("one-server-local-launch-and-hidden-sharing")

                client = subprocess.Popen(
                    [
                        str(binary),
                        "connect",
                        listen_url,
                        "--port",
                        str(client_port),
                        "--source-revision",
                        REVISION,
                        "--no-browser",
                    ],
                    stdout=log,
                    stderr=log,
                )
                ready(legacy_url, client)
                assets_url = listen_url + "/explore"
                for filename in (
                    "index.js",
                    "index.d.ts",
                    "integrations.js",
                    "integrations.d.ts",
                    "package.json",
                    "LICENSE",
                ):
                    with urllib.request.urlopen(
                        assets_url + "/sdk/" + filename
                    ) as response:
                        assert (
                            response.read() == (ROOT / "js/sdk" / filename).read_bytes()
                        ), filename
                for filename in (
                    "viewer.js",
                    "viewer.css",
                    "viewer-connection.js",
                    "natural-earth-land.geojson",
                ):
                    with urllib.request.urlopen(
                        assets_url + "/" + filename
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
                page.goto(f"{client_url}/?source-revision={REVISION}")
                select_layer(page, selected())
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
                expect(page.get_by_label("Basemap")).to_have_value("land")
                expect(page.locator(".leaflet-land-pane path").first).to_be_visible()
                expect(page.locator(".leaflet-control-attribution")).to_contain_text(
                    "Natural Earth"
                )
                page.route(
                    "https://tile.openstreetmap.org/**",
                    lambda route: route.fulfill(
                        path=ROOT / "tests/fixtures/demo/preview.png"
                    ),
                )
                page.get_by_label("Basemap").select_option("streets")
                expect(
                    page.locator(".leaflet-streets-pane .leaflet-tile-loaded").first
                ).to_be_visible()
                expect(page.locator(".leaflet-control-attribution")).to_contain_text(
                    "OpenStreetMap"
                )
                page.get_by_label("Basemap").select_option("none")
                expect(page.locator(".leaflet-land-pane path")).to_have_count(0)
                page.get_by_label("Basemap").select_option("land")
                checks.append("offline-default-and-basemap-picker")

                search = page.get_by_label("Search layers")
                search.fill("2024-01-02 nir")
                expect(page.locator(".layer-row")).to_have_count(2)
                search.fill("no-such-layer")
                expect(page.locator("#no-results")).to_be_visible()
                search.fill("")
                expect(page.locator(".layer-row")).to_have_count(30)
                page.get_by_role("button", name="Show more layers").click()
                expect(page.locator(".layer-row")).to_have_count(40)
                expect(
                    page.get_by_role("button", name="Show more layers")
                ).to_be_hidden()
                assert page.evaluate(
                    "document.documentElement.scrollHeight === innerHeight"
                )
                assert page.locator("#map").bounding_box()["height"] > 900
                page.get_by_role("tab", name="Layers", exact=False).focus()
                page.keyboard.press("ArrowRight")
                expect(
                    page.get_by_role("tab", name="Style", exact=True)
                ).to_be_focused()
                page.keyboard.press("End")
                expect(
                    page.get_by_role("tab", name="Details", exact=True)
                ).to_be_focused()
                checks.append("catalog-search-empty-result-and-keyboard-tabs")

                page.get_by_role("button", name="Zoom in", exact=True).click()
                page.wait_for_timeout(350)
                settled(page)
                page.get_by_role("button", name="Fit layer", exact=True).click()
                settled(page)
                inspect = page.get_by_role("button", name="Inspect", exact=True)
                expect(inspect).to_have_attribute("aria-pressed", "false")
                before = len(responses)
                page.locator("#map").click(position={"x": 850, "y": 425})
                page.locator("#map").press("Enter")
                expect(page.locator("#inspection")).to_have_count(0)
                assert not any("/point/" in row["url"] for row in responses[before:])
                inspect.click()
                expect(inspect).to_have_attribute("aria-pressed", "true")
                if profile == "native":
                    with page.expect_response(
                        lambda response: (
                            "/point/" in response.url and response.status == 200
                        )
                    ) as point:
                        page.locator("#map").press("Enter")
                    sample = point.value.json()
                    expected = (
                        page.evaluate(
                            "value => new Intl.NumberFormat(undefined, {maximumSignificantDigits:7}).format(value)",
                            sample["value"],
                        )
                        if sample["valid"]
                        else "No data"
                    )
                    expect(page.locator("#inspection .pixel-value")).to_have_text(
                        expected
                    )
                    checks.append("native-source-value-inspection")
                inspect.click()
                expect(page.locator("#inspection")).to_have_count(0)
                checks.append("inspection-toggles-and-starts-disabled")
                checks.append("zoom-and-fit-retain-live-tile-layer")

                page.get_by_role("tab", name="Layers", exact=False).click()
                page.get_by_label("Observation key", exact=True).select_option("time")
                assert (
                    page.get_by_label("Time", exact=True).locator("option").count() == 5
                )
                page.get_by_label("Time", exact=True).select_option("2024-01-02")
                expect(page.locator("#status")).to_contain_text("2024-01-02 / red")
                expect(page.locator("#header-layer")).to_contain_text(
                    "2024-01-02 / red"
                )
                checks.append("explicit-time-browsing")

                with page.expect_response(
                    lambda response: "/rgb/" in response.url and response.status == 200
                ):
                    page.get_by_label("Display mode", exact=True).select_option("rgb")
                settled(page)
                expect(page.locator("#status")).to_contain_text("2024-01-02 / red")
                page.wait_for_timeout(350)
                page.screenshot(path=str(out / f"{profile}-rgb.png"))
                for channel in ("Red", "Green", "Blue"):
                    expect(page.get_by_label(channel, exact=True)).to_be_visible()
                style_tab(page)
                expect(page.get_by_label("Red", exact=True)).to_be_hidden()
                checks.append("rgb-band-assignment-in-layers")

                green_range = page.locator("#g-min").input_value()
                view = page.locator(".leaflet-map-pane").get_attribute("style")
                with page.expect_response(
                    lambda response: (
                        "/rgb/" in response.url
                        and response.status == 200
                        and json.loads(
                            parse_qs(urlsplit(response.url).query)["r_range"][0]
                        )[0]
                        == 100
                    )
                ):
                    page.get_by_label("Red minimum", exact=True).fill("100")
                    page.get_by_label("Red minimum", exact=True).press("Tab")
                settled(page)
                assert page.locator("#g-min").input_value() == green_range
                assert page.locator(".leaflet-map-pane").get_attribute("style") == view
                page.get_by_role("button", name="Auto contrast").click()
                expect(page.locator("#r-min")).not_to_have_value("100")
                assert page.locator("#g-min").input_value() == green_range
                settled(page)
                page.get_by_label("Red minimum", exact=True).fill("100")
                page.get_by_label("Red minimum", exact=True).press("Tab")
                settled(page)
                with page.expect_response(
                    lambda response: (
                        "/rgb/" in response.url
                        and "color_transform=" in response.url
                        and response.status == 200
                    )
                ):
                    page.get_by_label("Color correction").select_option(
                        label="Brighten midtones"
                    )
                settled(page)
                before = len(responses)
                page.get_by_label("Layer opacity", exact=True).evaluate(
                    "e => {e.value=65; e.dispatchEvent(new Event('input'));}"
                )
                expect(page.locator("#opacity-value")).to_have_text("65%")
                page.wait_for_timeout(300)
                assert len(responses) == before
                page.get_by_label("Layer opacity", exact=True).evaluate(
                    "e => {e.value=100; e.dispatchEvent(new Event('input'));}"
                )
                share_view(page)
                page.reload()
                settled(page)
                assert page.locator("#r-min").input_value() == "100"
                assert page.locator("#g-min").input_value() == green_range
                assert (
                    page.locator("#color-correction").input_value() == "gamma rgb 1.35"
                )
                checks.append(
                    "independent-rgb-auto-contrast-color-correction-shared-view-and-opacity"
                )

                page.get_by_role("tab", name="Layers", exact=False).click()
                page.get_by_label("Display mode", exact=True).select_option("compute")
                expect(page.get_by_label("Expression", exact=True)).to_have_value("")
                before = len(responses)
                page.get_by_role("button", name="Insert NDVI").click()
                expect(page.get_by_label("Expression", exact=True)).not_to_have_value(
                    ""
                )
                assert not any("/compute/" in row["url"] for row in responses[before:])
                with page.expect_response(
                    lambda response, responses=responses, backend_url=backend_url: (
                        "/compute/" in response.url and response.status == 200
                    )
                ):
                    page.get_by_role("button", name="Apply expression").click()
                settled(page)
                checks.append("expression-blank-until-entered-and-renders-on-apply")
                inspect.click()
                page.locator("#map").press("Enter")
                expect(page.locator("#inspection")).to_contain_text("Display RGBA:")
                checks.append("keyboard-display-point-inspection")
                inspect.click()
                expect(page.get_by_text("Open another view link")).to_have_count(0)

                share_view(page)
                expect(page.locator("#share-link")).not_to_have_value("")
                shared_url = page.locator("#share-link").input_value()
                assert "sourceRevision" in shared_url and "renderRevision" in shared_url
                page.reload()
                expect(page.locator("#status")).to_contain_text("2024-01-02")
                assert page.locator("#mode").input_value() == "compute"
                assert page.locator("#time").input_value() == "2024-01-02"
                assert page.locator("#minimum").input_value() == "-1"
                checks.append("revisioned-state-restores-on-reload")
                share_view(page)
                expect(page.locator("#share-link")).not_to_have_value("")
                page.evaluate(
                    """async ({service,profile,revision}) => {
                    const sdk = await import(new URL('sdk/index.js', location.href));
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

                select_layer(page, selected(scene=ENCODED_SCENE))
                expect(page.locator("#status")).to_contain_text(ENCODED_SCENE)
                checks.append("encoded-path-components-live-server")

                style_tab(page)
                page.get_by_label("Stretch minimum").fill("0")
                page.get_by_label("Stretch maximum").fill("3000")
                page.get_by_label("Stretch maximum").press("Tab")
                expect(page.locator("#status")).to_contain_text(ENCODED_SCENE)
                select_layer(page, selected())
                expect(page.locator("#status")).to_contain_text(
                    "lagoon / 2024-01-01 / red"
                )

                sdk_checks = page.evaluate(
                    """async ({ service, profile, scene }) => {
                    const sdk = await import(new URL('sdk/index.js', location.href));
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
                    try { await new sdk.Client(service,{profile:profile === 'native' ? 'terracotta' : 'native'}).connect(); }
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

                denied_url = f"http://api.test:{client_port}"
                context.route(denied_url + "/**", lambda route: route.continue_())
                context.route(listen_url + "/**", lambda route: route.continue_())
                public_version = page.evaluate(
                    """async url => (await import(url + '/sdk/index.js')).VERSION""",
                    denied_url,
                )
                assert public_version == "0.1.0"
                checks.append("cross-origin-public-module-import")
                denied_metadata = urllib.request.Request(
                    listen_url + "/metadata/lagoon/2024-01-01/red",
                    headers={"Origin": backend_url},
                )
                with urllib.request.urlopen(denied_metadata, timeout=5) as response:
                    assert response.status == 200
                    assert response.headers.get("Access-Control-Allow-Origin") is None
                denied_result = page.evaluate(
                    """async ({service,profile}) => {
                    const {Client} = await import(new URL('sdk/index.js', location.href));
                    try { await new Client(service,{profile}).metadata(['lagoon','2024-01-01','red']); return false; }
                    catch(e) { return e.kind === 'network' && e.message.includes('CORS'); }
                }""",
                    {"service": listen_url, "profile": profile},
                )
                assert denied_result
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
                    const {Client} = await import(new URL('sdk/index.js', location.href));
                    const c = new Client(service,{profile}), controller = new AbortController();
                    const pending = c.metadata(['lagoon','2024-01-01','red'],{signal:controller.signal});
                    setTimeout(() => controller.abort(), 20);
                    try {await pending; return false;} catch(e) {return e.name === 'AbortError';}
                    }""",
                    {"service": backend_url, "profile": profile},
                )
                assert abort_result
                select_layer(page, selected(date="2024-01-01"))
                select_layer(page, selected(date="2024-01-02", band="blue"))
                expect(page.locator("#status")).to_contain_text("2024-01-02 / blue")
                page.wait_for_timeout(350)
                expect(page.locator("#status")).to_contain_text("2024-01-02 / blue")
                assert (
                    json.loads(page.locator("#metadata").text_content())["keys"]["band"]
                    == "blue"
                )
                page.unroute("**/metadata/lagoon/2024-01-01/red", slow_metadata)
                checks.append("real-fetch-abort-and-stale-view-suppression")

                page.get_by_role("tab", name="Layers", exact=False).click()
                page.get_by_label("Display mode", exact=True).select_option("compute")
                page.get_by_label("Expression", exact=True).fill("unknown(")
                page.get_by_role("button", name="Apply expression").click()
                expect(page.locator("#status")).to_contain_text("could not load")
                page.get_by_label("Display mode", exact=True).select_option(
                    "singleband"
                )
                expect(page.locator("#status")).to_contain_text("2024-01-02 / blue")
                expected_failures.append("expression=unknown")
                checks.append("render-error-and-recovery")

                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate(
                    "document.documentElement.scrollWidth <= innerWidth + 1"
                )
                assert page.evaluate(
                    "document.documentElement.scrollHeight <= innerHeight + 1"
                )
                page.get_by_role("tab", name="Layers", exact=False).click()
                page.get_by_label("Search layers").focus()
                assert page.get_by_label("Search layers").evaluate(
                    "e => e === document.activeElement"
                )
                page.keyboard.press("Escape")
                assert page.locator("#panel").get_attribute("data-open") == "false"
                assert page.locator("#map").bounding_box()["height"] > 700
                page.screenshot(path=str(out / f"{profile}-mobile.png"), full_page=True)
                checks.append("mobile-layout-and-keyboard-controls")
                page.set_viewport_size({"width": 1440, "height": 1000})

                for example in ("leaflet", "maplibre", "openlayers"):
                    before = len(responses)
                    page.goto(client_url + f"/examples/{example}.html")
                    select_layer(page, label="lagoon / 2024-01-01 / red")
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
            expect(page.locator("#status")).to_contain_text("lagoon / image / 1")
            page.wait_for_function(
                """() => [...document.querySelectorAll('.leaflet-tile-loaded')]
                    .some(image => image.complete && image.naturalWidth > 0)"""
            )
            page.get_by_role("button", name="Inspect", exact=True).click()
            page.locator("#map").press("Enter")
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
        if args.reference_python:
            hosted_terracotta(playwright, binary, database, out, args.reference_python)
            reports.append(
                {
                    "profile": "upstream-terracotta",
                    "checks": [
                        "static-project-path-connection-form-cli-launch-rgb-share-and-sdk-example"
                    ],
                }
            )
        report = {
            "passed": True,
            "browser": browser.version,
            "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
            "fixtures": manifest,
            "profiles": reports,
            "exclusions": [
                "External provider basemaps, tokens and WAN",
                "Source-cell point queries, covered separately by native_services.py",
                "True temporal acquisition correctness; labels reuse a pinned raster",
            ],
            "skipped": []
            if args.reference_python
            else ["Upstream Terracotta browser check requires --reference-python"],
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
