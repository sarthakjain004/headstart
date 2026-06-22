# Source: cboyd0319/JobSentinel  (fingerprinter pattern intel, NOT a slug list)

- **URL:** https://github.com/cboyd0319/JobSentinel
  (files: `src-tauri/src/core/automation/ats_detector.rs`, `src/mocks/handlers/atsPlatform.ts`)
- **Author/repo:** cboyd0319/JobSentinel
- **Retrieved:** 2026-06-23
- **Access:** public, two files fetched via raw.githubusercontent.com
- **License:** none confirmed; reference use only.

## What it is
A desktop job-tracker whose `ats_detector.rs` is a clean **URL→ATS fingerprinting ruleset**
(host/path matching) for 28 ATS platforms. Not company slugs — but directly relevant to HeadStart's
CLAUDE.md note that "the fingerprinter has no pattern for these yet". Useful as a cross-check /
starter for HeadStart's own ATS fingerprinter.

## Providers fingerprinted (28)
AdpRecruiting, AshbyHq, BambooHr, BreezyHr, Bullhorn, **Comeet** (+ `sparkhire.com` alias),
**Eightfold**, **Freshteam**, Greenhouse, Icims, JazzHr (+ `jazzhr.com`/`jazz.co`), JobScore,
Jobvite, **Jobylon**, Lever, **OracleRecruiting** (`careers.oracle.com` + `*.oraclecloud.com`),
Personio, **Phenom** (`phenompeople.com`), **Pinpoint**, Recruitee, **Rippling**, SmartRecruiters,
SuccessFactors (`successfactors.com`/`.eu`/`sapsf.com`), Taleo, Teamtailor, Ukg, Workable, Workday,
**ZohoRecruit**.

## Notable host rules (for HeadStart fingerprinter)
- Comeet ⇐ `comeet.com` OR `comeet.co` OR `sparkhire.com`
- Phenom ⇐ `*.phenompeople.com`
- Oracle ⇐ `careers.oracle.com` OR `*.oraclecloud.com`
- Eightfold ⇐ `*.eightfold.ai` (and Microsoft careers)
- Workday ⇐ `*.myworkdayjobs.com` OR (`workday.com` + path has `job`)

## Files saved (artifacts/)
- `ats_detector.rs` — the 28-provider fingerprint ruleset (verbatim).
- `atsPlatform.ts` — companion platform enum/mocks.

## One-line
28-provider URL→ATS fingerprinting ruleset — pattern intel for HeadStart's fingerprinter (incl.
Comeet/sparkhire, Phenom/phenompeople, Oracle Cloud, Eightfold, Jobylon), not company slugs.
