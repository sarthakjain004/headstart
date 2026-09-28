"""ByteDance's own in-house careers system (jobs.bytedance.com) — a Single source scraper, not a
platform other companies rent (ADR-0139, CONTEXT.md's glossary). There is exactly one tenant, so
``slug`` is fixed to ``"jobs.bytedance.com"``, never discovered. The Board is read from ByteDance's
``supplier`` search API, the backend ``tiktok`` reads too, so everything but ByteDance's host,
header value and links lives in :mod:`headstart.scrapers.supplier_search` (ADR-0198).

Adapted with reference to jobhive's ByteDance scraper (kalil0321/ats-scrapers, MIT); every fact
below was re-measured live against the real endpoint rather than trusted from it, on 2026-09-11
unless it says otherwise — full numbers in ``docs/bytedance/2026-09-11_api-measurement.md``.

**The public browse page has moved off ``jobs.bytedance.com``, but the API has not.**
``GET https://jobs.bytedance.com/en/position`` answers a bare 302 to
``https://joinbytedance.com/search`` — a Next.js app built as ``bd-career-site`` and served from
ByteDance's own Feishu CDN (path segment ``atsx-throne``, an internal ATS codename). That app is
fully client-rendered: the static HTML carries no job data at all, only ``<script>`` bundle tags.
The listing and filter calls it makes are cross-origin, back to ``jobs.bytedance.com`` — found by
downloading those bundles and reading the minified API client (module ``61215`` in
``8321-22536180820e7ed8.js``), not by guessing.

**The Board is ``website-path: en``.** Without the header the host answers HTTP 400 ``invalid
request``, as it does for ``cn`` (2026-09-11) and ``bytedance`` (2026-09-24); ``en`` is the app's
own default when no locale cookie is set. No auth cookie, Origin header, or Referer is needed; a
bare ``curl`` with this repo's own User-Agent gets the same 200 a browser does.

**One call reads the whole global board — no region looping.** With no location filter, the API
returns every posting regardless of country: the same unfiltered query's first page alone spanned
the US, Singapore, Malaysia, Thailand, the UAE, Hong Kong, the UK, Mexico and South Korea.
``data.count`` was **1,395** at measurement time and matched the number of ids returned exactly
once every page had been read.

**A ``GET /api/v1/public/supplier/job/posts/{id}`` route exists but is unused.** It is in the
bundle's route table (``JOB_DETAIL = "/job/posts/"``); probed live with a few plausible bodies, it
answered ``{"code":-9000002,...,"message":"params is invalid"}`` on a 200. Every listed row
already carries the description it would add.

**No rate limit found.** 15 sequential POST requests (10 at limit=10, 5 at limit=100, spanning
offsets 0-590) all returned 200, ~2.3-4.3s each — server-side latency, not throttling; no 429s, no
slowdown pattern.

**Job detail page:** ``https://joinbytedance.com/search/{id}``. Rendered in real Chrome on
2026-09-28, it shows the posting (title, location, team, Job Code), and its static HTML already
carries the posting title in ``<title>``, where a bogus id carries none.
``https://jobs.bytedance.com/en/position/{id}/detail`` 302s to it. The earlier link,
``https://jobs.bytedance.com/en/position/{id}``, was a dead end: it answers 200, as does a bogus id,
but in Chrome it renders "The page you are looking for is missing" (3/3 ids checked), so a 200
check could not tell.
"""

from __future__ import annotations

from headstart.scrapers.supplier_search import SupplierSearchScraper


class ByteDanceScraper(SupplierSearchScraper):
    """ByteDance's own careers API — a Single source scraper (ADR-0139). ``slug`` is fixed to
    ``"jobs.bytedance.com"``."""

    COMPANY = "ByteDance"

    ats = "bytedance"
    # scraper: f"https://joinbytedance.com/search/{id}" (job_url below). A Single source
    # scraper (ADR-0139) — one fixed host, so unlike the platform ATSes above there is nothing
    # to leave host-agnostic. Verified live 2026-09-28 in real Chrome: the page renders the
    # posting and its static <title> is the posting title; ids are numeric strings.
    url_shape = r"https://joinbytedance\.com/search/\d+"

    search_url = "https://jobs.bytedance.com/api/v1/public/supplier/search/job/posts"
    website_path = "en"

    def url(self) -> str:
        return self.search_url

    def job_url(self, job_id: str) -> str:
        return f"https://joinbytedance.com/search/{job_id}"
