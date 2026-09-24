"""The Search-filter compiler: a request's filters as a LanceDB where-clause (ADR-0031, ADR-0149).

:func:`build_filter` is the **reference product filter** — the full Search-filter vocabulary
(:class:`SearchFilters`) compiled against what the open table supports (:class:`IndexCapabilities`).
The Account's follow/hide clause (ADR-0171) is built here too, beside it, because both escape
their input through the same LIKE rules and every caller must narrow the same way.

Split out of :mod:`headstart.search` (ADR-0194) so :mod:`headstart.facets` and
:mod:`headstart.search` both import the compiler and neither imports the other. Each
materialized Search filter's own facts live in its module (ADR-0193); this compiler calls them.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import NamedTuple

from headstart import (
    employment_type_filter,
    experience_filter,
    fx,
    india_filter,
    posted_date_guard,
    salary_known_filter,
)

# The salary bracket's currency when a request names none (issue #275). ADR-0084 says the picker
# "defaults to USD", and 86.5% of the Jobs carrying a salary are USD (measured 2026-08-25), so
# that is the least surprising scope for a bare number. It lives here, beside the compiler that
# applies it, because it is the *compiler's* default rather than one client's — see build_filter.
SALARY_DEFAULT_CURRENCY = "USD"


class KeywordScope(NamedTuple):
    """One entry of :data:`KEYWORD_SCOPES`: the columns a keyword is matched in, and the rail's label."""

    columns: tuple[str, ...]
    label: str


# The Keyword filter's scopes (ADR-0104): which served text columns a keyword is matched in.
# The map is the extension point: `build_filter` compiles whatever it says, `parse_filters`
# whitelists `kw_in` against its keys, and the rail's <select>, its disabled-until-migrated rule
# and the disclaimer all derive from `keyword_scope_options()` below — so a new scope (company,
# location, department…) is one entry here, its columns and its label, and nothing in the
# template or the JS. `description` is nullable and exists only once ADR-0104's column migration
# has run, so any scope naming it is compiled only while `has_description` says the column is
# there, exactly like `has_first_seen`.
KEYWORD_SCOPES: dict[str, KeywordScope] = {
    "title": KeywordScope(("title",), "Title"),
    "description": KeywordScope(("description",), "Description"),
    "both": KeywordScope(("title", "description"), "Title or description"),
}
KEYWORD_DEFAULT_SCOPE = "title"
# The one column a scope can name that a table may not have yet; `build_filter`'s
# `has_description` says whether it does. A name rather than a set: a second such column would
# need its own runtime fact, so it could not simply be listed here.
_OPTIONAL_KEYWORD_COLUMN = "description"
# Enough for "senior backend kubernetes aws remote"; a bound because every term is one more LIKE
# per scoped column on every count the facet strip issues.
_KEYWORD_MAX_TERMS = 5


@dataclass(frozen=True)
class SearchFilters:
    """The full Search-filter vocabulary (ADR-0149) — every structured constraint a user (or a
    Subscription) can set, parsed once by :meth:`JobSearch.parse_filters` and compiled by
    :func:`build_filter`. Every field defaults to "unset", exactly as an absent query-string
    parameter compiles to no clause.

    Deliberately carries nothing about the *table* being queried — no ATS whitelist, no column
    migration state. That is :class:`IndexCapabilities`, a separate object, so that a facet count
    can vary one field here through :func:`dataclasses.replace` without touching the other, and
    so ``alerts.store.ALLOWED_SEARCH_FILTERS`` has one flat, real vocabulary of filter names to be
    checked against — no runtime facts mixed in that a subset check would have to know to exclude.
    """

    remote: bool = False
    max_years: int | None = None
    ats: str | None = None
    etype: str | None = None
    india: str | None = None
    location: str | None = None
    company: str | None = None
    has_salary: bool = False
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str | None = None
    posted_within: int | None = None
    posted_sortable: bool = False
    seen_within: int | None = None
    posted_after: str | None = None
    posted_before: str | None = None
    seen_after: str | None = None
    seen_before: str | None = None
    first_seen_after: str | None = None
    kw: str | None = None
    kw_in: str | None = None


