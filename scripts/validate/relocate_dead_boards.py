#!/usr/bin/env python3
"""Find where a dead Board actually moved to, and correct the liveness ledger to match.

A Board 404ing on its recorded ATS is usually read as "this employer is gone". Measured over the
292 Boards that 404'd across the runs of 2026-08-10/11, that reading was wrong for **46 of them**
(16%): the company had simply changed ATS, kept its slug, and was still hiring — 779 open jobs that
the ledger was about to delist. Greenhouse -> Ashby accounted for 21 of the 46.

So before delisting, ask every other provider whether it holds that slug. This probes each dead
Board's slug against every ATS addressable by bare slug, through the same ``PROBES`` the liveness
sweep uses, and treats only a **live board with at least one job** as evidence: Workable and
SmartRecruiters answer 200 with an empty list for slugs that were never boards, so a zero-job hit
proves nothing.

``--apply`` then writes both halves of the correction, because either alone leaves the ledger wrong:

  * the recorded Board is marked ``dead`` — it really does 404;
  * the Board it moved to is added (or revived) as ``live`` with the job count just measured.

Dry-run by default. ATSes that need more than a slug to address — Workday wants a full careers URL,
SuccessFactors a vanity host — cannot be searched this way, so a company that moved *there* still
looks gone here. That is a floor on what this finds, not a clean bill of health.

    python scripts/validate/relocate_dead_boards.py dead_ids.txt
    python scripts/validate/relocate_dead_boards.py dead_ids.txt --apply
"""

from __future__ import annotations

import argparse
import collections
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_liveness import PROBES  # noqa: E402 - needs the paths above first

from headstart import liveness  # noqa: E402

LEDGER = ROOT / "data" / "validate" / "liveness"

# Only ATSes a bare slug fully addresses. Workday needs `{co}.wdN.myworkdayjobs.com/{site}` and
# SuccessFactors a vanity careers host, so neither can be probed from a slug alone.
SLUG_ADDRESSABLE = (
    "greenhouse",
    "lever",
    "ashby",
    "smartrecruiters",
    "workable",
    "recruitee",
    "teamtailor",
    "personio",
    "zoho",
    "keka",
    "freshteam",
    "rippling",
    "ripplehire",
    "darwinbox",
)


def url_template(ats: str, rows: dict) -> str | None:
    """Infer this ATS's careers-URL shape from a ledger row that already uses it.

    Reading the shape off real data beats hardcoding fourteen of them here, where they would be a
    second source of truth to keep in step with each scraper.
    """
    for row in rows.values():
        if row.tenant and row.url and row.tenant in row.url:
            return row.url.replace(row.tenant, "{tenant}", 1)
    return None


def search(slug: str, workers: int) -> list[tuple[str, int]]:
    """Every slug-addressable ATS that serves this slug a live board with jobs on it."""
    found: list[tuple[str, int]] = []

    def probe(ats: str):
        try:
            return ats, PROBES[ats](slug, "")
        except Exception:  # noqa: BLE001 - a probe that blows up is simply not evidence
            return ats, ("error", None)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for done in as_completed(
            [pool.submit(probe, a) for a in SLUG_ADDRESSABLE if a in PROBES]
        ):
            ats, (verdict, jobs) = done.result()
            if verdict == "live" and (jobs or 0) > 0:
                found.append((ats, jobs))
    return sorted(found, key=lambda x: -x[1])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("ids", help="file of {ats}:{slug} lines for Boards that 404'd")
    ap.add_argument("--workers", type=int, default=14)
    ap.add_argument("--apply", action="store_true", help="write the ledger corrections")
    args = ap.parse_args()

    ids = [
        ln.strip()
        for ln in Path(args.ids).read_text().splitlines()
        if ln.strip() and not ln.startswith("#")
    ]
    ledgers: dict[str, dict] = {}

    def ledger(ats: str) -> dict:
        if ats not in ledgers:
            ledgers[ats] = liveness.load(LEDGER / f"{ats}.csv")
        return ledgers[ats]

    moved, stayed_dead, today = [], [], date.today().isoformat()
    for n, jid in enumerate(ids, 1):
        old_ats, _, slug = jid.partition(":")
        hits = search(slug, args.workers)
        if not hits:
            stayed_dead.append(jid)
            print(f"  [{n}/{len(ids)}] {jid:<44} no live board anywhere", flush=True)
            continue
        moved.append((old_ats, slug, hits))
        print(
            f"  [{n}/{len(ids)}] {jid:<44} -> "
            + ", ".join(f"{a}({j})" for a, j in hits),
            flush=True,
        )

    print(f"\n{len(moved)} moved, {len(stayed_dead)} gone (of {len(ids)})", flush=True)
    flow = collections.Counter((o, hits[0][0]) for o, _, hits in moved)
    for (a, b), count in flow.most_common():
        print(f"  {a} -> {b}: {count}", flush=True)

    if not args.apply:
        print("\ndry run — pass --apply to write the ledger")
        return 0

    touched = set()
    for old_ats, slug, hits in moved:
        old = ledger(old_ats)
        if slug in old:
            row = old[slug]
            old[slug] = liveness.Verdict(
                old_ats, slug, row.url, liveness.DEAD, None, today
            )
            touched.add(old_ats)
        new_ats, jobs = hits[0]  # the board carrying the most jobs
        new = ledger(new_ats)
        template = url_template(new_ats, new)
        if template is None:
            print(f"  skip {new_ats}:{slug} — no URL shape to infer from", flush=True)
            continue
        existing = new.get(slug)
        new[slug] = liveness.Verdict(
            new_ats,
            slug,
            existing.url if existing else template.format(tenant=slug),
            liveness.LIVE,
            jobs,
            today,
        )
        touched.add(new_ats)

    for ats in sorted(touched):
        liveness.write(LEDGER / f"{ats}.csv", ledgers[ats].values())
        print(f"  wrote {LEDGER / f'{ats}.csv'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
