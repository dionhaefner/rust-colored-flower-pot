#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
bundle=${1:-"$root/validation/artifacts/p03-bundle"}
output=${2:-"$root/validation/artifacts/p03-python"}
python=${PYTHON:-python3}
python_config=${PYTHON_CONFIG:-python3-config}

if [[ -e "$output" ]]; then
    echo "refusing to replace existing output: $output" >&2
    exit 2
fi
if [[ ! -d "$bundle/lib" ]]; then
    echo "bundle is missing: $bundle" >&2
    exit 2
fi

suffix=$($python_config --extension-suffix)
gdal_library=$(find "$bundle/lib" -maxdepth 1 -type f -name 'libgdal*.so*' -printf '%f\n' | sort | head -n 1)
if [[ -z "$gdal_library" ]]; then
    echo "bundle contains no GDAL shared library" >&2
    exit 2
fi
mkdir -p "$output/terracotta_rust"
cp -a "$root/python/terracotta_rust/." "$output/terracotta_rust/"
cp -a "$root/python/terracotta" "$output/terracotta"
cp -a "$bundle" "$output/terracotta_rust/runtime"
cc -shared -fPIC $($python_config --includes) \
    "$root/python/native.c" \
    -L"$bundle/lib" -lterracotta_core \
    -Wl,--no-as-needed -Wl,-l:"$gdal_library" -Wl,--as-needed \
    -Wl,--disable-new-dtags,-rpath,'$ORIGIN/runtime/lib' \
    -o "$output/terracotta_rust/_native$suffix"

PYTHONPATH="$output" "$python" -c 'import terracotta_rust; print(terracotta_rust.__all__)'
