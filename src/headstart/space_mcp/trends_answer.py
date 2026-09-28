"""One `read_trends` answer: the Space's `/trends` reading, told as figures a model can quote.

`/trends` serves every figure the Trends tab shows, reconciled (`trends.line_reading`, ADR-0233):
each line's start and latest openings, its hiring, and its "Not hiring" split into named causes,
with ``latest − start == hiring + Σ not_hiring``. The page draws; this answer only reports — the
drawing arrays (``netted``, ``steps_at``, ``reference``, ``points``, ``day_markers``) are left out,
which is what takes a 406 kB payload down to a few hundred words. A reading that does not
reconcile is still reported, saying so; one the Space could not read at all reports no figures.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any

from headstart.mcp_protocol.stdio import ToolFailure
from headstart.space_mcp import company_names, scraped_text
from headstart.space_mcp.space_client import SpaceClient, SpaceRoute

#: `breakdown` as this tool spells it -> `/trends`' `split` (None: the default per-category view).
SPLITS = {"category": None, "level": "bands", "role": "roles", "company": "company"}

#: How many lines a concise answer lists, largest moves first.
CONCISE_LINES = 8

#: Why a line's percentage is withheld, in words (`line_reading.MOSTLY_RECOUNTED`).
_WITHHELD_WORDS = {
    "mostly_recounted": "most of this line's change is re-counting, not hiring",
}


def _signed(value: float | None) -> str:
    return "?" if value is None else f"{value:+,.0f}"


def _breakdown(arguments: dict[str, Any], companies: list[str]) -> str:
    asked = arguments.get("breakdown")
    category = arguments.get("category")
    if asked is None:
        if len(companies) >= 2:
            return "company"
        return "level" if category else "category"
    if asked in ("level", "role") and not category:
        raise ToolFailure(f"breakdown {asked} splits one category; send category too.")
    if asked == "category" and category:
        raise ToolFailure(
            "breakdown category lists every category, so it takes no category; drop one of them."
        )
    if asked == "company" and len(companies) < 2:
        raise ToolFailure("breakdown company compares companies; name two or more.")
    return asked


def _move(move: dict[str, Any], *, causes: bool) -> str:
    said = [
        f"{move['start']:,} → {move['latest']:,}",
        f"hiring {_signed(move['hiring'])}",
    ]
    if move.get("percent") is not None:
        said[-1] += f" ({move['percent']:+.1f}%"
        said[-1] += (
            f", about {_signed(move['per_week'])} a week)"
            if move.get("per_week") is not None
            else ")"
        )
    elif move.get("percent_withheld"):
        reason = move["percent_withheld"]
        said.append(f"no percentage: {_WITHHELD_WORDS.get(reason, reason)}")
    total = move.get("not_hiring_total") or 0
    if total:
        text = f"not hiring {_signed(total)}"
        if causes and move.get("not_hiring"):
            text += ": " + ", ".join(
                f"{cause['label']} {_signed(cause['size'])}"
                for cause in move["not_hiring"]
            )
        said.append(text)
    turnover = move.get("turnover")
    if turnover:
        closed = turnover.get("closed")
        said.append(
            f"{turnover['opened']:,} opened, "
            + (f"{closed:,} closed" if closed is not None else "closures not counted")
        )
    return "; ".join(said)


def _label(line: dict[str, Any], breakdown: str) -> str:
    # A company line is labelled with the employer's own, scraped name; categories and levels
    # are HeadStart's words.
    if breakdown == "company" or line.get("whole_company"):
        return scraped_text.quoted(line.get("label"))
    return str(line.get("label"))


def _pick(
    client: SpaceClient, values: list[str]
) -> list[company_names.DirectoryCompany]:
    if not values:
        return []
    with ThreadPoolExecutor(max_workers=min(len(values), 4)) as pool:
        return list(pool.map(lambda v: company_names.for_trends(client, v), values))


def answer(client: SpaceClient, arguments: dict[str, Any]) -> str:
    asked_companies = [c.strip() for c in arguments.get("companies") or [] if c.strip()]
    breakdown = _breakdown(arguments, asked_companies)
    picks = _pick(client, asked_companies)
    days = int(arguments.get("days") or 30)
    since = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")
    params: list[tuple[str, str]] = [("since", since)]
    if category := arguments.get("category"):
        params.append(("family", category))
    if split := SPLITS[breakdown]:
        params.append(("split", split))
    params += [("company", pick.key) for pick in picks]
    payload = client.read(SpaceRoute.TRENDS, params)

    head = []
    if picks:
        head.append(
            "Companies: "
            + "; ".join(pick.described() for pick in picks)
            + ". A name is read as the directory's largest company of that name, as the site's "
            "Trends picker reads it."
        )
    if category:
        head.append(f"Category: {payload.get('family_label') or category}.")
    if not picks and not category:
        head.append("The whole index.")
    reading = payload.get("reading")
    window = (reading or {}).get("window") or {}
    if window:
        head.append(f"Window {window['from'][:10]} → {window['to'][:10]}.")
    counted = payload.get("counted_since") or {}
    if counted and max(counted.values()) > since:
        head.append(
            f"You asked for {days} days; a company is counted only from "
            f"{max(counted.values())[:10]}, when per-Board counting began."
        )
    if reading is None:
        why = payload.get("reading_error") or "no reason given"
        head.append(
            f"The Space has this trend's counts but could not read them into figures ({why}). "
            "No figures are reported rather than unchecked ones; a narrower question (one "
            "category, fewer companies) may read."
        )
        return "\n".join(head)

    full = arguments.get("detail") == "full"
    lines = head
    if picks and breakdown == "company":
        lines.append(scraped_text.SCRAPED_NOTE)
    if reading.get("total"):
        lines.append(f"Total {_move(reading['total']['move'], causes=True)}.")
    ranked = sorted(
        reading.get("lines") or [],
        key=lambda line: -abs(line["move"].get("hiring") or 0),
    )
    shown = ranked if full else ranked[:CONCISE_LINES]
    if shown:
        lines.append(
            f"By {breakdown}, largest moves first"
            + (
                ""
                if full or len(ranked) <= CONCISE_LINES
                else f" ({len(shown)} of {len(ranked)})"
            )
            + ":"
        )
        lines += [
            f"  {_label(line, breakdown)}: {_move(line['move'], causes=full)}"
            for line in shown
        ]
    if full and reading.get("marked_changes"):
        lines.append(
            "Marked changes: "
            + "; ".join(
                f"{change['ts'][:10]} {change['label']}"
                for change in reading["marked_changes"]
            )
            + "."
        )
    if reading.get("reconciles", True):
        lines.append("Figures reconcile.")
    else:
        lines.append(
            "These figures do not fully reconcile: "
            + "; ".join(reading.get("violations", [])[:3])
            + "."
        )
    if window:
        lines.append(f"Newest trends tick {window['to']}.")
    return "\n".join(lines)
