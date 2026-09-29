# ADR-0307: A Taleo section its twin host mirrors is buried onto the linked host

**Status:** accepted · **Date:** 2026-09-29 · **Amends:** [ADR-0186](0186-a-taleo-section-another-section-already-lists-is-an-alias.md) (a section is compared only with sections of its own host) · **Relates to:** [ADR-0188](0188-a-dedup-rule-change-is-a-trends-epoch.md) (`DEDUP_VERSION`), [ADR-0223](0223-a-taleo-or-adp-requisition-is-served-once-per-tenant.md) (one row per Tenant and requisition), [ADR-0265](0265-a-radancy-front-listing-only-what-another-front-lists-is-an-alias.md) (the front the employer links to is kept), #888

## Context

ADR-0186 buries a Taleo Enterprise career section whose requisitions another section already
lists, but only within one host, because "a tenant is one host". Its Consequences recorded eight
host pairs that break that premise. Each pair is one Taleo tenant answering under two hosts, and
each posting is scraped and served once per host. The index's one-row-per-requisition rule
(ADR-0223) keys a Tenant on the host too, so it keeps both copies.

Measured on 2026-09-29 with two full listing walks (05:16 and 05:19 UTC) of every ledger section
on the 16 hosts, through the scraper's own `_listing`. Served rows are from served table v499,
read off HF.

| twin host | employer's host | sections with a twin | reqs each | served rows on the twin |
| --- | --- | ---: | ---: | ---: |
| `pruitthealthcareers` | `pruitthealth` | 2 | 1,417 / 1,414 | 3 |
| `careerglobalhc` | `hyundaicapital` | 3 | 83 | 20 |
| `elsewedyelectric` | `aa010` | 2 | 41 / 2 | 18 |
| `ouhk` | `hkmu` | 4 | 17 / 17 / 1 / 1 | 14 |
| `daimler` | `tas-daimler` | 4 | 437 | 173 |
| `percepta` | `ttec` | 2 | 100 / 8 | 26 |
| `manpower` | `manpowergroup` | 2 | 86 | 4 |
| `gb-corporation` | `ghabbour` | 1 | 60 | 2 |

- **The id sets are equal**, section for section. On every shared id the title and requisition
  number (`contestNo`) agree too. The one difference was in walk 2, covered below.
- **The two hosts are one tenant, not two tenants that copy each other.** Each host answers
  sections that only the other has a ledger row for: `ttec`'s `10000` listed percepta's 69,
  `hkmu`'s `ex_st` ouhk's 3, `tas-daimler`'s `dnac` 435, `manpowergroup`'s `jobsearch` 86,
  `careerglobalhc`'s `hca` 83 and `pruitthealthcareers`'s `2` 1,414 (6 of 6 tried). Both hosts
  of each pair also serve the same `portalNo` (16 of 16 shells).
- **A long walk is not always the same set.** `pruitthealth`'s section `1` read 1,417 on both
  hosts in walk 1, and again in two more sequential walks of each host. In walk 2 it read 1,367
  on one host and 1,392 on the other. ADR-0186 found twelve of twelve re-reads identical. A 67-page section can
  still come back short.

**Which host is the employer's.** Of two mirrors, ADR-0186 keeps the lowest URL. That would keep
five twins the employer does not link to, as Radancy's language twins would have (ADR-0265):

| kept | evidence (read 2026-09-29) |
| --- | --- |
| `hyundaicapital` | hyundaicapitalamerica.com/Careers.html links `hyundaicapital.taleo.net/careersection/hca` |
| `tas-daimler` | `jobs.api.mercedes-benz.com` gives each posting the `ApplyURI` `tas-DAIMLER.taleo.net/careersection/ex/jobapply.ftl` (3 of 3 read) |
| `aa010` | careers.elsewedyelectric.com links `aa010.taleo.net/careersection/swd_external` and `swd_gdp`. It names `elsewedyelectric.taleo.net` only for Taleo's `smartorg` login |
| `ghabbour` | gb-corporation.com/careers links `ghabbour.taleo.net/careersection/white_collar_external`, and so does `gb-corporation`'s own shell |
| `manpowergroup` | a ManpowerGroup press release on PR Newswire links `manpowergroup.taleo.net/careersection/mp_external` |
| `ttec` | ttecjobs.com links `ttec.taleo.net/careersection/2`, and so does `percepta`'s own shell |
| `hkmu` | nothing read: hkmu.edu.hk answers 403. The lower host, and the university's name since 2021 (OUHK was renamed) |
| `pruitthealth` | nothing read: the careers front is Phenom, whose apply stays on Phenom. The lower host |

## Decision

**A section of a twin host is buried onto the employer's host's section that lists all its
reqs**, in the same `subset-reqs` ledger, `data/validate/aliases/taleo_enterprise.csv`. The same
writer, `scripts/validate/taleo_enterprise_subset_sections.py`, does it after ADR-0186's per-host
election, which is unchanged.

