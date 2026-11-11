"""Measure fixed-schedule map sessions, viewport coverage and interrupted navigation."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import math
import time
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from PIL import Image
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SPEC = {
    "actions": 12,
    "interval_ms": 2200,
    "episodes": 2,
    "gestures": 12,
    "gesture_ms": 300,
    "episode_ms": 12000,
    "styles": 6,
    "style_interval_ms": 2200,
}
SCENES = {
    "sentinel": {"center": [45.55, 12.65], "zoom": 14, "range": [0, 3000]},
    "dem": {"center": [46.5, 12.5], "zoom": 13, "range": [0, 4000]},
    "worldcover": {"center": [46.5, 13.5], "zoom": 13, "range": [0, 100]},
}


def quantile(values, q):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * q) - 1)] if ordered else None


def classify_requests(requests, sessions):
    measured = []
    for row in requests:
        session = sessions[row["user"]]
        origin = session["time_origin_epoch_ms"]
        elapsed = row["started_epoch_ms"] - origin
        row["phase"] = (
            "measured"
            if session["started_ms"] <= elapsed <= session["ended_ms"]
            else "warmup"
        )
        if row["phase"] != "measured":
            continue
        preceding = [a for a in session["actions"] if a["started_ms"] <= elapsed]
        row["active_input"] = preceding[-1]["id"] if preceding else None
        row["active_protocol"] = preceding[-1]["protocol"] if preceding else None
        if row.get("canceled"):
            failure_elapsed = elapsed + 1000 * (
                row["failed_monotonic_s"] - row["started_monotonic_s"]
            )
            row["canceled_after_session"] = failure_elapsed > session["ended_ms"]
        measured.append(row)
    return measured


def summarize(sessions):
    result = {}
    for protocol in ("paced", "rapid", "style"):
        actions = [
            a
            for session in sessions
            for a in session["actions"]
            if a["protocol"] == protocol and (protocol != "rapid" or a["final_gesture"])
        ]
        complete = [
            a["completion_ms"]
            for a in actions
            if a["completion_ms"] is not None and a["completion_ms"] <= a["deadline_ms"]
        ]
        ranked = complete + [math.inf] * (len(actions) - len(complete))
        p95 = quantile(ranked, 0.95)
        duration = deficit = under90 = 0.0
        for session in sessions:
            lookup = {a["id"]: a for a in session["actions"]}
            for before, after in zip(session["samples"], session["samples"][1:]):
                action = lookup.get(before["action"])
                if action is None or action["protocol"] != protocol:
                    continue
                seconds = (after["at_ms"] - before["at_ms"]) / 1000
                duration += seconds
                deficit += seconds * (1 - before["coverage"])
                under90 += seconds * (before["coverage"] < 0.9)
        result[protocol] = {
            "targets": len(actions),
            "completed_before_deadline": len(complete),
            "completion_fraction": len(complete) / len(actions) if actions else None,
            "p50_completed_ms": quantile(complete, 0.5),
            "p95_ms": p95 if p95 is not None and math.isfinite(p95) else None,
            "p95_censored": bool(p95 is not None and not math.isfinite(p95)),
            "deadline_ms": sorted({a["deadline_ms"] for a in actions}),
            "within_target_fraction": {
                str(limit): sum(v <= limit for v in complete) / len(actions)
                if actions
                else None
                for limit in (500, 1000, 1500, 2000)
            },
            "observed_seconds": duration,
            "uncovered_viewport_seconds": deficit,
            "mean_uncovered_fraction": deficit / duration if duration else None,
            "below_90_percent_coverage_fraction": under90 / duration
            if duration
            else None,
        }
    return result


def tile_url(origin, mode, scene="sentinel", style=None):
    lo, hi = SCENES[scene]["range"]
    if style is not None:
        hi *= (0.6, 0.85, 1.15)[style % 3]
    query = {}
    if mode == "singleband":
        band = (
            "green"
            if scene == "sentinel" and style is not None and style % 3 == 1
            else "red"
        )
        prefix = f"/singleband/{scene}/{band}"
        if scene == "worldcover":
            offset = 0 if style is None else 20 * style
            query["colormap"] = "explicit"
            query["explicit_color_map"] = json.dumps(
                {
                    v: [(v * 2 + offset) % 256, v * 3 % 256, v * 5 % 256, 255]
                    for v in (10, 20, 30, 40, 50, 60, 70, 80, 90, 95, 100)
                }
            )
        else:
            query["stretch_range"] = json.dumps([lo, hi])
    else:
        prefix = f"/rgb/{scene}"
        query.update(
            r="blue" if style is not None and style % 2 else "red",
            g="green",
            b="red" if style is not None and style % 2 else "blue",
        )
        query.update({c + "_range": json.dumps([lo, hi]) for c in ("r", "g", "b")})
    return origin + prefix + "/{z}/{x}/{y}.png?" + urlencode(query)


class Client:
    def __init__(self, page, cdp, origin, user):
        self.page, self.cdp, self.user = page, cdp, user
        self.origin = urlsplit(origin).netloc
        self.requests = {}
        self.errors = []
        self.capture_unavailable = []
        page.on("pageerror", lambda exc: self.errors.append(str(exc)))
        cdp.on("Network.requestWillBeSent", self.request)
        cdp.on("Network.responseReceived", self.response)
        cdp.on("Network.loadingFinished", self.finished)
        cdp.on("Network.loadingFailed", self.failed)
        cdp.on("Network.requestServedFromCache", self.cached)

    def request(self, event):
        url = event["request"]["url"]
        if urlsplit(url).netloc != self.origin or event.get("type") != "Image":
            return
        self.requests[event["requestId"]] = {
            "cdp_id": event["requestId"],
            "user": self.user,
            "path": urlsplit(url).path + "?" + urlsplit(url).query,
            "started_epoch_ms": event["wallTime"] * 1000,
            "started_monotonic_s": event["timestamp"],
        }

    def response(self, event):
        row = self.requests.get(event["requestId"])
        if row is None:
            return
        response = event["response"]
        headers = {k.lower(): v for k, v in response["headers"].items()}
        row.update(
            status=response["status"],
            request_id=headers.get("x-benchmark-request-id"),
            protocol=response["protocol"],
            browser_cache=bool(
                row.get("browser_cache")
                or response.get("fromDiskCache")
                or response.get("fromPrefetchCache")
            ),
            wire_timing=response.get("timing"),
            connection_id=response.get("connectionId"),
        )

    def cached(self, event):
        row = self.requests.get(event["requestId"])
        if row is not None:
            row["browser_cache"] = True

    def finished(self, event):
        row = self.requests.get(event["requestId"])
        if row is not None:
            row.update(
                finished_monotonic_s=event["timestamp"],
                wire_bytes=event["encodedDataLength"],
            )

    def failed(self, event):
        row = self.requests.get(event["requestId"])
        if row is not None:
            row.update(
                failed_monotonic_s=event["timestamp"],
                canceled=event.get("canceled", False),
                network_error=event["errorText"],
            )

    async def images(self, folder):
        selected = {}
        for row in self.requests.values():
            if row.get("status") == 200 and "finished_monotonic_s" in row:
                selected[row["path"]] = row
        output = []
        candidates = list(selected.items())
        if len(candidates) > 64:
            candidates = [
                candidates[i * (len(candidates) - 1) // 63] for i in range(64)
            ]
        for path, row in candidates:
            filename = hashlib.sha256(path.encode()).hexdigest() + ".png"
            target = folder / filename
            if target.exists():
                continue
            try:
                result = await self.cdp.send(
                    "Network.getResponseBody", {"requestId": row["cdp_id"]}
                )
            except PlaywrightError as exc:
                if "evicted from inspector cache" not in str(exc):
                    raise
                self.capture_unavailable.append(
                    {
                        "user": self.user,
                        "path": path,
                        "reason": "inspector_cache_eviction",
                    }
                )
                continue
            body = (
                base64.b64decode(result["body"])
                if result["base64Encoded"]
                else result["body"].encode()
            )
            image = Image.open(io.BytesIO(body)).convert("RGBA")
            if image.size != (256, 256):
                raise ValueError("unexpected tile dimensions")
            target.write_bytes(body)
            output.append(
                {
                    "path": path,
                    "filename": filename,
                    "rgba_sha256": hashlib.sha256(image.tobytes()).hexdigest(),
                }
            )
        return output


async def run_case(
    origin,
    local,
    users,
    mode,
    out,
    *,
    spec=None,
    scene="sentinel",
    arrangement="overlapping",
):
    specification = {**DEFAULT_SPEC, **(spec or {})}
    contexts, clients, sessions = [], [], []
    images = out / "images"
    images.mkdir(parents=True, exist_ok=True)
    started = time.time()
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-background-timer-throttling",
                    "--disable-renderer-backgrounding",
                    "--disable-backgrounding-occluded-windows",
                ],
            )
            version = browser.version
            try:
                for user in range(users):
                    context = await browser.new_context(
                        viewport={"width": 1600, "height": 1000},
                        device_scale_factor=1,
                        ignore_https_errors=origin.startswith("https://"),
                    )
                    contexts.append(context)
                    page = await context.new_page()
                    cdp = await context.new_cdp_session(page)
                    await cdp.send(
                        "Network.enable",
                        {
                            "maxTotalBufferSize": 64 * 1024**2,
                            "maxResourceBufferSize": 2 * 1024**2,
                        },
                    )
                    clients.append(Client(page, cdp, origin, user))
                    await page.goto(local, wait_until="load")
                warmup = await asyncio.gather(
                    *[
                        c.page.evaluate(
                            "opts => setup(opts)",
                            {
                                "url": tile_url(origin, mode, scene),
                                "center": SCENES[scene]["center"],
                                "zoom": SCENES[scene]["zoom"],
                                "user": c.user,
                                "users": users,
                                "arrangement": arrangement,
                            },
                        )
                        for c in clients
                    ]
                )
                # All inputs use one wall-clock schedule, independent of response completion.
                beginning = int(time.time() * 1000) + 1500
                sessions = await asyncio.gather(
                    *[
                        c.page.evaluate(
                            "spec => runSession(spec)",
                            {
                                **specification,
                                "start_epoch_ms": beginning,
                                "user_phase_ms": c.user
                                * specification["interval_ms"]
                                / users,
                                "style_urls": [
                                    tile_url(origin, mode, scene, i)
                                    for i in range(specification["styles"])
                                ],
                            },
                        )
                        for c in clients
                    ]
                )
                measured_end = time.time()
                captured = []
                for client in clients:
                    captured.extend(await client.images(images))
                await clients[0].page.screenshot(path=str(out / "final-view.png"))
                requests = [r for c in clients for r in c.requests.values()]
                javascript_errors = [e for c in clients for e in c.errors]
                return {
                    "browser_version": version,
                    "users": users,
                    "mode": mode,
                    "scene": scene,
                    "arrangement": arrangement,
                    "specification": specification,
                    "warmup": warmup,
                    "sessions": sessions,
                    "summary": summarize(sessions),
                    "requests": requests,
                    "images": captured,
                    "image_capture_unavailable": [
                        row for client in clients for row in client.capture_unavailable
                    ],
                    "javascript_errors": javascript_errors,
                    "started_epoch_s": started,
                    "measured_end_epoch_s": measured_end,
                    "ended_epoch_s": time.time(),
                }
            finally:
                await browser.close()
    finally:
        # The caller retains the case even when setup or instrumentation fails.
        (out / "partial-sessions.json").write_text(json.dumps(sessions) + "\n")
        (out / "partial-requests.json").write_text(
            json.dumps([r for c in clients for r in c.requests.values()]) + "\n"
        )
