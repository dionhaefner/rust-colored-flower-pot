"""Run sequential paired navigation trials and summarize every workload cell."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit

import numpy as np
from PIL import Image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--baseline-setting", action="append", default=[])
    parser.add_argument("--candidate-setting", action="append", default=[])
    parser.add_argument("--allow-image-change", action="store_true")
    parser.add_argument("--candidate-manifest", type=Path)
    parser.add_argument("--candidate-sources", type=Path)
    parser.add_argument("--candidate-rgb-format", choices=("png", "webp"))
    args, benchmark_args = parser.parse_known_args()

    def identity(route):
        if not args.candidate_manifest and not args.candidate_rgb_format:
            return route
        url = urlsplit(route)
        parameters = [
            (key, value)
            for key, value in parse_qsl(url.query)
            if key
            not in (
                ("band", "r_band", "g_band", "b_band")
                if args.candidate_manifest
                else ()
            )
            and key not in (("format", "quality") if args.candidate_rgb_format else ())
        ]
        return url.path + "?" + urlencode(sorted(parameters))

    if benchmark_args[:1] == ["--"]:
        benchmark_args = benchmark_args[1:]
    args.out.mkdir(parents=True, exist_ok=False)
    results = {"baseline": [], "candidate": []}
    for trial in range(args.trials):
        for name in (
            ["baseline", "candidate"] if trial % 2 == 0 else ["candidate", "baseline"]
        ):
            folder = args.out / f"{name}-{trial}"
            binary = getattr(args, name)
            settings = getattr(args, name + "_setting")
            command = [
                sys.executable,
                str(Path(__file__).with_name("navigation_benchmark.py")),
                "--binary",
                str(binary),
                "--out",
                str(folder),
                "--trials",
                "1",
                *benchmark_args,
            ]
            for setting in settings:
                command.extend(["--setting", setting])
            if name == "candidate":
                if args.candidate_rgb_format:
                    command.extend(["--rgb-format", args.candidate_rgb_format])
                for option in ("manifest", "sources"):
                    value = getattr(args, "candidate_" + option)
                    if value:
                        command.extend(["--" + option, str(value)])
            with (args.out / f"{name}-{trial}.log").open("w") as log:
                subprocess.run(command, check=True, stdout=log, stderr=log)
            results[name].append(json.loads((folder / "report.json").read_text()))
    cells = {}
    for name, trials in results.items():
        for report in trials:
            for case in report["cases"]:
                key = "-".join(
                    str(case[k])
                    for k in ("scene", "mode", "storage", "users", "overlap")
                )
                cells.setdefault(key, {"baseline": [], "candidate": []})[name].append(
                    case
                )
    summary = []
    for key, cell in cells.items():
        row = {"case": key}
        for kind in ("first", "zoom", "pan", "revisit"):
            for name in ("baseline", "candidate"):
                values = [
                    statistics.median(
                        step["completion_ms"]
                        for step in case["steps"]
                        if step["kind"] == kind
                    )
                    for case in cell[name]
                ]
                row[f"{name}_{kind}_ms"] = statistics.median(values)
            row[f"{kind}_ratio"] = (
                row[f"baseline_{kind}_ms"] / row[f"candidate_{kind}_ms"]
            )
        for name in ("baseline", "candidate"):
            row[name + "_response_bytes"] = statistics.median(
                sum(r["bytes"] for r in case["requests"]) for case in cell[name]
            )
            row[name + "_http_p95_ms"] = statistics.median(
                float(np.percentile([r["http_ms"] for r in case["requests"]], 95))
                for case in cell[name]
            )
            row[name + "_cpu_seconds"] = statistics.median(
                case["resources"].get("cpu_seconds", 0) for case in cell[name]
            )
            traffic = [
                case["source_traffic"] for case in cell[name] if case["source_traffic"]
            ]
            if traffic:
                row[name + "_source_bytes"] = statistics.median(
                    t["bytes"] for t in traffic
                )
                row[name + "_source_gets"] = statistics.median(
                    t["gets"] for t in traffic
                )
        changed = 0
        for trial, (baseline, candidate) in enumerate(
            zip(cell["baseline"], cell["candidate"])
        ):
            before = {identity(r["route"]): r for r in baseline["requests"]}
            after = {identity(r["route"]): r for r in candidate["requests"]}
            if before.keys() != after.keys():
                raise AssertionError("paired trials requested different tiles")
            for route, item in before.items():
                other = after[route]
                if (
                    item["status"] != 200
                    or other["status"] != 200
                    or item["dimensions"] != other["dimensions"]
                ):
                    raise AssertionError("paired request failed or changed dimensions")
                changed += item["decoded_sha256"] != other["decoded_sha256"]
                if args.candidate_rgb_format == "webp":
                    # Lossy display colors may change; placement/alpha must not.
                    for name, record in (("baseline", item), ("candidate", other)):
                        case = baseline if name == "baseline" else candidate
                        folder = args.out / f"{name}-{trial}" / case["case"] / "images"
                        file = folder / (
                            hashlib.sha256(record["route"].encode()).hexdigest()
                            + ".png"
                        )
                        alpha = np.asarray(Image.open(file).convert("RGBA"))[:, :, 3]
                        if name == "baseline":
                            original_alpha = alpha
                        elif not np.array_equal(original_alpha, alpha):
                            raise AssertionError("WebP changed coverage")
        row["changed_decoded_images"] = changed
        summary.append(row)
    (args.out / "comparison.json").write_text(
        json.dumps(
            {
                "trials": args.trials,
                "image_change_requires_independent_validation": args.allow_image_change,
                "cells": summary,
            },
            indent=2,
        )
        + "\n"
    )
    print(json.dumps(summary, indent=2))
    if not args.allow_image_change and any(
        r["changed_decoded_images"] for r in summary
    ):
        raise SystemExit("decoded images changed")


if __name__ == "__main__":
    main()
