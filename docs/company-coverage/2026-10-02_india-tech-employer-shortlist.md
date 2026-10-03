# India technology employer shortlist coverage — 2 October 2026

The 1,000-company shortlist has **715 company-name/brand matches in the liveness ledger**, of which **590 have at least one Scrapable Board**. The latter is the useful default for coverage. These are counts against this shortlist and ledger snapshot, not a claim to an objective top-1,000 ranking or to current India job availability.

| Shortlist prefix | Ledger present | Scrapable | Scrapable coverage | Hiring Board | Not found | Ambiguous only |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 200 | 193 | 177 | 88.5% | 160 | 7 | 0 |
| 300 | 288 | 252 | 84.0% | 227 | 12 | 0 |
| 400 | 348 | 307 | 76.8% | 276 | 52 | 0 |
| 500 | 422 | 364 | 72.8% | 327 | 78 | 0 |
| 1000 | 715 | 590 | 59.0% | 527 | 285 | 0 |

## Population and ordering

Scope: major technology employers operating in India, including international software and semiconductor firms, Indian IT services and startups, and non-tech companies with technology/GCC operations. Source inclusion supports India presence; the audit did not independently verify every company's current India office or hiring activity.

This is one nested coverage-priority shortlist. The first 200 entries are reused in the 300, 400, 500 and 1,000 prefixes. The ordering does not use ledger coverage. There are 302 nominated major employers in the first band, 65 additional workplace-recognized employers in the second, 178 additional companies appearing in multiple sources in the third, and 455 directory entries in the last. Major employers follow a disclosed editorial order in the replay script. Other bands sort by source count descending then normalized company name. Alphabetical order in the final band is a deterministic tie-break, not evidence that an A-named employer is better than a Z-named one.

Company spelling variants and explicitly identified employer aliases are collapsed. Distinct brands with their own hiring identities may remain separate, even when they share a parent. This is an employer-brand count, not a count of ultimate corporate groups. A parent's general Board does not automatically cover a subsidiary.

Sources include Dun & Bradstreet's 2025 India GCC directory, Bamboo Reports' India technology GCC pages, the Indian IT company list, Great Place To Work India's IT & IT-BPM 2025 winners, and community product/startup lists. 195 entries rely exclusively on community lists, some from 2023/2024; these are discovery leads with weaker and older India-presence evidence. Historical brands and directory misclassifications can remain. Treat the broad 1,000-company denominator as a research shortlist rather than an independently validated census of active employers. The 200/300 prefixes are editorial choices, not published revenue or employee-rating rankings.

## Revenue and workplace evidence

100 entries carry Great Place To Work India IT & IT-BPM 2025 recognition and, where stated, the source's employee count and location. Its assessment covered 551 participating organizations; recognition is not a comparison of every employer in India. The source categories are Top 25 / Top 50 / Top 100, not exact ordinal ranks. Source absence means no evidence collected here, not poor workplace quality.

8 entries carry annual revenue figures checked against primary company results or SEC filings: Microsoft, Amazon, Apple, NVIDIA, Salesforce, TCS, Infosys and HCLTech. Values preserve currency, unit, fiscal period and source link. They are **global consolidated company revenue, not India revenue**; Salesforce's figure is rounded. Other revenue cells are blank because this audit did not collect their revenue. This is selective enrichment, not exhaustive public-financial-data collection. No incomparable currencies or missing revenues were converted into a fabricated composite score.

## Ledger matching and meaning

Pinned current `origin/main` base and working-tree commit: `68ec759acd7866e53773f4d4602142ed0976b1df`. All 55 ATS liveness files were scanned, covering 337,708 raw rows. The full production loader returned 178,255 Scrapable Boards and 122,871 Hiring Boards. This branch includes the source-ledger changes represented in those totals.

- **Ledger present**: at least one accepted name/brand match in any row, including dead, unknown, parked or alias-buried rows. This does not imply usable scraping.
- **Scrapable**: an accepted matched canonical Board appears in `headstart.boards.scrapable_boards.load(min_jobs=0)`. This applies canonical identity, newer-dead election, registered/disabled providers, exclusions, aliases and parked-board rules.
- **Hiring Board**: at least one accepted match appears with `min_jobs=1`. The ledger job count can include all countries and non-tech roles, and may be old; it is not a count of current India tech jobs.
- **Not found**: no accepted or ambiguous name match. This is not proof of absence: opaque tenant IDs, uncollected names, old brands and parent hiring arrangements can hide matches.
- **Ambiguous only**: candidates exist, but their short name or a conflicting cached company name prevents counting them. UST, Cognex and Optiva are withheld.

