"""The one serving-path search implementation both UIs run (ADR-0042).

It compiles filters through :mod:`headstart.search_filter_compiler` (the reference Search-filter
compiler), counts Facets through :mod:`headstart.facets`, and shares the embedding conventions —
model id, task prefixes, table name, encoder — with the pipeline through
:mod:`headstart.embedding_conventions` (ADR-0194).

:class:`JobSearch` is the serving path behind one method: built once with the loaded
encoder and the open ``jobs`` table, ``run(args)`` takes a request's query-string mapping
and returns projected result rows. Both the HF Space app and the local dev server are thin
adapters over it — the Space image installs ``headstart`` as a real package (ADR-0153), so
this module imports ``fx`` and the Search-filter modules the same way everywhere.
"""

from __future__ import annotations

import time
from bisect import bisect_left
from collections import OrderedDict
from collections.abc import Callable, Collection, Mapping, Sequence
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import urlsplit

from headstart import (
    employment_type_filter,
    experience_filter,
    facets,
    fx,
    india_filter,
    log,
    posted_date_guard,
    salary_known_filter,
)
from headstart.embedding_conventions import encode_query
from headstart.search_filter_compiler import (
    KEYWORD_DEFAULT_SCOPE,
    KEYWORD_SCOPES,
    SALARY_DEFAULT_CURRENCY,
    IndexCapabilities,
    SearchFilters,
    account_clause,
    board_clause,
    build_filter,
    with_extra,
)

# In the Space nothing calls `setup()` (ADR-0153's app.py boots straight into serving), which
# is why the one boot line below is a WARNING — `logging.lastResort` carries WARNING and above
# to stderr with no handler configured, and a served table quietly ignoring whole filters is an
# anomaly by ADR-0039's own definition.
_log = log.get(__name__)

# ---- the product search path (ADR-0042) ----
# Everything below moved from the Space app, which had become the de-facto reference while
# this module lagged behind; the Space and the local dev server now both consume this.

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


def request_account_clause(
    args: Mapping[str, str], followed: Collection[str], hidden: Collection[str]
) -> str | None:
    """:func:`~headstart.search_filter_compiler.account_clause` for one request, ``mine`` read
    off its query string (ADR-0171).

    Both apps call this with their own Account's lists — the Space from the signed-in Account's
    stored record, the local renderer from its one in-memory record — so the query-string rule
    for ``mine`` is written once, beside the clause it switches, rather than in each app.
    """
    return account_clause(followed, hidden, mine=args.get("mine") in ("1", "true"))


#: The most Boards one ``board=`` hand-off may name. A company's Boards are its whole scope and
#: the largest measured is Hyatt's 83 (2026-09-24); the bound keeps a query string from growing
#: the where-clause without limit.
MAX_SCOPED_BOARDS = 200


def scoped_boards_clause(args) -> str | None:
    """The Boards a request names with ``board=`` (repeatable), or None (ADR-0185).

    How a company's trend hands over to its jobs: by the directory's Board keys rather than a
    company-name substring, which misses aliased names ("RTX" from ``globalhr`` rows) and merges
    same-named employers. Kept out of :class:`SearchFilters` for the reason
    :func:`~headstart.search_filter_compiler.board_clause` gives: a hand-off, not a control a
    Saved Set should freeze. Too many keys is a :class:`ValueError`, which both routes answer as
    an invalid filter.
    """
    boards = [board for board in args.getlist("board") if board.strip()]
    if len(boards) > MAX_SCOPED_BOARDS:
        raise ValueError(f"at most {MAX_SCOPED_BOARDS} boards")
    return board_clause(boards, exclude=False)


def load_family_ids(path: Path) -> dict[str, list[str]] | None:
    """``family -> served ids``, each list sorted case-folded, from the role-assignment snapshot
    (ADR-0057), or None without a readable one — which turns the category hand-off off (the
    page is told through its config) rather than failing boot.

    Sorted so a hand-off finds a Board's ids by bisection: scanning software-engineering's
    ~90,000 ids per request, lower-casing each, was the cost of a flat list."""
    if not Path(path).exists():
        return None
    try:
        import pyarrow.parquet as pq

        table = pq.read_table(path, columns=["id", "family"]).to_pydict()
    except (OSError, ValueError, KeyError) as exc:
        _log.warning(f"role assignments unreadable ({exc}); category hand-off off")
        return None
    out: dict[str, list[str]] = {}
    for job_id, family in zip(table["id"], table["family"], strict=True):
        out.setdefault(family, []).append(job_id)
    for ids in out.values():
        ids.sort(key=str.lower)
    return out


