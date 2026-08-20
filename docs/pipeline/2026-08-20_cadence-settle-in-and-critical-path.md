# Back-to-back cadence settling in, and a sharper cause for the WARP residual

**Date:** 2026-08-20 · **Verdict:** the back-to-back cadence is behaving as ADR-0071 predicted
(clean instant hand-offs, no overlap) and running slightly faster than its own projection — n=3,
not yet confirmed. The critical-path-board finding extends to 10 straight runs. The WARP
degradation residual has a sharper cause than "occasional apt/mirror contention": every sampled
degraded shard shows the `stub_gui_deps` step landing at or near its own 30-second ceiling.

Method: `analyse-fanout-run`, steps 0-5, using `scripts/runlog/` plus direct job-timestamp
inspection via `gh api .../jobs`. Continues `docs/pipeline/2026-08-20_warp-fix-validation.md` and
`docs/adr/0071-back-to-back-runs-instead-of-a-fixed-cadence.md` — read those first; this does not
re-derive what they already established.

## The runs

| run | sha | cadence | scrape-plan start → merge end | wall |
| --- | --- | --- | --- | --- |
| 32294914542 | 22332f1 | old 2h cron | 19:46:56 → 20:55:20 | 68.4m |
| 32305680181 | 22332f1 | old 2h cron | 21:47:43 → 23:01:19 | 73.6m |
| 32314496616 | 22332f1 | old 2h cron | 23:44:12 → 00:37:55 | 53.7m |
| 32325077157 | 22332f1 | old 2h cron | 02:33:48 → 03:30:12 | 56.4m |
| 32334230386 | ae20ef7 | **new, transition** | 05:04:49 → 06:11:41 | 66.9m |
| 32337598985 | ae20ef7 | **new, clean** | 06:11:45 → 07:20:33 | 68.8m |
| 32343784119 | ae20ef7 | **new, clean** | 07:25:58 → 08:14:27 | 48.5m |
| 32347139696 | ae20ef7 | **new, clean** | 08:14:30 → in progress | — |

