"""The Scrapable Board list: every Board a run may pick, each resolved to its identity once
(ADR-0191).

"Is Board X scraped?" has one answer and it lives here. :func:`load` reads the liveness ledger
(ADR-0012) and applies every reason a live row is not scraped, in the order the answer depends on:

1. an ATS with no registered scraper (warned) or in ``registry.DISABLED_ATS``;
2. a row not ``live``, or under ``min_jobs``;
3. a vendor test Board in ``config.EXCLUDED_BOARDS`` (:func:`is_excluded`, on the lowercased
   ``ats:slug``);
4. a Board buried as another's duplicate in the alias ledger (ADR-0111, on the lowercased slug);
5. the ADR-0023 dedupe: rows naming one Board collapse to the lexicographically-smallest
   identity, compared case-insensitively;
6. a Board in ``config.PARKED_BOARDS``, on the lowercased identity the dedupe collapsed on.

Steps 3 and 4 run before the dedupe and step 6 after it, and the difference matters: the first two
are keyed on the slug, so they must see every spelling of a Board, and the park is keyed on the
identity, so it must see the one survivor. Moving the park before the dedupe would drop one row and
promote another spelling of the same Board to survivor.

Each :class:`ScrapableBoard` carries its identity (``board_identity``, ADR-0155's lenient form)
and that identity lowercased, both computed when the Board is built. Before this module every
consumer re-derived them: ``scrape_plan`` called ``board_identity`` about eight times per Board
across the quarantine, the value gate, ``pick_boards`` and the shard sort.

Two dedupes run here and they catch different things: the alias ledger is *semantic* (one company,
two hostnames, no shared key to collapse on) and :func:`_dedupe_boards` is *syntactic* (one
hostname, two spellings). Neither subsumes the other.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from headstart import board_aliases, config, liveness, log
from headstart.board_identity import board_identity, lower_key
from headstart.config import CompanyRef
from headstart.scrapers.registry import DISABLED_ATS, SCRAPERS

_log = log.get(__name__)


@dataclass(frozen=True, slots=True)
class ScrapableBoard(CompanyRef):
    """A :class:`~headstart.config.CompanyRef` resolved to its Board identity.

    A subclass, so it goes wherever a ``CompanyRef`` does (``get_scraper``, ``harvest``, the shard
    files). Both identity fields are computed from ``ats`` and ``slug`` on construction and cannot
    be passed in, so they always agree with the reference they describe.
    """

    #: ``board_identity(self)``: the Board's ``board_key`` in the casing its scraper builds, or the
    #: plain ``ats:slug`` when the slug will not parse (ADR-0155). The per-Board state ledgers are
    #: keyed on this.
    identity: str = field(init=False)
    #: ``identity`` lowercased: the form the dedupe, the park and every case-insensitive ledger
    #: lookup compare on (ADR-0049).
    lowercase_identity: str = field(init=False)

    def __post_init__(self) -> None:
        identity = board_identity(self)
        object.__setattr__(self, "identity", identity)
        object.__setattr__(self, "lowercase_identity", lower_key(identity))


def is_excluded(ats: str, slug: str) -> bool:
    """Is this a vendor test Board in ``config.EXCLUDED_BOARDS``?

    Matched on the lowercased ``ats:slug``, so one entry covers every casing the ledger carries.
    The one predicate for a script that filters raw candidates the way :func:`load` does.
    """
    return f"{ats}:{slug}".lower() in config.EXCLUDED_BOARDS


def load(ledger_dir: str | Path, *, min_jobs: int = 1) -> list[ScrapableBoard]:
    """The Scrapable Boards in the liveness ledger at ``ledger_dir`` (the module docstring has the
    rules), keeping only those whose last probe counted at least ``min_jobs`` postings.

    ``min_jobs=0`` is CONTEXT.md's **Scrapable Board** count and what the pipeline reads;
    the default ``min_jobs=1`` is the **Hiring Board** subset. Each scraper turns a ledger row's
    ``(tenant, url)`` into its own slug via ``slug_from``, so no per-ATS logic lives here.
    ``config/companies.toml`` remains the small curated seed.
    """
    ledger_dir = Path(ledger_dir)
    boards: list[ScrapableBoard] = []
    for csv_path in sorted(ledger_dir.glob("*.csv")):
        scraper = SCRAPERS.get(csv_path.stem)
        if scraper is None:
            # A whole ledger file dropped, silently, because its stem is not a registered ATS.
            # That is how a scraper renamed without its ledger, or a ledger landed under a
            # misspelt name, takes every one of its Boards out of the run while the run reports
            # nothing unusual — the ATS simply stops appearing in the totals.
            _log.warning(
                f"{csv_path.name}: no scraper registered for '{csv_path.stem}' — "
                "every Board in this ledger is skipped"
            )
            continue
        if scraper.ats in DISABLED_ATS:
            continue  # deliberate (registry.DISABLED_ATS), so not worth a line
        # Boards this ATS publishes twice, buried in favour of their canonical (ADR-0111). Dropped
        # here beside EXCLUDED_BOARDS because both are keyed on the slug; the *syntactic* dedupe
        # below cannot do it, since two different hostnames share no `board_key` to collapse on.
        aliases = board_aliases.load_for(ledger_dir, scraper.ats)
        for verdict in liveness.load(csv_path).values():
            if verdict.status != liveness.LIVE or (verdict.jobs or 0) < min_jobs:
                continue
            slug = scraper.slug_from(verdict.tenant, verdict.url)
            if is_excluded(scraper.ats, slug) or slug.lower() in aliases:
                continue
            boards.append(
                ScrapableBoard(ats=scraper.ats, slug=slug, name=verdict.tenant)
            )
    return _drop_parked(_dedupe_boards(boards))


def _drop_parked(boards: list[ScrapableBoard]) -> list[ScrapableBoard]:
    """Drop ``config.PARKED_BOARDS``, matched on the same identity :func:`_dedupe_boards`
    collapses on so the two can never disagree about which Board an entry names."""
    return [b for b in boards if b.lowercase_identity not in config.PARKED_BOARDS]


def _dedupe_boards(boards: list[ScrapableBoard]) -> list[ScrapableBoard]:
    """Collapse Boards that map to the same canonical key to one entry (ADR-0023).

    The ledger holds duplicate rows for one Board — differing only by slug casing (Workday sites
    ``.../External`` vs ``.../external``) or by an equivalent tenant/url form that resolves to the
    same ``board_key``. Left in, each variant is scraped and indexed separately, so one job lands in
    the index two or three times. Keep the lexicographically-smallest ``board_key`` per canonical
    (lowercased) key — this picks the Board that is actually scraped, and ``index_plan.plan_prune``
    keeps the index row carrying *that* casing, so scrape and index agree. (Until 2026-08-11 the
    prune instead kept the lex-min casing *present in the index*, which is a different population —
    it includes casings that left the ledger — and the two disagreed permanently: ADR-0023's
    amendment.)"""
    best: dict[str, ScrapableBoard] = {}
    for board in boards:
        current = best.get(board.lowercase_identity)
        if current is None or board.identity < current.identity:
            best[board.lowercase_identity] = board
    return list(best.values())
