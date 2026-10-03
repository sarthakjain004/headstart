#!/usr/bin/env python3
"""Bounded, resumable Recruiterflow URL-index discovery without proxy changes.

Wayback covers the shared careers host; Common Crawl reads current-to-oldest
indexes within --years. Each complete page is saved and merged into this ATS's
candidate pool before its checkpoint is written. A failed/capped page remains
unfinished, and the command stops on source failure rather than treating it as
empty. This cannot discover custom sites that never expose a Recruiterflow URL.
No request reads private customer APIs or candidate records.

Run with PYTHONPATH=src: mine_recruiterflow.py wayback or cc. Archive bounds are
explicit; reporting a budget stop is not an exhausted-source claim. Run the real
check_liveness.py afterwards; this miner never writes liveness verdicts.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

from wayback_feeder import extract

ROOT = Path(__file__).resolve().parents[2]
POOL = ROOT / "data/ats-tenants-merged/recruiterflow.csv"
WAYBACK = ROOT / "data/wayback-ats/recruiterflow.csv"
ARTIFACTS = ROOT / "experiment/ats-gap-recruiterflow/artifacts"
FIELDS = ("ats", "tenant", "url", "source")


def candidates(text: str) -> dict[str, str]:
    """Use the shared extractor so widgets, encoded slugs and case agree."""
    found = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            url = line.strip()
        else:
            url = row.get("url", "") if isinstance(row, dict) else ""
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            raise SourceUnavailable(
                "URL index returned an unexpected record; unfinished"
            )
        item = extract(url, "recruiterflow.com", "path")
        if item:
            slug, url = item
            found[slug] = url
    return found


def merge(path: Path, found: dict[str, str], source: str) -> int:
    rows = {}
    if path.exists():
        with path.open(encoding="utf-8") as file:
            rows = {r["tenant"]: r for r in csv.DictReader(file)}
    added = 0
    for slug, url in sorted(found.items()):
        if slug not in rows:
            rows[slug] = {
                "ats": "recruiterflow",
                "tenant": slug,
                "url": url,
                "source": source,
            }
            added += 1
        else:
            sources = set(filter(None, rows[slug].get("source", "").split(";")))
            sources.add(source)
            rows[slug]["source"] = ";".join(sorted(sources))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".csv.part")
    with temporary.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(
            {key: row.get(key, "") for key in FIELDS} for row in rows.values()
        )
    temporary.replace(path)
    return added


class SourceUnavailable(RuntimeError):
    pass


def fetch(url: str, name: str, *, empty_404: bool = False) -> str:
    """One bounded request. Refusals stop this run; Retry-After is recorded."""
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    meta = {"at": datetime.now(UTC).isoformat(), "url": url}
    request = urllib.request.Request(
        url, headers={"User-Agent": "HeadStart-discovery/0.1"}
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read(15_000_001)
            meta.update(status=response.status, bytes=len(body))
            if len(body) > 15_000_000:
                raise SourceUnavailable("response exceeded 15 MB; not checkpointed")
    except urllib.error.HTTPError as exc:
        body = exc.read()
        meta.update(status=exc.code, retry_after=exc.headers.get("Retry-After"))
        if not (empty_404 and exc.code == 404 and b"No Captures found" in body):
            meta["error"] = f"HTTP {exc.code}"
    except (OSError, SourceUnavailable) as exc:
        body = b""
        meta["error"] = str(exc)
    finally:
        meta["elapsed_s"] = round(time.monotonic() - started, 3)
        (ARTIFACTS / f"{name}.meta.json").write_text(json.dumps(meta, indent=2))
    (ARTIFACTS / f"{name}.body").write_bytes(body)
    print(json.dumps(meta), flush=True)
    if meta.get("error"):
        raise SourceUnavailable(str(meta))
    time.sleep(max(0, 2 - (time.monotonic() - started)))
    return "" if meta.get("status") == 404 else body.decode("utf-8")


def checkpoint(state: dict, key: str, found: dict[str, str], source: str) -> None:
    added = merge(POOL, found, source)
    if source == "wayback":
        merge(WAYBACK, found, source)
    state[key] = {
        "at": datetime.now(UTC).isoformat(),
        "candidates": len(found),
        "new": added,
    }
    path = ARTIFACTS / "archive-checkpoints.json"
    path.write_text(json.dumps(state, indent=2, sort_keys=True))
    print(
        f"{key}: {len(found)} candidates, +{added} pool rows; checkpointed", flush=True
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", choices=("wayback", "cc"))
    parser.add_argument("--seconds", type=int, default=180)
    parser.add_argument("--max-pages", type=int, default=12)
    parser.add_argument("--max-crawls", type=int, default=40)
    parser.add_argument("--years", type=int, default=3)
    args = parser.parse_args()
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    path = ARTIFACTS / "archive-checkpoints.json"
    state = json.loads(path.read_text()) if path.exists() else {}
    deadline = time.monotonic() + args.seconds
    try:
        if args.source == "wayback":
            base = "https://web.archive.org/cdx/search/cdx?" + urllib.parse.urlencode(
                {"url": "recruiterflow.com", "matchType": "domain"}
            )
            count = fetch(base + "&showNumPages=true", "wayback-count").strip()
            if not count.isdigit():
                raise SourceUnavailable("Wayback page count is not an integer")
            pages = int(count)
            for page in range(min(pages, args.max_pages)):
                key = f"wayback:{page}"
                if key in state:
                    continue
                if time.monotonic() >= deadline:
                    raise SourceUnavailable("Wayback time budget reached; incomplete")
                text = fetch(
                    base + f"&fl=original&collapse=urlkey&page={page}",
                    f"wayback-page-{page}",
                )
                checkpoint(state, key, candidates(text), "wayback")
            print(
                f"Wayback pages: {sum(f'wayback:{p}' in state for p in range(pages))}/{pages}",
                flush=True,
            )
        else:
            crawls = json.loads(
                fetch("https://index.commoncrawl.org/collinfo.json", "cc-collections")
            )
            cutoff = datetime.now(UTC) - timedelta(days=365 * args.years)
            crawls = [
                c
                for c in crawls
                if datetime.fromisoformat(c["from"]).replace(tzinfo=UTC) >= cutoff
            ]
            for crawl in crawls[: args.max_crawls]:
                if time.monotonic() >= deadline:
                    raise SourceUnavailable(
                        "CC time budget reached; remaining crawls incomplete"
                    )
                cid = crawl["id"]
                base = (
                    crawl["cdx-api"]
                    + "?"
                    + urllib.parse.urlencode(
                        {
                            "url": "recruiterflow.com",
                            "matchType": "domain",
                            "output": "json",
                        }
                    )
                )
                raw = fetch(base + "&showNumPages=true", f"{cid}-count", empty_404=True)
                if not raw:
                    checkpoint(state, f"cc:{cid}:empty", {}, cid)
                    continue
                pages = json.loads(raw)["pages"]
                for page in range(min(pages, args.max_pages)):
                    key = f"cc:{cid}:{page}"
                    if key in state:
                        continue
                    if time.monotonic() >= deadline:
                        raise SourceUnavailable(
                            "CC time budget reached; remaining pages incomplete"
                        )
                    text = fetch(
                        base + f"&fl=url&page={page}",
                        f"{cid}-page-{page}",
                        empty_404=True,
                    )
                    checkpoint(state, key, candidates(text), cid)
                print(
                    f"{cid}: pages {sum(f'cc:{cid}:{p}' in state for p in range(pages))}/{pages}",
                    flush=True,
                )
                if pages > args.max_pages:
                    raise SourceUnavailable("CC page bound reached; crawl incomplete")
            print(
                f"CC eligible crawls: {len(crawls)}; attempted at most {args.max_crawls}",
                flush=True,
            )
    except (SourceUnavailable, ValueError, KeyError) as exc:
        print(f"INCOMPLETE: {exc}", flush=True)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
