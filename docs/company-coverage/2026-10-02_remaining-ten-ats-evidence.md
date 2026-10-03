# ATS evidence for the remaining ten top-200 employers

Observed 2026-10-02. Scope: the fixed ten employers remaining after excluding the two Kula employers, Acko and Cashfree. This identifies public recruitment systems; it does not add scraper support, change the ledger, or increase the measured coverage. A marketing CMS is not an ATS. Corporate acquisition redirects do not by themselves prove that every acquired employer's jobs are now represented on the parent board.

## Findings

| Employer | Public recruitment system / ATS evidence | Confidence and remaining limitation |
| --- | --- | --- |
| MakeMyTrip | **Darwinbox**, tenant `gommt`, behind MakeMyTrip's own career frontend. | Confirmed current job-detail response explicitly supplies a Darwinbox application URL. |
| Nykaa | **Skima AI** career portal on the company's vanity domain. | Confirmed canonical URL and Skima-hosted company assets. No separate downstream ATS identified. |
| Ansys | **Avature**, through the current Synopsys Radancy/TalentBrew career site. | Confirmed current application URL. Acquisition-era brand migration needs consideration when counting Ansys separately. |
| Dassault Systèmes | **Taleo Enterprise** behind the 3DS career frontend. | Confirmed current Taleo `jobapply.ftl` application URL. A separate Dassault Zwayam listing is not sufficient to identify its main worldwide board. |
| CyberArk | Former CyberArk corporate careers URL now redirects to **Palo Alto Networks**. Its current parent career frontend is Radancy/TalentBrew with **Workday** applications. | Current parent job application is confirmed Workday. Attribution of all CyberArk jobs to this parent board remains unconfirmed; old CyberArk SmartRecruiters feed currently reports zero. |
| D. E. Shaw India | **Custom company-hosted application portal, with iCIMS integration evidence** in its current frontend JavaScript. | Multiple iCIMS-specific data fields are strong backend-integration evidence, but no public iCIMS board/tenant was verified. Do not classify this as D. E. Shaw Research's Avature board. |
| Zerodha | **Custom company-hosted careers portal** with a public jobs API and application API. | Confirmed exposed routes. No named external ATS identified; public jobs response currently contains zero jobs. |
| MathWorks | **Company-hosted recruitment/application system**. | Current job's application GET leads to MathWorks' own Careers sign-in and account routes. No named external ATS backend confirmed. Separate talent-network domain does not establish the application backend. |
| Ramco Systems | **Company-hosted job listing with HubSpot Forms application collection**. | Confirmed job page embeds a HubSpot form. HubSpot CMS/Forms is not proof of an ATS backend; downstream ATS remains unknown. |
| Blinkit | **Unknown current ATS**. Official careers entry redirects to Eternal's careers page. | Current official destination provides hiring/culture content without a verified Blinkit public job/application endpoint. Eternal/Zomato Darwinbox presence is not proof of a Blinkit ATS. |

## Primary evidence

### MakeMyTrip

- Corporate entry: <https://careers.makemytrip.com/>.
- Captured public job-detail response contains `data.applyUrl` pointing to <https://gommt.darwinbox.in/ms/candidatev2/main/careers/jobDetails/a6a68ae6aedd4b?from=all> and identifies `group_company` as `MakeMyTrip (India) Limited`.
- Local captures: `experiment/top200-coverage-improvement/artifacts/makemytrip-detail.json`, `makemytrip-api-jobs.json`, `makemytrip-js.txt`. This is one sampled job, not a complete tenant audit.

### Nykaa

- Corporate entry: <https://careers.nykaa.com/> returned HTTP 200 in `careers_scan.json`.
- Its source declares canonical <https://careers.nykaa.com.skima.ai> and company logo/favicon assets under `storage.googleapis.com/skima-prod/companies/`.
- Capture: `experiment/top200-coverage-improvement/artifacts/nykaa-careers.html`. This confirms the public Skima career-site system, not every internal recruitment integration.

### Ansys

- <https://www.ansys.com/careers> redirects to <https://ansys.synopsys.com/careers>, which links to <https://careers.synopsys.com/search-jobs/ansys/44408/1>.
- Sample job: <https://careers.synopsys.com/job/waltham/senior-application-engineer/44408/100992954016>.
- Apply target: <https://synopsys.avature.net/careers/Login?jobId=18046>.
- Captures: `experiment/top200-coverage-improvement/artifacts/ansys-home.txt`, `synopsys-ansys.html`, `synopsys-ansys-job.html`. Radancy is the presentation layer; the application destination is Avature.

