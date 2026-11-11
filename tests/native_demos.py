"""Execute the native tutorial's Python examples against the installed package."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    subprocess.run(
        [sys.executable, "-m", "rcfp_cli", "sample", "-o", "sample"],
        cwd=out,
        check=True,
        timeout=30,
    )
    source = (ROOT / "docs/tutorials/native-demos.md").read_text()
    blocks = re.findall(r"```python\n(.*?)\n```", source, re.DOTALL)
    assert len(blocks) == 5, (
        "native demo Python blocks changed; review expected outputs"
    )
    for code in blocks:
        subprocess.run([sys.executable, "-c", code], cwd=out, check=True, timeout=60)
    outputs = ["ndvi.png", "stac.png", "mosaic.png", "model.png", "published.png"]
    for name in outputs:
        with Image.open(out / name) as image:
            assert image.size == (256, 256), name
            assert image.convert("RGBA").getchannel("A").getextrema()[1] > 0, name
    report = {"passed": True, "examples": len(blocks), "outputs": outputs}
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
