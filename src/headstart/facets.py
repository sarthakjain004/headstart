#!/usr/bin/env python3
"""How many jobs each filter option would actually return — the Search tab's facet counts.

Issue #275: *"show number of results after each filter is applied actually, so user is well
informed what his filters are actually doing."* A filter control that cannot say what it does
makes the user guess, and the guess is usually "nothing matched, this site is empty" when the
truth is "one of your six filters costs you 94% of the results".

**The counting rule, and why it is not the obvious one.** A facet's own constraint is *removed*
before its options are counted; every other filter stays applied. So the ATS strip answers "how
many would I get if I switched to Greenhouse", not "how many Greenhouse jobs are in my current
Greenhouse-filtered results" — which is the currently-selected total, repeated once per option,
and tells the user nothing. Only the dimension being drawn is excluded; this is per-dimension,
not a global unfiltered count.

**Counts are query-independent, and that is a fact about the index, not a shortcut.** A vector
search *ranks* the filtered set, it does not shrink it: every row matching the where-clause is a
candidate, and the query only decides the order they come back in. So a count needs no encoder
call and no vector at all — which is why this module never touches the model, and why the UI
must label the number as matching *filters* rather than matching the query.

**Cost.** One :meth:`count_rows` with a filter measured 4–6 ms against a 316,606-row table on
2026-08-25, and the full strip below is ~40 of them. They are issued through one
:class:`ThreadPoolExecutor` because LanceDB's counting happens in Rust with the GIL released, so
the wall cost is roughly the slowest count rather than their sum.

Exposed as one function, :func:`counts`, which takes the parsed filters and returns every number
the UI needs. It takes no query — see above; there is deliberately nowhere to pass one.
"""

from __future__ import annotations

from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from typing import Any

try:  # in the repo, a package member; in the Space image, a flat sibling module
    from headstart.search import ETYPE_CLAUSES, build_filter
except ImportError:  # pragma: no cover - exercised only in the deployed Space
    from search import ETYPE_CLAUSES, build_filter  # type: ignore[no-redef]

# How long "first seen by HeadStart" can look back, in hours. The short end matters more than
# the long: the pipeline cycles roughly hourly (ADR-0071), so 2h is "since about the last run"
# and the 4/6/8/12/18 steps issue #275 asked for are the shape of a working day either side of
# it. 168 is the week.
SEEN_HOURS = (2, 4, 6, 8, 12, 18, 24, 168)

# ...and "posted by the employer", in DAYS, because that is the granularity `posted_at` carries
# from the boards themselves.
POSTED_DAYS = (1, 7, 30, 90)

# Experience ceilings the UI offers. `max_years` is a "no more than" filter, so these read as
# "roles open to someone with N years".
MAX_YEARS = (0, 2, 5, 10)

# Enough to keep the strip's wall cost near the slowest single count rather than their sum,
# without opening a thread per option. LanceDB counts in Rust with the GIL released.
_WORKERS = 8


def _hours(h: int) -> str:
    return (
        f"Last {h} hours"
        if h < 24
        else ("Last 24 hours" if h == 24 else f"Last {h // 24} days")
    )


def _days(d: int) -> str:
    return "Last 24 hours" if d == 1 else f"Last {d} days"


# The two recency dropdowns, as ``(value, label)`` — rendered by the template AND counted here,
# from this one definition. They used to be a hardcoded ``<option>`` list beside these tuples,
# and that drift fails *silently*: a value present in the markup but absent here simply shows no
# count, which reads as "no jobs" rather than as the bug it is.
SEEN_OPTIONS = tuple((h, _hours(h)) for h in SEEN_HOURS)
POSTED_OPTIONS = tuple((d, _days(d)) for d in POSTED_DAYS)