### Dassault Systèmes

- Official job: <https://www.3ds.com/careers/jobs/industry-process-consultant-549891>.
- Explicit application link: <https://talentacquisition.3ds.com/careersection/qa/jobapply.ftl?job=549891&lang=en&src=CWS-10080>.
- Captures: `experiment/top200-coverage-improvement/artifacts/dassault-job.html`, `dassault-apply.html`. This URL shape establishes Taleo Enterprise, independently of the corporate CMS.

### CyberArk / current parent destination

- Corporate entry <https://www.cyberark.com/careers/> now lands at <https://www.paloaltonetworks.com/idira>; the current destination links to <https://jobs.paloaltonetworks.com/en/>.
- Parent search <https://jobs.paloaltonetworks.com/en/search-jobs> returned HTTP 200.
- Current sampled job <https://jobs.paloaltonetworks.com/en/job/singapore/identity-security-domain-consultant-singapore/47263/101264330224> returned HTTP 200, with requisition `JR-022828` and an explicit Apply anchor and `search-job-apply-url` meta field.
- Both point to <https://paloaltonetworks.wd5.myworkdayjobs.com/panwexternalcareers/job/Office---Singapore---Singapore---N-Bridge-Road/Identity-Security-Domain-Consultant---Singapore_JR-022828/apply>. This verifies Workday for a current Identity Security parent job, beyond the talent-community URL alone.
- Historical <https://api.smartrecruiters.com/v1/companies/cyberark1/postings> returned HTTP 200 with `totalFound: 0` in the existing verification capture. An empty historical feed is not proof that all CyberArk jobs migrated to Workday.
- New captures: `experiment/top200-ats-research/artifacts/pan-search.html`, `pan-identity-job.html`; prior captures `cyberark-careers.html`, `cyberark-privacy.html`, `paloalto-jobs.html`, `cyberark1-smartrecruiters.json` under the improvement artifacts directory.

### D. E. Shaw India

- Official <https://www.deshawindia.com/careers> returned HTTP 200 in the existing capture. It explicitly links to <https://www.apply.deshawindia.com/ApplicationPage1.html?entity=DESIS>.
- Current application bundle <https://www.apply.deshawindia.com/BasicInfo.js> returned HTTP 200 in this research. Its own application code filters server attachments on `isUploadedToIcims`, uses `icimsFileId`, and maps `normalizedEmployer.icimsId`.
- This establishes an iCIMS integration in the official application's frontend data model. It does not establish a publicly scrapeable iCIMS tenant or prove exclusive use of iCIMS.
- The bundle names company-hosted service routes such as `jobs/jobDetails/` and `jobs/externalWebsite/`. A direct GET of the latter redirected to `ValidateUrl.html?url=externalWebsite/&entity=DESIS` and returned HTML, so it was not accepted as a job-list API.
- New capture: `experiment/top200-ats-research/artifacts/deshaw-basic.js`; existing captures `deshaw-careers.html`, `deshaw-apply.html`.

### Zerodha

- Current public portal <https://careers.zerodha.com/> returned HTTP 200 in the earlier corporate scan.
- Its currently referenced bundle <https://careers.zerodha.com/assets/index-DKC6v3vo.js> returned HTTP 200 in this research and explicitly invokes GET `/api/jobs` and POST `/api/applications`.
- GET <https://careers.zerodha.com/api/jobs> returned HTTP 200 with `{"count":0,"data":[],"success":true}`. This is the current sample's zero-opening count, not proof of a dead portal.
- No application POST was made. No external ATS name was verified.
- New captures: `experiment/top200-ats-research/artifacts/zerodha-js.txt`, `zerodha-api.json`.

### MathWorks

