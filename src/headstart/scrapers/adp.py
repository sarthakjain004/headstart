"""ADP Workforce Now career-center scraper (``workforcenow.adp.com``).

A Board is one **career center**: two query values on one fixed host, the client GUID ``cid`` and
the career-center id ``ccId`` (``recruitment.html?cid=…&ccId=…``). The slug is ``{cid}/{ccId}``.
``cid`` is case-sensitive — an uppercased real GUID answers 404 — and ``ccId`` is an address, not
only a filter: of 8 hiring clients measured with more than one center, 4 had disjoint posting
sets, 3 had a center wholly inside the default ``19000101_000001`` and 1 overlapped partly, so
one posting can be served by two Boards of one employer (3.4% of pooled clients have several).

Everything below was measured 2026-09-23 against the live host and is written up in
``docs/adp/2026-09-23_careercenter-measurement.md`` (ADR-0180). Upstream
``kalil0321/ats-scrapers`` got five of these wrong.

**The rate limit shapes the scraper.** F5 BigIP refuses the 201st request in a fixed 60-second
window, counted across every tenant, with a bare 429 and no Retry-After (rested runs at 5 and 8
req/s were refused on exactly request #201; 450 requests at 3 req/s ran clean). So every request
goes through one process-wide :class:`_Pacer` at 0.4 s spacing, and a 429 anyway rests the whole
process through the window and retries; a Board still refused is marked truncated, never
returned short as complete.

**`lang` is a filter, not part of the identity.** A posting is listed only under its own language,
and a language the center does not use answers ``{"jobRequisitions": []}`` — no ``meta`` at all,
byte-identical to an empty Board. The center's languages come from content-links' ``Locale``
list (see :meth:`ADPScraper._languages`); each is walked and the rows merged by
``ExternalJobID``, English first, since a translated posting keeps its id across languages. Of
155 Boards measured, 2.9% of postings were ``fr_CA``/``es_US``; they are scraped and held out of
the English-only index downstream, as CLAUDE.md scopes it.

**The listing walk.** ``$top`` clamps at 20 silently; ``$skip`` is **1-based**
(``meta.startSequence`` echoes it, and ``$skip=0`` drops a row — upstream's ``0, 19, 39, …``
walk reads one row twice); ``meta.totalNumber`` is the terminator (460 of 460 read twice on the
largest Board measured).

**The detail** (``…/job-requisitions/{ExternalJobID}``) is the listing row plus
``requisitionDescription`` and nothing else (120 of 120), so the tech gate is exact and the
ADR-0048 skip blanks nothing. It needs the posting's own ``lang``; under any other, or for a
closed id, it answers 200 with a ~1.2 KB skeleton — no title, no description — counted as a loss.

Not mapped, on purpose: ``JobClass`` (50.5%: "Professional", "Clerical" — a class, not a
department; no department exists on either surface), ``clientRequisitionID`` (the tenant's own
number, not an address), ``InternalPostingFlag`` (false on 2,069 of 2,069 rows). No experience
field exists. The User-Agent does not matter (``headstart/0.1``, curl's, python-requests' and a
browser's all 200).
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any
from urllib.parse import urlencode

from headstart import company_name, http, salary
from headstart.models import Job, html_to_text, is_remote
from headstart.scrapers.base import USER_AGENT, BaseScraper

_HOST = "https://workforcenow.adp.com"
_PAGE = f"{_HOST}/mascsr/default/mdf/recruitment/recruitment.html"
_API = f"{_HOST}/mascsr/default/careercenter/public/events/staffing"
_LISTING = f"{_API}/v1/job-requisitions"
_LOCALES = f"{_API}/v1/content-links/career-center"
_CLIENT = f"{_API}/client-features"

#: The page size ADP serves, whatever is asked: `$top` clamps at 20 silently (47 of 195 seed
#: Boards returned 20 rows when asked for 100).
_PAGE_SIZE = 20
#: Our ceiling on one language's walk (10,000 postings; the largest Board measured was 460).
_MAX_PAGES = 500
#: The four languages postings were observed in (2,833 / 602 / 89 / 14 postings over 155 Boards),
#: English first so a translated posting keeps its English version. Walked only when a center's
#: content-links `Locale` list is empty; otherwise that list names the languages.
_LANGS = ("en_US", "en_CA", "fr_CA", "es_US")

#: The host refuses the 201st request in a fixed 60-second window, across tenants, with a bare
#: `429` from F5 BigIP and no Retry-After. Request starts are spaced process-wide at 0.4 s
#: (150/minute, 25% under the budget); a 429 anyway (another process on the same IP) rests every
#: request for the window and retries, `_TRIES` attempts in all.
_SPACING_S = 0.4
_WINDOW_S = 60.0
_TRIES = 3
#: The fetch seam's own retry ladder, minus 429: its ~5 s of backoff cannot outlast a 60 s window,
#: and three quick attempts would spend the budget the rest of the process is pacing against.
_RETRY_ON = http.TRANSIENT - {429}


class _RateLimited(Exception):
    """Still refused after resting through the window `_TRIES - 1` times."""


class _Pacer:
    """Spaces request starts to one host across every thread and event loop in the process.

    `harvest` scrapes many Boards concurrently in one process, so a delay kept per Board or per
    scraper instance multiplies by the Board count. This holds one next-free slot under a lock:
    :meth:`reserve` claims the next slot and says how long to wait for it, which the sync path
    sleeps and the async path awaits, so both paths draw from the same budget.

    A slot claimed before a :meth:`rest` would still fire into the refused window, so a caller
    that wakes while :meth:`resting` claims a fresh slot instead — which a rest has already put
    past the window's end.
    """

    def __init__(self, spacing: float) -> None:
        self.spacing = spacing
        self._lock = threading.Lock()
        self._next = 0.0
        self._rest_until = 0.0

    def reserve(self) -> float:
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next)
            self._next = start + self.spacing
            return start - now

    def rest(self, seconds: float) -> None:
        """Hold every request until ``seconds`` from now — a refused window's remainder."""
        with self._lock:
            until = time.monotonic() + seconds
            self._next = max(self._next, until)
            self._rest_until = max(self._rest_until, until)

    def resting(self) -> bool:
        with self._lock:
            return time.monotonic() < self._rest_until

    def wait(self) -> None:
        """Sleep until this caller's slot, re-claiming one if a rest began meanwhile."""
        while True:
            time.sleep(self.reserve())
            if not self.resting():
                return

    async def wait_async(self) -> None:
        while True:
            await asyncio.sleep(self.reserve())
            if not self.resting():
                return