@dataclass(frozen=True)
class IndexCapabilities:
    """Runtime facts about the currently-open Search index (ADR-0149) — never a user choice.

    Learned once per process in :meth:`JobSearch.__init__` (two full-table scans plus a schema
    check) and handed to :func:`build_filter` and :func:`headstart.facets.counts` as one object,
    in place of the loose keyword arguments both used to take. ``atses``, ``has_first_seen``
    and ``has_min_salary_annual`` carry no default: forgetting one used to silently drop the ATS
    whitelist, or turn ADR-0035's exact Watermark cutoff into no clause at all, so a caller that
    builds this by hand must state them. ``currencies`` and the optional-column/acceleration flags
    default because forgetting them only ever selects a safe fallback or narrows a feature to
    "not offered on this table" — never widens what matches or corrupts a result.
    """

    atses: Collection[str]
    has_first_seen: bool
    has_min_salary_annual: bool
    currencies: Collection[str] = ()
    has_description: bool = False
    has_country: bool = False
    has_employment_type_flags: bool = False
    has_description_stored: bool = False
    has_salary_known: bool = False
    has_posted_at_comparable: bool = False
    has_experience_filter_flags: bool = False


def _like(term: str) -> str:
    r"""A user term made safe for a quoted LIKE pattern: metacharacters escaped, quotes doubled.

    Doubling quotes keeps the term inside its literal; escaping `%`, `_` and `\` is what keeps it
    *meaning* what was typed, which is the substring match ADR-0104 specifies. Unescaped, a term
    is silently promoted to a wildcard pattern — measured on the local 318,003-row snapshot of
    the served table (2026-09-06): company "100%" matched 30 rows where exactly 1 is right,
    location "new_york" 9,004 against 8, and the keyword "c_" 199,591 rows — 63% of the table —
    against 243. `\` is on the list because DataFusion already treats it as LIKE's escape
    character with no ESCAPE clause present, so "AT\T" reads as "att" today (698 rows).

    No ESCAPE clause is emitted. Backslash is not just the default, it is the only character
    DataFusion accepts there ("LIKE does not support escape_char other than the backslash"), so
    the clause can only ever restate the default — measured identical with and without it, on
    both the served table and a fixture holding literal `%`, `_` and `\` values.

    The 60-char cap stays on the raw term, ahead of the escaping, for two reasons: it keeps the
    cap denominated in what the user typed, and cutting afterwards could split a `\x` pair and
    leave a trailing lone backslash, which escapes the pattern's own closing `%` and then matches
    nothing at all (measured: 0 rows).
    """
    return _escape_like(term[:60]).lower()


def _escape_like(term: str) -> str:
    r"""LIKE metacharacters escaped and quotes doubled — case and length left alone.

    Split out of :func:`_like` for :func:`board_clause`, which needs the escaping without the
    other two steps: a Board key is matched at full length (a Taleo key is a whole URL, and the
    60-char cap would turn it into a *shorter prefix* that hides more Boards than were chosen)
    and it lowercases both sides of its own comparison rather than only the pattern.
    """
    for char in ("\\", "%", "_"):
        term = term.replace(char, "\\" + char)
    return term.replace("'", "''")


def board_clause(boards: Collection[str], *, exclude: bool) -> str | None:
    """A where-clause over whole Boards, or None when there are none to name (ADR-0171).

    A Job id is ``{board_key}:{native_id}``, so a Board is a prefix match on ``id`` —
    ``id LIKE 'greenhouse:acme:%'``. The trailing colon is load-bearing: without it
    ``greenhouse:acme`` would also match a Board called ``greenhouse:acmecorp``, quietly hiding
    or following a company the user never chose.

    Kept **out** of :class:`SearchFilters` deliberately. That class is the vocabulary of controls
    a user sets, and a Saved Set serializes it (``alerts.store.ALLOWED_SEARCH_FILTERS``); baking
    a follow list into a stored Set would freeze it at save time, so a company followed later
    would never appear in a Set saved earlier. Account state is applied fresh on every request
    instead, which is also how the Matches tab already treats "current".

    Terms are escaped even though these strings come from our own ledger rather than a query
    string — a Board key legitimately contains ``_`` (``workday:ngc/Northrop_Grumman_External_Site``),
    which is a LIKE wildcard, and unescaped it would match Boards nobody chose.

    Both sides are lowercased, which has a second effect worth stating: the served index holds
    335 groups of Board keys that differ only in casing (43,067 rows), one company recorded
    twice. Matching case-insensitively means hiding or following such a company catches both
    spellings, where an exact match would silently catch one.
    """
    terms = [
        f"lower(id) LIKE '{_escape_like(board + ':').lower()}%'"
        for board in sorted(set(boards))
        if board
    ]
    if not terms:
        return None
    joined = " OR ".join(terms)
    return f"NOT ({joined})" if exclude else f"({joined})"


