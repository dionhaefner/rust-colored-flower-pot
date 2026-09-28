#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
bundle=$(realpath "${1:?native bundle required}")
output=$(realpath -m "${2:?wheel output directory required}")
python=${PYTHON:-"$root/.venv-reference/bin/python"}
if [[ "$python" == */* ]]; then
    python="$(cd "$(dirname "$python")" && pwd)/$(basename "$python")"
else
    python=$(command -v "$python")
fi
mkdir -p "$output"
stage=$(mktemp -d "$root/validation/artifacts/p07-wheel-stage.XXXXXX")
trap 'rm -rf "$stage"' EXIT
PYTHON="$python" "$root/packaging/build-python.sh" "$bundle" "$stage/package"
cp "$root/packaging/python-setup.py" "$stage/package/setup.py"
cd "$stage/package"
"$python" setup.py bdist_wheel --dist-dir "$output"
