"""Shared conventions for the embed/search/eval layer (ADR-0005, ADR-0008) — and, since
ADR-0042, the one serving-path search implementation both UIs run.

The model id, the load-bearing task prefixes, the LanceDB table name, the encoder factory,
and the where-clause builders live here once. The embed/search/eval scripts import the
conventions instead of re-declaring their own copies, so a mismatched prefix or model id
can't drift into one script and silently degrade ranking (ADR-0005 warns a wrong prefix
throws no error), and every caller escapes filter input the same way.

:func:`build_filter` is the **reference product filter** — the full Search-filter vocabulary
the UIs expose, previously duplicated in the Space app.

:class:`JobSearch` is the serving path behind one method: built once with the loaded
encoder and the open ``jobs`` table, ``run(args)`` takes a request's query-string mapping
and returns projected result rows. Both the HF Space app and the local dev server are thin
adapters over it — the Space image installs ``headstart`` as a real package (ADR-0153), so
this module imports ``fx``/``geo`` the same way everywhere.

Only the encoder helpers need torch/sentence-transformers; they import lazily so the
constants and the filter builders stay importable (and unit-testable) without the ML stack.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from threading import Lock
from typing import Any, NamedTuple
from urllib.parse import urlsplit

from headstart import fx, geo, log
from headstart.employment_type import FILTERS as EMPLOYMENT_TYPE_FILTERS
from headstart.experience_filter import CEILINGS as EXPERIENCE_FILTER_CEILINGS
from headstart.experience_filter import column as experience_filter_column

# In the Space nothing calls `setup()` (ADR-0153's app.py boots straight into serving), which
# is why the one boot line below is a WARNING — `logging.lastResort` carries WARNING and above
# to stderr with no handler configured, and a served table quietly ignoring whole filters is an
# anomaly by ADR-0039's own definition.
_log = log.get(__name__)

MODEL = "nomic-ai/nomic-embed-text-v1.5"
DOC_PREFIX = "search_document: "  # index time (ADR-0005)
QUERY_PREFIX = "search_query: "  # query time (ADR-0005)
PROD_TABLE = "jobs"  # the product's tech corpus (ADR-0019)


def load_encoder() -> Any:
    """The nomic bi-encoder, on the Apple GPU (MPS, fp16) when available else CPU."""
    import torch
    from sentence_transformers import SentenceTransformer

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model = SentenceTransformer(MODEL, trust_remote_code=True, device=device)
    return model.half() if device == "mps" else model


def encode_query(model: Any, text: str) -> Any:
    """Encode one search query: query prefix, L2-normalized, float32 — ready for cosine search."""
    return model.encode([QUERY_PREFIX + text], normalize_embeddings=True)[0].astype(
        "float32"
    )


# ---- the product search path (ADR-0042) ----
# Everything below moved from the Space app, which had become the de-facto reference while
# this module lagged behind; the Space and the local dev server now both consume this.

# Canonical employment-type filters mapped onto the messy per-ATS raw values
# ("fulltime", "Full-time", "fulltime_permanent", "Permanent / Full-Time", …).
ETYPE_CLAUSES = {
    name: rule.raw_clause() for name, rule in EMPLOYMENT_TYPE_FILTERS.items()
}

# The retained production-table operating point (ADR-0173): IVF-SQ at 80 probes with a 2x
# exact-vector refinement reproduced every top-20 result across 16 real queries and four filter
# selectivities. Lower settings lost results; IVF/HNSW Flat cost over 1.5 GB of extra storage.
ANN_NPROBES = 80
ANN_REFINE_FACTOR = 2
FACET_CACHE_SIZE = 128
FACET_CACHE_TTL_SECONDS = 60
BROWSE_CACHE_SIZE = 64
BROWSE_CACHE_TTL_SECONDS = 60
QUERY_VECTOR_CACHE_SIZE = 128


def _cache_get(cache: OrderedDict, lock: Lock, key: Any, ttl: float) -> Any | None:
    now = time.monotonic()
    with lock:
        cached = cache.pop(key, None)
        if cached is not None and now - cached[0] <= ttl:
            cache[key] = cached
            return cached[1]
    return None


def _cache_put(
    cache: OrderedDict, lock: Lock, key: Any, value: Any, limit: int
) -> None:
    with lock:
        cache[key] = (time.monotonic(), value)
        while len(cache) > limit:
            cache.popitem(last=False)


#: Exactly the columns :meth:`JobSearch.run` reads to build a result row — the projection the
#: served query asks for, rather than every column the table holds.
#:
#: **This is a latency fix, not tidiness.** Without it the sorted-query path materialises
#: `max_k * max_page` = 2,000 whole rows and throws all but these fields away one line later.
#: The 768-float `vector` is the bulk of that payload — a 2,000-row window costs 331 ms whole
#: against 162 ms with only the vector dropped — and a stored `description` (ADR-0104) adds to it
#: wherever the table has one.
#:
#: Measured through :meth:`JobSearch.run` on a 318,003-row table carrying **no index**, which is
#: the shape production is in, and the basis every figure here and in ADR-0084's amendment uses:
#:
#: ===================================  =========  ========
#: path                                 before     after
#: ===================================  =========  ========
#: query + sort (the 2,000-row window)  420.6 ms   256.5 ms
#: browse, no query                     115.9 ms    60.7 ms
#: query, no sort — one page of 20      105.0 ms   103.7 ms
#: ===================================  =========  ========
#:
#: The last row is the control: with 20 rows to carry, projecting is worth nothing, which is what
#: shows the saving is per-row payload rather than anything about the query. An earlier draft of
#: these numbers was taken on a local snapshot that had been given an IVF_PQ index by an unrelated
#: benchmark, and read 264.9 -> 83.6 ms; a faster search makes the payload a larger share, so it
#: flattered the fix.
#:
#: Intersected with the live schema in :meth:`JobSearch.__init__`, never used raw: `select()`
#: **raises** on a column the table lacks, and five of these arrive by migration
#: (`first_seen`, the ADR-0082 salary columns). Naming one unconditionally would turn
#: ADR-0031's dark-until-migrated rule into a 500 on every search.
RESULT_COLUMNS = (
    "id",
    "title",
    "company",
    "location",
    "remote",
    "employment_type",
    "min_years",
    "salary",
    "min_salary_annual",
    "max_salary_annual",
    "salary_currency",
    "salary_source",
    "ats",
    "posted_at",
    "first_seen",
    "url",
)


# The sort control's values, mapped to the column each orders by (issue #275). A whitelist
# because the result reaches an ORDER BY; "rel" is deliberately absent, since relevance is the
# ranking a vector search already applies and asking for it means adding no ordering at all.
#
# `salary` orders by `min_salary_annual`, the ADR-0082 derived column — so it covers the
# description-mined salaries too, not only the boards that publish a structured field. Unlike
# `posted` it needs no shape guard: the column is a real number or NULL, and NULLs sort last
# rather than leaking to the top of a descending order the way a non-ISO date string does.
SORT_COLUMNS = {
    "posted": "posted_at",
    "seen": "first_seen",
    "salary": "min_salary_annual",
}

#: Sort columns holding a number rather than a string. The distinction only matters on the
#: ranked path, which re-orders its window in Python: a missing value has to stand in as
#: something the rest of the column can be compared against, and `None or ""` would put a
#: `str` in a tuple beside `float`s and raise `TypeError` on the first comparison.
_NUMERIC_SORTS = frozenset({"min_salary_annual"})

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


def _int_arg(args: Mapping[str, str]) -> Callable[[str], int | None]:
    """Read an int out of a query string, or None when it isn't there.

    None rather than a default, so a caller can tell "absent" from "zero" — see the clamp in
    :meth:`JobSearch.run`, which is where that distinction earns its keep. Raises ValueError on
    garbage, which the routes answer as 400.
    """

    def read(name: str) -> int | None:
        raw = args.get(name)
        return int(raw) if raw else None

    return read


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


# TEMPORARY (2026-07-07) — INTENDED FOR REMOVAL. Darwinbox rows scraped before the
# candidatev2 URL fix carry the old `/ms/candidate/careers/jobs/{id}` link, which on v2
# tenants redirects to the careers home instead of the job. The stored data self-heals only
# as those postings turn over (sync leaves re-seen ids untouched — headstart.ingest.index_plan),
# so this rewrites the derivable URL at serve time as a stopgap. Remove once the darwinbox
# rows have healed (or once sync refreshes changed metadata for re-seen ids — the proper fix).
# Caveat: a legacy `new_careers=false` tenant's old-format URL would be wrongly rewritten,
# but none exist today (60/60 surveyed are v2).
_DARWINBOX_OLD = "/ms/candidate/careers/jobs/"
_DARWINBOX_NEW = "/ms/candidatev2/main/careers/jobDetails/"

# TEMPORARY (2026-08-12) — INTENDED FOR REMOVAL, and the same stopgap shape as darwinbox's
# above. Recruitee rows scraped before the tenant-host fix carry the customer's own vanity
# domain (the API's `careers_url`), and a third of those domains do not serve the board at
# all — see scrapers/recruitee._offer_url for the measurement. The right link is derivable
# from what the row already carries: the id holds the tenant, the path holds the offer slug.
# Rewriting here spares users the wait for every recruitee board to turn over. Remove once
# they have.
_RECRUITEE_DOMAIN = ".recruitee.com"


def _rehost_recruitee(job_id: str | None, url: str) -> str:
    """A recruitee link moved onto the tenant's own host, or the URL unchanged.

    Left alone when it is already canonical, when the id isn't the expected
    ``{ats}:{tenant}:{native}``, or when the path has no ``/o/`` segment to read the offer
    from — a URL this can't rebuild confidently is better served as-is than mangled.
    """
    parts = (job_id or "").split(":")
    split = urlsplit(url)
    if (
        split.netloc.endswith(_RECRUITEE_DOMAIN)
        or len(parts) < 3
        or "/o/" not in split.path
    ):
        return url
    offer = split.path.split("/o/", 1)[1].strip("/").split("/")[0]
    return f"https://{parts[1]}{_RECRUITEE_DOMAIN}/o/{offer}" if offer else url


def _canonical_url(ats: str | None, url: str | None, job_id: str | None) -> str | None:
    """Serve-time normalization of links the stored row gets wrong (see the two notes above).

    ``job_id`` is required rather than defaulted: recruitee's rewrite reads the tenant out of
    it, and a caller that forgot to pass it would silently keep serving the dead link.

    Stays here, hardcoding ``"darwinbox"``/``"recruitee"``, rather than becoming a
    ``canonical_url`` hook on ``DarwinboxScraper``/``RecruiteeScraper`` (ADR-0153): this module
    is deployed to the HF Space as a flat standalone file (``deploy-space.yml`` copies only
    ``search.py`` and a short list of siblings, never ``headstart.scrapers``, which pulls in
    ``curl_cffi`` and the network-fetch stack the served app has no use for) — importing the
    scraper registry here would break the deployed app's import graph for a repair this narrow.
    What ADR-0153 *does* close: this function's two rewrites are pinned to
    ``DarwinboxScraper.url_shape``/``RecruiteeScraper.url_shape`` by
    ``tests/test_search.py::test_canonical_url_rewrites_match_the_scrapers_own_url_shape`` — a
    repair whose output stops matching its scraper's declared shape fails CI, which is the
    structural check this repo-side test can give without shipping scraper code to the Space.
    """
    if not url:
        return url
    if ats == "darwinbox" and _DARWINBOX_OLD in url:
        return url.replace(_DARWINBOX_OLD, _DARWINBOX_NEW, 1)
    if ats == "recruitee":
        return _rehost_recruitee(job_id, url)
    return url


def _ago(**window: int) -> datetime:
    """``now`` minus one recency window, with the overflow answered as a bad date.

    Both windows arrive as unbounded ints off the query string, and each blows up twice over:
    739,865 days (17,756,755 hours) walks ``datetime`` below year 1 — one past the last that
    lands on 0001-01-01, and both creep by a day each day as ``now`` moves — and a magnitude past 999,999,999
    days breaks ``timedelta`` itself — in both directions, since a negative window that large
    runs off the far end instead. Converted here rather than clamped in :func:`_int_arg`, which
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
        filters.append(
            "salary_known = true"
            if has_salary_known
            else "min_salary_annual IS NOT NULL"
        )

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


