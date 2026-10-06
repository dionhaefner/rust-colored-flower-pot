"""Compare native raster engines on pinned inputs; keep performance and fidelity separate."""

from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import json
import os
import platform
import resource
import statistics
import subprocess
import time
from pathlib import Path

import numpy as np
import psutil
import rasterio
from native_rendering import bounds_for_tile, expected
from navigation_benchmark import ROOT, digest, quantiles, range_server, tile
from PIL import Image
from rasterio.enums import ColorInterp, Resampling
from rasterio.shutil import copy as raster_copy
from rasterio.warp import calculate_default_transform, reproject
from rasterio.windows import Window

ENGINES = ("rcfp", "async-native", "async-gdal")
MIB = 1024**2
GDAL_TYPES = {
    "uint8": 1,
    "uint16": 2,
    "int16": 3,
    "uint32": 4,
    "int32": 5,
    "float32": 6,
    "float64": 7,
    "uint64": 12,
    "int64": 13,
    "int8": 14,
}


def write(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def inputs(manifest_path, sources, scene_ids):
    manifest = json.loads(manifest_path.read_text())
    scenes = [s for s in manifest["scenes"] if s["id"] in scene_ids]
    if not scenes:
        raise ValueError("no selected scenes in manifest")
    used = {key for scene in scenes for key in scene["bands"].values()}
    paths, identities = {}, {}
    for key in sorted(used):
        row = manifest["sources"][key]
        path = (sources / row["file"]).resolve()
        if not path.is_file() or digest(path) != row["sha256"]:
            raise ValueError(f"missing or checksum-mismatched input: {key}: {path}")
        paths[key] = path
        identities[key] = dict(row, path=str(path), bytes=path.stat().st_size)
    return scenes, paths, identities


def groups_for(workload, scenes, paths, args):
    if workload == "metadata":
        jobs = [{"id": key, "source": key, "mode": "metadata"} for key in paths]
        return [
            {"name": "first", "jobs": jobs},
            {"name": "revisit", "jobs": copy.deepcopy(jobs)},
        ]
    if workload == "windows":
        first, pans, overviews = [], [], []
        for key, path in paths.items():
            with rasterio.open(path) as src:
                bands = [1, 2, 3] if src.count >= 3 else [1]
                ranges = [[0, 255] if src.dtypes[0] == "uint8" else [0, 3000]] * len(
                    bands
                )
                w, h = min(256, src.width), min(256, src.height)
                x, y = (src.width - w) // 2, (src.height - h) // 2
                base = {
                    "source": key,
                    "mode": "window",
                    "bands": bands,
                    "ranges": ranges,
                    "resampling": "nearest",
                }
                first.append(dict(base, id=f"{key}/base", window=[x, y, w, h]))
                pans.append(
                    dict(
                        base,
                        id=f"{key}/pan",
                        window=[min(x + w, src.width - w), y, w, h],
                    )
                )
                for level, _ in enumerate(src.overviews(1), 1):
                    with rasterio.open(path, OVERVIEW_LEVEL=level - 1) as overview:
                        ow, oh = min(256, overview.width), min(256, overview.height)
                        overviews.append(
                            dict(
                                base,
                                id=f"{key}/overview-{level}",
                                overview=level,
                                window=[
                                    (overview.width - ow) // 2,
                                    (overview.height - oh) // 2,
                                    ow,
                                    oh,
                                ],
                            )
                        )
        return [
            {"name": "first", "jobs": first},
            {"name": "pan", "jobs": pans},
            {"name": "overviews", "jobs": overviews},
            {"name": "revisit", "jobs": copy.deepcopy(first)},
        ]
    groups = []
    seen = set()
    for zoom_index in range(min(len(scene["zooms"]) for scene in scenes)):
        for offset, name in [
            (0, "first" if zoom_index == 0 else "zoom"),
            (1, "pan"),
            (2, "pan"),
            (0, "revisit"),
        ]:
            jobs = []
            viewport = set()
            for scene in scenes:
                zoom = scene["zooms"][zoom_index]
                cx, cy = tile(*scene["center"], zoom)
                for dy in range(-(args.viewport // 2), args.viewport // 2 + 1):
                    for dx in range(-(args.viewport // 2), args.viewport // 2 + 1):
                        x, y = cx + offset + dx, cy + dy
                        for channel, key in scene["bands"].items():
                            ident = f"{scene['id']}/{zoom}/{x}/{y}/{channel}"
                            viewport.add(ident)
                            if name == "pan" and ident in seen:
                                continue
                            bands = [scene.get("band_indexes", {}).get(channel, 1)]
                            ranges = [scene["range"]]
                            # Same-file RGB measures shared decoding/warp and the actual RCFP RGB encoder.
                            if scene.get("probe_rgb"):
                                if channel != "red":
                                    continue
                                bands, ranges = [1, 2, 3], [scene["range"]] * 3
                            jobs.append(
                                {
                                    "id": ident,
                                    "source": key,
                                    "mode": "tile",
                                    "bounds": bounds_for_tile(x, y, zoom),
                                    "size": [256, 256],
                                    "bands": bands,
                                    "ranges": ranges,
                                    "resampling": "nearest"
                                    if scene.get("categorical")
                                    else args.resampling,
                                    "overview_bias": args.overview_bias,
                                }
                            )
            seen = viewport
            groups.append({"name": name, "jobs": jobs})
    return groups


def oracle(groups, paths, destination):
    """Public GDAL/Rasterio reads supply independent expectations, never RCFP outputs."""
    result = {}
    destination.mkdir()
    for group in groups:
        for job in group["jobs"]:
            ident = job["id"]
            if ident in result:
                continue
            path = paths[job["source"]]
            if job["mode"] == "metadata":
                with rasterio.open(path) as src:
                    result[ident] = {
                        "metadata": {
                            "width": src.width,
                            "height": src.height,
                            "bands": src.count,
                            "transform": list(src.transform.to_gdal()),
                            "epsg": src.crs.to_epsg() if src.crs else None,
                        },
                        "job": job,
                    }
                continue
            values, masks = [], []
            if job["mode"] == "window":
                options = (
                    {"OVERVIEW_LEVEL": job.get("overview", 0) - 1}
                    if job.get("overview", 0)
                    else {}
                )
                with rasterio.open(path, **options) as src:
                    window = Window(*job["window"])
                    values = src.read(job["bands"], window=window, out_dtype="float64")
                    masks = src.read_masks(job["bands"], window=window) == 0
                    alpha = next(
                        (
                            i
                            for i, c in enumerate(src.colorinterp, 1)
                            if c.name == "alpha"
                        ),
                        None,
                    )
                    if alpha:
                        masks |= src.read(alpha, window=window) <= 0
                    masks |= ~np.isfinite(values)
            else:
                method = (
                    Resampling.nearest
                    if job["resampling"] == "nearest"
                    else Resampling.bilinear
                )
                for band in job["bands"]:
                    vrt = destination / (
                        hashlib.sha256(f"{ident}/{band}".encode()).hexdigest() + ".vrt"
                    )
                    value, valid = expected(
                        path, job["bounds"], tuple(job["size"]), method, vrt, band=band
                    )
                    values.append(value)
                    masks.append(~valid)
            values, masks = np.asarray(values), np.asarray(masks, dtype=bool)
            with rasterio.open(path) as src:
                data_types = [GDAL_TYPES[src.dtypes[band - 1]] for band in job["bands"]]
            np.savez(
                destination / (hashlib.sha256(ident.encode()).hexdigest() + ".npz"),
                values=values,
                masks=masks,
            )
            result[ident] = {
                "values": values,
                "masks": masks,
                "job": job,
                "data_types": data_types,
            }
    return result


def probes(paths, identities, destination):
    """Real Landsat values and existing validity produce tiled, RGBA and Mercator COGs."""
    destination.mkdir()
    source = paths["rgb-byte"]
    output_paths, output_identities = {}, {}
    with rasterio.open(source) as src:
        values, validity = src.read(), src.dataset_mask()
        profile = src.profile.copy()
        for kind in ("rgb", "rgba", "mercator"):
            key = f"landsat-{kind}"
            intermediate = destination / f"{key}-source.tif"
            path = destination / f"{key}.tif"
            pixels, mask, options = values, validity, profile.copy()
            options.update(
                driver="GTiff",
                tiled=True,
                blockxsize=256,
                blockysize=256,
                compress="deflate",
            )
            if kind == "rgba":
                pixels = np.concatenate([values, validity[None]], axis=0)
                options.update(count=4, nodata=None)
            elif kind == "mercator":
                transform, width, height = calculate_default_transform(
                    src.crs, "EPSG:3857", src.width, src.height, *src.bounds
                )
                pixels = np.zeros((src.count, height, width), dtype=values.dtype)
                mask = np.zeros((height, width), dtype=np.uint8)
                for band in range(src.count):
                    reproject(
                        values[band],
                        pixels[band],
                        src_transform=src.transform,
                        src_crs=src.crs,
                        dst_transform=transform,
                        dst_crs="EPSG:3857",
                        src_nodata=src.nodata,
                        dst_nodata=src.nodata,
                        resampling=Resampling.nearest,
                    )
                reproject(
                    validity,
                    mask,
                    src_transform=src.transform,
                    src_crs=src.crs,
                    dst_transform=transform,
                    dst_crs="EPSG:3857",
                    resampling=Resampling.nearest,
                )
                options.update(
                    crs="EPSG:3857", transform=transform, width=width, height=height
                )
            with rasterio.Env(GDAL_TIFF_INTERNAL_MASK=True):
                with rasterio.open(intermediate, "w", **options) as dst:
                    dst.write(pixels)
                    if kind == "rgba":
                        dst.colorinterp = (
                            ColorInterp.red,
                            ColorInterp.green,
                            ColorInterp.blue,
                            ColorInterp.alpha,
                        )
                    else:
                        dst.write_mask(mask)
                raster_copy(
                    intermediate,
                    path,
                    driver="COG",
                    BLOCKSIZE=256,
                    COMPRESS="DEFLATE",
                    OVERVIEW_RESAMPLING="NEAREST",
                    OVERVIEW_COUNT=2,
                )
            intermediate.unlink()
            output_paths[key] = path
            output_identities[key] = {
                "file": str(path),
                "path": str(path),
                "sha256": digest(path),
                "bytes": path.stat().st_size,
                "derived_from": identities["rgb-byte"]["sha256"],
                "attribution": identities["rgb-byte"]["attribution"],
                "recipe": {
                    "kind": kind,
                    "block_size": 256,
                    "overviews": 2,
                    "resampling": "nearest",
                    "mask": "existing source dataset mask",
                },
            }
    scenes = [
        {
            "id": key,
            "bands": {"red": key},
            "center": [-78.5, 24.5],
            "zooms": [8, 9, 10],
            "range": [0, 255],
            "probe_rgb": True,
        }
        for key in output_paths
    ]
    write(
        destination / "manifest.json", {"sources": output_identities, "scenes": scenes}
    )
    return scenes, output_paths, output_identities


def quality(report, expected_values, folder):
    checks = []
    for group in report["groups"]:
        for row in group["jobs"]:
            if row["status"] != "ok":
                checks.append(
                    {
                        "id": row["id"],
                        "status": "unavailable",
                        "error": row.get("error"),
                    }
                )
                continue
            target = expected_values[row["id"]]
            if "metadata" in target:
                actual = row["metadata"]
                fields = ("width", "height", "bands")
                equal = all(actual.get(k) == target["metadata"][k] for k in fields)
                if (
                    actual.get("transform") is not None
                    and target["metadata"]["transform"] is not None
                ):
                    equal &= bool(
                        np.allclose(
                            actual["transform"],
                            target["metadata"]["transform"],
                            rtol=0,
                            atol=1e-9,
                        )
                    )
                if "epsg" in actual:
                    equal &= actual["epsg"] == target["metadata"]["epsg"]
                checks.append(
                    {
                        "id": row["id"],
                        "status": "pass" if equal else "fail",
                        "kind": "metadata",
                    }
                )
                continue
            artifact = folder / row["artifact"]
            shape = tuple(row["shape"])
            actual = np.fromfile(artifact.with_suffix(".f64"), dtype="<f8").reshape(
                shape
            )
            mask = (
                np.fromfile(artifact.with_suffix(".mask"), dtype=np.uint8)
                .reshape(shape)
                .astype(bool)
            )
            if actual.shape != target["values"].shape:
                checks.append({"id": row["id"], "status": "fail", "kind": "shape"})
                continue
            mismatch = int(np.count_nonzero(mask != target["masks"]))
            valid = ~mask & ~target["masks"]
            delta = np.abs(actual[valid] - target["values"][valid])
            unequal = int(np.count_nonzero(delta))
            dtype_match = row["data_types"] == target["data_types"]
            decoded = np.asarray(
                Image.open(artifact.with_suffix(".png")).convert("RGBA")
            )
            job = target["job"]
            channels = []
            for band, stretch in enumerate(job["ranges"]):
                low, high = stretch
                stretched = target["values"][band] - low
                if abs(high - low) > 1e-8:
                    stretched *= 1.0 / (high - low)
                channel = np.rint(1 + np.clip(stretched, 0, 1) * 254)
                channel[target["masks"][band]] = 0
                channels.append(channel.astype(np.uint8))
            rgb = np.stack(channels if len(channels) == 3 else channels * 3, axis=-1)
            alpha = np.where(np.any(rgb != 0, axis=-1), 255, 0).astype(np.uint8)
            rgba = np.concatenate([rgb, alpha[..., None]], axis=-1)
            if decoded.shape != rgba.shape:
                checks.append(
                    {"id": row["id"], "status": "fail", "kind": "render_shape"}
                )
                continue
            rendered_delta = np.abs(
                decoded[..., :3].astype(np.int16) - rgba[..., :3].astype(np.int16)
            )
            alpha_difference = int(np.count_nonzero(decoded[..., 3] != alpha))
            checks.append(
                {
                    "id": row["id"],
                    "status": "pass"
                    if dtype_match
                    and mismatch == 0
                    and unequal == 0
                    and alpha_difference == 0
                    and not rendered_delta.any()
                    else "difference",
                    "kind": "raster",
                    "mask_pixels_different": mismatch,
                    "valid_values_different": unequal,
                    "data_types_match": dtype_match,
                    "common_valid_pixels": int(valid.sum()),
                    "max_abs_value_difference": float(delta.max())
                    if delta.size
                    else None,
                    "p99_abs_value_difference": float(np.percentile(delta, 99))
                    if delta.size
                    else None,
                    "mean_abs_value_difference": float(delta.mean())
                    if delta.size
                    else None,
                    "decoded_dimensions": list(decoded.shape[:2]),
                    "alpha_pixels_different": alpha_difference,
                    "max_rgb_difference": int(rendered_delta.max()),
                    "p99_rgb_difference": float(np.percentile(rendered_delta, 99)),
                    "mean_rgb_difference": float(rendered_delta.mean()),
                }
            )
    counts = {
        status: sum(c["status"] == status for c in checks)
        for status in ("pass", "fail", "difference", "unavailable")
    }
    return {"counts": counts, "checks": checks}


def execute(binary, config, env, cpus, folder, origin_counts):
    folder.mkdir()
    config = dict(config, out=str(folder / "artifacts"))
    write(folder / "config.json", config)
    command = [str(binary), str(folder / "config.json")]
    if cpus:
        command = ["taskset", "-c", ",".join(map(str, cpus)), *command]
    before = dict(origin_counts)
    cpu_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    peak = {
        "pss_bytes": 0,
        "uss_bytes": 0,
        "rss_bytes": 0,
        "cpu_seconds": 0.0,
        "threads": 0,
    }
    samples = []
    started = time.perf_counter()
    with (
        (folder / "stdout.json").open("w") as stdout,
        (folder / "stderr.log").open("w") as stderr,
    ):
        process = subprocess.Popen(command, env=env, stdout=stdout, stderr=stderr)
        child = psutil.Process(process.pid)
        deadline = time.monotonic() + 300
        while process.poll() is None:
            if time.monotonic() > deadline:
                process.kill()
                process.wait()
                raise TimeoutError(f"probe timed out: {folder}")
            try:
                memory = child.memory_full_info()
                cpu = child.cpu_times()
                sample = {
                    "ms": (time.perf_counter() - started) * 1000,
                    "pss_bytes": memory.pss,
                    "uss_bytes": memory.uss,
                    "rss_bytes": memory.rss,
                    "cpu_seconds": cpu.user + cpu.system,
                    "threads": child.num_threads(),
                }
                samples.append(sample)
                for key, previous in peak.items():
                    peak[key] = max(previous, sample[key])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
            time.sleep(0.01)
    elapsed = (time.perf_counter() - started) * 1000
    write(folder / "resources.json", samples)
    traffic = {key: origin_counts[key] - before[key] for key in before}
    if process.returncode:
        return {
            "status": "error",
            "exit_code": process.returncode,
            "folder": str(folder),
            "traffic": traffic,
        }
    report = json.loads((folder / "stdout.json").read_text())
    cpu_after = resource.getrusage(resource.RUSAGE_CHILDREN)
    report.update(
        status="ok",
        wall_ms=elapsed,
        resources_peak=peak,
        traffic=traffic,
        cpu_seconds=cpu_after.ru_utime
        + cpu_after.ru_stime
        - cpu_before.ru_utime
        - cpu_before.ru_stime,
    )
    return report


def summarize(cells):
    summaries = []
    keys = sorted(
        {(c["layout"], c["storage"], c["workload"], c["engine"]) for c in cells}
    )
    for layout, storage, workload, engine in keys:
        selected = [
            c
            for c in cells
            if (c["layout"], c["storage"], c["workload"], c["engine"])
            == (layout, storage, workload, engine)
        ]
        good = [c for c in selected if c["status"] == "ok"]
        phases = []
        for name in sorted({g["name"] for c in good for g in c["groups"]}):
            paired, completions, latencies = [], [], []
            for cell in good:
                reference = next(
                    (
                        c
                        for c in cells
                        if c["engine"] == "rcfp"
                        and c["trial"] == cell["trial"]
                        and c["layout"] == layout
                        and c["storage"] == storage
                        and c["workload"] == workload
                        and c["status"] == "ok"
                    ),
                    None,
                )
                for index, group in enumerate(cell["groups"]):
                    if group["name"] != name:
                        continue
                    valid = all(row["status"] == "ok" for row in group["jobs"])
                    if not valid:
                        continue
                    completions.append(group["completion_ms"])
                    latencies.extend(row["completed_ms"] for row in group["jobs"])
                    if reference and all(
                        r["status"] == "ok" for r in reference["groups"][index]["jobs"]
                    ):
                        paired.append(
                            reference["groups"][index]["completion_ms"]
                            / group["completion_ms"]
                        )
            phases.append(
                {
                    "name": name,
                    "completed_groups": len(completions),
                    "median_completion_ms": statistics.median(completions)
                    if completions
                    else None,
                    "paired_baseline_over_candidate": statistics.median(paired)
                    if paired
                    else None,
                    "request_latency_ms": quantiles(latencies),
                }
            )
        summaries.append(
            {
                "layout": layout,
                "storage": storage,
                "workload": workload,
                "engine": engine,
                "cells": len(selected),
                "executed": len(good),
                "phases": phases,
                "quality": {
                    k: sum(
                        c.get("quality", {}).get("counts", {}).get(k, 0) for c in good
                    )
                    for k in ("pass", "fail", "difference", "unavailable")
                },
                "median_peak_pss_mib": statistics.median(
                    c["resources_peak"]["pss_bytes"] / MIB for c in good
                )
                if good
                else None,
                "median_source_gets": statistics.median(
                    c["traffic"]["gets"] for c in good
                )
                if good
                else None,
                "median_source_bytes": statistics.median(
                    c["traffic"]["bytes"] for c in good
                )
                if good
                else None,
                "median_cpu_seconds": statistics.median(c["cpu_seconds"] for c in good)
                if good
                else None,
                "median_startup_ms": statistics.median(c["startup_ms"] for c in good)
                if good
                else None,
            }
        )
    return summaries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--manifest", type=Path, default=ROOT / "tests/fixtures/navigation.json"
    )
    parser.add_argument("--prepared-manifest", type=Path)
    parser.add_argument("--sources", type=Path, default=ROOT / ".cache/corpus")
    parser.add_argument(
        "--scenes",
        nargs="+",
        default=["imagery", "elevation", "classes", "south", "landsat"],
    )
    parser.add_argument("--engines", nargs="+", choices=ENGINES, default=list(ENGINES))
    parser.add_argument(
        "--workloads",
        nargs="+",
        choices=("metadata", "windows", "tiles"),
        default=["metadata", "windows", "tiles"],
    )
    parser.add_argument(
        "--storage",
        nargs="+",
        choices=("local", "range", "public"),
        default=["local", "range"],
    )
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--inflight", type=int, default=16)
    parser.add_argument("--io-concurrency", type=int, default=16)
    parser.add_argument("--cache-mib", type=int, default=64)
    parser.add_argument("--curl-cache-mib", type=int, default=16)
    parser.add_argument("--merge-gap", type=int, default=65536)
    parser.add_argument("--latency-ms", type=float, default=15)
    parser.add_argument("--viewport", type=int, choices=(1, 3), default=3)
    parser.add_argument(
        "--resampling", choices=("nearest", "bilinear"), default="bilinear"
    )
    parser.add_argument("--overview-bias", type=float, default=1.0)
    parser.add_argument(
        "--no-probes",
        action="store_true",
        help="Omit small Landsat mask/RGB/Mercator derivatives",
    )
    parser.add_argument(
        "--cpus",
        help="Comma-separated serving CPU IDs; defaults to four physical cores",
    )
    args = parser.parse_args()
    if (
        args.trials < 1
        or args.workers < 1
        or args.cache_mib < 0
        or args.latency_ms < 0
        or args.overview_bias <= 0
    ):
        parser.error("invalid trial, worker, cache, latency or overview setting")
    args.binary, args.out = args.binary.resolve(), args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=False)
    if args.cpus:
        cpus = list(map(int, args.cpus.split(",")))
    else:
        from service_benchmark import cpu_allocation

        cpus, _ = cpu_allocation()
    available = set(os.sched_getaffinity(0))
    client_cpus = sorted(available - set(cpus))
    if client_cpus:
        os.sched_setaffinity(0, client_cpus)
    env = dict(
        os.environ,
        RCFP_COMPAT_PROFILE="native",
        GDAL_CACHEMAX=str(args.cache_mib),
        CPL_VSIL_CURL_CACHE_SIZE=str(args.curl_cache_mib * MIB),
        GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
        TC_NATIVE_DIRECT_READ="true",
        TC_NATIVE_WARP_PLAN="true",
        TC_REMOTE_SOURCE_CACHE_TTL_SECONDS="60",
        TC_RENDER_CACHE_BYTES="0",
        TC_RASTER_CACHE_BYTES="0",
        TC_RUST_UTM_FAST="false",
        TC_RUST_BLOCK_CACHE_BYTES="0",
        TC_COMPACT_RASTER_CACHE="false",
        TC_PNG_COMPRESSION_LEVEL="1",
        GDAL_NUM_THREADS="1",
        PROJ_NETWORK="OFF",
    )
    gdal_path = Path(env["TERRACOTTA_GDAL_LIBRARY"]).resolve()
    proj_path = Path(env["RCFP_PROBE_PROJ_LIBRARY"]).resolve()
    manifests = [("original", args.manifest)]
    if args.prepared_manifest:
        manifests.append(("prepared", args.prepared_manifest))
    report = {
        "binary_sha256": digest(args.binary),
        "lock_sha256": digest(ROOT / "tests/benchmarks/async-tiff/Cargo.lock"),
        "platform": platform.platform(),
        "cpu_affinity": cpus,
        "client_cpu_affinity": client_cpus,
        "gdal_sha256": digest(gdal_path),
        "proj_sha256": digest(proj_path),
        "rasterio_version": rasterio.__version__,
        "settings": vars(args)
        | {
            "binary": str(args.binary),
            "out": str(args.out),
            "manifest": str(args.manifest),
            "sources": str(args.sources),
            "prepared_manifest": str(args.prepared_manifest)
            if args.prepared_manifest
            else None,
        },
        "cells": [],
        "inputs": {},
        "exclusions": [
            "HTTP/catalog/admission overhead",
            "Terracotta profile",
            "physical cold disk",
            "real S3/private credentials",
            "general preview/metadata statistics",
            "cubic/average warp kernels",
            "cross-platform artifacts",
            "three-source RGB composition and expressions",
        ],
        "probe_sources": {
            str(p.relative_to(ROOT)): digest(p)
            for p in (ROOT / "tests/benchmarks/async-tiff").rglob("*")
            if p.is_file()
        },
        "oracle_sources": {
            str(p.relative_to(ROOT)): digest(p)
            for p in [Path(__file__), ROOT / "tests/native_rendering.py"]
        },
        "core_sources": {
            str(p.relative_to(ROOT)): digest(p) for p in (ROOT / "src").rglob("*.rs")
        },
        "notes": [
            "Controlled range latency is a surrogate, not actual S3 evidence. Public storage uses manifest URLs; only the local copies are verified against full-file hashes.",
            "GDAL splits the configured cache budget between decoded blocks and compressed curl bytes. Async allocates the same total to decoded blocks. Inspect PSS and transient buffers separately.",
            "Artifacts are written after each timed group; process wall time and memory include artifact handling.",
            "Request latency starts at the batch request, including waiting for admission. Stage timers include semaphore waits and can overlap across jobs.",
            "Sampling differences are reported independently and never accepted automatically.",
            "Original and prepared layouts are paired within layout; preprocessing gains are not credited to a backend.",
        ],
    }
    layouts = [
        (layout, *inputs(manifest, args.sources, args.scenes))
        for layout, manifest in manifests
    ]
    if not args.no_probes and "rgb-byte" in layouts[0][2]:
        layouts.append(
            ("probes", *probes(layouts[0][2], layouts[0][3], args.out / "fixtures"))
        )
    for layout, scenes, paths, identities in layouts:
        report["inputs"][layout] = identities
        for workload in args.workloads:
            groups = groups_for(workload, scenes, paths, args)
            oracle_folder = args.out / f"oracle-{layout}-{workload}"
            expectations = oracle(groups, paths, oracle_folder)
            for storage in args.storage:
                if storage == "public" and any(
                    not row.get("url") for row in identities.values()
                ):
                    report.setdefault("skips", []).append(
                        {
                            "layout": layout,
                            "storage": storage,
                            "workload": workload,
                            "reason": "manifest has no public URLs",
                        }
                    )
                    continue
                server = (
                    range_server(
                        {f"/{key}.tif": path for key, path in paths.items()},
                        args.latency_ms,
                    )
                    if storage == "range"
                    else contextlib.nullcontext(
                        (None, {"gets": 0, "heads": 0, "range_gets": 0, "bytes": 0})
                    )
                )
                with server as (origin, counts):
                    for trial in range(args.trials):
                        order = (
                            args.engines[trial % len(args.engines) :]
                            + args.engines[: trial % len(args.engines)]
                        )
                        for engine in order:
                            folder = (
                                args.out
                                / f"{layout}-{storage}-{workload}-{trial}-{engine}"
                            )
                            jobs = copy.deepcopy(groups)
                            for group in jobs:
                                for job in group["jobs"]:
                                    key = job["source"]
                                    job["source"] = (
                                        str(paths[key])
                                        if storage == "local"
                                        else identities[key]["url"]
                                        if storage == "public"
                                        else f"{origin}/{key}.tif"
                                    )
                            config = {
                                "engine": engine,
                                "workers": args.workers,
                                "inflight": args.inflight,
                                "io_concurrency": args.io_concurrency,
                                "cache_bytes": (args.cache_mib + args.curl_cache_mib)
                                * MIB,
                                "merge_gap": args.merge_gap,
                                "groups": jobs,
                            }
                            cell = execute(
                                args.binary, config, env, cpus, folder, counts
                            )
                            cell.update(
                                layout=layout,
                                storage=storage,
                                workload=workload,
                                trial=trial,
                                engine=engine,
                                folder=str(folder),
                            )
                            if cell["status"] == "ok":
                                cell["quality"] = quality(
                                    cell, expectations, folder / "artifacts"
                                )
                            report["cells"].append(cell)
                            write(args.out / "report.json", report)
                            print(
                                f"{layout} {storage} {workload} trial={trial + 1} {engine}: {cell['status']} {cell.get('quality', {}).get('counts', {})}",
                                flush=True,
                            )
    report["summary"] = summarize(report["cells"])
    write(args.out / "report.json", report)
    write(args.out / "summary.json", report["summary"])
    failures = [
        c
        for c in report["cells"]
        if c["status"] != "ok"
        or (
            c["engine"] == "rcfp"
            and c.get("quality", {}).get("counts", {}).get("fail", 0)
        )
    ]
    if failures:
        raise SystemExit("execution or baseline validation failed; inspect report.json")


if __name__ == "__main__":
    main()