def with_extra(where: str | None, extra: str | None) -> str | None:
    """``where`` narrowed by ``extra``, either of which may be absent.

    One helper rather than the same three lines in :meth:`JobSearch.run` and
    :meth:`JobSearch.facets`: the ranked list and the counts beside it must be narrowed
    identically, or the page reports a total for a different query than the one it lists.
    """
    if not extra:
        return where
    return f"({where}) AND {extra}" if where else extra


def account_clause(
    followed: Collection[str], hidden: Collection[str], *, mine: bool
) -> str | None:
    """The whole follow/hide rule for one request (ADR-0171), or None when it says nothing.

    Stated here rather than in each app, because there are two apps serving the same UI — the
    Space and the local renderer (ADR-0042) — and a rule written twice is a rule that can come
    to disagree. They differ only in where the lists come from.

    Hidden Boards are excluded on **every** request, with or without ``mine``: hiding a company
    means not seeing it, not "not seeing it while a toggle happens to be on". ``mine`` with an
    **empty** follow list compiles to ``false`` rather than to no clause at all — silently
    widening to the whole index is the opposite of what was asked.
    """
    clauses = []
    if mine:
        clauses.append(board_clause(followed, exclude=False) or "false")
    if hidden:
        clauses.append(board_clause(hidden, exclude=True))
    return " AND ".join(c for c in clauses if c) or None


def request_account_clause(
    args: Mapping[str, str], followed: Collection[str], hidden: Collection[str]
) -> str | None:
    """:func:`account_clause` for one request, ``mine`` read off its query string (ADR-0171).

    Both apps call this with their own Account's lists — the Space from the signed-in Account's
    stored record, the local renderer from its one in-memory record — so the query-string rule
    for ``mine`` is written once, beside the clause it switches, rather than in each app.
    """
    return account_clause(followed, hidden, mine=args.get("mine") in ("1", "true"))


def _keyword_terms(kw: str) -> list[str]:
    """The Keyword filter's terms: whitespace-split, each escaped by :func:`_like`, capped.

    Substring, not whole-word, and deliberately so (ADR-0104): a word-boundary regex has no
    lookarounds in DataFusion's Rust engine, so `\\b` silently never matches `c++`, `.net` or
    `c#` — and a keyword box that cannot find "c++" is a worse failure than "java" also matching
    "javascript". Substring is also exactly how `location` and `company` already match, so this
    adds no second escaping path to reason about.
    """
    return [_like(t) for t in kw.split() if t][:_KEYWORD_MAX_TERMS]


def _keyword_columns(scope: str | None, has_description: bool) -> tuple[str, ...]:
    """The columns a scope compiles to on *this* table — an optional column only once it exists."""
    found = KEYWORD_SCOPES.get(scope or KEYWORD_DEFAULT_SCOPE)
    columns = found.columns if found else ()
    return tuple(c for c in columns if c != _OPTIONAL_KEYWORD_COLUMN or has_description)


def keyword_scope_options() -> list[tuple[str, str, bool]]:
    """``(value, label, needs_description)`` per scope, in map order — what the rail renders.

    The single place the template and the JS learn the scopes from, so the <select>, which
    options are disabled before the column exists, and which scopes carry the coverage disclaimer
    all follow the map rather than restating it.
    """
    return [
        (value, scope.label, _OPTIONAL_KEYWORD_COLUMN in scope.columns)
        for value, scope in KEYWORD_SCOPES.items()
    ]


