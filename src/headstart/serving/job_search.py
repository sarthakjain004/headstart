"""The one serving-path search implementation both UIs run (ADR-0042).

It compiles filters through :mod:`headstart.search_filters.compiler` (the reference Search-filter
compiler), counts Facets through :mod:`headstart.serving.facets`, and shares the embedding conventions —
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
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import urlsplit

from headstart import log
from headstart.embedding_conventions import encode_query
from headstart.search_filters import (
    country_filter,
    employment_type_filter,
    experience_filter,
    fx,
    india_filter,
    india_gazetteer,
    posted_date_guard,
    salary_known_filter,
)
from headstart.search_filters.compiler import (
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
from headstart.serving import (
    facets,
    job_absence,
    level_counts,
    location_counts,
    requirement_counts,
    tech_skills,
)
from headstart.serving.description_matches import (
    DescriptionMatches,
    reads_descriptions,
)

# In the Space nothing calls `setup()` (ADR-0153's app.py boots straight into serving), which
# is why the one boot line below is a WARNING — `logging.lastResort` carries WARNING and above
# to stderr with no handler configured, and a served table quietly ignoring whole filters is an
# anomaly by ADR-0039's own definition.
_log = log.get(__name__)

# ---- the product search path (ADR-0042) ----
# Everything below moved from the Space app, which had become the de-facto reference while
# this module lagged behind; the Space and the local dev server now both consume this.

#: How many rows the boot scans that learn the ATS and currency whitelists read. A table past it
#: gets whitelists that miss whatever the unread rows alone carry, so boot says so.
WHITELIST_SCAN_ROWS = 1_000_000

#: A request slower than this (uncached path) is named in the log — shapes only, never the query.
SLOW_SEARCH_MS = 2000

# The retained production-table operating point (ADR-0173): IVF-SQ at 80 probes with a 2x
# exact-vector refinement reproduced every top-20 result across 16 real queries and four filter
# selectivities. Lower settings lost results; IVF/HNSW Flat cost over 1.5 GB of extra storage.
ANN_NPROBES = 80
ANN_REFINE_FACTOR = 2
FACET_CACHE_SIZE = 128
#: What `/facets` may be asked to count (``counts=``, ADR-0274): every option's count, the default
#: the page reads, or only the total, for an agent that prints nothing else.
FACET_COUNTS = ("all", "total")
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

#: What a Job read by id (`/job`, ADR-0277) carries beyond :data:`RESULT_COLUMNS`: the stored
#: description and the raw fields a search row leaves out. Intersected with the live schema like
#: the search projection, since `description` and `description_stored` arrive by migration.
JOB_DETAIL_COLUMNS = (
    "department",
    "experience",
    "max_years",
    "description_stored",
    "description",
)

#: The most Jobs one read by id may name (ADR-0277).
MAX_JOB_IDS = 5

#: The longest a Job id may be in a read by id or in ``like=``. The longest served id was 180
#: characters on 2026-09-29 (a Workday slug); the bound keeps a crafted id out of a where-clause.
JOB_ID_MAX_CHARS = 300

#: The most of one description a read by id serves (ADR-0277). 11,860 characters was the 99th
#: percentile of 2,991 stored descriptions on 2026-09-29 (median 5,229, longest 22,806), so the
#: cut reaches about one description in a hundred, and a 30,000-character MCP answer still fits
#: one whole.
JOB_DESCRIPTION_LIMIT = 12_000


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


class ScopeUnavailable(LookupError):
    """What a ``strict=1`` request asked for cannot be applied on this deployment yet: no role
    assignments, watchlist or family taxonomy loaded, or a column the served table has not
    migrated onto. A state of the deployment, not the caller's error, so :func:`refusal`
    answers it 503 (ADR-0253)."""


def refusal(exc: ValueError | ScopeUnavailable) -> tuple[dict[str, str], int]:
    """The body and status ``/search`` and ``/facets`` answer a refused request with, in both
    apps: an invalid filter (the caller's error, named in ``detail``) is a 400, and a scope this
    deployment cannot apply (:class:`ScopeUnavailable`) a 503."""
    if isinstance(exc, ScopeUnavailable):
        return {"error": str(exc)}, 503
    return {"error": "invalid filter", "detail": str(exc)}, 400


def _is_strict(args: Mapping[str, str]) -> bool:
    """Whether the request asked for ``strict=1``: every value this module would otherwise drop,
    re-scope or widen with only a log line is refused instead (ADR-0253). An agent sends it; the
    browser never does, so a stale bookmark still never errors."""
    return args.get("strict") == "1"


def _listed(values: Collection[str]) -> str:
    """``values`` for a refusal's sentence: what the request could have said instead."""
    return ", ".join(values) if values else "none"


def _unmigrated(asked: str, column: str) -> ScopeUnavailable:
    """The refusal of a ``strict=1`` filter or sort keyed on a column this table lacks, which
    without ``strict`` goes dark rather than failing (ADR-0031)."""
    return ScopeUnavailable(
        f"{asked} needs the served table's {column} column, which it does not have yet"
    )


def request_account_clause(
    args: Mapping[str, str], followed: Collection[str], hidden: Collection[str]
) -> str | None:
    """:func:`~headstart.search_filters.compiler.account_clause` for one request, ``mine`` read
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

#: How many locations :meth:`JobSearch.locations` lists by default, and at most (ADR-0275).
LOCATIONS_SHOWN = 10
MAX_LOCATIONS = 50

#: How many Jobs :meth:`JobSearch.requirements` reads for its sample, fixed (ADR-0331). At 300, a share
#: near 50% is known to about 6 points either way (95%), which a "most asked for" list needs.
REQUIREMENTS_SAMPLE = 300
#: How many of a query's closest Jobs a requirements view reads to find its sample within one
#: category: the window a sorted search re-orders (`max_k * max_page`).
REQUIREMENTS_CATEGORY_WINDOW = 2_000
#: How many requirements answers one boot keeps; the table does not change until the next boot.
REQUIREMENTS_CACHE_SIZE = 64


def scoped_boards_clause(args) -> str | None:
    """The Boards a request names with ``board=`` (repeatable), or None (ADR-0185).

    How a company's trend hands over to its jobs: by the directory's Board keys rather than a
    company-name substring, which misses aliased names ("RTX" from ``globalhr`` rows) and merges
    same-named employers. Kept out of :class:`SearchFilters` for the reason
    :func:`~headstart.search_filters.compiler.board_clause` gives: a hand-off, not a control a
    Saved Set should freeze. Too many keys is a :class:`ValueError`, which both routes answer as
    an invalid filter.
    """
    boards = [board for board in args.getlist("board") if board.strip()]
    if len(boards) > MAX_SCOPED_BOARDS:
        raise ValueError(f"at most {MAX_SCOPED_BOARDS} boards")
    return board_clause(boards, exclude=False)


def _named_boards_clause(args) -> str:
    """:func:`scoped_boards_clause` for a route that reads a company's own rows: naming no Board
    is a :class:`ValueError`, since the scan would then read every row."""
    where = scoped_boards_clause(args)
    if where is None:
        raise ValueError("name at least one Board with board=")
    return where


def load_family_ids(path: Path) -> dict[str, list[str]] | None:
    """``family -> served ids``, each list sorted case-folded, from the role-assignment snapshot
    (ADR-0057), or None without a readable one — which turns the category hand-off off (the
    page is told through its config) rather than failing boot.

    Sorted so a hand-off finds a Board's ids by bisection: scanning software-engineering's
    ~90,000 ids per request, lower-casing each, was the cost of a flat list."""
    if not Path(path).exists():
        # The Space downloads this file by name, so its absence there is a snapshot that did
        # not carry it; the local renderer without a pull says so once, too.
        _log.warning(f"role assignments not found at {path}; category hand-off off")
        return None
    try:
        import pyarrow.parquet as pq

        table = pq.read_table(path, columns=["id", "family"]).to_pydict()
    except (OSError, ValueError, KeyError) as exc:
        _log.warning(
            f"role assignments at {path} unreadable ({exc}); category hand-off off"
        )
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


def scoped_jobs_clause(
    args,
    family_ids: Mapping[str, Sequence[str]] | None,
    watch_patterns: Mapping[str, Sequence[str]] | None = None,
    *,
    known_families: Collection[str] = (),
) -> str | None:
    """The Jobs a Trends hand-off names beside ``board=``: one role family's (``family=``), one
    tracked role's (``role=``), or None.

    Search has no family column; the family of each served Job is the pipeline's own
    ``role_assignments`` snapshot (ADR-0057), the same assignment the Trends counts are made
    of. So a trend's category hands over as exact ids — "243 AI roles at Google" in Trends
    opens as Google's AI roles in Search, where a semantic query alone ranked all 1,856 Google
    jobs. This clause is only the one beside ``board=``: ``family=`` without it is a category
    across the whole index, which :meth:`JobSearch.run` and :meth:`JobSearch.facets` read from
    :class:`FamilyTables` (ADR-0322), so this returns None for it. A category past
    :data:`MAX_FAMILY_IDS` on its Boards is refused as an invalid filter rather than widened.

    Under ``strict=1`` (ADR-0253) a hand-off this would ignore or widen is refused instead. The
    caller's errors are a :class:`ValueError`: ``role=`` without ``board=``, both
    at once, a role with no watch pattern, or a family ``known_families`` does not configure.
    A deployment that cannot apply one is a :class:`ScopeUnavailable`: no watchlist, no family
    taxonomy, or no role assignments, loaded. ``known_families`` are the families the taxonomy
    configures (``trend_history.family_labels``), so a configured family with no Jobs assigned
    yet still answers zero rows, as it does without ``strict``.
    """
    family = (args.get("family") or "").strip()
    boards = sorted({b.lower() + ":" for b in args.getlist("board") if b.strip()})
    # `role=`: a tracked role's jobs, by the same title patterns role_trends counts it by
    # (ADR-0051). Trends showed "LLM / GenAI 84" at Google with no way to open those 84.
    role = (args.get("role") or "").strip()
    patterns = (
        (watch_patterns or {}).get(
            role if role.startswith("watch:") else "watch:" + role
        )
        if role
        else None
    )
    if _is_strict(args):
        if role and not boards:
            raise ValueError(
                "role= needs board=: a watched role is searched within one company's Boards"
            )
        if family and role:
            raise ValueError("family= and role= name one scope each; send one of them")
        if role and not watch_patterns:
            raise ScopeUnavailable(
                "role= needs the role watchlist, which this deployment has not loaded"
            )
        if role and not patterns:
            watched = sorted(name.removeprefix("watch:") for name in watch_patterns)
            raise ValueError(
                f"role {role!r} has no watch pattern; watched roles: {_listed(watched)}"
            )
        if family and not known_families:
            raise ScopeUnavailable(
                "family= needs the family taxonomy, which this deployment has not loaded"
            )
        if family and family not in known_families:
            raise ValueError(
                f"family {family!r} is not a configured family; configured: "
                f"{_listed(sorted(known_families))}"
            )
        if family and family_ids is None:
            raise ScopeUnavailable(
                "family= needs the role assignments, which this deployment has not loaded"
            )
    if role and boards:
        if patterns:
            joined = "|".join(f"(?:{p})" for p in patterns).replace("'", "''")
            return f"regexp_like(title, '(?i){joined}')"
        if not family:
            # Clipped `%r` for the reason `_warn_unknown_filters` gives: query-string input.
            # No watchlist at all is a failed deploy of the config, not an unknown role.
            _log.warning(
                "scope widened: role %.40r asked with no watchlist loaded; whole Board served"
                if not watch_patterns
                else "scope widened: role %.40r has no watch pattern; whole Board served",
                role,
            )
    if not boards:
        if role:
            _log.warning("scope widened: role= given without board=; ignored")
        return None
    if family_ids is None:
        if family:
            _log.warning(
                "scope widened: family %.40r asked with no role assignments loaded; "
                "whole Board served",
                family,
            )
        return None
    if family:
        if family not in family_ids:
            _log.warning("family %.40r is not a known family; zero results", family)
        ids = _ids_on_boards(family_ids.get(family, ()), boards)
        if len(ids) > MAX_FAMILY_IDS:
            # The page hands over only under its own cap, so this is the page and the server
            # disagreeing, not a user typo — and the route answers a bare 400 that says neither.
            _log.warning(
                "category hand-off refused: %d ids > %d", len(ids), MAX_FAMILY_IDS
            )
            raise ValueError(f"at most {MAX_FAMILY_IDS} jobs in one category hand-off")
        return _ids_in_clause(ids) if ids else "id IN ('')"
    return None


@dataclass(frozen=True)
class RoleAssignments:
    """The role assignments a boot loaded (ADR-0057): each family's served ids, sorted case-folded
    as :func:`load_family_ids` sorts them (a family also holding its predecessors', ADR-0220), and
    the families the taxonomy lists now, retired ones left out."""

    ids: Mapping[str, Sequence[str]]
    current: frozenset[str]

    def family_of(self, job_id: str) -> str | None:
        """The current family holding ``job_id``, found by bisecting each family's ids."""
        folded = job_id.lower()
        for name in self.current:
            pool = self.ids.get(name, ())
            at = bisect_left(pool, folded, key=str.lower)
            while at < len(pool) and pool[at].lower() == folded:
                if pool[at] == job_id:
                    return name
                at += 1
        return None


def _ids_on_boards(pool: Sequence[str], prefixes: list[str]) -> list[str]:
    """The ids in ``pool`` (sorted case-folded) that fall on one of the Board ``prefixes``."""
    ids: list[str] = []
    for prefix in prefixes:
        at = bisect_left(pool, prefix, key=str.lower)
        while at < len(pool) and pool[at].lower().startswith(prefix):
            ids.append(pool[at])
            at += 1
    return ids


def _ids_in_clause(ids: list[str]) -> str:
    """``id IN (…)`` over ``ids``, each quote doubled."""
    return "id IN (" + ", ".join("'" + i.replace("'", "''") + "'" for i in ids) + ")"


#: What a family table leaves out of the served table's columns (ADR-0322): the vector, 768
#: floats a row, because a family table ranks nothing, and the description, because a
#: description keyword is matched on the served table and handed to the family table as ids.
_LEFT_OUT_OF_FAMILY_TABLES = ("vector", "description")

#: Rows a family table's build reads a batch. Building software-engineering's took 2,176 ms in the
#: default batches, 693 at 8,192 and 529 at 65,536 (lancedb 0.36, 2026-09-29).
FAMILY_SCAN_BATCH_ROWS = 65_536


class FamilyTables:
    """Each role family's served Jobs as an in-memory table, a *family table*: what a category
    across the whole index reads (``family=`` without ``board=``, ADR-0322).

    The served table has no family column, so a family is a set of ids (:func:`load_family_ids`),
    and naming software-engineering's 69,456 in a where-clause cost 1.8 s a statement and 24 s for
    the facet strip (measured 2026-09-29 on the 498,539-row table). A family table holds the
    family's rows with every column but :data:`_LEFT_OUT_OF_FAMILY_TABLES`, so any filter and any
    browse order reads it as it reads the served table. It is built on the family's first
    request, from one scan, and kept: the served table never changes under a process (the Space
    restarts on a new one). Software-engineering's is 25 MB; every family's together about 140 MB.
    """

    def __init__(self, table: Any, family_ids: Mapping[str, Sequence[str]]):
        self._table = table
        self._family_ids = family_ids
        self._columns = [
            c for c in table.schema.names if c not in _LEFT_OUT_OF_FAMILY_TABLES
        ]
        self._tables: dict[str, Any] = {}
        # Held only to build: a table is built once, and two first requests never scan the
        # served table twice. A build takes about a second, once a family a process; a family
        # already built is read without waiting on another's build.
        self._build_lock = Lock()

    def table(self, family: str) -> Any:
        """``family``'s table; empty for a family no Job is assigned to."""
        if (built := self._tables.get(family)) is not None:
            return built
        with self._build_lock:
            if family not in self._tables:
                self._tables[family] = self._build(self._family_ids.get(family, ()))
            return self._tables[family]

    def _build(self, ids: Sequence[str]) -> Any:
        import pyarrow as pa
        import pyarrow.compute as pc

        wanted = pa.array(list(ids), pa.string())
        # Streamed in batches of FAMILY_SCAN_BATCH_ROWS, so the scan never holds the whole
        # table's 185 MB of these columns at once.
        reader = (
            self._table.search()
            .select(self._columns)
            .to_batches(batch_size=FAMILY_SCAN_BATCH_ROWS)
        )
        rows = pa.Table.from_batches(
            [batch.filter(pc.is_in(batch["id"], value_set=wanted)) for batch in reader],
            schema=reader.schema,
        )
        return _in_memory_table(rows)


@dataclass(frozen=True)
class _FamilyScope:
    """A category across the whole index as one request reads it (ADR-0322)."""

    family: str
    #: The family's whole table.
    table: Any
    #: What the request reads: the whole table, or its rows a description keyword matched.
    rows: Any
    #: The request's filters, as applied to ``rows``: without a keyword matched beforehand.
    filters: SearchFilters

    @property
    def keyword_matched_first(self) -> bool:
        return self.rows is not self.table


def _family_asked(args: Mapping[str, str]) -> str | None:
    """The family a request names across the whole index (``family=`` without ``board=``), or
    None; ``family=`` beside ``board=`` is :func:`scoped_jobs_clause`'s. A plain mapping has no
    ``board=`` list to read."""
    family = (args.get("family") or "").strip()
    if not family:
        return None
    boards = args.getlist("board") if hasattr(args, "getlist") else ()
    return None if any(board.strip() for board in boards) else family


def _in_memory_table(rows: Any) -> Any:
    """``rows`` (an Arrow table) as a LanceDB table of its own. Each call connects its own
    in-memory database, so concurrent requests never share one."""
    import lancedb

    return lancedb.connect("memory://").create_table("rows", data=rows)


def _matching_ids(table: Any, where: str | None) -> Any:
    """The ids of ``table``'s rows ``where`` matches, as an Arrow array."""
    search = table.search()
    if where:
        search = search.where(where)
    return search.select(["id"]).to_arrow()["id"]


def _rows_with_ids(table: Any, ids: Any) -> Any:
    """``table``'s rows whose id is one of ``ids``, as an in-memory table."""
    import pyarrow.compute as pc

    rows = table.to_arrow()
    return _in_memory_table(rows.filter(pc.is_in(rows["id"], value_set=ids)))


def _checked_job_id(job_id: str, name: str) -> str:
    if len(job_id) > JOB_ID_MAX_CHARS:
        raise ValueError(
            f"{name} must be a job id of at most {JOB_ID_MAX_CHARS} characters"
        )
    return job_id


def _like_id(args: Mapping[str, str]) -> str | None:
    """The Job ``like=`` ranks by (ADR-0277), or None. Refused beside ``q``: one ranking replaces
    the other, and neither narrows what matches, so honouring both is not possible."""
    like = (args.get("like") or "").strip()
    if not like:
        return None
    if (args.get("q") or "").strip():
        raise ValueError(
            "like ranks by one job and q by a query; send one of them, not both"
        )
    return _checked_job_id(like, "like")


def _other_than(job_id: str) -> str:
    """Every Job but ``job_id``: a ``like=`` search never lists or counts its own Job."""
    return "id <> '" + job_id.replace("'", "''") + "'"


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
    ``canonical_url`` hook on ``DarwinboxScraper``/``RecruiteeScraper`` (ADR-0153): the HF Space
    installs the whole package but not ``curl_cffi`` (``deploy/hf-space/requirements.txt``), which
    ``headstart.scrapers`` imports through ``headstart.network.http`` along with the network-fetch
    stack the served app has no use for — importing the scraper registry here would break the deployed app's import graph for a repair this narrow.
    What ADR-0153 *does* close: this function's two rewrites are pinned to
    ``DarwinboxScraper.url_shape``/``RecruiteeScraper.url_shape`` by
    ``tests/test_serving_job_search.py::test_canonical_url_rewrites_match_the_scrapers_own_url_shape`` — a
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


#: Every value the India place filter knows, in `india_gazetteer.where`'s own lookup order: the
#: whole country, a region, else a city.
_INDIA_PLACES = (
    india_filter.WHOLE_COUNTRY,
    *india_gazetteer.REGIONS,
    *india_gazetteer.CITIES,
)


def _warn_unknown_filters(
    filters: SearchFilters, kw_in: str, sort: str, capabilities: IndexCapabilities
) -> None:
    """Say, once per request, that a query-string value missed its whitelist.

    A value that misses drops its filter entirely and the search runs unfiltered — the widest
    possible answer to a request that asked to be narrowed — so it is worth a line. It
    cannot raise instead, because the whitelist is whatever the served table happens to hold
    and a stale bookmark must not 500.

    It lives here rather than beside the drop in :func:`build_filter` because that compiler is
    re-entered once per facet option: :func:`headstart.serving.facets.counts` recompiles one request's
    kwargs 32 times, so a line on the drop itself came out **58 times** for a single
    ``?ats=bogus&etype=bogus`` — unauthenticated, user-controlled amplification, ~29 lines per
    bad parameter from any crawler with a stale link. :meth:`JobSearch.parse_filters` parses a
    request exactly once, so this is said exactly once.

    At most one line per parameter it checks — seven a request (ats, employment_type, india,
    country, salary_currency, kw_in, sort).

    Rendered through ``%r`` and clipped: the value comes from the query string, so it is never
    the format string itself and cannot open a second line in the log.
    """
    ats, etype, india = filters.ats, filters.etype, filters.india
    if ats and ats not in capabilities.atses:
        _log.warning("filter dropped: ats %.40r is not in this table", ats)
    if etype and etype not in employment_type_filter.RULES:
        _log.warning(
            "filter dropped: employment_type %.40r is not a known value", etype
        )
    if india and india not in _INDIA_PLACES:
        _log.warning("filter dropped: india %.40r is not a known place", india)
    if filters.country and filters.country not in country_filter.CODES:
        _log.warning(
            "filter dropped: country %.40r is not a known code", filters.country
        )
    # `build_filter`'s bracket fallback: an unserved currency is re-scoped to the default, and
    # with the default unserved too the bracket compiles to nothing. Only once a bound is set —
    # the currency alone is a modifier, not a filter.
    currencies = capabilities.currencies
    bracket = filters.salary_min is not None or filters.salary_max is not None
    if (
        capabilities.has_min_salary_annual
        and bracket
        and filters.salary_currency not in currencies
    ):
        _log.warning(
            "filter re-scoped: salary_currency %.40r not served; "
            + (
                f"bracket uses {SALARY_DEFAULT_CURRENCY}"
                if SALARY_DEFAULT_CURRENCY in currencies
                else f"{SALARY_DEFAULT_CURRENCY} not served either, bracket dropped"
            ),
            filters.salary_currency,
        )
    # `parse_filters` falls an unknown scope back to the default; `run` sorts by nothing for
    # an unknown sort. Both answer something other than what was asked.
    if filters.kw and kw_in and kw_in not in KEYWORD_SCOPES:
        _log.warning(
            f"filter re-scoped: kw_in %.40r is not a known scope; {KEYWORD_DEFAULT_SCOPE} used",
            kw_in,
        )
    if sort and sort not in SORT_COLUMNS:
        _log.warning("sort dropped: %.40r is not a known sort; default order", sort)


def _refuse_what_strict_forbids(
    filters: SearchFilters, kw_in: str, sort: str, capabilities: IndexCapabilities
) -> None:
    """Under ``strict=1`` (ADR-0253), raise where :func:`_warn_unknown_filters` would only warn,
    and where :func:`build_filter` would compile a filter to nothing on a column this table has
    not migrated onto.

    A value outside its whitelist is the caller's :class:`ValueError`, naming the value and the
    ones accepted. A filter on an unmigrated column is :class:`ScopeUnavailable`. The salary
    currency is checked last, because a table without the salary columns serves no currency.
    """
    ats, etype, india = filters.ats, filters.etype, filters.india
    if ats and ats not in capabilities.atses:
        raise ValueError(
            f"ats {ats!r} is not in this index; it serves: {_listed(capabilities.atses)}"
        )
    if etype and etype not in employment_type_filter.RULES:
        raise ValueError(
            f"etype {etype!r} is not a known employment type; known: "
            f"{_listed(employment_type_filter.RULES)}"
        )
    if india and india not in _INDIA_PLACES:
        raise ValueError(
            f"india {india!r} is not a known place; known: {_listed(_INDIA_PLACES)}"
        )
    if filters.country and filters.country not in country_filter.CODES:
        raise ValueError(
            f"country {filters.country!r} is not a supported ISO 3166-1 alpha-2 code; "
            f"supported: {_listed(country_filter.CODES)}"
        )
    if kw_in and kw_in not in KEYWORD_SCOPES:
        raise ValueError(
            f"kw_in {kw_in!r} is not a known scope; known: {_listed(KEYWORD_SCOPES)}"
        )
    if sort and sort not in SORT_COLUMNS:
        raise ValueError(
            f"sort {sort!r} is not a known sort; known: {_listed(SORT_COLUMNS)} "
            "(omit it to rank by relevance)"
        )
    in_description = (
        filters.kw and "description" in KEYWORD_SCOPES[filters.kw_in].columns
    )
    if in_description and not capabilities.has_description:
        raise _unmigrated("a description keyword scope", "description")
    bracket = filters.salary_min is not None or filters.salary_max is not None
    if (bracket or filters.has_salary) and not capabilities.has_min_salary_annual:
        raise _unmigrated("a salary filter", "min_salary_annual")
    seen = (
        filters.seen_within is not None
        or filters.first_seen_after
        or filters.seen_after
        or filters.seen_before
    )
    if seen and not capabilities.has_first_seen:
        raise _unmigrated("a first-seen filter", "first_seen")
    if filters.max_age_days is not None and not capabilities.has_first_seen:
        # Without it the age of a Job with no readable posted date is unknown, and the filter
        # would quietly read the posted date alone (ADR-0322).
        raise _unmigrated("max_age_days", "first_seen")
    if bracket:
        _refuse_an_unserved_currency(
            filters.salary_currency, "a salary bound", capabilities
        )


def _refuse_an_unserved_currency(
    asked: str | None, what: str, capabilities: IndexCapabilities
) -> None:
    """Refuse ``what`` in a currency this table does not serve, where the bracket and the salary
    sort would silently fall back to :data:`SALARY_DEFAULT_CURRENCY`, or with that unserved too
    drop the bracket and order the sort unconverted."""
    currency = asked or SALARY_DEFAULT_CURRENCY
    if currency in capabilities.currencies:
        return
    said = (
        f"salary_currency {currency!r}"
        if asked
        else f"{what} with no salary_currency is priced in {currency}, which"
    )
    raise ValueError(
        f"{said} is not served by this index; it serves: "
        f"{_listed(capabilities.currencies)}"
    )


def _result_row(row: Mapping[str, Any], ranked: bool) -> dict[str, Any]:
    """One served result: every :data:`RESULT_COLUMNS` value, plus ``score`` after the id.

    Built from that one tuple rather than a hand-written dict beside it (ADR-0194), so the
    projection the query asks for and the fields the response carries cannot drift apart. A
    column the table lacks comes back None. ``id`` is the star identity —
    ``{ats}:{slug}:{native_id}``. ``url`` is rewritten at serve time (temporary; see
    :func:`_canonical_url`).
    """
    result: dict[str, Any] = {
        "id": row.get("id"),
        "score": round(1 - row["_distance"], 3) if ranked else None,
    }
    result.update(
        {column: row.get(column) for column in RESULT_COLUMNS if column != "id"}
    )
    result["url"] = _canonical_url(row.get("ats"), row.get("url"), row.get("id"))
    return result


def _job_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """One Job read by id (ADR-0277): a search row's fields without ``score``, then
    :data:`JOB_DETAIL_COLUMNS`, the description cut at :data:`JOB_DESCRIPTION_LIMIT`.
    ``description_chars`` is its whole length, so a reader knows how much the cut left out."""
    result: dict[str, Any] = {column: row.get(column) for column in RESULT_COLUMNS}
    result["url"] = _canonical_url(row.get("ats"), row.get("url"), row.get("id"))
    result.update({column: row.get(column) for column in JOB_DETAIL_COLUMNS})
    description = row.get("description") or ""
    result["description"] = description[:JOB_DESCRIPTION_LIMIT] or None
    result["description_chars"] = len(description)
    result["description_cut"] = len(description) > JOB_DESCRIPTION_LIMIT
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
    and :func:`headstart.serving.facets.counts`, and read field by field by the UI adapters for the
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
        # synced since, `build_filter` falls back to `india_gazetteer.where("india")`'s slower-but-correct
        # regex alternation rather than erroring on a column that isn't there yet.
        has_country = india_filter.has_column(names)
        self.capabilities = IndexCapabilities(
            # the ATSes actually present in the index — feeds the dropdown and the whitelist
            atses=sorted(
                {
                    r["ats"]
                    for r in table.search()
                    .select(["ats"])
                    .limit(WHITELIST_SCAN_ROWS)
                    .to_list()
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
                        .limit(WHITELIST_SCAN_ROWS)
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
        #: What :meth:`jobs_by_id` asks for: the projection plus the detail columns present.
        self.job_projection = self.projection + tuple(
            c for c in JOB_DETAIL_COLUMNS if c in names
        )
        #: The family tables a category across the whole index reads (ADR-0322); the app sets
        #: them once it has loaded the role assignments. None: ``family=`` needs ``board=``.
        self.families: FamilyTables | None = None
        # Facets ignore the semantic query and the served table is immutable for this process's
        # lifetime (the Space restarts when a new table lands). Cache only the parsed structured
        # filters, bounded so arbitrary public requests cannot grow memory without limit.
        self._facet_cache: OrderedDict[
            tuple[SearchFilters, str | None, bool], tuple[float, dict[str, Any]]
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
        # Requirements answers (ADR-0324) and each asked family's ids as one Arrow array, both for
        # this boot's immutable table, so neither needs a TTL.
        self._requirements_cache: OrderedDict[
            tuple[Any, ...], tuple[float, dict[str, Any]]
        ] = OrderedDict()
        self._requirements_cache_lock = Lock()
        self._family_arrays: dict[str, Any] = {}
        # A description keyword's rows, found once and shared by the ranked page, the facet
        # total and every later page (ADR-0320).
        self._description_matches = DescriptionMatches(table, self.capabilities)
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
        # The materialized verdicts only speed a filter up (ADR-0173), so they share the line
        # rather than claim a disabled feature: a table without them answers on the raw clause.
        caps = self.capabilities
        slow = [
            name
            for name, live in (
                ("employment_type flags", caps.has_employment_type_flags),
                ("description_stored", caps.has_description_stored),
                ("salary_known", caps.has_salary_known),
                ("posted_at_comparable", caps.has_posted_at_comparable),
                ("experience flags", caps.has_experience_filter_flags),
            )
            if not live
        ]
        parts = []
        if dark:
            parts.append(
                f"served table is missing {', '.join(dark)} — every filter and sort keyed on "
                "those columns is disabled for this table, not failing"
            )
        if slow:
            parts.append(f"slow path (unmaterialized): {', '.join(slow)}")
        if parts:
            _log.warning("; ".join(parts))
        rows = table.count_rows()
        if rows > WHITELIST_SCAN_ROWS:
            _log.warning(
                f"ats/currency whitelists read {WHITELIST_SCAN_ROWS:,} of {rows:,} rows — "
                "a value only the unread rows carry is dropped as unknown"
            )
        # A served currency with no rate joins the unpriced rows in a cross-currency salary
        # sort and bracket (`fx.convert` refuses 1:1). No table at all is `fx`'s own line.
        rates = (fx.table() or {}).get("rates")
        if rates and (unpriced := [c for c in caps.currencies if c not in rates]):
            _log.warning(
                f"served currencies with no fx rate: {log.named_sample(unpriced)}"
            )
        if caps.currencies and SALARY_DEFAULT_CURRENCY not in caps.currencies:
            # `run` converts a salary sort to the asked currency or the default; with neither served
            # it falls back to raw cross-currency ordering (the ADR-0178 "INR above USD" shape),
            # and `build_filter` compiles no bracket at all.
            _log.warning(
                f"{SALARY_DEFAULT_CURRENCY} not among served currencies: a salary sort with no "
                "served currency asked is unconverted, and such a bracket is dropped"
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
        this table's own runtime facts separately, so a caller (:mod:`headstart.serving.facets`, most
        of all) can vary one without rebuilding the other.
        """

        _int = _int_arg(args)
        kw = (args.get("kw") or "").strip() or None
        kw_in = (args.get("kw_in") or "").strip().lower()
        ats = (args.get("ats") or "").strip() or None
        etype = (args.get("etype") or "").strip() or None
        india = (args.get("india") or "").strip().lower() or None
        country = (args.get("country") or "").strip().upper() or None
        filters = SearchFilters(
            remote=args.get("remote") == "true",
            max_years=_int("max_years"),
            ats=ats,
            etype=etype,
            india=india,
            country=country,
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
            title_words=(args.get("title_words") or "").strip() or None,
            max_age_days=_int("max_age_days"),
            required_years_at_least=_int("required_years_at_least"),
            exclude_company=(args.get("exclude_company") or "").strip() or None,
        )
        # The one place a request is parsed, and so the one place a dropped filter can be
        # reported without `facets.counts` repeating it once per option — see the helper. Under
        # `strict=1` it is refused instead (ADR-0253).
        sort = (args.get("sort") or "").strip()
        if _is_strict(args):
            _refuse_what_strict_forbids(filters, kw_in, sort, self.capabilities)
        _warn_unknown_filters(filters, kw_in, sort, self.capabilities)
        return filters

    def _family_scope(
        self, args: Mapping[str, str], filters: SearchFilters
    ) -> _FamilyScope | None:
        """The category across the whole index a request names (:func:`_family_asked`) as it
        reads it, or None (ADR-0322).

        A family table has no description, so a keyword the description scope matches is
        matched on the served table, its rows found once (ADR-0320), and the request reads the
        family's rows it matched, its filters without the keyword."""
        family = _family_asked(args)
        if family is None:
            return None
        if self.families is None:
            # `strict=1` was refused by `scoped_jobs_clause`; the page never sends this.
            _log.warning(
                "scope widened: family=%.40r with no family tables loaded", family
            )
            return None
        table = self.families.table(family)
        if not reads_descriptions(filters, self.capabilities):
            return _FamilyScope(family, table, table, filters)
        keyword = self._description_matches.where(
            SearchFilters(kw=filters.kw, kw_in=filters.kw_in), None
        )
        return _FamilyScope(
            family,
            table,
            _rows_with_ids(table, _matching_ids(self._table, keyword)),
            replace(filters, kw=None, kw_in=None),
        )

    def _keyword_figures(
        self,
        scope: _FamilyScope,
        counted: dict[str, Any],
        extra_where: str | None,
    ) -> dict[str, Any]:
        """``counted`` with the figures of a description keyword ``scope`` matched before
        counting (ADR-0322), which :func:`facets.counts` could not make over rows already
        narrowed by it: what the keyword may match at all, over the whole family table, and
        whether it is the filter costing the most. A family table has no description column,
        so only a table with the materialized `description_stored` flag can say; without it,
        ``counted`` comes back as it is."""
        if not self.capabilities.has_description_stored:
            return counted
        unkeyed = with_extra(
            build_filter(scope.filters, self.capabilities), extra_where
        )
        coverage = facets.description_coverage(scope.table, unkeyed, materialized=True)
        counted = {**counted, "description_coverage": coverage}
        if counted["total"] or not coverage["total"]:
            return counted
        # Lifting the keyword recovers `coverage["total"]` rows; the filter `counts` named, if
        # any, only what lifting it recovers among the keyword's rows. The larger is the answer.
        named = counted["blocking"]
        recovered = 0
        if named:
            unset = False if isinstance(getattr(scope.filters, named), bool) else None
            lifted = replace(scope.filters, **{named: unset})
            where = with_extra(build_filter(lifted, self.capabilities), extra_where)
            recovered = (
                scope.rows.count_rows(filter=where)
                if where
                else scope.rows.count_rows()
            )
        if coverage["total"] > recovered:
            counted["blocking"] = "kw"
        return counted

    def facets(
        self, args: Mapping[str, str], *, extra_where: str | None = None
    ) -> dict[str, Any]:
        """Per-option result counts for these filters — see :mod:`headstart.serving.facets`.

        ``extra_where`` is the same Account clause :meth:`run` takes, and passing it here is not
        optional: the UI prints ``facets.total`` as "Showing 1-N of TOTAL", so counting without
        it reported the whole index beside a list of ten rows.

        Here rather than in the route so the table and the runtime schema facts stay behind
        this object; a caller reaching for ``_table`` to count would be the same class of leak
        that ``parse_filters`` exists to prevent on the filter side.

        ``counts=total`` in ``args`` counts no option (:data:`FACET_COUNTS`, ADR-0274), cached
        apart from the full strip; any other value but ``all`` is refused.

        A ``like=`` Job is left out of every count, as :meth:`run` leaves it out of the list.
        """
        filters = self.parse_filters(args)
        asked = (args.get("counts") or "").strip() or FACET_COUNTS[0]
        if asked not in FACET_COUNTS:
            raise ValueError(
                f"counts {asked!r} is not known; known: {_listed(FACET_COUNTS)}"
            )
        only_total = asked == "total"
        if like := _like_id(args):
            extra_where = with_extra(extra_where, _other_than(like))
        cache_key = (filters, extra_where, only_total, _family_asked(args))
        cached = _cache_get(
            self._facet_cache,
            self._facet_cache_lock,
            cache_key,
            FACET_CACHE_TTL_SECONDS,
        )
        if cached is not None:
            return cached
        started = time.monotonic()
        # A category across the whole index counts its family table: every count is exact and
        # none names the family's ids (ADR-0322).
        scope = self._family_scope(args, filters)
        counted = facets.counts(
            scope.rows if scope else self._table,
            scope.filters if scope else filters,
            self.capabilities,
            extra_where=extra_where,
            only_total=only_total,
            # A family table's filters never read descriptions: its keyword was matched first.
            table_where=(
                lambda varied: self._description_matches.where(varied, extra_where)
            )
            if not scope and reads_descriptions(filters, self.capabilities)
            else None,
        )
        if scope and scope.keyword_matched_first:
            counted = self._keyword_figures(scope, counted, extra_where)
        elapsed_ms = (time.monotonic() - started) * 1000
        if elapsed_ms > SLOW_SEARCH_MS:
            # The strip is ~46 counts, the most expensive request the Space serves; shapes only,
            # never the keyword text (ADR-0032).
            _log.warning(
                f"slow facets {elapsed_ms:.0f} ms: blocking={counted.get('blocking') is not None} "
                f"india={bool(filters.india)} country={bool(filters.country)} "
                f"kw_scope={filters.kw_in} "
                f"extra_where={extra_where is not None} only_total={only_total}"
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

        ``like=<id>`` ranks by that Job's own stored vector in place of ``q``, and leaves the Job
        out (ADR-0277); ``ValueError`` when it comes with ``q`` or names no served Job.
        """
        started = time.monotonic()
        query = (args.get("q") or "").strip()
        # `like=` ranks by one Job's own stored vector instead of an encoded query, and leaves
        # that Job out (ADR-0277); from here on it is a ranked search like any other.
        like = _like_id(args)
        ranked = bool(query or like)
        _int = _int_arg(args)
        filters = self.parse_filters(args)
        # Narrowed as `facets` narrows it, so both ask the same where-clause.
        narrowed = with_extra(extra_where, _other_than(like)) if like else extra_where
        # A category across the whole index reads its family table (ADR-0322): a browse lists
        # from it, and a ranked search asks the served table, which holds the vectors, to rank
        # exactly the family table's matching rows by id.
        scope = self._family_scope(args, filters)
        table = scope.rows if scope else self._table
        if scope:
            where = with_extra(build_filter(scope.filters, self.capabilities), narrowed)
            if ranked:
                ids = _matching_ids(table, where).to_pylist()
                where = _ids_in_clause(ids) if ids else "id IN ('')"
                table = self._table
        elif reads_descriptions(filters, self.capabilities):
            # Its rows found once, and shared with the facet counts (ADR-0320).
            where = self._description_matches.where(filters, narrowed)
        else:
            where = with_extra(build_filter(filters, self.capabilities), narrowed)
        # Whitelisted to a column name, never taken from the query string — this reaches an
        # ORDER BY. An unknown value is no sort at all, which is the existing behaviour.
        sort = SORT_COLUMNS.get((args.get("sort") or "").strip())
        # Under `strict=1` each fallback below is refused rather than taken (ADR-0253).
        strict = _is_strict(args)
        if sort == "first_seen" and not self.capabilities.has_first_seen:
            if strict:
                raise _unmigrated("sort=seen", "first_seen")
            sort = None  # same dark-until-migrated rule as the filters above
        if sort == "min_salary_annual" and not self.capabilities.has_min_salary_annual:
            if strict:
                raise _unmigrated("sort=salary", "min_salary_annual")
            sort = None  # likewise: the ADR-0082 columns arrive by migration
        # Salary is stored in the employer's own currency (ADR-0082), so ordering the raw
        # column ranked ₹40,00,000 above $300,000 — the first 400 rows of a salary sort were
        # all INR (ADR-0178). The sort is stated in ONE currency, resolved and whitelisted exactly like the
        # bracket's; a table with no such currency keeps the raw ordering it always had.
        sort_currency = None
        if sort == "min_salary_annual":
            if strict:
                _refuse_an_unserved_currency(
                    filters.salary_currency, "a salary sort", self.capabilities
                )
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
        browse_key = (filters, sort, k, page, extra_where, _family_asked(args))
        if not ranked:
            cached = _cache_get(
                self._browse_cache,
                self._browse_cache_lock,
                browse_key,
                BROWSE_CACHE_TTL_SECONDS,
            )
            if cached is not None:
                return cached

        encode_ms = 0.0  # a cache hit costs ~0 too; the slow line says which it was
        if ranked:
            encode_started = time.monotonic()
            vector = self._query_vector(query) if query else self._stored_vector(like)
            encode_ms = (time.monotonic() - encode_started) * 1000
            search = self._nearest(vector)
        else:
            search = table.search()  # no vector: a plain, filtered scan (ADR-0074)
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
            [*self.projection, "_distance"] if ranked else [*self.projection]
        )
        if not ranked and not sort:
            # `first_seen` alone is not a stable sort key: pipeline runs stamp it once per
            # sync batch, so thousands of rows tie on the exact same timestamp, and `offset`
            # pagination over a tied sort silently repeats and drops rows across pages
            # (measured 2026-08-20 against a real table: 2 of 5 rows recurred between page 1
            # and page 2 with no tiebreaker, zero recurred with one). `id` is unique per row,
            # so it breaks every tie deterministically. Plain dicts, not `lancedb.query.
            # ColumnOrdering` instances — lancedb's pydantic layer coerces either (verified
            # 2026-08-20).
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

        if sort and ranked:
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
            path = "ranked-window"
        elif sort_currency:
            rows = self._salary_browse(table, where, sort_currency, k, offset)
            path = "salary-browse"
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
            path = "ranked" if ranked else "browse"

        result = [_result_row(r, ranked) for r in rows]
        elapsed_ms = (time.monotonic() - started) * 1000
        if elapsed_ms > SLOW_SEARCH_MS:
            # Shapes only: the query text is the user's and is never logged (ADR-0032). The path
            # and encode time say where the time went: the model, the window, or the scan.
            _log.warning(
                f"slow search {elapsed_ms:.0f} ms: path={path} encode_ms={encode_ms:.0f} "
                f"indexed={self.has_vector_index} page={page} k={k} sort={sort} "
                f"query={bool(query)} like={bool(like)} extra_where={extra_where is not None} "
                f"where_len={len(where or '')}"
            )
        if not ranked:
            _cache_put(
                self._browse_cache,
                self._browse_cache_lock,
                browse_key,
                result,
                BROWSE_CACHE_SIZE,
            )
        return result

    def _salary_browse(
        self, table: Any, where: str | None, currency: str, k: int, offset: int
    ) -> list[dict]:
        """One page of a salary-sorted browse of ``table`` (the served table, or a family table,
        ADR-0322), stated in ``currency``.

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
        n_own = table.count_rows(filter=with_extra(where, own))

        def segment(clause: str, ordering: list[dict], limit: int, skip: int) -> list:
            if limit <= 0:
                return []
            return (
                table.search()
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

    def jobs_by_id(self, ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        """Up to :data:`MAX_JOB_IDS` served Jobs, whole enough to read (``/job``, ADR-0277),
        keyed by id; an id the table does not hold is simply absent. ``ValueError`` on no id,
        too many, or one past :data:`JOB_ID_MAX_CHARS`, which the route answers as 400.

        One id-equality scan asking only for :attr:`job_projection`: the table has no index on
        ``id``, and none is needed — 18–20 ms for one to five ids on the 514,163-row table
        (measured 2026-09-29 on a local copy)."""
        wanted = list(dict.fromkeys(i for i in ids if i))
        if not wanted or len(wanted) > MAX_JOB_IDS:
            raise ValueError(f"name 1 to {MAX_JOB_IDS} job ids")
        for job_id in wanted:
            _checked_job_id(job_id, "id")
        rows = (
            self._table.search()
            .where(_ids_in_clause(wanted))
            .select([*self.job_projection])
            .limit(len(wanted))
            .to_list()
        )
        return {row["id"]: _job_row(row) for row in rows}

    def _stored_vector(self, job_id: str) -> Any:
        """``job_id``'s own stored vector, which ``like=`` ranks by; ``ValueError`` when the
        table does not hold it. 12 ms on the table above."""
        rows = (
            self._table.search()
            .where(_ids_in_clause([job_id]))
            .select(["vector"])
            .limit(1)
            .to_list()
        )
        if not rows:
            raise ValueError(
                f"no job with id {job_id!r} is in the index now. {job_absence.WHY_NOT_SERVED}"
            )
        return rows[0]["vector"]

    def n_seen_within(self, hours: int) -> int | None:
        """How many Jobs entered the index in the last ``hours`` — ``None`` without the column.

        The door's freshness proof (ADR-0112). Exact, which is the whole reason it is there:
        `first_seen` is written by us on arrival, and a row that lacks it predates the column
        (ADR-0031) and therefore cannot be new — so unlike a coverage share this window has no
        unknown bucket to hand-wave. Compiled through :func:`build_filter` rather than a
        hand-written clause so "new" means here exactly what it means in the Search rail.

        One :meth:`count_rows` — measured at 4–6 ms in `headstart.serving.facets`.
        """
        if not self.capabilities.has_first_seen:
            return None
        return self._table.count_rows(
            filter=build_filter(SearchFilters(seen_within=hours), self.capabilities)
        )

    def locations(self, args: Mapping[str, str]) -> dict[str, Any]:
        """The locations the served jobs on ``board=`` (repeatable, required) carry most, at most
        ``limit=`` of them (default :data:`LOCATIONS_SHOWN`) — see
        :mod:`headstart.serving.location_counts`. A request naming no Board, too many, or a
        ``limit`` outside 1 to :data:`MAX_LOCATIONS` is a :class:`ValueError`: without Boards it
        would read every row's location."""
        where = _named_boards_clause(args)
        limit = _int_arg(args)("limit")
        limit = LOCATIONS_SHOWN if limit is None else limit
        if not 1 <= limit <= MAX_LOCATIONS:
            raise ValueError(f"limit must be from 1 to {MAX_LOCATIONS}")
        return location_counts.top(self._table, where, limit)

    def levels(self, args: Mapping[str, str]) -> dict[str, Any]:
        """The Trends level bands of the served jobs on ``board=`` (repeatable, required) — see
        :mod:`headstart.serving.level_counts`. A request naming no Board, or too many, is a
        :class:`ValueError`, as :meth:`locations` refuses one."""
        return level_counts.bands(self._table, _named_boards_clause(args))

    def requirements(
        self,
        args: Mapping[str, str],
        assignments: RoleAssignments | None,
        board_and_name: Callable[[str], tuple[str, str | None]] | None = None,
    ) -> dict[str, Any]:
        """What a sample of the Jobs matching ``args`` asks for (``/requirements``, ADR-0324).

        ``q=`` (a role) and/or ``family=`` (a role family) choose the Jobs, narrowed by every
        search filter and ``board=``. With ``q`` the sample is the :data:`REQUIREMENTS_SAMPLE`
        rows closest to it (with ``family`` too, the family's among the
        :data:`REQUIREMENTS_CATEGORY_WINDOW` closest); with ``family`` alone, the family's newest
        to HeadStart. :func:`requirement_counts.summarize` counts it, one Job per requisition, with
        each Job's Board and directory name from ``board_and_name``. ``matching`` is how many Jobs
        the filters and family admit, which a query does not narrow. Only the sample's
        descriptions are read, by id. A :class:`ValueError` names what the request got wrong; a
        family without role assignments loaded is :class:`ScopeUnavailable`. Scoped by Boards and
        filters only, so no Account's follow or hide list reaches it."""
        query = (args.get("q") or "").strip()
        family = (args.get("family") or "").strip()
        if not query and not family:
            raise ValueError("name a role with q=, a category with family=, or both")
        if family:
            if assignments is None:
                raise ScopeUnavailable(
                    "family= needs the role assignments, which this deployment has not loaded"
                )
            if family not in assignments.current:
                raise ValueError(
                    f"family {family!r} is not a configured family; configured: "
                    f"{_listed(sorted(assignments.current))}"
                )
        filters = self.parse_filters(args)
        where = with_extra(
            build_filter(filters, self.capabilities), scoped_boards_clause(args)
        )
        cache_key = (filters, where, query, family)
        cached = _cache_get(
            self._requirements_cache,
            self._requirements_cache_lock,
            cache_key,
            float("inf"),
        )
        if cached is not None:
            return cached
        started = time.monotonic()
        # A family implies assignments: its absence was refused above.
        in_family = (
            self._in_family(where, self._family_array(family, assignments))
            if family
            else None
        )
        if query:
            ids, scores = self._closest_ids(query, where, in_family)
            matching = len(in_family) if in_family is not None else self._count(where)
        else:
            ids, scores = in_family[:REQUIREMENTS_SAMPLE], []
            matching = len(in_family)
        answer = {
            "matching": matching,
            "order": "closest" if query else "newest",
            "sample_size": REQUIREMENTS_SAMPLE,
            "category_window": (
                REQUIREMENTS_CATEGORY_WINDOW if query and family else None
            ),
            "closest_score": scores[0] if scores else None,
            "farthest_score": scores[-1] if scores else None,
            **requirement_counts.summarize(
                self._rows_for_requirements(ids),
                tech_skills.vocabulary(),
                assignments.family_of if assignments else None,
                board_and_name,
            ),
        }
        elapsed_ms = (time.monotonic() - started) * 1000
        if elapsed_ms > SLOW_SEARCH_MS:
            # Shapes only, never the query (ADR-0032).
            _log.warning(
                f"slow requirements {elapsed_ms:.0f} ms: query={bool(query)} "
                f"family={bool(family)} where_len={len(where or '')}"
            )
        _cache_put(
            self._requirements_cache,
            self._requirements_cache_lock,
            cache_key,
            answer,
            REQUIREMENTS_CACHE_SIZE,
        )
        return answer

    def _count(self, where: str | None) -> int:
        return (
            self._table.count_rows(filter=where) if where else self._table.count_rows()
        )

    def _nearest(self, vector: Any) -> Any:
        """A cosine search around ``vector`` at the served table's operating point (ADR-0173)."""
        search = self._table.search(vector).metric("cosine")
        if self.has_vector_index:
            search = search.nprobes(ANN_NPROBES).refine_factor(ANN_REFINE_FACTOR)
        return search

    def _family_array(self, family: str, assignments: RoleAssignments) -> Any:
        """``family``'s served ids as one Arrow array, built once per family per boot."""
        import pyarrow as pa

        if family not in self._family_arrays:
            self._family_arrays[family] = pa.array(
                list(assignments.ids.get(family, ())), pa.string()
            )
        return self._family_arrays[family]

    def _in_family(self, where: str | None, members: Any) -> list[str]:
        """The ids of every Job ``where`` admits that is in ``members``, newest to HeadStart
        first (ties by id): one filtered scan of two columns. Its length is the family's
        matching count."""
        import pyarrow.compute as pc

        dated = self.capabilities.has_first_seen
        search = self._table.search()
        if where:
            search = search.where(where, prefilter=True)
        table = (
            search.select(["id", "first_seen"] if dated else ["id"])
            .limit(max(1, self._count(None)))
            .to_arrow()
        )
        table = table.filter(pc.is_in(table["id"], value_set=members))
        ordering = [("id", "ascending")]
        if dated:
            ordering.insert(0, ("first_seen", "descending"))
        return table.sort_by(ordering)["id"].to_pylist()

    def _closest_ids(
        self, query: str, where: str | None, in_family: list[str] | None
    ) -> tuple[list[str], list[float]]:
        """The :data:`REQUIREMENTS_SAMPLE` Jobs closest to ``query`` that ``where`` admits
        (given ``in_family``, those in it among the category window), and their similarity."""
        search = self._nearest(self._query_vector(query))
        if where:
            search = search.where(where, prefilter=True)
        window = (
            REQUIREMENTS_SAMPLE if in_family is None else REQUIREMENTS_CATEGORY_WINDOW
        )
        rows = search.select(["id", "_distance"]).limit(window).to_list()
        if in_family is not None:
            members = set(in_family)
            rows = [row for row in rows if row["id"] in members]
        rows = rows[:REQUIREMENTS_SAMPLE]
        return [row["id"] for row in rows], [
            round(1 - row["_distance"], 3) for row in rows
        ]

    def _rows_for_requirements(self, ids: list[str]) -> list[dict[str, Any]]:
        """The columns :mod:`requirement_counts` reads, descriptions included, for ``ids``
        alone: one id-equality read, as :meth:`jobs_by_id` reads."""
        if not ids:
            return []
        names = set(self._table.schema.names)
        columns = ["id", *(c for c in requirement_counts.COLUMNS if c in names)]
        rows = (
            self._table.search()
            .where(_ids_in_clause(ids))
            .select(columns)
            .limit(len(ids))
            .to_list()
        )
        # In the sample's own order, so a posting's first copy is its closest or newest row.
        place = {job_id: n for n, job_id in enumerate(ids)}
        return sorted(rows, key=lambda row: place.get(row.get("id"), len(place)))