def counts(table: Any, filter_kwargs: Mapping[str, Any]) -> dict[str, Any]:
    """Every facet's per-option count, plus the total, for one request's filters.

    ``filter_kwargs`` is :meth:`headstart.search.JobSearch.filter_kwargs` output — the parsed
    filters, shared with the ranked search precisely so the count and the list it counts can
    never describe different queries. It is the only input: the request's query never reaches
    here, because a count is decided by the where-clause alone.

    Returns ``{"total": int, "facets": {dimension: [{value,label,count}, ...]}, "blocking":
    str|None}``. ``blocking`` names the single filter whose removal recovers the most results
    when the total is zero, and is ``None`` otherwise — the "why did I get nothing" answer that
    the same counting machinery already pays for. ``description_coverage`` is the Keyword
    filter's disclaimer (ADR-0104): ``{"covered": int, "total": int}`` counted with the keyword
    lifted, or ``None`` while the served table has no ``description`` column.
    """
    base = dict(filter_kwargs)

    def where_for(**overrides: Any) -> str | None:
        return build_filter(**{**base, **overrides})

    # (dimension, option value, label, the kwargs that option overrides). Built in full first
    # and counted second, so every count can go out at once.
    plan: list[tuple[str, Any, str, dict[str, Any]]] = []
    dimensions: set[str] = set()

    def add(dimension: str, value: Any, label: str, **overrides: Any) -> None:
        plan.append((dimension, value, label, overrides))
        dimensions.add(dimension)

    # Dark without the column, exactly like the salary facet below: `build_filter` compiles
    # nothing for `seen_within` while `has_first_seen` is false (ADR-0031), so every option here
    # — and the "Any" row the dimension's existence adds — would report the same unfiltered
    # total, nine numbers saying the window costs nothing. `posted_within` needs no such guard:
    # `posted_at` is in the table's base schema rather than added by a migration, so it is always
    # there to filter on.
    if base.get("has_first_seen"):
        for h, label in SEEN_OPTIONS:
            add("seen_within", h, label, seen_within=h)
    for d, label in POSTED_OPTIONS:
        add("posted_within", d, label, posted_within=d)
    for y in MAX_YEARS:
        add(
            "max_years",
            y,
            "Entry level" if y == 0 else f"{y} years or less",
            max_years=y,
        )
    for value, label in (
        ("full-time", "Full-time"),
        ("part-time", "Part-time"),
        ("contract", "Contract"),
        ("internship", "Internship"),
    ):
        if value in ETYPE_CLAUSES:
            add("etype", value, label, etype=value)
    add("remote", True, "Remote only", remote=True)
    if base.get("has_min_salary_annual"):
        add("has_salary", True, "Shows salary", has_salary=True)
    for a in base.get("atses") or ():
        add("ats", a, a, ats=a)

    # The "Any" row of each dimension, counted with that dimension LIFTED — the same rule every
    # other option follows, and the one place it is easy to get backwards. Counting it with the
    # current filters intact reports the *constrained* total, so an active 2-hour window makes
    # "Any time" read smaller than the 24-hour option nested inside it: the unconstrained choice
    # looking narrower than its own subset, which is worse than showing no number at all.
    for dimension in sorted(dimensions):
        if dimension in ("remote", "has_salary"):
            continue  # switches, not option lists — "off" is the absence of the row, not a row
        add(dimension, None, "Any", **{dimension: None})

    # `total` rides the same pool rather than being counted first — it is one more count, and
    # serialising it ahead of the rest would add its latency to every request for no reason.
    counted = [(d, v, lbl, where_for(**ov)) for d, v, lbl, ov in plan]
    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        totals = pool.submit(_count, table, where_for())
        # The Keyword filter's disclaimer (ADR-0104): of the rows the *other* filters match, how
        # many carry a description at all. Not a facet — there is no option to pick — but the
        # same rules apply: decided by the where-clause alone, so it rides this pool, and counted
        # with the keyword LIFTED, as every dimension's "Any" row is above. Counted with it
        # intact, a description-scoped keyword makes the two clauses one — every row it matched
        # has a description by construction — and the rail would read "N of N" on a table where
        # almost nothing has text. None while the column does not exist yet, which the UI reads
        # as "not available", distinct from a genuine zero.
        unkeyed = where_for(kw=None, kw_in=None)
        coverage = (
            (
                pool.submit(_count, table, _with_description(unkeyed)),
                pool.submit(_count, table, unkeyed),
            )
            if base.get("has_description")
            else None
        )
        results = list(pool.map(lambda c: _count(table, c[3]), counted))

    facets: dict[str, list[dict[str, Any]]] = {}
    for (dimension, value, label, _), n in zip(counted, results, strict=True):
        facets.setdefault(dimension, []).append(
            {"value": value, "label": label, "count": n}
        )

    total = totals.result()
    return {
        "total": total,
        "facets": facets,
        "blocking": _blocking(table, base, total),
        "description_coverage": (
            {"covered": coverage[0].result(), "total": coverage[1].result()}
            if coverage
            else None
        ),
    }


