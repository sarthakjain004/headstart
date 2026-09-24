# ADR-0209: A Board is named by a curated, stated or humanised name, never by its slug

**Status:** accepted · **Date:** 2026-09-25 · **Amends:**
[ADR-0114](0114-a-board-states-its-company-name-in-its-page-title.md) (its floor was "a title we
cannot read leaves the slug"; the slug is no longer a floor anyone may serve) · **Relates to:**
[ADR-0034](0034-nonprod-boards-dead-by-convention.md) (the vendor-Board blocklist this extends),
[ADR-0172](0172-a-single-source-scraper-declares-its-company.md) (a declared `COMPANY`),
[ADR-0185](0185-trends-narrow-to-companies-picked-from-a-directory-of-boards.md) (the curated
alias as the directory's cross-ATS identity)

## Context

The owner's rule, 2026-09-24: "we cannot show ATS slug as company name anymore". On served table
v121, 232,533 of 520,566 rows carried a slug, a host or a URL as their company
(`nvidia.wd5.myworkdayjobs.com/nvidiaexternalcareersite`, `careers-gd-ais.icims.com`,
`eeho.fa.us2.oraclecloud.com`, `cintel`). ADR-0114 had made the slug the floor: every source that
could not name a Board left it on its slug. Eight research agents measured per-ATS name sources the
same day. Two other changes wire those sources per ATS; this one is the shared policy they call.

## Decision

`headstart.company_name` is the whole naming policy, and `BaseScraper.fetch` applies it once per
Board, after `fetch_raw` and `resolve_company` and before `parse`. A Board's company is, in order:

1. **A curated name**, from `config/company_names.csv` (`board_key,name,evidence`, `#` lines are
   comments, keys lowercased once on load and matched case-insensitively, read at call time
   through `curated_names()`). It overrides every source, per-posting names included, and a
   curated Board skips its title request. It is for Boards that state a name
   nowhere we read, and every row says where a person read it. It replaces
   `board_naming.DISPLAY_ALIASES`, the Hot and Trends alias map, so one map names a Board on every
   surface; it keeps that map's ADR-0185 role as the directory's cross-ATS identity, which is why
   the file's header repeats the Lockheed Martin rule.
2. **A name a source stated during the fetch.** A page title's brand outranks a structured legal
   name where both exist (`brand_first`). A structured field's name is taken as the company typed
   it (`from_field`): not refused for equalling the slug or for being lowercase ("commercetools",
   "sunday"), not read as a hostname ("incident.io"), refused only when empty, padded, a URL or the
   ATS vendor. `from_title` now accepts a domain-shaped name too ("11x.ai") and refuses only a URL
   (a scheme or a leading `www.`; lever served "https://www.azuga.com/").
3. **Title case for an all-caps legal name**, from any source (`title_cased`): a name that is
   entirely uppercase, has more than one word and has a word longer than four letters. Short words
   keep their capitals unless they are legal forms or common words (`_SHORT_WORDS`).
4. **The humanised tenant** (`humanised`): `board_identity.tenant`, board and vendor labels dropped
   (`tidy`), split into words, a lowercase word of three letters or fewer read as an acronym, the
   first letter of every other word capitalised ("1password" is "1Password"). Never the raw slug.
   `board_naming.display_name` returns `humanised(board)` itself for a Board with no name of its
   own, so the Hot and Trends tabs and the served table spell it alike ("Hpe" is now "HPE"). A
   stated lowercase name reaches those tabs as stated unless it repeats the Board's key
   (`echoes_board`): "incident.io" on `gem:incident` stays "incident.io".
5. **No name at all** where the tenant is only a code: the ATSes in `_CODE_TENANTS` (Oracle,
   ADP), and a tenant whose every word is all digits or has digits in two or more runs (`B973N8`).
   `humanised`, `settled` and `display_name` return None, the one "no name" signal; the scrape
   turns it into an empty company at the edge. The Hot tab skips such a Board and counts it as
   `unnamed`, and the Trends directory leaves it unpickable.

"Stated during the fetch" is decided by comparing `self.company` with what the constructor left.
A value the constructor left is humanised only when it is one of the Board's identifiers
(`is_identifier`: lowercase identifier text, a path or address, or exactly a piece of the key), so
a declared `COMPANY` and the curated feed's "Stripe" are kept.

## Measurements

**Title case**, over the 4,454 distinct names in the research samples (keka, zwayam and darwinbox
censuses and the field-ATS samples): 296 are all caps and 129 convert. "IMPRONICS DIGITECH PRIVATE
LIMITED" is "Impronics Digitech Private Limited", "SS SUPPLY CHAIN SOLUTION PVT. LTD." is "SS Supply
Chain Solution Pvt. Ltd.", "IIFL FINANCE LIMITED" is "IIFL Finance Limited". HCL, CRISIL, EPAM and
KPMG stay. The single-word rule is an interpretation: the brief asked to convert a name "that has
at least one word longer than 4 letters" and also to keep CRISIL, a six-letter word, so a single
word is never converted. Known costs of keeping short words: "COLT Technology Services", "PIMA
Controls", "SAVA Healthcare", "PT Bukit Muria JAYA".

**The fallback**, over the 14,439 Boards of the 2026-09-24 bad-boards list: 14,397 carry one of
their own identifiers today (232,303 rows), 24 of them now curated. 810 Boards (26,182 rows) have no
name left: 795 Oracle Boards (26,111 rows), 3 ADP, 3 Taleo Business Edition codes and 9 others.
Oracle's rows stay unnamed until its own source is wired; its research found a site title or
facet naming 734 of 796 hosts.

## Considered and rejected

- **Serving the humanised code** ("Eeho", "37053934 22C6 4362 AA6A 1fee41c0cca3"). It is not a
  slug, but it reads as a company that does not exist. The owner ranked "never show a slug" first,
  and a code shown as a name is the same defect.
- **The registrable domain of the ATS host**, as the code fallback. For Oracle and ADP that host is
  the vendor's (`oraclecloud.com`, `workforcenow.adp.com`) and never names the employer; where a
  host is the employer's own (`careers.qualcomm.com`), `tidy` already reads it.
- **Oracle name prefixes.** Of the oracle ledger's 1,683 live rows (2026-09-25), 833 tenants are
  four-letter pods, 209 six-letter, 560 `fa-{pod}-saasfa…prod1`, and the other 81 split roughly in
  half between a name before the pod (`utulsa-ibvjjb`) and another code (`ia-erp-iaedkf`,
  `hcdtgccprod-iayeqy`). A rule that tells the halves apart would be a guess, so all of Oracle is
  left to its stated and curated sources.
- **A placeholder label** ("Unnamed employer"). Inventing text for the company column is what this
  ADR exists to stop.

## Consequences

- The curated map is the seam for Boards no source names, Workday's first among them. Its format
  is the three columns above; the Workday naming work fills it.
- `company` is never an identity key. The search filter matches it as a substring (a humanised
  "Nvidia" still matches "nvidia"), and hide and follow key on the Board.
- Existing rows are renamed by `update_meta` as their Boards are scraped again, over days, as with
  ADR-0114.
- **Known misses of the code rule**: four real names have digits in two runs and lose their
  humanised fallback: `bamboohr:d1g1t`, `trakstar:24x7table`, `trakstar:91springboard20349` and
  `workday:8x8inc`. Each can be curated.
- A Board-level name a source sets during `fetch_raw` is recognised as stated only if it differs
  from what the constructor left. One that equals it exactly (a title that is the ledger's own
  spelling) is humanised, which only recases it.
- `looks_like_slug` still gates `resolve_company`'s title request; it is untouched.
- A lowercase word of three letters or fewer is read as an acronym, which is right for "HPE",
  "GMV" and "AMD" and wrong for a real word such as `greenhouse:box` or `ashby:zip`. Those
  Boards state their names in fields and titles, so the fallback rarely reaches them.
- `board_identity.tenant` accepts only `http(s)://` while `company_name.without_scheme` strips any
  scheme. They stay two rules: `tenant` decides directory grouping, and changing it regroups
  companies, which is outside this change.
- Fifteen test Boards found by the research go to `config.EXCLUDED_BOARDS`, each read live on
  2026-09-25: BambooHR `implementation` and `whitmansandbox`, four Gem integration sandboxes,
  Jobvite `halogen-customer-support`, and eight SAP SuccessFactors demo tenants `ace19xx`.
