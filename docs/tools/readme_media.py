"""Capture the README walkthrough and feature demos from real RCFP responses."""

from __future__ import annotations

import argparse
import io
import json
import os
import runpy
import shutil
import sys
from pathlib import Path
from urllib.parse import urlencode

from PIL import Image
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
from browser_contracts import ports, ready
from viewer_helpers import select_layer, settled, view_link

WIDTH = 960


def save_gif(path, frames, durations):
    samples = Image.new("RGB", (160, 92 * len(frames)))
    for index, frame in enumerate(frames):
        samples.paste(frame.resize((160, 92)), (0, index * 92))
    palette = samples.quantize(colors=192)
    images = [
        frame.quantize(palette=palette, dither=Image.Dither.NONE) for frame in frames
    ]
    images[0].save(
        path,
        save_all=True,
        append_images=images[1:],
        duration=durations,
        loop=0,
        optimize=True,
        disposal=1,
    )


def walkthrough(page, viewer, assets, work):
    page.set_viewport_size({"width": 1200, "height": 690})
    page.goto(viewer)
    select_layer(page, '["lagoon","red"]')
    page.get_by_label("Search layers").fill("")
    page.get_by_label("Display mode").select_option("rgb")
    settled(page)
    render = page.evaluate(
        """() => ({
          mode: "rgb", keys: ["lagoon"],
          params: Object.fromEntries(["r", "g", "b"].flatMap(channel => [
            [channel, document.getElementById(channel).value],
            [channel + "_range", ["min", "max"].map(end =>
                Number(document.getElementById(channel + "-" + end).value))],
          ])),
        })"""
    )
    page.goto(view_link(page, render, center=[12.365, 45.440], zoom=11.75))
    settled(page)
    page.wait_for_timeout(260)
    page.evaluate(
        """() => {
          const cursor = document.createElement("div");
          cursor.style.cssText = "position:fixed;z-index:99999;width:14px;height:14px;"
            + "opacity:0;border-radius:50%;background:#fff9;box-shadow:0 0 0 1px #18262b;"
            + "pointer-events:none;transform:translate(-50%,-50%);";
          document.body.append(cursor);
          document.addEventListener("mousemove", ({clientX, clientY}) => {
            cursor.style.left = clientX + "px";
            cursor.style.top = clientY + "px";
            cursor.style.opacity = 1;
          });
          document.addEventListener("mousedown", () => cursor.style.background = "#f6be8e");
          document.addEventListener("mouseup", () => cursor.style.background = "#fff9");
        }"""
    )
    frames, durations = [], []

    def capture(duration=75):
        with Image.open(io.BytesIO(page.screenshot())) as image:
            frames.append(
                image.convert("RGB").resize((WIDTH, 552), Image.Resampling.LANCZOS)
            )
        durations.append(duration)

    def glide(points):
        for x, y in points:
            page.mouse.move(x, y, steps=3)
            capture()

    def wheel(x, y, deltas):
        page.mouse.move(x, y, steps=4)
        for delta in deltas:
            page.mouse.wheel(0, delta)
            page.wait_for_timeout(65)
            capture()
        page.wait_for_timeout(330)
        settled(page)
        capture(330)

    def drag(points):
        page.mouse.move(*points[0])
        page.mouse.down()
        try:
            glide(points[1:])
        finally:
            page.mouse.up()
        page.wait_for_timeout(210)
        settled(page)
        capture(280)

    capture(550)
    frames[-1].save(assets / "navigation.png")
    glide([(548, 378), (594, 366), (636, 344), (662, 321), (673, 313)])
    wheel(673, 313, (-16, -28, -31, -15))
    drag([(737, 355), (729, 357), (709, 369), (672, 385), (645, 393), (630, 395)])
    wheel(728, 312, (-12, -24, -15))
    drag([(884, 399), (874, 406), (850, 427), (813, 469), (783, 487), (767, 492)])
    wheel(754, 358, (15, 30, 23))
    drag([(727, 420), (740, 424), (761, 428), (794, 433), (809, 437)])

    page.get_by_label("Display mode").select_option("compute")
    page.get_by_role("button", name="Insert NDVI").click()
    capture(420)
    page.get_by_role("button", name="Apply expression").click()
    page.get_by_role("tab", name="Style").click()
    page.get_by_label("Color palette").select_option("viridis")
    settled(page)
    capture(540)
    wheel(729, 340, (-12, -24, -16))
    drag([(790, 336), (800, 341), (823, 356), (858, 377), (875, 380)])
    capture(950)

    save_gif(assets / "navigation.gif", frames, durations)
    for index in range(0, len(frames), 5):
        frames[index].save(work / f"frame-{index:02}.png")


