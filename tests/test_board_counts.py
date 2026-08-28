"""The Board counts in README.md and CONTEXT.md must match the committed ledger.

Five defensible answers exist to "how many Boards do we have" and they differ by tens of
thousands; CONTEXT.md §Counting Boards names each one. This file keeps those names bound to real
numbers, because a glossary that drifts is worse than none — it lends stale figures authority.

**This is possible only because every input is committed.** The liveness CSVs are git-tracked and
`DISABLED_ATS`/`EXCLUDED_BOARDS`/`PARKED_BOARDS` are source, so a Board count cannot change without
a commit — which is exactly the moment the docs should change with it. Two counts in that glossary
are therefore NOT checked here and cannot be: **Scraped Board** (`data/state/board_cost.csv`) and
**Scored Board** (`data/state/board_priority.csv`) live only on HF and move every run, with no
commit to hang an assertion on. They carry a measured-on date in the glossary instead.

No `importorskip`: this needs stdlib plus `headstart.config`, so unlike `test_readme_schema.py` it
actually runs in CI rather than skipping and reading green.
"""

from __future__ import annotations

import csv
import functools
import re
from pathlib import Path

from headstart import liveness
from headstart.config import (
    EXCLUDED_BOARDS,
    PARKED_BOARDS,
    CompanyRef,
    _dedupe_boards,
    board_identity,
    load_active_companies,
)
from headstart.scrapers.registry import DISABLED_ATS, SCRAPERS

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "data" / "validate" / "liveness"


def _live_companies() -> list[CompanyRef]:
    out = []
    for path in sorted(LEDGER.glob("*.csv")):
        scraper = SCRAPERS.get(path.stem)
        if scraper is None:
            continue
        for v in liveness.load(path).values():
            if v.status == liveness.LIVE:
                slug = scraper.slug_from(v.tenant, v.url)
                out.append(CompanyRef(ats=scraper.ats, slug=slug, name=v.tenant))
    return out


