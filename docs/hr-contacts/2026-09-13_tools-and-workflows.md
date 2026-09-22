# Public recruiting-contact email acquisition: tools and workflow

**Research date:** 2026-09-13
**Decision target:** a `company -> public recruiting/HR/careers role inbox` mapping for HeadStart. This is deliberately *not* a directory of named recruiters. A shared inbox such as `careers@example.com` is both more durable and more closely connected to the company's published hiring channel than an inferred address for a particular employee.

## Bottom line

There is no credible, reusable, public dataset found in this research that maps *all companies* to HR/recruiting emails. The available large datasets are either (a) web archives, which require extraction and recency checks, or (b) licensed B2B **person**-contact data. The latter is a poor fit for the primary target and normally cannot be republished as a HeadStart dataset.

Build a first-party, provenance-backed role-inbox dataset from the company/careers URLs HeadStart already knows. Use a paid domain-search vendor only as a measured backfill, and use a mailbox verifier only after an address has been found. Do not use person-enrichment vendors as the primary source or publish their output.

This is a search outcome, not a proof that no niche or unlicensed list exists: the primary-source search found commercial APIs and public web archives, not an open global HR-email corpus with a stated provenance, update process, and redistribution licence.

## What counts as a useful record

Keep role accounts and named-person emails separate at collection time.

| Class | Examples | Include in the primary dataset? | Why |
| --- | --- | --- | --- |
| Recruiting role account | `careers@`, `jobs@`, `recruiting@`, `talent@`, `hiring@`, `hr@` | Yes, with evidence | Durable company contact point; target outcome. |
| Application or candidate-support role account | `apply@`, `candidatecare@`, `campus@` | Yes, separately labelled | Useful, but it may be an ATS/vendor-run mailbox rather than HR. |
| Named recruiter/person | `first.last@company.com` | No, unless a separate, consent/terms-reviewed feature is approved | Personal data, changes often, and is a different product. |
| EEO, accommodation, privacy, legal, abuse, support | `accommodations@`, `privacy@`, `legal@` | Exclude from the recruiting mapping | A job-page email is not necessarily a recruitment contact. |

Each accepted row should retain: canonical company/board key, email, `contact_kind`, source URL, verbatim surrounding text (or a short safe excerpt), page retrieval time, source-page hash, first/last observed timestamps, extraction method, and verification result. Provenance makes removals, correction requests, re-crawls, and false-positive review possible.

## Acquisition options

| Option | What it actually returns | Fit for role inboxes | Access/cost facts | Recommendation |
| --- | --- | --- | --- | --- |
| First-party careers/site extraction | Exact addresses published on company career pages, job descriptions, FAQ/contact, and application instructions | Best: direct evidence and role wording | Build/run cost only; honour site rules and rate limits | **Primary path.** |
| Hunter Domain Search | Emails Hunter associates with one domain; it explicitly supports `type=generic` and `department=hr`, and returns sources/dates, confidence and verification status | Good paid backfill; its `generic` class is defined as role-based | Domain Search returns up to 100 results per call; 1 credit per 1--10 returned emails; documented API limits are 15 requests/s and 500/min | **Pilot after the first-party baseline.** Require a public source URL for acceptance. |
| Common Crawl | Archived web pages and URL/WARC indexes | Not permitted for this dataset | Its terms prohibit harvesting personal information separately from the crawled content | **Do not use for email discovery or collection.** |
| theHarvester | Open-source orchestrator of passive discovery sources; can return emails, URLs, people, hosts, etc. | Reconnaissance only | Source-specific quotas/terms still apply; it is not a verified role-account dataset | Optional seed generator; do not treat its output as evidence. |
| ZeroBounce (or comparable verifier) | Deliverability/status for an email supplied to it; no discovery | Useful QA, not collection | Its API distinguishes `role_based`, `role_based_catch_all`, `accept_all`, `unknown`, etc. | Verify only already-sourced candidates; keep the full result rather than reducing it to valid/invalid. |

### First-party extraction is the right core

HeadStart already has company/board URLs and job-page content, so it has the difficult entity-resolution asset that generic email scrapers lack. A bounded crawler can fetch only company-owned pages likely to contain hiring contacts:

1. Start from each canonical careers/ATS-board and from existing job detail URLs.
2. Extract `mailto:` links and visible, de-obfuscated addresses from job bodies, careers/help/contact pages and sitemaps; record URL and surrounding sentence.
3. Classify the address using both local-part and context. Accept recruiting/careers/talent/hiring/application wording; reject accommodation/EEO/privacy/legal wording even when co-located on a job page.
4. Canonicalize and deduplicate by company + address, but retain every evidence source and date.
5. Re-fetch a sampled source and re-check old records periodically. Mark a record stale rather than silently retaining it forever.

Scrapy provides a conventional implementation base: its robots middleware can enforce `ROBOTSTXT_OBEY`, and its documentation says it filters requests forbidden by the site's `robots.txt`. Do not override it for this collection; also respect a site's terms, publish an honest user agent/contact, and keep per-domain concurrency deliberately low. [Scrapy robots middleware](https://docs.scrapy.org/_/downloads/en/2.8/pdf/)

Do **not** synthesize candidate mailboxes (`jobs@domain`, `firstname.lastname@domain`) just because an MX record or a vendor says the domain accepts mail. That creates addresses without public provenance and turns the result into an outreach list rather than a public-contact index.

### Hunter: the strongest vendor match for this exact target

Hunter's Domain Search is unusually aligned with role inboxes: it accepts a domain and can filter by `type=generic` and `department=hr`; Hunter defines `generic` as a role-based address and exposes a source URL, first/last seen dates, confidence, and a verification object for each result. It also permits pagination up to 100 rows per request. [Hunter API reference](https://hunter.io/api-documentation)

The cost model needs a small pilot, not a budget extrapolation from vendor coverage: Hunter documents Domain Search as one credit per 1--10 returned addresses and its help centre says the crawler uses publicly available information. Its sources can be public or inferred, so accept only rows with a usable public source URL for this dataset (or label inferred rows as leads needing independent evidence). [Hunter API costs](https://help.hunter.io/en/articles/1970956-hunter-api) [Hunter source semantics](https://help.hunter.io/en/articles/1922737-what-information-can-i-find-with-the-domain-search)

One practical trap: a search on `example.com` does not automatically return addresses at a subdomain such as `jobs.example.com`; Hunter documents that the full subdomain must be queried. Board/careers hosts should therefore be tried in addition to the marketing root, subject to a bounded per-company budget. [Hunter Domain Search guidance](https://help.hunter.io/en/articles/1922737-what-information-can-i-find-with-the-domain-search)

Hunter is a **backfill service, not an exportable data feed**. Its policy prohibits sharing or reselling Hunter data without authorization; obtain written licence confirmation before exposing any vendor-derived address in a public HeadStart product. [Hunter content policy](https://hunter.io/transparency/content-policy?locale=fr)

### Common Crawl is out of bounds; open-source tooling is reconnaissance only

Common Crawl makes its CDX and columnar URL indexes available, but its terms prohibit collecting or harvesting personally identifiable or personal information separately from the crawled content. Do not use the archive to discover, extract, or persist email addresses for this dataset, even if the eventual record would be re-fetched from the origin. [Common Crawl terms of use](https://commoncrawl.org/terms-of-use) Its availability is not a licence for this collection.

[theHarvester](https://github.com/laramies/theHarvester/blob/master/README.md?plain=1) is a maintained open-source command-line tool that can query passive sources for email addresses and URLs. It is useful to compare discovery yield in a small experiment, but it aggregates third-party sources with their own restrictions and does not itself establish that an address is a recruiting role inbox. It should never write directly to the serving dataset.

## Why Apollo, People Data Labs, and Lusha are secondary at most

These products are designed to find or enrich **people**, not a company's shared `careers@` address. They can be appropriate only for a separately-approved named-contact product with a legal basis, opt-out handling, retention policy and a vendor licence that permits the intended presentation.

| Vendor | Capabilities relevant here | Feasibility | Why it is not the primary answer |
| --- | --- | --- | --- |
| Apollo | People enrichment returns contact data including emails; it costs 1--9 credits per person depending on returned data/options | Requires a work-email-registered account and plan/rate-limit review | Named business-contact data. Apollo's terms permit internal business use but prohibit distributing/selling the contributor database, creating competing databases/services, and third-party AI use of API data. [Docs](https://docs.apollo.io/reference/people-enrichment) [credit rules](https://docs.apollo.io/docs/api-pricing) [terms](https://www.apollo.io/terms) |
| People Data Labs (PDL) | Person Search can filter person profiles; its schema includes `work_email`, `job_company_name`, and `job_title` | Free accounts expose email fields as booleans, not values; Pro starts at $98/month for Person data and successful returned profiles consume credits | It is person data, not shared mailboxes. It is a reasonable benchmark for named-HR coverage only, not a role-inbox source. PDL's own example filters named decision-makers with a known `work_email`. [account/access](https://docs.peopledatalabs.com/docs/create-an-account) [free-plan restriction](https://support.peopledatalabs.com/hc/en-us/articles/30910938133147-Free-Plan-Person-Starter-Bundle) [schema](https://docs.peopledatalabs.com/docs/fields) [example](https://www.peopledatalabs.com/blog/post/build-custom-audience-with-pdl) |
| Lusha | V3 supports contact prospecting and a search-then-enrich flow; email is explicitly a revealed contact field | API key and account credit pool required; query can return large named-contact result sets | Again, named B2B contacts rather than shared recruitment mailboxes. Treat it as a paid, licence-bound enrichment vendor, not a public source. [Lusha API overview](https://docs.lusha.com/guides) [API FAQ](https://docs.lusha.com/qa) |

For named person data in particular, do not assume the vendor's compliance posture transfers to HeadStart. Apollo's terms say the customer is responsible for assessing lawfulness and place usage restrictions on the data; PDL similarly prohibits uses that violate data-protection law. [Apollo terms](https://www.apollo.io/terms) [PDL acceptable-data-use policy](https://privacy.peopledatalabs.com/policies?name=acceptable-data-use-policy)

## Verification is a status, not proof of an HR recipient

Mailbox validation is worth doing once evidence exists, but it cannot determine whether the mailbox reaches the hiring team. ZeroBounce's API returns a high-level status plus detailed sub-statuses, including role-based and catch-all cases. Preserve the raw status and timestamp; never reclassify an email as a recruiting address solely because it validated. [ZeroBounce API v2](https://www.zerobounce.net/docs/email-validation-api-quickstart/v2-validate-emails)

Suggested publication states:

- `observed`: exact address on a current, company-owned public page and recruiting context.
- `observed_vendor`: a vendor returned an address with a public, current evidence URL; retain vendor provenance and licence check.
- `unverified`: source is sound but no validator was run or it returned `unknown`/catch-all.
- `stale`: evidence disappeared or was not observed on re-crawl; do not surface by default.
- `removed`: company/requestor removal, invalid source, or an excluded context.

## Recommended staged experiment for HeadStart

1. **Define the product boundary before collection.** Publish only public, role-based recruiting/application contacts; exclude named people and sensitive/EEO/accommodation/legal contacts. Decide whether the dataset is internal-only or public, because vendor terms may rule out public display.
2. **Measure a first-party baseline on a stratified sample of canonical boards** (at least several ATS families, countries and company sizes). Track `companies scanned`, `companies with a role inbox`, `addresses per company`, source-context precision from manual review, and re-fetch survival after a few weeks.
3. **Run Hunter only on baseline misses**, with `type=generic`, HR/recruiting job-title/context filters where supported, a capped number of root and careers subdomains, and a requirement for public-source evidence. Record credits per accepted address, not merely responses.
4. **Optionally use theHarvester only as a bounded lead generator for the same misses.** Re-fetch and classify every candidate on the company origin before acceptance; do not use Common Crawl for this purpose.
5. **Validate the accepted set selectively.** Keep deliverability as a non-authoritative signal; do not send mail as a test.
6. **Decide on a named-contact product separately.** If it has value, evaluate PDL/Apollo/Lusha against a fixed company sample, privacy review and vendor licences. It should be a separate table and never silently merge into the public role-inbox mapping.

The simplest durable success criterion is: every surfaced address is a role account with a current, retrievable company-owned source page and hiring-related context. Coverage can grow over time without lowering that standard.
