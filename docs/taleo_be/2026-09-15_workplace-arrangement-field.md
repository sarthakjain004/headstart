# TBE workplace-arrangement field: label vocabulary measurement

Measured 2026-09-15, before wiring a native remote/hybrid/onsite field into
`headstart.scrapers.taleo_be.TaleoBEScraper`. Prior behavior set `Job.remote` purely from
`is_remote(location)` — a crude `"remote" in location.lower()` check — missing any structured
signal a Board's detail page states.

## Method

Sampled live rows from `data/validate/liveness/taleo_be.csv` (533 live, 400 with `jobs > 0`),
stratified across all 7 shard hosts (`phe`/`phf`/`phg`/`phh`/`tre`/`lde`/`syf`). For each sampled
Board: fetched the listing page, took up to 3 requisition detail pages, and extracted every
`_labels()` pair (the scraper's own generic `<span>label</span><strong>value</strong>` /
`cws-V2-reqfieldcell` parser). Two passes, 105 boards total, org-deduped down to **80 distinct
tenants** (`org=` values) — several ledger rows share one `org` across multiple `cws` career-site
instances (e.g. `ARNOTTS` sampled under 9 different `cws` ids, `COVESTIC2` under 3), which is not
independent evidence and was collapsed before counting.

Detection was value-first, not label-first: every `(label, value)` pair was checked for a value
matching a known discrete workplace-arrangement vocabulary (`remote`, `hybrid`, `onsite`,
`on-site`, `in-office`, `telework`, …), since TBE tenants configure their own label text and a
keyword search over label *names* alone (the first pass's approach) missed a real field —
`location type` — that carries no obviously-named keyword.

## Result

**2 of 80 distinct tenants (2.5%) state a discrete workplace-arrangement field at all.** Two
different label spellings, two different value vocabularies — no other spelling appeared across
the sample:

| Tenant | Label (as `_labels()` keys it) | Values seen |
| --- | --- | --- |
| 1199SEIU Funds (`org=NBF1199`) | `"Workplace Arrangement:"` — **with** the trailing colon | Hybrid (7/8 sampled jobs), In-Office (1/8) |
| Covestic (`org=COVESTIC2`) | `"Location Type"` — no colon | Onsite (5/6), Remote (1/6) |

The colon matters: `_field()` matches names literally (no punctuation normalization), so a lookup
list of `"Workplace Arrangement"` alone would silently miss NBF1199's real label — confirmed live
by probing `_field(labels, "Workplace Arrangement")` (`None`) vs.
`_field(labels, "Workplace Arrangement:")` (`"Hybrid"`) against the same fetched page. The scraper
therefore looks up both spellings, plus `"Location Type"`.

No tenant in the sample used "Hybrid" as a `Location Type` value or "Remote"/"Onsite" as a
`Workplace Arrangement` value — the two fields' vocabularies didn't overlap in what was observed —
but `_workplace_remote()` classifies by value text regardless of which label carried it, so either
field stating either vocabulary is read correctly.

## Yield caveat

At 2/80, most TBE tenants state no structured signal at all and keep relying on the
`is_remote(location)` fallback (e.g. Agios's own `"Remote - US"` free-text location, captured under
a still-unread label spelling, `"Primary Work Location"` — out of scope here, since it's a location
field, not a discrete flag). This is a small, real gain, not a broad one; it is unconditionally
safe because unrecognized or unmatched values fall through to the pre-existing fallback rather than
guessing.