#: The most Jobs a ``family=`` hand-off names by id. Amazon's largest category measured 1,018
#: (2026-09-25); the page hands a category over only under this bound (``CFG.max_family_ids``)
#: and ranks by its name past it, so the clause never silently widens to every job.
MAX_FAMILY_IDS = 5000


def scoped_family_clause(
    args, family_ids: Mapping[str, Sequence[str]] | None
) -> str | None:
    """The Jobs of the handed-over Boards in one role family (``family=``), or None.

    Search has no family column; the family of each served Job is the pipeline's own
    ``role_assignments`` snapshot (ADR-0057), the same assignment the Trends counts are made
    of. So a trend's category hands over as exact ids — "243 AI roles at Google" in Trends
    opens as Google's AI roles in Search, where a semantic query alone ranked all 1,856 Google
    jobs. Only with ``board=``: a family across the whole index is a Trends view, not a search.
    Past :data:`MAX_FAMILY_IDS` it is refused as an invalid filter rather than widened.
    """
    family = (args.get("family") or "").strip()
    boards = sorted({b.lower() + ":" for b in args.getlist("board") if b.strip()})
    if not boards or family_ids is None:
        return None
    if family:
        ids = _ids_on_boards(family_ids.get(family, ()), boards)
        if len(ids) > MAX_FAMILY_IDS:
            raise ValueError(f"at most {MAX_FAMILY_IDS} jobs in one category hand-off")
        return _id_list(ids) if ids else "id IN ('')"
    # `tech=1`: the company's tech roles, as its trend counts them — every served Job but those
    # the assignment puts in the reserved non-tech family. "See its open roles" listed 1,854
    # under a Google trend of 1,800. Past the bound the few non-tech rows simply stay.
    if args.get("tech") in ("1", "true"):
        ids = _ids_on_boards(family_ids.get(_NON_TECH_FAMILY, ()), boards)
        if ids and len(ids) <= MAX_FAMILY_IDS:
            return f"NOT ({_id_list(ids)})"
    return None


# role_trends' reserved family for what the tech filter kept but the taxonomy calls non-tech;
# mirrors `headstart.roles.NON_TECH` without importing the role model into search.
_NON_TECH_FAMILY = "non-tech"


def _ids_on_boards(pool: Sequence[str], prefixes: list[str]) -> list[str]:
    """The ids in ``pool`` (sorted case-folded) that fall on one of the Board ``prefixes``."""
    ids: list[str] = []
    for prefix in prefixes:
        at = bisect_left(pool, prefix, key=str.lower)
        while at < len(pool) and pool[at].lower().startswith(prefix):
            ids.append(pool[at])
            at += 1
    return ids


def _id_list(ids: list[str]) -> str:
    return "id IN (" + ", ".join("'" + i.replace("'", "''") + "'" for i in ids) + ")"


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
    if etype and etype not in employment_type_filter.RULES:
        _log.warning(
            "filter dropped: employment_type %.40r is not a known value", etype
        )


