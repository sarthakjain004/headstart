"""Which Board does this belong to — the one place that answers it, in both directions.

Before this module the answer was reimplemented independently at five call sites
(:mod:`headstart.config`, :mod:`headstart.ingest.board_failures`,
:mod:`headstart.ingest.scrape_join`, :mod:`headstart.ingest.index_plan`,
:mod:`headstart.harvest`), with three different policies for what to do when a slug won't parse
— a silent fallback key, a dropped-and-``None``, and a dropped-and-warned. That divergence was
never a design choice; it was five people solving the same problem without a shared name for it.
Two directions, both here:

**Construct** — a :class:`~headstart.config.CompanyRef` (or scraper) to its canonical key.
:func:`board_key` is the real per-ATS answer (``BaseScraper.board_key()``, ADR-0023) and can
raise on a slug its scraper cannot parse. Two names wrap it, chosen **per call site** rather than
collapsed into one, because the right thing to do with a raise genuinely differs by caller (see
ADR-0155):

- :func:`board_identity` — never raises; a slug that won't parse falls back to the plain
  ``ats:slug``, logged once per distinct Board (bounded, ADR-0039). For callers that need a name
  for *every* Board unconditionally: dedup and the parked-Board check
  (:mod:`headstart.scrapable_boards`, which stores the answer on each Board), cost/priority-ledger
  keys. Dropping a Board here would silently shrink the scrape list.
- :func:`board_key_of` — takes a raw ``"{ats}:{slug}"`` string (a shard report's own key, which
  carries no :class:`CompanyRef`) and returns ``None`` on anything that won't resolve. For callers
  pairing against a **real** ``board_key``-keyed ledger (``board_failures``, the ADR-0053
  unauthoritative-Board list): a synthetic fallback key here would be actively wrong, not merely
  unhelpful — it would silently reintroduce the two-keyspace bug ADR-0059/ADR-0096 spent two ADRs
  removing, because nothing built from a real scrape ever carries the synthetic spelling.

**Parse** — the reverse: :func:`board_of` recovers a Board from a Job id by splitting off the
last colon-separated segment. This is a **guess**, not an exact answer (ADR-0049) — a native id
can itself contain a colon, and for those this returns a Board that does not exist. Safe only
where both sides of a comparison run through this same function (so a phantom Board is produced
identically on each), or where the answer only ever falls back for an id on no *known* Board
(``index_plan.resolve_board``, which matches by prefix against a real keep-set first and reaches
this only when nothing in the keep-set matches).

Plus the two small conveniences duplicated ad hoc at a dozen-plus call sites each:
:func:`ats_of` (the ATS prefix of any ``{ats}:...``-shaped key — a board key, a Job id, or a
scrape-list working key; all three share the same first segment) and :func:`lower_key`
(the case-fold every Board-key comparison in this codebase uses, because a persisted ledger's
casing and a freshly-built ``board_key()`` need not agree, ADR-0049).
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

from headstart import log
from headstart.config import CompanyRef

_log = log.get(__name__)


def board_key(company: CompanyRef) -> str:
    """The Board's real key: its scraper's own ``board_key()`` (ADR-0023).

    Raises whatever the scraper's ``board_key()`` raises (or ``ValueError`` for an unregistered
    ``ats``) on a slug it cannot parse. This is the **strict** form — callers that cannot use a
    synthetic fallback (:func:`headstart.ingest.index_plan.live_keep_set`'s keep-set, which is
    matched against real Job-id prefixes, so a fabricated key would never match anything and is
    worse than an omission) call this directly and decide for themselves what a raise means.
    """
    from headstart.scrapers.registry import get_scraper

    return get_scraper(company.ats, company.slug, company.name).board_key()


#: Boards whose fallback reached the reporter, so one malformed slug says so once.
#:
#: "Seen", not "reported": past :data:`_IDENTITY_REPORT_CAP` a Board is added but no longer
#: named. The membership test is what keeps that cap counting *distinct* Boards.
#:
#: Module-level, and never cleared: a pipeline stage is one process, and the point is that every
#: caller shares one record. `board_identity` was reached from ~15 sites (`scrape_plan` x8,
#: `board_priority` x7, plus the Scrapable Board list's park and dedupe), each walking the
#: whole company list, so one bad slug restated itself ~15x per run — and a scraper whose
#: `board_key()` starts raising would emit `N_Boards x 15` lines for one bug. Since ADR-0191 a
#: `ScrapableBoard` computes it once and every one of those sites reads the stored answer, but
#: `harvest` and any second `scrapable_boards.load` in one process still reach it again.
#:
#: What reaches the fallback is decided by the *provenance* of the slug, and the two answers differ.
#: A **liveness**-ledger slug is a raw scraper slug, so `board_key()` really parses it: measured
#: 2026-09-09, `load_active_companies('data/validate/liveness', min_jobs=0)` (now
#: `scrapable_boards.load`) yields 91,325 Scrapable Boards and reaches this path **zero** times. A
#: **state**-ledger key is `board_key()`'s own *output*, and feeding one back in raises wherever the
#: scraper's parser demands its input form — every one of `data/state/board_cost.csv`'s 10,561
#: Workday keys is the shorthand `{co}/{site}`, which Workday's parser rejects because it wants a
#: careers URL. Until 2026-09-09 `board_cost._rekeyed` did exactly that round-trip on every row, so
#: each of `scrape-plan` and `join` emitted 10,561 lines a run — 21,122 in total, and 99.8% of the
#: `scrape_plan` *step*'s own output (10,561 of 10,585 lines; the surrounding job log is larger).
#: That caller is gone. ADR-0096's migration completed — the live ledger's legacy-key count read 0
#: on 2026-09-09 — so the shim, and the `report_failure` opt-out added for it, were both removed.
#: Nothing feeds a state-ledger key back through here today.
#:
#: `board_priority.csv` is keyed the same way (5,143 Workday keys, 5,120 of them that shorthand)
#: but never reaches here: `board_priority.load` returns `row["board"]` verbatim, and `pick_boards`
#: reads the `board_identity` of liveness-ledger Boards. That is the check on this diagnosis —
#: 1,142 of its Workday keys are absent from the cost ledger, so had it fed them back too the flood
#: would have been their 11,703-key union, not the 10,561 actually observed.
#:
#: So the blast radius was never nil, only mis-measured: the one population that was measured is
#: the one that does not reach the path. The bound below stands regardless, for the case the old
#: note was reaching for — a scraper whose `board_key()` starts raising on its *own* slugs.
_IDENTITY_FAILURES_SEEN: set[str] = set()

#: Distinct Boards named before the report goes quiet. Mirrors the compromise
#: `log.named_sample` strikes for the stages — enough examples to name the ATS and the parse
#: error, never a dump. Not that helper itself, though it is now importable from here: it
#: renders a list the caller already holds, and this reports as it goes, one Board at a time,
#: with no seam at which the whole set is in hand.
_IDENTITY_REPORT_CAP = 10

#: The annotation bound layered on that dedupe — one WARNING per process, the rest INFO. Two
#: different bounds because they answer two different floods: the set stops one Board being
#: restated ~15x, this stops N Boards each buying an annotation.
_IDENTITY_FAILURE = log.FirstOnly(_log)

#: :func:`board_key_of`'s own copy of both bounds. Separate state, because a key dropped from a
#: ledger and a Board re-keyed to its plain slug are different faults.
_KEY_OF_FAILURES_SEEN: set[str] = set()
_KEY_OF_FAILURE = log.FirstOnly(_log)


def board_identity(company: CompanyRef) -> str:
    """The Board's canonical key: ``board_key`` where the scraper can build one, the plain
    ``ats:slug`` where a malformed slug defeats it — never dropping the Board either way.

    Every caller passes a **raw scraper slug**, so a raise here is a real parse failure and is
    worth reporting. The one caller that fed an already-canonical key back in —
    ``board_cost._rekeyed``, for which the raise was the expected answer — is gone with ADR-0096's
    migration, and with it the ``report_failure`` opt-out that kept it from flooding the log.
    """
    try:
        return board_key(company)
    except Exception as exc:  # noqa: BLE001 - a malformed slug falls back to the plain key
        key = f"{company.ats}:{company.slug}"
        _report_identity_failure(key, exc)
        return key


def _report_identity_failure(key: str, exc: Exception) -> None:
    """Name a Board that fell back, once, up to :data:`_IDENTITY_REPORT_CAP` distinct Boards.

    The fallback key is a *different* identity from the one the rest of the pipeline uses for this
    Board — `scrapable_boards._elect` collapses on it and `index prune` builds its
    keep-set from it — so a Board quietly landing here can be scraped under one name and pruned
    under another.
    Worth a line even though nothing is dropped.

    The **first** distinct Board warns and carries its stack; every later one is INFO. Under
    Actions a WARNING is an annotation against a run-level quota (ADR-0039's 2026-09-08
    amendment), so N failing Boards must not buy N of them — but nor can this be INFO
    throughout. `index_plan`'s keep-set guard warns about the same population and is *not* a
    substitute: it runs in the **merge** job while this is reached from `scrape_plan` and
    `board_priority` in the plan and scrape jobs, so its annotation never appears on the job
    that hit the failure. A run whose plan stage silently re-keyed a whole ATS would show
    nothing on its own summary until a later job noticed.

    No running total accompanies the cap. A hook to flush one to does exist —
    `ingest.observability.summary` — but it is in `ingest`, which this module may not import (the
    curated-feed path reaches this module too), so the obstacle is the layering, not the absence
    of a mechanism. The expected count is zero, so any line at all is the signal; the named ones
    carry the ATS and the parse error, which is what a reader needs to find the scraper at fault.
    """
    if key in _IDENTITY_FAILURES_SEEN:
        return
    _IDENTITY_FAILURES_SEEN.add(key)
    if len(_IDENTITY_FAILURES_SEEN) <= _IDENTITY_REPORT_CAP:
        _IDENTITY_FAILURE.report(
            f"{key}: board_key() failed "
            f"({type(exc).__name__}: {exc}) — falling back to the plain ats:slug"
        )
    elif len(_IDENTITY_FAILURES_SEEN) == _IDENTITY_REPORT_CAP + 1:
        _log.info(
            f"further board_key() failures not named ({_IDENTITY_REPORT_CAP} shown) — "
            "each still falls back to the plain ats:slug"
        )


def board_key_of(report_key: str) -> str | None:
    """The canonical ``board_key()`` for a shard report's ``{ats}:{slug}`` key, or None if it
    will not resolve.

    The inputs to the failure and unauthoritative-Board ledgers arrive as raw ``{ats}:{slug}``
    strings — the *scrape* list's own working key (Workday's slug is a whole URL, so this is
    never the ``board_key`` shape). Both ``board_failures`` and ``scrape_join`` normalise it
    through this one conversion before it meets a real ``board_key``-keyed set.

    Unresolvable rows are dropped rather than passed through, because a key that cannot be
    resolved cannot be compared against a real one either: keeping it would let a Board accrue
    strikes that no successful scrape could ever clear, or protect nothing from eviction while
    looking like it protects something. That is the one place this differs from
    :func:`board_identity`, which falls back to the plain key because its job is to name every
    Board, not to pair two sets built from a real scraper's answer.
    """
    from headstart.scrapers.registry import get_scraper

    ats, sep, slug = str(report_key).partition(
        ":"
    )  # partition: a Workday slug holds colons
    if not sep or not ats or not slug:
        return None
    try:
        return get_scraper(ats, slug).board_key()
    except Exception as exc:  # noqa: BLE001 - a malformed slug must not sink the caller
        # Its callers count the drops but never the cause; name it, bounded the same two ways
        # as `_report_identity_failure` (a capped distinct-key dedupe, one WARNING per process).
        if report_key not in _KEY_OF_FAILURES_SEEN:
            _KEY_OF_FAILURES_SEEN.add(report_key)
            if len(_KEY_OF_FAILURES_SEEN) <= _IDENTITY_REPORT_CAP:
                _KEY_OF_FAILURE.report(
                    f"{report_key}: board_key() failed ({type(exc).__name__}: {exc}) — "
                    "dropped from the board_key-keyed ledger"
                )
            elif len(_KEY_OF_FAILURES_SEEN) == _IDENTITY_REPORT_CAP + 1:
                _log.info(
                    f"further board_key_of() failures not named ({_IDENTITY_REPORT_CAP} "
                    "shown) — each still dropped from the board_key-keyed ledger"
                )
        return None


def board_of(job_id: str) -> str:
    """The Board an id belongs to: the ``{ats}:{slug}`` prefix of ``{ats}:{slug}:{native_id}``.

    Split off only the *last* segment, so a slug that itself contains ``:`` (Workday's URL slugs)
    is preserved. **This is a guess, not an exact answer** (ADR-0049): the native id can carry
    colons too — real Workday ids include ``REQ: 228``, a postal address and an entire URL — and
    for those this returns a Board that does not exist.

    Safe only where both sides of a comparison run through this same function, so a phantom Board
    is produced identically on each and they still pair. Not safe where the result meets a **real**
    Board key: that mismatch is what ADR-0049 fixed. Both index planners now resolve ids against
    the live keep-set by prefix (``index_plan.resolve_board``) and call this only as the fallback
    for an id on no known Board, which is the self-comparing case again.

    The priority ledger is keyed by this function, so its consumers must pair against it rather than
    rebuild a key themselves: ``pick_boards`` now looks up each Board's ``ScrapableBoard.identity``
    (:func:`board_identity`, the real ``board_key()``) and ``embed_run.order_by_priority`` calls
    this. What remains of ADR-0049's caveat is only the colon-bearing native id — it writes a
    phantom Board no real key matches, which mis-*scores* that Board rather than evicting anything.
    """
    return job_id.rsplit(":", 1)[0]


def ats_of(key: str) -> str:
    """The ATS half of any ``{ats}:...``-shaped key.

    A board key, a Job id and a scrape-list working key (``{ats}:{slug}``) all share the same
    first colon-separated segment, so one split serves every one of them. Coerces to ``str``
    first: several callers split a key read back out of JSON, where it is typed ``Any``.
    """
    return str(key).split(":", 1)[0]


def lower_key(key: str) -> str:
    """Case-fold a Board key for a lookup or a dict key — the comparison every Board-key match in
    this codebase uses, because a persisted ledger's casing and a freshly-built ``board_key()``
    need not agree (ADR-0049).

    Plain ``str.lower()``, deliberately **not** ``str.casefold()`` — every existing call site this
    replaces already used ``.lower()``, and the two differ on some Unicode input, so switching
    would be a silent behaviour change unrelated to this consolidation. It is also not
    length-preserving (``'İ'.lower()`` is two characters): callers that slice a string by a
    position rather than by this function's output length are unaffected (they compare or index
    by position, never by the folded string's length), but a future caller must not assume
    otherwise.
    """
    return key.lower()


_URLISH = re.compile(r"^https?://", re.IGNORECASE)


def tenant(board_key: str) -> str:
    """The part of a board_key that names *whose* Board it is, without the site path.

    Deeper path segments name the career site, not the company, and an ATS lets a company name
    that site after whoever built it: Hyatt's Taleo section is `careersection/infosys_intl`, and
    reading the whole key labels **Hyatt** an Infosys board. The tenant is the Workday
    `{company}` before the site, or a URL's host, or the slug itself — and it still carries the
    real cases, since Avanade's Board is `accenture/AvanadeCareers`, whose tenant is Accenture.

    Taleo Business Edition is the exception to "a URL's host": its host is a pod many companies
    share (`phg.tbe.taleo.net`), and the company is the `org` its URL names. The company
    directory (ADR-0185) groups Boards by this, so a pod read as a tenant would merge them.

    Here rather than in `ingest.board_operator`, which re-exports it, because the scrape names a
    Board no source names by its tenant (`company_name.humanised`, ADR-0212), and nothing outside
    `ingest` may import from it.
    """
    slug = board_key.split(":", 1)[1] if ":" in board_key else board_key
    if board_key.startswith("taleo_be:"):
        org = parse_qs(urlsplit(slug).query).get("org")
        if org:
            return org[0]
    if _URLISH.match(slug):
        slug = slug.split("//", 1)[1]
    return slug.split("/", 1)[0]
