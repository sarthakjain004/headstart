# Infrastructure discovery on the multi-tenant ATSes (batch of 2026-09-29/30)

The Zoho and Keka runs (#730, 2026-09-26) found Boards by reading **infrastructure** (certificates, DNS
records, reverse-IP) where every earlier method read URLs someone had written down. This batch asked
which other ATSes leak the same way, ran the ones that do, and separately researched surfaces for
the ATSes that do not. Everything below was measured on 2026-09-29/30 unless another date is given.
The per-method research for the ATSes with no oracle is in
[2026-09-29_new-board-discovery-methods-research.md](2026-09-29_new-board-discovery-methods-research.md);
the Zoho/Keka techniques are in the private playbook summarised by
[shared-cert-tenant-rosters.md](shared-cert-tenant-rosters.md).

## Triage: which ATSes leak, and how

One tenant host and one made-up label per vendor, asked three ways.

| Signal | Result |
| --- | --- |
| **TLS certificate** (SAN handshake on a known tenant) | Dead for every subdomain ATS. Each vendor serves one wildcard certificate (`*.csod.com`, `*.taleo.net`, `*.recruitee.com`, ...) with 1 to 5 SANs and no customer named. Only Zoho batches customers onto shared certificates. |
| **No wildcard DNS record**: a made-up label NXDOMAINs, a tenant resolves | Avature (`avature.net`), Cornerstone (`csod.com`), Taleo Enterprise (`taleo.net`), Jibe (`jibeapply.com`), RippleHire (`ripplehire.com`). A resolving label proves a provisioned host. |
| **Wildcard A record, but a tenant gets its own CNAME** | Darwinbox (`.in` and `.com`: a tenant has a CNAME to `{host}.cdn.cloudflare.net`, a made-up label has none) and PeopleStrong (a tenant has a CNAME into `*.ng.impervadns.net`). Keka's pod sieve is the same shape. |
| **Wildcard DNS and wildcard certificate, same answer for real and made-up** | BambooHR, Breezy, ClearCompany (`hrmdirect.com`), Freshteam, iCIMS, JazzHR, Personio, Pinpoint, Recruitee, SenseHQ, Teamtailor, Trakstar. No infrastructure oracle. |
| **Reverse-IP** (HackerTarget, capped at 500 names, alphabetical, about 50 lookups a day) | Recruitee `35.186.220.63` and Pinpoint `134.209.133.3` each sit on one address. The first 500 names were 82% and 94% already held. Saturated, and the cap cannot be walked (`page=2` is byte-identical to page 1). |

Jibe and RippleHire were measured saturated on a partial sample of up to 25,000 labels (18 of 19 and 4 of 4
hits already held) and not swept in full. The path-based ATSes (Ashby, Lever, Greenhouse, Workable, Gem, Join,
Rippling, SmartRecruiters, Jobvite) have no host to inspect at all.

