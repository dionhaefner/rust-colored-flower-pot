"""Record the conda runtime for subsequent CI steps without guessing DLL names."""

import os
import sys
from pathlib import Path

prefix = Path(sys.prefix)
if sys.platform == "win32":
    directory = prefix / "Library/bin"
    patterns = ["gdal.dll", "gdal[0-9]*.dll"]
elif sys.platform == "darwin":
    directory = prefix / "lib"
    patterns = ["libgdal.dylib"]
else:
    directory = prefix / "lib"
    patterns = ["libgdal.so"]
libraries = [p for pattern in patterns for p in directory.glob(pattern)]
assert libraries, f"GDAL library missing in {directory}"
with open(os.environ["GITHUB_ENV"], "a") as f:
    f.write("TERRACOTTA_GDAL_LIBRARY=" + str(libraries[0]) + "\n")
    if sys.platform == "win32":
        f.write("GDAL_DATA=" + str(prefix / "Library/share/gdal") + "\n")
        f.write("PROJ_DATA=" + str(prefix / "Library/share/proj") + "\n")
