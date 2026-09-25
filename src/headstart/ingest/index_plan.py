"""Pure planners for the search index — what to add, evict, and prune (ADR-0014, ADR-0023) —
and the pure derivations that report on a finished plan.

Everything here computes row sets without touching LanceDB, so the scoping invariants stay
unit-testable on CI's base-deps-only install. :mod:`headstart.ingest.index` is the CLI that opens
the table and executes these plans; ``apply_sync`` is the one mutation helper, shared by its
``sync`` and ``prune`` steps.

Two layers, because they reach different rows:

**Freshness sync** (ADR-0014) — after a scrape, an index row is stale once its Board has been
scraped **twice running** without re-emitting its id (ADR-0083; a single absence only marks it
**Unconfirmed**, because a scrape that came back short is indistinguishable from a closure). New
ids are added; re-seen ids are left untouched (id-only, v1). Crucially the delete is *scoped to
the Boards actually scraped*, so a partial harvest never evicts Boards it didn't touch — and a
dead Board (scraped, yields nothing) has all its rows drop out for free. That scope is per-Board
but all-or-nothing, so a Board whose scrape came back *truncated* still looks fully covered.
Two mechanisms answer that. Upstream of here, the scrape reports its own outcome and ``index sync``
drops Unauthoritative Boards from the scope entirely (ADR-0053); here, a truncation that reports
nothing at all still has to survive the grace period above, which a *transient* short scrape
cannot. A Board short the same way twice running evicts in full (ADR-0101).


**Prune sweep** (ADR-0023) — because the sync is board-scoped it can't reach rows on Boards that left
the scrape list, nor case-variant duplicates of one job (Workday sites like ``.../External`` vs
``.../external``). These planners compute what to drop in those two cases; the duplicate case also
covers one requisition on several Boards of its Tenant — a Workday tenant's sites (ADR-0187), a
Taleo tenant's career sections or sites and an ADP client's career centers (ADR-0223) — which sync
declines to add in the first place, and an Eightfold career site's copy of a posting its backing
Board serves, matched on the stored ``requisition`` (ADR-0210).

Both layers ask "which Board owns this id", and both answer it through :func:`resolve_board`, whose
docstring says why they must agree (ADR-0049).
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from headstart import log, scrapable_boards
from headstart.board_identity import ats_of, board_key, board_of, lower_key
from headstart.corpus import iter_jobs
from headstart.ingest.board_operator import tenant

_log = log.get(__name__, __spec__)

#: The version of the rules that decide which served rows are duplicates of each other (ADR-0188).
#: A change to them removes rows that were served before, all in the tick it first runs, and the
#: Trends chart would draw that as a hiring drop; ``role_trends`` stamps this into the
#: ADR-0164 epoch ledger so the chart marks it instead. Bump it in the change that alters which
#: rows count as duplicates: a new grouping in :func:`plan_prune`, or a new alias-ledger signal
#: (:mod:`headstart.board_aliases`), or an existing signal's first ledger for an ATS (ADR-0222).
#: Don't bump it for a routine rewrite of an alias ledger that already exists, nor for a
#: ``config.PARKED_BOARDS`` entry, which is a temporary hold rather than a duplicate rule. The
#: marker lands on the step only because both routes remove rows through ``index prune``, which
#: has no grace period; a dedup that instead stops emitting ids at scrape time would drain
#: through ``sync``'s two-scrape grace (ADR-0083) and read as a slow decline after the marker, so
#: keep new dedup rules on the prune path.
#:
#: 1 — the rules when the counter was added (ADR-0188): casing duplicates, redirect and
#:     ``shared-reqs`` aliases. 2 — Taleo Enterprise ``subset-reqs`` aliases (ADR-0186).
#: 3 — one row per Workday tenant and requisition, public sites first (ADR-0187).
#: 4 — Eightfold ``backing-reqs`` aliases onto the ATS Board behind the career site (ADR-0205).
#: 5 — one row per posting across an Eightfold site and its backing Board, on ``requisition``
#:     (ADR-0210). Its removals follow the stamps, which arrive as each Board is re-scraped, so they
#:     spread over days after the marker rather than landing on it (ADR-0188's amendment).
#: 6 — one row per tenant and requisition extended to Taleo Enterprise, Taleo BE and ADP WFN
#:     (ADR-0223).
#: 7 — iCIMS redirect aliases, 270 Boards (ADR-0222).
#: 8 — the Eightfold fronts the first pairs file left out: 25 pairs across 22 fronts
#:     (ADR-0210's amendment).
DEDUP_VERSION = 8


@dataclass(frozen=True, slots=True)
class SyncPlan:
    """Ids to add to the index and ids to evict from it, plus what was withheld for a second look.

    ``unconfirmed`` is the grace period's own output (ADR-0083): **per-id** evidence that this id
    has been absent for one scrape of its Board and needs a second before it is believed. It is
    the only withholding this planner does — the ADR-0046 Board-level cap that used to sit beside
    it was removed by ADR-0101, which records why the two were never the redundant pair they
    looked like.

    The caller persists ``unconfirmed`` and hands it back next run as ``was_unconfirmed``.

    ``refused`` is the fresh ids *not* added because another Board of the same Tenant (a Workday
    site, a Taleo section or career site, an ADP career center) serves, or is being given, the same
    requisition — the rule ``plan_prune`` enforces on rows already indexed, applied where rows
    arrive so prune never has to take them back out.
    """

    add: frozenset[str]
    delete: frozenset[str]
    unconfirmed: frozenset[str] = frozenset()
    refused: frozenset[str] = frozenset()


def grace_period_counts(
    was_unconfirmed: AbstractSet[str], fresh: AbstractSet[str], plan: SyncPlan
) -> tuple[int, int]:
    """``(reappeared, still_waiting)`` for the ids the last run left unconfirmed (ADR-0083).

    Public and separate from :func:`sync` so a test can pin the derivation against real
    ``plan_sync`` output. Inlined in ``sync``, the only way to test it was to restate the same
    expressions in the test, which passes just as happily when they are wrong.

    ``reappeared`` is measured against ``fresh`` — "is this id in the scrape we just took?" —
    rather than inferred as the remainder after subtracting the other buckets. Subtraction
    over-counts, and ADR-0083 names exactly why: "An id that reappeared, was pruned, or sat on a
    board that left the ledger is simply not written again." Only the first of those three is a
    reappearance; the other two are rows on their way out via ``plan_prune``'s off-Board sweep,
    and folding them in would report churn that never happened.

    ``still_waiting`` is the carried-in ids that are unconfirmed *again* after this run — they
    neither came back nor were evicted. **This is the accretion signal**, and it has three
    distinct causes after ADR-0101 removed the collapse guard. They are worth separating only
    because a log line or a review written against the old set will misattribute them; all three
    reach the same branch here, because all three mean the Board is absent from ``scraped_boards``
    and this function cannot tell them apart:

    - The id's Board was not in this run's slice at all. Only ~80,000 are — about half of the
      Scrapable Boards — so this dominates a healthy set and is entirely benign; the streak simply
      did not advance.
    - The Board *was* scraped but came back Unauthoritative, so ``index sync`` subtracted it from
      the scope before calling this (ADR-0053). Measured at 63–126 Boards per run
      (``docs/pipeline/2026-09-01_twelve-run-log-review.md``), so it is not a rounding error — and
      unlike the first cause it has no drain, which is what makes the total worth watching.
    - The Board raised, a 404 included, so it wrote no ids and is not in ``boards_ok`` either,
      and :func:`scraped_boards` never saw it. A Board that scraped *clean* with zero jobs is no
      longer a cause: ``scrape_join`` adds it from ``boards_ok``, so its rows evict in scope.

    What is *no longer* a cause is the ADR-0046 collapse guard capping a Board still in scope: an
    id whose Board was scraped, was in scope, and was absent again is now evicted, not carried.

    Deliberately does *not* return an evicted count. With the grace period on, ``plan.delete`` is
    a subset of ``was_unconfirmed`` by construction (``eligible = absent & previously``), so such
    a number would be exactly the ``evict`` figure the plan line already prints — an intersection
    implying a distinction that cannot exist.
    """
    reappeared = was_unconfirmed & fresh
    # Subtract `fresh`: the two buckets must be disjoint or the line can print more than it
    # carried in. `fresh` is not filtered by `boards`, and ADR-0053 drops an Unauthoritative
    # Board *from* `boards` — so a carried-in id on a scope-excluded Board that genuinely came
    # back is both in `fresh` and re-added by the carry-forward loop, which asks only whether its
    # Board went unscraped. Counting it in each bucket inflates the accretion signal with an id
    # that demonstrably returned.
    return len(reappeared), len(was_unconfirmed & plan.unconfirmed - reappeared)


def plan_sync(
    index_ids: Iterable[str],
    fresh_ids: Iterable[str],
    scraped_boards: Iterable[str],
    live: dict[str, str],
    was_unconfirmed: Iterable[str] | None = None,
    *,
    site_jobs: dict[str, int] | None = None,
    replaced: AbstractSet[str] = frozenset(),
    requisitions: Mapping[str, str] | None = None,
    backing: Mapping[str, Iterable[str]] | None = None,
) -> SyncPlan:
    """Diff the current index against a scrape's fresh ids, scoped to the Boards it covered.

    ``add`` is the fresh ids not already indexed. ``delete`` is the indexed ids whose Board was
    scraped this run yet are missing from ``fresh_ids`` — a posting that has closed. An indexed id on
    a Board *not* in ``scraped_boards`` is never touched (the partial-harvest safety), and a re-seen
    id (present in both) is left as-is (id-only change detection).

    ``live`` is the :func:`boards_by_canon` lookup ids resolve through; pass an empty dict when
    there is no ledger to read, which degrades to :func:`~headstart.board_identity.board_of`. Required
    rather than defaulted: omitting it silently restores the scoping ADR-0049 records as *worse*
    than the bug it fixes.

    **One site per Workday requisition (ADR-0187), and one Board per Taleo or ADP requisition
    (ADR-0223).** A fresh id is not added — it lands in ``refused`` instead — when another Board
    of its Tenant already serves the same requisition from a live Board (unless it is a public
    copy and every serving site is non-public: that one displaces them), or when several sites
    bring it at once and another is the :func:`_survivor_board`. That is ``plan_prune``'s
    grouping, applied where rows arrive: without it, sync would re-add on the next run every copy
    prune took out. The served row is
    judged *after* this plan's evictions, so a survivor its Board stopped listing makes way on the
    scrape that evicts it, and one on a Board that left ``live`` makes way at once — the other
    copy arriving whenever its own Board is next scraped. ``replaced`` names ids the caller took
    out of the table only to re-add them with a new vector (ADR-0050); they are still their
    requisition's served row.

    **One row per posting across an Eightfold site and its backing Board (ADR-0210).** The same
    rule reaches an Eightfold id whose ``requisitions`` stamp a row on one of its ``backing``
    Boards also carries: it joins that row's group, where the backing row always wins. So the
    copy is refused while the backing row is served, and comes back on a later scrape of its own
    Board once the backing row is evicted or its Board leaves ``live``. Unstamped rows never match.

    **No Board-level cap (ADR-0101).** The board-scope check above is all-or-nothing at the *line*
    level: a Board that emitted one job line is fully in scope, so a scrape truncated by a
    rate-limit looks exactly like a Board that delisted everything it didn't re-emit. ADR-0046
    capped what a Board could lose in one run for exactly that reason, and ADR-0101 removed the
    cap: a truncation now has to survive **two consecutive scrapes of the same Board** to evict
    anything, because the grace period below is applied first, and the Boards that can describe
    their own truncation already leave the scope entirely (ADR-0053). What that trades away is
    recorded in ADR-0101 — a Board short the same way twice running sheds every missing row at
    once, where the cap would have spread it over several runs.

    **The grace period (ADR-0083).** ``was_unconfirmed`` is the ``unconfirmed`` set this function
    returned on the previous run. An id missing from ``fresh_ids`` is only *eligible* for deletion
    if it was already in that set — i.e. it has now been absent for **two consecutive scrapes of
    its own Board**. A first absence is returned in ``unconfirmed`` instead and nothing is deleted
    for it.

    The unit is *scrapes of that Board*, not runs, and that distinction is the whole point: only
    ~80,000 Boards, about half of the Scrapable Boards, are in any run's slice, and
    ``index sync`` already keeps
    Unauthoritative Boards out of ``scraped_boards`` (ADR-0053) — so a Board this run did not
    read is no evidence either way. Its ids keep their previous state rather than being counted
    as confirmed-present (which would reset the streak and make the grace period unreachable) or
    as absent (which would evict on a Board nobody looked at).

    Measured basis for two rather than three: every false eviction in
    ``docs/pipeline/2026-08-23_false-board-eviction-root-cause.md`` was a *single isolated* miss.
    The one id evicted twice (``successfactors:careers.hcltech.com:1364226855``) was verified
    present in the scrape between its two evictions — and the mechanics force that, since a
    second eviction requires a re-add, which requires reappearing in ``fresh_ids``.

    ``None`` and an empty set are **opposites** here, and the distinction is load-bearing.
    ``None`` disables the grace period entirely, restoring the previous evict-on-first-absence
    behaviour; it exists for callers written before this and is not what the pipeline passes. An
    empty set means the grace period is *on* and nothing is owed a second look yet — which is the
    cold-start path, since ``index sync`` reads a missing state file as an empty set. Cold start
    is therefore safe by construction: nothing is eligible, so that run deletes nothing and merely
    records what was absent.
    """
    index = set(index_ids)
    fresh = set(fresh_ids)
    boards = set(scraped_boards)
    add = fresh - index
    # None means "no grace period" (the pre-ADR-0083 behaviour); an empty set means "the grace
    # period is on and nothing is owed a second look yet". Those are different, so the None check
    # cannot collapse into `or set()`.
    grace_on = was_unconfirmed is not None
    previously = set(was_unconfirmed or ())

    indexed_by_board: dict[str, set[str]] = defaultdict(set)
    for job_id in index:
        board = resolve_board(job_id, live)
        if board in boards:
            indexed_by_board[board].add(job_id)

    delete: set[str] = set()
    unconfirmed: set[str] = set()
    for board, ids in indexed_by_board.items():
        absent = {i for i in ids if i not in fresh}
        # Eligible = absent now *and* absent at this Board's previous scrape. A first absence is
        # not eligible; it lands in `unconfirmed` below and gets one more look. Named apart from
        # `absent` on purpose — they differ by exactly the grace period, and reusing one word for
        # both is how this reads as "missing" twice and means two things.
        eligible = absent & previously if grace_on else absent
        delete |= eligible
        # A first absence is unconfirmed; a second evicts. Inside this branch `eligible` is
        # `absent & previously`, so this carries exactly the ids seen missing for the first time.
        if grace_on:
            unconfirmed |= absent - eligible

    if grace_on:
        # An id whose Board this run did not scrape keeps the state it had: no evidence arrived,
        # so its streak neither advances nor resets. Without this the set would be rebuilt from
        # the slice alone and a Board's ids would silently reset every run it sat out — with
        # ~80,000 Boards scraped per run — about half of the Scrapable Boards — so most
        # ids would never reach a second absence
        # and the grace period would never evict anything.
        #
        # Carried forward only while the Board is *still live*, which bounds the set. A Board
        # that leaves the ledger is never scraped again, so its entries would otherwise persist
        # for good — the same slow accretion ADR-0055 had to unwind for the collapse guard's
        # own hold, before ADR-0101 removed that guard. Those rows leave
        # the index through `plan_prune`'s off-Board sweep, and their entries leave here with
        # them. The check is per-Board, not per-id, so it costs a handful of lookups rather than
        # an intersection against the whole index.
        for job_id in previously:
            board = resolve_board(job_id, live)
            if board not in boards and lower_key(board) in live:
                unconfirmed.add(job_id)

    served = (index - delete) | (add & replaced)
    copies = _backing_copies(served | add, live, requisitions or {}, backing or {})
    refused = _other_site_copies(add, served, live, site_jobs or {}, copies)
    return SyncPlan(
        add=frozenset(add - refused),
        delete=frozenset(delete),
        unconfirmed=frozenset(unconfirmed),
        refused=frozenset(refused),
    )


def _placement(
    job_id: str,
    live: dict[str, str],
    copies: Mapping[str, tuple[str, str]] | None = None,
) -> tuple[tuple[str, str], str] | None:
    """``(duplicate group, lowercased Board)`` for an id on a live Board, else None.

    The group is ``(Board or Tenant, native id)`` — one served row each, the Tenant on the ATSes
    :func:`_requisition_tenant` names — and it is the one grouping both planners use:
    ``plan_prune`` to collapse the rows a group already holds, ``plan_sync`` to decline copies of
    one it already serves. An Eightfold id in ``copies``
    (:func:`_backing_copies`) joins the group of the backing row that carries its requisition
    instead (ADR-0210).
    """
    end = _live_board_end(job_id, live)
    if end is None:
        return None
    canon, native = lower_key(job_id[:end]), job_id[end + 1 :]
    group = (copies or {}).get(job_id)
    return group or (_requisition_tenant(canon, native) or canon, native), canon


def _backing_copies(
    job_ids: Iterable[str],
    live: dict[str, str],
    requisitions: Mapping[str, str],
    backing: Mapping[str, Iterable[str]],
) -> dict[str, tuple[str, str]]:
    """``{Eightfold id: the duplicate group of a row among job_ids that serves its requisition
    on one of its backing Boards}`` (ADR-0210).

    ``requisitions`` is each stamped row's ``requisition``; ``backing`` maps an Eightfold Board's
    slug to its backing Board keys (:mod:`headstart.eightfold_backing`). A row with no stamp never
    matches, so an unstamped pair keeps being served twice rather than risk serving it never.

    The Eightfold row joins the backing row's group rather than both joining a group keyed on
    the requisition, because a backing Board can serve one requisition as several rows —
    Greenhouse posts per location, SuccessFactors per locale — and those stay per row. A Workday
    or Taleo Enterprise backing Board is matched on its Tenant, the group ADR-0187 and ADR-0223
    already serve a requisition from, so a copy the Tenant serves from another Board still counts;
    a backing row is also found on its own Board, whatever its stamp looks like.
    """
    if not requisitions or not backing:
        return {}
    # A pair whose backing Board is itself an Eightfold site (a company's second site, #154)
    # is ADR-0205's alias business, not this rule's: the backing row must be a different ATS's.
    by_slug = {
        lower_key(f"eightfold:{slug}"): [
            lower_key(b) for b in boards if not b.startswith("eightfold:")
        ]
        for slug, boards in backing.items()
    }
    held: dict[tuple[str, str], tuple[str, str]] = {}
    fronts: list[tuple[str, str, str]] = []
    for job_id in job_ids:
        requisition = requisitions.get(job_id)
        placed = _placement(job_id, live) if requisition else None
        if placed is None:
            continue
        group, board = placed
        if board in by_slug:
            fronts.append((job_id, board, requisition))
        else:
            # Held under its own Board as well as its group: a Taleo Enterprise row is grouped on
            # its Tenant by its native `jobId`, while the lookup below tests the stamped
            # `contestNo`, so a `contestNo` with no digit still finds the row on its own Board.
            for key in {(group[0], requisition), (board, requisition)}:
                held[key] = min(group, held.get(key, group))
    copies: dict[str, tuple[str, str]] = {}
    for job_id, board, requisition in fronts:
        found = [
            held[key]
            for b in by_slug[board]
            if (key := (_requisition_tenant(b, requisition) or b, requisition)) in held
        ]
        if found:
            copies[job_id] = min(found)
    return copies


def _other_site_copies(
    new: set[str],
    served: set[str],
    live: dict[str, str],
    site_jobs: dict[str, int],
    copies: Mapping[str, tuple[str, str]],
) -> set[str]:
    """The ``new`` ids not to add because their requisition is served from another Board.

    An incumbent wins: a requisition already served from a live Board stays there, and a copy
    arriving from any other Board is refused — so a survivor never moves while its row stands.
    The one exception runs one way only: a **public** copy displaces an incumbent that sits on
    non-public sites alone (ADR-0187). A tenant's sites rarely share a slice, so which site a
    requisition reaches first is close to chance, and ranking alone would leave it wherever it
    landed; ``plan_prune`` ranks the same way, so it drops the non-public row in the same run.
    Within each class — public against public, non-public against non-public — the incumbent
    still wins, so the order is total and one-directional and nothing oscillates.

    A requisition with no incumbent takes :func:`_survivor_board`, the Board ``plan_prune`` would
    keep. A copy on the incumbent's own Board is still added: that is a case-variant spelling of
    it, which ``plan_prune`` settles by the live casing (ADR-0023), and refusing it would make a
    fossil casing immortal. Every group but a Tenant's (:func:`_requisition_tenant`) holds one
    Board, save an Eightfold copy that joined its backing row's group (``copies``, ADR-0210) —
    whose backing row outranks it both ways, so the copy is refused behind a served backing row
    and displaced by an arriving one.
    """
    arriving, _ = _by_group_and_board(new, live, copies)
    if not arriving:
        return set()
    incumbent_boards: dict[tuple[str, str], set[str]] = defaultdict(set)
    for job_id in served:
        placed = _placement(job_id, live, copies)
        if placed is None:
            continue
        group, board = placed
        if group in arriving:
            incumbent_boards[group].add(board)
    refused: set[str] = set()
    for group, by_board in arriving.items():
        incumbents = incumbent_boards.get(group, set())
        # Displaced when the arriving copies' best class outranks the incumbents' best — the same
        # first keys `_survivor_board` sorts on, compared on their own so a class never displaces
        # itself: a backing Board below an Eightfold site, public below non-public, in one
        # direction only.
        displaced = bool(incumbents) and min(map(_survivor_precedence, by_board)) < min(
            map(_survivor_precedence, incumbents)
        )
        keep = (
            {_survivor_board(by_board.keys(), site_jobs)}
            if not incumbents or displaced
            else incumbents
        )
        for board, ids in by_board.items():
            if board not in keep:
                refused.update(ids)
    return refused


def _by_group_and_board(
    job_ids: Iterable[str],
    live: dict[str, str],
    copies: Mapping[str, tuple[str, str]] | None = None,
) -> tuple[dict[tuple[str, str], dict[str, list[str]]], list[str]]:
    """``({duplicate group: {lowercased Board: ids}}, the ids on no live Board)``."""
    grouped: dict[tuple[str, str], dict[str, list[str]]] = defaultdict(
        lambda: defaultdict(list)
    )
    off_board: list[str] = []
    for job_id in job_ids:
        placed = _placement(job_id, live, copies)
        if placed is None:
            off_board.append(job_id)
            continue
        group, board = placed
        grouped[group][board].append(job_id)
    return grouped, off_board


def _quote(value: str) -> str:
    return (
        "'" + value.replace("'", "''") + "'"
    )  # SQL string literal for the delete predicate


def in_predicate(column: str, values: Iterable[str]) -> str:
    """``column IN ('a', 'b')`` over quoted literals, sorted so the predicate is stable.

    Lives here beside :func:`_quote`, which it uses, rather than being rebuilt by each caller —
    the escaping is the part worth having exactly one of.
    """
    return f"{column} IN ({', '.join(_quote(v) for v in sorted(values))})"


def apply_sync(
    table: Any,
    add_rows: list[dict],
    delete_ids: Iterable[str],
    *,
    chunk: int = 2048,
) -> None:
    """Execute a plan on a LanceDB ``table``: delete evicted ids by predicate, then add new rows.

    Deletes are chunked so the ``id IN (...)`` predicate can't grow unbounded. ``add_rows`` must
    already carry the table's schema (vector + metadata); embedding them is the caller's job.

    **The chunk is 2048, not 512, because each call is far more expensive than the predicate it
    carries.** Lance writes one deletion file per *fragment* a delete touches, and the ids in any
    chunk are scattered across every fragment — so a run writes ``ceil(deleted / chunk) x
    fragments`` of them, and the call count is the only half of that this can move. At ~40
    characters per id (``smartrecruiters:Nagarro1:744000144258659``) the predicate grows from
    ~21 KB to ~86 KB: a flat, un-nested ``IN`` list that DataFusion parses once, against a scan of
    every fragment the call was going to make anyway. So fewer, larger calls are also fewer scans.

    Measured over ten runs, evictions are 317-1,560 per run (median 688), which at 512 is 1-4
    delete calls (mean 1.9) and at 2048 is exactly 1 — so **roughly half** the deletion files on
    the eviction path, not a quarter. The larger win is ``index._refresh_metadata``, which already
    batches 2,048 rows at a time and so made four delete calls per batch where it now makes one.

    Either way this only divides a constant. The term that grows is the fragment count, which
    climbs every run and is reset only by ``index compact``. Halving the constant roughly doubles
    the time to the 10,000-file directory ceiling; it does not remove it.
    """
    ids = list(delete_ids)
    for start in range(0, len(ids), chunk):
        batch = ids[start : start + chunk]
        table.delete(in_predicate("id", batch))
    if add_rows:
        table.add(add_rows)


def live_keep_set(ledger_dir: str | Path) -> set[str]:
    """Board keys that should survive: every live ledger Board on an enabled ATS, each key exactly
    as its scraper's ``board_key()`` builds it — the real keys ids carry, which is what makes
    prefix-matching them exact (ADR-0049). ``scrapable_boards.load`` already drops dead Boards and
    ``DISABLED_ATS``; ``min_jobs=0`` keeps Scrapable Boards with no open postings.

    Kept in the ledger's **own casing**, not lowercased: :func:`plan_prune` matches Boards
    case-insensitively but needs the exact live casing to pick which duplicate row to keep."""
    keep: set[str] = set()
    keyless: list[str] = []
    no_board_key = log.FirstOnly(_log)
    for company in scrapable_boards.load(ledger_dir, min_jobs=0):
        try:
            keep.add(board_key(company))
        except Exception:  # noqa: BLE001 - a malformed ledger row shouldn't sink the whole set
            # Not sinking the set is right; doing it silently is not. The Board drops out of the
            # keep-set, so `plan_prune` reads its rows as off-Board and `prune --apply` deletes
            # them under that name — a live Board's postings evicted, and reported as a cause
            # that never happened. Only the exception distinguishes the two, so it is recorded.
            #
            # Bounded (`log.FirstOnly`): a Board failing here is usually one malformed row, but
            # a scraper whose `board_key` starts raising fails on every row it owns, and one
            # annotation per Board would spend the step's whole GitHub budget restating one bug.
            # The first stack says what broke; the summary below says how far it reached.
            keyless.append(f"{company.ats}:{company.slug}")
            no_board_key.report(f"keep-set: no board_key for {keyless[-1]}")
            continue
    if len(keyless) > 1:
        # "Scrapable Board", the term `index prune`'s own keep-set line uses and the one
        # `scrapable_boards.load(min_jobs=0)` actually yields — CONTEXT.md §Counting Boards
        # binds each name to exactly one figure, and "live Boards" names none of them.
        _log.warning(
            f"keep-set: {len(keyless)} Scrapable Board(s) built no board_key and will prune as "
            "off-Board: " + log.named_sample(keyless)
        )
    return keep


def workday_site_jobs(ledger_dir: str | Path) -> dict[str, int]:
    """``{lowercased Workday Board key: its jobs at the last Live probe}`` — what
    :func:`_survivor_board` ranks a Workday tenant's sites by.

    Read from the committed liveness ledger, the same file :func:`live_keep_set` builds the
    keep-set from, so the rule needs no state of its own. Case-variant rows of one Board take the
    larger count. Only the Boards a keep-set holds are ever looked up, so a dead row here is
    harmless, and one whose URL will not parse is already reported by :func:`live_keep_set`.
    """
    from headstart import liveness
    from headstart.scrapers.registry import company_from_row

    jobs: dict[str, int] = {}
    for verdict in liveness.load(Path(ledger_dir) / "workday.csv").values():
        if verdict.status != liveness.LIVE:
            continue
        company = company_from_row("workday", verdict.tenant, verdict.url)
        try:
            board = lower_key(board_key(company))
        except ValueError:
            continue
        jobs[board] = max(jobs.get(board, 0), verdict.jobs or 0)
    return jobs


def aliased_boards(ledger_dir: str | Path) -> dict[str, str]:
    """``{lowercased Board key: signal}`` for every ledger Board an alias ledger buries.

    A buried Board leaves the keep-set, so ``plan_prune`` evicts its rows as off-Board — but its
    canonical Board serves the same postings, so for Trends that is a dedup, not a closure, and
    ``index prune`` records it in the dedup eviction ledger as ``alias:{signal}`` (ADR-0210). The
    rows are matched exactly as ``scrapable_boards.load`` skips them — each liveness row's slug
    against the alias ledger's ``duplicate`` — whatever the row's status, since a buried Board
    that later died still left through the alias.
    """
    from headstart import board_aliases, liveness
    from headstart.scrapers.registry import company_from_row

    out: dict[str, str] = {}
    for ledger in sorted(Path(ledger_dir).glob("*.csv")):
        signals = board_aliases.signals_for(ledger_dir, ledger.stem)
        if not signals:
            continue
        for verdict in liveness.load(ledger).values():
            company = company_from_row(ledger.stem, verdict.tenant, verdict.url)
            signal = signals.get(company.slug.lower())
            if signal:
                try:
                    out[lower_key(board_key(company))] = signal
                except ValueError:
                    continue
    return out


def _live_board_end(job_id: str, live: dict[str, str]) -> int | None:
    """Index of the colon separating a live Board prefix from the native id, or None if the id is
    on no live Board.

    Returns a **position in the original string**, not a length: ``live``'s keys are lowercased and
    ``str.lower()`` is not length-preserving for every character (``'İ'.lower()`` is two chars), so
    slicing the original id by the lowercased key's length would silently eat a character of the
    native id — on the eviction path, that is a live row deleted as someone else's duplicate.

    Longest match wins as defence in depth rather than because anything needs it today: no two live
    Board keys currently nest at a colon, so first-match would give the same answer. (Workday's
    ``co/site`` tenants nest at a *slash*, which is never a candidate position.) Taking the longest
    keeps the answer right if a Board key ever gains a colon.
    """
    best: int | None = None
    for pos, char in enumerate(job_id):
        if char == ":" and lower_key(job_id[:pos]) in live:
            best = pos
    return best


def boards_by_canon(keep: Iterable[str]) -> dict[str, str]:
    """``{canonical (lowercased) Board: the live casing}`` — the lookup both planners match ids
    against.

    The lex-min tie-break is defensive, not the decision: a production ``keep`` already holds one
    casing per Board, because ``live_keep_set`` reads the list ``scrapable_boards.load`` has
    collapsed — the same list the scrape works from, which is *why* the casing prune keeps is the
    casing a scrape emits. It matters only for a caller assembling ``keep`` some other way, where an
    arbitrary set order must not be able to change the plan.
    """
    live: dict[str, str] = {}
    for board in sorted(keep):  # sorted so a caller's set order can't change the plan
        live.setdefault(lower_key(board), board)
    return live


def read_unauthoritative_boards(path: str | Path) -> dict[str, str]:
    """Boards whose scraped list is not authoritative this run — it came back truncated, or the
    scrape raised — lowercased for matching, mapped to *why* (ADR-0053).

    Written by ``scrape_join.write_unauthoritative_boards``; ``index sync`` drops these from the
    eviction scope so a truncated scrape cannot read as a delisting. Lives here rather than beside
    its caller so the scoping invariants stay unit-testable on CI's base-deps-only install.

    Returns the reason, not just the Board, because the exclusion warning is the only place the
    outcome surfaces: without it a Board reads as excluded for an unknown one of two very different
    causes, and telling a 429 apart from a short page needs the log line to say which. Callers that
    only want membership can still use ``in``.

    Fails **open** — an unreadable or wrong-shaped file yields an empty mapping, restoring the old
    infer-from-lines behaviour. Failing the other way would freeze eviction across the whole index
    on a bad file. The shape check is not paranoia: JSON's top level may legally be a list or a
    string, and iterating either yields items that are not Board keys — ``"abc"`` would quietly
    protect Boards ``a``, ``b`` and ``c``, and ``[1, 2]`` would raise on ``.lower()``.
    """
    p = Path(path)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - telemetry must never stop the index updating
        _log.warning(
            f"unreadable {p}: {exc} — no Board is protected from eviction this run"
        )
        return {}
    if not isinstance(data, dict):
        _log.warning(
            f"{p} holds {type(data).__name__}, expected an object of Board -> reason — "
            "no Board is protected from eviction this run"
        )
        return {}
    return {lower_key(str(k)): str(v) for k, v in data.items()}


def resolve_board(job_id: str, live: dict[str, str]) -> str:
    """The live Board that owns ``job_id``, falling back to :func:`board_of`'s guess when none
    matches (an id on a Board the ledger doesn't list — a fresh discovery, or a disabled ATS).

    Both planners resolve through this so they agree. They must: sync scopes eviction by Board and
    prune classifies by Board, and when the two disagreed about a colon-bearing id, a *closed*
    posting became unreachable by both — prune saw it on a live Board and left it, while sync's
    scope only ever held the phantom Board, which no fresh sibling recreates once the req closes.

    Returns the Board in the **id's own casing**, not the ledger's. Sync pairs indexed ids against
    the Boards a scrape emitted, and a scrape emits the live casing, so a fossil-cased row resolves
    to a Board absent from that scope and sync leaves it alone — which is the partial-harvest
    protection :func:`plan_prune` documents and relies on. Canonicalising to ``live[canon]`` here
    would fold fossils onto the live Board and let sync evict them as stale, quietly taking over
    the job prune does under its own keep-set guard.
    """
    end = _live_board_end(job_id, live)
    return job_id[:end] if end is not None else board_of(job_id)


def read_scraped_boards(path: str | Path) -> set[str] | None:
    """The Board set ``scrape_join.write_scraped_boards`` recorded, or ``None`` if it cannot be
    read — the reader half of that writer, as :func:`read_unauthoritative_boards` is of its own.

    ``None`` and an empty set are different answers and the caller acts on both: ``None`` means
    "nothing usable here, derive the scope instead", while ``[]`` is a run whose join covered no
    Board at all and is the honest scope for it.

    Fails **open** — a missing, unreadable or wrong-shaped file reads as ``None``, so the caller
    falls back to deriving the scope and the run keeps its old behaviour. Every one of those warns,
    because a caller only passes a path when it expects this file to be there. The shape check
    matters for the same reason it does in :func:`read_unauthoritative_boards`: JSON's top level
    may legally be a string, and iterating ``"abc"`` would scope eviction to the Boards ``a``,
    ``b`` and ``c`` — every other Board's rows silently out of scope, reported as a normal run.
    """
    path = Path(path)
    if not path.exists():
        _log.warning(
            f"no recorded scope at {path} — deriving the eviction scope instead"
        )
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - a bad file must not stop the index updating
        _log.warning(f"unreadable {path}: {exc} — deriving the eviction scope instead")
        return None
    if not isinstance(data, list) or not all(isinstance(b, str) for b in data):
        _log.warning(
            f"{path} holds {type(data).__name__}, expected a list of Board keys — "
            "deriving the eviction scope instead"
        )
        return None
    return set(data)


def scraped_boards(
    recorded: str | Path | None,
    scraped: str | Path,
    corpus_ids: AbstractSet[str],
    live: dict[str, str],
) -> set[str]:
    """The Boards this run actually scraped — the eviction scope.

    ``live`` is the :func:`boards_by_canon` lookup each id resolves through, so this scope lands in
    the same key space :func:`plan_sync` classifies indexed rows into (ADR-0049).

    A Board here but absent from the tech corpus was scraped and simply has no tech jobs now, so
    its stale tech rows are correctly evicted. That is why the scope has to come from the *full*
    scrape and not the tech subset — and it is the whole reason ``data/jobs/{ats}.jsonl`` used to
    ride the corpus-state artifact into the merge job, ~9 GB of job text read for a set of a few
    thousand strings. ``scrape_join`` now derives that set where the records already are and
    records it (ADR-0161), so three sources can answer the same question, tried in this order:

    1. ``scraped`` — the full-scrape ``{ats}.jsonl`` dir itself (a non-recursive glob, so the
       ``tech/`` subdir is not double-counted). The definition, so it goes first: a run with a
       real scrape on disk must never be scoped by a summary of some *other* scrape.
    2. ``recorded`` — the Board keys ``scrape_join`` wrote from the same full scrape. What the
       pipeline uses, and **only when the caller passes a path**. It is not defaulted, because
       this file rides ``data/state`` through the HF dataset: a local ``index sync`` with no full
       scrape on disk would otherwise find the last pipeline run's ~14,700 Boards sitting on disk
       and scope eviction on them instead of on the corpus it was handed.
    3. the corpus ids' Boards, when neither is available (a local sync with no full scrape on
       disk, or a unit test).

    (A Board scraped cleanly that yields *zero* jobs writes no ids, so only ``recorded`` covers
    it: ``scrape_join`` adds the shard reports' ``boards_ok``. The other two sources miss it, and
    prune does not catch it either, because a live Board with no postings stays in its keep-set.)
    """
    path = Path(scraped)
    if path.is_dir() and any(path.glob("*.jsonl")):
        return {resolve_board(job["id"], live) for job in iter_jobs(path)}
    if recorded is not None:
        from_join = read_scraped_boards(recorded)
        if from_join is not None:
            return from_join
    return {resolve_board(job_id, live) for job_id in corpus_ids}


#: The ATSes whose native id is a requisition id every Board of one **Tenant** shares, so a
#: requisition is served once per Tenant rather than once per Board: Workday's sites (ADR-0187),
#: and Taleo Enterprise's career sections, Taleo Business Edition's career sites and ADP Workforce
#: Now's career centers (ADR-0223). Measured on each before it was listed: the same id on two
#: Boards of one Tenant is the same posting.
_TENANT_REQUISITION_ATSES = frozenset(
    {"workday", "taleo_enterprise", "taleo_be", "adp"}
)


def _requisition_tenant(canon: str, native: str) -> str | None:
    """``{ats}:{tenant}`` — the Tenant a row's requisition belongs to — or None.

    A Board is one site of its Tenant (Workday's ``workday:{company}/{site}``, a Taleo career
    section, an ADP career center), and on the ATSes in :data:`_TENANT_REQUISITION_ATSES` a Tenant
    posts one requisition to several of its Boards under the same native id, so the requisition's
    identity is the Tenant plus that id, not the Board plus it. The Tenant is
    :func:`~headstart.board_identity.tenant`'s: Workday's ``{company}``, a Taleo Enterprise host,
    a Taleo Business Edition ``org``, an ADP ``cid``. None for every other ATS, and for a native
    id with no digit in it: that is a fallback id (Workday's ``Texas``, a title slug), not a
    requisition id, and two Boards sharing one says nothing about sharing a posting. The key never
    equals a real Board key: it drops the site part, and a Taleo key keeps no URL scheme.
    """
    ats = ats_of(canon)
    if ats not in _TENANT_REQUISITION_ATSES or not any(ch.isdigit() for ch in native):
        return None
    return f"{ats}:{tenant(canon)}"


#: Substrings of a Board's site segment (case-insensitive) that mark a site as not meant for the
#: public — a Workday site, or a Taleo Enterprise career section (ADR-0223). Measured on served
#: v654, where a confidential executive-recruiting site outranked its public sibling on ledger
#: jobs and kept 737 of GE Vernova's requisitions (ADR-0187). This only orders the choice of
#: which copy stays; it never decides whether a requisition is served, so one found only on
#: non-public sites still survives on one of them.
_NON_PUBLIC_SITE_TOKENS = (
    "hidden",
    "confidential",
    "internal",
    "private",
    "sourcer",
    "targeted",
)


def _survivor_board(boards: AbstractSet[str], site_jobs: dict[str, int]) -> str:
    """The one Board a requisition is served from when no incumbent decides it: a public site
    before a non-public one (:data:`_NON_PUBLIC_SITE_TOKENS`), then the Board with the most jobs
    in the liveness ledger, then the lexicographically smallest (lowercased) key.

    The one place the ranking is defined, so ``plan_sync`` admitting a new copy and
    ``plan_prune`` collapsing existing ones can never disagree about which Board keeps it; the
    displacement in :func:`_other_site_copies` compares this ranking's first key only.
    """
    return min(
        boards,
        key=lambda board: (
            *_survivor_precedence(board),
            -site_jobs.get(board, 0),
            board,
        ),
    )


def _survivor_precedence(board: str) -> tuple[bool, bool]:
    """``(an Eightfold career site, a non-public site)`` for a lowercased Board key —
    :func:`_survivor_board`'s first keys, and all a displacement compares. A backing Board
    outranks the Eightfold site in front of it (ADR-0210), and a public site a non-public one
    (ADR-0187, ADR-0223); a group holding one Board never reaches either."""
    return board.startswith("eightfold:"), _is_non_public(board)


def _is_non_public(board: str) -> bool:
    """Whether a lowercased Board key's site segment (after the first ``/``) names it non-public
    (:data:`_NON_PUBLIC_SITE_TOKENS`). Both callers pass the lowercased key :func:`_placement`
    builds, which is what makes the match case-insensitive. A Taleo Enterprise key's segment is
    ``/{host}/careersection/{section}``, and its internal sections (``mp_internal``) rank after
    the public ones (ADR-0223); the host part is shared by every Board of the Tenant, so it never
    decides between them. A Taleo Business Edition ``cws`` and an ADP ``ccId`` are numbers, which
    name nothing. Any other ATS's groups hold one Board, so the answer never decides anything
    there."""
    site = board.partition("/")[2]
    return any(token in site for token in _NON_PUBLIC_SITE_TOKENS)


#: Which rule took a duplicate row out, as :func:`plan_prune` names it and the dedup
#: eviction ledger records it (ADR-0210); :func:`alias_rules` adds ``alias:{signal}``. One
#: Tenant rule covers every ATS in :data:`_TENANT_REQUISITION_ATSES`, but Workday's removals keep
#: the name they had before ADR-0223, so ``tenant-requisition`` is Taleo's and ADP's alone.
CASE_VARIANT = "case-variant"
WORKDAY_TENANT = "workday-tenant"
TENANT_REQUISITION = "tenant-requisition"
BACKING_REQUISITION = "backing-requisition"


def alias_rules(
    off_board: Iterable[str], live: dict[str, str], aliased: Mapping[str, str]
) -> dict[str, str]:
    """``{off-Board id: "alias:{signal}"}`` for each one whose Board an alias ledger buries
    (``aliased`` is :func:`aliased_boards`). Its canonical Board serves the same posting, so the
    removal is a dedup for Trends; any other off-Board row is not, and is left out (ADR-0210)."""
    rules: dict[str, str] = {}
    for job_id in off_board:
        signal = aliased.get(lower_key(resolve_board(job_id, live)))
        if signal:
            rules[job_id] = f"alias:{signal}"
    return rules


def plan_prune(
    index_ids: Iterable[str],
    keep: set[str],
    *,
    site_jobs: dict[str, int] | None = None,
    requisitions: Mapping[str, str] | None = None,
    backing: Mapping[str, Iterable[str]] | None = None,
) -> tuple[list[str], dict[str, str]]:
    """Split index ids into ``(evict_off_board, {evict_duplicate: the rule that evicts it})``.

    The rule is :data:`CASE_VARIANT` for a row another casing of its own Board keeps,
    :data:`WORKDAY_TENANT` for one another site of its Workday tenant keeps (ADR-0187),
    :data:`TENANT_REQUISITION` for one another Board of its Taleo or ADP Tenant keeps (ADR-0223),
    and :data:`BACKING_REQUISITION` for an Eightfold copy its backing Board keeps (ADR-0210).

    A change to what counts as a duplicate here bumps :data:`DEDUP_VERSION` (ADR-0188).

    ``evict_off_board``: Board not in ``keep`` (dead / dropped from the ledger / disabled ATS).
    ``evict_duplicate``: among the survivors, every id but one per ``(lowercased Board, native id)``
    group — the case-variant dupes of one job — except that a Workday requisition is grouped on
    its **Workday tenant** rather than its Board, because a Workday tenant posts one requisition to
    several of its sites, each a Board, under the same native id (ADR-0187); Taleo Enterprise,
    Taleo Business Edition and ADP Workforce Now requisitions are grouped on their **Tenant** the
    same way (:func:`_requisition_tenant`, ADR-0223). Such a group keeps one Board, the
    :func:`_survivor_board` (``site_jobs`` is :func:`workday_site_jobs`, so a Taleo or ADP
    Tenant's Boards tie on jobs and the key decides; omitted, every Board ties on jobs).
    ``plan_sync`` refuses copies on the same rule, so once today's duplicates are gone this sees
    two Boards for one requisition only when a public copy has just displaced a non-public
    incumbent — and it drops the incumbent, because the ranking puts public first. The casing
    rule below then picks the row within the kept Board. An Eightfold row whose ``requisitions``
    stamp a row on one of its ``backing`` Boards carries joins that row's group and loses to it
    (:func:`_backing_copies`, ADR-0210).

    The row kept is the one whose Board casing the **live ledger** produces, because that is the
    casing a future scrape emits. Keeping the lexicographically-smallest instead (the rule until
    2026-08-11) preserved whichever casing happened to sort first, which is often a *fossil* — a row
    under a casing nothing scrapes any more. Sync cannot evict a fossil (its Board is absent from
    ``scraped_boards``, so the partial-harvest guard protects it), so the fresh row was deleted as
    the fossil's duplicate on every run, forever, while the fossil itself went stale and immortal.
    Falls back to lex-min when no row carries the live casing (the group is all fossils).

    Ids are matched against ``keep`` by **prefix**, not by parsing (ADR-0049): a composite key is
    ``{ats}:{slug}:{native}`` and *both* the slug and the native id can contain ``:``, so splitting
    on the last colon attributes some rows to a Board that does not exist. ``keep`` is the set of
    real Boards, so the longest member that prefixes an id is the answer — unambiguous against
    every Board key in use today, with longest-match as the documented tie-break should one Board
    key ever nest inside another at a colon."""
    live = boards_by_canon(keep)
    index_ids = list(index_ids)
    copies = _backing_copies(index_ids, live, requisitions or {}, backing or {})
    groups, off_board = _by_group_and_board(index_ids, live, copies)
    duplicate: dict[str, str] = {}
    for by_board in groups.values():
        kept_board = _survivor_board(by_board.keys(), site_jobs or {})
        for canon, ids in by_board.items():
            if canon != kept_board:
                for i in ids:
                    duplicate[i] = (
                        BACKING_REQUISITION
                        if i in copies
                        else WORKDAY_TENANT
                        if ats_of(canon) == "workday"
                        else TENANT_REQUISITION
                    )
            elif len(ids) > 1:
                kept = next(
                    (i for i in ids if i.startswith(live[canon] + ":")), min(ids)
                )
                duplicate.update((i, CASE_VARIANT) for i in ids if i != kept)
    return off_board, duplicate
