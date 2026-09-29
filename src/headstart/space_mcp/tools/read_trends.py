"""One `read_trends` answer: the Space's `/trends` reading, told so that hiring is postings opened
and closed, never the change in openings listed (ADR-0272).

`/trends` serves every figure the Trends tab shows (`trends.line_reading`, ADR-0233): each line's
start and latest openings, the counting steps the reading could size ("not hiring"), the rest it
calls hiring, and the postings opened and closed (turnover, ADR-0227). That rest is not hiring.
With no company picked nothing was sized at all, so a 30-day whole-index window read "hiring
+111,851" while its postings opened and closed netted −514; since ADR-0270 and ADR-0304 the
index's counting changes and Boards found are sized, but not its duplicate removals. So this
answer leads with turnover, reports the change in openings listed separately, and names what
neither turnover nor a sized step explains as change HeadStart could not size, with the window's
counting changes, each label once. Where turnover is missing or partial it says so. The drawing arrays (``netted``,
``steps_at``, ``reference``, ``points``, ``day_markers``) are left out, which takes a 406 kB
payload down to a few hundred words. A reading that fails the Space's arithmetic check is still
reported, saying so; one the Space could not read at all reports no figures.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from headstart.mcp_protocol.messages import ToolFailure
from headstart.space_mcp import company_scope, role_families, scraped_text
from headstart.space_mcp.space_client import SpaceClient, SpaceRoute
from headstart.space_mcp.space_tool import SpaceTool

#: `breakdown` as this tool spells it -> `/trends`' `split` (None: the default per-category view).
SPLITS = {"category": None, "level": "bands", "role": "roles", "company": "company"}

#: `measure` as this tool spells it -> `/trends`' `metric`.
MEASURES = {"openings": "stock", "new": "new"}

#: How many lines a concise answer lists, largest first.
CONCISE_LINES = 8

#: A line counted for less than this share of the window says for how long it was counted.
_SHORT_SPAN = 0.9

#: A window that starts more than this many days after the asked start says so.
_LATE_START_DAYS = 1.0


def _now() -> datetime:
    """Now, in UTC: where a `days` window is counted back from. Its own function so a test can
    pin it to its fixture's ticks."""
    return datetime.now(UTC)


def _signed(value: float | None) -> str:
    return "?" if value is None else f"{value:+,.0f}"


def _at(stamp: str) -> str:
    """A tick's stamp to the minute, as a reader names it: "2026-09-25 18:16"."""
    return stamp[:16].replace("T", " ")


def _days(start: str, end: str) -> float:
    return (
        datetime.fromisoformat(end) - datetime.fromisoformat(start)
    ).total_seconds() / 86400


def _date(name: str, value: str | None) -> str | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip()).isoformat()
    except ValueError as exc:
        raise ToolFailure(f"`{name}` must be a date, YYYY-MM-DD.") from exc


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


def _window(arguments: dict[str, Any]) -> tuple[str, str | None]:
    """The asked start and end as `/trends` stamps: `since` (a date) or `days` back from now,
    and `until` (a date, through its last second)."""
    since_day = _date("since", arguments.get("since"))
    until_day = _date("until", arguments.get("until"))
    since = (
        f"{since_day}T00:00:00+00:00"
        if since_day
        else (_now() - timedelta(days=int(arguments["days"]))).isoformat(
            timespec="seconds"
        )
    )
    until = f"{until_day}T23:59:59+00:00" if until_day else None
    if until and until < since:
        raise ToolFailure("`until` is before the window's start.")
    return since, until


def _window_params(
    arguments: dict[str, Any], since: str, until: str | None
) -> list[tuple[str, str]]:
    """The window as the site's Trends tab sends it: comparable coverage holds the Boards of its
    base fixed, and its base is the window's start (`app.js` `trendsQuery`)."""
    comparable = arguments["coverage"] == "comparable"
    params = [("base" if comparable else "since", since)]
    if until:
        params.append(("until", until))
    if comparable:
        params.append(("coverage", "comparable"))
    if (metric := MEASURES[arguments["measure"]]) != "stock":
        params.append(("metric", metric))
    return params