_PACER = _Pacer(_SPACING_S)


def _query(cid: str, cc_id: str, lang: str | None = None, **extra: Any) -> str:
    query: dict[str, Any] = {"cid": cid, "ccId": cc_id}
    if lang:
        query.update(lang=lang, locale=lang)
    return urlencode({**query, **extra})


def locales_url(cid: str, cc_id: str) -> str:
    """The career center's content-links: its languages and whether it is published."""
    return f"{_LOCALES}?{_query(cid, cc_id)}"


def listing_url(
    cid: str, cc_id: str, lang: str, skip: int, top: int = _PAGE_SIZE
) -> str:
    """One page of the listing in one language; ``skip`` is 1-based."""
    return f"{_LISTING}?{_query(cid, cc_id, lang, **{'$top': top, '$skip': skip})}"


def _fields(group: dict, kind: str, code: str, key: str) -> list[Any]:
    """Every ``key`` value of the ``kind`` fields named ``code`` in an ADP ``customFieldGroup``
    — the one shape all of its name/value data comes in (``stringFields``, ``codeFields``,
    ``indicatorFields``, each entry keyed by ``nameCode.codeValue``)."""
    return [
        f.get(key)
        for f in group.get(kind) or []
        if (f.get("nameCode") or {}).get("codeValue") == code
    ]


def _meta_group(body: dict) -> dict:
    return (body.get("meta") or {}).get("customFieldGroup") or {}


def _strings(group: dict, code: str) -> list[str]:
    """The non-empty, stripped `stringFields` values named ``code``."""
    return [
        v.strip()
        for v in _fields(group, "stringFields", code, "stringValue")
        if isinstance(v, str) and v.strip()
    ]


def languages_of(content_links: dict) -> list[str]:
    """The languages a content-links body names, English first — or the four observed, when it
    names none (the `Locale` list covered the posting languages on 45 of 45 Boards checked)."""
    listed = _strings(_meta_group(content_links), "Locale")
    if not listed:
        return list(_LANGS)
    # English first, so a posting translated into French or Spanish is kept in its English
    # version. Between two English variants (both posted on 2 of 155 Boards, the same ids in
    # each where they overlap) the order is alphabetical — `en_CA` before `en_US` — which is
    # arbitrary but stable: both render the same posting, and the detail is asked in whichever
    # language won.
    return sorted(dict.fromkeys(listed), key=lambda x: (not x.startswith("en"), x))


