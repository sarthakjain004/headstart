"""Reconcile PageUp channels/locales only within an account, using complete public feeds.

2026-10-03: CSU's sf channel contains 63 of cw's 2,487 native ids. Different
accounts can reuse numeric ids, so containment across accounts proves nothing.
Empty feeds and partial overlaps never produce aliases. Run after refreshing the
PageUp ledger; --apply writes only after every live Board was read successfully.
"""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from headstart.boards import alias_ledger, liveness_ledger
from headstart.scrapers.pageup import PageUpScraper, feed_items

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "experiment/ats-gap-pageup/artifacts/feeds"


def subset_aliases(postings: dict[str, set[str]]) -> dict[str, str]:
    """Elect a largest nonempty superset in the same PageUp account, without chains."""
    ordered = sorted(postings, key=lambda s: (-len(postings[s]), s))
    aliases = {}
    for duplicate in ordered:
        ids = postings[duplicate]
        if not ids:
            continue
        for canonical in ordered:
            if canonical == duplicate:
                break
            if (
                canonical in aliases
                or canonical.split("/")[0] != duplicate.split("/")[0]
            ):
                continue
            if ids <= postings[canonical]:
                aliases[duplicate] = canonical
                break
    return aliases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    directory = liveness_ledger.dir_for(ROOT)
    ledger = liveness_ledger.load(directory / "pageup.csv")
    postings = {}
    CACHE.mkdir(parents=True, exist_ok=True)
    slugs = sorted(
        {
            PageUpScraper.slug_from(row.tenant, row.url)
            for row in ledger.values()
            if row.status == liveness_ledger.LIVE
        }
    )
    for i, slug in enumerate(slugs, 1):
        scraper = PageUpScraper(slug)
        raw = scraper.fetch_raw()
        # Keep only the native id; channel and locale can change the URL spelling.
        ids = {
            item.findtext("link").split("/job/", 1)[1].split("/", 1)[0].split("?", 1)[0]
            for item in feed_items(raw)
        }
        postings[slug] = ids
        (CACHE / f"{quote(slug, safe='')}.xml").write_text(raw)
        print(f"[{i}/{len(slugs)}] {slug}: {len(ids)} Jobs", flush=True)
    elected = subset_aliases(postings)
    today = datetime.now(UTC).date().isoformat()
    aliases = [
        alias_ledger.Alias(
            "pageup", duplicate, canonical, "subset-reqs", canonical, today
        )
        for duplicate, canonical in sorted(elected.items())
    ]
    for alias in aliases:
        print(f"bury {alias.duplicate} -> {alias.canonical}", flush=True)
    (CACHE.parent / "subsets.json").write_text(
        json.dumps(
            {
                "checked_at": today,
                "postings": {slug: sorted(ids) for slug, ids in postings.items()},
                "aliases": elected,
            },
            indent=2,
        )
        + "\n"
    )
    if args.apply:
        alias_ledger.write(alias_ledger.path_for(directory, "pageup"), aliases)
    print(f"{len(slugs)} public Boards; {len(aliases)} aliases", flush=True)


if __name__ == "__main__":
    main()
