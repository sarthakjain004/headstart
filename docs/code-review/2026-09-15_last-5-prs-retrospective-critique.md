# Retrospective critique and fixes: #450, #452, #453, #454, #455

Reviewed the five most-recently-merged PRs on main (#450, #452, #453, #454, #455), each against its
own merge-base, via the `code-review` skill's two-axis Standards/Spec process. Unlike #450 (which
carries its own two-axis review at the bottom of
`docs/code-review/2026-09-13_pr-449-post-merge-critique.md`), #452, #453, #454, and #455 have zero
PR comments and no review artifact anywhere — no evidence the mandated pre-merge gate
(CLAUDE.md's "Run the `code-review` skill on every code-changing PR before it merges") ran on any
of them. This doc is that review, run after the fact, plus the fixes it produced.

## Findings and remediation

1. **P1 (live bug): #455's company-name fallback returned garbage on shells with no real logo
   image.** `_company()`'s image scan checked every `<img>` on the page for a non-chrome
   alt/title, unscoped. Reproduced live before fixing: `hyundaicapital.taleo.net` returned
   `'Job Search'` (the placeholder the fix exists to eliminate) and `easyjet.taleo.net` returned
   `'Create an RSS feed'` (an RSS icon's alt text). Fixed by requiring the candidate `<img>` tag to
   name itself as a logo (checked against `src`/`class`, matching all 3 of the PR's own verified
   controls — D.R. Horton, TTEC, Valero — dry-run confirmed unchanged) and by changing the final
   fallback from `title or None` to `None` (the generic placeholder must not be re-served as the
   company name on total exhaustion). `src/headstart/scrapers/taleo_enterprise.py`. Two regression
   tests added.
2. **P2: #453's `cc_miner.py` lowercased the whole Taleo Enterprise tenant match, including the
   case-sensitive Career Section slug.** `wayback_feeder.py` and the scraper's own `_canonical()`
   lowercase only the host. The shipped ledger already has 87/7,442 rows with mixed-case section
   codes — exactly CLAUDE.md's documented "tenant-key format proliferation" failure class
   (#202/#226). Fixed to lowercase only the host portion. `scripts/discover/cc_miner.py`. One
   regression test added.
3. **P3 (naming): #453 introduced a near-homograph** — a module-level `_detail(page)` parser and
   `TaleoEnterpriseScraper._detail(self, url)` sharing one name with different signatures, the
   exact pattern CLAUDE.md's module-naming rule warns against. Renamed the module function to
   `_parse_detail_page`. `src/headstart/scrapers/taleo_enterprise.py` + its tests.
4. **P2: #452 and #453 both build against CLAUDE.md's own documented "do not build" verdict for
   Oracle Taleo, without ever reconciling it.** The verdict (`experiment/ats-provider-expansion/
   PLAN.md` §4c) was scoped to a 2026-07-21 India-host mining pass; CLAUDE.md's TODO section
   repeated it without that qualifier. Both scrapers found hundreds of live boards via *global*
   discovery — consistent with the project's "global, not India-only" scope, but never stated as
   the reasoning, and never recorded as the ADR CLAUDE.md's own tactical rules ask for on a
   non-obvious reversal. **Verified two ways, the first attempt wrong and corrected by the
   second.** First pass: fetched all 22 India-*named* (Career Section slug contains "india")
   Taleo Enterprise sections and Genpact's 3 sections live on 2026-09-15 — none resolve to a
   working `portalNo`/listing API, which read as confirming the original India-tenant verdict
   still holds narrowly. That check was too narrow and its first cut had a real bug (an
   unbounded `"india" in location` substring match, which the second pass's own script initially
   wrote, caught "Indiana"/"Indianapolis" as India before word-boundary anchoring fixed it): the
   served LanceDB table (`data/lancedb`, re-pulled clean and cross-checked against the
   `_index_base.json` witness at 459,291 rows; query + captured output in
   `experiment/taleo-india-relevance/`) holds **168 currently-live India-located job rows**
   across both scrapers — 127 on `taleo_enterprise` (Mercedes-Benz 92, TTEC-via-Percepta 30,
   UFlex 5) and 41 on `taleo_be` (Milestone Technologies 12, plus 5 more employers) — none of them
   on an India-*named* Career Section, which is exactly why the slug-based first check missed
   them: these are India offices of global tenants, not India-branded tenants. The India-**tenant**
   verdict (few/no dedicated India career sites) and the India-**relevance** question (does the
   platform serve India job-seekers at all) are different measurements; PLAN.md only answered the
   first. Fixed: `CLAUDE.md`'s TODO section now carries ✅ DONE entries for both scrapers and drops
   "Oracle Taleo" from the dead-ends line;
   [ADR-0144](../adr/0144-oracle-taleo-was-a-dead-end-only-for-india.md) records the reasoning and
   both verification passes.
5. **P2: #452's PR description overstates a shipped number.** "61 redirect-backed alias mappings"
   — the shipped `data/validate/aliases/taleo_be.csv` has 55 rows. Not a code defect (the PR body
   isn't a tracked artifact), but the corrected figures (533 live / 1,760 rows, 55 aliases) are
   what CLAUDE.md's TODO section now carries instead of repeating the inflated one.
6. **P2: #454 shipped a mitigation its own evidence didn't support for 3 of the 5 ATSes it
   touched.** CLAUDE.md documents "a status code doesn't imply its mechanism" as a named pitfall,
   with two prior incidents (freshteam #311, personio #312/313) where a 429-shaped assumption was
   wrong. #454's own cited evidence — `experiment/pipeline-detail-loss/2026-09-13_run-34767229592/
   spare-egress-analysis.md` (titled "Eightfold vs Oracle") and `docs/pipeline/
   2026-09-13_429-egress-live-measurement.md` — names real production 429s **only** for Eightfold
   (5,497 events / 34 boards) and Oracle (1,357 / 2 pods). SuccessFactors, Taleo Business Edition,
   and Taleo Enterprise were wired the same day on zero production 429s and a controlled probe of
   1–9 requests each that reproduced none. Verified by grepping the raw run log directly: only
   `workday` and `eightfold` ever triggered a wall event in it. Fixed: reverted
   `egress_fallback_on`'s 429 addition (and the accompanying `**self._egress()` call-site
   additions it made otherwise-inert) for SuccessFactors, Taleo Business Edition, and Taleo
   Enterprise; kept it for Eightfold and Oracle, which do have production evidence.
   `src/headstart/scrapers/{successfactors,taleo_be,taleo_enterprise}.py` + `tests/test_base.py` +
   `tests/test_taleo_be.py`.
7. **Follow-up to (6), not a defect but closing a fleet-wide observability gap — grew beyond the
   3 reverted scrapers once the same pattern was audited across the whole registry.** `egress_board`
   — the parameter `http.py`'s own docstring says exists exactly "so the shard report can name
   *which* Boards spent the IP supply," calling out as "simply never passed" for callers that skip
   it — was bundled inside `BaseScraper._egress()`, which returned `{}` for any scraper without
   `egress_fallback_on` set. That's 31 of 35 scrapers: every one of them retried in total silence,
   indistinguishable in the log from a scraper that never needed to. Fixed at the root:
   `BaseScraper._egress()` (`src/headstart/scrapers/base.py`) now always returns
   `{"egress_board": self.board_key()}` at minimum — routing (`egress_group`/`egress_on`) stays
   exactly as inert as before for a scraper that hasn't opted in; only the log line changes.
   Audited every scraper's raw `http.fetch`/`http.fetch_async` call sites for the ones that still
   bypassed even that (called neither `self._get()` nor `**self._egress()`) and added
   `**self._egress()` to each: `darwinbox.py`, `icims.py`, `lever.py`, `meta.py`, `ripplehire.py`,
   `rippling.py`, `smartrecruiters.py`, `successfactors.py`, `taleo_be.py`, `taleo_enterprise.py`,
   `trakstar.py` (whose module-level `_fetch_feed` now takes `egress_board` as a parameter from its
   two instance-method callers instead of rebuilding `f"{ats}:{slug}"` itself), and `workday.py` —
   12 files in total. `join.py` was deliberately skipped: it's in `registry.py`'s `DISABLED_ATS`,
   so logging there has no operational value. This doesn't add production-facing telemetry by
   itself: `_note_retry`'s line is DEBUG-level, so a future investigator turning on debug logging
   (as `run-34767229592`'s own analysis did) now gets clean per-board attribution everywhere
   instead of a bare URL on most scrapers. Terminal (retries-exhausted) 429s were already visible
   per-board today via the pre-existing `note_detail_exception`/`report_detail_gaps` machinery on
   every scraper with a detail pass — confirmed, not assumed, by reading the relevant detail
   methods before concluding this.
8. **#450: one documentation overclaim, not a code defect.** The critique doc's own remediation
   claimed "corrupt gzip payloads and incompatible persisted timestamps are covered too," but the
   cited test only mocks `board_freshness.update` to directly raise — it never exercises real
   gzip/csv parsing or a real malformed `datetime.fromisoformat` input. Fixed by adding genuine
   fault-injection tests: a literal non-gzip byte string at the state path, and a real persisted
   CSV row with an unparseable `last_authoritative` value, both driven through
   `board_freshness.update()` itself rather than a mock. `tests/test_board_freshness.py`.

## Verification commands

- `uv run pytest -q`: 2956 passed, 1 skipped, 2 xfailed, 1 failed. Sole failure:
  `test_every_adr_has_exactly_one_index_row` — local untracked ADRs 0105–0109 have no index
  entries, as intended (same pre-existing, excluded condition #450's own doc records).
- `uv run pytest -q tests/test_base.py tests/test_scrapers.py tests/test_taleo_be.py
  tests/test_taleo_enterprise.py tests/test_cc_miner.py tests/test_board_freshness.py
  tests/test_adr_index.py`: 522 passed, 1 skipped, 1 failed — the same ADR exclusion above, not a
  clean pass; call it out here too rather than only in the full-suite line.
- `uv run ruff check` / `uv run ruff format --check` on every touched file: passed.
- Live verification, 2026-09-15: `_company()`'s fix dry-run-tested against live fetches of Valero,
  D.R. Horton, TTEC (unchanged output), easyjet, hyundaicapital (now `None` instead of garbage);
  22 India-tagged Taleo Enterprise Career Sections + Genpact's 3 sections probed live for
  ADR-0144, all non-functional; raw `spare-egress-analysis.md` production log grepped directly for
  wall events by ATS.
