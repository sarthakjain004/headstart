# ADR-0307: A Taleo section its twin host mirrors is buried onto the linked host

**Status:** accepted · **Date:** 2026-09-29 (amended 2026-09-30) · **Amends:** [ADR-0186](0186-a-taleo-section-another-section-already-lists-is-an-alias.md) (a section is compared only with sections of its own host) · **Relates to:** [ADR-0188](0188-a-dedup-rule-change-is-a-trends-epoch.md) (`DEDUP_VERSION`), [ADR-0223](0223-a-taleo-or-adp-requisition-is-served-once-per-tenant.md) (one row per Tenant and requisition), [ADR-0265](0265-a-radancy-front-listing-only-what-another-front-lists-is-an-alias.md) (the front the company's site links to is kept), #888

## Context

ADR-0186 buries a Taleo Enterprise career section whose requisitions another section already
lists, but only within one host, because a Taleo Enterprise **Tenant** is its host. Its
Consequences recorded eight host pairs where one Taleo customer is served under two hosts. Each
posting on those pairs is scraped and served once per host. The index's one-row-per-requisition
rule (ADR-0223) keys the Tenant on the host too, so it keeps both copies.

This ADR calls the second host a **Twin host**, and the host the company's own careers site links
to its **linked host** (CONTEXT.md). "The employer's host" is avoided on purpose, because
`employer` is an **Operator** value, and `manpower` is a staffing Operator.

Measured on 2026-09-29 with two full listing walks (05:16 and 05:19 UTC) of every ledger section
on the 16 hosts, through the scraper's own `_listing`. Served rows are from served table v499,
read off HF.

| twin host | linked host | twin sections with a copy | reqs each | served rows on the twin |
| --- | --- | ---: | ---: | ---: |
| `pruitthealthcareers` | `pruitthealth` | 2 | 1,417 / 1,414 | 3 |
| `careerglobalhc` | `hyundaicapital` | 3 | 83 | 20 |
| `elsewedyelectric` | `aa010` | 3 | 41 / 2 / 0 | 18 |
| `ouhk` | `hkmu` | 4 | 17 / 17 / 1 / 1 | 14 |
| `daimler` | `tas-daimler` | 4 | 437 | 173 |
| `percepta` | `ttec` | 2 | 100 / 8 | 26 |
| `manpower` | `manpowergroup` | 2 | 86 | 4 |
| `gb-corporation` | `ghabbour` | 1 | 60 | 2 |

- **The id sets are equal**, section for section. On every shared id, the title and the
  requisition number (`contestNo`) agree too. The one difference was in walk 2, covered below.
- **The two hosts serve one customer; they do not copy each other.** Each host answers sections
  that only the other host has a ledger row for:
  - `ttec`'s `10000` listed percepta's 69;
  - `hkmu`'s `ex_st` listed ouhk's 3;
  - `tas-daimler`'s `dnac` listed 435;
  - `manpowergroup`'s `jobsearch` listed 86;
  - `careerglobalhc`'s `hca` listed 83;
  - `pruitthealthcareers`'s `2` listed 1,414.

  That is 6 of 6 tried. Both hosts of each pair also serve the same `portalNo` (16 of 16 shells).
- **A long walk can come back short.** `pruitthealth`'s section `1` read 1,417 on both hosts in
  five of six walks of each host. In walk 2 it read 1,367 on one host and 1,392 on the other.
  ADR-0186 had found twelve of twelve re-reads identical.

**Which host is linked.** Of two mirrors, ADR-0186 keeps the lowest URL. That would keep five
twins that the company does not link to, as it would have kept Radancy's language twins
(ADR-0265).

| linked host | evidence (read 2026-09-29) |
| --- | --- |
| `hyundaicapital` | hyundaicapitalamerica.com/Careers.html links `hyundaicapital.taleo.net/careersection/hca` |
| `tas-daimler` | `jobs.api.mercedes-benz.com` gives each posting the `ApplyURI` `tas-DAIMLER.taleo.net/careersection/ex/jobapply.ftl` (3 of 3 read) |
| `aa010` | careers.elsewedyelectric.com links `aa010.taleo.net/careersection/swd_external` and `swd_gdp`. It names `elsewedyelectric.taleo.net` only for Taleo's `smartorg` login |
| `ghabbour` | gb-corporation.com/careers links `ghabbour.taleo.net/careersection/white_collar_external`, and so does `gb-corporation`'s own shell |
| `manpowergroup` | a ManpowerGroup press release on PR Newswire links `manpowergroup.taleo.net/careersection/mp_external` |
| `ttec` | ttecjobs.com links `ttec.taleo.net/careersection/2`, and so does `percepta`'s own shell |
| `hkmu` | nothing read: hkmu.edu.hk answers 403. The lower host, and the university's name since 2021 (OUHK was renamed) |
| `pruitthealth` | nothing read: the careers front is Phenom, and its apply stays on Phenom. The lower host |

## Decision

**A twin host's section is buried onto its linked host**, in the same `subset-reqs` ledger,
`data/validate/aliases/taleo_enterprise.csv`. The same writer,
`scripts/validate/taleo_enterprise_subset_sections.py`, does it after ADR-0186's per-host
election, which is unchanged.

- **The pairs are named by hand**, in the writer's `TWIN_HOSTS`, as `{twin host: linked host}`.
  A pair is added when a second host answers a held section with the same req ids.
- **A twin section with a same-path row goes to it.** If the linked host has a live ledger row
  at the same path, the twin section is buried onto that section, whatever either walk read. The
  path is the stable identity: on a declared pair it is the same section. Containment rests on
  both walks being complete, and a walk can come back short. The 16 same-path pairs read equal in
  13 of 13 readable pairs in walk 1 and in 15 of 16 in walk 2. The miss was `pruitthealth`'s `1`,
  which containment would have left unburied for a run. Unburied, that section's 1,417 postings
  are scraped again and its 3 tech rows are served twice, then evicted by the next run.
  An empty twin section with a same-path row is buried too (`elsewedyelectric`'s
  `swd_summer_internship`); it has the same identity.
- **A twin section with no same-path row falls back to containment.** If the per-host election
  left it unburied, it is buried onto the largest kept public section of the linked host that
  lists all its reqs, then the lowest URL (`alias_ledger.largest_containing`, the helper
  `bury_contained_keeping_public` also uses). That covers `careerglobalhc`'s `ex`, `daimler`'s
  `dnac` and `manpower`'s `jobsearch`. Their containment held in 5 of 5 reads each: walks 1 and
  2 and three writer runs.
- **Sections buried onto a buried section follow it**, however many links that takes: a twin
  section buried within its host onto one that then goes to the linked host, or a linked section
  that is itself buried within its host.
- **Nothing of the linked host is ever buried onto its twin.** If a twin section with no
  same-path row lists a req that the linked host does not, both stay, as with ADR-0186's partial
  overlaps.
- **A non-public section is never kept by containment** (ADR-0186's amendment), on either host.
- **A twin section with no copy on the linked host stays on the twin.** Examples are
  `percepta`'s `10000`, `10005`, `10300`, `10400`, `4` and `5`, and `ouhk`'s `ex_st`. The linked
  host holds no ledger row for any of them.
- **Elsewedy Electric's curated name moves to `aa010`'s section**
  (`config/company_names.csv`), because the section that stays is the one whose name is served.
- **`index_plan.DEDUP_VERSION` goes to 11** (ADR-0188), because this is a new grouping for an
  existing signal. Version 10 is #891's.

## Evidence

The writer ran three times against the committed ledger. Each run read 561 of 563 sections; the
two unreadable were `cwt`'s.

| run (UTC) | rule | twin sections buried |
| --- | --- | ---: |
| 05:33 | containment only | 12 |
| 05:42 | containment only | 13 |
| 06:35 | same path, then containment | 14 |

The first run missed `percepta`'s `2`: it read 100 reqs there and 99 on `ttec`'s `2` later in the
same run, because a posting closed in between. The same-path rule buries it whatever the reads
say. The third run added `elsewedyelectric`'s empty `swd_summer_internship`.

**The committed file is main's same-host rows plus the third run's twin-host rows.** The runs
also moved same-host burials this change does not touch. Hyatt's internal sections went in and
out of containment, and the third run re-elected Hyatt's `10880` group onto
`administrative_intl`. Those rows serve nothing (v499 serves Hyatt only from `careersection/1`).
They are ordinary churn, so they are left for the next routine re-run, which keeps this change's
evictions attributable to it.

The 14 buried twin sections are:
- `careerglobalhc`: `ex`;
- `daimler`: `dnac`;
- `elsewedyelectric`: `swd_external` and `swd_summer_internship`;
- `gb-corporation`: `white_collar_external`;
- `manpower`: `jobsearch`;
- `ouhk`: `ex_full_time`, `ex_non_full_time`, `in` and `mgr_post`;
- `percepta`: `2` and `10020`;
- `pruitthealthcareers`: `1` and `2`.

Seven same-host rows now point at the linked host instead: `careerglobalhc` `hca` and `hcca`,
`daimler` `ex`, `fusojp` and `ukexternal`, `elsewedyelectric` `swd_gdp` and `manpower` `mp_external`.

**Projected onto the served table, 260 rows leave, and none of them is a lost posting.** Every
one of the 260 job ids is also served on the linked host, as the same id with its own row. This
held on v499 and again on v45, read after HF's history reset. By section:

| twin section | rows |
| --- | ---: |
| `daimler/dnac` | 173 |
| `percepta/2` | 26 |
| `careerglobalhc/ex` | 20 |
| `elsewedyelectric/swd_external` | 18 |
| `ouhk/ex_non_full_time` | 11 |
| `manpower/jobsearch` | 4 |
| `ouhk/ex_full_time` | 3 |
| `pruitthealthcareers/1` | 3 |
| `gb-corporation/white_collar_external` | 2 |

## Alternatives considered

- **Keep the lowest URL, as ADR-0186 does.** Rejected. It keeps `careerglobalhc`, `daimler`,
  `gb-corporation`, `manpower` and `percepta`, which the companies do not link to.
  `careerglobalhc` would also lose #878's curated "Hyundai Capital America".
- **Containment for every twin section.** This was the first version. Rejected for the flap
  above: a short walk of a large section un-buries its twin for a run.
- **Detect twin hosts from the listings**, which the issue suggested ("compare sections across
  hosts that share requisition ids"). This would join two hosts when one section of each lists
  the same set. Rejected on two counts:
  - The req ids are small, customer-scoped numbers. `hkmu`'s `in` and `mgr_post` list one each,
    so two unrelated customers can list the same set by chance.
  - No listing names the linked host, so the election would still need a hand list.
- **Make both hosts one Tenant** (`board_identity.tenant`), so that ADR-0223's
  one-row-per-requisition rule keeps one copy. This would reach partial overlaps too. Rejected
  for this change: both hosts would still be scraped, and the company directory and Trends
  group on the Tenant too.
- **Move the twin's rows onto the linked host**: land `ttec.taleo.net/careersection/10000` and
  the rest, and drop the `percepta` rows. Every section would then be served from the linked host,
  and every twin section would have a same-path row. But it rewrites the liveness ledger for
  eight customers. Left as a follow-up.

## Consequences

- **Scrapable Board falls by 14 and Hiring Board by 12**, against the ledger at merge-base
  `e3a50922`:
  - Scrapable Board 164,287 → 164,273;
  - Hiring Board 109,233 → 109,221.

  Two of the 14 sections hold no posting in the ledger: `swd_summer_internship` and
  `ouhk/mgr_post`.
- **One cross-host duplicate row stays served.** `percepta`'s `10005` shares 3 of its 11 reqs with
  `ttec`'s `2`, and one of the three is served on both hosts. It has no same-path row and is a
  partial overlap, so neither section is buried.
- **Three twin sections still rest on containment**: `careerglobalhc/ex`, `daimler/dnac` and
  `manpower/jobsearch`. A short walk of one can still un-bury it for a run. `dnac` is the large
  one, at 173 rows. Landing each section at the same path on the linked host would remove this
  exposure.
- **A new twin pair is found by hand**, as the eight were: someone has to notice one customer
  under two host names. Nothing detects it.
- **The burial is only as fresh as the last run**, as with ADR-0186. A twin section with no
  same-path row that starts listing its own reqs comes back on the next run.

## Amendment, 2026-09-30: seven more pairs from the DNS-sieve landing

Landing 290 Taleo Enterprise sections found by a DNS sieve exposed seven more twin pairs. Each was
read through the scraper's own listing walk on 2026-09-29: the two sections list the same ids, and
the same `contestNo` and title on every shared id. `TWIN_HOSTS` in
`scripts/validate/taleo_enterprise_subset_sections.py` now carries all fifteen.

| twin host | linked host | shared ids | why that host is linked |
| --- | --- | ---: | --- |
| `teletech` | `ttec` | 100 of 100 | as for `percepta` above: ttecjobs.com links `ttec.taleo.net` |
| `aa246` | `vontier` | 46 of 46 | careers.vontier.com job pages apply through `vontier.taleo.net/careersection/external/jobapply.ftl` |
| `aa333` | `mlgw` | 8 of 8 | mlgw.com links `mlgw.taleo.net/careersection/ext` |
| `tas-tgh` | `tgh` | 697 of 697 | tgh.org/careers links `tgh.taleo.net/careersection/ex` |
| `westpacnz` | `westpac` | 6 of 6 | nothing read; the lower name |
| `wsp` | `golder` | 104 of 104 | nothing read; the lower name |
| `kearney` | `atkcareers` | 2 of 2 | nothing read; the lower name |

- **Three of the seven rest on the lower-name fallback**, as `hkmu` did. For `wsp` and `kearney`
  the lower name looks like the older brand host, not necessarily the one each company links to
  today (wsp.com and kearney.com answered 403, so this could not be read). If the company's site
  links the other host, swap the pair; the election is by path, so nothing else changes.
- **An id-only overlap is not a twin.** `rossstores` and `schneider` share 129 ids and no title.
- **`tas-tgh` and `tgh` were both held before this landing**, so that pair was already served
  twice; landing more sections on the twin widened it before the pair was added.
- **`vontier`'s section 4 is parked, not landed as a Board** (`PARKED_BOARDS`,
  `taleo_enterprise:https://vontier.taleo.net/careersection/4`). It lists 46 postings and 38 of
  those reqs are on the held `phenom:careers.vontier.com` (133 postings; Vontier's Taleo
  `external` section redirects to that site), so each would be served twice under two ATS labels,
  the Phenom landing rule read in the other direction. Parking drops the 8 reqs only the Taleo
  section lists. `aa246`'s section 4 is buried onto it, so neither is served. Un-park if the Phenom
  front goes dead, or once cross-ATS deduplication exists.
- **Westpac** also has `workday:westpacnz` (22 postings) beside its old Taleo sections (6). Not
  acted on: names alone are a lead, not proof of a shared posting.