- Official search: <https://www.mathworks.com/company/jobs/opportunities.html>; captured HTTP 200.
- Sample job `12382` source contains `/company/jobs/apply/apply_now?job_posting_id=12382`.
- GET <https://www.mathworks.com/company/jobs/apply/apply_now?job_posting_id=12382> returned HTTP 200 in this research. It displays a company-hosted sign-in form with action `/company/jobs/apply/apply_now_sign_in`, a create-account route `/company/jobs/apply/job_bids/new?job_posting_id=12382&sign_up=true`, and company-hosted apply JavaScript bundles.
- Official [application FAQ](https://www.mathworks.com/company/jobs/resources/applying-and-interviewing.html) describes its distinct Careers account. It names no backend vendor.
- Corporate page links separately to <https://recruitment.mathworks.com/flows/talent-network>; that GET returned HTTP 403 here. Do not classify the ATS from that separate marketing/talent route.
- New capture: `experiment/top200-ats-research/artifacts/mathworks-apply.html`; existing `mathworks-search.html`, `mathworks-job.html`. No sign-in, account creation or form submission was attempted.

### Ramco Systems

- Official listing: <https://www.ramco.com/careers/jobs-by-locations> returned HTTP 200 in the earlier scan.
- Sample Software Development Engineer job: <https://www.ramco.com/careers/jobs-by-locations/sde1>.
- The job source loads `https://js.hsforms.net/forms/v2-legacy.js` and calls `hbspt.forms.create(options)` with portal ID `494075` and form ID `3a3559b7-d756-4f0c-83a0-9b115a14324a`.
- Job/listing source references HubSpot CMS/HubDB resources. This supports a company-hosted listing and HubSpot application collection, not a named ATS backend. No form was submitted.
- Existing captures: `experiment/top200-coverage-improvement/artifacts/ramco-careers.html`, `ramco-job.html`.

### Blinkit

- GET <https://blinkit.com/careers> landed at <https://www.eternal.com/careers/> with HTTP 200 in the current corporate scan. Its source is hiring/culture content and supplies no verified Blinkit job/application destination.
- An Eternal Darwinbox listing was sampled separately and was empty in `eternal-darwinbox-jobs.json`; neither tenant ownership nor a zero count establishes Blinkit's ATS.
- The historically indexed nested route <https://blinkit.com/careers/careers/careers/> returned HTTP 403 in this research, although the response contains old Drupal career-site markup. A stale CMS page is not a current ATS identification.
- Existing capture `experiment/top200-coverage-improvement/artifacts/blinkit-careers.html`; new `experiment/top200-ats-research/artifacts/blinkit-old-careers.html`.

## Interpretation for coverage work

MakeMyTrip, Ansys and Dassault expose named ATS application destinations that can be investigated using existing provider capabilities. Nykaa exposes Skima as a distinct public system. D. E. Shaw India's application bundle references iCIMS-related IDs, but the public listings and application flow stay on company-hosted domains; no public iCIMS tenant was verified. Zerodha and MathWorks expose their own recruitment interfaces. Ramco exposes a custom listing and HubSpot form; Blinkit's current ATS remains unresolved. CyberArk requires an explicit brand/job attribution decision before treating the parent's Workday board as full CyberArk coverage.

No scrapeability percentage was recomputed by this research. Finding an ATS is not the same as landing an eligible, working board.

## Playwright API responses and HARs

The capture used clean Playwright contexts with no saved browser state. It only opened career, job, and application-information pages. It did not sign in, solve a challenge, fill a form, or submit an application. Cookie and authorization headers are removed, session and tracking IDs are redacted, analytics requests are excluded, and API JSON responses are preserved beside sanitized HARs.

The raw evidence is in [`experiment/top200-careers-har/artifacts/`](../../experiment/top200-careers-har/artifacts/): [D. E. Shaw HAR](../../experiment/top200-careers-har/artifacts/deshaw.har), [MathWorks HAR](../../experiment/top200-careers-har/artifacts/mathworks.har), [Ramco HAR](../../experiment/top200-careers-har/artifacts/ramco.har), their matching `*-api-responses.json` files, and the captured public page HTML. The reproducible Playwright capture is [capture.py](../../experiment/top200-careers-har/capture.py).

| Site | Browser result | Captured JSON responses | What the capture establishes |
|---|---|---:|---|
| D. E. Shaw India | Careers page and standalone application page returned 200 in visible Chrome | 3 | Public `BasicInfo`, `submissionDetails`, and feature-flag responses were captured; this session did not expose a public iCIMS job board or jobs listing API. |
| MathWorks | Careers search, a sample job, and its Careers sign-in page returned 200 in visible Chrome | 8 | Eight JSON-labelled responses came from two first-party endpoints: five returned `{"success": true}` and three returned empty 200 bodies. Job listings and detail content came in the page documents. No sign-in was attempted. |
| Ramco | Careers and a Software Development Engineer job page returned 200 | 4 | Two HubSpot form-definition responses and two page-configuration responses were captured. This supports HubSpot Forms as the intake layer; it does not expose the downstream ATS. |

This records the endpoints those unauthenticated page views actually called. It does not infer hidden backend systems or claim that the sites expose a complete export of all job data.
