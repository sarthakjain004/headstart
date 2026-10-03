"""Sweep all enumerable ATSes and audit company-domain providers' known hosts.

Output is candidate-grade, not a liveness verdict. Unknown company-domain Boards
are outside a host sweep's reach. A dated JSON report distinguishes complete,
incomplete and exempt scrapers, including successful empty responses. Restart
with the same --report and --since to retry only unfinished work.
"""

import argparse
import json
import time
from pathlib import Path
from urllib.parse import urlencode

from archive_targets import KNOWN_HOST_ATS, SINGLE_SOURCE_ATS, known_hosts
from wayback_feeder import ATS_HOSTS, FetchError, fetch, slug_sink
from wayback_paginate import sweep

from headstart import log
from headstart.network import http, spare_egress


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", help="YYYYMMDD; omitted = full archive history")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    log.setup()
    report = (
        json.loads(args.report.read_text())
        if args.report.exists()
        else {
            "since": args.since,
            "targets": {},
            "exempt_single_source": sorted(SINGLE_SOURCE_ATS),
        }
    )
    if report["since"] != args.since:
        parser.error("the report belongs to a different --since window")

    def save(key, status, **details):
        report["targets"][key] = {"status": status, **details}
        report["retry_stats"] = dict(http.retry_stats())
        report["egress_traffic"] = spare_egress.traffic()
        args.report.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.report.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, indent=2), encoding="utf-8")
        temporary.replace(args.report)

    for ats, hosts in ATS_HOSTS.items():
        with slug_sink(ats) as sink:
            for domain, style in hosts:
                key = f"{ats}|{domain}"
                previous = report["targets"].get(key, {})
                if previous.get("status") == "complete":
                    continue
                save(key, "running", mode="namespace")
                complete = sweep(
                    ats,
                    domain,
                    style,
                    0,
                    None,
                    sink,
                    refresh=not previous,
                    since=args.since,
                )
                save(key, "complete" if complete else "incomplete", mode="namespace")

    for ats in sorted(KNOWN_HOST_ATS):
        hosts = known_hosts(ats)
        print(
            f"[{ats}] Wayback archive-presence audit: {len(hosts)} known hosts",
            flush=True,
        )
        for number, host in enumerate(hosts, 1):
            key = f"{ats}|{host}"
            if report["targets"].get(key, {}).get("status") == "complete":
                continue
            query = {"url": host, "matchType": "host", "fl": "original", "limit": 1}
            if args.since:
                query["from"] = args.since
            try:
                body = fetch(
                    "https://web.archive.org/cdx/search/cdx?" + urlencode(query)
                )
            except FetchError as error:
                save(key, "incomplete", mode="known-host", error=str(error))
            else:
                readable = not body.strip() or body.strip().startswith(
                    ("http://", "https://")
                )
                save(
                    key,
                    "complete" if readable else "incomplete",
                    mode="known-host",
                    has_capture=bool(body.strip()) if readable else None,
                )
            if number % 100 == 0:
                print(f"[{ats}] {number}/{len(hosts)} known hosts checked", flush=True)
        time.sleep(1)
    incomplete = [
        key
        for key, result in report["targets"].items()
        if result["status"] != "complete"
    ]
    print(
        f"Wayback: {len(report['targets'])} targets; {len(incomplete)} incomplete",
        flush=True,
    )
    spare_egress.report()
    return 3 if incomplete else 0


if __name__ == "__main__":
    raise SystemExit(main())
