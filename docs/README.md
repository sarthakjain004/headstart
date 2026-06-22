# HeadStart docs

Start here — docs are grouped by area.

## Wellfound — scraping a DataDome/Cloudflare-gated board
- [wellfound/traffic-analysis.md](wellfound/traffic-analysis.md) — how Wellfound serves its job data and what gates it (HAR reverse-engineering).
- [wellfound/target-roles.md](wellfound/target-roles.md) — the role × {india, remote} boards to scrape, and how to run / resume the sweep.
- [wellfound/datadome-bypass.md](wellfound/datadome-bypass.md) — DataDome bypass methods and our open-source slider + audio solver.
- [wellfound/cloudflare-bypass.md](wellfound/cloudflare-bypass.md) — Cloudflare JS-Detections methods, and why a CDP-driven browser (pydoll) already clears it.

## Discovery — growing company / ATS coverage
- [discovery/overview.md](discovery/overview.md) — how we find the `(ats, slug)` pairs; what works, what doesn't.
- [discovery/crawler-design.md](discovery/crawler-design.md) — design for a focused ATS-tenant discovery crawler.
- [discovery/common-crawl-mining.md](discovery/common-crawl-mining.md) — the Common Crawl mining run for India-tier ATS tenants.

## Operations & notes
- [telegram-bot.md](telegram-bot.md) — Telegram new-job alert bot (runs on GitHub Actions).
- [learnings.md](learnings.md) — running log of non-obvious findings (newest first).

Deeper Wellfound R&D (captures, the device-check map, the experiment log) lives under
`experiment/wellfound-datadome/` (kept local; not in the repo).

Published output: `index.html` (the site) and `jobs.json` (the feed) also live in this folder.
