<!-- Provenance: solutions workflow run 2026-08-14, downstream of the adversarial audit.
     6 area designers (front door, outcome instrumentation, conversion, first-minute quality,
     freshness, ops safety), each stress-tested by a hostile feasibility-and-impact critic, then
     sequenced into one plan. Constraints given: product goal (get OTHER people hired), solo,
     part-time, ~$0 budget, free tiers only, no rewrite. -->

# The plan — HeadStart, next 12 weeks

**Date:** 2026-08-14 · **Constraints:** solo · part-time (~10h/week) · ~$0 · free tiers only ·
not a rewrite · goal is the *product* (get other people hired), not the portfolio.

> **Read the audit first:** [2026-08-14_adversarial-audit.md](2026-08-14_adversarial-audit.md)

## Corrections to the text below — verified 2026-08-14, after the plan was written

1. **Week 1's first instruction is already done.** The plan says
   `squash-dataset-history.yml` is "still `cron: "0 4 * * 0"`" — that agent read a stale local
   working tree. On `origin/main` it is `0 4 * * *` (PR #133) and the squash has run:
   HF `used_storage` went **94.77 GB -> 21.49 GB**, history collapsed to one commit, headroom is
   now 78.5 GB. **Skip that step.** Its one useful residue: `0 8 * * *` would be better than
   `0 4`, because 03:00-04:00 collides with the 01:30 pipeline's 84-minute mean runtime.
2. **The freshness area's critic failed mid-response** (API connection closed). Section 4's
   freshness and public-feed designs therefore received *less* adversarial scrutiny than the other
   five areas. Weight them accordingly.
3. **Highest-ROI single item, if only one thing gets done:** remove the sign-in wall from
   `/search` (`deploy/hf-space/app.py:191`). Everything else in this plan improves an experience
   that, today, no stranger can have.

---

## 1. The one-line strategy

**HeadStart is a no-login feed of tech jobs within hours of them appearing on the company's own ATS, with a link that lands on the actual posting — and the only thing that tells you which of them you applied to and which to chase.**

Yes, that means narrowing drastically. Stop trying to be a semantic search engine over 283,767 rows. hiring.cafe is free, has 3.5M jobs and the same "read the ATS directly" thesis; Fantastic.jobs will sell this entire corpus for ~$281. **Corpus size is not a moat and search quality is not a moat.** Two things are: (a) reading boards ~10x/day gives a latency edge nobody with a 3.5M-row crawl can match cheaply — *unmeasured, and week 7 measures it*; (b) Darwinbox + Keka + RippleHire = 9.4% of the corpus that no commercial API carries. And one thing nobody does at all: **what happens after the click.** All 19 routes stop at a URL (`search.py:352` — last field emitted is `url`).

So: freshness + link fidelity + the tail, wrapped around a tracker. Not "search everything."

---

## 2. Week 1 — the unblock (~2 weekends + 3 evenings, ~25h)

**Evening 1 — the storage emergency, 30 minutes.** `.github/workflows/squash-dataset-history.yml:20` is *still* `cron: "0 4 * * 0"` — I checked. Change to `"0 8 * * *"` (daily, an actual even hour; 03:00 collides with the 01:30 pipeline's 84-min mean). Do the arithmetic in the header comment while you're there: ~12 GB/day against ~11 GB live is a 7.4-day fuse, which is exactly why it hit 94.77. Daily squash caps peak at ~35 GB. **Do not build the `headroom.py` preflight gate** — HF's own doc says quota reflects a squash *within 36 hours*, longer than a 24h reclaim interval, so a hard-fail gate at 0.90 would go red on the first run and never come down. You'd convert "silently dying" into "loudly dead."

**Evening 1, second half — 20 minutes, no code.** On the Oracle box, prefix the Space's key in `authorized_keys` with `restrict,permitopen="127.0.0.1:4000",command="/bin/false"`. Matches `start.sh:32` exactly. Turns "a leaked Space secret is a shell" into "a leaked Space secret is a port forward." Then two lines in `deploy/hf-space/app.py`: set `HF_HUB_OFFLINE=1` after the `snapshot_download` at :49-57 and before the `SentenceTransformer` at :62. The designer's `revision=` pin does **not** close the hole — the executed code lives in `nomic-ai/nomic-bert-2048` via `auto_map`, a different repo `revision=` doesn't reach. Offline mode pins everything already baked into `/app/hf-cache`.

**Weekend 1 — open the door (~10h).** `_PUBLIC_PATHS` at `deploy/hf-space/app.py:191` is `{"/", "/auth/google", "/me", "/unsubscribe"}`. `/search` is not in it. Rename `_require_sign_in` → `_gate`, and the allowlist becomes `{"/", "/signin", "/auth/google", "/me", "/unsubscribe", "/search", "/trends", "/about", "/privacy", "/terms", "/healthz", "/robots.txt"}` plus `request.endpoint == "static"` (today `/static` 401s, which is why `signin.html` inlines its CSS). Keep it an allowlist. Every write route — `/profile*`, `/sets*`, `/saved*`, `/subscribe` — stays 401 without a session, and the per-route 401 test is the acceptance criterion, not an afterthought.

`index()` at app.py:781 stops branching to `signin.html`; always render `base.html` with `signed_in=bool(session.get('email'))` and `alerts_on=_ALERTS_ON and signed_in` (otherwise a stranger's page pulls in Google's script).

Three amendments to the design as proposed:
- **Keep the anonymous k-cap of 10, but relabel it.** The critic is right that it saves no CPU — the scan is brute-force over 871 MB regardless of k (`create_index` is called nowhere). It is an anti-bulk-export measure on the one asset. Say that in the docstring.
- **Drop `per_day=150` entirely.** Counters reset on the ~10x/day Space restart. A limit that lies is worse than none.
- **Keep `in_flight=2` and state the trade out loud:** three concurrent connections from one bored person take the product down. That is the accepted price of a free 2-vCPU box with no WAF.

Plus one line in `search_jobs()`: `print(json.dumps({"ev":"q","who":key,"q":query,"n":len(rows),"ms":ms}), flush=True)`. Stdout only. This is the entire phase-1 measurement system and it is enough to answer "has a stranger ever searched."

**Evening 2 — findability (~3h).** `README.md:18` claims GitHub Pages at a URL that 404s — delete the line, add a three-line "Try it" block with `https://imposeidon-headstart-search.hf.space`. Delete `docs/index.html` and `docs/jobs.json` (5.26 MB, stale since 2026-06-29) — a second, worse product on a URL that doesn't resolve. Set the repo Website field + description + topics (2 min, owner action). Add `short_description` and `tags` to `deploy/hf-space/README.md` front matter. Add `<meta name="description">`, OG tags and `<link rel="canonical">` to `src/headstart/ui/templates/base.html` — its `<head>` currently has charset, viewport, title, stylesheet and nothing else. And the three-line deep-link handler in `app.js` against the existing `CONTROL` map, so `/?q=backend+engineer` prefills and runs — that is the only $0 distribution primitive in the whole design set.

**Evening 3 — `/healthz` + `hf_transfer` (~2h).** Fifteen lines for `/healthz` (returns 503 on boot error — this is the missing monitoring hook for the 13.7h outage), `hf_transfer` in `deploy/hf-space/requirements.txt` + `HF_HUB_ENABLE_HF_TRANSFER=1` (the only real lever on a single 755 MB `.lance` file; `max_workers=16` is a verified no-op), and background the 30-second router-tunnel wait in `start.sh` that blocks the port bind today. **Skip the threaded boot and the warm-up banner** — 20 min/day of dark time against 2 visitors/fortnight is optimising a funnel with no arrivals.

**Weekend 2 — `/about`, `/privacy`, `/terms` (~10h, mostly not code).** Three routes, three templates, footer links. The code is 3 hours. The rest is reading the router config to name *which vendor* receives up to 20,000 chars of résumé text, reading that vendor's current API retention terms, and deciding whether you publish your real name and a monitored mailbox. If you can't verify the retention claim, write "we cannot guarantee this text is not retained" — an unverified claim is worse than silence. The single highest-value line is the one beside the paste box in `profile.html`: *"This text is sent to <provider> to extract your details. What happens to it →"*. Google's OAuth consent screen wants these URLs anyway. **This is the item that will slip. Do it before you enjoy yourself in weeks 2-6.**

---

## 3. Weeks 2-6 — the one feature: the Application Ladder

**The pick: turn `SavedJob` into an Application.** Not fit scoring, not the answer pack, not the query pre-parse, not the public feed.

Why over the alternatives. *Requirement coverage / skill matching* fails its own ship gate before it starts — the spike measured median 1 distinct skill per job and 38% zero-hit; "you match 1 of 1" is worse than silence, and it's the most expensive item anyone proposed (25-40h). *The answer pack* has the best first-order mechanism (minutes per application) but ships a data-loss bug — `Profile.revised()` at `store.py:371` rebuilds every field from `fields.get()`, so a second résumé parse blanks all eight answers — and its denominator is zero. *The query pre-parse* silently sets a hard-excluding `max_years` from prose, which is the exact "invents a filter the user never typed" failure it claims to avoid by not using an LLM. *The public fresh feed* is distribution, not conversion; it's phase 3.

The ladder wins on four counts: it fixes a **live data-loss bug** (verified: `star_job` at app.py:606 calls `SavedJob.create(...)` and `put_saved` overwrites, so re-starring destroys `starred_at` — the only per-account outcome signal this product has ever captured); it is the only thing a user opens *on purpose*, so the sensor gets filled in; it records the north star at its source rather than inferring it from clicks; and it is the hard dependency for the day-8 follow-up, the only mechanism in all six areas with a defensible causal path to an interview.

**North star: confirmed applications per weekly-active user.** Guardrail, checked monthly and never steered on: interviews reported per 100 confirmed applications — a product that makes people spray raises the north star while that ratio collapses.

**Build order:**

- **W2 — RippleHire deep links (~8h).** Every one of 4,596 tech rows points at one of 37 board landing pages (`scrapers/ripplehire.py:119`, hardcoded `/candidate/careers`). Two people independently verified `?jobSeq={jobSeq}` deep-links to one job on 12/12 live tenants. Change the scraper's `url=` and add a `_canonical_url` branch in `search.py:152` beside the darwinbox/recruitee rewrites, so stored rows heal at serve time. Fix `scripts/eval/verify_filters.py:97`, which currently codifies the board-level shape as *passing*. **Do not do the Greenhouse half.** Probes showed the canonical host 302s back to the custom domain for live jobs and converts honest 404/410 into 200 landing pages for dead ones — it improves the dead-link metric by hiding dead links. I'm overruling that designer in favour of the critic.
- **W3 — the record (~8h).** `src/headstart/alerts/store.py`: `STATUSES`, `status`, `status_at`, `opened_at`, `note`, and `advanced()`. `star_job` becomes read-merge, not overwrite. `POST /saved/<saved_id>/status` beside `unstar_job`, through `_account_gate()`. `from_dict` already filters to `__dataclass_fields__`, so no migration.
- **W4 — the ask (~10h).** Status pills in the Saved tab, and the one that matters: after a click-through on a starred job, an inline `Did you apply? [Yes] [Not yet]` row. Cap at 3 per session, two dismissals mutes it, and a kill switch on the Profile. Do **not** build the email one-click — a GET that mutates state gets fired by Outlook SafeLinks and would mark applications rejected that nobody clicked.
- **W5 — the follow-up (~5h).** A second block in `alerts/digest.py::render`, under the new matches: applications at `status=applied` with `status_at` older than 8 days, with a three-line copy-paste nudge. Plus `nudged_at` so nobody is nudged twice. It must not touch the Watermark. This rides the digest that already sends.
- **W6 — buffer + the two cheap search fixes (~8h).** The per-company cap in `JobSearch.run` (over-fetch `min(k*5, 200)`, max 2 rows per company) — structural, kills slot-flooding for every query forever, and free because the scan is k-independent. And ship `experience_source` in the projection with a "~5 yrs · from title" label, so a guessed number is visibly a guess. I'm **deferring the min_years grace band** — it directly contradicts the promise that `max_years=3` excludes 5-year jobs, and labelling is worth more than re-admitting.

Measurement stays the stdout log line plus a ~50-line `scripts/eval/funnel_report.py` you run by hand against the subscribers store. **No event spine, no `/e` route, no events dataset, no daily Telegram funnel message.** A daily message reading `wau 0 · searches 0 · applied 0` will feel like the instrument is working while nothing moves.

---

## 4. Weeks 7-12 — earn the claim

**W7 (evening) — make the freshness stamp durable.** `src/headstart/ingest/index.py:306` stamps every row in `plan.add` with the run's time. Nothing preserves `first_seen` across an eviction, so a board that comes back after a hiccup broadcasts weeks-old postings as new. A small append-only `first_seen_store` on the ADR-0050 pattern (~90 lines, ~60 KB/run) fixes it. Without this, the study's t0 is fiction. **Do not backfill the 77% null** — `first_seen IS NULL` provably means "before 2026-07-28", it already self-excludes from every window via SQL NULL semantics, and every candidate fill fabricates the exact number the product is betting on.

**W7-8 — the lead-time study (~2 weekends + 3 days of cron polling).** `scripts/eval/lead_time.py`, prospective, 300 rows stratified to over-sample darwinbox/keka/ripplehire. Poll Adzuna (free API, real `created` timestamp), LinkedIn's unauthenticated guest endpoint, and hiring.cafe at t0+0/2/6/24/72h from a GitHub Actions cron. Kaplan-Meier, because "never appeared" is right-censored. **Pre-register the matching rule in `experiment/lead-time/LOG.md` before the first poll, and write the null-result section before running it.**

**The gate.** Ship the freshness pitch if median lead ≥6h on ≥40% of the panel, or ≥20% never appear within 72h. Then weeks 9-12 build the public fresh feed: `src/headstart/ingest/publish_fresh.py` in the merge job, GitHub Pages via `upload-pages-artifact` (no commits, no repo bloat), per-role RSS, a public Telegram channel through the existing `alerts/telegram.py` — because a push channel has an 8-recipient ceiling and a pull channel has none. Gate it with a link check over only the ~1.5k published rows, never the table.

**If the thesis dies** — ≥90% appear within 4h on both comparators — **abandon freshness rather than re-arguing it.** The pitch becomes: *"Every tech opening on 94,000 company career pages, including the ~9% on Darwinbox, Keka and RippleHire that no aggregator and no commercial API carries."* Weeks 9-12 then go to the fingerprinter gaps in CLAUDE.md's TODO (11 curated Darwinbox companies + 6 Keka + 3 Workday, **zero new scrapers** — the highest-ROI work in the repo) and to the answer pack, with the `revised()` merge bug fixed first.

**Either branch, ~8h somewhere in weeks 9-12:** the alert spine, trimmed to three things. `watch pipeline-age` (newest HF commit older than 5h — catches failure, hang, *and* a schedule GitHub silently dropped); an authenticated `/search?q=backend+engineer&k=1` probe using the `_ALERTS_TOKEN` service path already built at `app.py:199` (subsumes both the `/` probe and a row-count check with one end-to-end assertion); and a **weekly heartbeat**, non-negotiable, because without it a dead watchdog is bit-identical to a healthy system. Plus `data/state/*.csv` (1 MB, no PII, months of measurement across 94k boards — the only genuinely non-re-derivable asset) committed to a `state-backup` branch. Ten minutes.

---

## 5. The stop-doing list

**Delete outright:**
- **Requirement coverage / skill extraction sidecar.** Fails its own gate on measured input density. 25-40h.
- **The Greenhouse canonical URL rewrite.** Launders 404/410 into 200 landing pages on 3,024 rows and makes the dashboard green by hiding dead links.
- **The segmented embedding store.** Its safety story ("segment 0 is read-only") contradicts `embed_plan._prior_rows`, which evicts precisely the oldest rows; and its storage payoff is already delivered by the one-line cron. Three to four days of high-risk surgery on 283k vectors ≈ 230 CPU-hours, to save ~40 seconds per run.
- **The backup HF dataset + restore drill.** The 100 GB quota is **per account, not per repo** — the backup shares the exact failure domain it claims to escape, and it mirrors PII into a second place while you're simultaneously trying to make deletion real. Keep only the `state-backup` git branch.
- **The quota preflight gate.** 36h metric lag > 24h reclaim interval = permanent red.
- **`docs/index.html` + `docs/jobs.json`.** A second product on a URL that 404s.

**Freeze until a gate opens:**
- The event spine, `/e`, the events dataset, the daily Telegram funnel report — until searches > 0 for a week.
- The English-gate widening. It recovers ~20k jobs at the cost of two entire embed runs (`embed_run.py:84` measures ~9,700 Docs/run ceiling) — during which no *new* job gets embedded, spending the freshness lever to buy recall. And ~1,300 of the recovered rows are RippleHire, which can't be applied to until W2 ships.
- Board-age eviction (`board_scrapes.csv`). Correct, safely calibrated, and invisible to a job seeker for three months. Its real value is alerting, and `watch pipeline-age` costs an hour.
- The scraper-rot yield alarm. As designed it's a one-shot — `board_cost.update` at `board_cost.py:126` overwrites `jobs` wholesale, so run N poisons its own baseline and Darwinbox's priors are *already* poisoned. It needs rebasing on the committed `data/validate/liveness/{ats}.csv`. Worth doing, not in the first 12 weeks.
- Extending the 8-recipient invite allowlist. The ceiling is a property of push; the answer is pull.

**Never:** the LLM query-parser (stays deferred permanently), auto-fill / one-click apply (CORS makes it a browser extension across 21 ATSes — a second product), per-job LLM tailoring (ADR-0041 caps parses at 3/account precisely because unbounded router spend violates the $0 constraint), a cross-encoder reranker (+1.04 GB on a 940 MB cold boot), and a kanban board. The moment the ladder grows columns and drag-and-drop it has stopped being an instrument.

---

## 6. Decision gates

**End of week 2 (two weeks of an open door).**
- **Continue** at ≥20 unique non-owner search sessions in the stdout log.
- **Stop and rethink** below 5. The problem is *distribution*, not product — spend weeks 3-6 getting the URL in front of people (a Show HN, r/developersIndia, r/cscareerquestionsIN, the Telegram channel) instead of building the ladder. Building a tracker for nobody is the single most likely way this plan fails.
- **Special case:** if ≥20 searches but **fewer than 5 of the first 50 searches produce a click on a job link**, the problem is result quality, not conversion. Reorder: per-company cap and the `experience_source` label move to week 3, the ladder to week 5.

**End of week 6.**
- **Continue** if ≥5 distinct accounts have marked at least one job `applied`.
- **Stop** if ≥10 people clicked an apply link and **zero** ever marked applied. That means people apply and leave, the tracker is the wrong feature, and the remaining six weeks go to the feed and to reach — not to more tracker UI.
- **Guardrail:** if interviews per 100 confirmed applications is below ~3 once there are ≥50 applications, the north star is lying and you're driving spray.

**Week 8 (the study).**
- **Ship the freshness pitch** at median lead ≥6h on ≥40% of the panel, or ≥20% never seen within 72h.
- **Kill freshness** if ≥90% appear within 4h on both surviving comparators.
- **Inconclusive** if fewer than 200 of 300 panel rows resolve (LinkedIn blocking is the likely cause) — re-run once on a fresh sample, then accept whatever it says. Do not argue with it.

**Week 12.**
- **Continue** if at least one person who is not the owner reports an interview traceable to a HeadStart-marked application.
- **Stop and reconsider the whole thing** if there are zero interviews *and* fewer than 50 confirmed applications across all users. That is not a feature problem or a distribution problem; it's a market problem, and the honest options are to publish the corpus as an open dataset or to stop.

---

## 7. What could make this fail anyway

**The thesis is wrong and the tail is worthless too.** Freshness may be a few minutes, not hours — aggregators poll ATS APIs as well. And the 9.4% tail is disproportionately Indian GCC boards; if the people you can actually reach don't want those jobs, "coverage no aggregator has" is a fact about a market segment you have no channel into. Both legs failing at once is a real branch, and there is no third moat behind them.

**Nothing in this plan creates demand.** A README, a Space card, and OG tags stop you *losing* visitors; they don't produce any. A Show HN is one shot and the Space 503s past two concurrent searches with `in_flight=2` — the front page would arrive and find a dead box. If distribution is going to happen it needs a real slot in weeks 3-6, taken from the ladder, and that trade is a coin flip.

**The measurement is thin on purpose and might be too thin.** Stdout logs on a container that restarts 10x/day mean the week-2 gate is answered by eyeballing a log. If that number is ambiguous, the gate doesn't fire cleanly and you'll drift.

**The budget is 12 weeks × ~10h = ~120 hours and the plan spends ~110.** One bad fortnight at the day job eats a whole phase. The privacy weekend and the study weekend are the two most likely to slip, and they are the two that gate everything downstream.

**The infrastructure can still bite.** HF's 36-hour GC lag means a bad week walls the quota even with a daily squash. The Space runs the Flask dev server as root holding `SECRET_KEY`, the router admin key and an SSH key — the `HF_HUB_OFFLINE` pin and the `permitopen` restriction bound that but don't close it. And résumé text goes to a third-party LLM under a policy you'll write in one time-pressured weekend.

**And the honest one: hiring.cafe may have already won.** They're free, they're 12x bigger, they ship the same thesis, and if they add the Indian ATS tail or a tracker there is nothing left that is yours. The counter is not to out-build them — it's that a solo part-time project only needs a few hundred people it serves better than anyone else. The gates above exist to tell you, by week 6, whether even that is true.