def _pick(
    client: SpaceClient, values: list[str]
) -> list[company_scope.DirectoryCompany]:
    if not values:
        return []
    with ThreadPoolExecutor(max_workers=min(len(values), 4)) as pool:
        return list(pool.map(lambda v: company_scope.for_trends(client, v), values))


class _Changes:
    """The counting changes an answer names, numbered once each by label: a window repeats
    labels ("we got better at spotting tech jobs…" four times in 30 days), so each is said once
    and every figure refers to it by number, with the sizes of one label summed."""

    def __init__(self, marked: list[dict[str, Any]]) -> None:
        self.days: dict[str, list[str]] = {}
        for change in marked:
            self.days.setdefault(change["label"], []).append(change["ts"][:10])
        self.unsized = [
            label
            for label in self.days
            if not any(c.get("sizes") for c in marked if c["label"] == label)
        ]
        self.numbers: dict[str, int] = {}

    def number(self, label: str) -> str:
        self.numbers.setdefault(label, len(self.numbers) + 1)
        return f"[{self.numbers[label]}]"

    def causes(self, move: dict[str, Any]) -> str:
        """``move``'s sized causes, one per label with its sizes summed, by number."""
        summed: dict[str, int] = {}
        for cause in move.get("not_hiring") or []:
            summed[cause["label"]] = summed.get(cause["label"], 0) + cause["size"]
        return ", ".join(
            f"{self.number(label)} {_signed(size)}"
            for label, size in summed.items()
            if size
        )

    def legend(self, full: bool) -> str | None:
        if not self.numbers:
            return None

        def said(label: str) -> str:
            days = self.days.get(label)
            if not days:
                return label
            if full or len(days) == 1:
                return f"{label} ({', '.join(days)})"
            span = days[0] if days[0] == days[-1] else f"{days[0]} to {days[-1]}"
            return f"{label} ({len(days)} times, {span})"

        return "Counting changes: " + "; ".join(
            f"[{n}] {said(label)}" for label, n in self.numbers.items()
        )


@dataclass(frozen=True)
class _Split:
    """A line's change in openings listed, split into what turnover and the sized steps explain
    and the rest. ``net`` is None where turnover gives no net (not counted, or closures not
    counted), and ``rest`` is then the change less the sized steps, hiring and re-counting
    together."""

    change: int
    sized: int
    net: int | None
    rest: int

    @classmethod
    def of(cls, move: dict[str, Any]) -> _Split:
        change = move["latest"] - move["start"]
        sized = move.get("not_hiring_total") or 0
        net = (move.get("turnover") or {}).get("net")
        return cls(change, sized, net, change - sized - (net or 0))


def _turnover(move: dict[str, Any]) -> str | None:
    turnover = move.get("turnover")
    if not turnover:
        return None
    if turnover.get("closed") is None:
        return f"{turnover['opened']:,} opened, closed not counted"
    return (
        f"{turnover['opened']:,} opened, {turnover['closed']:,} closed, "
        f"net {_signed(turnover['net'])}"
    )


def _span(move: dict[str, Any], window_days: float) -> str:
    span = move.get("span_days") or 0.0
    if window_days and span < _SHORT_SPAN * window_days:
        return (
            f", counted for its last {span:.1f} of the window's {window_days:.1f} days"
        )
    return ""


def _line(
    move: dict[str, Any], window_days: float, changes: _Changes, full: bool, what: str
) -> str:
    """One line of a breakdown: its turnover first, then its openings listed (``what``) and
    their split."""
    split = _Split.of(move)
    said = [_turnover(move)] if move.get("turnover") else []
    said.append(
        f"{what} {move['start']:,} → {move['latest']:,} ({_signed(split.change)}"
        f"{_span(move, window_days)})"
    )
    if split.sized:
        causes = changes.causes(move) if full else ""
        said.append(
            f"sized re-counting {_signed(split.sized)}"
            + (f" ({causes})" if causes else "")
        )
    if split.rest and split.net is not None:
        said.append(f"unsized rest {_signed(split.rest)}")
    if move.get("percent_withheld") == "mostly_recounted":
        said.append("the site marks it mostly re-counted")
    return "; ".join(said)


