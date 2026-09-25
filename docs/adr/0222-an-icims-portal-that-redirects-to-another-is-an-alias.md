# ADR-0222: An iCIMS portal that redirects to another is an alias

**Status:** accepted · **Date:** 2026-09-25 · **Relates to:** [ADR-0111](0111-duplicate-boards-resolve-the-board-surface.md) (the redirect signal and the alias ledger, applied here unchanged), [ADR-0182](0182-a-clearcompany-board-is-its-hrm-direct-feed.md) and [ADR-0186](0186-a-taleo-section-another-section-already-lists-is-an-alias.md) (the hand-run footing followed here), [ADR-0188](0188-a-dedup-rule-change-is-a-trends-epoch.md) (the Trends epoch), [ADR-0189](0189-a-jibe-board-is-a-client-read-under-its-own-robots-rules.md) (Jibe's iCIMS drop)

## Context

An iCIMS Board is one portal host, `{portal}-{customer}.icims.com`. A customer often runs several
portals, and iCIMS job ids belong to the customer, so every portal lists the same posting under
the same id. Many retired or secondary portals redirect to the customer's main one:
`careers-gd-ais`, `careers-c4s`, `cybercareers-gdms`, `cybercareers-gd-ais` and
`university-gd-ais` all answer `/sitemap.xml` with a redirect to `careers-gdms`, and
`peraton-perspecta` redirects to `careers-peraton`. The scraper follows the redirect, reads the
main portal's sitemap and indexes it again under the redirecting host's `board_key`. So each
posting is served once per portal.

iCIMS is the largest class of duplicate rows across Boards: 5,190 by exact description on served
v65 (critique #2, 2026-09-25). General Dynamics' portals alone held 2,215 rows and Peraton's pair
1,091. The ADR-0111 alias ledger and `dedupe_boards.py` already existed, but had never been run
for iCIMS.

## Decision

**An iCIMS Board on a Live row whose sitemap redirects to another Board on a Live row is buried
onto it**, in
`data/validate/aliases/icims.csv` with signal `redirect`. This is ADR-0111 as shipped: the default
`alias_key` fits because an iCIMS slug is a host, and the survivor is the redirect's target, so no
cluster needs an election. No code changes.

- **The writer is `dedupe_boards.py --ats icims --apply`, and `--apply` is safe here.** Every row
  in the file comes from the redirect scan, so rewriting the file from the scan loses nothing. If
  another writer ever adds a row with a different signal, the script's existing check refuses
  `--apply`, as it does for ClearCompany and Taleo Enterprise.
- **It runs by hand, after every refresh of `data/validate/liveness/icims.csv`**, on the same
  footing as ClearCompany's and Taleo Enterprise's writers. CLAUDE.md's landing rules carry the
  step. A full scan is 4,166 GETs, about ten minutes.

## Evidence

Measured 2026-09-25 against the Boards on the committed iCIMS ledger's 4,166 Live rows (4,166
distinct slugs):

- **Two full scans about an hour apart gave the same answer.** Both found 230 clusters and 272
  Boards to bury, pair for pair. Critique #1 had counted the same 272 in 230 clusters on
  2026-09-24.
- **Every cluster was checked live, not a sample.** All 502 member sitemaps (272 buried Boards and
  their 230 survivors) were read the way the scraper reads them. For 272 of 272 buried Boards, the
  sitemap answered 200, landed on the survivor's host, and listed exactly the survivor's job-id
  set. 200 of these sets were non-empty, 39,546 postings in all (every role, not only tech). No
  buried Board lists a posting its survivor does not.
- **54 Boards resolved to a host on no Live row of the iCIMS ledger** and are reported only
  (`migrated`, ADR-0111). Some redirect to the customer's own site (`careers-gdit` →
  `www.gdit.com`), some to another ATS (five Mimecast portals → Workday), and some to an iCIMS
  host the ledger holds on no Live row. Scraping these returns the target's list or nothing, and
  that list has no other copy, so none is buried.

Projected onto served v65 (518,846 rows, 20,480 iCIMS), opened read-only:

| | |
| ---: | --- |
| 114 | buried Boards with served rows |
| 5,265 | served rows the next `prune` evicts |
| 15,218 → 15,215 | distinct (survivor, job id) tech postings served |

Three tech postings had no copy on their survivor in v65. None is a posting the survivor lacks:

- `careers-avantus` 12310 (QinetiQ): closed. Neither sitemap lists it, so `sync` would evict it
  anyway.
