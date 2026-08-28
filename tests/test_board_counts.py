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


def counts() -> dict[str, int]:
    """Every git-derivable figure in CONTEXT.md §Counting Boards, from the ledger itself."""
    rows = {p.stem: liveness.load(p) for p in sorted(LEDGER.glob("*.csv"))}
    by_status: dict[str, int] = {}
    for recs in rows.values():
        for v in recs.values():
            by_status[v.status] = by_status.get(v.status, 0) + 1

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


def _n(text: str) -> int:
    return int(text.replace(",", "").replace("−", "").replace("-", "").strip())


def _glossary() -> dict[str, int]:
    """`**Term** — 1,234:` lines out of CONTEXT.md's Counting Boards section."""
    body = (ROOT / "CONTEXT.md").read_text(encoding="utf-8")
    section = body.split("#### Counting Boards", 1)[1].split("\n### ", 1)[0]
    # Not anchored on a trailing colon: the two HF-backed entries carry a "measured on" note
    # between the figure and it, and requiring the colon silently dropped them from the parse.
    return {
        m[1]: _n(m[2]) for m in re.finditer(r"\*\*([\w ]+)\*\* — ([\d,]+)", section)
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


def test_the_glossary_covers_the_hf_backed_counts_it_cannot_check() -> None:
    """Their presence is the assertion: a reader must not think all of them are guarded."""
    said = _glossary()
    assert "Scraped Board" in said and "Scored Board" in said


def test_the_readme_funnel_agrees_with_the_ledger() -> None:
    """The README excludes before deduping, so its deltas differ from the glossary's — the same
    endpoints reached the other way round. Both must land on Scrapable Board."""
    truth = counts()
    table = (ROOT / "README.md").read_text(encoding="utf-8")
    section = table.split("### Which boards a run picks", 1)[1].split("\n## ", 1)[0]
    figures = [
        _n(m) for m in re.findall(r"\|\s*\*{0,2}(−?[\d,]+)\*{0,2}\s*\|", section)
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