def _ago(**window: int) -> datetime:
    """``now`` minus one recency window, with the overflow answered as a bad date.

    Both windows arrive as unbounded ints off the query string, and each blows up twice over:
    739,865 days (17,756,755 hours) walks ``datetime`` below year 1 — one past the last that
    lands on 0001-01-01, and both creep by a day each day as ``now`` moves — and a magnitude past 999,999,999
    days breaks ``timedelta`` itself — in both directions, since a negative window that large
    runs off the far end instead. Converted here rather than clamped in :func:`headstart.search._int_arg`, which
    is shared with ``k``, ``page`` and the salary bounds and has no business knowing what a date
    can hold; here it matches ``_next_day``'s identical treatment further down and also covers the facet
    counts, which call :func:`build_filter` directly rather than through the parse step.
    """
    try:
        return datetime.now(UTC) - timedelta(**window)
    except OverflowError as exc:  # off the calendar, or past timedelta's cap
        unit, size = next(iter(window.items()))
        raise ValueError(f"recency window out of range: {size} {unit}") from exc


def _next_day(value: str) -> str:
    """The day after ``value``, so an inclusive "before" can compare strictly below it.

    Both date columns hold date-or-datetime ISO strings and `'2026-08-10T12:00' > '2026-08-10'`,
    so an inclusive upper bound has to be expressed as "< the next day" rather than "<= this one".
    Module level rather than nested inside :func:`build_filter`, where it captured nothing from
    the enclosing scope and could not be read or tested on its own.
    """
    try:
        return (date.fromisoformat(value) + timedelta(days=1)).isoformat()
    except OverflowError as exc:  # 9999-12-31 + 1 day; a 400 like any bad date
        raise ValueError(f"date out of range: {value!r}") from exc


def _keyword_clauses(
    *, kw: str | None, kw_in: str | None, has_description: bool
) -> list[str]:
    """The Keyword filter's clauses (ADR-0104) — one per term, OR'd across the scope's columns."""
    filters: list[str] = []
    if kw:
        # Per term, OR across the scope's columns; AND across terms. A scope whose only column is
        # absent compiles to nothing at all — dark, never an error — like every optional-column
        # filter in this module.
        columns = _keyword_columns(kw_in, has_description)
        for term in _keyword_terms(kw) if columns else ():
            filters.append(
                "(" + " OR ".join(f"lower({c}) LIKE '%{term}%'" for c in columns) + ")"
            )
    return filters


