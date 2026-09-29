#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -x .cache/cargo/bin/cargo ]]; then
    export CARGO_HOME="$PWD/.cache/cargo"
    export RUSTUP_HOME="$PWD/.cache/rustup"
    export PATH="$CARGO_HOME/bin:$PATH"
fi
exec cargo "$@"