def _rank(line: dict[str, Any]) -> int:
    """How large a line's move is: its turnover net, else its postings opened, else its change
    in openings listed."""
    move = line["move"]
    turnover = move.get("turnover") or {}
    if turnover.get("net") is not None:
        return abs(turnover["net"])
    if turnover:
        return turnover["opened"]
    return abs(move["latest"] - move["start"])


def _rest_contains(
    payload: dict[str, Any], picked: bool, changes: _Changes, window: dict[str, str]
) -> str:
    """What change HeadStart could not size can hold, in this view: a company's line has its
    found Boards and duplicate removals sized; the index's has its found Boards sized from the
    first per-Board count on (ADR-0304), under openings, but not its duplicate removals; and
    comparable coverage leaves found Boards out."""
    ledger = payload.get("ledger_start")
    if picked:
        held = ["a Board dropped or read differently from before"]
    elif payload.get("coverage") == "comparable":
        held = ["Boards dropped", "duplicate postings removed"]
    elif payload.get("metric") == "new":
        held = ["Boards found or dropped", "duplicate postings removed"]
    elif ledger and window["from"] < ledger:
        held = [
            f"Boards found before per-Board counting began on {ledger[:10]}",
            "Boards dropped or read differently",
            "duplicate postings removed",
        ]
    else:
        held = ["Boards dropped or read differently", "duplicate postings removed"]
    if changes.unsized:
        held.append(
            "counting changes not sized here ("
            + ", ".join(changes.number(label) for label in changes.unsized)
            + ")"
        )
    return held[0] if len(held) == 1 else ", ".join(held[:-1]) + " and " + held[-1]


def _turnover_notes(
    payload: dict[str, Any], window: dict[str, str], labels: dict[str, str]
) -> list[str]:
    """What turnover leaves out of this window: the runs before HeadStart counted it, the runs a
    counting change landed on, and closures a partial read could not see."""
    notes = []
    began = payload.get("turnover_since")
    if began and began > window["from"]:
        notes.append(
            f"Opened and closed are counted only from {_at(began)}, when HeadStart began "
            f"counting them: {_days(began, window['to']):.1f} of the window's "
            f"{_days(window['from'], window['to']):.1f} days"
        )
    if left_out := payload.get("turnover_left_out"):
        notes.append(
            f"they leave out the {len(left_out)} runs a counting change landed on"
        )
    in_scope = payload.get("boards_in_scope") or {}
    for pick, boards in sorted((payload.get("closures_unseen") or {}).items()):
        if not boards:
            continue
        # The index's own count is of every Board with such a run: it has no fair "of".
        of = f" of {in_scope[pick]:,}" if pick and pick in in_scope else ""
        whose = f" of {labels.get(pick, pick)}" if pick else " in scope"
        notes.append(
            f"closures went uncounted on some run on {boards:,}{of} Boards{whose}, "
            "so closed can run low"
        )
    if payload.get("closures_uncounted") and any(labels):
        notes.append(
            "closed is not counted where every Board a line covers had such a run"
        )
    if not notes:
        return []
    text = "; ".join(notes)
    return [text[0].upper() + text[1:] + "."]