Matches use exact normalized names, cached employer names, curated aliases and whole tenant/domain labels. Generic ATS domain labels never identify the vendor's customers as the vendor. Very short brands require stronger evidence. A cached employer name that conflicts with a token match causes that match to be withheld. Fuzzy similarities are review leads only and are not counted. Known false matches `trakstar:amazon` and `workday:google/GOCJobs` are excluded.

The original targeted identity checks recovered Ubisoft, SentinelOne, Bentley Systems, LatentView, Shiprocket, Aspire Systems, Brillio, Apexon, Nagarro, YASH Technologies and Citrix. This branch adds a wider direct-apply recovery documented in `docs/company-coverage/2026-10-03_indeed-direct-apply-coverage.md`: it maps verified employers to existing canonical Boards where possible and lands only Board identities that are both new and provider-verified. Parent, group, and stale-brand routes remain excluded from the company mapping.

There are no shared accepted Scrapable Board identities across two shortlist entries. Results were reconciled independently at all five cutoffs, checked for 1,000 unique names, and checked for Hiring ≤ Scrapable ≤ Ledger-present.

## First-200 gaps

No accepted ledger match:

- Sonata Software: not found
- KPIT Technologies: not found
- Blinkit: not found
- Ansys: not found
- Dassault Systemes SE: not found
- D. E. Shaw: not found
- Ramco Systems: not found

Recorded but no matched Scrapable Board:

IBM, Zensar Technologies, Zerodha, MakeMyTrip, Nykaa, Acko, Cashfree, CouchBase, CyberArk, MathWorks, American Express, BNY, Bain & Company, Eightfold ai, Harness, Zynga.

## Deliverables and replay

- Workbook: `data/company-shortlists/2026-10-02_india-tech-employers-1000.xlsx`, with a formula-driven coverage summary and a filterable company list.
- Full evidence: `data/company-shortlists/2026-10-02_india-tech-employers-1000.json`, including accepted/ambiguous matches, raw source rows, source links, ledger-file hashes and the pinned Git SHA.
- Local replay and captures: `experiment/india-tech-employer-coverage/`, intentionally gitignored under the repo's experiment convention. `collect_sources.py`, `build_shortlist.py`, `verify_brand_aliases.py`, `match_ledger.py`, `write_report.py`, then `build_workbook.mjs`. Recollecting sources or changing curated nominations can change the shortlist and its coverage; preserve this published JSON to compare the same population later.

## Source links

- [bambooreports.com](https://bambooreports.com/gcc/industries/electronics/)
- [bambooreports.com](https://bambooreports.com/gcc/industries/hi-tech/)
- [bambooreports.com](https://bambooreports.com/gcc/industries/it-service/)
- [bambooreports.com](https://bambooreports.com/gcc/industries/software-and-saas/)
- [en.wikipedia.org](https://en.wikipedia.org/wiki/List_of_Indian_IT_companies)
- [gist.githubusercontent.com](https://gist.githubusercontent.com/ivinoop/ca8f5906d2cf65116b67886e32f02fa6/raw)
- [raw.githubusercontent.com](https://raw.githubusercontent.com/ysoni24/companies-list/main/README.md)
- [www.dnb.co.in](https://www.dnb.co.in/files/reports/Rethinking-the-Future-of-Global-Capability-Center-Hyderabad-2025.pdf)
- [www.greatplacetowork.in](https://www.greatplacetowork.in/indias-best-workplaces-in-it-amp-it-bpm/)
- [www.hcltech.com](https://www.hcltech.com/en-us/press-releases/hcltech-fy26-revenue-39-led-increasing-demand-advanced-ai)
- [www.infosys.com](https://www.infosys.com/about/last-fiscal.html)
- [www.microsoft.com](https://www.microsoft.com/en-us/investor/earnings/fy-2026-q4/press-release-webcast)
- [www.tcs.com](https://www.tcs.com/who-we-are/newsroom/press-release/tcs-financial-results-q4-fy-2026)
