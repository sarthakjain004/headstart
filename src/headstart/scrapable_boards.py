"""The Scrapable Board list: every Board a run may pick, each resolved to its identity once
(ADR-0191).

"Is Board X scraped?" has one answer and it lives here. :func:`load` reads the liveness ledger
(ADR-0012) and applies every reason a Board is not scraped, in the order the answer depends on:

1. an ATS with no registered scraper (warned) or in ``registry.DISABLED_ATS``;
2. a vendor test Board in ``excluded_and_parked.EXCLUDED_BOARDS`` (:func:`is_excluded`, on the lowercased
   ``ats:slug``);
3. a Board buried as another's duplicate in the alias ledger (ADR-0111, on the lowercased slug);
4. the election (:func:`_elect`, ADR-0023 as amended by ADR-0219): rows naming one Board, compared
   case-insensitively, collapse to one; a Board with a ``dead`` row newer than its newest ``live`` row drops out;
5. under ``min_jobs``, on the elected row's count;
6. a Board in ``excluded_and_parked.PARKED_BOARDS``, on the lowercased identity the election collapsed on.

Steps 2 and 3 run before the election and step 6 after it, and the difference matters: the first
two are keyed on the slug, so they must see every spelling of a Board, and the park is keyed on the
identity, so it must see the one survivor. Moving the park before the election would drop one row
and promote another spelling of the same Board to survivor. Step 5 runs after the election for the
same reason: filtering rows first let the Hiring list elect a different row than the Scrapable one.

Each :class:`ScrapableBoard` carries its identity (``board_identity``, ADR-0155's lenient form)
and that identity lowercased, both computed when the Board is built. Before this module every
consumer re-derived them: ``scrape_plan`` called ``board_identity`` about eight times per Board
across the quarantine, the value gate, ``pick_boards`` and the shard sort.

Two dedupes run here and they catch different things: the alias ledger is *semantic* (one company,
two hostnames, no shared key to collapse on) and :func:`_elect` is *syntactic* (one
hostname, two spellings). Neither subsumes the other.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from headstart import board_aliases, excluded_and_parked, liveness, log
from headstart.board_identity import board_identity, board_key, lower_key
from headstart.config import CompanyRef
from headstart.scrapers.registry import DISABLED_ATS, SCRAPERS, company_from_row

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


#: One ledger row read as the Board it names: the unit :func:`_elect` groups and chooses among.
Row = tuple[ScrapableBoard, liveness.Verdict]


def _verdict_of(row: Row) -> liveness.Verdict:
    _board, verdict = row
    return verdict


def is_excluded(ats: str, slug: str) -> bool:
    """Is this a vendor test Board in ``excluded_and_parked.EXCLUDED_BOARDS``?

    Matched on the lowercased ``ats:slug``, so one entry covers every casing the ledger carries.
    The one predicate for a script that filters raw candidates the way :func:`load` does.
    """
    return f"{ats}:{slug}".lower() in excluded_and_parked.EXCLUDED_BOARDS


def load(ledger_dir: str | Path, *, min_jobs: int = 1) -> list[ScrapableBoard]:
    """The Scrapable Boards in the liveness ledger at ``ledger_dir`` (the module docstring has the
    rules), keeping only those whose elected row last counted at least ``min_jobs`` postings.

    ``min_jobs=0`` is CONTEXT.md's **Scrapable Board** count and what the pipeline reads;
    the default ``min_jobs=1`` is the **Hiring Board** subset. Each scraper turns a ledger row's
    ``(tenant, url)`` into its own slug via ``slug_from`` (``registry.company_from_row``,
    ADR-0203), so no per-ATS logic lives here.
    ``config/companies.toml`` remains the small curated seed.
    """
    ledger_dir = Path(ledger_dir)
    rows: list[Row] = []
    read = excluded = buried = unparseable = 0
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
        # here beside EXCLUDED_BOARDS because both are keyed on the slug; the *syntactic* election
        # below cannot do it, since two different hostnames share no `board_key` to collapse on.
        aliases = board_aliases.load_for(ledger_dir, scraper.ats)
        for verdict in liveness.load(csv_path).values():
            read += 1
            company = company_from_row(scraper.ats, verdict.tenant, verdict.url)
            if is_excluded(company.ats, company.slug):
                excluded += 1
                continue
            if company.slug.lower() in aliases:
                buried += 1
                continue
            board = _board_of_row(company, verdict)
            if board is None:
                unparseable += 1
            else:
                rows.append((board, verdict))
    winners = _elect(rows)
    elected = [board for board, verdict in winners if (verdict.jobs or 0) >= min_jobs]
    scrapable = _drop_parked(elected)
    # Every reason a row did not become a Board, in the module docstring's order, so a list
    # that shrank says which rule took it. `_elect`'s two outcomes are told apart by the group
    # count: rows beyond one per identity collapsed, groups beyond the winners had no live row
    # or a newer dead one.
    groups = len({board.lowercase_identity for board, _ in rows})
    _log.info(
        f"scrapable boards: {len(scrapable)} from {read} ledger rows — "
        f"excluded {excluded}, alias-buried {buried}, unparseable non-live {unparseable}, "
        f"collapsed {len(rows) - groups}, no live or newer dead {groups - len(winners)}, "
        f"under min_jobs={min_jobs} {len(winners) - len(elected)}, "
        f"parked {len(elected) - len(scrapable)}"
    )
    return scrapable


def _drop_parked(boards: list[ScrapableBoard]) -> list[ScrapableBoard]:
    """Drop ``excluded_and_parked.PARKED_BOARDS``, matched on the same identity :func:`_elect` collapses on
    so the two can never disagree about which Board an entry names."""
    return [b for b in boards if b.lowercase_identity not in excluded_and_parked.PARKED_BOARDS]


def _board_of_row(
    company: CompanyRef, verdict: liveness.Verdict
) -> ScrapableBoard | None:
    """The Board a ledger row names, or None for a row that is not ``live`` and whose slug its
    scraper cannot parse.

    Every row takes part in the election, not only the live ones, because a newer ``dead`` row is
    what takes a Board out. A live row that will not parse keeps ADR-0155's fallback identity, as
    before. A dead or unknown one is skipped: it names no Board, so it cannot overrule one, and
    Workday's ledger holds hundreds of dead rows with no url, each of which would otherwise reach
    the fallback's warning on every load."""
    if verdict.status != liveness.LIVE:
        try:
            board_key(company)  # only asks whether the slug parses
        except Exception:  # noqa: BLE001 - an unparseable non-live row names no Board
            return None
    return ScrapableBoard(ats=company.ats, slug=company.slug, name=company.name)


