# HeadStart docs

Start here — docs are grouped by area.

## Product & strategy — what this is for, and whether it does it
- [product/2026-08-14_adversarial-audit.md](product/2026-08-14_adversarial-audit.md) — adversarial audit of the whole project against the goal of getting people hired; what is strong, what blocks it, and the five claims the skeptic pass refuted.
- [product/2026-08-14_twelve-week-roadmap.md](product/2026-08-14_twelve-week-roadmap.md) — the plan that came out of it: one strategy, one conversion feature, and the decision gates that say continue or stop.

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
- [telegram-alerts.md](telegram-alerts.md) — job alerts as Telegram DMs: bot setup, the master's approval flow, commands.
- [email-alerts.md](email-alerts.md) — invite-only email Digests after each pipeline run (ADR-0035).
- [learnings.md](learnings.md) — running log of non-obvious findings (newest first).

Deeper Wellfound R&D (captures, the device-check map, the experiment log) lives under
`experiment/wellfound-datadome/` (kept local; not in the repo).

Published output: `index.html` (the site) and `jobs.json` (the feed) also live in this folder.
