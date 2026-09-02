"""Shared conventions for the embed/search/eval layer (ADR-0005, ADR-0008) — and, since
ADR-0042, the one serving-path search implementation both UIs run.

The model id, the load-bearing task prefixes, the LanceDB table names, the encoder factory,
and the where-clause builders live here once. The embed/search/eval scripts import the
conventions instead of re-declaring their own copies, so a mismatched prefix or model id
can't drift into one script and silently degrade ranking (ADR-0005 warns a wrong prefix
throws no error), and every caller escapes filter input the same way.

Two where-clause builders, deliberately distinct: :func:`build_filter` is the **reference
product filter** — the full Search-filter vocabulary the UIs expose, previously duplicated
in the Space app — and :func:`eval_filter` is the frozen three-filter builder the wellfound
retrieval benchmark queries with (ADR-0019; its ``employment_type`` vocabulary includes
wellfound's ``cofounder``, which the product deliberately doesn't).

:class:`JobSearch` is the serving path behind one method: built once with the loaded
encoder and the open ``jobs`` table, ``run(args)`` takes a request's query-string mapping
and returns projected result rows. Both the HF Space app and the local dev server are thin
adapters over it — this module is synced into the Space image beside ``geo.py``
(deploy-space.yml), which is why it imports ``geo`` both ways below.

Only the encoder helpers need torch/sentence-transformers; they import lazily so the
constants and both filter builders stay importable (and unit-testable) without the ML stack.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

try:  # in the repo, a package member; in the Space image, a flat sibling module
    from headstart import geo
except ImportError:  # pragma: no cover - exercised only in the deployed Space
    import geo  # type: ignore[no-redef]

MODEL = "nomic-ai/nomic-embed-text-v1.5"
DOC_PREFIX = "search_document: "  # index time (ADR-0005)
QUERY_PREFIX = "search_query: "  # query time (ADR-0005)
PROD_TABLE = "jobs"  # the product's tech corpus (ADR-0019)
EVAL_TABLE = "wellfound"  # frozen retrieval benchmark (ADR-0019)

# employment_type is a fixed vocabulary (the UI <select>); an unrecognized value is
# rejected rather than interpolated into the LanceDB where-clause.
EMPLOYMENT_TYPES = frozenset({"full-time", "contract", "internship", "cofounder"})


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


def eval_filter(
    *,
    remote: bool = False,
    employment_type: str | None = None,
    max_years: int | None = None,
) -> str | None:
    """The frozen benchmark where-clause (ADR-0008, ADR-0019) — was ``build_filter``.

    Queries the wellfound eval table only; the product filter is :func:`build_filter`.
    ``employment_type`` is validated against :data:`EMPLOYMENT_TYPES` — an unknown value
    raises ``ValueError`` instead of being interpolated into the clause. ``max_years`` must
    already be an int; jobs with unknown experience (``min_years IS NULL``) are kept, since
    "unknown" is not "too senior" (ADR-0009).
    """
    filters: list[str] = []
    if remote:
        filters.append("remote = true")
    if employment_type:
        if employment_type not in EMPLOYMENT_TYPES:
            raise ValueError(f"unknown employment_type {employment_type!r}")
        filters.append(f"employment_type = '{employment_type}'")
    if max_years is not None:
        filters.append(f"(min_years <= {int(max_years)} OR min_years IS NULL)")
    return " AND ".join(filters) if filters else None


# ---- the product search path (ADR-0042) ----
# Everything below moved from the Space app, which had become the de-facto reference while
# this module lagged behind; the Space and the local dev server now both consume this.

# Canonical employment-type filters mapped onto the messy per-ATS raw values
# ("fulltime", "Full-time", "fulltime_permanent", "Permanent / Full-Time", …).
ETYPE_CLAUSES = {
    "full-time": "(lower(employment_type) LIKE '%full%'"
    " OR lower(employment_type) LIKE '%permanent%')",
    "part-time": "lower(employment_type) LIKE '%part%'",
    "contract": "(lower(employment_type) LIKE '%contract%'"
    " OR lower(employment_type) LIKE '%freelance%')",
    "internship": "lower(employment_type) LIKE '%intern%'",
}


# The sort control's values, mapped to the column each orders by (issue #275). A whitelist
# because the result reaches an ORDER BY; "rel" is deliberately absent, since relevance is the
# ranking a vector search already applies and asking for it means adding no ordering at all.
SORT_COLUMNS = {"posted": "posted_at", "seen": "first_seen"}


# The Keyword filter's scopes (ADR-0104): which served text columns a keyword is matched in.
# One map is the whole extension point — `build_filter` compiles whatever it says, `filter_kwargs`
# whitelists `kw_in` against its keys, and the rail's <select> mirrors them — so a new scope
# (company, location, department…) is one entry here and nothing else. `description` is nullable
# and exists only once ADR-0104's column migration has run, so any scope naming it is compiled
# only while `has_description` says the column is there, exactly like `has_first_seen`.
KEYWORD_SCOPES: dict[str, tuple[str, ...]] = {
    "title": ("title",),
    "description": ("description",),
    "both": ("title", "description"),
}
KEYWORD_DEFAULT_SCOPE = "title"
# Enough for "senior backend kubernetes aws remote"; a bound because every term is one more LIKE
# per scoped column on every count the facet strip issues.
_KEYWORD_MAX_TERMS = 5


def _int_arg(args: Mapping[str, str]):
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
    """A user term made safe for a quoted LIKE pattern: quotes doubled, length-capped."""
    return term[:60].replace("'", "''").lower()


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
    """The columns a scope compiles to on *this* table — `description` only once it exists."""
    columns = KEYWORD_SCOPES.get(scope or KEYWORD_DEFAULT_SCOPE, ())
    return tuple(c for c in columns if c != "description" or has_description)


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
    """
    if not url:
        return url
    if ats == "darwinbox" and _DARWINBOX_OLD in url:
        return url.replace(_DARWINBOX_OLD, _DARWINBOX_NEW, 1)
    if ats == "recruitee":
        return _rehost_recruitee(job_id, url)
    return url


