"""One `hiring_now` answer: the Space's Hot ranking (`trends.hot_ranking`), one Lens of it.

The Space ranks every Company directory entry once at boot, over the trailing week, on four
Lenses; this answer lists one of them, with the site's numbers. The Operators the Hiring now tab
hides by default (staffing firms and job boards, ADR-0238) are the ones `/hot` names in
``hidden_by_default``, so the page and this answer read one list; what was left out, and why, is
said rather than silently applied.

The default Lens is `opened_less_closed` (ADR-0321): postings opened less postings closed, for
companies whose closures were counted on every Board, so its figure holds no re-counting. A row's
``net`` is its trend's "hiring": the change in openings less the counting steps the reading could
size, so re-counting it could not size stays in (ADR-0272). Bosch Group led Expansion at net +442
with 23 postings opened and 33 closed. So each row states opened less closed beside its net, and
the site's three older Lenses flag every artifact that questions what they rank by: a net more
than its postings opened and closed could make, closures that went uncounted on all or some of
its Boards, more postings opened than are open now, and on Rate a base too small to read.
Opened less closed ranks by a figure none of those questions (`LENSES`). Every Lens flags a
row whose operator is unverified (ADR-0335): an employer only because no curated list names
it, while its name reads like a staffing firm's, as Vrinda International's did at #3. Every
flagged row `/hot` serves is listed after every unflagged one, each group in the site's order,
before the answer is cut to its `limit`, and every row then gives its place on the page.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, NamedTuple

from headstart.space_mcp import scraped_text
from headstart.space_mcp.space_client import SpaceClient, SpaceRoute
from headstart.space_mcp.space_tool import SpaceTool
from headstart.space_mcp.turnover_span import span_sentence

#: A company name past this is cut, as search cuts one.
COMPANY_FIELD = 60

#: What precedes each flag on a row: the eval reads it to tell a row the tool disowns (ADR-0325).
FLAG_MARK = " · FLAG "

#: A Rate row whose company has fewer openings than this many times the ranking's floor is
#: flagged: at 25 openings each posting opened moves its rate 4 points. RadNet read 48% on
#: 2026-09-29: 12 postings opened on 25 openings.
SMALL_BASE_FLOORS = 2


class Flag(StrEnum):
    """An artifact a row can carry, as a row names it."""

    NET_NOT_BACKED = "net not backed by postings opened: mostly re-counting, not hiring"
    SMALL_BASE = "small base"
    OPENED_OVER_OPEN_NOW = "more postings opened than are open now"
    # A few words a row; the line under the rows says what each means.
    CLOSURES_UNCOUNTED = "closures not counted"
    CLOSURES_PARTLY_UNCOUNTED = "closures counted on only some Boards"
    OPERATOR_UNVERIFIED = "operator unverified"


#: What the line under the rows says of each flag, after "N of these rows".
_FLAG_SUMMARY = {
    Flag.NET_NOT_BACKED: (
        "have a net their postings opened and closed could not make, even at their "
        "pace over the whole window: that net is re-counting HeadStart could not size, not "
        "hiring, so report their opened and closed instead."
    ),
    Flag.CLOSURES_UNCOUNTED: (
        "had their closures go uncounted: their postings opened may be the same postings "
        "listed again, so read them beside open now."
    ),
    Flag.CLOSURES_PARTLY_UNCOUNTED: (
        "had their closures counted on only some of their Boards, so their closed runs low."
    ),
    Flag.SMALL_BASE: (
        "rank on a small base, where a few postings move the rate far: weigh them by their "
        "postings opened, not the percentage."
    ),
    Flag.OPERATOR_UNVERIFIED: (
        "are named like a staffing firm or recruiter, and HeadStart has not checked who posts "
        "for them: 'employer' is only its default, so do not report them as employers hiring."
    ),
}

#: Why `/hot` left a company out of a Lens, by the count it gives, after "N".
_LEFT_OUT = {
    "closures_uncounted": (
        "whose closures were not counted, so their postings opened may be the same postings "
        "listed again"
    ),
    "closures_partly_uncounted": (
        "whose closures went uncounted on some of their Boards, so their closed runs low"
    ),
    "not_growing": (
        "whose net change was 0 or less, so what they opened only replaced what closed"
    ),
}


@dataclass(frozen=True)
class Lens:
    """What one Lens ranks, and how its answer treats its rows."""

    #: What it ranks, as the header says it.
    ranks: str
    #: One of the site's older Lenses, which rank by a figure that can hold re-counting.
    in_site_order: bool
    #: The flags that question the figure it ranks by, or who posts for the row.
    checks: tuple[Flag, ...]
    #: `/hot`'s counts of companies it leaves out beyond every Lens's (`_LEFT_OUT`).
    left_out: tuple[str, ...] = ()


_SITE_CHECKS = (
    Flag.NET_NOT_BACKED,
    Flag.OPENED_OVER_OPEN_NOW,
    Flag.CLOSURES_UNCOUNTED,
    Flag.CLOSURES_PARTLY_UNCOUNTED,
    Flag.OPERATOR_UNVERIFIED,
)

#: The Lens answered unless another is asked for: the one whose figure holds no re-counting.
DEFAULT_LENS = "opened_less_closed"

#: Every Lens `/hot` serves.
LENSES = {
    DEFAULT_LENS: Lens(
        ranks=(
            "postings opened less postings closed, only for companies whose closures were "
            "counted on every Board, largest first"
        ),
        in_site_order=False,
        # Its figure holds no re-counting, but who posts for a row is a question on every Lens.
        checks=(Flag.OPERATOR_UNVERIFIED,),
        left_out=("closures_uncounted", "closures_partly_uncounted"),
    ),
    "expansion": Lens(
        ranks=(
            "the site's net change in tech openings, less the counting steps it could size; "
            "re-counting it could not size stays in, so read it beside opened and closed"
        ),
        in_site_order=True,
        checks=_SITE_CHECKS,
    ),
    "volume": Lens(ranks="postings opened", in_site_order=True, checks=_SITE_CHECKS),
    "rate": Lens(
        ranks="postings opened as a share of the company's openings now",
        in_site_order=True,
        checks=(*_SITE_CHECKS, Flag.SMALL_BASE),
        left_out=("closures_uncounted", "not_growing"),
    ),
}


class ListedRow(NamedTuple):
    """One row as the answer lists it."""

    page_place: int
    row: dict[str, Any]
    flags: tuple[Flag, ...]


@dataclass(frozen=True)
class _Listing:
    """The rows an answer lists, in its order, and what the page hides on this Lens."""

    rows: list[ListedRow]
    hidden: int
    #: Whether a flag moved any row from its place on the page.
    moved: bool


def _change(value: int | None) -> str:
    return "not counted" if value is None else f"{value:+,}"


def _count(value: int | None) -> str:
    return "not counted" if value is None else f"{value:,}"


def _days(start: str, end: str) -> float:
    return (
        datetime.fromisoformat(end) - datetime.fromisoformat(start)
    ).total_seconds() / 86400


def _pace(window: dict[str, Any]) -> float:
    """How many times the span opened and closed were counted over the window's net covers: the
    net runs from the window's base, opened and closed only from ``turnover_from`` when that is
    later (ADR-0227)."""
    base, to, began = window.get("base"), window.get("to"), window.get("turnover_from")
    if not (base and to and began) or began <= base:
        return 1.0
    counted = _days(began, to)
    return _days(base, to) / counted if counted > 0 else 1.0


def _net_not_backed(row: dict[str, Any], pace: float) -> bool:
    """Whether the row's net is more than its postings opened and closed could make, sign by
    sign, even at their pace over the whole window: a gain needs postings opened, a loss needs
    postings closed, and a net against the sign of opened less closed, larger than that pace
    could turn round, is re-counting. AgileEngine read net +100 on 306 opened and 321 closed. A
    shrinking net whose closures went uncounted cannot be judged."""
    net, opened, closed = row.get("net"), row.get("opened"), row.get("closed")
    if not net or opened is None:
        return False
    if net > opened * pace:
        return True
    if closed is None:
        return False
    if -net > closed * pace:
        return True
    turnover_net = opened - closed
    return net * turnover_net < 0 and abs(net) > abs(turnover_net) * pace


def _flags(
    row: dict[str, Any], lens: Lens, pace: float, min_stock: int
) -> tuple[Flag, ...]:
    """The flags among ``lens.checks`` that ``row`` carries (ADR-0321)."""
    stock, opened = row.get("stock") or 0, row.get("opened")
    carried = {
        Flag.NET_NOT_BACKED: _net_not_backed(row, pace),
        Flag.SMALL_BASE: bool(stock) and stock < SMALL_BASE_FLOORS * min_stock,
        Flag.OPENED_OVER_OPEN_NOW: bool(stock) and (opened or 0) > stock,
        Flag.CLOSURES_UNCOUNTED: bool(opened) and row.get("closed") is None,
        Flag.CLOSURES_PARTLY_UNCOUNTED: row.get("closed") is not None
        and bool(row.get("closures_uncounted_boards")),
        Flag.OPERATOR_UNVERIFIED: bool(row.get("operator_unverified")),
    }
    return tuple(flag for flag in lens.checks if carried[flag])


def _said(flag: Flag, row: dict[str, Any]) -> str:
    if flag is Flag.SMALL_BASE:
        stock = row["stock"]
        return (
            f"small base: at {stock:,} openings each posting opened moves the rate "
            f"{100 / stock:.0f} points"
        )
    return flag.value


def _listing(hot: dict[str, Any], lens: str, limit: int, show_hidden: bool) -> _Listing:
    """The rows the page shows on ``lens``, with their flags, the first ``limit`` of them. Every
    flagged row `/hot` serves goes after every unflagged one before the cut, so a small ``limit``
    still leads with real rows; sorting is stable, so each group keeps the site's order."""
    hidden = set(hot.get("hidden_by_default") or ())
    ranked = hot.get("lenses", {}).get(lens) or []
    rows = [r for r in ranked if show_hidden or r.get("operator") not in hidden]
    pace = _pace(hot.get("window") or {})
    min_stock = (hot.get("counts") or {}).get("min_stock", 25)
    listed = [
        ListedRow(place, row, _flags(row, LENSES[lens], pace, min_stock))
        for place, row in enumerate(rows, start=1)
    ]
    listed.sort(key=lambda listed_row: bool(listed_row.flags))
    shown = listed[:limit]
    moved = [r.page_place for r in shown] != list(range(1, len(shown) + 1))
    return _Listing(shown, len(ranked) - len(rows), moved)


