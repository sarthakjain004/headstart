# ADR-0271: A scraper declares whether discovery keeps its slug's casing

**Status:** accepted · **Date:** 2026-09-29 · **Relates to:**
[ADR-0157](0157-a-scrapers-job-url-is-declared-once-not-authored-three-times.md) (`url_shape`, the
same one-declaration pattern) · **Issue:** #827

## Context

Both careers-page fingerprinters, `scripts/resolve/fingerprint.py` and
`scripts/discover/fingerprint_careers.py`, lower-case every slug they capture except for the ATSes
in a set each file kept by hand, `CASE_SENSITIVE`. The two sets drifted. #813 added `lever` to
`resolve/fingerprint.py` only, so `fingerprint_careers.py` kept verifying live mixed-case Lever
Boards as dead until #824 patched it.

The sets also mixed up two separate facts about an ATS:

- **Lever.** It reads a slug case-sensitively, and 241 of its 3,048 `live` ledger rows carry
  capitals. `jobs.lever.co/Onehouse` answers 200 and `/onehouse` answers 404 (2026-09-29). A
  lower-cased slug loses the Board.
- **SmartRecruiters.** Its API is case-insensitive: `01Systems` and `01systems` both answer 200
  with 6 postings (2026-09-29). But 8,059 of the 12,458 ledger rows carry capitals, and
  `check_liveness.py` keys a ledger on the raw slug spelling in its `tenant` column. So a
  lower-cased slug lands as a second row for a Board we already hold.
- **PyjamaHR** was in `fingerprint_careers.py`'s set. It does read a slug case-sensitively, but
  every PyjamaHR slug is lower-case. None of the 676 slugs in its jobs sitemap carries a capital,
  and none of its 768 ledger rows does. Re-cased, 3 of 3 slugs tried answer `count: 0`
  (`8Byte`, `1-Percent-Group`, `7th-Sky-Technologies-LLC`), and `jobs.pyjamahr.com/8Byte`
  answers 404 (2026-09-29). So keeping a captured capital can only name a dead slug.

The question the fingerprinters ask is not whether the ATS is case-sensitive. It is whether
lower-casing a captured slug loses the Board.

## Decision

**Each scraper declares `keeps_slug_case` (default False), and both fingerprinters derive their
set, `KEEPS_SLUG_CASE`, from `registry.SCRAPERS`.** It is True on `LeverScraper` and
`SmartRecruitersScraper`, each with its measurement beside it. PyjamaHR leaves it False, so
`fingerprint_careers.py` now lower-cases a captured PyjamaHR slug to the one its API answers.

The attribute is set True only for an ATS where a live measurement showed that lower-casing a
slug loses its Board. `tests/test_scraper_registry.py` pins the set to `{lever, smartrecruiters}`,
so adding an ATS means editing that test and citing the measurement here. Ashby stays False: 4 of
4 mixed-case Ashby Boards tried (`Elveo`, `Feegow`, `Discovery-Loop`, `Blackpoint%20Cyber`)
answer the same lower-cased (2026-09-29).

The attribute covers only a bare slug token. An ATS whose Board is a URL (Workday, and ADP in
`fingerprint_careers.py`) builds that URL, and its casing, in its own branch.

`resolve/fingerprint.py` has no PyjamaHR pattern, so the two fingerprinters detect different ATSes.
`tests/test_scraper_registry.py` runs both on one mixed-case link per declared ATS, and on an
undeclared Ashby link, so a fingerprinter that stops reading the attribute fails it.

**`cc_miner`, `mine_lever` and `merge_harvest_into_tenants` do not read the attribute.** They
lower-case no path slug for any ATS. They keep the slug as captured and match it case-insensitively
(#813). That is a separate rule, and it cannot drift against the attribute, because it never
lower-cases a Lever or SmartRecruiters slug. Making these scripts lower-case on the attribute's
say-so would change what they write for every other ATS, including the 38 mixed-case Ashby slugs
and 407 mixed-case Workday slugs among the ledgers' Live rows.

## Alternatives

- **One shared constant in a script module that both fingerprinters import.** It fixes the drift,
  but the knowledge would sit away from the scraper that knows how its ATS reads a slug.
- **Name it `slug_case_sensitive`.** That was the first name suggested. It reads as a claim about
  the ATS, and would be wrong both ways: SmartRecruiters is not case-sensitive but must keep case,
  and PyjamaHR is case-sensitive but must not.
- **Keep PyjamaHR in the set to preserve behaviour.** No mixed-case PyjamaHR link was found in any
  local data, so the change moves little today. But keeping case there is measurably wrong, and
  one declaration per scraper should say what is true.