def feature_screenshots(page, service, assets, examples):
    for name in ("ndvi", "stac", "mosaic", "model"):
        shutil.copyfile(examples / f"{name}.png", assets / f"{name}.png")

    page.set_viewport_size({"width": 880, "height": 570})
    page.goto(service + "/explore/examples/leaflet.html")
    page.get_by_label("Dataset").select_option(label="lagoon / red")
    expect(page.locator(".leaflet-tile-loaded").first).to_be_visible()
    page.screenshot(path=assets / "leaflet.png")

    publication = service + "/published.html"
    page.route(
        publication + "*",
        lambda route: route.fulfill(path=ROOT / "docs/examples/rendered-cache.html"),
    )
    page.set_viewport_size({"width": 880, "height": 440})
    page.goto(
        publication
        + "?"
        + urlencode({"url": service + "/singleband/lagoon/red/preview.png"})
    )
    page.get_by_role("button", name="Load snapshot").click()
    expect(page.get_by_role("status")).to_contain_text("?revision=")
    page.wait_for_function("document.querySelector('#image').naturalWidth > 0")
    page.screenshot(path=assets / "publication.png")

    page.goto(service + "/__terracotta/metrics")
    panel = page.locator("pre")
    panel.evaluate(
        """pre => {
          const {workers, queued, queue_capacity, render_cache} = JSON.parse(pre.textContent);
          pre.textContent = JSON.stringify(
            {workers, queued, queue_capacity, render_cache},
            null, 2
          );
          pre.style.cssText = "font-size:15px;line-height:1.55;margin:24px";
        }"""
    )
    page.screenshot(path=assets / "operations.png")


def main():
    from contextlib import ExitStack
    import subprocess

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--binary", type=Path, required=True, help="Native bundle executable"
    )
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument(
        "--features", type=Path, required=True, help="Feature screenshot directory"
    )
    parser.add_argument(
        "--sources", type=Path, help="Use previously downloaded demo rasters"
    )
    args = parser.parse_args()
    binary = args.binary.resolve()
    work = args.work.resolve()
    work.mkdir(parents=True, exist_ok=False)
    args.assets.mkdir(parents=True, exist_ok=True)
    args.features.mkdir(parents=True, exist_ok=True)
    prepare = runpy.run_path(str(ROOT / "packaging/demo/prepare.py"))["prepare"]
    manifest = prepare(binary, work / "public-demo", sources=args.sources)
    examples = work / "features"
    subprocess.run(
        [sys.executable, str(ROOT / "tests/native_demos.py"), "--out", str(examples)],
        check=True,
    )

    services = [f"http://127.0.0.1:{port}" for port in ports()]
    catalogs = [
        work / "public-demo/catalog.sqlite",
        examples / "demo-data/map.sqlite",
    ]
    responses, errors, external = [], [], []
    with ExitStack() as cleanup, (work / "services.log").open("w") as log:
        for service, catalog, queue in zip(
            services, catalogs, ("64", "8"), strict=True
        ):
            backend = subprocess.Popen(
                [
                    binary,
                    "serve",
                    "-d",
                    catalog,
                    "--bind",
                    service.removeprefix("http://"),
                ],
                env={
                    **os.environ,
                    "TC_RASTER_WORKERS": "2",
                    "TC_RASTER_QUEUE_CAPACITY": queue,
                    "TC_RENDER_CACHE_BYTES": "4194304",
                    "TC_EXPOSE_METRICS": "true",
                },
                stdout=log,
                stderr=log,
            )
            cleanup.callback(backend.wait, timeout=10)
            cleanup.callback(backend.terminate)
            ready(service + "/keys", backend)

        viewer = subprocess.check_output(
            [binary, "connect", services[0], "--local", "--no-browser"],
            text=True,
        ).strip()
        assert viewer == services[0] + "/explore/"
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
            context = browser.new_context()

            def local_only(route):
                if route.request.url.startswith(tuple(s + "/" for s in services)):
                    route.continue_()
                else:
                    external.append(route.request.url)
                    route.abort()

            context.route("**/*", local_only)
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on(
                "response",
                lambda response: responses.append((response.url, response.status)),
            )
            walkthrough(page, viewer, args.assets, work)
            feature_screenshots(page, services[1], args.features, examples)
            browser.close()

    assert not errors, errors
    assert not external, external
    assert all(status < 400 for _, status in responses), responses
    assert any("/rgb/" in url for url, _ in responses)
    assert any("/compute/" in url for url, _ in responses)
    (work / "report.json").write_text(
        json.dumps({"sources": manifest, "responses": len(responses)}, indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
