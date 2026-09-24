# ADR-0189: A Jibe Board is a client, read under its own robots.txt, minus what iCIMS already serves

**Status:** accepted · **Date:** 2026-09-24 · **Relates to:** [ADR-0001](0001-per-ats-slug-derivation.md) (a scraper's slug is its own to define), [ADR-0012](0012-liveness-ledger.md) (the ledger is the scrape list), [ADR-0053](0053-scope-eviction-on-scrape-outcome.md) and [ADR-0121](0121-a-negligible-shortfall-is-still-an-authoritative-list.md) (truncation), [ADR-0111](0111-duplicate-boards-resolve-the-board-surface.md) (alias ledger), [ADR-0114](0114-a-board-states-its-company-name-in-its-page-title.md) (the name comes off the board page), [ADR-0158](0158-jazzhr-and-jobvite-are-worth-their-storage.md) (the storage bar)

## Context

Jibe is iCIMS's career-site layer. An employer's branded careers site (`careers.costco.com`) is a
Jibe site, and its postings come from the employer's ATS, usually an iCIMS tenant. The Indeed sweep
(#576) found 278 such sites. It also found that 99.7% of their postings sit on iCIMS tenants whose
robots.txt says `Disallow: /`, which the sitemap-only iCIMS scraper cannot read. Every Jibe site
serves a JSON listing at `/api/jobs` and a robots.txt that allows crawling with `crawl-delay: 5`.

The measurement is `docs/jibe/2026-09-24_api-jobs-measurement.md`. It covers 1,268 candidate
clients, a full walk of 278 sites (151,619 rows) and the scraper run end to end on the four largest
Boards. Two of its findings forced decisions the scraper could not make on its own. First, the host
we would read and the host that holds the postings publish different robots.txt rules. Second,
26.8% of the pool's rows are postings the iCIMS scraper already serves.

## Decision

1. **The Board is the Jibe client, and its id is the slug.** Every client answers at
   `{client}.jibeapply.com` with the same `/api/jobs` as its vanity sites: the same `totalCount` on
   276 of 276 walked. A client with several vanity sites (Rollins 11, MasTec 8) is one Board. Each
   site's page adds a `searchOverride` filter; without it the API returns the client's whole public
   set, and those postings' pages answer 200 on the client host. So no override is sent.
   `slug_from` lowercases and strips a `.jibeapply.com` suffix. A vanity host is a spelling that
   discovery resolves to its client: the rows' `client_code`, else the page's `_jibe` cid. The cid
   is a template leftover on 4 of 211 checked.

2. **robots.txt is honoured per host, strictly. The user made this call.** RFC 9309 scopes a
   robots.txt to the host that serves it. The scraper requests only the client host: robots.txt,
   `/api/jobs`, and `/jobs` for the name. It never requests a posting's `apply_url`, and it
   requests nothing else from the backing tenant except that tenant's robots.txt (decision 3).
   - **What the two hosts say.**
     - The client hosts serve `User-agent: * / Allow: / / crawl-delay: 5`: 1,138 of 1,143
       resolving client hosts do.
     - `carrefour.jibeapply.com` serves `Disallow: /`. The one exception among the other four is
       `www`, whose robots.txt is iCIMS's own; the other three (`email`, `mail`, `uri`) served none
       that could be read.
     - The iCIMS tenants behind the Indeed-sweep sites serve `Disallow: /`: 82 of 85 sampled do.
   - **How the scraper enforces it.**
     - It reads robots.txt once per Board per run, before any other request to the host, and
       checks every later path against it, redirects included.
     - It spaces every request to one host at least 5 s apart. That includes its own retries; the
       shared retry ladder is switched off here, because its backoff starts at 0.75 s.
     - It never follows a redirect off the client host, except robots.txt's own (RFC 9309
       §2.3.1.2 asks for up to five). Three `career.page` hosts redirect theirs.
   - **What each robots.txt answer means.**
     - A host that disallows `/api/jobs` is not read, and its Board comes back empty.
     - A 4xx robots.txt means no rules apply.
     - A 5xx robots.txt, or an unreachable host, fails the Board for the run. Returning it empty
       would evict its rows.
   - **The prober.** It obeys the same rules: robots.txt first, then the listing 5 s later. A
     disallowing or unreachable host is UNKNOWN, never read. A label counts as dead only when a
     public resolver says it has no A record. Under a 64-thread sweep, the macOS resolver said "no
     such host" for live clients. `demo*` labels are dead before any probe, as every ATS's
     non-prod Boards are (ADR-0034).
   - **One recorded breach.** Before the policy existed, the step-2 client probe made one
     `/api/jobs` request to carrefour after reading its robots.txt without acting on it. Nothing
     reads it now.

3. **A posting iCIMS can already read is dropped. This is the user's option A.** Each row's
   `apply_url` names its backing Board, and Jibe's `slug` is that same requisition id. Serving it
   here would serve the posting twice.
   - **When a posting is dropped.** Only when its tenant's robots.txt is known to allow
     `/sitemap.xml`, the only surface the iCIMS scraper reads.
   - **What keeps the posting here.** A disallowing tenant, a 5xx robots.txt, or an unreachable
     host.
   - **Cost.** One robots.txt fetch per tenant per scrape process, cached, with one fetch in
     flight at a time. A run's shards are separate processes, so each shard that meets a tenant
     asks once. A tenant that flips either way is followed on the next run.
   - **Why a posting at a time.** The alternatives were a per-client gate, which loses the new
     postings of mixed clients (Herbalife, Cordis), and retiring about 774 iCIMS Boards, which
     would re-key about 94,000 served jobs.
   - **Size.** An estimated 89,804 postings a run are dropped this way. uhs alone dropped 273
     end to end.

4. **Clients wholly on a Workday or Oracle Board we hold are parked.** The drop in decision 3
   cannot see these. Each flagged client's whole listing was walked, and every `apply_url` host
   joined to the ledgers. Six sit entirely on held live Boards, so they go in `PARKED_BOARDS`:
   stjude (163 of 163), spglobal (292), fedexfreight (677) and mercy (2,257) on Workday, and
   mountsinai (1,821) and marriott (1) on Oracle. `aidt`, flagged from its first page, has 1 of 25
   on Workday and is kept.

5. **Not boards of openings are excluded** (`EXCLUDED_BOARDS`), each read before exclusion:
   - `fedex`: a historical feed of 136,186 rows, 2024-dated.
   - `hexdigital` and `launch`: Jibe's demo clients. Left in, hexdigital's 906 "Software Engineer"
     rows would serve as fake tech postings.
   - `mortonfinancial`: test data.
   - `testaxa` and `axatest`: AXA's UAT site.
   - `discovery1`: one "DO NOT APPLY" posting.
   - `icims` and `template`: the vendor's own labels.

   `rentokil-initial` serves `rentokil`'s identical listing and is aliased
   (`data/validate/aliases/jibe.csv`).

6. **One listing surface, no detail pass.** `GET /api/jobs?page=N&limit=100&internal=false`.
   - **The listing is enough.** It carries the full description: its text equals the detail
     endpoint's on 77 of 90 postings, and the rest differ by 1 to 7 whitespace characters.
   - **Page size.** `limit` above 100 answers 422.
   - **Languages.** Rows are per language, so one Job is kept per `slug`, the English row first.
   - **When a walk ends.** At `totalCount`, on an empty page, or on a page with no new
     (slug, language) pair.

7. **Boards past the 5,100-row window are split by facet.** Past row 5,100 some Boards repeat
   one fixed page (Costco, UHS), while others page on (PetSmart, JCPenney).
   - **The split.** A Board over the window is read as one query per `state` facet term, falling
     back to its categories, and the slices are unioned.
   - **Truncation.** A slice that hits the window, seen as a page that repeats, is a hard cap
     (`mark_truncated`). Any other shortfall against `totalCount` goes to
     `mark_truncated_unless_negligible`.
   - **Why the window is detected rather than predicted.** The coordinator's rule was "a slice
     over the window". The code instead marks the cap only when the window actually bites, because
     not every Board has it: PetSmart pages to 10,943 without repeating. A slice over 5,100 on such
     a Board is read whole and is not truncated.
   - **End to end.** costco 20,093 of 20,093 (49 states, 19 min), hrblock 16,924 of 16,924 (17
     min), greatclips 11,856 of 11,858 (categories, 11 min), uhs 5,774 postings (16 min). None
     was truncated.

8. **The company name is the board page's `<title>`.** It goes through
   `company_name.PATTERNS["jibe"]`, and 887 of 1,116 live clients yield a name. Otherwise the slug
   is kept, which is a readable word on this ATS. The per-row `hiring_organization` is never used:
   it varies within 68 of 277 Boards.

9. **It lands active.**
   - **Volume.** The ledger's 858 hiring clients, after exclusions, parks and the alias, list
     332,801 rows. At 17.9 KB a row, that is about 5.95 GB fetched a run.
   - **Tech Jobs.** After the iCIMS drop, `is_tech` keeps an estimated 16,821 of the postings
     that remain.
   - **Against the bar.** That is about **354 KB per tech Job**, 5.6 times under ADR-0158's
     2 MB bar.

## Alternatives considered

- **Treat the vanity host as the Board.** It needs alias handling for every multi-site client.
  DNS discovery cannot produce vanity hosts, and the answers are identical anyway.
- **Treat the backing iCIMS tenant's `Disallow: /` as governing the Jibe front.** robots.txt is
  per host. The front is the employer's own published career site, which explicitly allows
  crawling. The user chose to honour each host's own file.
- **A per-client overlap gate, or letting Jibe replace the overlapping iCIMS Boards.** These were
  covered under decision 3.
- **Mark Boards over the window truncated.** A truncated Board leaves eviction scope and accretes
  dead rows with no drain (ADR-0053). The facet split reads them whole.

## Consequences

- **Some Boards run long.** A Board's scrape time is its request count times 5 s. The largest
  (costco, about 250 requests) takes about 19 minutes, inside a shard's 60-minute budget but
  large enough to dominate a shard. The per-host pace is the published rule, not a measured knee,
  so it cannot be tuned down.
- **Ledger counts overstate Jobs.** A ledger row's `jobs` is `totalCount`: rows per language,
  before the iCIMS drop. A client whose postings all sit on readable iCIMS tenants (compsych: 44
  of 44) is a Hiring Board that yields no Jobs.
- **A tenant flip moves postings between ATS labels, with a gap.** When a tenant starts allowing
  crawling, its postings leave Jibe on the next run. iCIMS serves them only after its ledger
  re-probes that tenant.
- **Follow-ups outside this change:**
  - Readable iCIMS tenants our iCIMS ledger does not hold: about 1.1% of rows, listed for a
    later landing.
  - Three iCIMS ledger rows now `Disallow` with a 403 sitemap: goauto, concorde and uti.
  - The shared salary parser rounds a figure before annualising it (13.66 an hour is read as 14).
