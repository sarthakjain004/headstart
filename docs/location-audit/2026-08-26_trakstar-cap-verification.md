# Trakstar 25-card cap — live re-verification of the granular numbers

**Date:** 2026-08-26 · **Scope:** the specific per-board and per-sample figures cited in
`src/headstart/scrapers/trakstar.py`'s module docstring (PR #306) · **Why this doc exists:** the
original figures (906 feed-verified boards, 4,968/10,210 jobs, 72/906 boards capped, a 58/60
button-presence sample, a 3.9% feed-unreachable rate) came from
`experiment/location-audit-2026-08-25/trakstar.md`, which is **gitignored and was never
committed** — exactly the failure mode `2026-08-25_ats-field-audit.md`'s own opening paragraph
warns about ("an earlier fix in this same series was measured, reviewed, committed and then lost
because it only ever existed outside the tracked tree"). A citation pointing at that summary doc
does not, on its own, back the granular numbers sitting next to it — the summary only carries the
one headline figure (see below). This doc supplies a smaller but fully reproducible, tracked
replacement measurement, taken fresh rather than trusted from memory.

## Method

- Universe: `data/validate/liveness/trakstar.csv` (tracked in git), filtered to `status == "live"`,
  `jobs > 0`, from the most recent liveness batch (`checked_at == "2026-08-14"`, 611 candidates).
- Sample: 80 tenants, `random.sample(candidates, 80)` with `random.seed(20260826)` — reproducible
  from the same ledger snapshot.
- For each: fetched `https://{slug}.hire.trakstar.com/` live, then ran the scraper's own
  `_codes_from`, `_total_openings`, `_is_capped` (imported directly from
  `src/headstart/scrapers/trakstar.py`, not reimplemented) against the real HTML. For every board
  `_is_capped` flagged, also fetched `https://{slug}.hire.trakstar.com/jobfeeds/{slug}` live to
  check RSS reachability.

## Results (N=80)

- **80/80 careers pages fetched successfully** (no errors, no non-200s in this sample).
- **The "View N Openings" total button was present on 80/80 (100%)** of the `jobs > 0` sample.
  A second, separate sample of 15 boards with `jobs == 0` in the same ledger batch confirmed the
  other half of the claim directly: the button was **absent (``_total_openings`` returned
  ``None``) on 15/15** — it really does stop rendering when a Board has nothing to show, not just
  read as zero.
- **5/80 (6.25%) landed capped** (rendered exactly 25 cards, with the page's own total reading
  higher): `turnkeyconsulting` (25 rendered / 36 real), `colcare` (25/64), `iqraeducation`
  (25/148), `sleekr` (25/78), `exotel` (25/47).
- Across the sample, the cap hides **248 of 634 real jobs (39.1% of this sample's true
  corpus)** — the sample is small and dominated by a few large boards (`iqraeducation` alone
  accounts for 123 of the 248 hidden jobs), so this percentage carries a wide error bar, but it
  confirms the cap is not a rounding-error-sized problem.
- Of the 5 capped boards, the RSS feed was **reachable on 3 (`turnkeyconsulting`, `colcare`,
  `exotel`) and unreachable — a 404 — on 2 (`iqraeducation`, `sleekr`)**: 40% unreachable in this
  small capped sub-sample. This is the same direction as the original claim (the feed is not
  universal) but a much smaller N (2 of 5) than the original "3.9% of tenants," so treat this as
  confirming the *existence* of the unreachable case, not as a precise replacement rate.

## Named examples re-verified live 2026-08-26

The module docstring's illustrative board names were re-checked directly against the live site
today (one day after the PR's original 2026-08-25 verification date; unchanged):

| slug | cards | total | capped |
|---|---|---|---|
| `interglobalhomes` | 25 | 25 | no |
| `2workonline1` | 25 | 25 | no |
| `dataentrydirect` | 25 | 25 | no |
| `sleekr` | 25 | 78 | yes (feed 404s) |
| `colcare` | 25 | 64 | yes (feed reachable) |
| `hazelhawkins` | 25 | 53 | yes |
| `turnkeyconsulting` | 25 | 36 | yes (feed reachable) |
| `sajenaturalwellnessretail` | 25 | 92 | yes |

## `_feed_location`'s duplicate city==state claim

`_feed_location`'s own docstring claims a state that only repeats the city (e.g. `"Hamburg,
Hamburg, Deutschland"`) is `"confirmed on ~6% of records"`. That number has the same
gitignored-source problem as the cap statistics above, so it was checked directly rather than
carried forward:

- **The underlying pattern is real** — confirmed live 2026-08-25 and re-confirmed live
  2026-08-26 on `anduin`'s feed, which currently serves `<job:locationCity>Ho Chi Minh
  City</job:locationCity><job:locationState>Ho Chi Minh City</job:locationState>` verbatim, and
  `_feed_location` correctly collapses it to `"Ho Chi Minh City, Vietnam"`.
- **The "~6%" rate does not reproduce.** A fresh, separate live sample — 40 boards, `jobs > 0`,
  `random.seed(20260826)` against the full (not date-filtered) `data/validate/liveness/
trakstar.csv` — fetched 36 reachable feeds totaling 267 items, of which 67 had both `city` and
  `state` populated. **0 of those 67 (0.0%) had a city==state duplicate.** This doesn't disprove
  the phenomenon (`anduin` above is live proof it happens), but a 6%-scale rate should have
  produced roughly 4 hits in 67 and produced zero — the true rate is likely much lower than 6%,
  or concentrated in specific tenants/geographies this sample didn't happen to hit. Either way,
  "~6%" is not a number this repo can currently stand behind.
- The docstring has been softened to describe the pattern as real and handled, without repeating
  the unreproduced rate.

## Relationship to the 2026-08-25 field audit

`docs/location-audit/2026-08-25_ats-field-audit.md` (tracked) independently backs one headline
figure — **"48.7% of all Jobs hidden"** — from a much larger sample (906 boards per the original,
now-gitignored writeup) than this doc's 80. This smaller, reproducible sample's 39.1%
sample-hidden-jobs figure and 6.25% board-capped rate are the same order of magnitude and same
direction, which is the most this doc can honestly claim: it corroborates the shape of the larger
finding without re-deriving its exact figures. Nothing in the trakstar.py docstring should cite a
number larger than what this doc or the 2026-08-25 audit actually measured.
