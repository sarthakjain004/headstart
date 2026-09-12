#!/usr/bin/env python3
"""Probe historical Workday JSON-decode Boards from a runner, one listing page each.

Four workflow replicas partition the inventory. Each requested arm runs the same partition:
direct, one fixed WARP route, WARP with one rotation+retry only after a positively classified
non-JSON response, or the exact production retry/429-wall route. Results stream to JSONL and a
summary is rewritten after every completion.
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import csv
import hashlib
import json
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from headstart import http, spare_egress
from headstart.scrapers.base import USER_AGENT


_URL = re.compile(
    r"^https://(?P<company>[^.]+)\.(?P<pod>wd\d+)\.myworkdayjobs\.com/"
    r"(?P<site>[^/?#]+)"
)
_HEADERS = {
    "User-Agent": USER_AGENT,
    "Content-Type": "application/json",
    "Accept": "application/json",
}
_BODY = {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}
_PREFIX_BYTES = 320
_SENSITIVE = re.compile(
    r"(?i)(authorization|api[_-]?key|access[_-]?token|password|secret)"
    r"(\s*[:=]\s*)([^\s<>&;,]+)"
)


def _proxy(proxy: str | None) -> dict:
    return {"proxies": {"http": proxy, "https": proxy}} if proxy else {}


def _classification(headers: dict, body: bytes) -> tuple[str, bool]:
    text = body[:4096].decode("utf-8", errors="replace").lower()
    lowered = {str(k).lower(): str(v).lower() for k, v in headers.items()}
    if lowered.get("cf-mitigated") == "challenge" or "<title>just a moment" in text:
        return "challenge", True
    if "<title>maintenance" in text or (
        "temporarily unavailable" in text and "<html" in text
    ):
        return "maintenance", True
    if "graphicscontainer" in text and "wdaylogo" in text:
        return "workday-error-page", True
    return "unexpected-body", False


def _bounded(response) -> dict:
    body = response.content
    parsed = urlsplit(str(response.url))
    final_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    prefix = " ".join(
        body[:_PREFIX_BYTES].decode("utf-8", errors="replace").replace("\x00", " ").split()
    )
    prefix = _SENSITIVE.sub(r"\1\2[redacted]", prefix)
    classification, transient = _classification(dict(response.headers), body)
    return {
        "status": response.status_code,
        "content_type": response.headers.get("content-type"),
        "final_url": final_url,
        "bytes": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
        "body_prefix": prefix,
        "classification": classification,
        "classified_transient": transient,
    }


def _request(url: str, proxy: str | None, *, production: bool = False):
    match = _URL.match(url)
    if not match:
        raise ValueError(f"unparseable Workday URL: {url}")
    company, pod, site = match.group("company", "pod", "site")
    endpoint = f"https://{company}.{pod}.myworkdayjobs.com/wday/cxs/{company}/{site}/jobs"
    route = _proxy(proxy)
    retry_on = frozenset()
    if production:
        retry_on = http.TRANSIENT
        route = {
            "egress_group": "workday",
            "egress_on": frozenset({429}),
            "egress_board": url,
        }
    response = http.fetch(
        "POST",
        endpoint,
        json=_BODY,
        headers=_HEADERS,
        timeout=30,
        retry_on=retry_on,
        **route,
    )
    return endpoint, response


def _probe(url: str, arm: str, proxy: str | None) -> dict:
    row = {"url": url, "arm": arm, "observed_at": datetime.now(UTC).isoformat()}
    try:
        endpoint, response = _request(
            url, proxy, production=arm in {"production", "production-walled"}
        )
        row["endpoint"] = endpoint
        if arm in {"production", "production-walled"}:
            try:
                response.raise_for_status()
            except Exception as exc:
                row.update(
                    status=response.status_code,
                    content_type=response.headers.get("content-type"),
                    error=f"{type(exc).__name__}: {exc}",
                )
                return row
        try:
            payload = response.json()
        except ValueError as exc:
            row.update(json=False, error=type(exc).__name__, **_bounded(response))
            if arm == "rotating-warp" and row["classified_transient"]:
                before = dict(spare_egress.egress_ips())
                rotated = spare_egress.rotate(url)
                endpoint, retry = _request(url, proxy)
                try:
                    retry_payload = retry.json()
                except ValueError as retry_exc:
                    retry_result = {
                        "json": False,
                        "error": type(retry_exc).__name__,
                        **_bounded(retry),
                    }
                else:
                    retry_result = {
                        "json": True,
                        "total": retry_payload.get("total")
                        if isinstance(retry_payload, dict)
                        else None,
                    }
                row.update(
                    rotated=rotated,
                    egress_before=before,
                    egress_after=dict(spare_egress.egress_ips()),
                    retry=retry_result,
                )
        else:
            row.update(
                json=True,
                status=response.status_code,
                content_type=response.headers.get("content-type"),
                total=payload.get("total") if isinstance(payload, dict) else None,
            )
    except Exception as exc:  # noqa: BLE001 - every terminal observation is probe output
        row["error"] = f"{type(exc).__name__}: {exc}"
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--boards", type=Path, required=True)
    parser.add_argument("--replica", type=int, required=True)
    parser.add_argument("--replicas", type=int, default=4)
    parser.add_argument("--width", type=int, default=12)
    parser.add_argument(
        "--arms", default="direct,warp,rotating-warp,production,production-walled"
    )
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    with args.boards.open(encoding="utf-8") as handle:
        all_urls = [row["url"] for row in csv.DictReader(handle)]
    urls = [
        url for index, url in enumerate(all_urls) if index % args.replicas == args.replica - 1
    ]
    arms = [arm.strip() for arm in args.arms.split(",") if arm.strip()]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    summary_path = args.out.with_suffix(".summary.json")
    summary: dict = {
        "replica": args.replica,
        "replicas": args.replicas,
        "boards": len(urls),
        "width": args.width,
        "arms": {},
    }

    with args.out.open("w", encoding="utf-8") as output:
        for arm in arms:
            proxy = None
            if arm in {"warp", "rotating-warp"}:
                proxy = spare_egress.proxy_url()
            if arm == "production-walled":
                # Recreate a shard after any earlier Workday request has returned 429: every
                # later Workday request inherits WARP, and WARP 429s drive the ordinary rotation
                # ladder. This is stateful at ATS scope in production, not per Board.
                spare_egress.reset()
                spare_egress.mark_walled("workday", 429)
            counts: Counter[str] = Counter()
            print(f"arm={arm} boards={len(urls)} proxy={proxy or 'none'}", flush=True)
            with futures.ThreadPoolExecutor(max_workers=args.width) as pool:
                pending = [pool.submit(_probe, url, arm, proxy) for url in urls]
                for done, future in enumerate(futures.as_completed(pending), 1):
                    row = future.result()
                    verdict = "json" if row.get("json") else row.get("classification", "raised")
                    counts[verdict] += 1
                    if row.get("retry", {}).get("json"):
                        counts["recovered_after_rotation"] += 1
                    output.write(json.dumps(row, ensure_ascii=False) + "\n")
                    output.flush()
                    summary["arms"][arm] = dict(counts)
                    summary["egress_ips"] = dict(spare_egress.egress_ips())
                    summary["rotations"] = dict(spare_egress.rotations())
                    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
                    if done % 100 == 0 or verdict != "json":
                        print(f"  [{done}/{len(urls)}] {verdict} {row['url']}", flush=True)
    print(json.dumps(summary, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
