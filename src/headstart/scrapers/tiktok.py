"""TikTok careers scraper (lifeattiktok.com) — a Single source scraper (ADR-0139): one company,
one board, ``slug`` fixed to ``"lifeattiktok.com"`` rather than discovered. The Board is read from
ByteDance's ``supplier`` search API, the backend ``bytedance`` reads too, so everything but
TikTok's host, header value and links lives in :mod:`headstart.scrapers.supplier_search`
(ADR-0198).

**The marketing frontend (``lifeattiktok.com``) is not the data source and, measured 2026-09-11,
answers every path with a bare 503** — the home page, ``/search/{id}``, and even ``/robots.txt``,
across three plain attempts each and three separate curl_cffi TLS impersonations
(chrome/chrome124/safari/edge), body ``503 Service Temporarily Unavailable`` / server ``TLB``. A
disallowed ``robots.txt`` is the one path a bot wall almost never blocks, so this reads as a real
origin outage rather than a fingerprint-based block — but it is unverified beyond that, and the
job-detail URLs this scraper emits (``lifeattiktok.com/search/{id}``, the reference implementation's
own convention) are accordingly unconfirmed to render; they are the right canonical link either way.

**The data source is a separate, healthy host**, ``api.lifeattiktok.com``, found by inspecting the
reference scraper in the sibling ``kalil0321/ats-scrapers`` project and confirmed live. Only the
``website-path: tiktok`` header is required: without it the host answers HTTP 400 ``invalid
request``, and ``Origin``/``Referer`` change nothing (measured 2026-09-11 and again 2026-09-24).

**Two independent full sweeps, run back-to-back 2026-09-12, collected identical id sets** — 4,239
postings each, zero ids in one sweep and not the other, confirming the ``count`` terminator is
reliable rather than an artifact of one lucky crawl. **No rate limit found** in a 12-request sample
(offsets 0-1100, ~12s wall clock): all 200, no ``Retry-After``, no 429/5xx.
"""

from __future__ import annotations

from headstart.scrapers.supplier_search import SupplierSearchScraper


class TikTokScraper(SupplierSearchScraper):
    """TikTok's own careers site — a Single source scraper (ADR-0139), ``slug`` fixed to the
    marketing host even though the marketing host itself is not the data source (module
    docstring)."""

    COMPANY = "TikTok"

    ats = "tiktok"
    # scraper: f"https://{slug}/search/{id}" (job_url below, the reference implementation's own
    # convention — ADR-0139, single fixed slug "lifeattiktok.com"). Not verified end-to-end: the
    # marketing frontend answered a bare 503 on every path tried, robots.txt included, across
    # three curl_cffi TLS impersonations (docs/tiktok/2026-09-11_api-measurement.md), so
    # `status_ok`/`title_on_page` are expected to read false here the way greenhouse's
    # client-rendered embed form does above — a measured limit of the HTTP probe against this
    # host, not evidence the link is wrong.
    url_shape = r"https://lifeattiktok\.com/search/\d+"

    search_url = "https://api.lifeattiktok.com/api/v1/public/supplier/search/job/posts"
    website_path = "tiktok"

    def url(self) -> str:
        return f"https://{self.slug}/"

    def job_url(self, job_id: str) -> str:
        return f"https://{self.slug}/search/{job_id}"