`eabe1c8` (#200, ADR-0071's cadence change) landed 2026-08-20T04:01:23Z — confirmed by
`git merge-base --is-ancestor`, not inferred from position in `git log`. The four `22332f1` runs
predate it and are the old 2-hourly cadence; the four `ae20ef7` runs postdate it.

**A run's `createdAt`/`startedAt` fields are the cron *fire* time, not execution start** — for a
queued run they can sit tens of minutes before the first job actually runs. `scrape-plan`'s real
`started_at` is the number that means something; the table above uses that, not `gh run list`'s
convenience fields, which is what produced the handoff's coarser "69.4 min mean gap" figure.

## 1. Back-to-back is real, measured at the job level, not just the run level

Idle time from one run's `merge` completion to the next run's `scrape-plan` start:

| gap | duration | reading |
| --- | --- | --- |
| 32305680181 → 32314496616 | 42.9m | old cadence, idle between cron fires |
| 32314496616 → 32325077157 | 115.9m | old cadence, idle between cron fires |
| 32325077157 → 32334230386 | 94.6m | **transition** — predecessor still timed by the old cron |
| 32334230386 → 32337598985 | **0.07m** (4s) | new cadence, clean |
| 32337598985 → 32343784119 | 5.4m | new cadence — predecessor finished between hourly fires, see below |
| 32343784119 → 32347139696 | 0.05m (3s) | new cadence, clean |

Two of three clean-cadence gaps are 3-4 seconds — the successor was already queued and started the
instant `merge` finished, exactly the mechanism ADR-0071 describes. The 5.4m gap is not a
regression: `32337598985` finished at 07:20:33, ahead of the next hourly `:30` fire, so there was
briefly nothing queued; the next fire plus runner spin-up cost the 5.4 minutes. This is the
expected shape when a run finishes faster than the hour it started in — the queue only has
something waiting when the *previous* fire already landed mid-run.

## 2. Runs are landing faster than the cadence's own baseline — early, not confirmed

New-cadence wall-clock: 66.9m, 68.8m, 48.5m — mean **61.4m**, against the **74.1m** historical mean
ADR-0071 built its ~19.4 runs/day ceiling on. Start-to-start under the new cadence (66.9m, 74.2m,
48.6m — mean **63.2m**) projects to **~22.8 runs/day**, above the ADR's projection.

**Treat this as encouraging, not confirmed.** n=3. The 48.5m run in particular is fast partly
because it drew a favorable slice — no shard degraded to DIRECT that run (see §4), and none of its
board floors ran as long as `walmart`'s did in the run before it. One more day of runs, including
the compulsory 21:30 nightly, would make this a real reading instead of three data points that
happen to point the same way.

## 3. Critical-path boards: 7 consecutive runs becomes 10

| run | wall | owner | share | worst floor | board |
| --- | --- | --- | --- | --- | --- |
| 32334230386 | 66.9m | scrape 35.0m | 52% | **94%** | `successfactors:careers.ey.com` |
| 32337598985 | 68.8m | scrape 39.8m | 58% | **94%** | `workday:walmart.wd504` (see §4 — this shard also degraded to DIRECT) |
| 32343784119 | 48.5m | scrape 25.0m | 51% | **93%** | `successfactors:careers.hcltech.com` (92% `hcltech.jobs.hr.cloud.sap` in the same run, different shard) |

Same three named companies, no exceptions, extending the WARP doc's 7-run streak to 10.

**Correction, checked directly against both hosts after this doc first shipped:**
`hcltech.jobs.hr.cloud.sap` is not a second floor-bound board — it's a plain Apache 301 redirect to
`careers.hcltech.com` (`hcltech.jobs.hr.cloud.sap/sitemap.xml` → `careers.hcltech.com/xml/sitemap.xml`,
same for the site root). Same site, same jobs, reached through a vanity hostname the liveness
ledger independently tracks as a "live" tenant. So `32343784119` showing both in its three slowest
shards simultaneously isn't two boards costing critical-path time, it's **one board scraped twice
per run**, at full cost each time — and since the scraper keys job ids off `self.slug`
(`src/headstart/scrapers/successfactors.py:314`), every posting lands in the index twice under two
different ids with no dedup between them. Dropping the alias tenant from the liveness ledger is a
free win: no scraper change, no recall cost, pure duplicate-work removal. Filed as
[#212](https://github.com/sarthakjain004/headstart/issues/212) rather than fixed directly — it
touches discovery/liveness data, not scraper code, and deserves its own change.

**New this session — the two open items connect.** `32337598985`'s worst shard (walmart, a/p
**3.40**, the single worst ratio across every shard examined in this doc or the WARP validation
doc) is not just floor-bound; it is the same shard that lost WARP and fell back to direct (§4).
Predicted 11.0m, actual 39.8m. When a critical-path board's shard also loses its proxy mid-run, the
two costs compound rather than sitting side by side — worth knowing when scoping the eventual
per-board timeout, since the fix should not assume every slow walmart shard is slow for the same
reason.

## 4. The WARP residual has a sharper cause than "occasional contention"

One of the three new-cadence runs degraded: `32337598985` shard 7, 1/15 — consistent with the
validation doc's 4.0% POST rate (now 4/90 across all runs checked to date). But reading the actual
`equivs`-stub timestamps on every degraded shard sampled so far — the 3 from the validation doc's
POST arm plus this session's new one — turns up a pattern the doc didn't have the granularity to
see:

| run | shard | `apt-get install equivs` duration | what happened next |
| --- | --- | --- | --- |
| 32294914542 | 13 | **30.01s** (hit the ceiling) — apt sat silent the whole window, killed before dpkg ever started (pure download/acquire stall) | both `cloudflare-warp` attempts ran their full 60s timeout, no error text — genuine network-bound failure |
| 32305680181 | 8 | **30.01s** (hit the ceiling), same silent-stall shape as 13 | same — both attempts ran the full 60s |
| 32305680181 | 11 | **30.01s** (hit the ceiling) — reached dpkg this time (packages fetched fast), then killed after `man-db`/`install-info` trigger processing, right at the wall | both attempts ran the full 60s — dpkg's status was left consistent this time, so the retries at least got a real network attempt |
| 32337598985 | 7 | **30.01s** (hit the ceiling) — killed **mid `libc-bin` trigger processing**, earlier in the same trigger sequence than shard 11 got to | dpkg left "interrupted" — both `cloudflare-warp` attempts failed in **8ms and 0.8ms** with `E: dpkg was interrupted, you must manually run 'sudo dpkg --configure -a'` — no network attempt happened at all |

`stub_gui_deps()` wraps only the `apt-get install equivs` call in `timeout 30`
(`.github/workflows/pipeline.yml:246`); the two `equivs-build` calls and `dpkg -i` after it are
unwrapped, and never ran in any of these four — the outer timeout fired inside the `apt-get`
call itself every time. `equivs` is not a lightweight package — it pulls `autopoint`, `gettext`,
`libarchive-zip-perl`, `libdebhelper-perl`, `dh-strip-nondeterminism`, `debhelper`, and
`libmail-sendmail-perl` — and **4 of 4 sampled instances land within 2 seconds of the 30s
ceiling** (three of them within 10ms of it), which reads as a systematically tight budget, not a
random one. Whether that kill lands before dpkg starts (13, 8 — harmless), after dpkg finishes its
own transaction but mid-trigger (11 — harmless), or mid-transaction (7 — corrupts dpkg for the rest
of the job) looks like it comes down to exactly where in that ~30s the runner's disk/mirror
happened to be slow that invocation, not something the timeout value alone controls. Widening the
cap mainly buys the process room to reach a safe kill point more often; it doesn't make every kill
safe.

This is a **new, sharper lead** for the WARP doc's open item 2 ("occasional 100-141s install
tail... a real lead but n=5 and no issue filed"). It reframes the question from *why is the
network slow sometimes* to *why does the equivs chain run this close to a 30s ceiling, and what
happens when it loses*. n=4 sampled shards — every degraded shard found across the runs this doc
and the validation doc examined, not a subsample — so it's a small but complete census, not a
cherry-pick.

**Two candidate fixes were on the table: widen the cap, or trim `equivs`'s dependency chain with
`--no-install-recommends`.** Only the first is real — checked against `packages.ubuntu.com/noble`:
`equivs` → `debhelper` is a hard `Depends`, and `debhelper`'s own pull of `man-db`,
`dh-strip-nondeterminism`, `libdebhelper-perl`, and `po-debconf` are hard `Depends` too. Only
`po-debconf` → `libmail-sendmail-perl` is a `Recommends`. `--no-install-recommends` would shave one
small package off a chain of a dozen-plus and leave the part that actually burns the 30s (the
database read and trigger processing measured above) untouched, so it was dropped rather than
tried. **Fixed directly in [#211](https://github.com/sarthakjain004/headstart/pull/211)** — cap
raised to 60s, plus a `dpkg --configure -a` self-heal after a failed stub, since widening the cap
reduces how often shard 7's corruption happens but does not make it impossible on a genuinely
slower day. Shipped as code rather than filed as an issue, since the fix was small, well-evidenced,
and asked for in the same session that found it.

## 5. Workday and corpus — unchanged, confirms the earlier read

Workday scraped-row counts: 552,626 / 571,277 / 538,375 (mean ≈ 554k) across the three new runs —
continues the post-WARP-fix plateau the validation doc measured (≈553k), still short of the
632,578-672,050 pre-collapse band. Corpus totals hold steady (1.29-1.31M scraped, 20.2-20.4% tech).
Nothing new here; this just extends the existing read by three more runs and confirms it wasn't a
one-off.

No shard hit its time budget in any of the three new runs. Error rates (2.2-23.9 err/1k boards)
are unremarkable except shard 7 of `32337598985`, which carries 21% of that run's errors — the
same shard that lost WARP, consistent rather than new.

## 6. Six retail-dominated Workday boards, narrowed at the source

Widened the run survey to 20 recent pipeline runs and tallied every Workday board that hit ≥60%
floor on some shard, not just each run's single worst. Ranked by consistency and severity, six
stood out as genuinely heavy — not a one-off slow day, a board that dominates a shard essentially
every time it's scraped:

| board | occurrences (of 20 runs) | max floor | max wall |
| --- | --- | --- | --- |
| walmart (`WalmartExternal`) | 9 | 98% | 49.5m |
| cvshealth (`CVS_Health_Careers`) | 7 | 98% | 48.4m |
| target (`targetcareers`) | 9 | 97% | 35.8m |
| tjx (`TJX_EXTERNAL`) | 9 | 95% | 28.0m |
| lowes (`LWS_External_CS`) | 1 (severe on its one appearance) | 93% | 33.5m |
| loblaw (`myview.wd3/paradox_careers`) | 8 | 95% | 32.4m |

All six share Walmart's shape: a huge retail/operations board where the tech-labeled category is a
sliver. Live-checked against each board's own API, 2026-08-20:

| board | total postings | tech category | tech count | % of board |
| --- | --- | --- | --- | --- |
| walmart | 19,272 | Technology | 869 | 4.5% |
| cvshealth | 19,283 | Technology | 187 | 1.0% |
| target | ~11,900 | Technology | 118 | 1.0% |
| tjx | 10,349 | **Information Technology** (no "Technology" label exists) | 63 | 0.6% |
| loblaw | 12,438 | Technology + Digital & Ecommerce | 25 + 9 | 0.3% |
| lowes | 12,029 | Technology + Digital + IND_Digital | 42 + 27 + 4 | 0.6% |

"tech count" is the category alone, before intersecting with Full time — the code's inline comments
show the smaller final combined-query total each board actually gets scraped down to (e.g. walmart
869 category -> 823 once Full time is applied too), which is what to check against a live run's
board size, not this table.

Two boards don't use a single clean "Technology" bucket: TJX's own taxonomy calls it "Information
Technology" instead, and Lowe's/Loblaw split tech-adjacent roles across more than one small bucket.
Which extra buckets to fold in (e.g. Lowe's "Digital"/"IND_Digital" alongside "Technology") is a
judgment call, not something inferrable from the facet name alone — decided explicitly per board,
not by a blanket rule. Target's board also carries a `timeType=Variable` majority (11,089 of
~11,900) used for retail scheduling; checked specifically, all 118 of its Technology postings are
independently Full time, so the filter combination costs nothing extra there.

**Same ADR-0017 tradeoff as §3's Walmart entry, accepted explicitly per board**: postings outside
the listed categories, and every Part-time posting, are never fetched, so a real tech role filed
under an odd department at any of these six companies is now invisible to HeadStart, not merely
deprioritized — the post-hoc filter never gets a chance to see what the scrape never fetched.
Implemented in `src/headstart/scrapers/workday.py`'s `_FIXED_FACETS_BY_SLUG`, same mechanism as
Walmart, each board's exact facet ids and the specific postings given up documented inline.

## What to do next

1. ~~File an issue for the equivs-ceiling finding~~ — done directly instead, see §4:
   [#211](https://github.com/sarthakjain004/headstart/pull/211) raises the cap and adds a dpkg
   self-heal. Watch the next several scheduled runs (`analyse-fanout-run`, same as the WARP fix's
   own validation) to confirm shard-7-style corruption actually drops, not just that the cap stops
   firing as often.
2. **Don't revise ADR-0071's ~19.4 runs/day projection yet.** The ~22.8/day reading in §2 is real
   but n=3; let the nightly-plus-a-few-more-cycles data in before treating it as the new number.
3. ~~The three critical-path boards are the single highest-value target~~ — six Workday boards
   narrowed at the source instead of timed out (§6); EY is SuccessFactors and still open, and
   HCLTech's second "board" turned out to be a duplicate, not a second target (§3, #212). Watch the
   next several runs for whether the six narrowed boards actually drop off the critical path, or
   whether EY (and whatever board now surfaces once these six stop dominating shards) becomes the
   next target.
4. Workday (#194/#195) and quarantine counts are unchanged from prior reads — nothing actionable
   surfaced here beyond confirming the plateau held.
