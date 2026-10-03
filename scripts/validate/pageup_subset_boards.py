"""Reconcile PageUp channels/locales only within an account, using complete public feeds.

2026-10-03: CSU's sf channel contains 63 of cw's 2,487 native ids. Different
accounts can reuse numeric ids, so containment across accounts proves nothing.
Empty feeds and partial overlaps never produce aliases. Run after refreshing the
PageUp ledger; --apply writes only after every live Board was read successfully.
"""

import argparse
import csv
import io
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote
from xml.etree import ElementTree

from headstart.boards import alias_ledger, liveness_ledger
from headstart.scrapers.pageup import PageUpScraper, feed_items

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "experiment/ats-gap-pageup/artifacts/feeds"


def subset_aliases(
    postings: dict[str, set[str]],
    *,
    preferred: set[str] | None = None,
    canonical: set[str] | None = None,
) -> dict[str, str]:
    """Elect a largest nonempty superset in the same PageUp account, without chains."""
    ordered = sorted(
        postings,
        key=lambda s: (
            -len(postings[s]),
            s not in (preferred or set()),
            s not in (canonical or set()),
            s,
        ),
    )
    aliases = {}
    for duplicate in ordered:
        ids = postings[duplicate]
        if not ids:
            continue
        for target in ordered:
            if target == duplicate:
                break
            if target in aliases or target.split("/")[0] != duplicate.split("/")[0]:
                continue
            if ids <= postings[target]:
                aliases[duplicate] = target
                break
    return aliases


def held_canonicals(ref: str) -> set[str]:
    """Preserve already-held equal-set identities when landing new archive spellings."""
    subprocess.run(
        ["git", "rev-parse", "--verify", ref], cwd=ROOT, check=True, capture_output=True
    )

    def rows(path):
        result = subprocess.run(
            ["git", "show", f"{ref}:{path}"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        return (
            list(csv.DictReader(io.StringIO(result.stdout)))
            if result.returncode == 0
            else []
        )

    buried = {r["duplicate"] for r in rows("data/validate/aliases/pageup.csv")}
    return {
        slug
        for row in rows("data/validate/liveness/pageup.csv")
        if row["status"] == "live"
        and (slug := PageUpScraper.slug_from(row["tenant"], row["url"])) not in buried
        and re.fullmatch(r"[a-z]{2,3}(?:-[a-z0-9]{2,8})*", slug.split("/")[-1])
    }


def feed_canonical(raw: str) -> str | None:
    try:
        return PageUpScraper.slug_from(
            "", ElementTree.fromstring(raw).findtext("./channel/link") or ""
        )
    except ValueError:
        return None  # some empty archive views name an internal/test feed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--baseline-ref",
        default="origin/main",
        help="ref whose already-held canonical identities win equal-set ties",
    )
    args = parser.parse_args()
    directory = liveness_ledger.dir_for(ROOT)
    ledger = liveness_ledger.load(directory / "pageup.csv")
    postings = {}
    canonical = set()
    preferred = held_canonicals(args.baseline_ref)
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
        if target := feed_canonical(raw):
            canonical.add(target)
        # Keep only the native id; channel and locale can change the URL spelling.
        ids = {
            item.findtext("link").split("/job/", 1)[1].split("/", 1)[0].split("?", 1)[0]
            for item in feed_items(raw)
        }
        postings[slug] = ids
        (CACHE / f"{quote(slug, safe='')}.xml").write_text(raw)
        print(f"[{i}/{len(slugs)}] {slug}: {len(ids)} Jobs", flush=True)
    elected = subset_aliases(postings, preferred=preferred, canonical=canonical)
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
                "baseline_ref": args.baseline_ref,
                "feed_canonicals": sorted(canonical),
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