def is_published(content_links: dict) -> bool:
    """`PublishedIndicator`: true on every real career center measured (hiring or empty), false
    for a `ccId` the client does not have — which otherwise answers exactly like an empty one."""
    indicators = _fields(
        _meta_group(content_links),
        "indicatorFields",
        "PublishedIndicator",
        "indicatorValue",
    )
    return True in indicators


def listing_total(listing: dict) -> int:
    """`meta.totalNumber`, or 0 for the metaless envelope a language with no postings returns."""
    return int((listing.get("meta") or {}).get("totalNumber") or 0)


def _string_field(row: dict, code: str) -> str | None:
    return next(iter(_strings(row.get("customFieldGroup") or {}, code)), None)


#: `SalaryType` codes -> the period phrase `salary.from_field` reads. The two observed
#: (HR 668, AN 412 of 1,080 paid rows). Anything else yields no salary: annual is the parser's
#: default, so an unmapped daily or monthly figure would be served at the wrong scale.
_PERIODS = {"HR": "per-hour", "AN": "per-year"}


def _code_field(row: dict, code: str) -> str | None:
    values = _fields(row.get("customFieldGroup") or {}, "codeFields", code, "codeValue")
    return values[0] if values else None


def _digits(value: float) -> str:
    """A float as digits, never `:g` (which writes 1,200,000 as `1.2e+06`)."""
    return str(int(value)) if float(value).is_integer() else f"{value:.2f}"


def _ext_id(row: dict) -> str | None:
    """The posting's native id: `ExternalJobID`, all-digit and unique within its Board on 2,069
    of 2,069 rows. Not `itemID`, which carries `:` on 8 rows (`HRB:2048292:11346772`) and would
    split wrong in `board_identity.board_of`."""
    return _string_field(row, "ExternalJobID")


def _location(row: dict) -> str | None:
    places: list[str] = []
    for loc in row.get("requisitionLocations") or []:
        place = ((loc.get("nameCode") or {}).get("shortName") or "").strip()
        if place and place not in places:
            places.append(place)
    return "; ".join(places) or None