def _elect(
    rows: list[Row],
) -> list[Row]:
    """One representative row per Board, for the Boards whose newest verdict is live (ADR-0219,
    amending ADR-0023).

    The ledger holds several rows for one Board: casing variants (Workday ``.../External`` vs
    ``.../external``), a display slug beside a careers URL, or one site on two data centres. They
    group on the lowercased identity, and three questions are answered separately:

    - **Is the Board scraped?** Only if no ``dead`` row is newer than its newest ``live`` row.
      ``unknown`` rows never count: a probe that earned no verdict is no evidence. A ``dead`` row
      on the *same* day as a ``live`` one does not take the Board out: re-probed 2026-09-25, all
      45 such groups answered live.
    - **Under which key?** The lexicographically-smallest identity among its live rows, the rule
      ADR-0023 has always used. It is the casing every served id already carries, so changing it
      would re-key them.
    - **From which row?** The newest live row carrying that key, and on a same-day tie the one
      the ledger lists first, as before: a probe date is the only evidence of recency. Its slug
      is what the scraper fetches and its job count is what ``min_jobs`` reads, so the Scrapable
      and the Hiring lists elect the same row.
    """
    groups: dict[str, list[Row]] = {}
    for board, verdict in rows:
        groups.setdefault(board.lowercase_identity, []).append((board, verdict))
    elected = []
    for group in groups.values():
        live = [(b, v) for b, v in group if v.status == liveness.LIVE]
        if not live:
            continue
        newest_live = max(v.checked_at for _, v in live)
        if any(
            v.status == liveness.DEAD and v.checked_at > newest_live for _, v in group
        ):
            continue
        key = min(b.identity for b, _ in live)
        carriers = [(b, v) for b, v in live if b.identity == key]
        # `max` keeps the first of equal dates, and `rows` is in ledger order.
        elected.append(max(carriers, key=lambda row: _verdict_of(row).checked_at))
    return elected