def _result_row(row: Mapping[str, Any], query: str) -> dict[str, Any]:
    """One served result: every :data:`RESULT_COLUMNS` value, plus ``score`` after the id.

    Built from that one tuple rather than a hand-written dict beside it (ADR-0194), so the
    projection the query asks for and the fields the response carries cannot drift apart. A
    column the table lacks comes back None. ``id`` is the star identity —
    ``{ats}:{slug}:{native_id}``. ``url`` is rewritten at serve time (temporary; see
    :func:`_canonical_url`).
    """
    result: dict[str, Any] = {
        "id": row.get("id"),
        "score": round(1 - row["_distance"], 3) if query else None,
    }
    result.update(
        {column: row.get(column) for column in RESULT_COLUMNS if column != "id"}
    )
    result["url"] = _canonical_url(row.get("ats"), row.get("url"), row.get("id"))
    return result


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

    The table's runtime facts live in one place, :attr:`capabilities` — an
    :class:`IndexCapabilities` (ADR-0149) learned once here, handed as-is to :func:`build_filter`
    and :func:`headstart.facets.counts`, and read field by field by the UI adapters for the
    Board dropdown, the "first seen" control and the rest (ADR-0194). A test that needs a
    different table swaps the whole object with :func:`dataclasses.replace`.
    """

    def __init__(self, model: Any, table: Any, *, max_k: int = 100, max_page: int = 20):
        self._model = model
        self._table = table
        self.max_k = max_k
        self.max_page = max_page
        names = table.schema.names
        # `first_seen` only appears on the first pipeline run after ADR-0031; filtering on
        # a column the table lacks errors every query, so the feature stays dark until then.
        has_first_seen = "first_seen" in names
        # Same reasoning for the ADR-0082 salary columns, added by the same class of
        # idempotent migration (`index.py`'s `_salary_fields`) — a table that hasn't synced
        # since would error on `has_salary=true` rather than just not supporting it yet.
        has_min_salary_annual = "min_salary_annual" in names
        # The Keyword filter's description scope (ADR-0104), same dark-until-migrated rule: the
        # column arrives with the first `index sync` after that ADR, and the UI disables the
        # scope until it does rather than 500ing on it.
        has_description = "description" in names
        # The materialized India-filter column (ADR-0138), same rule again: until a table has
        # synced since, `build_filter` falls back to `geo.where("india")`'s slower-but-correct
        # regex alternation rather than erroring on a column that isn't there yet.
        has_country = india_filter.has_column(names)
        self.capabilities = IndexCapabilities(
            # the ATSes actually present in the index — feeds the dropdown and the whitelist
            atses=sorted(
                {
                    r["ats"]
                    for r in table.search().select(["ats"]).limit(1_000_000).to_list()
                }
            ),
            has_first_seen=has_first_seen,
            has_min_salary_annual=has_min_salary_annual,
            # The currency whitelist for the ADR-0082 salary bracket, learned the same way and
            # for the same reason as `atses`: it lands in a where-clause, so it is matched against
            # what the table holds rather than interpolated from the query string.
            currencies=(
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
                if has_min_salary_annual
                else []
            ),
            has_description=has_description,
            has_country=has_country,
            # The materialized verdicts (ADR-0173) are an optional acceleration layer: a
            # pre-migration table keeps each filter's raw clause, so none can disable a filter.
            has_employment_type_flags=employment_type_filter.has_flags(names),
            has_description_stored="description_stored" in names,
            has_salary_known=salary_known_filter.has_flags(names),
            has_posted_at_comparable=posted_date_guard.has_flags(names),
            has_experience_filter_flags=experience_filter.has_flags(names),
        )
        list_indices = getattr(table, "list_indices", None)
        self.has_vector_index = bool(
            list_indices and any("vector" in index.columns for index in list_indices())
        )
        #: :data:`RESULT_COLUMNS` narrowed to what this table actually has — see that constant
        #: for why the intersection is mandatory rather than defensive.
        self.projection = tuple(c for c in RESULT_COLUMNS if c in names)
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
                ("first_seen", has_first_seen),
                ("min_salary_annual", has_min_salary_annual),
                ("description", has_description),
                (india_filter.COLUMN, has_country),
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
    def salary_bracket_converts(self) -> bool:
        """Whether a salary bracket can actually cross a currency boundary on this table.

        Two served currencies must both carry a rate; with fewer, `build_filter` compiles the
        single-currency clause and any copy promising conversion would be describing nothing.
        Read on every access, like the rate table it consults (ADR-0117).
        """
        rates = (fx.table() or {}).get("rates") or {}
        return len([c for c in self.capabilities.currencies if c in rates]) > 1

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
        _warn_unknown_filters(ats, etype, self.capabilities.atses)
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
        that ``parse_filters`` exists to prevent on the filter side.
        """
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
        if sort == "first_seen" and not self.capabilities.has_first_seen:
            sort = None  # same dark-until-migrated rule as the filters above
        if sort == "min_salary_annual" and not self.capabilities.has_min_salary_annual:
            sort = None  # likewise: the ADR-0082 columns arrive by migration
        # Salary is stored in the employer's own currency (ADR-0082), so ordering the raw
        # column ranked ₹40,00,000 above $300,000 — the first 400 rows of a salary sort were
        # all INR (ADR-0178). The sort is stated in ONE currency, resolved and whitelisted exactly like the
        # bracket's; a table with no such currency keeps the raw ordering it always had.
        sort_currency = None
        if sort == "min_salary_annual":
            sort_currency = (
                filters.salary_currency
                if filters.salary_currency in self.capabilities.currencies
                else SALARY_DEFAULT_CURRENCY
            )
            if sort_currency not in self.capabilities.currencies:
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
                if self.capabilities.has_first_seen
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

        result = [_result_row(r, query) for r in rows]
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
        if not self.capabilities.has_first_seen:
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
                if self.capabilities.has_first_seen
                else None,
                "salary": salary_known_filter.clause(self.capabilities.has_salary_known)
                if self.capabilities.has_min_salary_annual
                else None,
                "min_years": "min_years IS NOT NULL",
                "description": (
                    "description_stored = true"
                    if self.capabilities.has_description_stored
                    else "description IS NOT NULL AND description != ''"
                )
                if self.capabilities.has_description
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
