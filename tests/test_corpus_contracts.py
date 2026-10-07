"""Controlled failures for decoded corpus identities beyond file checksums."""

import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import rasterio
from corpus_contracts import MANIFESTS, check_fixture, digest


class CorpusControls(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.manifest = MANIFESTS[0]
        self.rows = json.loads(self.manifest.read_text())["fixtures"]

    def fixture(self, name):
        row = copy.deepcopy(next(row for row in self.rows if row["id"] == name))
        path = self.folder / row["file"]
        shutil.copyfile(self.manifest.parent / row["file"], path)
        return path, row

    def test_changed_sample_with_updated_file_checksum(self):
        path, row = self.fixture("red")
        with rasterio.open(path, "r+") as source:
            data = source.read(1)
            data[0, 0] += 1
            source.write(data, 1)
        row["sha256"] = digest(path)
        with self.assertRaisesRegex(AssertionError, "decoded pixels"):
            check_fixture(path, row)

    def test_changed_mask_with_updated_file_checksum(self):
        path, row = self.fixture("edge")
        with (
            rasterio.Env(GDAL_TIFF_INTERNAL_MASK=True),
            rasterio.open(path, "r+") as source,
        ):
            mask = source.read_masks(1)
            mask[0, 0] = 255 - mask[0, 0]
            source.write_mask(mask)
        row["sha256"] = digest(path)
        with self.assertRaisesRegex(AssertionError, "decoded mask"):
            check_fixture(path, row)

    def test_changed_category_with_updated_file_checksum(self):
        path, row = self.fixture("classes")
        with rasterio.open(path, "r+") as source:
            data = source.read(1)
            data[0, 0] = 10 if data[0, 0] != 10 else 80
            source.write(data, 1)
        row["sha256"] = digest(path)
        with self.assertRaisesRegex(AssertionError, "decoded pixels"):
            check_fixture(path, row)

    def test_misplaced_grid_with_updated_file_checksum(self):
        path, row = self.fixture("red")
        with rasterio.open(path, "r+") as source:
            source.transform = source.transform @ rasterio.Affine.translation(1, 0)
        row["sha256"] = digest(path)
        with self.assertRaisesRegex(AssertionError, "affine placement"):
            check_fixture(path, row)

    def test_wrong_geographic_expectation(self):
        path, row = self.fixture("red")
        row["geographic_bounds"][0] += 1
        with self.assertRaisesRegex(AssertionError, "geographic bounds"):
            check_fixture(path, row)

    def test_wrong_numerical_expectation(self):
        path, row = self.fixture("elevation")
        row["center_pixel"] += 1
        with self.assertRaisesRegex(AssertionError, "center value"):
            check_fixture(path, row)


if __name__ == "__main__":
    unittest.main()
