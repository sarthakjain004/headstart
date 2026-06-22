# LEADS (fetch-blocked): Enlyft technographic pages

- **Provider:** Enlyft (enlyft.com/tech/products/<ats>)
- **Access:** web — BLOCKED. WebFetch returns HTTP 403 Forbidden on Enlyft product pages (bot protection). No named companies could be fetched directly.
- **Date:** 2026-06-23
- **Status:** LEAD only. Enlyft pages DO contain named-company sections + market-share + firmographic breakdowns, but the page body is not retrievable via WebFetch. Would need a real browser session / headless render (out of this lane).

## Stated totals seen in search-result snippets (vendor-stated, NOT from a fetched page body — treat as leads, not data):
- Greenhouse — Enlyft: ~4,823 companies. URL: https://enlyft.com/tech/products/greenhouse
- Lever — Enlyft: ~2,329 companies. URL: https://enlyft.com/tech/products/lever

## Named customers mentioned in Enlyft/secondary search snippets (NOT saved as company data — unverified against a fetched Enlyft page; listed here only as leads to confirm elsewhere):
- Greenhouse: DoorDash, Robinhood, Elastic, HubSpot, Buzzfeed, J.D. Power, Booking.com, Scout24, The Knot Worldwide
- Lever: Netflix, KPMG, Spotify, Talend, Cirque du Soleil

## Recommended next step
Render Enlyft pages with the headless browser (gstack / WARP) to capture the on-page named-company tables, or use the same approach for other blocked vendors (BuiltWith trends list is JS-rendered and also returned empty via WebFetch).
