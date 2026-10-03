"""Core data model and normalization helpers for HeadStart."""

from __future__ import annotations

import html
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from headstart.jobs.location import tidy


@dataclass(frozen=True, slots=True)
class Job:
    """A single job posting, normalized to be ATS-agnostic."""

    id: str  # globally unique: "{ats}:{slug}:{native_id}"
    ats: str  # source ATS: "greenhouse" | "lever" | "ashby"
    company: str
    title: str
    location: str | None
    remote: bool | None
    department: str | None
    url: str
    posted_at: str | None  # ISO-8601 if the source provides it
    scraped_at: str  # ISO-8601 UTC, when this run fetched it
    # Optional richer fields — populated only when the source exposes them. Values are kept
    # as the provider phrases them (not normalized across ATSes); None when not available.
    description: str | None = None  # plain text, tags/entities stripped
    experience: str | None = None  # e.g. "3-5 Years", "Mid-Senior level"
    employment_type: str | None = None  # e.g. "Full-time", "Intern", "Contract"
    salary: str | None = None
    # A provider-owned employer-profile key for a marketplace posting, never a cross-source
    # company identity (ADR-0389). None for every ordinary ATS Board.
    marketplace_employer_id: str | None = None
    # The ATS's own requisition id as it states it (ADR-0210), stated by the eight ATSes a served
    # row can be matched across: an Eightfold career site's posting names its backing Board's
    # requisition, and a row on that Board carries the same id. None on every other ATS; the store
    # keeps it only on the paired Boards (`doc_prep.stored_facts`).
    requisition: str | None = None

    def __post_init__(self) -> None:
        # The one point every scraper's Jobs pass through, so the display text is cleaned once
        # rather than per scraper. Each defect was served on 2026-09-24: entities left in 56
        # titles (smartrecruiters, zwayam) and a company (pyjamahr), 3,851 companies with edge
        # whitespace, and locations carrying markup (teamtailor's own feed) or one place per
        # line (50 icims rows, one workday). `object.__setattr__` because the dataclass is frozen.
        # A location also loses its BLANK template tokens, a place listed twice and a country
        # named twice at the end of a place (`tidy`, ADR-0345; 4,347 rows served on 2026-09-29).
        # UTF-8 read once as Latin-1 is repaired here too (`repaired_mojibake`): zoho serves its
        # locations that way at source ("San JosÃ©", "FÃ¨s-Boulemane").
        object.__setattr__(self, "title", _unescaped(self.title))
        object.__setattr__(self, "company", _unescaped(self.company))
        object.__setattr__(self, "location", _location_text(self.location))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def requisition_of(value: Any) -> str | None:
    """A stated requisition id as ``Job.requisition`` stores it: text, trimmed, None when absent.

    ATSes state one as a number (Greenhouse's ``internal_job_id``) or a string (Workday's
    ``R-100``), and two rows match only on equal strings (ADR-0210)."""
    text = "" if value is None else str(value).strip()
    return text or None


_TAGS = re.compile(r"<[^>]+>")
#: Blocks whose content is never posting text. A `<style>` block's rules otherwise survive tag
#: stripping as words: 756 served descriptions opened with CSS on 2026-09-29 (successfactors,
#: cornerstone, wp_job_openings, radancy, avature, zoho; #876). A block holds no opener of its own
#: kind, so an unclosed `<style>` cannot run on to a later block's close and take the posting text
#: between them.
_NON_TEXT_BLOCKS = re.compile(
    r"<(style|script)\b[^>]*>(?:(?!<\1\b).)*?</\1\s*>", re.IGNORECASE | re.DOTALL
)
#: A UTF-8 two-byte sequence read as Latin-1: its lead byte shows as "Ã" or "Â", its continuation
#: byte as one character in U+0080-U+00BF ("é" -> "Ã©", "°" -> "Â°").
_MOJIBAKE = re.compile("[\u00c2\u00c3][\u0080-\u00bf]")
_WS = re.compile(r"\s+")
_LINE_BREAKS = re.compile(r"\s*[\r\n]+\s*")


def repaired_mojibake(value: str | None) -> str | None:
    """``value`` with UTF-8-read-as-Latin-1 reversed ("San JosÃ©" -> "San José"), or unchanged.

    Guarded, because a Latin-1 "Ã" is also real text ("SÃO PAULO"): the repair applies only when
    the whole string re-encodes as Latin-1, that decodes as valid UTF-8, and no "Ã"/"Â" sequence
    is left afterwards. "SÃO" fails the second test — "Ã" before "O" is not a UTF-8 sequence.
    """
    if not value or not _MOJIBAKE.search(value):
        return value
    try:
        repaired = value.encode("latin-1").decode("utf-8")
    except UnicodeError:
        return value
    return value if _MOJIBAKE.search(repaired) else repaired