A DNS existence oracle proves a **provisioned host, not a Board**: every ATS below needed a second step
(the scraper's own request, and for Taleo a career-section search) to say whether it lists anything.

## What each run landed

| PR | ATS | Technique | Rows added | Live | Hiring Boards (jobs) |
| --- | --- | --- | ---: | ---: | --- |
| #986 | Avature | DNS sweep (Tranco, company directory) plus urlscan `search_after` paging | 94 | 91 | 10 (2,173 sitemap ids; strabag 1,955) |
| #987 | PeopleStrong | CNAME sieve, then snowball from known tenants into portal shapes | 146 | 146 | 21 (373) |
| #988 | Darwinbox | CNAME plus explicit-A sieve over `.in` and `.com` | 424 | 385 | 103 (3,268) |
| #993 | Taleo Enterprise | DNS sweep for hosts, then a section finder (core list plus Wayback CDX) | 290 | 285 | 236 (46,019 listed; 116 kept after the alias ledger, 21,943 jobs) |
| #996 | Cornerstone | DNS sweep, about 1.0M labels | 1,450 | 168 | 16 real (589 postings, 8.7% tech) |
| #999 | Recruitee | passive DNS, redirect targets of renamed accounts, offers-API sieve | 452 | 411 | 350 net (6,103 jobs) |
| #995 | 13 ledgers | the research's namespace-enumerating surfaces (below) | 14,916 | 13,560 | see the PR |

Wordlist yield, in unheld hosts per label swept (a list that fell under **0.05%** on a 5,000-label sample
was stopped, and that stop rule saved most of the sweeps):

- India-buyer ATSes (Darwinbox): Instahyre employer sitemap 0.32%, Indeed employer labels 0.09%,
  AmbitionBox and Cutshort 0.07%.
- Global-buyer ATSes: Tranco top 5,000 0.24% (Avature), Tranco top 100,000 0.44% (Cornerstone),
  every 1-3 character label 0.39% (Cornerstone; three-letter alphabetic labels 1.07%), the
  `aa###` code pocket 4.1% of 1,952 labels (Taleo).
- PeopleStrong: plain wordlists were dead (under 0.02%). What worked was finding a customer's bare HRMS
  host and expanding each tenant root into portal shapes (`{brand}careers`, `careers-{brand}`, ...),
  2.7% tenants and about 45% live, saturated after five rounds.
- Nearly half of what Cornerstone's DNS sweep found was demo or test tenants, and about 78% of what
  landed was siteless: expect roughly one hiring Board per 100 rows landed there.

## The research surfaces (#994 miners, #995 data)

For the ATSes with no oracle the finding was **enumerate the namespace, do not sample the traffic**:
popularity-biased sources (aggregators, GitHub lists, Wikipedia, Hacker News) measured 0% to 2.2% unheld,
while the vendor's own index or a namespace-wide dataset measured 2% to 60%. The miners
(`scripts/discover/mine_*`) read: the MIT-licensed SmartRecruiters roster file (59.8% unheld of 11,258;
the unofficial company-lookup endpoint is deliberately not called), Common Crawl's host-level web graph
(5,164 unheld tenant hosts over 245.8M vertex rows, 1.77 GiB), the five sitemaps `app.jazz.co/robots.txt`
names (2,715 unheld of 7,540), CrUX origins beyond the top million (350 net new after the others), the
Hugging Face `open-apply-jobs` dataset (Gem, Ashby, Rippling), the Pinpoint sitemap index, jobseek, and
Sweden's JobTech snapshot. Tech share of the new Boards was measured per source and is in #995: from 9% of
JazzHR Boards (3.1% of postings) to 51% of the Hugging Face set.

## Hazards found, so the next run does not repeat them

- **A timeout is not absence.** The stock `eightfold_dns_sweep.py` counts four timeouts as a label that
  does not exist: on a congested link it read 13 of 101 real csod hosts as absent (34,000 labels swept
  twice, 2026-09-29). Every miner written here checks its replies: a timeout, SERVFAIL or NOERROR
  without an answer is retried and, if it never settles, written to an `.unresolved` file; known-tenant
  controls run every 1,000 to 3,000 labels; a chunk whose controls fail is redone. The original script
  is unchanged.
- **`check_liveness.py` reads curl error 6 (could not resolve host) as DEAD.** On a flaky resolver that
  writes real tenants dead for the 90-day dead TTL. It hit four separate agents (Cornerstone,
  Avature, Darwinbox, PeopleStrong); each landed through a DNS-over-HTTPS wrapper. For Darwinbox it is a real bug in
  `p_darwinbox`: both domains have a wildcard record, so DNS can never prove a tenant absent. Not fixed
  here.
- **Recruitee rate-limits per address.** The dedupe scan over 4,388 Boards takes about two hours at
  4 workers, and any other probe of `*.recruitee.com` from the same address turns labels into
  `unreachable` (five did). ADR-0301 says to apply only a run with none; the union scan in #999 was
  applied with five and each was then re-read singly (two were duplicates the scan had missed).
- **A short read is a silent regression.** An agent's own run of
  `taleo_enterprise_subset_sections.py` left 12 `burnsmcdonn` mirrors un-buried because some walks came
  back short; a clean full re-run buries all 28 onto one section. Re-derive the alias ledger with the
  script, never with a driver that caches or splits the walks.
- **Landing has traps the ledger cannot catch.** Vendor sample data: Pinpoint seeds three fixed
  postings into every new account (122 landed Boards serve only them, now excluded, and the **held
  Pinpoint rows were not read**); SmartRecruiters, Gem, JazzHR and Cornerstone each have demo clusters.
  Vendor infrastructure hosts read as tenants (`repo`, `status`, `assets`, `mobile`, ...). Read a
  Board's postings before excluding; a name is a lead, not proof.
- **A Park is not an Exclusion.** CONTEXT.md: Excluded is permanent and names not-genuine Boards; a Park
  carries the condition that lifts it. Two Cornerstone tenants that hold only an employer's own test
  postings are parked, not excluded.

## Left undone

- Cornerstone's 4-letter label space (449k labels, 0.06% on two 5,000-label samples: about 85 minutes for
  one or two hiring Boards), Taleo's Tranco tail, and the other unswept lists; each is resumable from the
  worktrees' `experiment/infra-discovery-2026-09-29/` folders (gitignored, deleted with the worktree).
- Recruitee's offers-API sieve beyond `.nl/.be` and the top of `.de/.at/.ch` (yield falls fast with rank);
  the four redirect targets held only as dead rows (`klekt`, `madeingroup`, `redhouse`, `scallent`) flip
  on their own re-probe; 15 real companies answer `403 Public API disabled` and cannot be read.
- Reading the held Pinpoint rows for the seeded sample postings; the fix for `p_darwinbox`'s code-6
  branch and the general dead-on-DNS-failure rule; `wayback_feeder.ATS_HOSTS` lacks JazzHR, Jobvite and
  Join and its `extract` drops a dotted Teamtailor label (`{slug}.na`, 274 live rows landed here that the
  CDX sweeps could never see).
- Westpac (a Workday Board beside old Taleo sections) and Vontier (a Taleo section 38 of whose 46 reqs a held
  Phenom front serves, so parked) are cross-ATS overlaps noted in #993: names alone are leads, and there is
  no cross-ATS deduplication yet (see the Phenom and Happydance parking rules).
