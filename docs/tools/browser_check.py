"""Verify documentation navigation, search, mobile layout, and tutorial images."""

import argparse
import json
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--html", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)

    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, format, *values):
            pass

    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(Quiet, directory=str(args.html.resolve()))
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    checks = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 1360, "height": 950})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(origin, wait_until="networkidle")
            page.locator('article a[href="tutorials/first-map.html"]').first.click()
            page.wait_for_load_state("networkidle")
            assert page.locator("h1").inner_text().startswith("Your first raster map")
            assert page.locator("article img").evaluate_all(
                "imgs => imgs.every(i => i.complete && i.naturalWidth > 0 && i.alt.length > 0)"
            )
            page.screenshot(
                path=str(args.out / "first-map-desktop.png"), full_page=True
            )
            checks.append("first_map_navigation_and_image")
            page.goto(origin + "/index.html")
            page.locator(
                'article a[href="how-to/upgrade-terracotta.html"]'
            ).first.click()
            assert page.locator("h1").inner_text().startswith("Upgrade from Terracotta")
            checks.append("migration_entry")
            page.goto(origin + "/search.html?q=Terracotta", wait_until="networkidle")
            page.wait_for_function(
                "document.querySelectorAll('#search-results a').length > 0"
            )
            assert "Terracotta" in page.locator("#search-results").inner_text()
            checks.append("search")
            for route in (
                "index.html",
                "tutorials/first-map.html",
                "how-to/upgrade-terracotta.html",
                "explanation/architecture.html",
                "reference/configuration.html",
            ):
                page.set_viewport_size({"width": 390, "height": 844})
                page.goto(origin + "/" + route, wait_until="networkidle")
                assert page.locator("h1").is_visible()
                assert page.evaluate(
                    "document.documentElement.scrollWidth <= window.innerWidth + 1"
                ), route
                assert page.locator("article img").evaluate_all(
                    "imgs => imgs.every(i => i.complete && i.naturalWidth > 0 && i.alt.length > 0)"
                )
            page.goto(
                origin + "/how-to/upgrade-terracotta.html", wait_until="networkidle"
            )
            page.screenshot(path=str(args.out / "migration-mobile.png"), full_page=True)
            checks.append("mobile_no_horizontal_page_overflow")
            page.set_viewport_size({"width": 1360, "height": 950})
            page.goto(
                origin + "/explanation/architecture.html", wait_until="networkidle"
            )
            page.screenshot(
                path=str(args.out / "architecture-desktop.png"), full_page=True
            )
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)
    report = {"passed": True, "checks": checks, "javascript_errors": errors}
    (args.out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(report)


if __name__ == "__main__":
    main()
