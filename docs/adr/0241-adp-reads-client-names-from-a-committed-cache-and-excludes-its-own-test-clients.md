# ADR-0241: ADP reads client names from a committed cache, and excludes ADP's own test clients

**Status:** accepted · **Date:** 2026-09-26 · **Relates to:** [ADR-0180](0180-an-adp-board-is-a-career-center-read-in-every-language-at-one-paced-budget.md) (the ADP Workforce Now scraper and its paced budget), [ADR-0114](0114-a-board-states-its-company-name-in-its-page-title.md) (one name request per Board), [ADR-0216](0216-a-workday-board-is-named-by-its-postings-legal-entities.md) (Workday's committed name cache)

## Context

The seven-run log review of 2026-09-26 (runs 36200233818–36218633315) found two ADP Workforce Now
problems.

1. **ADP's own test clients were served as employers.** Thirty-six Boards are ADP's QA and
   build-verification (BVT) clients. Most name themselves in `client-features`' `ClientName`:
   `WFNQAFR60W`, `WFNQABVT41`, `WFNPJL969`, `FARM 61 BVT4`, `NAS TEST CODE- Prod Enablement`.
   Their titles are "NEW", "BVT Analyst_07/30/2026" and "RECT AUTO REQS_07/17/2025", and 62–100%
   of each client's postings sit at "BVT Location, Anchorage, AK". The ledger counts 52,765
   postings on them, and about 190 were served.
2. **The name lookup spends the paced budget on a name that never changes.** Every ADP request
   goes through one process-wide pacer at 0.4 s spacing, and that pacer holds about 51% of every
   shard's worker time. `resolve_company` makes one of those paced requests per Board per run,
   because the ledger carries no name. The payroll client's `ClientName` does not change from run
   to run.

## Decision

1. **The 36 Boards are in `EXCLUDED_BOARDS`, each confirmed by content.** On 2026-09-26 each
   client's `ClientName` and listing were read. Thirty-one name themselves as ADP test clients,
   `1af96a82` "WFN4PRODA1" among them (60 of 60 postings titled "NEW", at "BVT Location"). The
   other five were confirmed from their postings alone:
   - `ff0a5e37` "PARAMOUNT V23P2gh", and the unnamed `c347ef8d` and `b47d5556`: "BVT Analyst"
     rows, all in Anchorage.
   - `a1630435` "V15P1TALONNN": "Administrative Analyst after restart", "multiselect posting" and
     ADP's own "About Company: At ADP…" copy.
   - `d096a084`, unnamed: "testjob_oct09", "Test", "E only with referrals", in "Alabama, Chicago,
     IL".

   `index prune`'s keep-set is built from `scrapable_boards.load`, so the rows already served are
   evicted on the next merge.
2. **Client names come from a committed cache.** `data/validate/company_names/adp.csv` has one
   `cid,name,checked_at` row per client, written by `scripts/validate/adp_company_names.py`. This
   is the same shape as Workday's `RESOLVED_NAMES`, but keyed on the client GUID, because
   `ClientName` belongs to the client and every career center of a client shares it.
   - `resolve_company` reads the cache first and asks `client-features` only for a client not on
     file.
   - An empty cached name means ADP states no name for that client. It is not asked again.
3. **Landing check.** The same script prints `test-client?` for a `ClientName` that matches
   `^WFN`, `BVT<n>`, `TEST CODE` or `Prod Enablement`. The match is a lead, not a verdict. Five of
   the 36 matched no such name, and `EXCLUDED_BOARDS` still requires reading the postings. The
   rule is in CLAUDE.md's landing rules.
4. **The pacer's spacing stays at 0.4 s.** The documented limit is the 201st request in a 60 s
   window. 0.32 s spacing is at most 188 in any window, 7% under the limit.
   - It was measured live. The fill made about 16,900 `client-features` requests from one
     residential IP over about 90 minutes at 0.32 s spacing, sustaining about 3.1 requests a
     second, and drew no 429.
   - That measurement does not carry over to the pipeline. Scrape shards leave through WARP, and
     one run's report shows most shards on the same `IAD` colo, on addresses in shared
     `2a09:bac1:…` ranges. A per-IP counter there can see more than one process. The 25%
     margin ADR-0180 chose is what absorbs that.
   - The cache removes one request per Board outright. It needs no narrower margin.

## Consequences

- A Board whose client is on file makes one fewer paced request per run. For an empty Board that
  is one of its three requests (content-links, one listing page, the name).
- A client landed after the cache was written costs the request until the script is re-run.
  Re-run the script after landing ADP rows.
- A client that renames itself keeps its old name until the script re-reads it. The script skips
  clients already on file, so a refresh means deleting their rows first.
- 30 of 16,882 clients are not on file. On every attempt they timed out after 30 s, answered 500
  or answered 404. They cost the request on each run, as before.
