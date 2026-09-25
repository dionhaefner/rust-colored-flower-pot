#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
output=${1:-"$root/validation/artifacts/p03-bundle"}
reference_python=${TERRACOTTA_REFERENCE_PYTHON:-"$root/.venv-reference/bin/python"}

if [[ -e "$output" ]]; then
    echo "refusing to replace existing output: $output" >&2
    exit 2
fi
if [[ ! -x "$reference_python" ]]; then
    echo "reference Python is missing: $reference_python" >&2
    exit 2
fi

if [[ -n "${TERRACOTTA_NATIVE_TARGET:-}" ]]; then
    native_target=$(realpath "$TERRACOTTA_NATIVE_TARGET")
else
    bundle_rustflags="${RUSTFLAGS:-} -C link-arg=-Wl,--disable-new-dtags,-rpath,\$ORIGIN"
    RUSTFLAGS="$bundle_rustflags" "$root/validation/tools/cargo.sh" build --locked --offline --release
    native_target="$root/target"
fi
site=$($reference_python -c 'import pathlib, rasterio; print(pathlib.Path(rasterio.__file__).parent.parent)')
mkdir -p "$output/bin" "$output/lib" "$output/share/gdal" "$output/share/proj" "$output/licenses"
cp "$native_target/release/terracotta" "$output/bin/terracotta"
cp "$native_target/release/libterracotta_core.so" "$output/lib/libterracotta_core.so"
cp -a "$site/rasterio.libs/." "$output/lib/"
if [[ -d "$native_target/runtime-libs" ]]; then
    cp -a "$native_target/runtime-libs/." "$output/lib/"
    cp -a "$native_target/licenses/." "$output/licenses/"
fi
cp -a "$site/rasterio/gdal_data/." "$output/share/gdal/"
cp -a "$site/rasterio/proj_data/." "$output/share/proj/"
cp "$root/LICENSE" "$output/licenses/TERRACOTTA-LICENSE.txt"
cp "$site/rasterio-"*.dist-info/LICENSE.txt "$output/licenses/RASTERIO-LICENSE.txt"
cp "$root/src/assets/COLOR_OPERATIONS_LICENSE.txt" "$output/licenses/"
cp "$root/src/assets/CRICK_LICENSE.txt" "$output/licenses/"
cp "$root/src/assets/TDIGEST_LICENSE.txt" "$output/licenses/"
cp "$root/src/assets/static/redoc.standalone.js.LICENSE.txt" "$output/licenses/REDOC-LICENSE.txt"
cp "$root/python/terracotta/client/static/react/static/js/"*.LICENSE.txt "$output/licenses/CLIENT-LICENSE.txt"
cp "$root/THIRD_PARTY_NOTICES.md" "$output/licenses/THIRD_PARTY_NOTICES.md"

"$reference_python" "$root/packaging/collect-licenses.py" "$output/licenses/cargo"

"$output/bin/terracotta" --version-json
