#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "$0")/../.." && pwd)
reference=${TERRACOTTA_REFERENCE:-"$root/../terracotta"}
commit=6db38d5873cde5ebabe47338a1f61ddc788eada6
snapshot="$root/.cache/reference"
environment="$root/.venv-reference"

test "$(git -C "$reference" rev-parse "$commit^{commit}")" = "$commit"
if [[ -e "$snapshot" || -e "$environment" ]]; then
    echo "Refusing to replace an existing snapshot or environment." >&2
    echo "Use an empty workspace cache, or move the existing paths aside." >&2
    exit 2
fi

mkdir -p "$snapshot"
git -C "$reference" archive --format=tar "$commit" -o "$snapshot/source.tar"
tar -xf "$snapshot/source.tar" -C "$snapshot"

# Debian may omit ensurepip. pip's --python option can populate an otherwise
# empty venv directory without changing the system Python installation.
python3 -m venv "$environment" || true
python3 -m pip --python "$environment" install \
    pip==25.1.1 setuptools==80.9.0 wheel==0.45.1 \
    -r "$root/validation/reference/requirements.lock"
SETUPTOOLS_SCM_PRETEND_VERSION=0.10.1 \
    "$environment/bin/pip" install --use-pep517 --no-build-isolation --no-deps -e "$snapshot"
"$environment/bin/python" "$root/validation/tools/environment.py"
