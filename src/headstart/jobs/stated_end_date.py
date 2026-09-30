"""The day a job description says its posting or its applications end, read by rules (ADR-0367).

Some employers write the closing day into the posting itself: PwC's "Job Posting End Date
December 17, 2025", "Applications close: 9 October 2026", "Last Date to Apply: October 1, 2026",
"Deadline: 25/09/2026". A Board that keeps listing such a posting past that day still serves it,
so a reader deserves to hear what the text says. :func:`latest` reads every such statement and
answers the latest day any of them names, with the words it was read from.

A statement is a closing label followed at once by a date: "deadline" alone is a word of every
second skills list ("meet deadlines"), so a label counts only when a date follows it within a
weekday's name and a colon. A date needs its year, since a day with none ("closes on 11th
October") could be either side of today. A numeric date is read only when it cannot be two days:
"25/09/2026" is 25 September, but "5/10/2026" is 10 May in the US and 5 October elsewhere, and
is left unread rather than guessed. Where a text names several days, the latest wins, so a
posting is never said to have ended while one of its own statements says it runs on.
"""

from __future__ import annotations

import re
from datetime import date
from typing import NamedTuple

#: The years a stated end day is read in; any other is a typo or not a closing day.
_YEARS = range(2015, 2036)

_MONTHS = {
    name: number
    for number, names in enumerate(
        (
            ("january", "jan"),
            ("february", "feb"),
            ("march", "mar"),
            ("april", "apr"),
            ("may",),
            ("june", "jun"),
            ("july", "jul"),
            ("august", "aug"),
            ("september", "sept", "sep"),
            ("october", "oct"),
            ("november", "nov"),
            ("december", "dec"),
        ),
        1,
    )
    for name in names
}
_MONTH = "(?:" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?"
_ORDINAL = r"(?:\s?(?:st|nd|rd|th))?"

# Month first ("December 17, 2025"), day first ("17th of December 2025"), year first
# ("2026-12-11", "2026.09.30") and day or month first in digits ("25/09/2026", "09/6/2026").
_DATE = re.compile(
    rf"(?P<md_month>{_MONTH})\s+(?P<md_day>\d{{1,2}}){_ORDINAL},?\s+(?P<md_year>\d{{4}})\b"
    rf"|(?P<dm_day>\d{{1,2}}){_ORDINAL}\s+(?:of\s+)?(?P<dm_month>{_MONTH}),?\s+(?P<dm_year>\d{{4}})\b"
    r"|(?P<iso_year>\d{4})[-./](?P<iso_month>\d{1,2})[-./](?P<iso_day>\d{1,2})\b"
    r"|(?P<a>\d{1,2})[-./](?P<b>\d{1,2})[-./](?P<num_year>\d{4})\b",
    re.IGNORECASE,
)

# What says a posting or its applications end. Each is read only with a date right after it.
_LABEL = re.compile(
    r"(?:job\s+)?posting\s+end\s+date"
    r"|(?:application|applications|apply)\s+(?:deadline|end\s+date|clos(?:e|ing)\s+date)"
    r"|closing\s+date(?:\s+for\s+applications)?"
    r"|deadline(?:\s+(?:to|for)\s+(?:apply|applying|applications?))?"
    r"|applications?\s+(?:will\s+)?close[sd]?(?:\s+on)?"
    r"|(?:job\s+)?(?:posting|position|vacancy|advert(?:isement)?|requisition|role)\s+"
    r"(?:will\s+)?(?:close[sd]?|expires?)(?:\s+on)?"
    r"|last\s+(?:date|day)\s+(?:to|for)\s+(?:apply|applying|applications?)(?:\s+for\s+(?:this\s+)?job)?"
    r"|apply\s+by"
    r"|(?:closes|expires)\s+on",
    re.IGNORECASE,
)

# Between a label and its date: a colon or dash, "is", "on", "by", a weekday.
_GAP = re.compile(
    r"[\s:：\-–—]*(?:(?:is|on|by)\s+)?"
    r"(?:(?:mon|tue(?:s)?|wed(?:nes)?|thu(?:rs)?|fri|sat(?:ur)?|sun)(?:day)?\.?,?\s+)?",
    re.IGNORECASE,
)


class StatedEndDate(NamedTuple):
    """A day the description says its posting or applications end, and the words it said it in."""

    day: date
    said: str


def _day(match: re.Match[str]) -> date | None:
    """The day ``match`` (of :data:`_DATE`) names, or None when it names none for certain."""
    found = match.groupdict()
    if found["md_month"]:
        year, month, day = (
            found["md_year"],
            _MONTHS[found["md_month"].lower().rstrip(".")],
            found["md_day"],
        )
    elif found["dm_month"]:
        year, month, day = (
            found["dm_year"],
            _MONTHS[found["dm_month"].lower().rstrip(".")],
            found["dm_day"],
        )
    elif found["iso_year"]:
        year, month, day = found["iso_year"], found["iso_month"], found["iso_day"]
    else:
        a, b = int(found["a"]), int(found["b"])
        if a <= 12 and b <= 12 and a != b:
            return None  # 5/10 is 10 May in the US and 5 October elsewhere
        month, day = (a, b) if a <= 12 else (b, a)
        year = found["num_year"]
    try:
        read = date(int(year), int(month), int(day))
    except ValueError:
        return None
    return read if read.year in _YEARS else None


def latest(description: str | None) -> StatedEndDate | None:
    """The latest day ``description`` says its posting or applications end, or None when it says
    no such day with its year."""
    stated: list[StatedEndDate] = []
    for label in _LABEL.finditer(description or ""):
        gap = _GAP.match(description, label.end())
        found = _DATE.match(description, gap.end() if gap else label.end())
        if not found or (day := _day(found)) is None:
            continue
        said = " ".join(description[label.start() : found.end()].split())
        stated.append(StatedEndDate(day, said))
    return max(stated, default=None)
