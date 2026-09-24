"""ByteDance's own in-house careers system (jobs.bytedance.com) — a Single source scraper, not a
platform other companies rent (ADR-0139, CONTEXT.md's glossary). There is exactly one tenant, so
``slug`` is fixed to ``"jobs.bytedance.com"``, never discovered. The Board is read from ByteDance's
``supplier`` search API, the backend ``tiktok`` reads too, so everything but ByteDance's host,
header value and links lives in :mod:`headstart.scrapers.supplier_search` (ADR-0198).

Adapted with reference to jobhive's ByteDance scraper (kalil0321/ats-scrapers, MIT); every fact
below was re-measured live against the real endpoint on 2026-09-11 rather than trusted from it —
full numbers in ``docs/bytedance/2026-09-11_api-measurement.md``.

**The public browse page has moved off ``jobs.bytedance.com``, but the API has not.**
``GET https://jobs.bytedance.com/en/position`` answers a bare 302 to
``https://joinbytedance.com/search`` — a Next.js app built as ``bd-career-site`` and served from
ByteDance's own Feishu CDN (path segment ``atsx-throne``, an internal ATS codename). That app is
fully client-rendered: the static HTML carries no job data at all, only ``<script>`` bundle tags.
The listing and filter calls it makes are cross-origin, back to ``jobs.bytedance.com`` — found by
downloading those bundles and reading the minified API client (module ``61215`` in
``8321-22536180820e7ed8.js``), not by guessing.

**The Board is ``website-path: en``.** Without the header the host answers HTTP 400 ``invalid
request``, as it does for ``cn`` and ``bytedance``; ``en`` is the app's own default when no locale
cookie is set. No auth cookie, Origin header, or Referer is needed; a bare ``curl`` with this
repo's own User-Agent gets the same 200 a browser does.

**One call reads the whole global board — no region looping.** With no location filter, the API
returns every posting regardless of country: the same unfiltered query's first page alone spanned
the US, Singapore, Malaysia, Thailand, the UAE, Hong Kong, the UK, Mexico and South Korea.
``data.count`` was **1,395** at measurement time and matched the number of ids returned exactly
once every page had been read.

**A ``GET {base}/job/posts/{id}`` route exists but is unused.** It is in the bundle's route table
(``JOB_DETAIL = "/job/posts/"``); probed live with a few plausible bodies, it answered
``{"code":-9000002,...,"message":"params is invalid"}`` on a 200. Every listed row already carries
the description it would add.

**No rate limit found.** 15 sequential POST requests (10 at limit=10, 5 at limit=100, spanning
offsets 0-590) all returned 200, ~2.3-4.3s each — server-side latency, not throttling; no 429s, no
slowdown pattern.

**Job detail page:** ``https://jobs.bytedance.com/en/position/{id}`` answers 200 for a real id
(verified: the id from a live search result). It is itself a client-rendered shell like the search
page, so a bogus id also answers 200 — this scraper does not depend on that page's content, only
on it being the stable link shape ByteDance's own site would route a person to.
"""

from __future__ import annotations

from headstart.scrapers.supplier_search import SupplierSearchScraper


class ByteDanceScraper(SupplierSearchScraper):
    """ByteDance's own careers API — a Single source scraper (ADR-0139). ``slug`` is fixed to
    ``"jobs.bytedance.com"``."""

    COMPANY = "ByteDance"

    ats = "bytedance"
    # scraper: f"https://jobs.bytedance.com/en/position/{id}" (job_url below). A Single source
    # scraper (ADR-0139) — one fixed host, so unlike the platform ATSes above there is nothing
    # to leave host-agnostic. Verified live 2026-09-11: the route answers 200 for a real id
    # pulled from the search API; ids are numeric strings (e.g. "7673941558289205509").
    url_shape = r"https://jobs\.bytedance\.com/en/position/\d+"

    search_url = "https://jobs.bytedance.com/api/v1/public/supplier/search/job/posts"
    website_path = "en"

    def url(self) -> str:
        return self.search_url

    def job_url(self, job_id: str) -> str:
        return f"https://jobs.bytedance.com/en/position/{job_id}"
