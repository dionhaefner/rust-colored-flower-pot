#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
output=${1:-"$root/validation/artifacts/p03-lambda-target"}
output=$(realpath -m "$output")
image=${TERRACOTTA_LAMBDA_BUILD_IMAGE:-"public.ecr.aws/lambda/provided@sha256:6e44223227dfb00686065ab66db7c4ce86f1483a8f69e40bcba5ebdb0f4d5fd7"}

if [[ -e "$output" && "${TERRACOTTA_REUSE_TARGET:-0}" != 1 ]]; then
    echo "refusing to replace existing output: $output" >&2
    exit 2
fi
mkdir -p "$output"
docker run --rm \
    --entrypoint /workspace/packaging/lambda/build-in-al2023.sh \
    -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" \
    -v "$root:/workspace:ro" \
    -v "$output:/output" \
    "$image"

objdump -T "$output/release/terracotta" \
    | grep -o 'GLIBC_[0-9.]*' \
    | sort -Vu \
    | tail -n 1
