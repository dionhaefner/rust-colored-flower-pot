"""Protect detached server startup and shared-storage ownership."""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


host = load(ROOT / "packaging/cloud-test/host.py")


class ComparisonTests(unittest.TestCase):
    @unittest.skipUnless(hasattr(os, "fork"), "EC2 comparison requires a POSIX host")
    def test_daemon_does_not_hold_command_output_open(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            executable = folder / "nginx"
            executable.write_text(
                f"#!{sys.executable}\n"
                "import os, time\n"
                "if os.fork() == 0:\n"
                "    time.sleep(3)\n"
                "    os._exit(0)\n"
            )
            executable.chmod(0o755)
            program = (
                "import importlib.util; from pathlib import Path; "
                f"s=importlib.util.spec_from_file_location('remote', {str(ROOT / 'packaging/ec2-comparison/remote.py')!r}); "
                "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
                f"m.BASE=Path({directory!r}); m.nginx(m.BASE); print('ready')"
            )
            result = subprocess.run(
                [sys.executable, "-c", program],
                env={**os.environ, "PATH": directory + os.pathsep + os.environ["PATH"]},
                capture_output=True,
                text=True,
                timeout=2,
                check=True,
            )
            self.assertEqual(result.stdout.strip(), "ready")

    def test_shared_storage_cannot_be_purged_by_host_helper(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            session = folder / "session.json"
            session.write_text(
                json.dumps({"region": "eu-north-1", "storage_stack": None})
            )
            with (
                patch.object(host, "AWS") as client,
                patch(
                    "sys.argv",
                    [
                        "host.py",
                        "purge-storage",
                        "--session",
                        str(session),
                        "--out",
                        str(folder / "out"),
                    ],
                ),
                self.assertRaisesRegex(ValueError, "not owned"),
            ):
                host.main()
            client.return_value.call.assert_not_called()
            client.return_value.json.assert_not_called()


if __name__ == "__main__":
    unittest.main()
