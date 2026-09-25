#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
bundle=${1:-"$root/validation/artifacts/p03-bundle"}
output=${2:-"$root/validation/artifacts/p03-lambda"}
bundle=$(realpath "$bundle")
output=$(realpath -m "$output")

if [[ -e "$output" ]]; then
    echo "refusing to replace existing output: $output" >&2
    exit 2
fi
mkdir -p "$output/package"
cp -a "$bundle" "$output/package/runtime"
if [[ -n "${TERRACOTTA_LAMBDA_TARGET:-}" ]]; then
    lambda_target=$(realpath "$TERRACOTTA_LAMBDA_TARGET")
    cp "$lambda_target/release/terracotta" "$output/package/runtime/bin/terracotta"
    cp "$lambda_target/release/libterracotta_core.so" "$output/package/runtime/lib/libterracotta_core.so"
    cp -a "$lambda_target/runtime-libs/." "$output/package/runtime/lib/"
    if [[ -d "$lambda_target/licenses" ]]; then
        cp -a "$lambda_target/licenses/." "$output/package/runtime/licenses/"
    fi
fi
cp "$root/packaging/lambda/bootstrap" "$output/package/bootstrap"
chmod +x "$output/package/bootstrap"
(
    cd "$output/package"
    zip -q -r "$output/terracotta-lambda.zip" bootstrap runtime
)
sha256sum "$output/terracotta-lambda.zip" > "$output/terracotta-lambda.zip.sha256"
