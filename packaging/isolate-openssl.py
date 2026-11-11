"""Give bundled Linux OpenSSL libraries private names before Python loads them."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


def isolate(runtime: Path) -> None:
    libraries = runtime / "lib"
    replacements = {
        "libcrypto.so.3": "librcfp_crypto.so.3",
        "libssl.so.3": "librcfp_ssl.so.3",
    }
    for original, private in replacements.items():
        source = libraries / original
        destination = libraries / private
        if source.exists() and not destination.exists():
            destination.write_bytes(source.read_bytes())
            destination.chmod(source.stat().st_mode)
        if not destination.is_file():
            raise RuntimeError(f"missing bundled OpenSSL library: {original}")
        subprocess.run(
            ["patchelf", "--set-soname", private, str(destination)], check=True
        )

    candidates = [runtime / "bin/rcfp", *libraries.iterdir()]
    for path in candidates:
        if path.is_symlink() or not path.is_file():
            continue
        with path.open("rb") as stream:
            if stream.read(4) != b"\x7fELF":
                continue
        needed = subprocess.check_output(
            ["patchelf", "--print-needed", str(path)], text=True
        ).splitlines()
        for original, private in replacements.items():
            if original in needed:
                subprocess.run(
                    ["patchelf", "--replace-needed", original, private, str(path)],
                    check=True,
                )
    for original, private in replacements.items():
        for path in libraries.iterdir():
            if path.is_symlink() and path.resolve().name == original:
                path.unlink()
                path.symlink_to(private)
        (libraries / original).unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runtime", type=Path)
    isolate(parser.parse_args().runtime)
