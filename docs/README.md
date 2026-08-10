# HeadStart docs

Start here — docs are grouped by area.

## Sidecorpus — scraping a DataDome/Cloudflare-gated board
- [sidecorpus/traffic-analysis.md](sidecorpus/traffic-analysis.md) — how Sidecorpus serves its job data and what gates it (HAR reverse-engineering).
- [sidecorpus/target-roles.md](sidecorpus/target-roles.md) — the role × {india, remote} boards to scrape, and how to run / resume the sweep.
- [sidecorpus/datadome-bypass.md](sidecorpus/datadome-bypass.md) — DataDome bypass methods and our open-source slider + audio solver.
- [sidecorpus/cloudflare-bypass.md](sidecorpus/cloudflare-bypass.md) — Cloudflare JS-Detections methods, and why a CDP-driven browser (pydoll) already clears it.

## Discovery — growing company / ATS coverage
- [discovery/overview.md](discovery/overview.md) — how we find the `(ats, slug)` pairs; what works, what doesn't.
- [discovery/crawler-design.md](discovery/crawler-design.md) — design for a focused ATS-tenant discovery crawler.
- [discovery/common-crawl-mining.md](discovery/common-crawl-mining.md) — the Common Crawl mining run for India-tier ATS tenants.

## Operations & notes
- [telegram-alerts.md](telegram-alerts.md) — job alerts as Telegram DMs: bot setup, the master's approval flow, commands.
- [email-alerts.md](email-alerts.md) — invite-only email Digests after each pipeline run (ADR-0035).
- [learnings.md](learnings.md) — running log of non-obvious findings (newest first).

Deeper Sidecorpus R&D (captures, the device-check map, the experiment log) lives under
`experiment/sidecorpus-datadome/` (kept local; not in the repo).

Published output: `index.html` (the site) and `jobs.json` (the feed) also live in this folder.