def build_filter(
    *,
    remote: bool = False,
    max_years: int | None = None,
    ats: str | None = None,
    etype: str | None = None,
    india: str | None = None,
    location: str | None = None,
    company: str | None = None,
    has_salary: bool = False,
    salary_min: int | None = None,
    salary_max: int | None = None,
    salary_currency: str | None = None,
    posted_within: int | None = None,
    posted_sortable: bool = False,
    seen_within: int | None = None,
    posted_after: str | None = None,
    posted_before: str | None = None,
    seen_after: str | None = None,
    seen_before: str | None = None,
    first_seen_after: str | None = None,
    kw: str | None = None,
    kw_in: str | None = None,
    has_description: bool = False,
    atses: Collection[str],
    currencies: Collection[str] = (),
    has_first_seen: bool,
    has_min_salary_annual: bool,
) -> str | None:
    """The prod-table where-clause — the reference Search-filter compiler (ADR-0031).

    ``atses`` is the whitelist of ATSes actually present in the served table,
    ``has_first_seen`` whether the table carries that column, and ``has_min_salary_annual``
    likewise for the ADR-0082 salary columns — all runtime facts of the index a
    :class:`JobSearch` learns once at startup and passes through. Deliberately required, not
    defaulted: a caller that forgot them would silently drop the ATS whitelist and turn the
    alerts Watermark cutoff into no clause at all (ADR-0035's exactness guarantee), or error
    ``has_salary`` on a table LanceDB hasn't migrated onto the new columns yet.

    ``has_description`` (ADR-0104) is the one runtime fact that *is* defaulted, and the asymmetry
    is deliberate: forgetting it can only leave the Keyword filter's description scope dark —
    the safe direction — where forgetting ``has_first_seen`` would turn a cutoff into no clause.
    ``kw``/``kw_in`` are the Keyword filter: every term must appear (AND) in at least one of the
    scope's columns (OR); see :data:`KEYWORD_SCOPES`.
    """
    filters: list[str] = []
    if remote:
        filters.append("remote = true")
    if max_years is not None:
        filters.append(f"(min_years <= {int(max_years)} OR min_years IS NULL)")
    if ats in atses:  # whitelist — never interpolated from free text
        filters.append(f"ats = '{ats}'")
    if etype in ETYPE_CLAUSES:
        filters.append(ETYPE_CLAUSES[etype])
    if india:
        clause = geo.where(india)  # canonical-place lookup — unknown values are ignored
        if clause:
            filters.append(clause)
    if location:
        filters.append(f"lower(location) LIKE '%{_like(location)}%'")
    if company:
        filters.append(f"lower(company) LIKE '%{_like(company)}%'")
    if kw:
        # Per term, OR across the scope's columns; AND across terms. A scope whose only column is
        # absent compiles to nothing at all — dark, never an error — like every optional-column
        # filter above.
        columns = _keyword_columns(kw_in, has_description)
        for term in _keyword_terms(kw) if columns else ():
            filters.append(
                "(" + " OR ".join(f"lower({c}) LIKE '%{term}%'" for c in columns) + ")"
            )
    if has_salary and has_min_salary_annual:
        # `min_salary_annual` (ADR-0082), not the raw `salary` string: `salary` is only ever
        # populated from a scraper's own structured field, so gating on it silently excluded
        # every Job whose salary is only known via Tier-2 description-mining — most of this
        # initiative's own measured coverage on most ATSes. `min_salary_annual` is the fully
        # reconciled cascade result (Tier 1 or Tier 2), so it's the correct "do we have a real
        # number" check either way. Guarded like `has_first_seen` above: a table LanceDB
        # hasn't migrated onto the new columns yet would error on every query otherwise —
        # the feature stays dark until then rather than 500ing.
        filters.append("min_salary_annual IS NOT NULL")

    # The salary bracket (issue #275) is scoped to ONE currency, and that is not a UI nicety:
    # salary is period-normalised but deliberately never FX-converted (ADR-0082), so comparing
    # a bare number across currencies would rank 60,000 INR beside 60,000 USD as equals. The
    # currency therefore comes first and is whitelisted against what the table actually holds,
    # exactly like `ats` — never interpolated from free text. Without one the bracket does not
    # apply at all, because an unscoped bracket is the wrong answer, not a looser one.
    #
    # The currency is a *modifier of the bracket*, not a filter of its own: picking one with
    # both bounds empty must not quietly cut the result set to the 28.5% of Jobs that carry a
    # salary at all (measured 2026-08-25), which is what filtering on it alone would do. So it
    # only bites once the user has actually named a bound.
    if (
        salary_currency in currencies
        and has_min_salary_annual
        and (salary_min is not None or salary_max is not None)
    ):
        filters.append(f"salary_currency = '{salary_currency}'")
        if salary_min is not None:
            # The job's TOP of range clears the user's floor: a 90k-140k posting answers
            # "at least 100k". `max_salary_annual` is null on single-figure postings, so
            # COALESCE falls back to the one number there is rather than dropping the row.
            filters.append(
                f"COALESCE(max_salary_annual, min_salary_annual) >= {int(salary_min)}"
            )
        if salary_max is not None:
            # ...and its BOTTOM sits under the ceiling, so the two together are an overlap
            # test rather than containment: a band wider than the user's still qualifies.
            filters.append(f"min_salary_annual <= {int(salary_max)}")

    if posted_sortable:
        # Ordering by `posted_at` needs the same shape guard filtering by it does, and it has
        # to be compiled HERE rather than bolted onto the where-clause in `run` — otherwise the
        # facet counts, which never see the sort, would count rows the sorted list excludes and
        # the header would overstate the result set by the 8.4% carrying no readable date.
        filters.append("(posted_at LIKE '____-__-__%')")
    if posted_within is not None:
        # posted_at is a raw string; ISO-prefixed values (97%) compare correctly. The LIKE
        # shape guard excludes the rest — non-ISO forms like darwinbox's legacy
        # '21-Apr-2026' sort lexicographically ABOVE any ISO cutoff and would otherwise
        # leak into every window.
        cutoff = (datetime.now(UTC) - timedelta(days=int(posted_within))).strftime(
            "%Y-%m-%d"
        )
        filters.append(f"(posted_at >= '{cutoff}' AND posted_at LIKE '____-__-__%')")

    # Custom date ranges (both ends optional, both inclusive). Each value arrives as free
    # text and lands in a where-clause, so it is re-serialized through date.fromisoformat —
    # garbage raises ValueError, which the routes answer as 400, and nothing user-typed is
    # ever interpolated. Inclusive "before" compares strictly below the NEXT day, because
    # both columns hold date-or-datetime ISO strings and '2026-08-10T12:00' > '2026-08-10'.
    def _next_day(value: str) -> str:
        try:
            return (date.fromisoformat(value) + timedelta(days=1)).isoformat()
        except OverflowError as exc:  # 9999-12-31 + 1 day; a 400 like any bad date
            raise ValueError(f"date out of range: {value!r}") from exc

    if posted_after:
        start = date.fromisoformat(posted_after).isoformat()
        filters.append(f"(posted_at >= '{start}' AND posted_at LIKE '____-__-__%')")
    if posted_before:
        filters.append(
            f"(posted_at < '{_next_day(posted_before)}' AND posted_at LIKE '____-__-__%')"
        )
    if seen_after and has_first_seen:
        start = date.fromisoformat(seen_after).isoformat()
        filters.append(f"first_seen >= '{start}'")
    if seen_before and has_first_seen:
        filters.append(f"first_seen < '{_next_day(seen_before)}'")
    if seen_within is not None and has_first_seen:
        # In HOURS, not days: this window is meant to be shorter than one pipeline cycle.
        # No shape guard is needed here — unlike `posted_at`, we write `first_seen`
        # ourselves, so it is always ISO-8601 UTC. Rows predating the column are null, and
        # `NULL >= '…'` is never true, so they drop out on their own (ADR-0031).
        since = (datetime.now(UTC) - timedelta(hours=int(seen_within))).isoformat(
            timespec="seconds"
        )
        filters.append(f"first_seen >= '{since}'")
    if first_seen_after and has_first_seen:
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
    return " AND ".join(filters) if filters else None


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
    template context can carry them straight through.
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

    def filter_kwargs(self, args: Mapping[str, str]) -> dict[str, Any]:
        """The :func:`build_filter` keywords one request asks for, parsed exactly once.

        Split out of :meth:`run` so the ranked search and the facet counts (:mod:`headstart.
        facets`) compile the *same* description of the user's filters. Two call sites parsing
        the same query string independently is how a count comes to disagree with the list it
        is counting — the one defect that would make the whole facet feature worse than no
        counts at all, because a wrong number is trusted where a missing one is not.
        """

        _int = _int_arg(args)
        kw = (args.get("kw") or "").strip() or None
        kw_in = (args.get("kw_in") or "").strip().lower()
        return {
            "remote": args.get("remote") == "true",
            "max_years": _int("max_years"),
            "ats": (args.get("ats") or "").strip() or None,
            "etype": (args.get("etype") or "").strip() or None,
            "india": (args.get("india") or "").strip().lower() or None,
            "location": (args.get("location") or "").strip() or None,
            "company": (args.get("company") or "").strip() or None,
            "has_salary": args.get("has_salary") == "true",
            "salary_min": _int("salary_min"),
            "salary_max": _int("salary_max"),
            "salary_currency": (args.get("salary_currency") or "").strip().upper()
            or None,
            "posted_within": _int("posted_within"),
            # A sort by posting date can only place rows whose date is readable, so the
            # window it sorts is part of the filter, not of the ordering — see build_filter.
            "posted_sortable": SORT_COLUMNS.get((args.get("sort") or "").strip())
            == "posted_at",
            "seen_within": _int("seen_within"),
            "posted_after": (args.get("posted_after") or "").strip() or None,
            "posted_before": (args.get("posted_before") or "").strip() or None,
            "seen_after": (args.get("seen_after") or "").strip() or None,
            "seen_before": (args.get("seen_before") or "").strip() or None,
            "first_seen_after": (args.get("first_seen_after") or "").strip() or None,
            "kw": kw,
            # A scope is a modifier of the keyword, not a filter of its own (the salary
            # currency's rule): without a keyword it is None, so it can never be named as the
            # Blocking filter, and an unknown value falls back to the default rather than being
            # interpolated.
            "kw_in": (kw_in if kw_in in KEYWORD_SCOPES else KEYWORD_DEFAULT_SCOPE)
            if kw
            else None,
            "has_description": self.has_description,
            "atses": self.atses,
            "currencies": self.currencies,
            "has_first_seen": self.has_first_seen,
            "has_min_salary_annual": self.has_min_salary_annual,
        }

    def facets(self, args: Mapping[str, str]) -> dict[str, Any]:
        """Per-option result counts for these filters — see :mod:`headstart.facets`.

        Here rather than in the route so the table and the runtime schema facts stay behind
        this object; a caller reaching for ``_table`` to count would be the same class of leak
        that ``filter_kwargs`` exists to prevent on the filter side. Imported inside the method
        because :mod:`headstart.facets` imports back from this one — and both ways, because the
        Space image has no ``headstart`` package at all: it lays every module down flat beside
        ``app.py`` (deploy-space.yml). A package-only import here raised ``ModuleNotFoundError``,
        which the route's ``except ValueError`` does not catch, so ``/facets`` 500'd and the
        browser's own ``.catch`` degraded it to silence — no counts, no total, in production only.
        """
        try:  # in the repo, a package member; in the Space image, a flat sibling module
            from headstart import facets
        except ImportError:  # pragma: no cover - exercised only in the deployed Space
            import facets  # type: ignore[no-redef]

        return facets.counts(self._table, self.filter_kwargs(args))

    def run(self, args: Mapping[str, str]) -> list[dict]:
        query = (args.get("q") or "").strip()
        _int = _int_arg(args)
        where = build_filter(**self.filter_kwargs(args))
        # Whitelisted to a column name, never taken from the query string — this reaches an
        # ORDER BY. An unknown value is no sort at all, which is the existing behaviour.
        sort = SORT_COLUMNS.get((args.get("sort") or "").strip())
        if sort == "first_seen" and not self.has_first_seen:
            sort = None  # same dark-until-migrated rule as the filters above
        # `is None`, not `or`: the old route's `int(raw or 20)` gave k=0 → 1 row, and an
        # `or` on the parsed int would silently turn k=0 into the default 20 instead. Same
        # reasoning for `page`, new in ADR-0074: page=1 is the default, not a falsy no-op.
        k = _int("k")
        k = max(1, min(20 if k is None else k, self.max_k))
        page = _int("page")
        page = max(1, min(1 if page is None else page, self.max_page))
        offset = (page - 1) * k

        if query:
            search = self._table.search(encode_query(self._model, query)).metric(
                "cosine"
            )
        else:
            search = (
                self._table.search()
            )  # no vector: a plain, filtered scan (ADR-0074)
        if where:
            search = search.where(where, prefilter=True)
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
            # was already unreachable by any request. Measured 2026-08-25 on a 316,606-row
            # table: 2.7 ms for one page against 9.2 ms for the full 2,000-row window, so
            # keeping the query costs ~6.5 ms rather than a redesign.
            #
            # It is NOT a global sort, and the UI says so: a Job older than the 2,000th-best
            # match cannot appear. That is the honest shape of "newest among your best
            # matches" — the alternative, scanning by date, answers a question the user did
            # not ask by throwing their query away.
            window = search.limit(self.max_k * self.max_page).to_list()
            window.sort(
                key=lambda r: ((r.get(sort) or ""), r.get("id") or ""), reverse=True
            )
            rows = window[offset : offset + k]
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

        return [
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

    def indexed(self, ids: Collection[str]) -> set[str]:
        """Which of these job ids are still in the index — the Saved tab's "closed" check.

        The ids come back out of stored records the browser once sent, so they are escaped
        like every other filter term before reaching the where-clause."""
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
