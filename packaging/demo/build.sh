#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$root"
docker build --platform linux/amd64 -f packaging/lambda/Dockerfile.ci -t rcfp-lambda-build .
docker build --platform linux/amd64 --provenance=false -f packaging/demo/Dockerfile \
    --build-arg "DEMO_BUCKET=${DEMO_BUCKET:-}" -t "${DEMO_IMAGE:-rcfp-demo}" .