def _with_description(where: str | None) -> str:
    """A where-clause narrowed to rows whose description is stored."""
    return (
        f"({where}) AND description IS NOT NULL" if where else "description IS NOT NULL"
    )


def _count(table: Any, where: str | None) -> int:
    return table.count_rows(filter=where) if where else table.count_rows()


# Keys :func:`_blocking` may never name. Neither the runtime facts of the index nor the sort are
# user filters, so none of them can be "dropped"; `posted_sortable` in particular is the sort
# control's shape guard. Naming any of them would render a raw key in the empty state beside a
# button that removes nothing, since the UI has neither a label nor a control for it.
NEVER_BLOCKING = frozenset(
    {
        "atses",
        "currencies",
        "has_first_seen",
        "has_min_salary_annual",
        "has_description",
        "posted_sortable",
        # The keyword's scope, not a filter: `filter_kwargs` already nulls it without a keyword,
        # and with one it is the `kw` entry that would be named.
        "kw_in",
        # The salary bracket's scope, for the same reason and with a sharper consequence. Unsetting
        # it does not remove the bracket — it re-scopes it to `SALARY_DEFAULT_CURRENCY` — so the
        # recovery `_blocking` measures would be one no control can perform: the empty state's button
        # clears the two *bounds* (app.js's `BRACKET`), which is a different, much larger recovery.
        # Measured on a 318,003-row table, an INR bracket at 100,000: unsetting the currency recovers
        # 70,549 rows, clearing the bounds recovers all 318,003. Naming the currency would point the
        # user at a button that does something else.
        "salary_currency",
        # The Matches tab's date ranges (`matchesRange()`) and the alerts run's Watermark cutoff
        # (ADR-0035). Excluded rather than labelled, because there is nothing here for the empty
        # state's button to clear: the four range inputs live in matches.html, so adding them to
        # app.js's `CONTROL` — the *Search* tab's control registry, which `clearAll()` and
        # `applyProfile()` sweep wholesale — would have one tab blanking another's controls, and
        # `first_seen_after` is machine-set by the alerts run and has no input at all. If the Search
        # tab ever grows its own range controls, drop them from here in the same change;
        # `tests/test_facets.py` fails on a filter that is in neither this tuple nor those maps.
        "posted_after",
        "posted_before",
        "seen_after",
        "seen_before",
        "first_seen_after",
    }
)


def _blocking(table: Any, base: Mapping[str, Any], total: int) -> str | None:
    """Which single active filter is costing the user everything, when nothing matched.

    Only computed on a zero total, where it is the whole answer and the request is otherwise
    doing no work anyway. It drops each active filter in turn and keeps the one that recovers
    the most rows; ``None`` when nothing matched even with every filter dropped, because then
    no filter is to blame and saying one is would be a lie.
    """
    if total:
        return None
    active = [
        key
        for key, value in base.items()
        if key not in NEVER_BLOCKING
        # `is`, not `in (None, False, "")`: `max_years=0` is the Entry-level filter and a real
        # constraint, but `0 == False` in Python, so a membership test silently calls it unset
        # and would never name it as the blocker.
        and value is not None
        and value is not False
        and value != ""
    ]
    best, best_n = None, 0
    for key in active:
        # "Unset" is False for the two switches and None for everything else — build_filter
        # reads both as absent, but a bool kwarg given None would be a lie about its type.
        unset = False if isinstance(base[key], bool) else None
        n = _count(table, build_filter(**{**base, key: unset}))
        if n > best_n:
            best, best_n = key, n
    return best
