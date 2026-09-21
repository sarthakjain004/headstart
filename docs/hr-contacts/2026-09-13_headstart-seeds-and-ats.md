# HeadStart seeds for a public recruiting-contact dataset

**Question.** Can HeadStart's existing jobs corpus seed a Company -> public HR,
recruiting, talent, or careers email mapping, and do the four common public ATS APIs
already expose such contacts?

**Result.** Yes, the job-description corpus is a useful *role-inbox* seed, but it is
not a recruiter-identity dataset. The public Greenhouse, Lever and Ashby board APIs
did not expose recruiter/owner/contact-email fields in live checks. SmartRecruiters
details expose a sometimes-populated creator name, never its email. Therefore the
low-cost, defensible first dataset is ``company -> publicly stated role inbox`` with
per-occurrence evidence; it must not label EEO/accommodation inboxes or personal
addresses as general recruiting contacts.

This is a research note, not a scraper change. Live API observations below were made
on 2026-09-13. Existing-corpus counts are explicitly dated because the production
data lives in the private HF dataset and the working-tree copy is not authoritative.

## What HeadStart already has

Each scraper normalizes a Job to ``id``, ``ats``, ``company``, ``url`` and plain-text
``description`` ([Job model](../../src/headstart/models.py#L12-L35)). The canonical
corpus reader deduplicates resumed output by job id, but has no email extraction or
contact fields ([corpus reader](../../src/headstart/corpus.py#L54-L69)). A repository
search found no recruiting/email extractor in ``src/`` or ``scripts/``; the current
scrapers only retain the fields represented by ``Job``.

The useful source is the durable description store: it holds ``{id, description}``
per ATS, appends fresh text, and restores it into a run when a later fetch fails
([implementation](../../src/headstart/ingest/update_descriptions.py#L1-L33),
[record shape](../../src/headstart/ingest/update_descriptions.py#L112-L145)). It is
materially better than the local ``data/jobs/`` files for a build: those are ephemeral
stage output and several visible files are from July. The source-of-truth rule is the
private HF dataset; refresh only the small needed slice or query the served table, not
the whole vector store ([project deployment guidance](../agents/deployment.md)).

A prior targeted corpus audit (2026-09-11) found 71,030 email-bearing descriptions
of 495,343 (14.3%), with 6,711 unique addresses. Its context pass placed 48,172
occurrences in accommodation/EEO language, 16,578 in application/recruiting language,
and 18,000 elsewhere. The repeated addresses were predominantly role/compliance
inboxes. These are useful directional seed measurements, not current production
counts; rerun them against the refreshed description store before publishing a
coverage number. The underlying audit was a dated, run-local measurement rather
than a versioned dataset artifact, so this note does not make its counts a current
coverage claim.

One important coverage limit: every current scraper applies ``html_to_text`` to the
description. It removes tags and keeps their visible text
([implementation](../../src/headstart/models.py#L55-L64)). Thus a ``mailto:jobs@example.com``
whose visible label is "Contact us" may have already lost the email before the
description store sees it. Description extraction finds visible addresses, not every
mailto target. A later enrichment pass over raw career pages/ATS HTML is the separate
high-recall route for that gap.

There is also direct evidence that the project contains email-only careers flows:
the careers-page investigation classified 15 DIY boards as ``mailto:careers@``
([finding](../discovery/2026-09-07_resolving-the-opaque-careers-pages.md#L182-L190)).
Those are high-confidence Company -> careers-inbox seeds once each original page URL
and capture date is retained.

## Public ATS field check

The check used anonymous public board endpoints only. It does not make a claim about
authenticated employer/recruiter APIs, which have a different privacy and authorization
boundary.

| ATS | Live public surface checked | Observation | Dataset implication |
| --- | --- | --- | --- |
| Greenhouse | [GET board API](https://boards-api.greenhouse.io/v1/boards/stripe/jobs?content=true) | Envelope keys were ``jobs`` and ``meta``; sampled jobs had posting, department and content data, with no owner/recruiter/email key. The project scraper maps that same public payload into Job fields ([parser](../../src/headstart/scrapers/greenhouse.py#L58-L82)). | Mine visible addresses in ``content``/stored descriptions; do not schedule an owner-detail pass. |
| Lever | [GET postings API](https://api.lever.co/v0/postings/100ms?mode=json&limit=2) | Two live postings returned posting/category/application content without owner/recruiter/email keys. The scraper notes that the public postings API has no company name and maps only posting fields ([board contract and parser](../../src/headstart/scrapers/lever.py#L364-L423)). | Mine visible description text; resolve company from the board/page, not an imagined recruiter field. |
| Ashby | [GET board API](https://api.ashbyhq.com/posting-api/job-board/10xteam) | Envelope ``apiVersion`` + ``jobs``; no recruiter, owner, or contact-email field in the sampled jobs. The board API itself lacks a company field ([scraper note](../../src/headstart/scrapers/ashby.py#L156-L165)). | Mine public job description text; obtain company identity from the board page/ledger. |
| SmartRecruiters | [Listing](https://api.smartrecruiters.com/v1/companies/0rijinVillage/postings?limit=2) plus [two details](https://api.smartrecruiters.com/v1/companies/0rijinVillage/postings/743999839224974) | Six details across 01Systems, 0rijinVillage and 10xvaluepartnersgmbh all contained ``creator: {name, avatarUrl}``; two had a non-empty creator name and four an empty one. None contained creator email, recruiter, owner, or contact-email fields. The project already uses this detail endpoint for description/salary ([detail endpoint](../../src/headstart/scrapers/smartrecruiters.py#L128-L160), [mapped fields](../../src/headstart/scrapers/smartrecruiters.py#L257-L309)). | ``creator.name`` is weak optional provenance for a *posting creator*, not an email lead and not a recruiter assertion. Do not infer an address from it. |

These new observations reproduce the 2026-09-11 twenty-posting / ten-tenant
SmartRecruiters audit: creator names appeared in 4/20 postings and no creator email
appeared. Its broader negative result for Greenhouse, Lever and Ashby remains valid
for anonymous board APIs, now rechecked on live public endpoints.

## Required contact record and evidence policy

Keep immutable occurrence evidence separate from the deduplicated mapping. A minimal
occurrence schema is:

```text
company_key, company_display, board_key, ats, job_id, job_url,
email_raw, email_canonical, contact_kind, context_class, confidence,
source_kind, source_url, source_quote, observed_at, source_content_hash
```

- ``source_kind`` is one of ``job_description_visible_email``,
  ``career_page_mailto`` or ``ats_public_metadata``. The last is currently not a
  route to emails for the four APIs above, but names the distinction honestly.
- ``source_quote`` is the smallest surrounding sentence/window that both contains
  the address and explains its purpose. It supports review and later reclassification;
  store a hash of the complete source text instead of duplicating a whole JD.
- ``observed_at`` is a collection timestamp, not ``posted_at``. Keep a last-verified
  timestamp too; a contact can become stale while a job remains indexed.
- Preserve ``email_raw`` and canonicalize a separate value by removing ``mailto:``,
  trimming punctuation/space, and lowercasing the domain. Lowercase the local part
  only as a practical dedupe key while retaining raw form; email standards permit
  local-part case distinctions even though most employers do not use them.

``company_key`` should be the real scraper ``board_key()``, not a hand-split job id:
some providers override that identity and ``board_of`` explicitly documents why
parsing on the last colon is only a fallback
([model contract](../../src/headstart/scrapers/base.py#L340-L375),
[corpus caveat](../../src/headstart/corpus.py#L25-L50)). Map aliases/subsidiaries as a
many-to-many relation rather than forcing an email into one display-name spelling.

Deduplicate at two levels:

1. **Occurrence:** retain one row per ``(job_id, email_canonical, source-content-hash)``.
   It preserves independent evidence and lets a closed job age out without deleting a
   contact immediately.
2. **Published mapping:** group by ``(company_key, email_canonical)`` and retain the
   newest evidence plus occurrence count, first/last seen, all source kinds, and the
   strongest *non-conflicting* class. Do not merge identical addresses across different
   companies: outsourced recruiting, parent companies and shared service desks are real.

## Classification: what the dataset may claim

Use a deterministic address pattern plus a sentence-level context rule, and retain the
two independently. A proposed initial taxonomy:

| Class | Rule/evidence | Include in a public recruiting-contact export? |
| --- | --- | --- |
| ``recruiting_role_inbox`` | Role local-part (``careers``, ``jobs``, ``talent``, ``recruiting``, ``recruitment``, ``hiring``, ``hr``, ``people``) and nearby application/contact language. | Yes; highest priority. |
| ``application_contact`` | Explicit "send/apply/email your resume/CV" language, even if address does not match a known role prefix. | Yes, labelled as application-specific. |
| ``accommodation_or_eeo`` | Nearby reasonable-accommodation, disability, EEO, equal-opportunity, or compliance language. | Keep as a separate accessibility/compliance contact; **do not** present as a recruiter inbox. |
| ``other_business_contact`` | Public corporate address but no hiring-context proof. | Keep only in evidence/review output, not the recruiting mapping. |
| ``personal_address`` | Personal-looking local part or free-mail domain, even if publicly printed. | Exclude by default from a general dataset; require a policy-approved, clearly job-related exception and a manual review. |
| ``invalid_or_placeholder`` | Test/example address, malformed text, or an ATS/vendor address unrelated to the company. | Exclude. |

The classification has to use the actual source wording; a local part such as
``hr@`` alone is not proof that it accepts applications. The prior audit's large
accommodation/EEO bucket is exactly why a raw-email-only export would be misleading.

## Recommended first build, bounded to HeadStart data

1. Refresh/query the durable description store and a small current job/board identity
   projection. Extract addresses from stored text with source windows; do not treat
   July's local raw JSONL files as current data.
2. Produce the occurrence table and a review sample per class/company. Validate a
   sample by fetching the original job URL and checking that the address is still
   visible and context is classified correctly.
3. Add the explicit ``career_page_mailto`` lane for the known DIY/email-only boards
   and, later, a bounded public careers-page scan to recover link-href-only addresses
   discarded by ``html_to_text``.
4. Publish only ``recruiting_role_inbox`` and ``application_contact`` as the initial
   Company -> email mapping; retain EEO/accommodation separately and omit personal
   addresses by default.

This approach uses HeadStart's strongest existing asset (public text already collected
for jobs) without inventing owner/recruiter data that public ATS boards do not provide.
