# ADR-0197: A ledger row becomes a Board only through its Scraper

**Status:** accepted · **Date:** 2026-09-24 · **Extends:**
[ADR-0001](0001-per-ats-slug-derivation.md) (the Scraper owns its slug) · **Relates to:**
[ADR-0012](0012-liveness-ledger.md) (the ledger is the scrape list),
[ADR-0023](0023-prune-stale-and-duplicate-index-rows.md) (`board_key`),
[ADR-0034](0034-nonprod-boards-dead-by-convention.md) (non-prod rows skip the probe),
[ADR-0111](0111-duplicate-boards-resolve-the-board-surface.md) (`alias_key`)

## Context

ADR-0001 made each Scraper the one authority on what its slug means: `slug_from(tenant, url)`
reads the slug off a discovered row, and `url()` turns it back into the Board's address. The
code around the Scrapers kept re-deriving both.

- **The row-to-Board loop was written out seven times.** `config.load_active_companies`,
  `run_scrapers._load_rows`, `verify_scraper.load_pool`, `index_plan.workday_site_jobs`,
  `dedupe_boards`, `check_liveness._drop_alias_duplicates` and `relocate_dead_boards.derives_back`
  each called `SCRAPERS[ats].slug_from` and built a `CompanyRef` by hand. `user_agent_sweep`
  skipped `slug_from` and passed the raw tenant as the slug. That tenant is not the slug on
  personio (2,503 of the 2,503 rows the sweep samples from), taleo_be (217/217), workday
  (4,899/5,021) or zoho (4,416/4,446), so those Boards failed under both User-Agents and read
  UNREACHED. The sweep measured nothing on them.
- **The liveness probes copied the Scrapers.** Of `check_liveness.py`'s 35 probes, 2 went
  through `slug_from`. The other 33 took the raw `(tenant, url)`. Most rebuilt the listing URL
  that the Scraper's `url()` already builds. Four regexes copied a Scraper's own: Zoho's jobs
  `<input>` and RippleHire's token byte for byte, Workday's careers URL and Taleo BE's next-page
  link differing only in their group names. Lever's two instances and Darwinbox's TLDs and listing path were copied too.
  `check_liveness_browser.py` held a third copy of the workable, personio and recruitee board
  addresses, and `probe_icims.py` a copy of `ICIMSScraper.url()`.
- **Both Taleo editions copied `alias_key`.** They re-implemented the base fetch-and-follow
  (ADR-0111) only to read the landing URL with their own `_canonical` instead of taking its host.

A copy can disagree with its Scraper about which host a row names, and three did. Oracle, Phenom
and Zwayam read the host out of `url`, and their probes used the raw `tenant`. Personio's probe
had no "`personio` in host" check. The browser checker navigated to whatever `url` held.

## Decision

**Every caller that picks a Scraper by ATS turns a row into a Board through
`registry.company_from_row(ats, tenant, url)`**.
It returns a `CompanyRef` whose slug comes from the Scraper's `slug_from` and whose `name` is the
raw tenant, as the scrape list has always carried it. The function lives in `registry` beside
`get_scraper`, because it needs every Scraper class. `config` still imports the registry lazily,
so `load_companies` stays free of Scraper imports (ADR-0001). All seven copies call it now, and
so does `user_agent_sweep`.

**The Scrapers export the facts the probes need, and keep defining them:**
`workday.CAREERS_URL_PATTERN`, `zoho.JOBS_INPUT`, `ripplehire.CAREERS_TOKEN`,
`taleo_be.NEXT_PAGE_LINK`, `lever.API_HOSTS`, `darwinbox.TLDS` and `darwinbox.LISTING_PATH`.
Three small methods take the place of copied f-strings:

- `WorkdayScraper.listing_url_on(instance)`. `url()` and `_resolve_instance` use it too.
- `LeverScraper.url(api_host)`.
- `RippleHireScraper.search_url()` and `DarwinboxScraper.host_on_tld(tld)`.

**A probe reads the Board through the Scraper.** `check_liveness._scraper_for_row` builds
`SCRAPERS[ats](slug)` from the row's slug.

- **These probes ask the Scraper's own URL**, `url()` or one of the methods above: recruitee,
  teamtailor, freshteam, keka, bamboohr, clearcompany, jazzhr, rippling, trakstar,
  successfactors, zoho, personio, join, gem, jobvite, pinpoint, taleo_be, taleo_enterprise,
  lever, workday, ripplehire and phenom. For join and gem that is the company page they read
  first; for taleo_be, the first page of its walk. pyjamahr asks the Scraper's `board_page()` for
  its second question.
- **These probes ask their own request on the Scraper's slug**, each cheaper one with its reason
  in a comment: greenhouse (no `content=true`), ashby (no `includeCompensation`), workable (no
  `details`), breezy (no `verbose`), smartrecruiters (`limit=10`), oracle and pyjamahr's listing
  (a limit of 1 against a stated total). adp, darwinbox and zwayam build theirs from the
  Scraper's own helpers.
