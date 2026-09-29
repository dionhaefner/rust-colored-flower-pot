"""Record oracle package/native versions and installed data/library checksums."""
import importlib.metadata
import platform
import sqlite3
import subprocess
import sys
from pathlib import Path
import rasterio
import rasterio._env
import shapely
import PIL.features
from common import ROOT, COMMIT, read_json, write_json, sha256


def manifest():
    source_archive = ROOT / ".cache/reference/source.tar.gz"
    if not source_archive.is_file():
        source_archive = ROOT / ".cache/reference/source.tar"
    previous = read_json(ROOT / 'process/validation/reference/environment.json') if not source_archive.is_file() else None
    archive_hash = sha256(source_archive) if source_archive.is_file() else previous['source_archive_sha256']
    archive_name = source_archive.name if source_archive.is_file() else 'not retained (hash from prior manifest)'
    versions={d.metadata['Name']:d.version for d in importlib.metadata.distributions()}
    native={}
    site=Path(rasterio.__file__).parent.parent
    for folder in ('rasterio.libs','shapely.libs','rasterio/proj_data','rasterio/gdal_data'):
        for p in sorted((site/folder).rglob('*')):
            if p.is_file(): native[str(p.relative_to(site))]=sha256(p)
    return dict(reference_commit=COMMIT,reference_version=versions['terracotta'],
                source_archive_sha256=archive_hash,
                source_archive_name=archive_name,
                source_modifications=('No application patch. PyPI sdist generates _version.py from tag v0.10.1.'
                                      if source_archive.suffix == '.gz' else
                                      'No application patch. setuptools_scm generates _version.py from explicit 0.10.1 tag.'),
                python=sys.version,platform=platform.platform(),machine=platform.machine(),byteorder=sys.byteorder,
                packages=dict(sorted(versions.items())),GDAL=rasterio.__gdal_version__,PROJ=rasterio.__proj_version__,
                GEOS=shapely.geos_version_string,SQLite=sqlite3.sqlite_version,
                codecs={x:PIL.features.version(x) for x in PIL.features.get_supported()},native_files=native)


if __name__=='__main__':
    write_json(ROOT/'process/validation/reference/environment.json',manifest())
    freeze=subprocess.check_output([sys.executable,'-m','pip','freeze','--all'],text=True)
    lines = [
        line
        for line in freeze.splitlines()
        if not line.lower().startswith("terracotta")
        and not line.startswith("-e ")
        and not line.startswith("# Editable install with no version control")
        and not line.startswith("## !!")
    ]
    (ROOT/'process/validation/reference/requirements.lock').write_text('\n'.join(lines)+'\n')