def _salary_clauses(
    *,
    has_salary: bool,
    salary_min: int | None,
    salary_max: int | None,
    salary_currency: str | None,
    currencies: Collection[str],
    has_min_salary_annual: bool,
    has_salary_known: bool,
) -> list[str]:
    """The "shows salary" switch and the ADR-0082 salary bracket, which share a column."""
    filters: list[str] = []
    if has_salary and has_min_salary_annual:
        # `min_salary_annual` (ADR-0082), not the raw `salary` string: `salary` is only ever
        # populated from a scraper's own structured field, so gating on it silently excluded
        # every Job whose salary is only known via Tier-2 description-mining — most of this
        # initiative's own measured coverage on most ATSes. `min_salary_annual` is the fully
        # reconciled cascade result (Tier 1 or Tier 2), so it's the correct "do we have a real
        # number" check either way. Guarded like `has_first_seen` above: a table LanceDB
        # hasn't migrated onto the new columns yet would error on every query otherwise —
        # the feature stays dark until then rather than 500ing.
        filters.append(salary_known_filter.clause(has_salary_known))

    # The salary bracket (issue #275) is stated in ONE currency the user picks, and that is not
    # a UI nicety: salary is period-normalised but stored in the employer's own currency
    # (ADR-0082), so a bare number compared across currencies would rank 60,000 INR beside
    # 60,000 USD as equals. That currency is what the bounds are converted FROM further down
    # (ADR-0117); it comes first and is whitelisted against what the table actually holds,
    # exactly like `ats` — never interpolated from free text. Without one the bracket does not
    # apply unscoped, because an unscoped bracket is the wrong answer, not a looser one — it
    # takes :data:`SALARY_DEFAULT_CURRENCY` instead.
    #
    # The currency is a *modifier of the bracket*, not a filter of its own: picking one with
    # both bounds empty must not quietly cut the result set to the 28.5% of Jobs that carry a
    # salary at all (measured 2026-08-25), which is what filtering on it alone would do. So it
    # only bites once the user has actually named a bound.
    #
    # The default is applied HERE and not in `parse_filters` for two reasons. It used to live
    # only in the browser's <select> (`ui/static/app.js`, control `salcur`), so a bound with no
    # currency compiled to no filter at all and the numeric constraint was silently discarded for
    # every caller that is not that <select> — a hand-built `/search?salary_min=…`, or
    # `scripts/eval/verify_filters.py`. (Not the alerts path: `alerts.store.ALLOWED_SEARCH_FILTERS`
    # excludes the salary keys, so a Subscription can never carry a bracket at all.)
    #
    # An unrecognised currency falls back to the same default rather than raising, which is the
    # rule ADR-0084 states — whitelisted "like `ats`", and `ats` ignores what it does not know.
    # The alternative, a 400, was tried and reverted: it bought nothing, since the picker is
    # rendered from `currencies` and only a hand-built request could reach it, and it made this
    # parameter contradict `kw_in`, the other "modifier with a default".
    #
    # It resolves the fallback in a different *layer* from `kw_in`, though, and deliberately.
    # `kw_in`'s lands in `parse_filters` and this builder compiles nothing for a scope it does not
    # know; that is enough for `kw_in` because every path into the builder — `/search`, and
    # `facets` via `parse_filters` output — has already normalised it. It is not enough here: the
    # bug this fixes was reported against `scripts/eval/verify_filters.py` and hand-built requests,
    # which call the reference compiler (ADR-0031) directly and never see the parse step.
    #
    # The default is resolved against `currencies` too, rather than trusted. It is a module
    # constant so it can never be free text, but a table holding no USD salary would otherwise get
    # a clause matching nothing — the silent wrong answer this whole block exists to remove, just
    # relocated. Where even the default is unavailable the bracket does not apply.
    if has_min_salary_annual and (salary_min is not None or salary_max is not None):
        currency = (
            salary_currency
            if salary_currency in currencies
            else SALARY_DEFAULT_CURRENCY
        )
    else:
        currency = None
    if currency in currencies:
        # The bracket is compared ACROSS currencies (ADR-0117), not within the one picked.
        # Pinning `salary_currency = 'USD'` made a USD range silently drop every INR job —
        # fewer results, no stated reason. Each currency present in the table gets the user's
        # bounds restated in its own units, and the whole thing ORs together; the numbers in
        # the where-clause stay the employer's own, so nothing is rewritten in the index.
        #
        # A currency with no rate is left OUT rather than compared at 1:1, and if the table
        # is unavailable entirely this falls back to the single-currency clause that predates
        # ADR-0117 — a narrower answer, never a wrong one.
        fx_table = fx.table()
        rates = (fx_table or {}).get("rates") or {}
        comparable = [c for c in currencies if c in rates] if currency in rates else []
        for_currencies = comparable or [currency]
        arms = []
        for other in for_currencies:
            bounds = []
            for is_floor, value, template in (
                # The job's TOP of range clears the user's floor: a 90k-140k posting answers
                # "at least 100k". `max_salary_annual` is null on single-figure postings, so
                # COALESCE falls back to the one number there is rather than dropping the row.
                (
                    True,
                    salary_min,
                    "COALESCE(max_salary_annual, min_salary_annual) >= {}",
                ),
                # ...and its BOTTOM sits under the ceiling, so the two together are an overlap
                # test rather than containment: a band wider than the user's still qualifies.
                (False, salary_max, "min_salary_annual <= {}"),
            ):
                if value is None:
                    continue
                if other == currency:
                    # The user's own currency is not converted, so it keeps the number they
                    # typed exactly — rounding it would move a bound nobody asked to move.
                    bounds.append(template.format(int(value)))
                    continue
                here = fx.convert(float(value), currency, other, rates)
                if here is None:
                    bounds = []
                    break
                # Converted bounds round OUTWARD — floor down, ceiling up — so arithmetic can
                # never drop a job sitting exactly on the boundary the user asked for. Keyed on
                # `is_floor`, not on `value is salary_min`: Python interns small ints, so a range
                # whose two ends are equal and under 257 made both bounds the same object and
                # rounded the ceiling inward — the exact opposite of the guarantee above.
                bounds.append(template.format(int(here) if is_floor else int(here) + 1))
            if bounds:
                arms.append(" AND ".join([f"salary_currency = '{other}'", *bounds]))
        if arms:
            filters.append(
                "(" + " OR ".join(f"({arm})" for arm in arms) + ")"
                if len(arms) > 1
                else arms[0]
            )
        else:
            filters.append(f"salary_currency = '{currency}'")
    return filters


