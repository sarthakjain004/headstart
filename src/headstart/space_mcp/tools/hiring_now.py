"""One `hiring_now` answer: the Space's Hot ranking (`trends.hot_ranking`), one Lens of it.

The Space ranks every Company directory entry once at boot, over the trailing week, on four
Lenses; this answer lists one of them, with the site's numbers. The Operators the Hot tab hides by
default (staffing firms and job boards, ADR-0238) are the ones `/hot` names in
``hidden_by_default``, so the page and this answer read one list; what was left out, and why, is
said rather than silently applied.

The default Lens is `opened_less_closed` (ADR-0321): postings opened less postings closed, for
companies whose closures were counted on every Board, so its figure holds no re-counting. A row's
``net`` is its trend's "hiring": the change in openings less the counting steps the reading could
size, so re-counting it could not size stays in (ADR-0272). Bosch Group led Expansion at net +442
with 23 postings opened and 33 closed. So each row states opened less closed beside its net, and
every Lens flags the same artifacts: a net more than its postings opened and closed could make, a
company whose closures went uncounted, more postings opened than are open now, and on Rate a base
too small to read. On the site's three older Lenses a flagged row is listed after the unflagged
ones, each group in the site's order, and every row then gives its place on the page; Opened less
closed ranks by a figure no flag questions, so it keeps its order.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from headstart.space_mcp import scraped_text
from headstart.space_mcp.space_client import SpaceClient, SpaceRoute
from headstart.space_mcp.space_tool import SpaceTool

#: A company name past this is cut, as search cuts one.
COMPANY_FIELD = 60

#: A Rate row whose company has fewer openings than this many times the ranking's floor is
#: flagged: at 25 openings each posting opened moves its rate 4 points, and New York Life read
#: 2016% off 25.
SMALL_BASE_FLOORS = 2

#: The Lens answered unless another is asked for: the one whose figure holds no re-counting.
DEFAULT_LENS = "opened_less_closed"

_LENS_WORDS = {
    DEFAULT_LENS: (
        "postings opened less postings closed, only for companies whose closures were counted "
        "on every Board, largest first"
    ),
    "expansion": (
        "the site's net change in tech openings, less the counting steps it could size; "
        "re-counting it could not size stays in, so read it beside opened and closed"
    ),
    "volume": "postings opened",
    "rate": "postings opened as a share of the company's openings now",
}

_UNBACKED = "net not backed by postings opened: mostly re-counting, not hiring"
#: Said per row in two words; the line under the rows says what it means.
_UNCOUNTED_CLOSURES = "closures not counted"


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


def _unbacked(row: dict[str, Any], pace: float) -> bool:
    """Whether the row's net is more than its postings opened and closed could make, even at
    their pace over the whole window: an opening gained or lost is a posting opened or closed.
    A shrinking net whose closures went uncounted cannot be judged."""
    net, opened, closed = row.get("net"), row.get("opened"), row.get("closed")
    if net is None or opened is None:
        return False
    if closed is None:
        return net > opened * pace
    return abs(net) > (opened + closed) * pace


def _flags(row: dict[str, Any], lens: str, pace: float, min_stock: int) -> list[str]:
    """Every artifact check, on every Lens (ADR-0321). Only a small base is Rate's alone: it
    questions a share, which no other Lens ranks by."""
    flags = [_UNBACKED] if _unbacked(row, pace) else []
    stock, opened = row.get("stock") or 0, row.get("opened")
    if lens == "rate" and stock and stock < SMALL_BASE_FLOORS * min_stock:
        flags.append(
            f"small base: at {stock:,} openings each posting opened moves the rate "
            f"{100 / stock:.0f} points"
        )
    if stock and (opened or 0) > stock:
        flags.append("more postings opened than are open now")
    if opened and row.get("closed") is None:
        flags.append(_UNCOUNTED_CLOSURES)
    return flags


def _row(
    rank: int, row: dict[str, Any], flags: list[str], site_rank: int | None
) -> str:
    rate = "not counted" if row.get("rate") is None else f"{row['rate']}%"
    opened, closed = row.get("opened"), row.get("closed")
    both = opened is not None and closed is not None
    partly = row.get("closures_uncounted_boards") or 0
    return (
        f"{rank:>2}. "
        + (f"site #{site_rank} · " if site_rank else "")
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
        + "".join(f" · FLAG {flag}" for flag in flags)
    )


def _not_ranked(
    lens: str, counts: dict[str, Any], hidden_here: int, hidden: set[str]
) -> str:
    """What the ranking left out, and why, as one line."""
    left_out = [
        f"{counts.get('too_new', 0):,} counted for under 3 days",
        (
            f"{counts.get('below_min_stock', 0):,} with fewer than "
            f"{counts.get('min_stock', 25)} openings"
        ),
        f"{counts.get('unnamed', 0):,} Boards no directory company holds",
    ]
    if lens in ("rate", DEFAULT_LENS):
        left_out.append(
            f"{counts.get('closures_uncounted', 0):,} whose closures were not counted, so "
            "their postings opened may be the same postings listed again"
        )
    if lens == DEFAULT_LENS:
        left_out.append(
            f"{counts.get('closures_partly_uncounted', 0):,} whose closures went uncounted on "
            "some of their Boards, so their closed runs low"
        )
    if hidden_here:
        left_out.append(
            f"{hidden_here} {' and '.join(sorted(hidden))} rows hidden, as the site's tab hides "
            "them (include_hidden_operators shows them)"
        )
    return (
        f"Ranked {counts.get('ranked', 0):,} companies; not ranked: "
        + "; ".join(left_out)
        + "."
    )


def in_answer_order(
    hot: dict[str, Any], lens: str, limit: int, show_hidden: bool = False
) -> list[tuple[int, dict[str, Any], list[str]]]:
    """The rows an answer lists, in its order, each as ``(its place on the page, row, flags)``:
    the first ``limit`` rows the page shows on ``lens``, and on the site's older Lenses the
    flagged ones after the rest. The evaluation judges a ranking answer by this order."""
    hidden = set(hot.get("hidden_by_default") or ())
    ranked = hot.get("lenses", {}).get(lens) or []
    rows = [r for r in ranked if show_hidden or r.get("operator") not in hidden]
    pace = _pace(hot.get("window") or {})
    min_stock = (hot.get("counts") or {}).get("min_stock", 25)
    shown = [
        (site_rank, row, _flags(row, lens, pace, min_stock))
        for site_rank, row in enumerate(rows[:limit], start=1)
    ]
    if lens == DEFAULT_LENS:
        return shown
    # A flag questions the figure the site's older Lenses rank by, so a flagged row goes last;
    # sorted() is stable, so each group keeps the site's order.
    return sorted(shown, key=lambda item: bool(item[2]))


def answer(client: SpaceClient, arguments: dict[str, Any]) -> str:
    lens = arguments["lens"]
    show_hidden = bool(arguments.get("include_hidden_operators"))
    hot = client.read(SpaceRoute.HOT)
    hidden = set(hot.get("hidden_by_default") or ())
    ranked = hot.get("lenses", {}).get(lens) or []
    rows = [r for r in ranked if show_hidden or r.get("operator") not in hidden]
    window = hot.get("window") or {}
    counts = hot.get("counts") or {}
    pace = _pace(window)
    in_order = in_answer_order(hot, lens, int(arguments["limit"]), show_hidden)
    moved = [site_rank for site_rank, _, _ in in_order] != list(
        range(1, len(in_order) + 1)
    )
    lines = [
        (
            f"Hiring now, {lens}: {_LENS_WORDS[lens]}, over "
            f"{str(window.get('base'))[:10]} → {str(window.get('to'))[:10]}"
            + ("" if lens == DEFAULT_LENS else ", in the site's order")
            + ". Whole tech index."
        ),
        scraped_text.SCRAPED_NOTE,
    ]
    if pace > 1:
        began = str(window["turnover_from"])
        lines.append(
            f"Opened and closed are counted only from {began[:16].replace('T', ' ')}, when "
            f"HeadStart began counting them: {_days(began, window['to']):.1f} of the window's "
            f"{_days(window['base'], window['to']):.1f} days."
        )
    if moved:
        lines.append(
            "Flagged rows are listed after the unflagged ones, each group in the site's order; "
            "site #N is the row's place on the page."
        )
    lines += [
        _row(rank, row, flags, site_rank if moved else None)
        for rank, (site_rank, row, flags) in enumerate(in_order, start=1)
    ]
    if not rows:
        lines.append("No company qualified on this Lens this week.")
    flagged = [flag for _, _, flags in in_order for flag in flags]
    if unbacked := flagged.count(_UNBACKED):
        lines.append(
            f"{unbacked} of these rows have a net larger than their postings opened and "
            "closed could make, even at their pace over the whole window: that net is "
            "re-counting HeadStart could not size, not hiring, so report their opened and "
            "closed instead."
        )
    if uncounted := flagged.count(_UNCOUNTED_CLOSURES):
        lines.append(
            f"{uncounted} of these rows had their closures go uncounted: their postings "
            "opened may be the same postings listed again, so read them beside open now."
        )
    if small := sum(flag.startswith("small base") for flag in flagged):
        lines.append(
            f"{small} of these rows rank on a small base, where a few postings move the rate "
            "far: weigh them by their postings opened, not the percentage."
        )
    lines.append(_not_ranked(lens, counts, len(ranked) - len(rows), hidden))
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
        "Companies ranked over the trailing week on one Lens. The default, "
        "`opened_less_closed`, ranks postings opened less postings closed, only for companies "
        "whose closures were counted on every Board: the one Lens with no re-counting in its "
        "figure, so lead with it for who is hiring. The site's other Lenses: `expansion` (net "
        "change in tech openings, less the counting steps HeadStart could size; it can still "
        "hold re-counting), `volume` (postings opened) or `rate` (postings opened as a share of "
        "the company's openings now). Every row gives postings opened and closed, and is "
        "flagged where its net is not backed by them (mostly re-counting: report its opened and "
        "closed), its closures went uncounted, it opened more postings than are open now, or, "
        "on rate, its base is small; on expansion, volume and rate flagged rows are listed "
        "last. Whole tech index; companies under 25 openings or counted for under 3 days are "
        "not ranked. Each row's operator says who posts: employer (the company itself, and any "
        "company not on HeadStart's curated list), services (an IT services firm posting "
        "client work it staffs with its own engineers), staffing (a staffing agency posting "
        "its clients' contracts) or aggregator (a job board re-posting other companies' jobs). "
        "Staffing and aggregator rows are left out unless asked for, as on the site. Each row "
        "carries a key that search_jobs and read_trends accept."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "lens": {
                "type": "string",
                "enum": [DEFAULT_LENS, "expansion", "volume", "rate"],
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