- `careers-virginpulse` 4849 → `careers-personifyhealth`, and `careers-trnty` 3441 →
  `careers-ricardo`: posted 2026-09-24, after the survivor was last read. Both are on the
  survivor's sitemap today, so its next scrape serves them. Until then they are not served:
  `prune` evicts the buried copy with no grace period. The gap can last several runs. Both
  survivors rank outside the 6,000-Board Head (`board_priority.csv`, 2026-09-25:
  `careers-ricardo` 7,111th, `careers-personifyhealth` 8,505th), so each waits for the random
  Tail to pick it.

## What this does not catch

**Portals that share job ids without redirecting.** Critique #2 lists 184 iCIMS–iCIMS Board
pairs by shared descriptions. The redirect clusters join 125 of them and miss 59:

- **28 Boards** whose served job ids sit inside a sibling portal's, about **270 served rows**.
  Examples: `uscareers-fujifilm` against `uscareershub-fujifilm` (59 of 59), `careersus-shure` and
  `careers-shure` against `careershub-shure`, and `careers-wyn` against `careers2-wyn`.
- **20 partial-overlap pairs** sharing 78 served rows. These stay unaliased by rule: burying either
  side would hide the postings only it lists. Each pair below is shared ids / ids on each side,
  on served v65:
  - `jobs-bylight` with `jobs-metova`, and with `jobs-cesi`: 15 / 81, 18 each.
  - `careers-eastpennmanufacturing` with `careersnavitas-eastpennmanufacturing`: 5 / 19, 6.
  - `careers-cis` with `careers-darkbladesystems`: 4 / 10, 7.
  - `careers-chemtradelogistics` with `careers2-chemtradelogistics`: 4 / 5, 6.
  - `externalhourly-highgate` with `externalmanagement-highgate`: 3 / 38, 15.
  - `careers-steeldynamics` with `careers-newmillennium` (3 / 10, 8) and with
    `careers-aluminumdynamics` (3 / 10, 10).
  - `careers-en-nortal` with `career-eu-nortal` and with `career-de-nortal`: 3 / 31, 7 each.
  - `corporatecareers-{aus,alliedbarton}` with `securitycareers-{aus,alliedbarton}`: 4 pairs,
    2 / 29, 76 each.
  - `careers-quanta` with `careers2-quanta`: 2 / 51, 245.
  - `careers-melaleuca` with `studentcareers-melaleuca`: 2 / 8, 3.
  - `careers-davidsonhospitality` with `management-davidsonhospitality`: 2 / 36, 11.
  - `globalcareers-atlassian` with `careers-apac-atlassian` (2 / 23, 34), `careers-americas`
    (2 / 23, 63) and `campus-americas` (2 / 23, 5).

A native-id containment writer like Taleo's `subset-reqs` could catch the first group, but it is
**not built**, for two reasons:

- **Job ids are per customer, not global.** Id 4849 is served on four unrelated customers
  (`careers-sysmex`, `careers-americansystems`, `jobs-auxis`, `careers-virginpulse`). Containment
  therefore needs a customer grouping, and the host does not provide one: `careers-gdms` and
  `careers-c4s` are one customer, while `careers-sysmex` and `careers-americansystems` are two.
- **The only customer key found is undocumented.** It is the `hashed=` value on the listing
  page's links. On 2026-09-25 it agreed within each of 8 same-customer portal groups and differed
  across 6 other customers, but nobody has measured whether it stays stable over time.
- **The host's customer suffix is not enough either.** Grouping by it would catch Fujifilm,
  Shure, Wyn and East Penn, but it is a guess made from the spelling of the host. It would also
  miss containments across differently named hosts of one company: `teamwork-ovg` sits inside
  `careers-comcast-spectacor`, and `careers-gocs` inside `careers-gilbaneco`.

The two groups together are about 350 rows, 7% of what the redirect removes.

## Consequences

- **Scrapable Board** falls 154,033 → 153,761 (−272) and **Hiring Board** 101,482 → 101,280
  (−202). `index prune` evicts the buried Boards' rows through its existing off-Board path.
- **`DEDUP_VERSION` is not bumped in this change.** `index_plan`'s rule says not to bump it for
  an alias-ledger rewrite that applies an existing signal, and `redirect` was in version 1. Even
  so, the first `prune` removes about 5,265 rows in one tick, and ADR-0188 exists so that Trends
  does not read a step of that size as a hiring drop. Whether to mark it is left to the merge.
- **Jibe loses nothing.** Jibe drops a posting whose iCIMS apply host lets the sitemap be read,
  whether or not that host is held (ADR-0189). A posting on a buried host is still served, because
  the host's survivor lists the same id.
- **The gap is ADR-0186's.** A burial is only as fresh as the last writer run. If a buried portal
  stops redirecting and starts listing postings of its own, nothing scrapes it until the scan runs
  again. The landing rule is what closes the gap.
