#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
bundle=$(realpath "${1:?native bundle required}")
output=$(realpath -m "${2:?wheel output directory required}")
python=${PYTHON:-python3}
mkdir -p "$output"
cd "$root"
RCFP_BUNDLE_DIR="$bundle" "$python" -m build --wheel --no-isolation --outdir "$output"
