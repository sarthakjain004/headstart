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
from datetime import date, datetime, timedelta, timezone
from typing import Any

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


def _like(term: str) -> str:
    """A user term made safe for a quoted LIKE pattern: quotes doubled, length-capped."""
    return term[:60].replace("'", "''").lower()


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


def _canonical_url(ats: str | None, url: str | None) -> str | None:
    """Serve-time URL normalization; only darwinbox's stale links are rewritten (see above)."""
    if ats == "darwinbox" and url and _DARWINBOX_OLD in url:
        return url.replace(_DARWINBOX_OLD, _DARWINBOX_NEW, 1)
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
    posted_within: int | None = None,
    seen_within: int | None = None,
    posted_after: str | None = None,
    posted_before: str | None = None,
    seen_after: str | None = None,
    seen_before: str | None = None,
    first_seen_after: str | None = None,
    atses: Collection[str],
    has_first_seen: bool,
) -> str | None:
    """The prod-table where-clause — the reference Search-filter compiler (ADR-0031).

    ``atses`` is the whitelist of ATSes actually present in the served table and
    ``has_first_seen`` whether the table carries that column — both runtime facts of the
    index a :class:`JobSearch` learns once at startup and passes through. Deliberately
    required, not defaulted: a caller that forgot them would silently drop the ATS
    whitelist and turn the alerts Watermark cutoff into no clause at all (ADR-0035's
    exactness guarantee).
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
    if has_salary:
        filters.append("salary IS NOT NULL")
    if posted_within is not None:
        # posted_at is a raw string; ISO-prefixed values (97%) compare correctly. The LIKE
        # shape guard excludes the rest — non-ISO forms like darwinbox's legacy
        # '21-Apr-2026' sort lexicographically ABOVE any ISO cutoff and would otherwise
        # leak into every window.
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=int(posted_within))
        ).strftime("%Y-%m-%d")
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
        since = (
            datetime.now(timezone.utc) - timedelta(hours=int(seen_within))
        ).isoformat(timespec="seconds")
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
    request's query-string mapping and returns the projected result rows — ``[]`` for an
    empty query (no encode), ``ValueError`` for garbage filter input, which the routes
    answer as 400. ``max_k`` caps the page size so a crafted ``k`` can't dump the table.

    The two facts the UI templates need — :attr:`atses` for the Board dropdown and
    :attr:`has_first_seen` for the "first seen" control — are attributes, not methods, so a
    template context can carry them straight through.
    """

    def __init__(self, model: Any, table: Any, *, max_k: int = 100):
        self._model = model
        self._table = table
        self.max_k = max_k
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

    def run(self, args: Mapping[str, str]) -> list[dict]:
        query = (args.get("q") or "").strip()
        if not query:
            return []

        def _int(name: str) -> int | None:
            raw = args.get(name)
            return int(raw) if raw else None

        where = build_filter(
            remote=args.get("remote") == "true",
            max_years=_int("max_years"),
            ats=(args.get("ats") or "").strip() or None,
            etype=(args.get("etype") or "").strip() or None,
            india=(args.get("india") or "").strip().lower() or None,
            location=(args.get("location") or "").strip() or None,
            company=(args.get("company") or "").strip() or None,
            has_salary=args.get("has_salary") == "true",
            posted_within=_int("posted_within"),
            seen_within=_int("seen_within"),
            posted_after=(args.get("posted_after") or "").strip() or None,
            posted_before=(args.get("posted_before") or "").strip() or None,
            seen_after=(args.get("seen_after") or "").strip() or None,
            seen_before=(args.get("seen_before") or "").strip() or None,
            first_seen_after=(args.get("first_seen_after") or "").strip() or None,
            atses=self.atses,
            has_first_seen=self.has_first_seen,
        )
        # `is None`, not `or`: the old route's `int(raw or 20)` gave k=0 → 1 row, and an
        # `or` on the parsed int would silently turn k=0 into the default 20 instead.
        k = _int("k")
        k = max(1, min(20 if k is None else k, self.max_k))
        search = self._table.search(encode_query(self._model, query)).metric("cosine")
        if where:
            search = search.where(where, prefilter=True)
        return [
            {
                "id": r.get("id"),  # the star identity — {ats}:{slug}:{native_id}
                "score": round(1 - r["_distance"], 3),
                "title": r["title"],
                "company": r["company"],
                "location": r.get("location"),
                "remote": r["remote"],
                "employment_type": r.get("employment_type"),
                "min_years": r.get("min_years"),
                "salary": r.get("salary"),
                "ats": r.get("ats"),
                "posted_at": r.get("posted_at"),
                "first_seen": r.get("first_seen"),
                "url": _canonical_url(
                    r.get("ats"), r.get("url")
                ),  # temporary; see _canonical_url
            }
            for r in search.limit(k).to_list()
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