def _row(rank: int, listed: ListedRow, moved: bool) -> str:
    row = listed.row
    rate = "not counted" if row.get("rate") is None else f"{row['rate']}%"
    opened, closed = row.get("opened"), row.get("closed")
    both = opened is not None and closed is not None
    partly = row.get("closures_uncounted_boards") or 0
    return (
        f"{rank:>2}. "
        + (f"site #{listed.page_place} · " if moved else "")
        + f"{scraped_text.quoted(row.get('company'), COMPANY_FIELD)} · key "
        f"{row.get('key')} · "
        f"{row.get('operator')} · {row.get('stock', 0):,} open now · net "
        f"{_change(row.get('net'))} · opened {_count(opened)} · closed {_count(closed)}"
        + (f" (opened less closed {opened - closed:+,})" if both else "")
        + (
            f", closed read on {row['boards_in_scope'] - partly} of "
            f"{row['boards_in_scope']} Boards"
            if both and partly
            else ""
        )
        + f" · rate {rate}"
        + "".join(f"{FLAG_MARK}{_said(flag, row)}" for flag in listed.flags)
    )


def _not_ranked(
    name: str, lens: Lens, counts: dict[str, Any], hidden_here: int, hidden: set[str]
) -> str:
    """What the ranking left out, and why, as one line. A Lens's own exclusions are companies
    the ranking holds, so they are said as a part of them: listed among the companies not
    ranked, "Ranked 2,196 companies; not ranked: … 334 whose closures were not counted" read
    the 334 as outside the 2,196, which already held them."""
    left_out = [
        f"{counts.get('too_new', 0):,} counted for under 3 days",
        (
            f"{counts.get('below_min_stock', 0):,} with fewer than "
            f"{counts.get('min_stock', 25)} openings"
        ),
        f"{counts.get('unnamed', 0):,} Boards no directory company holds",
    ]
    if hidden_here:
        left_out.append(
            f"{hidden_here} {' and '.join(sorted(hidden))} rows hidden, as the site's tab hides "
            "them (include_hidden_operators shows them)"
        )
    ranked = counts.get("ranked", 0)
    said = f"Ranked {ranked:,} companies; not ranked: " + "; ".join(left_out) + "."
    if lens.left_out:
        said += (
            f" Of those {ranked:,}, {name} leaves out "
            + "; ".join(
                f"{counts.get(key, 0):,} {_LEFT_OUT[key]}" for key in lens.left_out
            )
            + "."
        )
    return said


