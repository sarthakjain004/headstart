# ADR-0234: A PeopleStrong Board is its portal label, read through one paced budget

**Status:** accepted · **Date:** 2026-09-25 · **Relates to:** [ADR-0001](0001-per-ats-slug-derivation.md) (a scraper's slug is its own to define), [ADR-0048](0048-skip-details-we-already-hold.md) (skipping a held detail — deliberately not applied here), [ADR-0111](0111-duplicate-boards-resolve-the-board-surface.md) (alias keys), [ADR-0158](0158-jazzhr-and-jobvite-are-worth-their-storage.md) (the enable bar), [ADR-0166](0166-gate-the-detail-pass-on-the-tech-filter.md) (the pre-detail tech gate), [ADR-0167](0167-a-scraper-may-decline-the-multiplexed-path.md) (declining the multiplexed path), [ADR-0180](0180-an-adp-board-is-a-career-center-read-in-every-language-at-one-paced-budget.md) (a platform-wide paced budget)

## Context

PeopleStrong sat on CLAUDE.md's build list as "Angular SPA XHR — opportunistic", and
`docs/learnings.md` (2026-06-21) had dropped it from discovery because its "candidate portals are
login-walled". Measuring the live platform before building
(`docs/peoplestrong/2026-09-25_candidate-portal-measurement.md`: a 423-label census, then every
listing row and every detail of all 56 hiring Boards, 35,732 postings) replaced both premises.

1. **The portals are public.** Each candidate portal, `{label}.peoplestrong.com`, is served by an
   API on its own host that needs no session: `POST .../cp/jobs/v1` with an empty body lists the
   Board, and `GET .../cp/job/{code}/v2` returns the posting with its description (99.8%). The
   "login-walled" hosts (`abfrl`, `aavashrms`) are PeopleStrong HRMS logins, not candidate portals.
2. **The platform says which labels are portals.** A registered portal states `totalRecords`. A
   host that is not a portal answers 200 with a code-201 `getTpUrl` envelope. A departed tenant's
   host answers HAProxy's deny page with a 403. The zone is a wildcard, so DNS never decides.
3. **One budget spans every tenant.** Kong allows 5,000 requests per calendar minute per client IP
   across all tenants and endpoints, and refuses the rest with a bare 429.
4. **The edge closes every connection and drops some connection attempts.** HTTP/1.1 with
   `Connection: close` on 60 of 60 responses, and 18 of 60 fresh connections took ~2 s to connect
   (a dropped SYN). The same latency showed through the repo's fetch seam and a plain
   `requests.Session`.
5. **Nothing is duplicated.** No job code, and no title+location+description, appears on two
   PeopleStrong Boards. Six employers we already hold elsewhere, scraped live and compared title by
   title, shared one title in total (BMW TechWorks with Zwayam).

## Decision

**A Board is the portal's subdomain label, lowercased.** It is the URL, the API key and the
discovery key. `slug_from` lowercases it (`Careers-BMWTechWorks` reads the same Board) and reduces
a host or URL to its label. The Job's native id is the job code in its URL form, each `/` written
as `_`, which equals the API's own `jobDetailUrl` on 35,732 of 35,732 postings.

**The listing walks pages of 99 by row offset until a short page.** `limit` clamps silently at 99,
and `offset` counts rows. A walk short of the stated `totalRecords` is marked truncated (the two
were equal on 56 of 56 Boards), as is a walk that hits the 500-page cap. A listing that answers
without a `totalRecords` envelope (the 201 `getTpUrl` answer of a host that is no longer a portal)
is noted unreadable and read as the departed tenant it measures as.

**Every request waits on one process-wide pacer, and a 429 rests the whole process.** Request
starts are spaced at 16 ms, 3,750 a minute or 25% under the budget, ADR-0180's headroom. A 429 rests
every request for the window and retries, three attempts in all. A listing still refused is marked
truncated; a detail still refused is a labelled loss. The policy lives in the scraper's `_fetch` and
`_fetch_async` overrides, so the base class's `run_detail_pass` runs unchanged.

**The detail pass runs on 48 threads, not the multiplexed path** (`async_fanout = False`,
ADR-0167). The edge allows no reused connection, so the shared `AsyncSession`'s 10-connection cap
held the multiplexed path to 13.0–13.5 details/s whatever its width. Threads spend their time
waiting on connects, so width buys rate: L&T's 1,394 details ran at 20.7/s on 16 threads and 39.3/s
on 48, and Muthoot's 16,315 at 37.5/s, all clean.

**The tech gate is exact** (ADR-0166): title and `organizationUnit` both come off the listing, and
the detail changed neither. 1,964 of 35,732 postings (5.5%) pass, so the gate spares about 94% of
detail requests. **ADR-0048's held-description skip is not taken**: the detail is the only source of
`employment_type`.

**Fields.** Location is the place path in the provider's order, repeats dropped. Remote is read
off the location, since nothing states it. Experience is the listing's `expRange`. `posted_at` is
`jobPostedDate`. Employment type is the detail's word, with the three Indian payroll words
`flags` cannot read (`On Roll`, `Employee`, `Regular`, 762 postings) labelled "Full Time (…)".
**Salary is not served**: the job page shows the stated figure only where the employer's display
config sets `ctcMaxRendered` (365 of 35,732 postings), bare and in mixed units. Employment type is
display-gated the same way (20,516 postings) but served regardless, because it is a plain fact about
the job rather than a figure the employer chose not to publish. **Company name** stays the label:
nothing names the employer at Board level.

**The alias key is the label the portal names itself** (`urlinfo`'s `url`), a key in the slug's own
space, so `dedupe_boards.py` compares like with like. It equalled the label on 104 of 104 live Boards.

**Liveness.** One `POST jobs/v1?limit=1`: `totalRecords` is LIVE with that count (0 included), the
201 `getTpUrl` envelope is DEAD, and anything else is UNKNOWN (another 403 body, the vendor's LMS and
helpdesk hosts answering HTML, 429, 5xx, and any DNS failure on the wildcard zone). HAProxy's exact
deny body is DEAD **only when two pinned addresses both get it**: a direct ask and a spare-egress
ask, each sent outside the host's egress group so nothing can re-route it. A bare 403 trips no
prober gate, so the same page served to our IP would otherwise write every Board dead. The probe's
first ask cannot count as one of the two: once a 429 walls the group, it already rides the spare
egress (the code review of #724 caught the first draft asking that address twice). A real answer
from either pinned address is read instead; with no spare egress, or no answer, the row stays
UNKNOWN. Measured:
four departed hosts denied both our address and WARP's (a different Cloudflare IPv6 address), while
live and unregistered hosts answered the same on both. `peoplestrong.com` is in `_SPANNING` and gated
at 50 req/s.

**The vendor's demo tenant is excluded.** `candidate.peoplestrong.com` serves 314–315 postings of test
data ("Test Job 1909", every code `BOS/…`) and is in `config.EXCLUDED_BOARDS`.

**The ATS lands active.** A run reads about 45 MB of listing (35,725 rows at ~1.3 KB) and 15 MB of
tech details (1,964 at 7.7 KB): about 31 KB per tech Job against ADR-0158's ~2 MB bar.

**Discovery**, over 426 pool labels: Wayback's CDX index for `*.peoplestrong.com` named 396 (265
found nowhere else); Common Crawl, 35 crawls back to CC-MAIN-2023-40, named 148 (16 only there);
GitHub code search 9 and local captures 5 more. Certificate transparency names none: the zone
carries a wildcard certificate. The ledger holds 104 live (57 hiring, 35,725 postings; 56 and 35,410 once the demo tenant is
excluded), 263 dead and 59 unknown. The census covered the first 423 labels; the ledger run probed
all 426.

## Alternatives considered

- **Render the job pages** (one upstream implementation's fallback). Rejected: the detail API
  carries the full description once asked for the page's own part list.
- **Serve the stated salary where `ctcMaxRendered` is set.** Rejected: about 100 postings, no
  currency or period, and units that range from rupees to lakhs. A wrong-unit figure is worse than
  none, and the description's own figures still reach Tier 2.
- **Gate employment type on `employmentTypeRendered` too.** Rejected: one more request per Board
  to hide a true, non-sensitive fact on 43% of postings.
- **Treat the HAProxy 403 as UNKNOWN.** Rejected: 65 departed tenants would be re-probed on every
  pass forever.
- **Treat the HAProxy 403 as DEAD on one address.** Rejected: a bare 403 trips no prober gate, so
  an IP-wide block serving the same page would write the whole ledger dead, and DEAD persists for
  its TTL (the jazzhr pass in #463 wrote 2,740). Never observed in ~90,000 requests, where overload
  drew Kong's 429 every time, but unobserved is not excluded; confirming from a second address
  costs one request per departed tenant per pass.
- **Keep the multiplexed detail path and raise its width.** Measured: width does not move it past
  the session's connection cap on an edge that allows no reuse.
- **Hand-compose the detail pass around the pacer, as ADP does.** Rejected: one `_fetch` override
  carries the same policy for the listing and both detail transports, and leaves `run_detail_pass`
  (ADR-0201) intact.

## Consequences

- 56 hiring PeopleStrong Boards (35,410 postings at the ledger run; 1,964 tech on the measured set)
  join the scrape list; the demo tenant's 315 stay out. The largest is Muthoot Fincorp at 16,315
  postings, 51 of them tech.
- Every PeopleStrong request in a process shares one pacer, and the platform's budget is per IP,
  so a shard that also runs the prober, or two scrapes on one address, can still draw 429s. Those
  are rested through and retried, and a Board still refused is marked truncated, never served short.
- Two shared-code defects surfaced and are left for their own changes: `employment_type_filter.
  flags("Third Party Agency")` reads part-time (3 postings), and `tech_filter` rejects "ADAS
  Function Development (C++)" in "Digital Car" (a recall miss on an embedded-software role).
- The earlier "login-walled" verdict in `docs/learnings.md` and `scripts/merge/merge_tenants.py`
  is superseded by this ADR, and both now point here.
