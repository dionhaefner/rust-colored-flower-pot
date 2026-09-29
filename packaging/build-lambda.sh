#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
bundle=${1:-"$root/dist/bundle"}
output=${2:-"$root/dist/lambda"}
bundle=$(realpath "$bundle")
output=$(realpath -m "$output")

if [[ -e "$output" ]]; then
    echo "refusing to replace existing output: $output" >&2
    exit 2
fi
mkdir -p "$output/package"
cp -a "$bundle" "$output/package/runtime"
if [[ -n "${RCFP_LAMBDA_TARGET:-}" ]]; then
    lambda_target=$(realpath "$RCFP_LAMBDA_TARGET")
    cp "$lambda_target/release/rcfp" "$output/package/runtime/bin/rcfp"
    cp "$lambda_target/release/librcfp_core.so" "$output/package/runtime/lib/librcfp_core.so"
    cp -a "$lambda_target/runtime-libs/." "$output/package/runtime/lib/"
    if [[ -d "$lambda_target/licenses" ]]; then
        cp -a "$lambda_target/licenses/." "$output/package/runtime/licenses/"
    fi
fi
cp "$root/packaging/lambda/bootstrap" "$output/package/bootstrap"
chmod +x "$output/package/bootstrap"
(
    cd "$output/package"
    zip -q -r "$output/rcfp-lambda.zip" bootstrap runtime
)
sha256sum "$output/rcfp-lambda.zip" >"$output/rcfp-lambda.zip.sha256"