def _posted_clauses(
    *,
    posted_sortable: bool,
    posted_within: int | None,
    posted_after: str | None,
    posted_before: str | None,
    has_posted_at_comparable: bool,
) -> list[str]:
    """Everything keyed on ``posted_at`` — the company's own date.

    One rule holds these together: `posted_at` is a raw string the ATSes write, so every clause
    here carries the same `LIKE '____-__-__%'` shape guard against the 3% that are not ISO. The
    column is in the table's base schema, so unlike `first_seen` there is nothing to be dark
    about.
    """
    filters: list[str] = []
    guard = posted_date_guard.clause(has_posted_at_comparable)
    if posted_sortable:
        # Ordering by `posted_at` needs the same shape guard filtering by it does, and it has
        # to be compiled HERE rather than bolted onto the where-clause in `run` — otherwise the
        # facet counts, which never see the sort, would count rows the sorted list excludes and
        # the header would overstate the result set by the 8.4% carrying no readable date.
        filters.append(f"({guard})")
    if posted_within is not None:
        # posted_at is a raw string; ISO-prefixed values (97%) compare correctly. The LIKE
        # shape guard excludes the rest — non-ISO forms like darwinbox's legacy
        # '21-Apr-2026' sort lexicographically ABOVE any ISO cutoff and would otherwise
        # leak into every window.
        cutoff = _ago(days=int(posted_within)).strftime("%Y-%m-%d")
        filters.append(f"(posted_at >= '{cutoff}' AND {guard})")

    # Custom date ranges (both ends optional, both inclusive). Each value arrives as free
    # text and lands in a where-clause, so it is re-serialized through date.fromisoformat —
    # garbage raises ValueError, which the routes answer as 400, and nothing user-typed is
    # ever interpolated. `_next_day` carries why the upper bound is exclusive.
    if posted_after:
        start = date.fromisoformat(posted_after).isoformat()
        filters.append(f"(posted_at >= '{start}' AND {guard})")
    if posted_before:
        filters.append(f"(posted_at < '{_next_day(posted_before)}' AND {guard})")
    return filters


def _first_seen_clauses(
    *,
    seen_after: str | None,
    seen_before: str | None,
    seen_within: int | None,
    first_seen_after: str | None,
    has_first_seen: bool,
) -> list[str]:
    """Everything keyed on ``first_seen`` — when *we* indexed the Job.

    One rule holds these together, and it is not the one above: `first_seen` arrives by migration,
    so the whole group is dark until the column exists (ADR-0031). Hoisting that to a single
    guard is why none of the four clauses repeats it. No shape guard either — we write this
    column ourselves, so it is always ISO-8601 UTC.
    """
    if not has_first_seen:
        return []
    filters: list[str] = []
    if seen_after:
        start = date.fromisoformat(seen_after).isoformat()
        filters.append(f"first_seen >= '{start}'")
    if seen_before:
        filters.append(f"first_seen < '{_next_day(seen_before)}'")
    if seen_within is not None:
        # In HOURS, not days: this window is meant to be shorter than one pipeline cycle.
        # Rows predating the column are null, and `NULL >= '…'` is never true, so they drop
        # out on their own (ADR-0031).
        since = _ago(hours=int(seen_within)).isoformat(timespec="seconds")
        filters.append(f"first_seen >= '{since}'")
    if first_seen_after:
        # The alerts run's exact cutoff (ADR-0035), beside the UI's hour-granular window: a
        # Digest must carry precisely what appeared since that Subscription's Watermark, and
        # rounding up to whole hours would re-offer rows already mailed. Strictly `>`, so a
        # Watermark taken from a row's own `first_seen` cannot re-select that row.
        #
        # This is the one recency value that arrives as free text and lands in a
        # where-clause, so it is re-serialized from a parsed datetime rather than
        # interpolated as given — anything unparseable raises ValueError, which the routes
        # answer as 400.
        moment = datetime.fromisoformat(first_seen_after).isoformat(timespec="seconds")
        filters.append(f"first_seen > '{moment}'")
    return filters


