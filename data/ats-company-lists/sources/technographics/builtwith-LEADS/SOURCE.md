# LEADS (fetch-blocked): BuiltWith Trends website lists

- **Provider:** BuiltWith (trends.builtwith.com/websitelist/<Tech>)
- **Access:** web — EFFECTIVELY BLOCKED. WebFetch returns the page chrome ("BuiltWith Trends" header) but the domain list itself is JS-rendered/lazy-loaded and comes back EMPTY. No domains retrievable via WebFetch.
- **Date:** 2026-06-23
- **Status:** LEAD only. BuiltWith publishes large public "websites using X" lists (Greenhouse, Lever, Workday, etc.) but they require JS rendering to read.
- **URLs to render later (headless browser):**
  - https://trends.builtwith.com/websitelist/Greenhouse
  - https://trends.builtwith.com/websitelist/Lever
  - https://trends.builtwith.com/websitelist/Workday
  - https://trends.builtwith.com/websitelist/Ashby
  - https://trends.builtwith.com/websitelist/SmartRecruiters
  - (pattern: /websitelist/<TechName>; also /<TechName>/Historical for offboarded sites)

## Recommended next step
Render with gstack/headless (WARP) to capture the visible domain list, which is typically larger than the TheirStack/Bloomberry free samples.