def posted_at_is_comparable(value: str | None) -> bool:
    """Whether the raw date matches Lance's legacy ``LIKE '____-__-__%'`` guard."""
    return bool(value and len(value) >= 10 and value[4] == "-" and value[7] == "-")


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
    guard = (
        "posted_at_comparable = true"
        if has_posted_at_comparable
        else "posted_at LIKE '____-__-__%'"
    )
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
        years = int(filters.max_years)
        clauses.append(
            f"{experience_filter_column(years)} = true"
            if capabilities.has_experience_filter_flags
            and years in EXPERIENCE_FILTER_CEILINGS
            else f"(min_years <= {years} OR min_years IS NULL)"
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
    if filters.etype in ETYPE_CLAUSES:
        clauses.append(
            f"{EMPLOYMENT_TYPE_FILTERS[filters.etype].column} = true"
            if capabilities.has_employment_type_flags
            else ETYPE_CLAUSES[filters.etype]
        )
    if filters.india:
        # "india" is the exact sentinel `geo.where()` itself uses for "whole country" (as
        # opposed to a REGIONS/CITIES key like "bengaluru"), and it is the only case the 1,338ms
        # regex alternation was ever measured on (ADR-0138) — city/region clauses are far
        # smaller and stay on the unchanged path below regardless of `has_country`.
        if filters.india == "india" and capabilities.has_country:
            clauses.append("country = 'IN'")
        else:
            clause = geo.where(
                filters.india
            )  # canonical-place lookup — unknown values are ignored
            if clause:
                clauses.append(clause)
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


def _warn_unknown_filters(
    ats: str | None, etype: str | None, atses: Collection[str]
) -> None:
    """Say, once per request, that a query-string value missed its whitelist.

    A value that misses drops its filter entirely and the search runs unfiltered — the widest
    possible answer to a request that asked to be narrowed — so it is worth a line. It
    cannot raise instead, because the whitelist is whatever the served table happens to hold
    and a stale bookmark must not 500.

    It lives here rather than beside the drop in :func:`build_filter` because that compiler is
    re-entered once per facet option: :func:`headstart.facets.counts` recompiles one request's
    kwargs 32 times, so a line on the drop itself came out **58 times** for a single
    ``?ats=bogus&etype=bogus`` — unauthenticated, user-controlled amplification, ~29 lines per
    bad parameter from any crawler with a stale link. :meth:`JobSearch.parse_filters` parses a
    request exactly once, so this is said exactly once.

    Rendered through ``%r`` and clipped: the value comes from the query string, so it is never
    the format string itself and cannot open a second line in the log.
    """
    if ats and ats not in atses:
        _log.warning("filter dropped: ats %.40r is not in this table", ats)
    if etype and etype not in ETYPE_CLAUSES:
        _log.warning(
            "filter dropped: employment_type %.40r is not a known value", etype
        )


class JobSearch:
    """The serving-path search behind one method: parse → filter → rank → project.

    Built once per process with the loaded encoder and the open LanceDB ``jobs`` table; the
    constructor scans the table's ATS whitelist and schema once. ``run(args)`` takes a
    request's query-string mapping and returns the projected result rows for one page.
    ``ValueError`` on garbage filter input, which the routes answer as 400. ``max_k`` caps
    the page size and ``max_page`` caps how far ``page`` can walk (ADR-0074) — together they
    bound how much of the table one Search can ever address, so a crafted request can't dump
    it.

    An empty query (ADR-0074) does not call the encoder — it lists the table's newest rows by
    ``first_seen`` instead of ranking by similarity, and every row's ``score`` comes back
    ``None`` rather than a number that would imply a relevance this ranking never computed.
    Filters and pagination apply identically either way.

    The two facts the UI templates need — :attr:`atses` for the Board dropdown and
    :attr:`has_first_seen` for the "first seen" control — are attributes, not methods, so a
    template context can carry them straight through. :attr:`capabilities` (ADR-0149) bundles
    those and the other runtime facts into one :class:`IndexCapabilities` for
    :func:`build_filter` and :func:`headstart.facets.counts`; the capability attributes stay
    directly settable, since templates and tests both read and monkeypatch them one at a time.
    """

    def __init__(self, model: Any, table: Any, *, max_k: int = 100, max_page: int = 20):
        self._model = model
        self._table = table
        self.max_k = max_k
        self.max_page = max_page
        # the ATSes actually present in the index — feeds the dropdown and the whitelist
        self.atses = sorted(
            {
                r["ats"]
                for r in table.search().select(["ats"]).limit(1_000_000).to_list()
            }
        )
        # `first_seen` only appears on the first pipeline run after ADR-0031; filtering on
        # a column the table lacks errors every query, so the feature stays dark until then.
        self.has_first_seen = "first_seen" in table.schema.names
        # Same reasoning for the ADR-0082 salary columns, added by the same class of
        # idempotent migration (`index.py`'s `_salary_fields`) — a table that hasn't synced
        # since would error on `has_salary=true` rather than just not supporting it yet.
        self.has_min_salary_annual = "min_salary_annual" in table.schema.names
        # The Keyword filter's description scope (ADR-0104), same dark-until-migrated rule: the
        # column arrives with the first `index sync` after that ADR, and the UI disables the
        # scope until it does rather than 500ing on it.
        self.has_description = "description" in table.schema.names
        # The materialized India-filter column (ADR-0138), same rule again: until a table has
        # synced since, `build_filter` falls back to `geo.where("india")`'s slower-but-correct
        # regex alternation rather than erroring on a column that isn't there yet.
        self.has_country = "country" in table.schema.names
        # The employment-type booleans are an optional acceleration layer. A pre-migration
        # table keeps the raw LIKE clauses above, so this can never disable the filter.
        self.has_employment_type_flags = all(
            rule.column in table.schema.names
            for rule in EMPLOYMENT_TYPE_FILTERS.values()
        )
        self.has_description_stored = "description_stored" in table.schema.names
        self.has_salary_known = "salary_known" in table.schema.names
        self.has_posted_at_comparable = "posted_at_comparable" in table.schema.names
        self.has_experience_filter_flags = all(
            experience_filter_column(ceiling) in table.schema.names
            for ceiling in EXPERIENCE_FILTER_CEILINGS
        )
        list_indices = getattr(table, "list_indices", None)
        self.has_vector_index = bool(
            list_indices and any("vector" in index.columns for index in list_indices())
        )
        #: :data:`RESULT_COLUMNS` narrowed to what this table actually has — see that constant
        #: for why the intersection is mandatory rather than defensive.
        self.projection = tuple(c for c in RESULT_COLUMNS if c in table.schema.names)
        # The currency whitelist for the ADR-0082 salary bracket, learned the same way and for
        # the same reason as `atses`: it lands in a where-clause, so it is matched against what
        # the table holds rather than interpolated from the query string.
        self.currencies = (
            sorted(
                {
                    r["salary_currency"]
                    for r in table.search()
                    .select(["salary_currency"])
                    .limit(1_000_000)
                    .to_list()
                    if r.get("salary_currency")
                }
            )
            if self.has_min_salary_annual
            else []
        )
        # The Data tab's coverage counts (ADR-0113), filled on first use. Not counted here:
        # boot is the one moment a cold Space has a visitor waiting on it, and nobody has
        # asked for the tab yet.
        self._coverage: dict[str, Any] | None = None
        # Facets ignore the semantic query and the served table is immutable for this process's
        # lifetime (the Space restarts when a new table lands). Cache only the parsed structured
        # filters, bounded so arbitrary public requests cannot grow memory without limit.
        self._facet_cache: OrderedDict[
            tuple[SearchFilters, str | None], tuple[float, dict[str, Any]]
        ] = OrderedDict()
        self._facet_cache_lock = Lock()
        self._browse_cache: OrderedDict[tuple[Any, ...], tuple[float, list[dict]]] = (
            OrderedDict()
        )
        self._browse_cache_lock = Lock()
        # Changing a filter or page must not run the same model inference again. Query vectors
        # depend only on this process's immutable model, so they need a size bound but no TTL.
        self._query_vector_cache: OrderedDict[str, Any] = OrderedDict()
        self._query_vector_cache_lock = Lock()
        # The four flags above are each a whole feature silently switched off: an un-migrated
        # table ignores every `seen_within`/`first_seen_after` bound, the salary bracket and
        # `has_salary`, the Keyword filter's description scope, and the `seen`/`salary` sorts
        # (`run` below quietly drops those too) — and answers each request as though no such
        # filter had been asked for. That is the shape of the incident where the derived salary
        # columns were never read by the serving path and nothing said so, so it is said here,
        # once, naming the columns rather than the features. A fully migrated table logs nothing.
        # `country` degrades more gently than the other three: its filter (`india="india"`) still
        # answers correctly without it, just slower (ADR-0138), so it is named here for visibility
        # but never disables a feature the way the other three can.
        dark = [
            column
            for column, live in (
                ("first_seen", self.has_first_seen),
                ("min_salary_annual", self.has_min_salary_annual),
                ("description", self.has_description),
                ("country", self.has_country),
            )
            if not live
        ]
        if dark:
            _log.warning(
                "served table is missing %s — every filter and sort keyed on those columns "
                "is disabled for this table, not failing",
                ", ".join(dark),
            )

    @property
    def capabilities(self) -> IndexCapabilities:
        """This table's :class:`IndexCapabilities` (ADR-0149) — the attributes above,
        bundled for :func:`build_filter` and :func:`headstart.facets.counts`.

        A property, not a field set once and cached: the individual attributes stay
        directly settable (the UI templates read them one at a time, and the test suite
        monkeypatches them the same way), and this just re-packs whatever they currently hold
        on every access — free, since it costs attribute reads and no table I/O.
        """
        return IndexCapabilities(
            atses=self.atses,
            has_first_seen=self.has_first_seen,
            has_min_salary_annual=self.has_min_salary_annual,
            currencies=self.currencies,
            has_description=self.has_description,
            has_country=self.has_country,
            has_employment_type_flags=self.has_employment_type_flags,
            has_description_stored=self.has_description_stored,
            has_salary_known=self.has_salary_known,
            has_posted_at_comparable=self.has_posted_at_comparable,
            has_experience_filter_flags=self.has_experience_filter_flags,
        )

    def parse_filters(self, args: Mapping[str, str]) -> SearchFilters:
        """The :class:`SearchFilters` one request asks for, parsed exactly once.

        Split out of :meth:`run` so the ranked search and the facet counts (:mod:`headstart.
        facets`) compile the *same* description of the user's filters. Two call sites parsing
        the same query string independently is how a count comes to disagree with the list it
        is counting — the one defect that would make the whole facet feature worse than no
        counts at all, because a wrong number is trusted where a missing one is not.

        Returns only the user-settable vocabulary (ADR-0149) — :attr:`capabilities` carries
        this table's own runtime facts separately, so a caller (:mod:`headstart.facets`, most
        of all) can vary one without rebuilding the other.
        """

        _int = _int_arg(args)
        kw = (args.get("kw") or "").strip() or None
        kw_in = (args.get("kw_in") or "").strip().lower()
        ats = (args.get("ats") or "").strip() or None
        etype = (args.get("etype") or "").strip() or None
        # The one place a request is parsed, and so the one place a dropped filter can be
        # reported without `facets.counts` repeating it once per option — see the helper.
        _warn_unknown_filters(ats, etype, self.atses)
        return SearchFilters(
            remote=args.get("remote") == "true",
            max_years=_int("max_years"),
            ats=ats,
            etype=etype,
            india=(args.get("india") or "").strip().lower() or None,
            location=(args.get("location") or "").strip() or None,
            company=(args.get("company") or "").strip() or None,
            has_salary=args.get("has_salary") == "true",
            salary_min=_int("salary_min"),
            salary_max=_int("salary_max"),
            salary_currency=(args.get("salary_currency") or "").strip().upper() or None,
            posted_within=_int("posted_within"),
            # A sort by posting date can only place rows whose date is readable, so the
            # window it sorts is part of the filter, not of the ordering — see build_filter.
            posted_sortable=SORT_COLUMNS.get((args.get("sort") or "").strip())
            == "posted_at",
            seen_within=_int("seen_within"),
            posted_after=(args.get("posted_after") or "").strip() or None,
            posted_before=(args.get("posted_before") or "").strip() or None,
            seen_after=(args.get("seen_after") or "").strip() or None,
            seen_before=(args.get("seen_before") or "").strip() or None,
            first_seen_after=(args.get("first_seen_after") or "").strip() or None,
            kw=kw,
            # A scope is a modifier of the keyword, not a filter of its own (the salary
            # currency's rule): without a keyword it is None, so it can never be named as the
            # Blocking filter, and an unknown value falls back to the default rather than being
            # interpolated.
            kw_in=(kw_in if kw_in in KEYWORD_SCOPES else KEYWORD_DEFAULT_SCOPE)
            if kw
            else None,
        )

    def facets(
        self, args: Mapping[str, str], *, extra_where: str | None = None
    ) -> dict[str, Any]:
        """Per-option result counts for these filters — see :mod:`headstart.facets`.

        ``extra_where`` is the same Account clause :meth:`run` takes, and passing it here is not
        optional: the UI prints ``facets.total`` as "Showing 1-N of TOTAL", so counting without
        it reported the whole index beside a list of ten rows.

        Here rather than in the route so the table and the runtime schema facts stay behind
        this object; a caller reaching for ``_table`` to count would be the same class of leak
        that ``parse_filters`` exists to prevent on the filter side. Imported inside the method
        because :mod:`headstart.facets` imports back from this one — a module-level import
        here would be circular.
        """
        from headstart import facets

        filters = self.parse_filters(args)
        cache_key = (filters, extra_where)
        cached = _cache_get(
            self._facet_cache,
            self._facet_cache_lock,
            cache_key,
            FACET_CACHE_TTL_SECONDS,
        )
        if cached is not None:
            return cached
        counted = facets.counts(
            self._table,
            filters,
            self.capabilities,
            extra_where=extra_where,
        )
        _cache_put(
            self._facet_cache,
            self._facet_cache_lock,
            cache_key,
            counted,
            FACET_CACHE_SIZE,
        )
        return counted

    def _query_vector(self, query: str) -> Any:
        with self._query_vector_cache_lock:
            cached = self._query_vector_cache.pop(query, None)
            if cached is not None:
                self._query_vector_cache[query] = cached
                return cached
        vector = encode_query(self._model, query)
        with self._query_vector_cache_lock:
            self._query_vector_cache[query] = vector
            while len(self._query_vector_cache) > QUERY_VECTOR_CACHE_SIZE:
                self._query_vector_cache.popitem(last=False)
        return vector

    def run(
        self, args: Mapping[str, str], *, extra_where: str | None = None
    ) -> list[dict]:
        """One page of results. ``extra_where`` is ANDed onto the compiled filter.

        A separate parameter rather than another :class:`SearchFilters` field, because what goes
        here is **Account state** — the follow/hide lists (ADR-0171) — not a control the user set
        on this request. Keeping it out of `SearchFilters` is what stops a Saved Set freezing a
        follow list at the moment it was saved.
        """
        query = (args.get("q") or "").strip()
        _int = _int_arg(args)
        filters = self.parse_filters(args)
        where = with_extra(build_filter(filters, self.capabilities), extra_where)
        # Whitelisted to a column name, never taken from the query string — this reaches an
        # ORDER BY. An unknown value is no sort at all, which is the existing behaviour.
        sort = SORT_COLUMNS.get((args.get("sort") or "").strip())
        if sort == "first_seen" and not self.has_first_seen:
            sort = None  # same dark-until-migrated rule as the filters above
        if sort == "min_salary_annual" and not self.has_min_salary_annual:
            sort = None  # likewise: the ADR-0082 columns arrive by migration
        # Salary is stored in the employer's own currency (ADR-0082), so ordering the raw
        # column ranked ₹40,00,000 above $300,000 — the first 400 rows of a salary sort were
        # all INR (ADR-0178). The sort is stated in ONE currency, resolved and whitelisted exactly like the
        # bracket's; a table with no such currency keeps the raw ordering it always had.
        sort_currency = None
        if sort == "min_salary_annual":
            sort_currency = (
                filters.salary_currency
                if filters.salary_currency in self.currencies
                else SALARY_DEFAULT_CURRENCY
            )
            if sort_currency not in self.currencies:
                sort_currency = None
        # `is None`, not `or`: the old route's `int(raw or 20)` gave k=0 → 1 row, and an
        # `or` on the parsed int would silently turn k=0 into the default 20 instead. Same
        # reasoning for `page`, new in ADR-0074: page=1 is the default, not a falsy no-op.
        k = _int("k")
        k = max(1, min(20 if k is None else k, self.max_k))
        page = _int("page")
        page = max(1, min(1 if page is None else page, self.max_page))
        offset = (page - 1) * k
        browse_key = (filters, sort, k, page, extra_where)
        if not query:
            cached = _cache_get(
                self._browse_cache,
                self._browse_cache_lock,
                browse_key,
                BROWSE_CACHE_TTL_SECONDS,
            )
            if cached is not None:
                return cached

        if query:
            search = self._table.search(self._query_vector(query)).metric("cosine")
            if self.has_vector_index:
                search = search.nprobes(ANN_NPROBES).refine_factor(ANN_REFINE_FACTOR)
        else:
            search = (
                self._table.search()
            )  # no vector: a plain, filtered scan (ADR-0074)
        if where:
            search = search.where(where, prefilter=True)
        # Ask only for the columns the response is built from (:data:`RESULT_COLUMNS`).
        # `_distance` is named explicitly rather than left to lancedb's auto-projection, which
        # warns that it "will change in the future" and stop supplying it — and `score` is that
        # value. Nothing extra is needed on the browse side: an `order_by` over a projected scan
        # plans fine *provided the ordering column is in the projection*, which
        # `test_every_sortable_column_is_projected` pins. (Leave one out and planning fails with
        # "TakeExec requires the input plan to have a column named `_rowaddr` or `_rowid`" — the
        # error that briefly bought a `_rowid` here, on a probe whose projection was the thing
        # at fault.)
        search = search.select(
            [*self.projection, "_distance"] if query else [*self.projection]
        )
        if not query and not sort:
            # `first_seen` alone is not a stable sort key: pipeline runs stamp it once per
            # sync batch, so thousands of rows tie on the exact same timestamp, and `offset`
            # pagination over a tied sort silently repeats and drops rows across pages
            # (measured 2026-08-20 against a real table: 2 of 5 rows recurred between page 1
            # and page 2 with no tiebreaker, zero recurred with one). `id` is unique per row,
            # so it breaks every tie deterministically. Plain dicts, not `lancedb.query.
            # ColumnOrdering` instances — lancedb's pydantic layer coerces either (verified
            # 2026-08-20), and a dict keeps `search.py` importable without lancedb installed
            # (the quality job's `.[dev]` extra omits it — lancedb only ships in `.[embed]`).
            # Do NOT add this ordering to the query branch above — passing any explicit
            # `order_by` alongside a vector search was measured to override ranking by
            # similarity entirely, not merely break ties within it.
            ordering = (
                [
                    {
                        "column_name": "first_seen",
                        "ascending": False,
                        "nulls_first": False,
                    }
                ]
                if self.has_first_seen
                else []
            )
            ordering.append({"column_name": "id", "ascending": True})
            search = search.order_by(ordering)

        if sort and query:
            # Sorting a *ranked* result set, issue #275. The comment above is the constraint:
            # an `order_by` on the vector branch does not tie-break similarity, it replaces
            # it — so asking LanceDB to do this would silently discard the query. Instead take
            # the window and re-order it here.
            #
            # The window is the whole result set as far as anyone can tell: `max_k * max_page`
            # is exactly what ADR-0074's clamp lets pagination address, so a row outside it
            # was already unreachable by any request. The window is not free — measured
            # through this method on a 318,003-row unindexed table, it is 420.6 ms against
            # 105.0 ms for a single page — but `select()` above is what pays for it, taking it
            # to 256.5 ms without touching the shape. (ADR-0084 recorded "2.7 ms … 9.2 ms …
            # ~6.5 ms" here on 2026-08-25; its amendment carries why that no longer holds.)
            #
            # It is NOT a global sort, and the UI says so: a Job older than the 2,000th-best
            # match cannot appear. That is the honest shape of "newest among your best
            # matches" — the alternative, scanning by date, answers a question the user did
            # not ask by throwing their query away.
            window = search.limit(self.max_k * self.max_page).to_list()
            # `reverse=True`, so the stand-in for a missing value has to be the smallest thing
            # in its own type — `""` for the date columns, -inf for a numeric one — which puts
            # rows that have no value last either way.
            missing = float("-inf") if sort in _NUMERIC_SORTS else ""
            rates = ((fx.table() or {}).get("rates") or {}) if sort_currency else {}

            def key(r: dict) -> tuple:
                value, currency = r.get(sort), r.get("salary_currency")
                if sort_currency and value is not None and currency != sort_currency:
                    # Restated in the sort's currency with the ADR-0117 rates. A currency with
                    # no rate cannot be compared, so it joins the unpriced rows rather than
                    # being taken 1:1 — the silent wrong answer `fx` exists to refuse.
                    value = fx.convert(float(value), currency, sort_currency, rates)
                return (missing if value is None else value, r.get("id") or "")

            window.sort(key=key, reverse=True)
            rows = window[offset : offset + k]
        elif sort_currency:
            rows = self._salary_browse(where, sort_currency, k, offset)
        else:
            if sort:
                # No query, so no ranking to protect: LanceDB can order the whole table. Same
                # `id` tiebreak as above, for the same pagination reason.
                search = search.order_by(
                    [
                        {"column_name": sort, "ascending": False, "nulls_first": False},
                        {"column_name": "id", "ascending": True},
                    ]
                )
            rows = search.limit(k).offset(offset).to_list()

        result = [
            {
                "id": r.get("id"),  # the star identity — {ats}:{slug}:{native_id}
                "score": round(1 - r["_distance"], 3) if query else None,
                "title": r["title"],
                "company": r["company"],
                "location": r.get("location"),
                "remote": r["remote"],
                "employment_type": r.get("employment_type"),
                "min_years": r.get("min_years"),
                "salary": r.get("salary"),
                "min_salary_annual": r.get("min_salary_annual"),
                "max_salary_annual": r.get("max_salary_annual"),
                "salary_currency": r.get("salary_currency"),
                "salary_source": r.get("salary_source"),
                "ats": r.get("ats"),
                "posted_at": r.get("posted_at"),
                "first_seen": r.get("first_seen"),
                "url": _canonical_url(
                    r.get("ats"), r.get("url"), r.get("id")
                ),  # temporary; see _canonical_url
            }
            for r in rows
        ]
        if not query:
            _cache_put(
                self._browse_cache,
                self._browse_cache_lock,
                browse_key,
                result,
                BROWSE_CACHE_SIZE,
            )
        return result

    def _salary_browse(
        self, where: str | None, currency: str, k: int, offset: int
    ) -> list[dict]:
        """One page of a salary-sorted browse, stated in ``currency``.

        LanceDB can only ORDER BY a stored column, and salary is stored in each employer's own
        currency, so the whole table cannot be ordered across currencies. The page is cut from
        two segments instead: ``currency``'s Jobs in true salary order, then every other Job
        grouped by currency — each group in its own true order — with the unpriced last. The
        result SET is exactly the filter's, so the facet total beside it still describes it;
        scoping to ``currency`` alone emptied an India browse sorted in USD.

        ``currency`` is already whitelisted against :attr:`currencies`, like the bracket's.
        """
        own = f"salary_currency = '{currency}'"
        rest = f"(salary_currency IS NULL OR salary_currency <> '{currency}')"
        salary = {
            "column_name": "min_salary_annual",
            "ascending": False,
            "nulls_first": False,
        }
        by_id = {"column_name": "id", "ascending": True}
        n_own = self._table.count_rows(filter=with_extra(where, own))

        def segment(clause: str, ordering: list[dict], limit: int, skip: int) -> list:
            if limit <= 0:
                return []
            return (
                self._table.search()
                .where(clause, prefilter=True)
                .select([*self.projection])
                .order_by(ordering)
                .limit(limit)
                .offset(skip)
                .to_list()
            )

        rows = segment(
            with_extra(where, own), [salary, by_id], min(k, n_own - offset), offset
        )
        by_currency = {
            "column_name": "salary_currency",
            "ascending": True,
            "nulls_first": False,
        }
        rows += segment(
            with_extra(where, rest),
            [by_currency, salary, by_id],
            k - len(rows),
            max(0, offset - n_own),
        )
        return rows

    def warm(self) -> None:
        """Preload default responses plus one semantic pass every fresh process serves first."""
        self.run({})
        self.facets({})
        self.run({"q": "software engineer"})

    def indexed(self, ids: Collection[str]) -> set[str]:
        """Which of these job ids are still in the index — the Saved tab's "closed" check.

        The ids come back out of stored records the browser once sent, so they are quote-doubled
        before reaching the where-clause. Not `_like`'s escaping, and deliberately: this is an
        `id IN (…)` equality test, where `%` and `_` are ordinary characters — escaping them here
        would stop a real id containing one from ever matching itself."""
        wanted = [i for i in ids if i]
        if not wanted:
            return set()
        quoted = ", ".join("'" + i.replace("'", "''") + "'" for i in wanted)
        rows = (
            self._table.search()
            .select(["id"])
            .where(f"id IN ({quoted})")
            .limit(len(wanted))
            .to_list()
        )
        return {r["id"] for r in rows}

    def n_seen_within(self, hours: int) -> int | None:
        """How many Jobs entered the index in the last ``hours`` — ``None`` without the column.

        The door's freshness proof (ADR-0112). Exact, which is the whole reason it is there:
        `first_seen` is written by us on arrival, and a row that lacks it predates the column
        (ADR-0031) and therefore cannot be new — so unlike a coverage share this window has no
        unknown bucket to hand-wave. Compiled through :func:`build_filter` rather than a
        hand-written clause so "new" means here exactly what it means in the Search rail.

        One :meth:`count_rows` — measured at 4–6 ms in `headstart.facets`.
        """
        if not self.has_first_seen:
            return None
        return self._table.count_rows(
            filter=build_filter(SearchFilters(seen_within=hours), self.capabilities)
        )

    def coverage(self) -> dict[str, Any]:
        """What share of the served table actually carries each field (ADR-0113).

        The Data tab's numbers. Every one is counted here rather than written down, because a
        coverage figure in prose is stale the moment the next run lands — README §"The served
        table" already carries two dated to 2026-08-18 for exactly that reason. A number the
        product measures about itself gets worse on the page when the pipeline gets worse,
        which is the only incentive a limits page should have.

        Only fields a Job may legitimately be *missing* belong here. ``remote`` was removed
        after review: it is a facet, not a gap — a share here would answer "how many are
        remote", which the Search rail's own counts already answer, rather than "how often do
        we not know". Its provenance is also mixed — many scrapers read a board-supplied
        workplace-type field, others fall back to ``models.is_remote`` over the location text,
        several OR the two — so no single sentence describes the column. Successive revisions
        of this docstring asserted "the board's flag" and then "an inference" with equal
        confidence, and two attempts to count the split were both wrong; see ADR-0113.

        Costs one :meth:`count_rows` for the total plus one per field — six in all, not five.
        `headstart.facets` measured that primitive at 4–6 ms against a 316,606-row table,
        so the whole panel is cheaper than a single ranked search — and it is cached per
        process anyway: a new index arrives with a Space restart, never under a running one.

        A field whose column arrives with a migration (``first_seen``, the salary columns,
        ``description``) is reported as ``None`` on a table that predates it — never as zero,
        which would read as "measured, and none have it" (ADR-0009's unknown-is-not-zero rule).
        ``posted_at`` and ``min_years`` need no such guard: they are in the base ``_schema()``
        and every served table has carried them.
        """
        if self._coverage is None:
            fields = {
                "posted_at": "posted_at IS NOT NULL AND posted_at != ''",
                "first_seen": "first_seen IS NOT NULL AND first_seen != ''"
                if self.has_first_seen
                else None,
                "salary": (
                    "salary_known = true"
                    if self.has_salary_known
                    else "min_salary_annual IS NOT NULL"
                )
                if self.has_min_salary_annual
                else None,
                "min_years": "min_years IS NOT NULL",
                "description": (
                    "description_stored = true"
                    if self.has_description_stored
                    else "description IS NOT NULL AND description != ''"
                )
                if self.has_description
                else None,
            }
            self._coverage = {
                "total": self._table.count_rows(),
                "fields": {
                    name: (
                        None if where is None else self._table.count_rows(filter=where)
                    )
                    for name, where in fields.items()
                },
            }
        return self._coverage
