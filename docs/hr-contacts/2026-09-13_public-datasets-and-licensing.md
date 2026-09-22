# Public HR/recruiting contact sources: feasibility and licensing

*Research date: 2026-09-13. Scope: a HeadStart `company -> public email address`
mapping, with an emphasis on generic recruiting, careers, talent-acquisition, and
application inboxes. This is product/licensing research, not legal advice.*

## Executive finding

**Do not seed a production dataset from a pre-made "HR email" download.** The
search found no public source that is simultaneously global, current, mapped to a
company, restricted to HR/recruiting role inboxes, and supported by auditable source
provenance. The closest apparent match, Hugging Face's `moa7amed/HREmails`, is a
79,180-row flat list with an MIT metadata label, but its card says that the curator,
sources, collection process, personal-data assessment, and limitations are all
"More Information Needed"; the preview includes named corporate addresses and the
repository was last updated in 2024. It is not an evidence base for a current
company mapping. [Dataset card](https://huggingface.co/datasets/moa7amed/HREmails)

The defensible product is instead a **provenance-first role-inbox ledger**:

1. Extract only publicly displayed addresses from a HeadStart job description,
   application instructions, or employer/ATS careers page.
2. Keep an address only when its evidence is still live and its surrounding text
   explicitly describes recruiting, careers, application, candidate support, or
   accommodation/EEO handling. Record the exact evidence URL and observation time.
3. Make *generic* inboxes (for example, `careers@`, `jobs@`, `recruiting@`,
   `talent@`) the default product. Put a person-named address in a separate,
   opt-in review queue; do not publish it or use it for marketing by default.
4. Use a licensed enrichment product only as a **discovery/verification sidecar**,
   preserving its licence terms and independently rechecking the source page before
   an address enters HeadStart's ledger.

That direction uses HeadStart's unusually good company/board identity and live job
URLs, avoids representing a data-broker result as employer-published, and produces
a dataset that can be refreshed and removed accurately.

## Sources and tools considered

| Direction/source | What it can actually contribute | Licence/terms and coverage constraint | Decision |
| --- | --- | --- | --- |
| **HeadStart's own public job/careers evidence** | The best company join: board key, current job URL, employer context, and a page on which an address can be checked again. It can recover role inboxes embedded in posting text or application instructions. | Each employer/ATS remains the content owner; there is no single licence covering all boards. Follow the source site's terms/robots, rate limits, and removal requests. The evidence should be retained as a URL, timestamp, and minimal contextual label, not a copied archive of job text. | **Primary source.** High precision, unknown/likely incomplete recall. |
| **Hunter Domain Search API** | Takes a company/domain and returns email results with source URLs. Its API distinguishes `generic` (role-based) from `personal`, exposes source first/last-seen dates, and offers department classifications including `hr`. Generic results can be lexical-ranked for recruiting/careers/talent inboxes. [API reference](https://hunter.io/api-documentation) | Paid, rate-limited API; the source itself may be old or off-company-domain, so it is discovery rather than proof. Hunter retains its and third-party IP and prohibits building a similar/competitive service without written consent. Its API also has legal-removal responses. [Terms](https://hunter.io/terms-of-service) | **Best evaluated sidecar.** Query only HeadStart-resolved company domains; retain API source metadata; fetch the source/live career page before accepting an address. Do not redistribute a raw Hunter-derived contact corpus without contract review. |
| **People Data Labs (PDL)** | A licensed person-data service; paid bundles can return email fields and employment information. [Person schema](https://docs.peopledatalabs.com/docs/fields) | Its enrichment endpoint requires a known person/profile/email/phone/ID, or name plus company/location-style attributes; it is not an all-company role-inbox lookup. Contact-field values require Pro access. [Input requirements](https://docs.peopledatalabs.com/docs/input-parameters-person-enrichment-api) | **Not a fit for this role-inbox dataset.** Consider only under a separately negotiated, person-contact use case with privacy review. |
| **Apollo** | A commercial B2B contact product. Export fields include business email, verification/source fields, job title, and company website. [Export field documentation](https://knowledge.apollo.io/hc/en-us/articles/4409237712141-Export-Contacts-to-a-CSV) | This is named business-contact data, not a public HR-inbox dataset. Apollo says customers remain responsible for any additional consent/notices needed for their use. [Apollo trust material](https://trust.apollo.io/api/statuspage/share/4d613c64-8b94-4789-a90f-38919215a13b/public/download?fileEngagementSource=file_row&fileId=c4942921-6383-482a-b2d0-011a771465b3&itemUid=9d9c6a4d-f335-4a92-ad43-ab4aae24bf0e&productId=default) | **Out of scope by default.** It would turn a role-inbox feature into a regulated, licensed people-data product. |
| **Hugging Face `HREmails`** | A flat CSV of 79,180 addresses, tagged jobs/HR and licensed `mit` in the repository metadata. [Dataset card](https://huggingface.co/datasets/moa7amed/HREmails) | The card has no identified curator, source, collection/processing method, personal-data analysis, or limitations. Preview rows include named address local-parts; the metadata last changed 2024-10-15. An asserted MIT label cannot repair unknown provenance or make the claims current. | **Reject as a production seed.** At most, use a small, isolated research sample after legal approval; never merge it into the evidence ledger. |
| **Wikidata P968** | Fully queryable open data: P968 is an email-address property usable for people or organisations. [Property](https://www.wikidata.org/wiki/Property:P968) | Main structured data is CC0. [Licensing policy](https://www.wikidata.org/wiki/Wikidata:Licensing) It has no HR/recruiting semantics and is too sparse/heterogeneous for broad company coverage. | **Optional low-volume supplement** for organisation-level contacts only; require an employer-domain match and a live source check. |
| **OpenStreetMap `contact:email`** | A structured tag for an email associated with a mapped object. Its own privacy note says to add only addresses intended to be public, such as business/government inquiry contacts. [Tag guidance](https://wiki.openstreetmap.org/wiki/Key:contact:email) | ODbL attribution and share-alike obligations apply to the database/derivative database. [OSM licence](https://www.openstreetmap.org/copyright) It is primarily a geographic POI source, not an HR or company roster. | **Do not mix into the first release.** It may be useful later for generic corporate contact discovery only if ODbL obligations are accepted and company identity is verified. |
| **Common Crawl** | Technically contains raw web pages plus WET extracted text, and is accessible without an AWS account. [Data formats/access](https://commoncrawl.org/get-started) | Explicitly unsuitable: its ToU says crawled pages may have their own terms and prohibits collecting/harvesting PII for use separately from Crawled Content; it also says users must assess legality and third-party rights. [ToU](https://commoncrawl.org/terms-of-use) | **Reject.** Do not mine it for an email-address ledger. |

### What the source table means for coverage

There are two distinct questions that should not be collapsed:

- **Does a company have a public email?** Hunter, OSM, Wikidata, and public pages may answer this.
- **Is it the company's recruiting/application inbox?** Only context on an employer-owned careers/job page, or an independently rechecked source explicitly saying so, establishes that. A `contact@` address is not evidence that it accepts applicants.

Consequently, no source above supplies an honest coverage percentage for "all HeadStart
companies." Measure that after a pilot against a frozen, deduplicated company/board
sample; report both **companies with a qualifying role inbox** and **companies with
only a generic/non-HR address**, never a blended number.

## Recommended data contract and collection policy

Use one row per `(canonical_company_or_board, normalized_email, purpose)`, not one
row per person. Suggested fields:

| Field | Purpose |
| --- | --- |
| `company_key`, `board_key`, `company_name` | Stable HeadStart join; preserve the Board as the source identity where a parent company has several hiring brands. |
| `email`, `purpose` | Normalised address and a controlled purpose: `application`, `recruiting`, `careers`, `candidate_support`, `accommodation_eeo`, or `general_contact`. |
| `address_type` | `generic` or `personal`. Personal must be quarantined/default-excluded. |
| `evidence_url`, `evidence_source`, `observed_at`, `last_checked_at` | Reproducibility, freshness, and removals. `evidence_source` should distinguish `employer_page`, `ats_job`, and `licensed_provider`. |
| `evidence_context` | A short, redacted label such as "Email resumes to" or "accommodation requests"; no full job description required. |
| `source_domain_match`, `verification_status` | Whether the address domain matches the employer/known recruiting domain, plus a non-invasive live-page/format/MX outcome. Do not infer an address from a guessed pattern. |
| `suppression_reason`, `removed_at` | A durable no-contact/removal trail. It is safer than deleting an opt-out and accidentally re-adding it on the next crawl. |

Acceptance should require all of the following: (1) public display on a source page
or an API source URL that still resolves; (2) a company/board join; (3) explicit
recruiting/candidate context for a recruiting-purpose label; and (4) no indication
that the address is obfuscated, private, or opt-out/removed. A generic corporate
address lacking recruiting context may be retained only as `general_contact`; it
must not be surfaced as a hiring contact.

For freshness, recheck the evidence page periodically and expire a row when the
address disappears. Do not use SMTP probing as proof of ownership: catch-all mail
domains make it ambiguous, and active probing is not needed when a live source page
already supplies evidence.

## Privacy, usage, and publication risks

Generic role inboxes materially reduce risk but are not a universal exemption. A
named work address identifies a person and is personal data under UK regulator
guidance; a generic department inbox usually does not. The same guidance says that
public availability is not consent to direct marketing, and that bought/sold business
contact lists involving personal data need a lawful basis and transparency. [ICO B2B
guidance](https://ico.org.uk/for-organisations/direct-marketing-and-privacy-and-electronic-communications/business-to-business-marketing/)

For EU personal data, GDPR Article 14 imposes information duties when data was not
obtained from the data subject, and Article 21 gives an absolute objection right for
direct marketing. [GDPR text](https://eur-lex.europa.eu/eli/reg/2016/679/) National
e-privacy/anti-spam rules still vary; for example, the ePrivacy Directive regulates
unsolicited electronic-mail marketing and requires a valid opt-out address in all
cases. [Directive 2002/58/EC, Article 13](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32002L0058)

Practical guardrails:

- Make this a **jobseeker routing/contact-support feature**, not an outbound
  prospecting list. Do not send bulk mail from it.
- Publish only source-backed generic role inboxes, preferably behind an explanation
  and a report/removal path. Named contacts should be excluded unless the person has
  expressly opted in or counsel approves a narrowly defined use.
- Maintain a suppression ledger and immediately stop showing/recollecting a disputed
  personal address; do not simply delete it and rediscover it later.
- Treat data-broker API results, job-board content, and their source URLs as
  separately governed inputs. Keep a per-source licence/terms record and refresh it
  before a production run.
- Obtain privacy counsel review before any cross-border publication, monetisation,
  personal-email collection, or email campaign.

## A small, falsifiable pilot

Before committing to a broad build, run a 500-board stratified pilot across ATSes,
regions, and company sizes. Compare three arms on the same frozen board sample:

1. Existing job descriptions/application instructions;
2. One linked public careers/contact page per board (only where permitted); and
3. Hunter Domain Search, if a licence/trial is approved, followed by live source
   validation.

For each arm, measure: qualifying generic-role inbox yield, precision from manual
review, proportion of `personal` addresses, source-domain mismatch rate, and the
fraction still displayed at a 30-day recheck. A result is successful only if it
produces a clearly labelled, source-backed generic inbox at acceptable precision;
raw address count is not a success metric. This pilot will answer the real economic
question—coverage per company and cost per valid inbox—without prematurely creating
a sensitive, stale contact database.

## Search method and limits

I checked official documentation/terms for contact-enrichment products and open
sources, then searched Hugging Face, Kaggle, Zenodo, and Harvard Dataverse for a
public HR/recruiting email dataset. Marketplace results were predominantly synthetic
HR analytics data, unprovenanced flat email lists, or sample B2B contact files. This
is not proof that no niche list exists; it is evidence that none located in this
search meets the production bar above. Any future candidate should be evaluated
against the same provenance, purpose, freshness, and redistribution tests before
import.
