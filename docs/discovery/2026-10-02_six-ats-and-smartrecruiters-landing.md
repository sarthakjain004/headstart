# Six-ATS and SmartRecruiters discovery landing

Reconciled against main `59dda794` on 2026-10-02. Candidates were checked on
2026-09-30 using `scripts/validate/check_liveness.py`. Counts below are additions
to the current ledger, after matching each scraper's canonical Board identity.
Earlier research totals were estimates and overlapped other landings.

| ATS | Added rows | Live rows | Unknown rows | Observed postings |
| --- | ---: | ---: | ---: | ---: |
| Workday | 664 | 664 | 0 | 26,270 |
| Darwinbox | 230 | 230 | 0 | 3,594 |
| Keka | 200 | 200 | 0 | 1,925 |
| Greenhouse | 66 | 66 | 0 | 680 |
| Ashby | 15 | 15 | 0 | 92 |
| Lever | 15 | 15 | 0 | 495 |
| SmartRecruiters | 37 | 37 | 0 | 1,008 |

Postings include all roles, not only tech, and can overlap across sibling career
sites. All existing ledger rows retain their exact field values. New identities
have no collisions with existing identities or one another. Empty live rows are
included by the owner's choice. Eight unknown Darwinbox candidates are left in
the local candidate captures for later checks and excluded from this landing.

Review removed WashU's private preboarding workflow and JLL's referral-interest
pool after reading their live pages. The finished Darwinbox DNS sweep is included.
Test/demo tenants and Workday
internal, private, referral, alumni and syndication sites were filtered out.
Workday sibling overlap was measured on 30 sites: four listed only postings a
sibling also listed, and two overlapped by at least 90%. This landing does not
add a Workday requisition-subset alias mechanism.

The Workday company-name follow-up cached 422 names. The Eightfold backing scan
finished and rewrote its aliases with eight buried fronts out of 62 considered.
The remaining 516 of the original 553 non-Dormant SmartRecruiters candidates
were already held on current main. The lookup miner preserves canonical casing,
paces requests and records prefix responses for resuming.

Validation: per-row and canonical-identity audit; Board-count, Eightfold backing,
Workday company-name and existing SmartRecruiters roster tests; Ruff; one live
lookup request (`q=aa`) returned 15 companies, all already held.