@functools.cache
def counts() -> dict[str, int]:
    """Every git-derivable figure in CONTEXT.md §Counting Boards, from the ledger itself.

    Cached: four tests want it and it re-reads 20 CSVs each time (~1.3s a call).
    """
    # Counted off the raw lines, not `liveness.load()`'s tenant-keyed dict. The glossary defines a
    # Ledger row as one LINE, and CLAUDE.md's own "duplicate boards" section says these ledgers
    # routinely hold more than one row per board — a dict would collapse exactly those and
    # under-report the number this entry exists to name.
    by_status: dict[str, int] = {}
    for path in sorted(LEDGER.glob("*.csv")):
        with path.open(encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                st = (row.get("status") or "").strip()
                by_status[st] = by_status.get(st, 0) + 1

    live = _live_companies()
    unique = _dedupe_boards(live)
    # Dedupe-first order, which is what the glossary states. The README's funnel excludes first and
    # so reads different intermediate deltas for the same endpoints — two of the excluded Boards
    # are themselves duplicate spellings, so `EXCLUDED_BOARDS` removes 43 there and 41 here.
    enabled = [c for c in unique if c.ats not in DISABLED_ATS]
    kept = [c for c in enabled if f"{c.ats}:{c.slug}".lower() not in EXCLUDED_BOARDS]
    return {
        "Ledger row": sum(by_status.values()),
        "Live row": by_status.get("live", 0),
        "dead": by_status.get("dead", 0),
        "unknown": by_status.get("unknown", 0),
        "Unique Board": len(unique),
        "disabled": len(unique) - len(enabled),
        "excluded_after_dedupe": len(enabled) - len(kept),
        "parked": sum(1 for c in kept if board_identity(c).lower() in PARKED_BOARDS),
        "Scrapable Board": len(load_active_companies(LEDGER, min_jobs=0)),
        "Hiring Board": len(load_active_companies(LEDGER, min_jobs=1)),
    }


def _abs_int(text: str) -> int:
    """The magnitude of a figure, sign discarded — the funnel writes its deltas as `−25,416`
    and sums them as positives. Handles the Unicode minus the docs use and the ASCII one."""
    return int(text.replace(",", "").replace("−", "").replace("-", "").strip())


def _glossary() -> dict[str, int]:
    """`**Term** — 1,234:` lines out of CONTEXT.md's Counting Boards section."""
    body = (ROOT / "CONTEXT.md").read_text(encoding="utf-8")
    section = body.split("### Counting Boards", 1)[1].split("\n### ", 1)[0]
    # Not anchored on a trailing colon: the two HF-backed entries carry a "measured on" note
    # between the figure and it, and requiring the colon silently dropped them from the parse.
    return {
        m[1]: _abs_int(m[2])
        for m in re.finditer(r"\*\*([\w ]+)\*\* — ([\d,]+)", section)
    }


def test_the_glossary_agrees_with_the_ledger() -> None:
    truth, said = counts(), _glossary()
    checked = [
        "Ledger row",
        "Live row",
        "Unique Board",
        "Scrapable Board",
        "Hiring Board",
    ]
    wrong = {k: (said.get(k), truth[k]) for k in checked if said.get(k) != truth[k]}
    assert not wrong, (
        "CONTEXT.md §Counting Boards is stale — {said, actual}: "
        f"{wrong}. Re-measure with `python -m pytest tests/test_board_counts.py -q` "
        "and update the glossary, the README funnel, and the measured-on date together."
    )


def test_the_two_unguarded_counts_say_so_where_a_reader_will_see_it() -> None:
    """Partial coverage that reads as total is the trap. These two cannot be checked — they live
    only on HF — so the glossary must both list them and mark them, or a reader takes the whole
    section as enforced."""
    body = (ROOT / "CONTEXT.md").read_text(encoding="utf-8")
    section = body.split("### Counting Boards", 1)[1].split("\n### ", 1)[0]
    for term in ("Scraped Board", "Scored Board"):
        # the definition line, not the first mention — both terms also appear in the intro prose,
        # and splitting on the bare name matched that instead (this test caught it on itself)
        line = re.search(rf"^\*\*{term}\*\* — [\d,]+.*$", section, re.MULTILINE)
        assert line, f"{term} has no definition line in the glossary"
        assert "not test-enforced" in line[0], (
            f"{term} is not marked as unchecked, so the section reads as fully guarded"
        )


def test_the_readme_funnel_agrees_with_the_ledger() -> None:
    """The README excludes before deduping, so its deltas differ from the glossary's — the same
    endpoints reached the other way round. Both must land on Scrapable Board."""
    truth = counts()
    table = (ROOT / "README.md").read_text(encoding="utf-8")
    section = table.split("### Which boards a run picks", 1)[1].split("\n## ", 1)[0]
    figures = [
        _abs_int(m) for m in re.findall(r"\|\s*\*{0,2}(−?[\d,]+)\*{0,2}\s*\|", section)
    ]
    assert figures, "could not parse the README funnel table"
    start, *deltas, end = figures
    assert start == truth["Live row"], (
        f"README funnel starts at {start:,}, ledger has {truth['Live row']:,}"
    )
    assert end == truth["Scrapable Board"], (
        f"README funnel ends at {end:,}, ledger has {truth['Scrapable Board']:,}"
    )
    assert start - sum(deltas) == end, (
        f"README funnel does not sum: {start:,} - {sum(deltas):,} = {start - sum(deltas):,}, not {end:,}"
    )


def test_both_orders_reach_the_same_scrapable_count() -> None:
    """The property that makes either order quotable: exclude-then-dedupe and dedupe-then-exclude
    disagree on every intermediate delta and agree on the endpoint."""
    truth = counts()
    dedupe_first = (
        truth["Unique Board"]
        - truth["disabled"]
        - truth["excluded_after_dedupe"]
        - truth["parked"]
    )
    assert dedupe_first == truth["Scrapable Board"]
