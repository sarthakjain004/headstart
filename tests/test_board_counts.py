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

from headstart import board_aliases, liveness
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
def _counts() -> dict[str, int]:
    """Every git-derivable figure in CONTEXT.md §Counting Boards, from the ledger itself.

    Cached: several tests want it and it re-reads 20 CSVs each time (~1.3s a call).
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
    # Boards buried as another Board's duplicate (ADR-0111). A stage of the funnel that neither
    # `EXCLUDED_BOARDS` nor the case-variant dedupe accounts for: it is keyed on evidence from
    # outside the ledger, so without it the components stop summing to Scrapable Board.
    alias = {
        ats: board_aliases.load(board_aliases.path_for(LEDGER, ats))
        for ats in {c.ats for c in live}
    }
    is_alias = lambda c: c.slug in alias.get(c.ats, {})  # noqa: E731
    # Dedupe-first order, which is what the glossary states. The README's funnel excludes first and
    # so reads different intermediate deltas for the same endpoints — two of the excluded Boards
    # are themselves duplicate spellings, so `EXCLUDED_BOARDS` removes 43 there and 41 here.
    enabled = [c for c in unique if c.ats not in DISABLED_ATS]
    kept = [c for c in enabled if f"{c.ats}:{c.slug}".lower() not in EXCLUDED_BOARDS]
    unaliased = [c for c in kept if not is_alias(c)]
    # the other order, for the README's funnel: exclude on the raw live set, then dedupe
    live_enabled = [c for c in live if c.ats not in DISABLED_ATS]
    exclude_first_excluded = [
        c for c in live_enabled if f"{c.ats}:{c.slug}".lower() in EXCLUDED_BOARDS
    ]
    exclude_first_kept = [
        c
        for c in live_enabled
        if f"{c.ats}:{c.slug}".lower() not in EXCLUDED_BOARDS and not is_alias(c)
    ]
    return {
        "Ledger row": sum(by_status.values()),
        "Live row": by_status.get("live", 0),
        "dead": by_status.get("dead", 0),
        "unknown": by_status.get("unknown", 0),
        "Unique Board": len(unique),
        "disabled": len(unique) - len(enabled),
        "excluded_after_dedupe": len(enabled) - len(kept),
        "aliased": len(kept) - len(unaliased),
        # The README excludes first, so its two middle deltas differ from the dedupe-first ones
        # above. Both are real; each doc must be checked in the order it actually states.
        "excluded_before_dedupe": len(exclude_first_excluded),
        "dedupe_after_exclude": len(exclude_first_kept)
        - len(_dedupe_boards(exclude_first_kept)),
        "parked": sum(1 for c in unaliased if board_identity(c).lower() in PARKED_BOARDS),
        "Scrapable Board": len(load_active_companies(LEDGER, min_jobs=0)),
        "Hiring Board": len(load_active_companies(LEDGER, min_jobs=1)),
        # Needs `data/state/board_cost.csv`, which is HF-backed and gitignored. Absent on a fresh
        # clone and in CI, so the one figure derived from it is skipped there rather than guessed.
        "scraped_not_unique": _scraped_not_unique(
            {board_identity(c).lower() for c in _dedupe_boards(live)}
        ),
    }


def counts() -> dict[str, int]:
    """A private copy of the cached figures — a cached function must not hand every caller the
    same mutable dict."""
    return dict(_counts())


def _scraped_not_unique(unique: set[str]) -> int | None:
    """Scraped Boards no longer in the live set, or ``None`` when the cost ledger is not here.

    Read through `board_cost.load()`, never the raw CSV: the loader normalises legacy `{ats}:{slug}`
    keys to `board_key` (ADR-0096), so this figure is the same before and after the ledger migrates
    itself. Counted off the file directly it would jump the first time a pipeline run rewrote it,
    turning this test red against a file nobody edited.
    """
    from headstart.board_cost import load as load_cost

    path = LEDGER.parent.parent / "state" / "board_cost.csv"
    if not path.exists():
        return None
    # Case-folded on both sides. `board_key()` preserves case, so the ledger carries ADR-0023
    # variants of one Board; counting them as separate Boards is the conflation this file exists
    # to catch, and it inflated this figure from 600 to 2,552.
    return len({k.lower() for k in load_cost(path)} - unique)


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
    # Summing is not enough: two offsetting wrong deltas pass it. Check each against config, in
    # the README's own exclude-then-dedupe order.
    expected = [
        truth["disabled"],
        truth["excluded_before_dedupe"],
        truth["aliased"],
        truth["dedupe_after_exclude"],
        truth["parked"],
    ]
    assert deltas == expected, (
        f"README funnel deltas are {deltas}, ledger says {expected}"
    )


def test_every_derived_figure_is_current_at_every_site_that_quotes_it() -> None:
    """The headline counts are parsed and asserted; the figures *derived* from them are not, and a
    wrong ratio shipped that way once already.

    Matched **per sentence**, not by searching the file for the number. An earlier version did the
    latter and passed on real drift: `6,617` appears at two sites in CONTEXT.md, so editing one and
    leaving the other stale — the realistic failure — read as green.
    """
    truth = counts()
    dupes = truth["Live row"] - truth["Unique Board"]
    empty = truth["Scrapable Board"] - truth["Hiring Board"]
    skipped = truth["Unique Board"] - truth["Scrapable Board"]
    scraped_not_unique = truth["scraped_not_unique"]

    sites = [
        (
            "README.md",
            r"([\d,]+) live rows of ([\d,]+)",
            (truth["Live row"], truth["Ledger row"]),
        ),
        (
            "README.md",
            r"a row, not a board — ([\d,]+) of them are duplicate spellings",
            (dupes,),
        ),
        ("README.md", r"the ([\d,]+) live-but-empty boards", (empty,)),
        (
            "README.md",
            r"\*\*([\d,]+) ledger rows\*\*: ([\d,]+) live, ([\d,]+) dead, ([\d,]+) unknown",
            (truth["Ledger row"], truth["Live row"], truth["dead"], truth["unknown"]),
        ),
        ("README.md", r"collapse to ([\d,]+) Unique Boards", (truth["Unique Board"],)),
        ("CONTEXT.md", r"because ([\d,]+) live rows are duplicate spellings", (dupes,)),
        (
            "CONTEXT.md",
            r"because ([\d,]+) of those rows are duplicate spellings",
            (dupes,),
        ),
        ("CONTEXT.md", r"the ([\d,]+) Boards between it and Unique Board", (skipped,)),
        (
            "CONTEXT.md",
            r"([\d,]+) Scraped Boards are absent from it",
            (scraped_not_unique,),
        ),
        # the Scrapable Board entry restates the chain it is the end of
        (
            "CONTEXT.md",
            r"minus `registry\.DISABLED_ATS` \(−([\d,]+),.*?`config\.EXCLUDED_BOARDS` \(−([\d,]+) vendor test Boards\), the alias ledger \(−([\d,]+) Boards.*?`config\.PARKED_BOARDS` \(−([\d,]+)\)",
            (
                truth["disabled"],
                truth["excluded_after_dedupe"],
                truth["aliased"],
                truth["parked"],
            ),
        ),
        # and the two-orders rule quotes all four of the numbers that make it true
        (
            "CONTEXT.md",
            r"removes ([\d,]+) Boards from the raw live rows but only \*\*([\d,]+)\*\*",
            (truth["excluded_before_dedupe"], truth["excluded_after_dedupe"]),
        ),
        (
            "CONTEXT.md",
            r"the README's funnel excludes first and so reads −([\d,]+) / −([\d,]+)",
            (truth["excluded_before_dedupe"], truth["dedupe_after_exclude"]),
        ),
        # CLAUDE.md carries the same vocabulary and was read by nothing until round 4
        ("CLAUDE.md", r"still a row — ([\d,]+) are duplicate spellings", (dupes,)),
    ]
    for name, pattern, expected in sites:
        if None in expected:
            continue  # derived from an HF-backed ledger this checkout does not have
        found = re.search(pattern, (ROOT / name).read_text(encoding="utf-8"))
        assert found, (
            f"{name}: no sentence matching {pattern!r} — did the wording change?"
        )
        got = tuple(_abs_int(g) for g in found.groups())
        assert got == expected, (
            f"{name}: {pattern!r} says {got}, ledger says {expected}"
        )


def test_both_orders_reach_the_same_scrapable_count() -> None:
    """The property that makes either order quotable: exclude-then-dedupe and dedupe-then-exclude
    disagree on every intermediate delta and agree on the endpoint."""
    truth = counts()
    dedupe_first = (
        truth["Unique Board"]
        - truth["disabled"]
        - truth["excluded_after_dedupe"]
        - truth["aliased"]
        - truth["parked"]
    )
    assert dedupe_first == truth["Scrapable Board"]