def _total(
    move: dict[str, Any],
    payload: dict[str, Any],
    window: dict[str, str],
    changes: _Changes,
    labels: dict[str, str],
    full: bool,
    named: str | None = None,
) -> list[str]:
    """The first row: hiring as turnover, then the openings listed and what explains them."""
    split = _Split.of(move)
    began = payload.get("turnover_since")
    before = (
        f", plus any hiring before {_at(began)}"
        if began and began > window["from"]
        else ""
    )
    who = f"{named}: " if named else ""
    new = payload.get("metric") == "new"
    out = []
    if new:
        out.append(
            f"{who}new this week, postings HeadStart first saw in the trailing 7 days: "
            f"{move['start']:,} → {move['latest']:,} ({_signed(split.change)}). It is not a "
            "count of postings opened: Boards found and counting changes move it too. "
            "Opened and closed are counted only under measure openings."
        )
    elif turnover := _turnover(move):
        out.append(f"{who}hiring, as postings opened and closed: {turnover}.")
        out += _turnover_notes(payload, window, labels)
    else:
        out.append(f"{who}opened and closed are not counted in this view.")
    if not new:
        out.append(
            f"Openings listed: {move['start']:,} → {move['latest']:,} "
            f"({_signed(split.change)}{_span(move, _days(window['from'], window['to']))})."
        )
    explained = []
    if split.net is not None:
        explained.append(f"postings opened and closed account for {_signed(split.net)}")
    if split.sized:
        explained.append(
            f"counting changes HeadStart sized for {_signed(split.sized)} "
            f"({changes.causes(move)})"
        )
    elif move.get("turnover") or new:
        explained.append("HeadStart sized none of it as re-counting")
    contains = _rest_contains(payload, bool(labels), changes, window)
    if split.rest and split.net is not None:
        explained.append(
            f"the other {_signed(split.rest)}, the unsized rest, is not a hiring figure: "
            f"HeadStart could not size it, and it holds re-counting such as {contains}{before}"
        )
    elif split.rest:
        explained.append(
            f"the other {_signed(split.rest)} mixes hiring with re-counting HeadStart could "
            f"not size, such as {contains}"
        )
    if explained:
        text = "; ".join(explained)
        out.append(text[0].upper() + text[1:] + ".")
    out[0] = out[0][0].upper() + out[0][1:]
    if full and move.get("hiring") is not None:
        rate = [f"{move['percent']:+.1f}%"] if move.get("percent") is not None else []
        if move.get("per_week") is not None:
            rate.append(f"about {_signed(move['per_week'])} a week")
        out.append(
            f"The Trends tab shows {_signed(move['hiring'])}"
            + (f" ({', '.join(rate)})" if rate else "")
            + " as hiring: the change less the sized steps, the unsized change included."
        )
    return out


def _label(line: dict[str, Any], breakdown: str) -> str:
    # A company line is labelled with the employer's own, scraped name; categories and levels
    # are HeadStart's words.
    if breakdown == "company" or line.get("whole_company"):
        return scraped_text.quoted(line.get("label"))
    return str(line.get("label"))


def _roles_head(payload: dict[str, Any], category: str, label: str) -> list[str]:
    """What a role breakdown's first row is: the watched roles added together, which are not
    the category, or the category having none."""
    if category not in (payload.get("watch_parents") or ()):
        none = (
            f"{label} has no watched roles, so it has no role breakdown; breakdown level "
            "splits it by seniority. Its own figures:"
        )
        return [none]
    total = ((payload.get("reading") or {}).get("total") or {}).get("move")
    if not total:
        return []
    change = _signed(total["latest"] - total["start"])
    roles = (
        f"Watched roles within {label}, added together (not the whole category): listed "
        f"{total['start']:,} → {total['latest']:,} ({change}). Opened and closed are not "
        "counted per watched role, and HeadStart sizes no re-counting on them, so each "
        "role's change mixes hiring with re-counting."
    )
    return [roles]


