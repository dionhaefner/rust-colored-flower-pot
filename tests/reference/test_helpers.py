"""Check reference-runner cleanup without starting services or reading rasters."""

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import URLError

import differential


class CaptureCleanup(unittest.TestCase):
    def test_startup_failures_reap_candidate(self):
        for running in (False, True):
            with (
                self.subTest(running=running),
                tempfile.TemporaryDirectory() as temporary,
            ):
                server = MagicMock()
                server.poll.return_value = None if running else 2
                clock = [0, 0, 21] if running else [0, 0]
                with (
                    patch.object(differential.subprocess, "Popen", return_value=server),
                    patch.object(differential.socket, "socket"),
                    patch.object(differential.time, "monotonic", side_effect=clock),
                    patch.object(differential.time, "sleep"),
                    patch.object(
                        differential.urllib.request,
                        "urlopen",
                        side_effect=URLError("unavailable"),
                    ),
                    self.assertRaisesRegex(RuntimeError, "candidate server"),
                ):
                    differential.capture(
                        "candidate",
                        Path("catalog.sqlite"),
                        Path(temporary) / "out",
                        Path("rcfp"),
                    )
                server.terminate.assert_called_once()
                server.wait.assert_called_once_with(timeout=10)

    def test_shutdown_timeout_kills_candidate(self):
        server = MagicMock()
        server.wait.side_effect = [subprocess.TimeoutExpired("rcfp", 10), 0]
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(differential.subprocess, "Popen", return_value=server),
            patch.object(differential.socket, "socket"),
            patch.object(differential, "cases", return_value=[]),
            patch.object(differential.urllib.request, "urlopen"),
        ):
            differential.capture(
                "candidate",
                Path("catalog.sqlite"),
                Path(temporary) / "out",
                Path("rcfp"),
            )
        server.kill.assert_called_once()
        self.assertEqual(server.wait.call_count, 2)


if __name__ == "__main__":
    unittest.main()
