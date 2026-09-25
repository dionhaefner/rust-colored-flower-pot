#!/bin/sh
set -eu

dnf install -y gcc gcc-c++ make perl-core expat zlib
cp -a /workspace/.cache/cargo /tmp/cargo
export CARGO_HOME=/tmp/cargo
export PATH=/workspace/.cache/rustup/toolchains/1.85.1-x86_64-unknown-linux-gnu/bin:$PATH
export RUSTC=/workspace/.cache/rustup/toolchains/1.85.1-x86_64-unknown-linux-gnu/bin/rustc
export CARGO_TARGET_DIR=/output
export RUSTFLAGS='-C link-arg=-Wl,--disable-new-dtags,-rpath,$ORIGIN'

cargo build --manifest-path /workspace/Cargo.toml --locked --offline --release
mkdir -p /output/runtime-libs /output/licenses
cp -aL /usr/lib64/libstdc++.so.6* /output/runtime-libs/
cp -aL /usr/lib64/libgcc_s.so.1* /output/runtime-libs/
cp -aL /usr/lib64/libexpat.so.1* /output/runtime-libs/
cp -aL /usr/lib64/libz.so.1* /output/runtime-libs/
for directory in /usr/share/licenses/gcc /usr/share/licenses/libgcc /usr/share/licenses/expat /usr/share/licenses/zlib; do
    if [ -d "$directory" ]; then
        name=$(basename "$directory")
        mkdir -p "/output/licenses/$name"
        cp -a "$directory/." "/output/licenses/$name/"
    fi
done
chown -R "$HOST_UID:$HOST_GID" /output