def answer(client: SpaceClient, arguments: dict[str, Any]) -> str:
    asked_companies = [c.strip() for c in arguments.get("companies") or [] if c.strip()]
    breakdown = _breakdown(arguments, asked_companies)
    since, until = _window(arguments)
    picks = _pick(client, asked_companies)
    params: list[tuple[str, str]] = _window_params(arguments, since, until)
    if category := arguments.get("category"):
        params.append(("family", category))
    if split := SPLITS[breakdown]:
        params.append(("split", split))
    params += [("company", pick.key) for pick in picks]
    payload = client.read(SpaceRoute.TRENDS, params)
    # `/trends` answers an unknown family with an empty window that reconciles; without
    # `config/`, `category` is a free string, so the Space's own word is what refuses it.
    if category and payload.get("family_known") is False:
        raise ToolFailure(f"No job category is called {category!r}.")
    # A role breakdown's first row is its watched roles, not the category: the category's own
    # figures are the level view's first row.
    whole = (
        client.read(
            SpaceRoute.TRENDS,
            [(k, "bands" if k == "split" else v) for k, v in params],
        )
        if breakdown == "role"
        else None
    )
    ends = until[:10] if until else "now"
    comparable = arguments["coverage"] == "comparable"

    head = []
    if picks:
        head.append(
            "Companies: "
            + "; ".join(pick.described() for pick in picks)
            + ". A name is read as the directory's largest company of that name, as the site's "
            "Trends picker reads it; a different employer with the same name is not included."
        )
    category_label = payload.get("family_label") or category
    if category:
        head.append(f"Category: {category_label}.")
    if not picks and not category:
        head.append("The whole index.")
    reading = (whole or payload).get("reading")
    window = (reading or {}).get("window") or {}
    if window:
        head.append(f"Window {window['from'][:10]} → {window['to'][:10]}.")
    if comparable and payload.get("base"):
        base = payload["base"]
        moved = (
            f"; you asked from {since[:10]}, and per-Board counting began "
            f"{str(payload.get('ledger_start'))[:10]}"
            if _days(since, base) > _LATE_START_DAYS
            else ""
        )
        head.append(
            f"Comparable coverage: only Boards HeadStart already tracked on {base[:10]} are "
            f"counted{moved}."
        )
    counted = payload.get("counted_since") or {}
    if counted and max(counted.values()) > since:
        began = max(counted.values())
        why = (
            ", when per-Board counting began"
            if began == payload.get("ledger_start")
            else ""
        )
        head.append(
            f"You asked from {since[:10]}; a company is counted only from {began[:10]}{why}."
        )
    elif window and not comparable and _days(since, window["from"]) > _LATE_START_DAYS:
        head.append(
            f"You asked from {since[:10]}; the history starts {window['from'][:10]}."
        )
    if reading is None:
        why = (whole or payload).get("reading_error") or "no reason given"
        head.append(
            f"The Space has this trend's counts but could not read them into figures ({why}). "
            "No figures are reported rather than unchecked ones; a narrower question (one "
            "category, fewer companies) may read."
        )
        return "\n".join(head)

    if not window:
        start = payload.get("ledger_start")
        head.append(
            f"No trend counts fall between {since[:10]} and {ends}"
            + (f"; per-company counts begin {start[:10]}" if start else "")
            + ". Ask for an earlier or longer window."
        )
        return "\n".join(head)

    full = arguments["detail"] == "full"
    window_days = _days(window["from"], window["to"])
    changes = _Changes(reading.get("marked_changes") or [])
    labels = {
        company["key"]: scraped_text.quoted(company.get("label"))
        for company in payload.get("companies") or []
    }
    lines = head
    if picks and breakdown == "company":
        lines.append(scraped_text.SCRAPED_NOTE)
    if breakdown == "role":
        lines += _roles_head(payload, category, category_label)
    source = whole or payload
    if reading.get("total"):
        lines += _total(
            reading["total"]["move"],
            source,
            window,
            changes,
            labels,
            full,
            named=f"{category_label} as a whole" if whole else None,
        )
    ranked_from = payload.get("reading") or {}
    # A hidden family's line comes last as "Other", so the lines still add up to the whole.
    unlisted = set(payload.get("unlisted_series") or ())
    ranked = sorted(
        ranked_from.get("lines") or [],
        key=lambda line: (line["name"] in unlisted, -_rank(line)),
    )
    shown = ranked if full else ranked[:CONCISE_LINES]
    if shown:
        new = payload.get("metric") == "new"
        by = (
            "largest net of opened and closed first"
            if any((line["move"].get("turnover") or {}) for line in shown)
            else f"largest change in {'new postings' if new else 'openings listed'} first"
        )
        cut = (
            f" ({len(shown)} of {len(ranked)}; detail full shows all {len(ranked)})"
            if len(shown) < len(ranked)
            else ""
        )
        lines.append(f"By {breakdown}, {by}{cut}:")
        lines += [
            f"  {_label(line, breakdown)}: "
            + _line(
                line["move"], window_days, changes, full, "new" if new else "listed"
            )
            for line in shown
        ]
    if legend := changes.legend(full):
        lines.append(legend + ".")
    checked = [r for r in (payload.get("reading"), whole and whole.get("reading")) if r]
    violations = [v for r in checked for v in r.get("violations") or []]
    if all(r.get("reconciles", True) for r in checked):
        lines.append(
            "The Space's arithmetic check passed: each line's parts add up to its change. "
            "It checks sums, not that any figure is hiring."
        )
    else:
        lines.append(
            "The Space's arithmetic check failed: " + "; ".join(violations[:3]) + "."
        )
    lines.append(f"Newest trends tick {window['to']}.")
    return "\n".join(lines)


