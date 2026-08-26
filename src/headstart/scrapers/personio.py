"""Personio job-board scraper ({slug}.jobs.personio.{de|com}/xml feed).

Personio publishes a public XML feed of openings. The careers host's TLD varies per tenant
(`.de`, `.com`, ...), and the pool already stores each tenant's full host, so the slug here is
that host and the feed is ``https://{host}/xml``. Each ``<position>`` carries the title, office
(location), department, employment type, seniority, salary, and one or more ``<jobDescription>``
sections (CDATA HTML) that we concatenate into the description.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

from headstart import http
from headstart.experience import from_field
from headstart.models import Job, host_of, html_to_text, is_remote
from headstart.scrapers.base import USER_AGENT, BaseScraper

#: Every redirect status personio's edge could answer with. A tenant that is still on the ATS
#: never redirects its own feed (0 of 600 live Boards sampled 2026-08-26), so any of these
#: means the Board is not where the ledger says it is.
_REDIRECTS = frozenset({301, 302, 303, 307, 308})


def _text(pos: ET.Element, tag: str) -> str | None:
    e = pos.find(tag)
    return e.text.strip() if e is not None and e.text and e.text.strip() else None


def _location(pos: ET.Element) -> str | None:
    """`<office>` plus every `<additionalOffices>/office` entry, joined into one filterable string.

    `<additionalOffices>` is a sibling of `<office>` inside the same `<position>` that nothing
    previously read: 274 of 1,101 positions (24.89%) in a live 149-Board sample (2026-08-25,
    matching the 18.31%/771-Board audit this fix is based on — sample variance, same real
    defect) carry it, dropping every string it holds. On a real subset `<office>` alone is a
    localized placeless marker ("Home Office", "Mobil", "Hybrid", "standortunabhängig", ...)
    while the position's actual city sits only in the dropped element — e.g. live 2026-08-25,
    `interlead.jobs.personio.de` serves a position with `office="Home Office"` and
    `additionalOffices=["Bremen"]`; today's `location` is just "Home Office" and Bremen is
    unfindable by any place filter.

    This is the same shape recruitee's `_is_remote_sentinel` fixed for `location`/`city`, but
    recruitee could safely REPLACE its marker because `city` is one authoritative field.
    Personio's `additionalOffices` is a LIST of the position's OTHER real offices, so `<office>`
    disagreeing with it is not proof of a marker — a position genuinely posted across several
    cities (`office="Leipzig"`, `additionalOffices=["Dubai", "Hybrid"]`, live sample) disagrees
    too. Measured directly on the same sample: comparing `<office>` against `<additionalOffices>`
    fires on 267/274 (97.4%) of positions that carry the sibling element, which does not
    discriminate a marker from an ordinary additional office — so classifying and
    discarding/reordering on it would misclassify genuine multi-office postings on nearly every
    occurrence. Joining instead (deduplicated, primary office first) recovers every dropped place
    with zero data lost (measured: 0 positions where the old string vanished from the new one), at
    the cost of a marker word occasionally riding alongside the real place it used to hide.
    """
    office = _text(pos, "office")
    add_block = pos.find("additionalOffices")
    additional = (
        [
            o.text.strip()
            for o in add_block.findall("office")
            if o is not None and o.text and o.text.strip()
        ]
        if add_block is not None
        else []
    )
    seen: set[str] = set()
    parts: list[str] = []
    for candidate in ([office] if office else []) + additional:
        key = candidate.lower()
        if key not in seen:
            seen.add(key)
            parts.append(candidate)
    return ", ".join(parts) if parts else None


def _experience(pos: ET.Element) -> str | None:
    """Prefer the native `<yearsOfExperience>` range over the coarse `<seniority>` word, falling
    back to `<seniority>` only when the range doesn't parse as a Tier-1 field.

    `<seniority>` is a four-value enum (`experienced`/`entry-level`/`student`/`executive`)
    populated on ~100% of positions, so `seniority or yearsOfExperience` wins the `or` chain
    almost unconditionally and discards `<yearsOfExperience>`'s real numeric range
    ("2-5"/"1-2"/"5-7"/...) on essentially every position that has one. Measured through the
    real `headstart.experience.extract()` cascade over a live 149-Board / 1,101-position sample
    (2026-08-25): preferring the range changes 54.95% of answers, corrects a `min_years` that was
    too high on 36.33% of positions (e.g. "experienced" -> floor 5 when the field says "1-2"), and
    a real `max_years` bound appears on 54.04% that never had one. 0 positions lose their answer.

    A naive swap (`yearsOfExperience or seniority`) loses positions where `from_field` cannot
    parse personio's own open-ended spellings ("lt-1", "gt-15" — no leading digit) — those would
    fall through to `None` where seniority at least gave a floor. Testing parseability here keeps
    the fallback, so nothing already served is lost.
    """
    yoe = _text(pos, "yearsOfExperience")
    if yoe is not None and from_field(yoe) is not None:
        return yoe
    return _text(pos, "seniority")


def _salary(pos: ET.Element) -> str | None:
    """A real, structured `<salaryInformation>` element (`<min>`/`<max>`/`<currencyCode>`/
    `<type>`) formatted as "50000-70000 EUR yearly" — the same RANGE + CODE + interval shape
    `_field_range_currency_interval` already parses for lever/recruitee/teamtailor/ashby. Found
    via direct API inspection (2026-08-22): 13.4% of positions carry this structured element; the
    scraper previously read only `<salaryInformation>`'s own direct text via `_text()`, which is
    always empty when the element is structured like this (`.text` is the text *before* the first
    child, and Personio never puts any there) — so this was a real, silent Tier-1 dead end, not a
    genuinely-absent field.

    `<type>`'s real values ("yearly"/"monthly"/"hourly" — confirmed, 80 real boards) are passed
    through unmapped, on purpose: `_period_multiplier`'s own hardcoded phrase checks already
    recognize bare "monthly"/"hourly" as substrings, and "yearly" already gets the correct
    multiplier (1) from that function's own annual default — verified directly, not assumed, that
    `from_field()` on each of these three raw strings already returns the correct span with no
    mapping at all. An earlier version of this function mapped `<type>` to `_period_multiplier_
    structured`'s bare-word set ("month"/"hour"/etc.) on the assumption the "-ly" suffix would
    break word-boundary matching — true for that function, irrelevant in practice, since
    `_period_multiplier` runs first and already handles every real value; code review caught the
    mapping was speculative generality (3 of 5 entries provably redundant, the other 2 —
    "daily"/"weekly" — unevidenced in any real sample) and it was removed. An unrecognized
    `<type>` (or a genuinely absent one) safely defaults to the annual multiplier and gets caught
    by `_bounded`'s plausibility floor if that default is wrong for the real value, same as any
    other unrecognized period marker elsewhere in this module — a decline, not a silent
    corruption.

    `min` is present whenever `<salaryInformation>` carries any content in every real element
    checked (0 max-only/no-min cases across 94 real structured elements, live-checked 2026-08-22
    specifically because ashby.md flagged this exact shape — a ceiling with no stated floor — as
    an unresolved risk for any future caller of `_field_range_currency_interval`); `max` is
    sometimes absent (a fixed-rate or floor-only figure) — left as a bare single value for
    `_field_range_currency_interval`'s own `_SINGLE_NUM` fallback to handle, not guessed at as a
    range.

    `lo`/`hi` are checked with `is not None`, not truthiness — XML text is always a string here
    (`findtext` never returns a raw numeric type), so a real "0.00" would already be truthy and
    this specific ashby-class bug can't occur (verified: 0 zero-valued and 0 non-string min/max
    across a live 80-board check) — but the explicit check costs nothing and removes any doubt for
    a future reader, given this exact shape has already caused a real bug once in this module."""
    sal = pos.find("salaryInformation")
    if sal is None:
        return None
    lo, hi = sal.findtext("min"), sal.findtext("max")
    if lo is None and hi is None:
        return None
    span = (
        f"{lo}-{hi}"
        if lo is not None and hi is not None
        else (lo if lo is not None else hi)
    )
    code = sal.findtext("currencyCode")
    period = sal.findtext("type")
    return " ".join(x for x in (span, code, period) if x)


def _description(pos: ET.Element) -> str | None:
    """Concatenate the <jobDescription> sections (name + CDATA HTML value) into clean text."""
    block = pos.find("jobDescriptions")
    if block is None:
        return None
    parts = []
    for d in block.findall("jobDescription"):
        name = (d.findtext("name") or "").strip()
        value = (d.findtext("value") or "").strip()
        if value:
            parts.append(f"{name}\n{value}" if name else value)
    return html_to_text("\n\n".join(parts)) if parts else None


class PersonioScraper(BaseScraper):
    ats = "personio"

    @staticmethod
    def slug_from(tenant: str, url: str) -> str:
        # Host only — the ledger's url is not always the board. Discovery stored the raw capture
        # for host-shaped ATSes, so 634 personio rows carry a job deep link with tracking params.
        # A path alone 404s honestly, but a *query* is silent: `url()` appends /xml, which on
        # `...?language=de` lands inside the query string, so Personio serves the ordinary HTML
        # job page with a 200 and the XML parse dies on it — 678 ParseErrors over 19 runs, and a
        # liveness prober sharing this split marked all 312 such boards live with 0 positions.
        host = host_of(url)
        return host if "personio" in host else f"{tenant}.jobs.personio.de"

    @property
    def _tenant(self) -> str:
        """The slug is the whole host; the tenant is its first label."""
        return self.slug.split(".")[0]

    def board_key(self) -> str:
        # Jobs are ided by tenant, not by the host-shaped slug, so the inherited `{ats}:{slug}`
        # would never match `board_of` of our own rows and the prune would evict every personio
        # row as off-Board on every run (ADR-0023). Both sides read `_tenant` so they can't drift.
        return f"{self.ats}:{self._tenant}"

    def url(self) -> str:
        # no ?language= — that returns the listing but empties descriptions for non-English
        # tenants; the bare feed gives each posting in the company's own language.
        return f"https://{self.slug}/xml"

    def fetch_raw(self) -> Any:
        """The tenant's XML feed — refusing to follow a redirect off the Board host.

        A tenant that has left personio does not 404. `https://{host}/xml` answers **307 ->
        https://personio.com**, and the marketing site there is behind Vercel bot mitigation which
        answers **429** to our User-Agent. Following that redirect is what produced every terminal
        `HTTP Error 429` this ATS has ever reported: measured live 2026-08-26, all 22 Boards that
        failed that way across runs 32936269675 and 32942748996 redirect to the marketing site,
        against 8 of 600 randomly sampled live Boards (1.33%) and 0 of 600 that redirect anywhere
        else or redirect and still serve a feed.

        The 429 is keyed on the header, not the client — same IP and same second, a Chrome
        User-Agent gets 200 and ours gets `x-vercel-mitigated: challenge`. That is why ADR-0063's
        spare egress could not rescue these Boards and is no longer asked to: driven against them
        the real scraper rotated through three verified-distinct WARP addresses and was refused by
        every one.

        Reported in the shape :func:`~headstart.ingest.board_failures.is_gone` recognises, the way
        lever reports a slug that is on no Lever board. That matters beyond the message: a 429
        deliberately never ages a Board (ADR-0058), so read as a rate limit these departed tenants
        stayed in the slice failing every run forever; read as gone, the existing quarantine
        retires them after five agreeing runs.
        """
        response = http.fetch(
            "GET",
            self.url(),
            headers={"User-Agent": USER_AGENT, "Accept": "application/json, text/html"},
            timeout=30,
            allow_redirects=False,
            **self._egress(),
        )
        if response.status_code in _REDIRECTS:
            raise http.RequestsError(
                f"HTTP Error 404: no personio board for {self.slug} — /xml redirects to "
                f"{response.headers.get('location') or '(no location)'}"
            )
        response.raise_for_status()
        # personio serves XML; encode back to bytes so ElementTree accepts the encoding decl.
        return ET.fromstring(response.text.encode("utf-8"))

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        tenant = self._tenant
        jobs: list[Job] = []
        for pos in raw.findall("position"):
            jid = _text(pos, "id")
            if not jid:
                continue
            office = _text(pos, "office")
            etype, sched = _text(pos, "employmentType"), _text(pos, "schedule")
            jobs.append(
                Job(
                    id=f"{self.ats}:{tenant}:{jid}",
                    ats=self.ats,
                    company=_text(pos, "subcompany") or self.company,
                    title=_text(pos, "name") or "",
                    location=_location(pos),
                    # Deliberately from the bare `<office>`, not the joined location: a marker
                    # like "Home Office" carries no "remote" substring today, and joining in
                    # `additionalOffices` (real places) must not change that verdict either way.
                    remote=is_remote(office),
                    department=_text(pos, "department"),
                    url=f"https://{self.slug}/job/{jid}",
                    posted_at=_text(pos, "createdAt"),
                    scraped_at=scraped_at,
                    description=_description(pos),
                    experience=_experience(pos),
                    employment_type=" / ".join(x for x in (etype, sched) if x) or None,
                    salary=_salary(pos),
                )
            )
        return jobs