- **The pairs are named by hand**, in the writer's `TWIN_HOSTS`, `{twin host: employer's host}`.
  A pair is added when a second host answers a held section with the same req ids.
- **Only a section the per-host election left unburied is compared.** It is buried onto the
  largest kept public section of the employer's host that lists all its reqs, then the lowest
  URL. The twin sections the per-host election had buried onto it follow it there.
- **Nothing of the employer's host is ever buried onto its twin.** If a twin section lists a req
  the employer's host does not, both stay, as with ADR-0186's partial overlaps. A section of the
  employer's host that its twin wholly contains stays too.
- **A non-public section is never the kept one** (ADR-0186's amendment), on either host.
- **A section the employer's host has no ledger row for stays on the twin.** Examples are
  `percepta`'s `10000`, `10005`, `10300`, `10400`, `4` and `5`, and `ouhk`'s `ex_st`. The writer
  compares only sections on live ledger rows, and these have no copy on the employer's host that
  we scrape.
- **Elsewedy Electric's curated name moves to `aa010`'s section**
  (`config/company_names.csv`), because the section that stays serves the name.
- **`index_plan.DEDUP_VERSION` goes to 11** (ADR-0188), because it is a new grouping for an
  existing signal. Version 10 is #891's.

## Evidence

The writer ran twice against the committed ledger, at 05:33 and 05:42 UTC. Each run read 561 of
563 sections; the two unreadable were `cwt`'s. Apart from the cross-host rows, neither run changed
the alias ledger, except for three Hyatt internal sections the second run buried onto
`careersection/1` (see Consequences).

| run | cross-host burials | re-pointed onto the employer's host |
| --- | ---: | ---: |
| 05:33 | 12 | 7 |
| 05:42 | 13 | 7 |

The first run missed `percepta`'s `2`: it read 100 reqs there and 99 on `ttec`'s `2` later in
the same run. A posting had closed in between; a back-to-back read gave 99 and 99. The error is
safe: a twin that reads larger stays unburied, and the pair is served twice until the next run.
The committed ledger is the second run's.

The 13 buried twin sections: `careerglobalhc` `ex`; `daimler` `dnac`; `elsewedyelectric`
`swd_external`; `gb-corporation` `white_collar_external`; `manpower` `jobsearch`; `ouhk`
`ex_full_time`, `ex_non_full_time`, `in` and `mgr_post`; `percepta` `2` and `10020`;
`pruitthealthcareers` `1` and `2`. The seven re-pointed rows are `careerglobalhc` `hca` and `hcca`,
`daimler` `ex`, `fusojp` and `ukexternal`, `elsewedyelectric` `swd_gdp` and `manpower`
`mp_external`.

**Projected onto served v499: 260 rows leave, and none of them is a lost posting.** Every one of
the 260 job ids is served on the employer's host too, the same id with its own row (260 of 260).
By section: `daimler/dnac` 173, `percepta/2` 26, `careerglobalhc/ex` 20,
`elsewedyelectric/swd_external` 18, `ouhk/ex_non_full_time` 11, `ouhk/ex_full_time` 3,
`pruitthealthcareers/1` 3, `manpower/jobsearch` 4 and `gb-corporation/white_collar_external` 2.

## Alternatives considered

- **Keep the lowest URL, as ADR-0186 does.** Rejected. It keeps `careerglobalhc`, `daimler`,
  `gb-corporation`, `manpower` and `percepta`, which the employers do not link to. `careerglobalhc`
  would also lose #878's curated "Hyundai Capital America".
- **Detect twin hosts from the listings.** This would join two hosts when one section of each
  lists the same set. Rejected: the req ids are small tenant-scoped numbers (`hkmu`'s `in` and
  `mgr_post` list one each), so two unrelated tenants can list the same set by chance. No listing
  names the employer's host either, so the election would still need a hand list.
- **Make the twin part of the host's Tenant** (`board_identity.tenant`), so that ADR-0223's
  one-row-per-requisition rule keeps one copy. This would reach partial overlaps too. Rejected
  for this change: both hosts would still be scraped, and the Tenant is also what the company
  directory and Trends group on.
- **Move the twin's rows onto the employer's host**: land `ttec.taleo.net/careersection/10000` and
  the rest, and drop the `percepta` rows. This would serve every section from the employer's host,
  but it rewrites the liveness ledger for eight tenants. Left as a follow-up.

## Consequences

- **Scrapable Board** falls by 16, 164,287 → 164,271, and **Hiring Board** by 15,
  109,233 → 109,218, against the ledger at merge-base `5eb7de7d`. That is the 13 twin sections and three Hyatt internal sections
  (`clearwater_internal`, `hc_all_jobs_internal`, `wallstreet_internal`). The second run found the
  Hyatt sections inside `careersection/1` (3,250 reqs each). They serve no row on v499, so burying
  them evicts nothing; they had flapped in and out of containment before.
- **One cross-host duplicate row stays served.** `percepta`'s `10005` shares 3 of its 11 reqs with
  `ttec`'s `2`, and one of the three is served on both hosts. It is a partial overlap, so neither
  section is buried.
- **A new twin pair is found by hand**, as the eight were: by a person who notices one tenant under
  two names. Nothing detects it.
- **The burial is only as fresh as the last run**, as with ADR-0186. A twin section that starts
  listing its own reqs comes back on the next run, and so do a twin and its employer's section read
  a posting apart.
