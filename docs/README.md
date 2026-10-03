# HeadStart docs

Start here — docs are grouped by area.

## Product & strategy — what this is for, and whether it does it
- [product/2026-08-14_adversarial-audit.md](product/2026-08-14_adversarial-audit.md) — adversarial audit of the whole project against the goal of getting people hired; what is strong, what blocks it, and the five claims the skeptic pass refuted.
- [product/2026-08-14_twelve-week-roadmap.md](product/2026-08-14_twelve-week-roadmap.md) — the plan that came out of it: one strategy, one conversion feature, and the decision gates that say continue or stop.

## Discovery — growing company / ATS coverage
- [Recruiterflow public HTML](recruiterflow/2026-10-03_public-api-measurement.md), [PageUp public RSS](pageup/2026-10-03_public-api-measurement.md), and [Manatal Jobs/JobPosts](manatal/2026-10-03_public-api-measurement.md) — measured protocols, identity, discovery and operating limits (ADRs 0382–0384).
- [discovery/overview.md](discovery/overview.md) — how we find the `(ats, slug)` pairs; what works, what doesn't.
- [discovery/crawler-design.md](discovery/crawler-design.md) — design for a focused ATS-tenant discovery crawler.
- [discovery/common-crawl-mining.md](discovery/common-crawl-mining.md) — the Common Crawl mining run for India-tier ATS tenants.

## Public ATS measurements
- [Comeet / Spark Hire Recruit](comeet/2026-10-03_public-api-measurement.md) — public hosted JSON, UID identity, canonical labels and consent unknowns.
- [JobScore](jobscore/2026-10-03_public-html-measurement.md) — public HTML, canonical labels and stale posting routes.
- [Polymer](polymer/2026-10-03_public-api-measurement.md) — public listing/details, bounded pagination and metadata.

## Pipeline — how the run actually works
- [pipeline/walkthrough.md](pipeline/walkthrough.md) — plain-language explainer of the run, written for someone learning it: the job/stage map, facts vs derivations and `DERIVATIONS_VERSION`, and the description store. Question-driven and grows; undated, unlike the dated one-off analyses beside it in that folder.

## Operations & notes
- [telegram-alerts.md](telegram-alerts.md) — job alerts as Telegram DMs: bot setup, the master's approval flow, commands.
- [email-alerts.md](email-alerts.md) — invite-only email Digests after each pipeline run (ADR-0035).
- [learnings.md](learnings.md) — running log of non-obvious findings (newest first).

Published output: `index.html` (the site) and `jobs.json` (the feed) also live in this folder.
