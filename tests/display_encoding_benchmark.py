"""Explore display-codec tradeoffs using real rendered navigation tiles.

Pillow codec results are feasibility evidence, not native encoder timings.
Sample paths and SHA-256 values identify every input; no reference goldens are
created. Alpha loss and decoded error are reported alongside CPU and size.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import statistics
import time
from pathlib import Path

import numpy as np
from PIL import Image, features


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=20)
    args = parser.parse_args()
    report = json.loads(args.report.read_text())
    rows = []
    cases = {(case["scene"], case["mode"]): case for case in report["cases"]}
    for case in cases.values():
        requests = [
            r for r in case["requests"] if r["status"] == 200 and r["valid_pixels"] > 0
        ]
        for request in requests[:: max(1, len(requests) // 3)][:3]:
            name = hashlib.sha256(request["route"].encode()).hexdigest() + ".png"
            path = args.report.parent / case["case"] / "images" / name
            source = Image.open(path).convert("RGBA")
            rgba = np.asarray(source)
            for codec, options in (
                ("PNG", {"compress_level": 1}),
                ("PNG", {"compress_level": 6}),
                ("JPEG", {"quality": 90}),
                ("WEBP", {"quality": 85, "method": 0}),
                ("WEBP", {"lossless": True, "method": 0}),
            ):
                if codec == "WEBP" and not features.check("webp"):
                    continue
                image = source.convert("RGB") if codec == "JPEG" else source
                times = []
                for _ in range(args.repeats):
                    output = io.BytesIO()
                    start = time.process_time_ns()
                    image.save(output, format=codec, **options)
                    times.append((time.process_time_ns() - start) / 1e6)
                payload = output.getvalue()
                decode = time.process_time_ns()
                decoded = np.asarray(Image.open(io.BytesIO(payload)).convert("RGBA"))
                decode = (time.process_time_ns() - decode) / 1e6
                delta = np.abs(
                    decoded[:, :, :3].astype("int16") - rgba[:, :, :3].astype("int16")
                )
                rows.append(
                    {
                        "scene": case["scene"],
                        "mode": case["mode"],
                        "input": str(path),
                        "input_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        "native_png_bytes": path.stat().st_size,
                        "codec": codec,
                        "options": options,
                        "encode_cpu_ms": statistics.median(times),
                        "decode_cpu_ms": decode,
                        "bytes": len(payload),
                        "max_channel_error": int(delta.max()),
                        "mean_channel_error": float(delta.mean()),
                        "alpha_exact": bool(
                            np.array_equal(decoded[:, :, 3], rgba[:, :, 3])
                        ),
                    }
                )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps({"pillow": Image.__version__, "rows": rows}, indent=2) + "\n"
    )
    print("Measured", len(rows), "codec/input combinations")


if __name__ == "__main__":
    main()
