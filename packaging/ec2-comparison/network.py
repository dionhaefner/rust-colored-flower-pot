"""Apply a recorded network delay to one server on a disposable browser host."""

import argparse
import ipaddress
import json
import subprocess
from pathlib import Path


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-ip", required=True)
    parser.add_argument("--delay-ms", type=int, default=20)
    parser.add_argument("--rate-mbit", type=int, default=1000)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    address = ipaddress.ip_address(args.server_ip)
    if address.version != 4 or not address.is_global:
        parser.error("server IP must be a public IPv4 address")
    route = json.loads(run("ip", "-j", "route", "get", str(address)))[0]
    interface = route["dev"]
    before = json.loads(run("tc", "-j", "qdisc", "show", "dev", interface))
    run("modprobe", "ifb")
    run("modprobe", "sch_netem")
    result = subprocess.run(
        ["ip", "link", "show", "rcfp-ifb"], capture_output=True, check=False
    )
    if result.returncode:
        run("ip", "link", "add", "rcfp-ifb", "type", "ifb")
    run("ip", "link", "set", "rcfp-ifb", "up")
    run(
        "tc",
        "qdisc",
        "replace",
        "dev",
        interface,
        "root",
        "handle",
        "1:",
        "prio",
        "bands",
        "3",
    )
    run(
        "tc",
        "qdisc",
        "replace",
        "dev",
        interface,
        "parent",
        "1:3",
        "handle",
        "30:",
        "netem",
        "delay",
        f"{args.delay_ms}ms",
        "rate",
        f"{args.rate_mbit}mbit",
        "limit",
        "10000",
    )
    run(
        "tc",
        "filter",
        "replace",
        "dev",
        interface,
        "parent",
        "1:",
        "protocol",
        "ip",
        "prio",
        "1",
        "u32",
        "match",
        "ip",
        "dst",
        str(address) + "/32",
        "flowid",
        "1:3",
    )
    if not any(
        q.get("kind") == "ingress"
        for q in json.loads(run("tc", "-j", "qdisc", "show", "dev", interface))
    ):
        run("tc", "qdisc", "add", "dev", interface, "handle", "ffff:", "ingress")
    run(
        "tc",
        "qdisc",
        "replace",
        "dev",
        "rcfp-ifb",
        "root",
        "netem",
        "delay",
        f"{args.delay_ms}ms",
        "rate",
        f"{args.rate_mbit}mbit",
        "limit",
        "10000",
    )
    run(
        "tc",
        "filter",
        "replace",
        "dev",
        interface,
        "parent",
        "ffff:",
        "protocol",
        "ip",
        "prio",
        "1",
        "u32",
        "match",
        "ip",
        "src",
        str(address) + "/32",
        "action",
        "mirred",
        "egress",
        "redirect",
        "dev",
        "rcfp-ifb",
    )
    report = {
        "server_ip": str(address),
        "interface": interface,
        "delay_ms_per_direction": args.delay_ms,
        "aggregate_rate_mbit": args.rate_mbit,
        "before": before,
        "egress": json.loads(run("tc", "-j", "qdisc", "show", "dev", interface)),
        "ingress": json.loads(run("tc", "-j", "qdisc", "show", "dev", "rcfp-ifb")),
        "filters": json.loads(
            run("tc", "-j", "filter", "show", "dev", interface, "parent", "1:")
        ),
        "ingress_filters": json.loads(
            run("tc", "-j", "filter", "show", "dev", interface, "parent", "ffff:")
        ),
    }
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                k: report[k]
                for k in (
                    "server_ip",
                    "interface",
                    "delay_ms_per_direction",
                    "aggregate_rate_mbit",
                )
            }
        )
    )


if __name__ == "__main__":
    main()
