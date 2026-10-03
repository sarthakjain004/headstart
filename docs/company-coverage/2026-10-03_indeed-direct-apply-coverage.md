# Top-1,000 missing employers: Indeed direct-apply recovery

Observed 3 October 2026 against the fixed India technology-employer shortlist. This pass targets all 339 entries that had no accepted ledger attribution in the preceding audit. It does not serve Indeed jobs or submit applications.

## Method

Indeed's browser pages may put third-party application redirects behind an interstitial. The pass therefore used the public iOS `jobSearch` GraphQL contract documented by [JobSpy](https://github.com/speedyapply/JobSpy), through the pre-existing local Indeed-discovery client. The public app configuration was imported only at runtime; it is not copied into this repository or any captured artifact.

For every shortlist employer, the query read public job title, employer name, corporate website, and `recruit.viewJobUrl` in the India catalogue. Employer matching uses whole normalized name tokens, so a query for `Arm Holdings` cannot match a company merely because one of its words contains `arm`. The saved data retains query-free direct apply URLs only. Each supported URL then ran through `fingerprint_careers.py indeed --deep`, followed by its provider-native liveness verifier.

No account state, resume, application form, CAPTCHA solution, or job submission was used.

## Measured result

- 339 employers queried.
- 99 employers returned at least one exact-employer result.
- 1,505 exact-employer job records yielded 1,209 distinct direct apply URLs across 83 hosts.
- The fingerprint pass condensed those into 124 employer/host inputs: 54 provider-live, 18 provider-unknown, 6 provider-dead, 5 unprobed iCIMS, and the remainder external systems without a supported provider contract.
- 56 exact employer-to-canonical-Board pairs are written to `data/validate/company_names/indeed_top1000.csv` for the coverage audit. This is an audit cache and does not override runtime company naming.
- `wp_job_openings:consiliumsoftware.com` is the only new supported Board that passed the canonical novelty check. Its provider probe observed 21 jobs and added a live liveness row.

Representative direct paths validated by the provider fingerprint are Dun & Bradstreet -> Lever `dnb`, Kore.ai -> Rippling `koreai-india-careers`, Cornerstone OnDemand -> Cornerstone `cornerstone`, and Consilium Software -> its WordPress Jobs board. Axtria's direct link is a current SuccessFactors CSB application route; it is not a scrapeable RMK Board. A result initially matched as Arm Holdings was rejected after the GraphQL employer field identified Sun Pharmaceutical Industries.

Direct Phenom career fronts for Arrow, Ecolab, and Esko were not added as duplicate Boards because their postings are already represented through their backing ATSes. Direct routes on unsupported systems such as UKG, TalentRecruit, CEIPAL, Zappyhire, and Adrenalin remain evidence for provider work, not liveness rows that the current registry cannot scrape.

## Evidence artifacts

Ignored local artifacts under `experiment/top1000-ats-recovery/artifacts/` retain the exact-employer GraphQL response projection, sanitized direct apply URLs, fingerprint results, provider verification results, and the browser-interstitial observations. No artifact contains the app configuration or transient Indeed application tokens.