def _unescaped(value: str | None) -> str | None:
    """``value`` with its entities decoded, mojibake repaired and ends stripped; the inside is
    otherwise left as stated."""
    return value if value is None else repaired_mojibake(html.unescape(value).strip())


def _location_text(value: str | None) -> str | None:
    """A location as display text: tags dropped, entities decoded, a line break read as a list.

    Tags go before entities are decoded, so an escaped ``&lt;Remote&gt;`` survives as text. A line
    break separates places (all 50 icims rows served with one on 2026-09-24), so it becomes the
    ``"; "`` the scrapers already join places with, not a space that runs two places into one.
    The template token ``BLANK`` and a place said twice go last, in :func:`tidy`.
    """
    if not value:
        return None
    text = _LINE_BREAKS.sub("; ", _TAGS.sub(" ", value).strip())
    return tidy(repaired_mojibake(_WS.sub(" ", html.unescape(text)).strip()) or None)


def host_of(url: str | None) -> str:
    """The bare host of a url — no scheme, path, query or trailing slash.

    One definition because the rule has to hold in four places at once: a scraper whose slug *is*
    a host (``personio``, ``zoho``), the liveness prober for that same ATS, and the ledger repair.
    They disagreed once, and it was expensive: discovery had stored raw Common Crawl captures —
    job deep links, some carrying ``utm_*`` — in the ledger's ``url`` column, and Personio's
    ``url()`` appends ``/xml``. On ``.../job/186062?language=de`` that suffix landed *inside* the
    query string, so Personio served the ordinary HTML job page with a 200: the scrape died in
    ``ET.fromstring`` (678 ParseErrors across 19 runs) while the prober, splitting the same wrong
    way, recorded all 312 such boards live with zero jobs.
    """
    return (url or "").split("://", 1)[-1].split("/", 1)[0].split("?", 1)[0]


def http_url(value: Any) -> str:
    """A job link if it is http(s), else "" — rendered like a job with no link.

    A scraped `javascript:` or `data:` URL must not ship as a link (#594). The scheme test is
    `safeUrl`'s in the web UI's app.js; surrounding whitespace is stripped first, as a browser
    strips it from an href. Every Python surface that ships a job's link passes it through here.
    """
    url = str(value or "").strip()
    return url if url.lower().startswith(("http://", "https://")) else ""


def html_to_text(value: str | None) -> str | None:
    """Strip HTML tags/entities from a description blob into clean, single-spaced text.

    Unescapes twice because some sources (e.g. Darwinbox) entity-encode their HTML, so one
    pass leaves the tags as text and the second clears entities inside the stripped content.

    `<style>` and `<script>` blocks go with their content (:data:`_NON_TEXT_BLOCKS`). In markup
    they go before any entity is decoded, so a posting's escaped code sample
    (`&lt;script&gt;init()&lt;/script&gt;`) keeps its text. HTML that is entity-encoded whole
    has no real tag, so it is decoded first and its own blocks go too.
    """
    if not value:
        return None
    if _TAGS.search(value):
        text = html.unescape(_NON_TEXT_BLOCKS.sub(" ", value))
    else:
        text = _NON_TEXT_BLOCKS.sub(" ", html.unescape(value))
    return _WS.sub(" ", html.unescape(_TAGS.sub(" ", text))).strip() or None


def is_remote(location: str | None) -> bool | None:
    """Best-effort remote detection from a location string.

    Returns None when there is no location to judge from.
    """
    if not location:
        return None
    return "remote" in location.lower()


def remote_from_workplace(workplace: str | None, location: str | None) -> bool | None:
    """Whether a posting is remote, from the workplace type its ATS states, else the location.

    Hybrid is neither remote nor on-site, so it is None, as ``ashby._remote`` and
    ``workday._remote_from`` answer it; a location saying so ("Hybrid in Boston, MA") counts.
    A remote location still reads remote under a stated on-site type, as it did before this
    helper (0 such postings on airbnb's 160, 2026-09-28). With
    nothing stated, the location decides (``is_remote``), None when there is none.
    """
    stated = (workplace or "").lower()
    if "hybrid" in stated:
        return None
    if "remote" in stated or is_remote(location):
        return True
    if re.sub(r"[^a-z]", "", stated) in ("onsite", "office", "inoffice"):
        return False
    if location and "hybrid" in location.lower():
        return None
    return is_remote(location)


def epoch_ms_to_iso(ms: int | None) -> str | None:
    """Convert a millisecond Unix timestamp to an ISO-8601 UTC string."""
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat()
