#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "$0")/../../.." && pwd)
cd "$root"
python="$root/.venv-reference/bin/python"

"$python" process/validation/tools/acquire.py --offline --report process/validation/artifacts/acquisition-check.json
"$python" process/validation/tools/subset.py --verify
"$python" -m pytest process/validation/tools/test_compare.py
packaging/cargo.sh test --locked --offline