- **Left as they were:** cornerstone and jibe, which already went through `slug_from`, and
  eightfold, which another change is editing now.

The browser checker's builders take the slug too, and `probe_icims.py` asks `ICIMSScraper.url()`.

**`BaseScraper.alias_key` gains one hook.** `alias_key_of_landing(landing_url)` turns the landing
URL into the key. Its default is the lower-cased host, the same code as before. Both Taleo
editions override only the hook and drop their copied fetch. The shared fetch now passes
`egress_board`. The Taleo copies went through `self._fetch`, which carried it, and
`test_taleo_be` pins it (retrospective finding 7, 2026-09-15). Every other Scraper on the
default gains one DEBUG retry-log attribution. It routes nothing and walls nothing.

## What changed on the wire

Measured before landing, 2026-09-24. We ran both versions of every probe over all 286,509 rows of
the 35 committed ledgers. Every network seam was stubbed to record the request and answer 404, so
each fallback also ran (Lever's second instance, every Workday data centre, both Darwinbox TLDs).
**34 ATSes sent identical requests.** Personio's changed rule matched no row: no ledger row holds
a vanity host.

**Oracle changed on 441 rows.** Every one has a bare label as its tenant (`bun`) and its pod host
only in `url`. All 441 were recorded `dead`, because the probe asked `https://bun/...`, which
cannot resolve. The Scraper reads the pod host. For 433 of the 441, another row already holds
that host `live`. 2 are non-prod pods, which ADR-0034 settles before any probe. Live A/B on 24
rows:

| Sample | Rows | Before | After |
|---|---|---|---|
| Affected, host held live by another row | 10 | 10 dead | 10 live (24–708 jobs) |
| Affected, host held dead or unknown | 4 | 4 dead | 2 unknown; 2 live (the non-prod pods) |
| Affected, host held by no other row | 2 | 2 dead | 1 unknown (`careers.honeywell.com`), 1 dead |
| Control, host-spelled, ledger live | 4 | live | live, same counts |
| Control, host-spelled, ledger dead | 4 | as ledger | same as before |

The old verdict came from a URL the Scraper never reads, so the change landed.

**In the browser checker, recruitee changed on 50 rows.** 48 are `live` and store a posting link,
a `/l/{lang}/` landing or `robots.txt` in place of the board. Personio, workable and workday
changed on none. Live A/B in one headless Chrome on 12 of the 50 rows (10 live, 2 dead):

- **Before:** 1 live, 9 unknown on the live rows.
- **After:** 9 live, 1 unknown.
- **`2fchowco` (dead):** before, it read another Board (`chowco`) as live with 40 jobs. After, it
  reads its own slug, which is dead, as the ledger says.

## Consequences

- **Oracle's bare-label rows become duplicate spellings of live Boards.** The 464 bare-label rows
  were all last probed on 2026-09-09. The next probe after their 90-day dead TTL, or a `--force`
  run, will find about 433 of them live. Simulated on the committed ledger, the Scrapable Oracle
  Boards stay at 1,681, because `_dedupe_boards` collapses equal `board_key`s. But **432 of those
  Boards would change the name they carry.** The tie-break keeps the first of equal keys, and
  ledger order puts `bun` ahead of `bun.fa.em2.oraclecloud.com`. Every served row would then say
  `bun` in place of the pod host. We have not decided this. Either drop the bare-label rows from
  `oracle.csv` before that refresh, since each duplicates a host row, or accept the label.
- A new copy of a Scraper fact outside the Scraper is now visible in review. A probe that asks
  anything other than `url()` carries a comment saying why.
- `alias_key_of_landing` is the extension point for an ATS whose alias key is not a host. An ATS
  whose signal is not a redirect off `url()` still overrides `alias_key` whole, as Workday and the
  single source Scrapers do.
- `taleo_enterprise_subset_sections.py` still calls `TaleoEnterpriseScraper.slug_from` itself.
  It reads one ATS and names its Scraper class, so it has no lookup to share.
- `taleo_be.slug_from`/`board_key` and `taleo_enterprise.slug_from`/`board_key` are still
  textually identical over two different `_canonical`s. A shared base for two classes was not
  worth it.

## Alternatives considered

- **Put the function in `config` next to `CompanyRef`.** That would need a lazy registry import
  inside it and would move ADR-0001's `config → registry` edge from one function to the module's
  public surface.
- **Pass each probe a Scraper instance, not `(tenant, url)`.** This is cleaner at the call site.
  But Lever and Darwinbox still read the raw `url` for a hint of which instance to ask first,
  and every probe test would change shape for no behavioural gain.
- **Make every probe ask `url()` exactly.** This would pull every description on greenhouse,
  workable and ashby, and a page of 100–1,000 postings on smartrecruiters, pyjamahr and oracle,
  to read one count.
