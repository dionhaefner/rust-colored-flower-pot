#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "$0")/../.." && pwd)
snapshot="$root/.cache/reference"
environment="$root/.venv-reference"

if [[ -e "$snapshot" || -e "$environment" ]]; then
    echo "Refusing to replace an existing snapshot or environment." >&2
    echo "Use an empty workspace cache, or move the existing paths aside." >&2
    exit 2
fi

fetch_args=(--output "$snapshot")
if [[ -n "${TERRACOTTA_SOURCE_ARCHIVE:-}" ]]; then
    fetch_args+=(--archive "$TERRACOTTA_SOURCE_ARCHIVE")
fi
python3 "$root/tests/reference/fetch.py" "${fetch_args[@]}"

# Debian may omit ensurepip. pip's --python option can populate an otherwise
# empty venv directory without changing the system Python installation.
python3 -m venv "$environment" || true
python3 -m pip --python "$environment" install \
    pip==25.1.1 setuptools==80.9.0 wheel==0.45.1 \
    -r "$root/tests/reference/requirements.lock"
SETUPTOOLS_SCM_PRETEND_VERSION=0.10.1 \
    "$environment/bin/pip" install --use-pep517 --no-build-isolation --no-deps -e "$snapshot"
"$environment/bin/python" "$root/tests/reference/record_environment.py"
