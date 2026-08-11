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

Some moves the search cannot reach — a company that changed provider *and* renamed. Those come
from research and are supplied by ``--moves`` as ``old_ats old_slug new_ats new_slug`` lines, with
``-  -`` as the destination when the Board is known dead but no replacement was found. Curated or
searched, nothing is written unproven: the source must probe dead and the destination live-with-jobs.

    python scripts/validate/relocate_dead_boards.py dead_ids.txt
    python scripts/validate/relocate_dead_boards.py dead_ids.txt --apply
    python scripts/validate/relocate_dead_boards.py --moves data/validate/board_moves.txt --apply
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
from headstart.scrapers.registry import SCRAPERS  # noqa: E402

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
    """Infer this ATS's careers-URL shape from the ledger rows that already use it.

    Reading the shape off real data beats hardcoding fourteen of them here, where they would be a
    second source of truth to keep in step with each scraper. Two things this must not do naively:

    * **Take any old row.** A ledger accumulates conventions: rippling's seeded rows are bare
      ``ats.rippling.com/{tenant}`` while every row ``check_liveness`` writes today carries the
      scheme, and the fossils still outnumber them 2,323 to 346. So neither first-row-wins nor a
      whole-file mode is right — both elect the fossil. Match what the ledger's *current writer*
      produces, by taking the modal shape among the most recently checked rows.
    * **Replace the tenant's first occurrence.** A tenant like ``ats`` appears in the host as well
      as the path, so a leftmost replace rewrites the host. Take the *last* occurrence instead.

    It must equally not assume where in the URL the tenant sits. Half these ATSes name the board in
    a **subdomain** (``https://{tenant}.darwinbox.in``, ``{tenant}.freshteam.com``) and half in a
    **path** (``https://jobs.ashbyhq.com/{tenant}``), so anything anchored to the URL's tail silently
    yields nothing for darwinbox, freshteam, keka, personio and ripplehire — and a ``None`` here
    means a move writes no destination at all. Keep whatever follows the tenant.
    """
    shaped: list[tuple[str, str]] = []
    for row in rows.values():
        if not (row.tenant and row.url):
            continue
        head, found, tail = row.url.rpartition(row.tenant)
        if found:
            shaped.append((row.checked_at or "", f"{head}{{tenant}}{tail}"))
    if not shaped:
        return None
    # Among the current writer's rows, take the *shortest* shape. A ledger's URL column also holds
    # job-detail links and referral URLs that happen to contain the tenant — teamtailor's
    # `www.teamtailor.com/?utm_content={tenant}...`, rippling's `.../{tenant}/jobs` — and every one
    # of those is longer than the board address itself. Ties go to the most common.
    newest = max(when for when, _ in shaped)
    current = collections.Counter(shape for when, shape in shaped if when == newest)
    return min(current, key=lambda shape: (len(shape), -current[shape]))


def derives_back(ats: str, slug: str, url: str) -> bool:
    """Would the pipeline rebuild ``slug`` from this row?

    ``load_active_companies`` does not use the ledger's slug directly — it calls
    ``scraper.slug_from(tenant, url)``. For most ATSes that returns the tenant and the url is
    decoration, but personio and zoho derive the slug *from the url* (their slug is the whole host),
    so a plausible-looking url that reads back as something else would quietly scrape a different
    Board. Cheaper to check than to reason about per ATS.
    """
    scraper = SCRAPERS.get(ats)
    if scraper is None:
        return False
    try:
        return scraper.slug_from(slug, url) == slug
    except Exception:  # noqa: BLE001 - a url the scraper cannot parse is not usable
        return False


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


