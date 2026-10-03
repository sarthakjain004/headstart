#!/usr/bin/env python3
"""Resolve Recruiterflow readable/db aliases by database identity AND equal jobs.

Read public unfiltered listings for every live ledger row. Cached captures from
the real liveness prober can supply discovery hints, but every suspected alias
group is fetched afresh before comparing full nonempty posting-id sets. Different
or partially overlapping sets stay separate. Empty Boards are not buried.

One shared-origin pacer from RecruiterflowScraper bounds all requests. A failed
read aborts before replacing the alias ledger. Re-run after refreshing its ledger;
--apply writes shared-reqs rows, otherwise this is a dry run. Cache files live in
experiment/ats-gap-recruiterflow/artifacts/boards and are same-day hints only.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from headstart.boards import alias_ledger, liveness_ledger
from headstart.scrapers.recruiterflow import (
    RecruiterflowScraper,
    inactive_board,
    listed_jobs,
    public_detail,
    public_listing,
)

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "experiment/ats-gap-recruiterflow/artifacts/boards"
_DATABASE = re.compile(r"/careers-page/prod/(db_[a-f0-9]+)/", re.IGNORECASE)


def database_of(page: str, detail: dict | None = None) -> str | None:
    """A specific public asset namespace or JSON-LD identifier, never slug guessing."""
    if detail:
        value = (
            (detail.get("google_for_jobs_fragment") or {}).get("identifier") or {}
        ).get("value", "")
        if re.fullmatch(r"db_[a-f0-9]+__\d+", value, re.IGNORECASE):
            return value.rsplit("__", 1)[0].lower()
    values = {value.lower() for value in _DATABASE.findall(page)}
    return next(iter(values)) if len(values) == 1 else None


def choose_aliases(
    identities: dict[str, str | None],
    postings: dict[str, set[str]],
    *,
    prefer: set[str] | None = None,
) -> dict[str, str]:
    """Equal nonempty sets within one database only; never subset containment."""
    groups: dict[tuple[str, frozenset[str]], list[str]] = defaultdict(list)
    for slug, database in identities.items():
        ids = postings.get(slug)
        if database and ids:
            groups[(database, frozenset(ids))].append(slug)
    selected = {}
    for slugs in groups.values():
        canonical = min(
            slugs, key=lambda s: (s not in (prefer or set()), s.startswith("db_"), s)
        )
        selected.update((slug, canonical) for slug in slugs if slug != canonical)
    return selected


def read_board(slug: str, *, cached: bool = True) -> tuple[str | None, set[str]]:
    scraper = RecruiterflowScraper(slug)
    stem = quote(slug, safe="")
    body_file, meta_file = CACHE / f"{stem}.html", CACHE / f"{stem}.meta.json"
    metadata = json.loads(meta_file.read_text()) if meta_file.exists() else {}
    today = datetime.now(UTC).date().isoformat()
    if (
        cached
        and body_file.exists()
        and metadata.get("status") == 200
        and metadata.get("at", "").startswith(today)
    ):
        page = body_file.read_text()
    else:
        response = scraper._fetch(
            "GET", scraper.url(), headers={"Accept": "text/html"}, timeout=30
        )
        response.raise_for_status()
        page = response.text
        CACHE.mkdir(parents=True, exist_ok=True)
        body_file.write_text(page)
        meta_file.write_text(
            json.dumps(
                {
                    "at": datetime.now(UTC).isoformat(),
                    "url": scraper.url(),
                    "slug": slug,
                    "status": 200,
                },
                indent=2,
            )
        )
    if inactive_board(page):
        raise ValueError(f"{slug}: inactive since the liveness probe; re-probe first")
    jobs = listed_jobs(public_listing(page))
    ids = {str(row["job_id"]) for row in jobs}
    database = database_of(page)
    if database is None and jobs:
        response = scraper._fetch(
            "GET",
            scraper.job_url(str(jobs[0]["job_id"])),
            headers={"Accept": "text/html"},
            timeout=30,
        )
        response.raise_for_status()
        detail = public_detail(response.text)
        if not detail or str(detail.get("job_id")) != str(jobs[0]["job_id"]):
            raise ValueError(f"{slug}: detail identity unavailable")
        database = database_of(page, detail)
    return database, ids


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--prefer", action="append", default=[])
    args = parser.parse_args()
    ledger_dir = liveness_ledger.dir_for(ROOT)
    ledger = liveness_ledger.load(ledger_dir / "recruiterflow.csv")
    slugs = sorted(
        {
            RecruiterflowScraper.slug_from(row.tenant, row.url)
            for row in ledger.values()
            if row.status == liveness_ledger.LIVE
        }
    )
    identities, postings = {}, {}
    for index, slug in enumerate(slugs, 1):
        identities[slug], postings[slug] = read_board(slug)
        print(
            f"[{index}/{len(slugs)}] {slug}: {identities[slug]}, {len(postings[slug])} jobs",
            flush=True,
        )
    by_database = defaultdict(list)
    for slug, database in identities.items():
        if database:
            by_database[database].append(slug)
    for database, group in by_database.items():
        if len(group) < 2:
            continue
        for slug in group:
            current_database, postings[slug] = read_board(slug, cached=False)
            if current_database != database:
                raise SystemExit(
                    f"{slug}: identity changed during scan; alias ledger untouched"
                )
        print(
            f"fresh comparison {database}: {[(s, len(postings[s])) for s in group]}",
            flush=True,
        )
    selected = choose_aliases(identities, postings, prefer=set(args.prefer))
    alias_path = alias_ledger.path_for(ledger_dir, "recruiterflow")
    previous = alias_ledger.load(alias_path)
    confirmed_dead = {
        RecruiterflowScraper.slug_from(row.tenant, row.url)
        for row in ledger.values()
        if row.status == liveness_ledger.DEAD
    }
    if any(
        not identities.get(slug)
        and slug not in confirmed_dead
        and postings.get(slug) != set()
        for pair in previous.items()
        for slug in pair
    ):
        raise SystemExit(
            "a previous alias lacks current identity evidence; ledger untouched"
        )
    today = datetime.now(UTC).date().isoformat()
    aliases = [
        alias_ledger.Alias(
            "recruiterflow",
            duplicate,
            canonical,
            "shared-reqs",
            identities[duplicate],
            today,
        )
        for duplicate, canonical in sorted(selected.items())
    ]
    for alias in aliases:
        print(
            f"bury {alias.duplicate} -> {alias.canonical}: {len(postings[alias.duplicate])} equal jobs",
            flush=True,
        )
    for duplicate, canonical in sorted(previous.items()):
        if selected.get(duplicate) != canonical:
            print(
                f"remove previous alias {duplicate} -> {canonical}: no longer equal",
                flush=True,
            )
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    (CACHE.parent / "alias-validation.json").write_text(
        json.dumps(
            {
                "at": datetime.now(UTC).isoformat(),
                "identities": identities,
                "postings": {s: sorted(ids) for s, ids in postings.items()},
                "aliases": selected,
            },
            indent=2,
        )
    )
    print(
        f"{len(slugs)} live rows; {len(aliases)} aliases; {sum(d is None for d in identities.values())} unresolved identities",
        flush=True,
    )
    if args.apply:
        alias_ledger.write(alias_path, aliases)


if __name__ == "__main__":
    main()
