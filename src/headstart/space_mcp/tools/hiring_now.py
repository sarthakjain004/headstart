"""One `hiring_now` answer: the Space's Hot ranking (`trends.hot_ranking`), one Lens of it.

The Space ranks every Company directory entry once at boot, over the trailing week, on three
Lenses; this answer lists one of them, in the site's order, with the site's numbers. The Operators
the Hot tab hides by default (staffing firms and job boards, ADR-0238) are the ones `/hot` names in
``hidden_by_default``, so the page and this answer read one list; what was left out, and why, is
said rather than silently applied.

A row's ``net`` is its trend's "hiring": the change in openings less the counting steps the
reading could size, so re-counting it could not size stays in (ADR-0272). Bosch Group led
Expansion at net +439 with 23 postings opened and 32 closed. So each row states opened less closed
beside its net, a row whose net is more than its postings opened and closed could make is flagged
as mostly re-counting, and on the Rate lens a row whose base is too small to read is flagged too.
The rows are never re-ranked, so the numbers and their order match the page.
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

_LENS_WORDS = {
    "expansion": (
        "the site's net change in tech openings, less the counting steps it could size; "
        "re-counting it could not size stays in, so read it beside opened and closed"
    ),
    "volume": "postings opened",
    "rate": "postings opened as a share of the company's openings now",
}


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
    flags = []
    if _unbacked(row, pace):
        flags.append(
            "net not backed by postings opened: mostly re-counting, not hiring"
        )
    stock = row.get("stock") or 0
    if lens == "rate" and stock:
        if stock < SMALL_BASE_FLOORS * min_stock:
            flags.append(
                f"small base: at {stock:,} openings each posting opened moves the rate "
                f"{100 / stock:.0f} points"
            )
        if (row.get("rate") or 0) > 100:
            flags.append("more postings opened than are open now")
    return flags


def _row(rank: int, row: dict[str, Any], flags: list[str]) -> str:
    rate = "not counted" if row.get("rate") is None else f"{row['rate']}%"
    opened, closed = row.get("opened"), row.get("closed")
    both = opened is not None and closed is not None
    partly = row.get("closures_uncounted_boards") or 0
    return (
        f"{rank:>2}. {scraped_text.quoted(row.get('company'), COMPANY_FIELD)} · key "
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


def answer(client: SpaceClient, arguments: dict[str, Any]) -> str:
    lens = arguments["lens"]
    limit = int(arguments["limit"])
    show_hidden = bool(arguments.get("include_hidden_operators"))
    hot = client.read(SpaceRoute.HOT)
    hidden = set(hot.get("hidden_by_default") or ())
    ranked = hot.get("lenses", {}).get(lens) or []
    rows = [r for r in ranked if show_hidden or r.get("operator") not in hidden]
    window = hot.get("window") or {}
    counts = hot.get("counts") or {}
    pace = _pace(window)
    lines = [
        (
            f"Hiring now, {lens}: {_LENS_WORDS[lens]}, over "
            f"{str(window.get('base'))[:10]} → {str(window.get('to'))[:10]}, in the site's "
            "order. Whole tech index."
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
    unbacked = small = 0
    for rank, row in enumerate(rows[:limit], start=1):
        flags = _flags(row, lens, pace, counts.get("min_stock", 25))
        unbacked += _unbacked(row, pace)
        small += any(flag.startswith("small base") for flag in flags)
        lines.append(_row(rank, row, flags))
    if not rows:
        lines.append("No company qualified on this Lens this week.")
    if unbacked:
        lines.append(
            f"{unbacked} of these rows have a net larger than their postings opened and "
            "closed could make, even at their pace over the whole window: that net is "
            "re-counting HeadStart could not size, not hiring, so report their opened and "
            "closed instead."
        )
    if small:
        lines.append(
            f"{small} of these rows rank on a small base, where a few postings move the rate "
            "far: weigh them by their postings opened, not the percentage."
        )
    left_out = [
        f"{counts.get('too_new', 0):,} counted for under 3 days",
        (
            f"{counts.get('below_min_stock', 0):,} with fewer than "
            f"{counts.get('min_stock', 25)} openings"
        ),
        f"{counts.get('unnamed', 0):,} Boards no directory company holds",
    ]
    hidden_here = len(ranked) - len(rows)
    if hidden_here:
        left_out.append(
            f"{hidden_here} {' and '.join(sorted(hidden))} rows hidden, as the site's tab hides "
            "them (include_hidden_operators shows them)"
        )
    lines.append(
        f"Ranked {counts.get('ranked', 0):,} companies; not ranked: "
        + "; ".join(left_out)
        + "."
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
        "Companies ranked over the trailing week on one Lens, in the site's order; a row "
        "flagged 'not backed by postings opened' is mostly re-counting, not hiring, so report "
        "its postings opened and closed, not its net. Lenses: `expansion` (net change in tech "
        "openings, less the counting steps HeadStart could size; it can still hold re-counting, "
        "so every row also gives postings opened and closed), `volume` (postings opened) or "
        "`rate` (postings opened as a share of the company's openings now; a small base is "
        "flagged). Whole tech index; companies under 25 openings or counted for under 3 days "
        "are not ranked. Each row's operator says who posts: employer (the company itself, and "
        "any company not on HeadStart's curated list), services (an IT services firm posting "
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
                "enum": ["expansion", "volume", "rate"],
                "default": "expansion",
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
    max_chars=20_000,
)