def build_filter(filters: SearchFilters, capabilities: IndexCapabilities) -> str | None:
    """The prod-table where-clause — the reference Search-filter compiler (ADR-0031, ADR-0149).

    Two objects rather than one flat parameter list. ``filters`` is every value a user (or a
    Subscription) actually set — :class:`SearchFilters`. ``capabilities`` is what this
    particular Search index happens to support right now — :class:`IndexCapabilities`: the ATS
    and currency whitelists the ``ats``/``salary_currency`` clauses may name, and which
    migration-only columns (``first_seen``, the ADR-0082 salary columns, ``description``,
    ``country``) it carries. Both are required, and deliberately so, for the reason the loose
    facts used to be: a caller that reaches this directly (``scripts/bench/scalar_index_bench_v2
    .py``, a hand-built request) that skimps on ``capabilities`` can silently drop the ATS
    whitelist, turn ADR-0035's exact Watermark cutoff into no clause at all, or make a salary
    bound compile to **nothing** — see :class:`IndexCapabilities`'s docstring for which of its
    fields fail loudly (no default) and which fail quietly (default, but only by narrowing a
    feature to "not offered here").

    Every in-repo caller reaches this through :meth:`JobSearch.parse_filters` (``filters``) and
    :attr:`JobSearch.capabilities` (``capabilities``), which are always supplied together.
    """
    clauses: list[str] = []
    if filters.remote:
        clauses.append("remote = true")
    if filters.max_years is not None:
        clauses.append(
            experience_filter.clause(
                int(filters.max_years), capabilities.has_experience_filter_flags
            )
        )
    # A value that misses either whitelist drops the filter silently *here* and is reported
    # once, by `JobSearch.parse_filters`, before this compiler is ever entered. It cannot be
    # reported here: `facets.counts` recompiles the same filters once per facet option, so a
    # warning on this line is emitted ~29 times for one bad query-string parameter — see
    # `_warn_unknown_filters`.
    if (
        filters.ats in capabilities.atses
    ):  # whitelist — never interpolated from free text
        clauses.append(f"ats = '{filters.ats}'")
    etype_clause = employment_type_filter.clause(
        filters.etype, capabilities.has_employment_type_flags
    )
    if etype_clause:
        clauses.append(etype_clause)
    if filters.india:
        # Only the whole country is materialized (ADR-0138) — it is the only case the 1,338ms
        # regex alternation was ever measured on; city/region clauses are far smaller and keep
        # the gazetteer clause regardless of `has_country`. See `india_filter`.
        india_clause = india_filter.clause(filters.india, capabilities.has_country)
        if india_clause:
            clauses.append(india_clause)
    if filters.location:
        clauses.append(f"lower(location) LIKE '%{_like(filters.location)}%'")
    if filters.company:
        clauses.append(f"lower(company) LIKE '%{_like(filters.company)}%'")
    # These four append in order, and that order is part of the string this returns —
    # `" AND ".join` below is not a set. SQL's AND commutes, so reordering reads as harmless and
    # is not: every test asserting a whole where-clause would fail, and so would any caller
    # comparing two compiled filters for equality.
    clauses += _keyword_clauses(
        kw=filters.kw, kw_in=filters.kw_in, has_description=capabilities.has_description
    )
    clauses += _salary_clauses(
        has_salary=filters.has_salary,
        salary_min=filters.salary_min,
        salary_max=filters.salary_max,
        salary_currency=filters.salary_currency,
        currencies=capabilities.currencies,
        has_min_salary_annual=capabilities.has_min_salary_annual,
        has_salary_known=capabilities.has_salary_known,
    )
    clauses += _posted_clauses(
        posted_sortable=filters.posted_sortable,
        posted_within=filters.posted_within,
        posted_after=filters.posted_after,
        posted_before=filters.posted_before,
        has_posted_at_comparable=capabilities.has_posted_at_comparable,
    )
    clauses += _first_seen_clauses(
        seen_after=filters.seen_after,
        seen_before=filters.seen_before,
        seen_within=filters.seen_within,
        first_seen_after=filters.first_seen_after,
        has_first_seen=capabilities.has_first_seen,
    )
    return " AND ".join(clauses) if clauses else None
