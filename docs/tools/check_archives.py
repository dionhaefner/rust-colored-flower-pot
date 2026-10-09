"""Verify documentation and versioning boundaries in package artifacts."""

import argparse
import email
import tarfile
import tomllib
import zipfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdist", type=Path, required=True)
    parser.add_argument("--wheel", type=Path, required=True)
    args = parser.parse_args()
    with tarfile.open(args.sdist) as archive:
        names = archive.getnames()
        prefix = names[0].split("/")[0] + "/"
        required = (
            "docs/conf.py",
            "docs/reference/javascript.md",
            "js/sdk/package.json",
            "js/sdk/index.js",
            "js/sdk/index.d.ts",
            "js/sdk/CHANGELOG.md",
            "js/sdk/integrations.js",
            "packaging/sdk/package.json",
            "packaging/sdk/package-lock.json",
            "packaging/verify-sdk-release.mjs",
            "docs/how-to/release-sdk.md",
            "src/assets/client/examples/leaflet.html",
            "src/assets/client/examples/maplibre.html",
            "src/assets/client/examples/openlayers.html",
            "src/assets/client/vendor/manifest.json",
            "tests/sdk_contracts.mjs",
            "tests/sdk_types.mts",
            "tests/sdk_release.mjs",
            "tests/sdk_versioning.py",
            "tests/sdk_package.mjs",
            "tests/sdk_package_types.mts",
            "docs/index.md",
            "docs/how-to/upgrade-terracotta.md",
            "docs/how-to/evaluate.md",
            "docs/support-policy.md",
            "docs/explanation/raster-corpus.md",
            "docs/examples/application-evaluation.md",
            "tests/fixtures/README.md",
            "tests/corpus_contracts.py",
            "tests/license_contracts.py",
            "tests/test_corpus_contracts.py",
            "tests/upgrade_contracts.py",
            "docs/examples/create_catalog.py",
            "docs/examples/stac_map.py",
            "docs/examples/data/lagoon-stac.json",
            "docs/examples/data/earth-search-source.json",
            "docs/examples/data/earth-search-revisions.json",
            "tests/stac_live.py",
            "tests/fixtures/stac-live.json",
            "docs/examples/data/lagoon-multispectral-stac.json",
            "docs/examples/data/lagoon-catalog.json",
            "docs/examples/data/lagoon-green-stac.json",
            "src/stac/catalog.rs",
            "src/stac/serve.rs",
            "src/catalog.rs",
            "src/catalog/source.rs",
            "src/catalog/dataset.rs",
            "src/catalog/plan.rs",
            "src/catalog/point.rs",
            "src/http/native.rs",
            "src/tile_matrix.rs",
            "docs/reference/native-services.md",
            "tests/native_services.py",
            "tests/fixtures/tile-grids/manifest.json",
            "tests/fixtures/tile-grids/arctic.tif",
            "tests/fixtures/tile-grids/south.tif",
            "tests/fixtures/tile-grids/dateline-east.tif",
            "tests/fixtures/tile-grids/dateline-west.tif",
            "src/catalog/write.rs",
            "src/storage/native.rs",
            "tests/catalog_contracts.py",
            "tests/stac_serving.py",
            "docs/how-to/stac.md",
            "docs/how-to/demo.md",
            "packaging/demo/export-viewer.py",
            "packaging/stage-licenses.py",
            "packaging/demo/prepare.py",
            "packaging/demo/template.yaml",
            "packaging/demo/storage.yaml",
            "packaging/demo/Dockerfile",
            "packaging/demo/build.sh",
            "packaging/demo/deploy.py",
            "packaging/demo/remove.py",
            "tests/demo_contracts.py",
            "tests/fixtures/demo/manifest.json",
            "tests/fixtures/demo/preview.png",
            "tests/reference/acquire_demo.py",
            "docs/how-to/cloud-test.md",
            "packaging/cloud-test/host.py",
            "packaging/cloud-test/remote.py",
            "packaging/cloud-test/template.yaml",
            "packaging/cloud-test/storage.yaml",
            "tests/cloud_deployment.py",
            "tests/cloud_test_contracts.py",
            "src/stac.rs",
            "python/rcfp/stac.py",
            "tests/stac_contracts.py",
            "src/layers.rs",
            "src/layers/catalog.rs",
            "src/layers/mosaic.rs",
            "packaging/prepare-mosaic.py",
            "tests/mosaic_contracts.py",
            "docs/how-to/mosaics.md",
            "python/rcfp/layers.py",
            "docs/reference/layers.md",
            "docs/reference/advanced-layers.md",
            "tests/layer_contracts.py",
            "src/http/delivery.rs",
            "tests/rendered_cache_contracts.py",
            "docs/how-to/rendered-caching.md",
            "docs/examples/render-cache.nginx.conf",
            "docs/examples/rendered-cache.html",
            "docs/examples/data/red.tif",
            "docs/examples/data/manifest.json",
            "docs/tools/verify.py",
            "docs/tools/generate_reference.py",
            "docs/_generated/version.json",
            "docs/_static/architecture.svg",
            "docs/_static/TERRACOTTA-LICENSE.txt",
            "python/rcfp/_version.py",
        )
        for name in required:
            assert prefix + name in names, name
        assert not any(
            "/process/" in name
            or "/design/" in name
            or "/docs/_build/" in name
            or "/.cache/" in name
            or "/node_modules/" in name
            for name in names
        )
        metadata = tomllib.loads(
            archive.extractfile(prefix + "pyproject.toml").read().decode()
        )
        assert metadata["tool"]["hatch"]["version"]["source"] == "vcs"
        assert any(
            name.startswith("hatch-vcs")
            for name in metadata["build-system"]["requires"]
        )
        for target in ("wheel", "sdist"):
            assert (
                metadata["tool"]["hatch"]["build"]["targets"][target][
                    "core-metadata-version"
                ]
                == "2.4"
            )
        package = email.message_from_bytes(
            archive.extractfile(prefix + "PKG-INFO").read()
        )
        assert package["Metadata-Version"] == "2.4"
    with zipfile.ZipFile(args.wheel) as archive:
        assert not any(
            name.startswith(
                ("docs/", "design/", "process/", "terracotta/", "terracotta_rust/")
            )
            for name in archive.namelist()
        )
        assert "rcfp/_version.py" in archive.namelist()
        assert "rcfp/stac.py" in archive.namelist()
    print("Source docs, wheel boundary, and Hatch VCS requirements verified")


if __name__ == "__main__":
    main()
