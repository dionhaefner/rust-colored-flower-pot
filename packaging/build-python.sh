#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
bundle=${1:-"$root/dist/bundle"}
output=${2:-"$root/dist/python"}
python=${PYTHON:-python3}

if [[ -e "$output" ]]; then
    echo "refusing to replace existing output: $output" >&2
    exit 2
fi
if [[ ! -d "$bundle/lib" ]]; then
    echo "bundle is missing: $bundle" >&2
    exit 2
fi

suffix=.abi3.so
include=$("$python" -c 'import sysconfig; print(sysconfig.get_paths()["include"])')
gdal_library=$(find "$bundle/lib" -maxdepth 1 -type f -name 'libgdal*.so*' -printf '%f\n' | sort | head -n 1)
if [[ -z "$gdal_library" ]]; then
    echo "bundle contains no GDAL shared library" >&2
    exit 2
fi
mkdir -p "$output"
cp -a "$root/python/rcfp" "$output/rcfp"
cp "$root/README.md" "$output/README.md"
find "$output/rcfp" -type d -name __pycache__ -prune -exec rm -rf {} +
find "$output/rcfp" -type f -name '*.pyc' -delete
cp -a "$bundle" "$output/rcfp/runtime"
"$python" "$root/packaging/isolate-openssl.py" "$output/rcfp/runtime"
# The runtime loader expands $ORIGIN in the rpath.
# shellcheck disable=SC2016
cc -shared -fPIC -DPy_LIMITED_API=0x030C0000 -I"$include" \
    "$root/python/native.c" \
    -L"$bundle/lib" -lrcfp_core \
    -Wl,--no-as-needed -Wl,-l:"$gdal_library" -Wl,--as-needed \
    -Wl,--no-as-needed -Wl,--undefined=getpid -lc -Wl,--as-needed \
    -Wl,--disable-new-dtags,-rpath,'$ORIGIN/runtime/lib' \
    -o "$output/rcfp/_native$suffix"

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$output" "$python" -c 'import rcfp; print(rcfp.__all__)'