def answer(client: SpaceClient, arguments: dict[str, Any]) -> str:
    name = arguments["lens"]
    lens = LENSES[name]
    hot = client.read(SpaceRoute.HOT)
    window = hot.get("window") or {}
    listing = _listing(
        hot,
        name,
        int(arguments["limit"]),
        bool(arguments.get("include_hidden_operators")),
    )
    lines = [
        (
            f"Hiring now, {name}: {lens.ranks}, over {str(window.get('base'))[:10]} → "
            f"{str(window.get('to'))[:10]}"
            + (", in the site's order" if lens.in_site_order else "")
            + ". Whole tech index."
        ),
        scraped_text.SCRAPED_NOTE,
    ]
    if window.get("base") and window.get("to"):
        span = span_sentence(window.get("turnover_from"), window["base"], window["to"])
        lines += [span] if span else []
    if not lens.in_site_order:
        lines.append(
            "A row's net is the site's change in openings, which can hold re-counting; this "
            "Lens ranks by opened less closed, so report that."
        )
    if listing.moved:
        lines.append(
            "Flagged rows are listed after the unflagged ones, each group in the site's order; "
            "site #N is the row's place on the page."
        )
    lines += [
        _row(rank, listed, listing.moved)
        for rank, listed in enumerate(listing.rows, start=1)
    ]
    if not listing.rows:
        lines.append("No company qualified on this Lens this week.")
    for flag, summary in _FLAG_SUMMARY.items():
        if carried := sum(flag in listed.flags for listed in listing.rows):
            lines.append(f"{carried} of these rows {summary}")
    lines.append(
        _not_ranked(
            name,
            lens,
            hot.get("counts") or {},
            listing.hidden,
            set(hot.get("hidden_by_default") or ()),
        )
    )
    lines.append(
        "There is no per-category ranking; for who is growing in one category, use read_trends "
        "with category and the companies to compare."
    )
    if window.get("to"):
        lines.append(f"Newest trends tick {window['to']}.")
    return "\n".join(lines)