def read_moves(path: Path) -> list[tuple[str, str, str, str]]:
    """Curated ``old_ats old_slug new_ats new_slug`` rows, whitespace-separated, ``#`` comments ok.

    The search above only ever tries the *same* slug on another provider, so it is blind to a
    company that moved and renamed — ``greenhouse:aerospike`` is live as ``rippling:aerospike-inc``,
    and no amount of probing ``aerospike`` finds it. Those come from research rather than from a
    sweep, so they are supplied explicitly. Each is still probed before anything is written: a
    curated mapping is a hypothesis, and the ledger only records what answered.
    """
    moves = []
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 4:
            raise SystemExit(f"bad move line (want 4 fields): {line!r}")
        moves.append(tuple(parts))  # type: ignore[arg-type]
    return moves


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "ids", nargs="?", help="file of {ats}:{slug} lines for Boards that 404'd"
    )
    ap.add_argument(
        "--moves", type=Path, help="curated `old_ats old_slug new_ats new_slug` lines"
    )
    ap.add_argument("--workers", type=int, default=14)
    ap.add_argument("--apply", action="store_true", help="write the ledger corrections")
    args = ap.parse_args()
    if not args.ids and not args.moves:
        ap.error("give an ids file, --moves, or both")

    ids = (
        [
            ln.strip()
            for ln in Path(args.ids).read_text().splitlines()
            if ln.strip() and not ln.startswith("#")
        ]
        if args.ids
        else []
    )
    ledgers: dict[str, dict] = {}

    def ledger(ats: str) -> dict:
        if ats not in ledgers:
            ledgers[ats] = liveness.load(LEDGER / f"{ats}.csv")
        return ledgers[ats]

    moved, stayed_dead, dead_only = [], [], []
    today = date.today().isoformat()
    for n, jid in enumerate(ids, 1):
        old_ats, _, slug = jid.partition(":")
        hits = search(slug, args.workers)
        if not hits:
            stayed_dead.append(jid)
            print(f"  [{n}/{len(ids)}] {jid:<44} no live board anywhere", flush=True)
            continue
        moved.append((old_ats, slug, hits, slug))  # searched: the slug is unchanged
        print(
            f"  [{n}/{len(ids)}] {jid:<44} -> "
            + ", ".join(f"{a}({j})" for a, j in hits),
            flush=True,
        )

    curated = read_moves(args.moves) if args.moves else []
    for old_ats, old_slug, new_ats, new_slug in curated:
        # the source is only marked dead if it says so itself — the `ids` path earns that by
        # probing, and a curated line must not be trusted to assert it for free
        try:
            src_url = ledger(old_ats).get(old_slug)
            src_verdict, _ = PROBES[old_ats](
                old_slug, src_url.url if src_url else old_slug
            )
        except Exception:  # noqa: BLE001 - treat an unreachable source as unproven
            src_verdict = "error"
        if src_verdict != liveness.DEAD:
            print(
                f"  skip {old_ats}:{old_slug} — source probed {src_verdict}, "
                "not dead, so nothing to relocate",
                flush=True,
            )
            continue
        if new_ats == "-":  # proven dead, no destination found: record the half we know
            dead_only.append((old_ats, old_slug))
            print(f"  dead {old_ats}:{old_slug:<32} (no known replacement)", flush=True)
            continue
        # From here the source is known dead, so it is recorded whatever becomes of the
        # destination. Refusing an unproven replacement is not a reason to keep fetching a 404.
        if new_ats not in PROBES:
            dead_only.append((old_ats, old_slug))
            print(
                f"  dead {old_ats}:{old_slug:<32} (no probe for {new_ats}; "
                "we could not scrape the replacement either)",
                flush=True,
            )
            continue
        try:
            verdict, jobs = PROBES[new_ats](new_slug, new_slug)
        except Exception:  # noqa: BLE001 - an unreachable target is not a confirmed move
            verdict, jobs = "error", None
        if verdict != liveness.LIVE or not (jobs or 0):
            dead_only.append((old_ats, old_slug))
            print(
                f"  dead {old_ats}:{old_slug:<32} (target {new_ats}:{new_slug} probed "
                f"{verdict} with {jobs or 0} jobs, so not written)",
                flush=True,
            )
            continue
        moved.append((old_ats, old_slug, [(new_ats, jobs)], new_slug))
        print(
            f"  move {old_ats}:{old_slug:<32} -> {new_ats}:{new_slug} ({jobs} jobs)",
            flush=True,
        )

    print(
        f"\n{len(moved)} moved, {len(dead_only)} dead with no replacement"
        + (f", {len(stayed_dead)} of {len(ids)} searched found nothing" if ids else ""),
        flush=True,
    )
    flow = collections.Counter((o, hits[0][0]) for o, _, hits, _ in moved)
    for (a, b), count in flow.most_common():
        print(f"  {a} -> {b}: {count}", flush=True)

    if not args.apply:
        print("\ndry run — pass --apply to write the ledger")
        return 0

    touched = set()

    def bury(old_ats: str, old_slug: str) -> None:
        """Record a Board that probed dead. Safe to call for a move whose destination fell through:
        the source's own 404 is what justifies this, not the existence of a replacement."""
        old = ledger(old_ats)
        if old_slug in old:
            row = old[old_slug]
            old[old_slug] = liveness.Verdict(
                old_ats, old_slug, row.url, liveness.DEAD, None, today
            )
            touched.add(old_ats)

    for old_ats, slug, hits, new_slug in moved:
        new_ats, jobs = hits[0]  # the board carrying the most jobs
        new = ledger(new_ats)
        template = url_template(new_ats, new)
        if template is None:
            # write nothing: burying the source here would delist a live Board and put no
            # replacement in its place, which is strictly worse than leaving the row stale
            print(
                f"  skip {old_ats}:{slug} -> {new_ats}:{new_slug} "
                "— no URL shape to infer from, source left untouched",
                flush=True,
            )
            continue
        existing = new.get(new_slug)
        url = existing.url if existing else template.format(tenant=new_slug)
        if not derives_back(new_ats, new_slug, url):
            # the url column is not decoration: `load_active_companies` feeds it to `slug_from` to
            # rebuild the slug. A url the scraper reads back as something else would silently
            # scrape the wrong Board, so refuse it rather than infer harder.
            print(
                f"  skip {old_ats}:{slug} -> {new_ats}:{new_slug} — inferred url {url!r} "
                "does not read back as that slug, source left untouched",
                flush=True,
            )
            continue
        new[new_slug] = liveness.Verdict(
            new_ats, new_slug, url, liveness.LIVE, jobs, today
        )
        touched.add(new_ats)
        bury(old_ats, slug)  # only once the replacement is actually in the ledger

    for old_ats, old_slug in dead_only:
        bury(old_ats, old_slug)

    for ats in sorted(touched):
        liveness.write(LEDGER / f"{ats}.csv", ledgers[ats].values())
        print(f"  wrote {LEDGER / f'{ats}.csv'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
