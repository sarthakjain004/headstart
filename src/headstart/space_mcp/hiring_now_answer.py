"""One `hiring_now` answer: the Space's Hot ranking (`trends.hot_ranking`), one Lens of it.

The Space ranks every Company directory entry once at boot, over the trailing week, on three
Lenses; this answer lists one of them. The Operators the Hot tab hides by default (staffing firms
and job boards, ADR-0238) are the ones `/hot` names in ``hidden_by_default``, so the page and this
answer read one list; what was left out, and why, is said rather than silently applied.
"""

from __future__ import annotations

from typing import Any

from headstart.space_mcp import scraped_text
from headstart.space_mcp.space_client import SpaceClient, SpaceRoute

_LENS_WORDS = {
    "expansion": "net growth in tech openings, with the steps that are not hiring removed",
    "volume": "jobs opened",
    "rate": "jobs opened as a share of the company's openings now",
}


def _signed(value: int | None) -> str:
    return "not counted" if value is None else f"{value:+,}"


def _count(value: int | None) -> str:
    return "not counted" if value is None else f"{value:,}"


def _row(rank: int, row: dict[str, Any]) -> str:
    rate = "not counted" if row.get("rate") is None else f"{row['rate']}%"
    return (
        f"{rank:>2}. {scraped_text.quoted(row.get('company'))} · key {row.get('key')} · "
        f"{row.get('operator')} · {row.get('stock', 0):,} open now · net "
        f"{_signed(row.get('net'))} · opened {_count(row.get('opened'))} · closed "
        f"{_count(row.get('closed'))} · rate {rate}"
    )


def answer(client: SpaceClient, arguments: dict[str, Any]) -> str:
    lens = arguments.get("lens") or "expansion"
    limit = int(arguments.get("limit") or 15)
    show_hidden = bool(arguments.get("include_hidden_operators"))
    hot = client.read(SpaceRoute.HOT)
    hidden = set(hot.get("hidden_by_default") or ())
    ranked = hot.get("lenses", {}).get(lens) or []
    rows = [r for r in ranked if show_hidden or r.get("operator") not in hidden]
    window = hot.get("window") or {}
    lines = [
        (
            f"Hiring now, {lens}: {_LENS_WORDS[lens]}, over "
            f"{str(window.get('base'))[:10]} → {str(window.get('to'))[:10]}. "
            "Whole tech index."
        ),
        scraped_text.SCRAPED_NOTE,
    ]
    lines += [_row(i, row) for i, row in enumerate(rows[:limit], start=1)]
    if not rows:
        lines.append("No company qualified on this Lens this week.")
    counts = hot.get("counts") or {}
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
