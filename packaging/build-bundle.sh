#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
output=${1:-"$root/dist/bundle"}
build_python=${RCFP_BUILD_PYTHON:-${PYTHON:-python3}}

if [[ -e "$output" ]]; then
    echo "refusing to replace existing output: $output" >&2
    exit 2
fi
if ! command -v "$build_python" >/dev/null 2>&1; then
    echo "build Python is missing: $build_python" >&2
    exit 2
fi

if [[ -n "${RCFP_NATIVE_TARGET:-}" ]]; then
    native_target=$(realpath "$RCFP_NATIVE_TARGET")
else
    bundle_rustflags="${RUSTFLAGS:-} -C link-arg=-Wl,--disable-new-dtags,-rpath,\$ORIGIN"
    cargo_flags=(--locked --release)
    if [[ "${RCFP_CARGO_OFFLINE:-0}" == 1 ]]; then
        cargo_flags+=(--offline)
    fi
    RUSTFLAGS="$bundle_rustflags" "$root/packaging/cargo.sh" build "${cargo_flags[@]}"
    native_target="$root/target"
fi
mkdir -p "$output/bin" "$output/lib" "$output/share/gdal" "$output/share/proj" "$output/licenses"
cp "$native_target/release/rcfp" "$output/bin/rcfp"
cp "$native_target/release/librcfp_core.so" "$output/lib/librcfp_core.so"
if [[ -n "${RCFP_GDAL_PREFIX:-}" ]]; then
    gdal_prefix=$(realpath "$RCFP_GDAL_PREFIX")
    shopt -s nullglob
    libraries=("$gdal_prefix"/lib/*.so*)
    if ((${#libraries[@]} == 0)) || [[ ! -f "$gdal_prefix/share/proj/proj.db" ]]; then
        echo "GDAL/PROJ prefix is incomplete: $gdal_prefix" >&2
        exit 2
    fi
    cp -a "${libraries[@]}" "$output/lib/"
    cp -a "$gdal_prefix/share/gdal/." "$output/share/gdal/"
    cp -a "$gdal_prefix/share/proj/." "$output/share/proj/"
    if [[ -f "$gdal_prefix/ssl/cacert.pem" ]]; then
        cp "$gdal_prefix/ssl/cacert.pem" "$output/share/cacert.pem"
    fi
    "$build_python" "$root/packaging/stage-conda-licenses.py" "$gdal_prefix" "$output/licenses/conda"
else
    archive_cache=${RCFP_GDAL_CACHE:-$(mktemp -d)}
    if [[ -z "${RCFP_GDAL_CACHE:-}" ]]; then
        trap 'rm -rf "$archive_cache"' EXIT
    fi
    "$build_python" "$root/packaging/bootstrap-gdal.py" --output "$output" --cache "$archive_cache"
fi
if [[ -d "$native_target/runtime-libs" ]]; then
    cp -a "$native_target/runtime-libs/." "$output/lib/"
    cp -a "$native_target/licenses/." "$output/licenses/"
fi
cp "$root/LICENSE" "$output/licenses/TERRACOTTA-LICENSE.txt"
cp "$root/src/assets/COLOR_OPERATIONS_LICENSE.txt" "$output/licenses/"
cp "$root/src/assets/CRICK_LICENSE.txt" "$output/licenses/"
cp "$root/src/assets/TDIGEST_LICENSE.txt" "$output/licenses/"
cp "$root/src/assets/static/redoc.standalone.js.LICENSE.txt" "$output/licenses/REDOC-LICENSE.txt"
cp "$root/packaging/licenses/CLIENT-LICENSE.txt" "$output/licenses/CLIENT-LICENSE.txt"
cp "$root/THIRD_PARTY_NOTICES.md" "$output/licenses/THIRD_PARTY_NOTICES.md"

"$build_python" "$root/packaging/collect-licenses.py" "$output/licenses/cargo"

"$output/bin/rcfp" --version-json