TOOL = SpaceTool(
    name="read_trends",
    title="Read how tech hiring is changing",
    description=(
        "How tech hiring changed over a window, where hiring is postings opened and closed "
        "(and their net), never the change in openings listed: that change also holds "
        "re-counting (Boards found or dropped, duplicates removed, HeadStart's own counting "
        "changes), and the answer says how much of it turnover and sized steps explain and "
        "how much HeadStart could not size. Report the opened/closed net as hiring; never "
        "call the change in openings listed hiring. Whole index by default, or one job "
        "category, or up to 10 named companies. A company is a directory company: a key such "
        "as 'greenhouse:stripe', or its exact name (read as the site's Trends picker reads "
        "it). Tell the user which directory company each name was read as, with its key and "
        "Boards: it can hold fewer Boards than a search_jobs company match on the same name. "
        "Opened and closed are counted from 2026-09-25 and company counts from 2026-09-13; "
        "the answer says when a window starts later than asked or turnover covers only part "
        "of it. coverage comparable holds the Boards tracked at the window's start fixed. "
        "measure new reads postings first seen in the trailing 7 days instead of openings."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "companies": {
                "type": "array",
                "items": {"type": "string", "maxLength": 100},
                "maxItems": 10,
            },
            "category": role_families.schema("One job category."),
            "breakdown": {
                "type": "string",
                "enum": list(SPLITS),
                "description": (
                    "Lines by category, seniority level, watched role or company "
                    "(company needs two or more companies; one company's lines are "
                    "by category). Default: company with two or more companies, level with a "
                    "category, else category."
                ),
            },
            "days": {
                "type": "integer",
                "minimum": 1,
                "maximum": 365,
                "default": 30,
                "description": "The window: this many days back from now, unless since is given.",
            },
            "since": {
                "type": "string",
                "maxLength": 10,
                "description": "The window's first day, YYYY-MM-DD; overrides days.",
            },
            "until": {
                "type": "string",
                "maxLength": 10,
                "description": "The window's last day, YYYY-MM-DD; default now.",
            },
            "coverage": {
                "type": "string",
                "enum": ["all", "comparable"],
                "default": "all",
                "description": (
                    "all counts every Board HeadStart has; comparable only the Boards it "
                    "already tracked at the window's start (from 2026-09-13), so Boards found "
                    "later do not move the openings listed."
                ),
            },
            "measure": {
                "type": "string",
                "enum": list(MEASURES),
                "default": "openings",
                "description": (
                    "openings: open postings, with opened and closed; new: postings first "
                    "seen in the trailing 7 days, the site's New this week."
                ),
            },
            "detail": {
                "type": "string",
                "enum": ["concise", "full"],
                "default": "concise",
                "description": "full lists every line with every sized cause.",
            },
        },
        "additionalProperties": False,
    },
    when_to_use=(
        "Use read_trends for how hiring is changing overall, in a category or at named "
        "companies; report postings opened and closed as hiring; say which directory company "
        "each name was read as."
    ),
    answer=answer,
    max_chars=20_000,
    argument_readers={"category": role_families.resolve},
)
