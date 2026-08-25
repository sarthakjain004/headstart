# ADR-0086: The India filter matches country tags, not just place names

**Status:** Accepted · **Date:** 2026-08-25 · **Extends ADR-0024 (the India gazetteer)**

## Context

ADR-0024 built the gazetteer on one measured finding: 47% of India rows never contain the word
"india", because zoho/keka/ripplehire write city-only strings. The answer was a city alias map.

Re-auditing against the live 317,421-row table on 2026-08-25 (`scripts/eval/location_filter_audit.py`,
findings in `docs/india-location-filter/2026-08-25_audit.md`) found the city map has a blind spot
of its own, and it is not the one you would guess. It is not missing *cities* — reading the top
110 unmatched strings by hand surfaced no Indian city at all, and a scan for ~90 Indian towns
absent from `CITIES` returned traps rather than misses (`salem` → Jerusalem/Winston-Salem,
`kota` → **Dakota**, `agra` → Agrate Brianza, `erode` → Wernigerode; all correctly excluded
already).

The blind spot is rows that carry a **country tag and no place a gazetteer could hold**: a plant,
a tower, a campus code, or a town too small to enumerate. `IND-BLR-Divyasree Technopolis`,
`IND BNGL FL2-3 TWR 3`, `Jagiroad, AS, IN`, `Vemagal, KA, IN`. 432 rows, and no amount of city
curation reaches them.

The same audit found the country term itself is a **substring** — `LIKE '%india%'` — so it was
claiming "Indian Head, MD", "Indialantic, FL", "Indian Springs, NV", "Indianola, PA" and Diego
Garcia's "British Indian Ocean Territory".

## Decision

Add two country-level rules and one guard set. Measured on the live table: **+429 rows added, 17 removed, net +412 (49,480 → 49,892)** — of the added rows, +327 come
from the `IND` rule and +102 from the subdivision tail, with no overlap between them, with every removed string verified non-India by hand.

### 1. ISO alpha-3 `IND`, matched only where it is the country tag

`ind` sits inside Indore, Indianapolis, and a hundred ordinary words, so this is never a bare
substring match. Only four anchored forms (`IND_FORMS`), derived from the shapes actually
observed: `ind-%`, `ind %`, `%(ind)%`, `% - ind`, plus exact `= 'ind'`.

**`IND` is also Indianapolis's IATA code**, and that is how the naive version bites: the row
`IND U; CVG SD; United States, PA, Philadelphia - Remote; MKE W; MSP` is a list of US airports.
`IND_EXCLUDE` guards on the one token that settles it — "united states". That is deliberately
narrow, and it is the known limit of this rule: an airport-list row that never names the country
(`IND U; CVG SD; MKE W`) would still be claimed. No such row exists in the live table today, and
widening the guard to airport codes would trade a rare miss for a permanent maintenance burden.

The trailing form is `'% - ind'`, **not** `'% ind'`, because the looser one also claims
"Grayslake, Ind" — Illinois. That single character of anchoring is the whole difference.

### 2. The subdivision tail `City, ST, IN`

Workday writes `Vemagal, KA, IN`. The 36 ISO 3166-2:IN codes **plus the four vehicle-registration variants** ATSes also use — the
two schemes disagree on five states and the data uses both. Measured: `tg` (ISO, Telangana) has
55 tails in the live table while `ts` (the vehicle code) has **0**, so a first pass that shipped
only the vehicle codes would have missed a Telangana tail town entirely. Its net cost today was
zero purely because those 55 rows also carry "Hyderabad" — luck, not design.

Two letters would be catastrophic loose, so they are only ever matched inside the anchored
`', {code}, in'` tail. Zero false positives across all 40 codes, `or` (Odisha) included.

### 3. `INDIA_EXCLUDE` for US places containing "india"

The country term **stays a substring on purpose.** The obvious fix — a word-boundary `\bindia\b`
test — is wrong: `IN_India_WFH` has no word boundary around "india" and would be *lost*. So the
fix is a NOT-LIKE list of the specific offenders, in the same shape as `EXCLUDE`'s per-city
guards.

The guards are scoped to the country sub-clause alone, not the whole expression. That is what
keeps `Nagpur, Maharashtra, British Indian Ocean Territory` — a real India row wearing a bad
country tag — matching via `maharashtra`.

## Consequences

Query-time only. Nothing is re-derived, re-embedded or re-indexed; the next search simply matches
more rows. That is the standing advantage of ADR-0024's LIKE-expansion design over a stored
country column, and it is why this was cheap enough to be worth doing for 0.8%.

`tests/test_geo.py` carries the new rows and traps in its existing table-driven form. Proven
against the pre-fix clause: it fails **8 missed + 5 false positives**, and passes clean after.

Two things this deliberately does **not** do. It does not add the tail towns the audit found by
hand (Chakan, Cheyyar, Kanchipuram, …) — the anchored subdivision rule already reaches most of
them, and each new alias costs a world-substring vetting pass. And it leaves the genuinely
ambiguous bucket alone: a bare trailing `, IN` is India's alpha-2 *and* Indiana's USPS code, and
339 rows sit in it. Splitting those needs evidence outside the location string.

The residual known error is now dominated by field health, not by the gazetteer: **8,592 rows
(2.7%) carry no `location` at all** — every one a NULL, none an empty string — concentrated in
zoho (17.3%), successfactors (14.8%) and recruitee (13.4%). No place filter can reach those, and
fixing it is scraper work.
