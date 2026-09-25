#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "$0")/../.." && pwd)
cd "$root"
python="$root/.venv-reference/bin/python"

"$python" validation/tools/acquire.py --offline --report validation/artifacts/acquisition-check.json
"$python" validation/tools/subset.py --verify
"$python" -m pytest validation/tools/test_compare.py
validation/tools/cargo.sh test --locked --offline
