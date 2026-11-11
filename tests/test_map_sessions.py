"""Protect failure-inclusive map percentiles and sampled viewport coverage."""

import base64
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock

from map_sessions import Client, PlaywrightError, classify_requests, quantile, summarize
from PIL import Image


class SessionStatisticsTests(unittest.TestCase):
    def session(self, durations, deadline=2200):
        return {
            "actions": [
                {
                    "id": i,
                    "protocol": "paced",
                    "completion_ms": value,
                    "deadline_ms": deadline,
                }
                for i, value in enumerate(durations)
            ],
            "samples": [],
        }

    def test_unfinished_targets_remain_in_percentile_and_slo_denominators(self):
        result = summarize([self.session([100, 200, None])])["paced"]
        self.assertEqual(result["targets"], 3)
        self.assertEqual(result["within_target_fraction"]["1000"], 2 / 3)
        self.assertTrue(result["p95_censored"])
        self.assertIsNone(result["p95_ms"])

    def test_completion_after_target_deadline_is_not_a_success(self):
        result = summarize([self.session([2300])])["paced"]
        self.assertEqual(result["completed_before_deadline"], 0)
        self.assertEqual(result["within_target_fraction"]["2000"], 0)

    def test_area_deficit_integrates_elapsed_time(self):
        session = self.session([100])
        session["samples"] = [
            {"at_ms": 0, "coverage": 0, "action": 0},
            {"at_ms": 100, "coverage": 0.5, "action": 0},
            {"at_ms": 300, "coverage": 1, "action": 0},
        ]
        result = summarize([session])["paced"]
        self.assertAlmostEqual(result["uncovered_viewport_seconds"], 0.2)
        self.assertAlmostEqual(result["mean_uncovered_fraction"], 2 / 3)

    def test_rapid_recovery_counts_final_targets_only(self):
        session = {
            "actions": [
                {
                    "id": 0,
                    "protocol": "rapid",
                    "final_gesture": False,
                    "completion_ms": None,
                    "deadline_ms": 300,
                },
                {
                    "id": 1,
                    "protocol": "rapid",
                    "final_gesture": True,
                    "completion_ms": 500,
                    "deadline_ms": 3000,
                },
            ],
            "samples": [],
        }
        result = summarize([session])["rapid"]
        self.assertEqual(result["targets"], 1)
        self.assertEqual(result["p95_ms"], 500)

    def test_nearest_rank_quantile(self):
        self.assertEqual(quantile(range(1, 101), 0.95), 95)

    def test_cleanup_cancellation_is_separate_from_navigation(self):
        session = {
            "time_origin_epoch_ms": 1000,
            "started_ms": 100,
            "ended_ms": 1000,
            "actions": [{"id": 0, "started_ms": 100, "protocol": "rapid"}],
        }
        requests = [
            {
                "user": 0,
                "started_epoch_ms": 1200,
                "started_monotonic_s": 10,
                "failed_monotonic_s": failure,
                "canceled": True,
            }
            for failure in (10.5, 11)
        ]
        rows = classify_requests(requests, [session])
        self.assertEqual([r["canceled_after_session"] for r in rows], [False, True])
        self.assertTrue(all(r["active_protocol"] == "rapid" for r in rows))


class ImageCaptureTests(unittest.IsolatedAsyncioTestCase):
    async def test_evicted_body_does_not_discard_other_images_or_request_timings(self):
        png = io.BytesIO()
        Image.new("RGBA", (256, 256), (20, 80, 120, 255)).save(png, format="PNG")
        cdp = Mock()
        cdp.send = AsyncMock(
            side_effect=[
                PlaywrightError("Request content was evicted from inspector cache"),
                {
                    "base64Encoded": True,
                    "body": base64.b64encode(png.getvalue()).decode(),
                },
            ]
        )
        client = Client(Mock(), cdp, "https://example.test", 0)
        client.requests = {
            str(i): {
                "path": f"/tiles/{i}.png",
                "status": 200,
                "finished_monotonic_s": i,
                "cdp_id": str(i),
            }
            for i in range(2)
        }
        with tempfile.TemporaryDirectory() as directory:
            images = await client.images(Path(directory))
            self.assertEqual(len(images), 1)
            self.assertTrue((Path(directory) / images[0]["filename"]).exists())
        self.assertEqual(len(client.requests), 2)
        self.assertEqual(client.capture_unavailable[0]["path"], "/tiles/0.png")

    async def test_unexpected_capture_error_is_not_hidden(self):
        cdp = Mock()
        cdp.send = AsyncMock(side_effect=PlaywrightError("Session closed"))
        client = Client(Mock(), cdp, "https://example.test", 0)
        client.requests = {
            "1": {
                "path": "/tile.png",
                "status": 200,
                "finished_monotonic_s": 1,
                "cdp_id": "1",
            }
        }
        with (
            tempfile.TemporaryDirectory() as directory,
            self.assertRaisesRegex(PlaywrightError, "Session closed"),
        ):
            await client.images(Path(directory))


if __name__ == "__main__":
    unittest.main()
