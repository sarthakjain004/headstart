# ADR-0254: An iCIMS portal listing only what a sibling portal lists is an alias

**Status:** accepted · **Date:** 2026-09-28 · **Amends:** [ADR-0222](0222-an-icims-portal-that-redirects-to-another-is-an-alias.md) (the redirect alias, kept, and its writer, replaced) · **Relates to:** [ADR-0186](0186-a-taleo-section-another-section-already-lists-is-an-alias.md) and [ADR-0202](0202-an-adp-recruiting-board-is-a-career-site-read-through-its-token.md) (the `subset-reqs` election), [ADR-0240](0240-jibe-drops-a-posting-only-when-its-icims-tenant-is-a-board-we-scrape.md) (the Jibe drop)

## Context

ADR-0222 buried an iCIMS portal whose sitemap redirects to another Live portal. Many sibling
portals list a customer's postings without redirecting. On 2026-09-28, with no redirect on either
host:

- `careers-redlobster` and `hourly-spanish-redlobster` listed the same 2,399 postings;
- `uscareers-fujifilm` and `uscareershub-fujifilm` listed the same 314;
- `jobs-noodles` and `spjobs-noodles` listed the same 918;
- `germancareers-celanese` listed 15, every one also on `europecareers-celanese`.

Each of these postings was served once per portal. The served table (v469) held 489 iCIMS native
ids on two or more Boards whose host labels share a customer word.

## Decision

**An iCIMS portal whose every posting another portal of the same customer also lists is buried
onto it, with signal `subset-reqs`.** The election is `alias_ledger.bury_contained`, as for Taleo
sections and ADP career sites. A posting is its `(job id, title slug)` pair off the sitemap URL.
The customer is the host label's last hyphen-separated word (`redlobster`), which is a heuristic,
so the title slug is what makes a match evidence.

**One writer owns the file: `scripts/validate/icims_subset_portals.py`.** It reads every live
portal's sitemap once, following redirects:

- a portal that lands on another read portal is buried as `redirect`, exactly as in ADR-0222;
- the rest are compared by containment.

It replaces `dedupe_boards.py --ats icims --apply`, which now refuses the file because it holds
rows that script did not write. Re-run it after every refresh of the iCIMS ledger.

**`jibe._scraped_icims_tenants` counts a buried portal as covered when the portal it is buried
onto is Scrapable.** A Jibe row applying on the buried portal is served by iCIMS under the kept
portal's key.

## Evidence

Run 2026-09-28 over the 4,149 live portals, after re-probing the ledger's live rows: all 4,149
were read, 272 were buried by redirect and 148 by `subset-reqs`.

- The 270 redirect rows ADR-0222 wrote all came back. Two re-point through a chain onto the
  surviving portal:
  - `careers-recovercare` → `careershomepage-joerns`;
  - `globalcareers-ivansinsurance` → `careers-appliedsystems`.
- The two portals ADR-0222 held back are buried again, as that ADR said they would be:
  `careers-virginpulse` and `careers-trnty`.

## Consequences

- Four public Beaumont Hospital portals are buried onto `internal-beaumonthospital`, the one
  portal listing them all. Its job pages are public: the same page served on
  `general-beaumonthospital` renders the same apply and login links. Whether outsiders can apply
  from the internal portal was not verified.
- Two maximal portals that overlap without either containing the other both stay, so the
  postings they share are still served twice.
