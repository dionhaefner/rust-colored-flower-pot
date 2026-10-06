"""Check Python VCS versions with interleaved Python and SDK release tags."""

import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path

from hatch_vcs.version_source import VCSVersionSource

ROOT = Path(__file__).resolve().parents[1]


class ReleaseVersioning(unittest.TestCase):
    def test_independent_release_tags(self):
        config = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["hatch"][
            "version"
        ]
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)

            def git(*args):
                subprocess.run(
                    ["git", *args], cwd=repo, check=True, capture_output=True
                )

            def commit(text):
                (repo / "file.txt").write_text(text)
                git("add", "file.txt")
                git(
                    "-c",
                    "core.hooksPath=/dev/null",
                    "commit",
                    "--no-gpg-sign",
                    "--quiet",
                    "-m",
                    "Version fixture",
                )

            def version():
                return VCSVersionSource(str(repo), config).get_version_data()["version"]

            git("init", "--quiet")
            git("config", "user.name", "Release fixture")
            git("config", "user.email", "fixture@example.test")
            commit("first")
            git("tag", "v2.3.4")
            self.assertEqual(version(), "2.3.4")
            commit("second")
            baseline = version()
            self.assertTrue(baseline.startswith("2.3.5.dev1+"), baseline)
            git("tag", "sdk-v9.0.0")
            self.assertEqual(version(), baseline)
            git("tag", "sdk-v9.0.1-rc.1")
            self.assertEqual(version(), baseline)
            git("tag", "v2.4.0")
            self.assertEqual(version(), "2.4.0")


if __name__ == "__main__":
    unittest.main()