TOOL = SpaceTool(
    name="hiring_now",
    title="Which companies are hiring hardest this week",
    description=(
        "Report a row's postings opened and closed as its hiring, and never lead with a row "
        "marked FLAG: its net is re-counting or its opened cannot be trusted. Companies are "
        "ranked over the trailing week on one Lens. The default, `opened_less_closed`, ranks "
        "postings opened less postings closed, only for companies whose closures were counted "
        "on every Board: the one Lens with no re-counting in its figure. "
        "The site's other Lenses: `expansion` (net change in tech openings, less the counting "
        "steps HeadStart could size; it can still hold re-counting), `volume` (postings "
        "opened) or `rate` (postings opened as a share of the company's openings now, only for "
        "companies whose net change was above 0 and whose closures were counted). On those, a "
        "row is flagged where its net is not backed by its postings opened and "
        "closed, its closures went uncounted on any Board, it opened more postings than are "
        "open now, or, on rate, its base is small. "
        "Whole tech index; companies under 25 openings or counted for under 3 days are not "
        "ranked. Each "
        "row's operator says who posts: employer (the company itself, and any company not on "
        "HeadStart's curated list), services (an IT services firm posting client work it "
        "staffs with its own engineers), staffing (a staffing agency posting its clients' "
        "contracts) or aggregator (a job board re-posting other companies' jobs). On every "
        "Lens a row is flagged operator unverified when it is an employer only by that default "
        "and its name reads like an agency's. Flagged rows are listed after the rest. "
        "Staffing and "
        "aggregator rows are left out unless asked for, as on the site. Each row carries a key "
        "that search_jobs and read_trends accept."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "lens": {
                "type": "string",
                "enum": list(LENSES),
                "default": DEFAULT_LENS,
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "default": 15,
            },
            "include_hidden_operators": {
                "type": "boolean",
                "description": "Also show staffing firms and job boards.",
            },
        },
        "additionalProperties": False,
    },
    when_to_use=(
        "Use hiring_now for which companies are expanding or opening the most roles this week."
    ),
    answer=answer,
    # 50 rows at their longest, each with every flag, measured 20,811 characters (ADR-0321).
    max_chars=25_000,
)