class ADPScraper(BaseScraper):
    ats = "adp"
    has_detail_pass = True  # per-Job fetch fills `description` (ADR-0050)
    #: Width is irrelevant under a process-wide 0.4 s pacer; this only bounds the threads (or
    #: streams) queued on it, at the 16 icims, zwayam, oracle and pyjamahr run.
    detail_workers = 16
    # scraper: f"{_PAGE}?cid={cid}&ccId={ccId}&lang={lang}&jobId={ExternalJobID}". Rendered in
    # Chromium 2026-09-23 it shows the posting (the page itself fetches the detail by that id),
    # with or without `source=`; the `lang` must be the posting's own — the detail answers an
    # empty skeleton under any other.
    url_shape = (
        r"https://workforcenow\.adp\.com/mascsr/default/mdf/recruitment/recruitment\.html"
        r"\?cid=[0-9a-f-]{36}&ccId=\d+_\d+&lang=[a-z]{2}_[A-Z]{2}&jobId=\d+"
    )

    #: Process-wide, shared by every instance (see `_Pacer`).
    pacer = _PACER

    def __init__(self, slug: str, company: str | None = None) -> None:
        super().__init__(slug, company)
        self.cid, self.cc_id = slug.split("/", 1)

    def url(self) -> str:
        return f"{_PAGE}?{urlencode({'cid': self.cid, 'ccId': self.cc_id})}"

    def _paced_get(self, url: str, *, tries: int = _TRIES, **kwargs: Any) -> Any:
        """One GET through the pacer; a 429 rests the whole process through the window and, for
        up to ``tries`` attempts in all, asks again. The company-name lookup takes one: a
        display name is not worth a second window, but its 429 still rests everyone else."""
        for _ in range(tries):
            self.pacer.wait()
            response = self._fetch(
                "GET",
                url,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=30,
                retry_on=_RETRY_ON,
                **kwargs,
            )
            if response.status_code != 429:
                response.raise_for_status()
                return json.loads(response.text)
            self.pacer.rest(_WINDOW_S)
        raise _RateLimited(url)

    async def _paced_get_async(self, session: Any, url: str) -> Any:
        for _ in range(_TRIES):
            await self.pacer.wait_async()
            response = await self._fetch_async(
                session,
                "GET",
                url,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=30,
                retry_on=_RETRY_ON,
            )
            if response.status_code != 429:
                response.raise_for_status()
                return json.loads(response.text)
            self.pacer.rest(_WINDOW_S)
        raise _RateLimited(url)

    def _languages(self) -> list[str]:
        """The languages this career center posts in, English first.

        `lang` is a filter, not an address: a posting is listed only under its own language, and
        a language the center does not use answers the same empty envelope an empty Board does.
        content-links' `Locale` list names the center's languages — it covered the posting
        languages on 45 of 45 Boards checked (26 of them multi-language or not `en_US`), and does
        not depend on the `lang` asked. The page supports `de_DE` and `ko_KR` too; neither held a
        posting on those 45.
        """
        return languages_of(self._paced_get(locales_url(self.cid, self.cc_id)))

    def _walk(self, lang: str) -> list[dict]:
        """Every listing row in one language: pages of 20 from the 1-based `$skip=1` until
        `meta.totalNumber` is read (460 of 460 on the largest Board measured, twice).

        Progress is counted in **unique** `ExternalJobID`s, not rows, so a posting that moves
        between pages mid-walk cannot count twice and end the walk early; every "read N of M"
        this reports is that same count.
        """
        rows: list[dict] = []
        ids: set[str | None] = set()
        total: int | None = None
        skip = 1
        try:
            for _ in range(_MAX_PAGES):
                data = self._paced_get(listing_url(self.cid, self.cc_id, lang, skip))
                page = data.get("jobRequisitions") or []
                total = (data.get("meta") or {}).get("totalNumber", total)
                rows.extend(page)
                ids.update(_ext_id(r) for r in page)
                if not page or not total or len(ids) >= total:
                    break
                skip += len(page)
            else:
                self.mark_truncated(
                    f"{lang}: hit the {_MAX_PAGES}-page cap at {len(ids)} of {total} postings"
                )
        except _RateLimited:
            self.mark_truncated(
                f"{lang}: still rate-limited after {_TRIES} windows at {len(ids)} of "
                f"{total if total is not None else 'unknown'} postings — the rest unread"
            )
            return rows
        if rows and not total:
            # Every page with rows measured stated a positive total; rows without one (absent or
            # 0) leave no way to know the list is whole, so it is not reported as whole.
            self.mark_truncated(
                f"{lang}: rows with no stated total — completeness unknown"
            )
        elif total and len(ids) < total:
            self.mark_truncated_unless_negligible(
                len(ids),
                total,
                f"{lang}: read {len(ids)} of {total} postings — the rest is unread, not absent",
            )
        return rows

    def fetch_raw(self) -> Any:
        merged: dict[str, dict] = {}
        for lang in self._languages():
            for row in self._walk(lang):
                ext = _ext_id(row)
                if ext and ext not in merged:
                    merged[ext] = {**row, "_lang": lang}
        rows = list(merged.values())
        # The gate is exact: no department on either surface (`HomeDepartment` empty on 2,069 of
        # 2,069 rows and 120 of 120 details) and the detail overrides nothing — it adds
        # `requisitionDescription` and nothing else on 120 of 120 — so the description store's
        # skip (ADR-0048) blanks no other field either.
        wanted = [
            r
            for r in self.tech_detail_wanted(rows, lambda r: r.get("requisitionTitle"))
            if self.needs_detail(_ext_id(r))
        ]
        details: dict[str, dict] = {}
        if wanted:
            if self.async_fanout_enabled():
                fetched = self.fan_out_async(wanted, self._detail_async)
            else:
                fetched = self.fan_out(
                    wanted, self._detail, workers=self.detail_workers
                )
            self.report_detail_gaps(fetched, "detail payloads")
            details = {_ext_id(r): d for r, d in zip(wanted, fetched) if d}
        return {"rows": rows, "details": details}

    def resolve_company(self) -> None:
        """The employer, from ``client-features``' ``ClientName`` — one request per Board.

        Nothing a browser renders names the employer: the title is "Recruitment" on every
        career center, there is no og: tag or JSON-LD, and no posting field carries it (5 centers
        rendered through board, job and apply steps, 2026-09-23). ``ClientName`` is ADP's own
        client record, present on 120 of 120 centers sampled. It is the payroll client's name,
        often the legal entity: of 60 whose seed-list brand name was known, 18 read the same,
        16 differ only by a suffix or case ("CHM Hotels Inc", "VETPRIDE SERVICES INC"), and 26
        name a parent or legal entity ("KERRIDGE COMMERCIAL SYSTEMS CORP" for Klipboard). It is
        served as stated — the company's own claim, like every other name source (ADR-0114).

        One attempt that can never wall the host, as the base method's title fetch — a 429 still
        rests the shared pacer for everyone, but is not retried; any failure leaves the slug,
        which is the floor.
        """
        if not company_name.looks_like_slug(self.company):
            return
        try:
            body = self._paced_get(
                f"{_CLIENT}?{_query(self.cid, self.cc_id, 'en_US')}",
                tries=1,
                attempts=1,
                marks_wall=False,
            )
        except Exception:  # noqa: BLE001 - a display name is never worth failing a Board for
            return
        stated = next(iter(_strings(_meta_group(body), "ClientName")), "")
        name = company_name.from_title(self.ats, stated, self.slug)
        if name:
            self.company = name

    def _detail_url(self, row: dict) -> str:
        return f"{_LISTING}/{_ext_id(row)}?{_query(self.cid, self.cc_id, row['_lang'])}"

    def _detail_of(self, body: Any) -> dict | None:
        # A closed or unknown id — or the right id under the wrong `lang` — answers 200 with a
        # ~1.2 KB skeleton: no title, no description. A silent empty, counted as one.
        if not body.get("requisitionTitle"):
            self.note_detail_loss("no requisitionTitle on a 200")
            return None
        return body

    def _detail(self, row: dict) -> dict | None:
        try:
            return self._detail_of(self._paced_get(self._detail_url(row)))
        except _RateLimited:
            self.note_detail_loss(f"HTTP 429 after {_TRIES} windows")
        except http.RequestsError as exc:
            self.note_detail_exception(exc)
        return None

    async def _detail_async(self, session: Any, row: dict) -> dict | None:
        try:
            return self._detail_of(
                await self._paced_get_async(session, self._detail_url(row))
            )
        except _RateLimited:
            self.note_detail_loss(f"HTTP 429 after {_TRIES} windows")
        except http.RequestsError as exc:
            self.note_detail_exception(exc)
        return None

    def job_url(self, native_id: str, lang: str) -> str:
        query = {"cid": self.cid, "ccId": self.cc_id, "lang": lang, "jobId": native_id}
        return f"{_PAGE}?{urlencode(query)}"

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        details = raw.get("details") or {}
        jobs: list[Job] = []
        for row in raw.get("rows") or []:
            ext = _ext_id(row)
            detail = details.get(ext) or {}
            location = _location(row)
            jobs.append(
                Job(
                    id=self.job_id(ext),
                    ats=self.ats,
                    company=self.company,
                    title=(row.get("requisitionTitle") or "").strip(),
                    location=location,
                    remote=is_remote(location),
                    department=None,
                    url=self.job_url(ext, row["_lang"]),
                    posted_at=row.get("postDate"),
                    scraped_at=scraped_at,
                    description=html_to_text(detail.get("requisitionDescription")),
                    employment_type=(
                        (row.get("workLevelCode") or {}).get("shortName") or ""
                    ).strip()
                    or None,
                    salary=self._salary_field(row),
                )
            )
        return jobs

    def _salary_field(self, raw: Any) -> str | None:
        """``Job.salary`` from the listing row's ``payGradeRange`` and ``SalaryType``.

        ``"19-20.50 USD per-hour"``, built by ``salary.to_field`` — the spelling
        ``salary.from_field`` reads for adp. Two shapes need care (measured over 1,080 paid
        rows): an "Up to X" range arrives as **min 0.0**
        (158 rows), a lone ceiling that no spelling makes the parser read as a maximum, so it is
        refused; "X Onwards" arrives as **max 0.0** (1 row), a real floor, emitted alone.
        """
        pay = raw.get("payGradeRange") or {}
        period = _PERIODS.get(_code_field(raw, "SalaryType") or "")
        lo = (pay.get("minimumRate") or {}).get("amountValue")
        hi = (pay.get("maximumRate") or {}).get("amountValue")
        if period is None or not lo:
            return None
        currency = (pay.get("minimumRate") or {}).get("currencyCode") or ""
        return salary.to_field(
            _digits(lo), _digits(hi) if hi else None, currency, period
        )
