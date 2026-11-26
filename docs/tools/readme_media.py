"""Capture README demos from the installed samples and bundled browser viewer."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import socket
import subprocess
import time
from pathlib import Path
from urllib.request import urlopen

from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import expect, sync_playwright

WIDTH = 960
BACKGROUND = "#12212b"
FOREGROUND = "#eef3f5"
MUTED = "#a6bbc8"
ACCENT = "#efaa7c"


def font(folder, size):
    return ImageFont.truetype(str(folder / "DejaVuSans.ttf"), size)


def port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def ready(url, process):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            urlopen(url, timeout=1).close()
            return
        except OSError:
            if process.poll() is not None:
                raise RuntimeError("Demo service exited before becoming ready")
            time.sleep(0.1)
    raise TimeoutError(url)


def save_gif(path, frames, durations):
    samples = Image.new("RGB", (160, 96 * len(frames)))
    for index, frame in enumerate(frames):
        samples.paste(frame.resize((160, 96)), (0, index * 96))
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument(
        "--fonts",
        type=Path,
        default=Path("/usr/share/fonts/truetype/dejavu"),
    )
    args = parser.parse_args()
    binary = args.binary.resolve()
    work = args.work.resolve()
    work.mkdir(parents=True, exist_ok=False)
    args.assets.mkdir(parents=True, exist_ok=True)
    title_font = font(args.fonts, 22)
    small_font = font(args.fonts, 15)
    transcript = []

    def run(command, display):
        result = subprocess.run(
            command, cwd=work, capture_output=True, text=True, check=True
        )
        transcript.append(
            {"command": display, "stdout": result.stdout, "stderr": result.stderr}
        )
        return result.stdout

    run([binary, "demo", "-o", "demo-data"], "rcfp demo -o demo-data")
    manifest = json.loads((work / "demo-data/data/manifest.json").read_text())
    for row in manifest["fixtures"]:
        actual = hashlib.sha256(
            (work / "demo-data/data" / row["file"]).read_bytes()
        ).hexdigest()
        assert actual == row["sha256"], row["file"]
    backend_port = port()
    service = f"http://127.0.0.1:{backend_port}"
    viewer = service + "/explore/"
    frames, durations, stages, responses, errors, external = [], [], [], [], [], []
    processes = []
    with (work / "services.log").open("w") as log:
        try:
            environment = {
                **os.environ,
                "TC_RASTER_QUEUE_CAPACITY": "64",
            }
            backend = subprocess.Popen(
                [
                    binary,
                    "serve",
                    "-d",
                    "demo-data/map.sqlite",
                    "--bind",
                    f"127.0.0.1:{backend_port}",
                ],
                cwd=work,
                env=environment,
                stdout=log,
                stderr=log,
            )
            processes.append(backend)
            ready(service + "/keys", backend)
            assert (
                run(
                    [binary, "connect", service, "--no-browser"],
                    "rcfp connect localhost:5000",
                ).strip()
                == viewer
            )
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    headless=True, args=["--no-sandbox"]
                )
                context = browser.new_context(viewport={"width": 1376, "height": 790})

                def local_only(route):
                    if route.request.url.startswith(
                        (service + "/", viewer + "/", "data:", "blob:")
                    ):
                        route.continue_()
                    else:
                        external.append(route.request.url)
                        route.abort()

                context.route("**/*", local_only)
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on(
                    "response",
                    lambda response: responses.append(
                        {"url": response.url, "status": response.status}
                    ),
                )
                page.goto(viewer)
                page.get_by_role("button", name="lagoon / red", exact=True).click()
                expect(page.locator("#status")).to_contain_text("lagoon / red")
                page.get_by_label("Display mode", exact=True).select_option("rgb")
                for label, band in (
                    ("Red", "red"),
                    ("Green", "green"),
                    ("Blue", "blue"),
                ):
                    page.get_by_label(label, exact=True).select_option(band)
                page.get_by_role("tab", name="Style", exact=True).click()
                page.wait_for_load_state("networkidle")
                expect(page.locator(".leaflet-tile-loaded").first).to_be_visible()
                page.get_by_role("button", name="Zoom in", exact=True).click()
                page.wait_for_load_state("networkidle")

                def capture(label, duration=140):
                    screenshot = Image.open(io.BytesIO(page.screenshot())).convert(
                        "RGB"
                    )
                    screenshot = screenshot.resize(
                        (WIDTH, 546), Image.Resampling.LANCZOS
                    )
                    image = Image.new("RGB", (WIDTH, 640), BACKGROUND)
                    image.paste(screenshot, (0, 54))
                    draw = ImageDraw.Draw(image)
                    draw.text(
                        (20, 14),
                        "Venice lagoon / Sentinel-2",
                        font=title_font,
                        fill=FOREGROUND,
                    )
                    draw.text(
                        (WIDTH - 20, 18),
                        label,
                        font=small_font,
                        fill=ACCENT,
                        anchor="ra",
                    )
                    draw.text(
                        (20, 613),
                        "Contains modified Copernicus Sentinel data 2021",
                        font=small_font,
                        fill=MUTED,
                    )
                    draw.text(
                        (WIDTH - 20, 613),
                        "RCFP / local imagery / offline viewer",
                        font=small_font,
                        fill=MUTED,
                        anchor="ra",
                    )
                    frames.append(image)
                    durations.append(duration)

                def settled(label):
                    expect(page.locator("#status")).to_contain_text("lagoon / red")
                    page.wait_for_load_state("networkidle")
                    page.wait_for_timeout(350)
                    assert "failed" not in page.locator("#status").inner_text().lower()
                    capture(label, 1400)
                    stages.append(label)

                settled("01 / RGB overview")
                frames[-1].save(args.assets / "navigation.png")
                page.get_by_role("button", name="Zoom in", exact=True).click()
                for _ in range(5):
                    capture("02 / Zoom in")
                settled("02 / Zoom in")
                box = page.locator("#map").bounding_box()
                x, y = box["x"] + box["width"] * 0.55, box["y"] + box["height"] * 0.5
                page.mouse.move(x, y)
                page.mouse.down()
                for step in range(1, 9):
                    page.mouse.move(x + step * 22, y + step * 7)
                    capture("03 / Pan across the lagoon")
                page.mouse.up()
                settled("03 / Pan across the lagoon")
                page.get_by_role("tab", name="Layers", exact=False).click()
                page.get_by_label("Display mode", exact=True).select_option("compute")
                page.get_by_role("button", name="Insert NDVI").click()
                page.get_by_role("button", name="Apply expression").click()
                expect(page.locator("#header-layer")).to_contain_text("Expression")
                page.get_by_role("tab", name="Style", exact=True).click()
                page.get_by_label("Color palette", exact=True).select_option("viridis")
                settled("04 / NDVI / (nir − red) / (nir + red)")
                page.get_by_role("button", name="Zoom out", exact=True).click()
                for _ in range(5):
                    capture("05 / Zoom out")
                settled("05 / NDVI overview")
                page.screenshot(path=str(work / "viewer.png"), full_page=True)
                browser.close()
        finally:
            for process in reversed(processes):
                process.terminate()
                process.wait(timeout=10)

    assert not errors, errors
    assert not external, external
    assert all(row["status"] < 400 for row in responses), responses
    assert any("/rgb/" in row["url"] for row in responses)
    assert any("/compute/" in row["url"] for row in responses)
    save_gif(args.assets / "navigation.gif", frames, durations)

    (work / "transcript.json").write_text(json.dumps(transcript, indent=2) + "\n")
    report = {
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "sources": manifest["fixtures"],
        "stages": stages,
        "http_responses": len(responses),
        "http_errors": [],
        "javascript_errors": errors,
        "external_requests": external,
        "playback": "Edited screenshot sequence; playback does not measure serving performance.",
        "assets": {
            path.name: {
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path in sorted(args.assets.glob("*"))
            if path.suffix in (".gif", ".png")
        },
    }
    (work / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {"stages": stages, "responses": len(responses), "assets": report["assets"]},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
