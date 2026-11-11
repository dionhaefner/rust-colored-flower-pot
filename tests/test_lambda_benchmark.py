"""Protect the timing boundary and paired statistics used by Lambda comparisons."""

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "lambda_benchmark", Path(__file__).with_name("lambda_benchmark.py")
)
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


class BenchmarkTests(unittest.TestCase):
    def test_completion_excludes_client_decode_and_record(self):
        replies = [
            ({"status": 503, "received_at": 101.0, "http_ms": 1000}, b""),
            ({"status": 503, "received_at": 102.0, "http_ms": 2000}, b""),
        ]
        rows = []
        with (
            patch.object(benchmark, "request", side_effect=replies),
            patch.object(
                benchmark.time, "perf_counter", side_effect=[100.0, 103.0, 110.0]
            ),
        ):
            result = benchmark.batch(
                "https://example.invalid",
                ["/a", "/b"],
                1,
                {"engine": "native"},
                Path("unused"),
                rows,
            )
        self.assertEqual(result["completion_ms"], 2000)
        self.assertEqual(result["client_decode_record_ms"], 7000)
        self.assertEqual(result["errors"], 2)
        self.assertEqual(len(rows), 2)

    def test_ratios_are_computed_within_pairs(self):
        rows = []
        for trial, (native, terracotta) in enumerate(((1, 50), (100, 150), (200, 100))):
            for engine, value in (("native", native), ("terracotta", terracotta)):
                rows.append(
                    {
                        "phase": "measured",
                        "trial": trial,
                        "engine": engine,
                        "mode": "rgb",
                        "concurrency": 4,
                        "stage": "pan",
                        "completion_ms": value,
                        "errors": 0,
                    }
                )
        rows.append({**rows[0], "phase": "warmup", "completion_ms": 9999})
        result = benchmark.summarize(rows)[0]
        self.assertEqual(result["median_paired_speedup"], 1.5)
        self.assertEqual(result["paired_speedups"], [50, 1.5, 0.5])
        self.assertEqual(result["paired_trials"], 3)

    def test_workload_has_three_zooms_and_separate_band_assets(self):
        singleband, rgb = benchmark.stages("singleband"), benchmark.stages("rgb")
        self.assertEqual(
            {path.split("/")[4] for _, paths in singleband for path in paths},
            {"11", "13", "14"},
        )
        self.assertTrue(
            all("r=red&g=green&b=blue" in path for _, paths in rgb for path in paths)
        )
        self.assertEqual(singleband[0][1], singleband[-1][1])
        self.assertEqual([len(paths) for _, paths in rgb], [9] * 5)

    def test_failed_pairs_do_not_contribute_speedups(self):
        rows = [
            {
                "phase": "measured",
                "trial": 0,
                "engine": engine,
                "mode": "rgb",
                "concurrency": 4,
                "stage": "pan",
                "completion_ms": 10,
                "errors": int(engine == "terracotta"),
            }
            for engine in ("native", "terracotta")
        ]
        result = benchmark.summarize(rows)[0]
        self.assertEqual(result["paired_trials"], 0)
        self.assertEqual(result["excluded_pairs"], 1)
        self.assertEqual(result["errors"], 1)
        self.assertIsNone(result["median_paired_speedup"])


if __name__ == "__main__":
    unittest.main()
