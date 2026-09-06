#!/usr/bin/env python3
"""Find live Boards that are the same Board, and write the alias ledger (ADR-0111).

A company can publish one Board under two hostnames. Both go live in the liveness ledger, both
are scraped, and both are indexed under different ``board_key``s, so the same posting is served
twice. `config._dedupe_boards` cannot catch it — that collapses Boards whose canonical
`board_key` already matches modulo casing or URL form, and two different hostnames match nothing.

The signal is each scraper's `alias_key()`: by default the host its Board surface resolves to
after redirects, which is the site owner's own declaration of which name is canonical. Boards
sharing a key are the same Board. The election needs no heuristic — the canonical *is* the key —
because reading the owner's answer beats guessing between two hostnames, and guessing would be
wrong in both directions here: BASF points its SAP-hosted name at its vanity one, Colas points
its vanity name at its SAP-hosted one.

**Nothing is trusted from a prior run.** Every verdict is re-derived live on each invocation, so a
cluster that has since diverged disappears from the output rather than persisting as a stale
burial. Re-run after discovery adds Boards.

**Only duplicates are acted on.** A Board that resolves somewhere the ledger has never heard of
has *moved*, not duplicated, and the fix differs per case — seed the target, normalise the ledger
row, or mark it dead. Those are reported under `moved` and left alone, deliberately: each is a
liveness change rather than a dedupe one (ADR-0111).

    python scripts/validate/dedupe_boards.py --ats successfactors
    python scripts/validate/dedupe_boards.py --ats successfactors --apply
    python scripts/validate/dedupe_boards.py --ats successfactors --prefer winners.txt --apply
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from headstart import board_aliases, liveness
from headstart.scrapers.registry import SCRAPERS

#: Probe width. These are one cheap header-only GET each against ~2,200 distinct hosts, so the
#: bound is politeness to nobody in particular — no single origin sees more than a couple.
_WORKERS = 24


def read_prefer(path: Path) -> set[str]:
    """Curated winner slugs, one per line — each wins whichever cluster it belongs to.

    Bare slugs, `#` comments allowed. A cluster with no curated winner keeps the redirect's own
    answer, which is right for every cluster measured so far."""
    prefer: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if " " in line:
            raise SystemExit(f"bad prefer line (want a bare slug): {line!r}")
        prefer.add(line)
    return prefer


def probe_all(scraper_cls, slugs: list[str]) -> dict[str, str | None]:
    """Every Board's `alias_key`, printed as it lands.

    `as_completed`, not `map`: one Board behind a 30s timeout must not hold up the other 2,200,
    and a scan this long has to show progress rather than a final dump."""
    keys: dict[str, str | None] = {}
    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        futures = {pool.submit(scraper_cls(slug).alias_key): slug for slug in slugs}
        for done, future in enumerate(as_completed(futures), start=1):
            slug = futures[future]
            key = future.result()
            keys[slug] = key
            if key is None:
                print(f"  [{done}/{len(slugs)}] {slug} -> unreachable", flush=True)
            elif key != slug:
                print(f"  [{done}/{len(slugs)}] {slug} -> {key}", flush=True)
            elif done % 250 == 0:
                print(f"  [{done}/{len(slugs)}] ...", flush=True)
    return keys


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ats", required=True, help="which liveness ledger to scan")
    ap.add_argument(
        "--prefer", type=Path, help="curated winner slugs, one bare slug per line"
    )
    ap.add_argument(
        "--apply",
        action="store_true",
        help="write the alias ledger (default: report only)",
    )
    ap.add_argument("--limit", type=int, help="probe only the first N live Boards")
    args = ap.parse_args()

    scraper_cls = SCRAPERS.get(args.ats)
    if scraper_cls is None:
        raise SystemExit(f"no scraper for ats {args.ats!r}")

    ledger_dir = liveness.dir_for(ROOT)
    ledger_path = ledger_dir / f"{args.ats}.csv"
    ledger = liveness.load(ledger_path)
    live = sorted(
        {
            scraper_cls.slug_from(v.tenant, v.url)
            for v in ledger.values()
            if v.status == liveness.LIVE
        }
    )
    # `--limit` bounds what is PROBED, never what counts as live. Truncating the membership set
    # would relabel a real duplicate as `migrated` — its canonical simply fell outside the slice —
    # so a quick smoke run would print verdicts that a full run contradicts.
    probe = live[: args.limit] if args.limit else live
    print(f"{args.ats}: probing {len(probe)} of {len(live)} live Board(s)", flush=True)

    keys = probe_all(scraper_cls, probe)
    resolution = board_aliases.resolve(
        keys,
        live,
        signal="redirect",
        vendor_hosts=scraper_cls.alias_vendor_hosts,
        prefer=read_prefer(args.prefer) if args.prefer else (),
    )

    buried = sum(len(c.duplicates) for c in resolution.clusters)
    print(
        f"\n{len(resolution.clusters)} duplicate cluster(s), {buried} Board(s) to bury",
        flush=True,
    )
    for c in sorted(resolution.clusters, key=lambda c: c.canonical):
        print(f"  keep {c.canonical}", flush=True)
        for dup in c.duplicates:
            print(f"       bury {dup}", flush=True)

    by_reason: dict[str, list[board_aliases.Moved]] = defaultdict(list)
    for m in resolution.moved:
        by_reason[m.reason].append(m)
    if by_reason:
        print(
            "\nmoved but not duplicated — reported only, nothing written:", flush=True
        )
        for reason, items in sorted(by_reason.items()):
            print(f"  {reason}: {len(items)}", flush=True)
            for m in sorted(items, key=lambda m: m.slug)[:20]:
                print(f"      {m.slug} -> {m.resolved_to or '?'}", flush=True)
            if len(items) > 20:
                print(f"      ... +{len(items) - 20} more", flush=True)

    counts = Counter(m.reason for m in resolution.moved)
    print(f"\nsummary: {dict(counts)} | duplicates {buried}", flush=True)

    # An ATS whose slug is not a hostname (Workday's is a careers URL, Zoho's a host plus path)
    # cannot use the default `alias_key`: every key it returns falls outside the live slug set, so
    # every Board is labelled `migrated` and the ledger comes back empty. That is a silent wrong
    # answer, not an error, so say it out loud rather than letting a clean-looking zero stand.
    migrated = counts.get(board_aliases.MIGRATED, 0)
    if probe and migrated > len(probe) // 2:
        print(
            f"\nWARNING: {migrated} of {len(probe)} probed Boards resolved outside the ledger. "
            f"That usually means {args.ats}'s slug is not a hostname, so the default "
            "`alias_key` cannot be compared against it — give that scraper an override "
            "(ADR-0111) rather than trusting this run.",
            flush=True,
        )

    out = board_aliases.path_for(ledger_dir, args.ats)
    if not args.apply:
        print(
            f"\n(dry run — pass --apply to write {out.relative_to(ROOT)})", flush=True
        )
        return 0

    today = datetime.now(UTC).date().isoformat()
    board_aliases.write(out, board_aliases.aliases_of(resolution, args.ats, today))
    print(f"\nwrote {buried} alias row(s) -> {out.relative_to(ROOT)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
