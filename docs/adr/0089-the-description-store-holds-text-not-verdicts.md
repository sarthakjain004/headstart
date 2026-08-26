# ADR-0089: The description store holds text, not verdicts — drop `detail_fetched`

**Status:** accepted · **Date:** 2026-08-26 · **Amends:**
[ADR-0050](0050-persist-descriptions-across-runs.md) (the store this narrows, and the three-state
design it introduced), [ADR-0062](0062-drain-the-description-gap.md) (only text is queued for
re-derivation now) · **Relates to:**
[ADR-0048](0048-skip-details-we-already-hold.md) (the skip-list the removed
state fed), [ADR-0088](0088-a-lost-detail-is-not-a-truncation.md) (the same week's finding that
a lost detail is a fetch failure, not an absence)

## Context

ADR-0050 gave the description store three states, because "this Job has no description" has two
causes that look identical in the corpus — both are an empty string:

| store entry | meaning | skip the detail? |
| --- | --- | --- |
| text | we hold this description | yes |
| `null` | the detail answered; this posting genuinely has none | yes |
| absent | never settled | no — fetch again |

The second row existed so a genuinely description-less posting would stop being re-fetched every
run forever. It was gated on `Job.detail_fetched`, which the scraper set: `True` only when a
per-Job detail fetch *completed*.

Two findings, both measured 2026-08-26, say that row does not earn its complexity.

**The middle row is real, but it is ~7 tech Jobs wide.** An earlier draft of this ADR claimed the
category was empty, on a 713-Job sample. Re-measuring wider refuted that: the category exists — 4
verified Jobs in the pass below — and finding it changes the argument for removal rather than
weakening it. Its width in *tech* Jobs is pinned only by the store's own lifetime `null` count of
**7**; the sample found no tech Job in the category at all.

The wider pass ran the real scrapers — `registry.get_scraper(ats, slug, slug).fetch()` over boards
drawn at random from `data/validate/liveness/{ats}.csv` — across six of the eight scrapable
**detail-pass** ATSes (rippling and smartrecruiters were not probed):

| ATS | boards | Jobs | empty `description` |
| --- | --- | --- | --- |
| eightfold | 16 | 6,249 | 272 |
| workday | 19 | 2,419 | 1 |
| ripplehire | 14 | 2,335 | 0 |
| successfactors | 10 | 1,030 | 1 |
| zoho | 18 | 942 | 123 |
| trakstar | 18 | 86 | 2 |
| **total** | **95** | **13,061** | **399** |

Each of the 399 was then traced to a cause:

- **4 are the middle row** — the detail answered, and the posting's *own public page* carries an
  empty description. Boards: cbts 2, trinet 1, jobs.hyundai-europe.com 1. Verified on the page's
  JSON-LD (`"description": ""` on `cbts.eightfold.ai/careers/job/1443152815632`, a *Senior Project
  Manager*, and `trinet.eightfold.ai/careers/job/44020930`, a *Sales Development Representative*)
  and on SuccessFactors' `itemprop="description"` span, which on Hyundai Motor Europe's
  `1340431355` contains literally `<div></div>`.

  **This bullet read "9, of which 1 is tech" until a review re-probe on 2026-08-26 refuted it.**
  The 9 included `telekom-growthhub` 5 — the one board in the list the original pass never
  JSON-LD-verified. Re-probed: the scraper still returns exactly 5 empties of 219 Jobs, but **5 of
  5 of those Jobs carry a full description in their public JSON-LD** (HTTP 200, ~205 KB pages).
  The descriptions exist, so these are not the posting's own absence and they move to the bullet
  below. That also removes the
  only tech role in the group: the 5 are German-titled engineering posts and
  `tech_filter.is_tech()` — the ADR-0017 gate that decides what reaches `data/jobs/tech/` and
  therefore the store — scores exactly 1 of them tech, the `Senior DevOps Engineer`. So **0 of the
  4 verified middle-row Jobs are tech**, and this sample corroborates nothing about the store's
  lifetime `null` count; that count stands on its own observation.

  Note what the refutation *is*: five postings that a flag meaning "the detail answered" would have
  settled as description-less, on a board where the descriptions demonstrably exist. One of them
  passes the tech gate, so one of them would have reached the store and been wrong there
  permanently. This is the ADR's own argument, measured rather than reasoned.
- **327 the detail got wrong.** Two distinct mechanisms, and the difference is the whole ADR.
  `telekom-growthhub.eightfold.ai`, 5: the detail **answered** — HTTP 200, no `jobDescription` —
  for postings whose pages carry full text, so `detail_fetched` would have been `True` and the
  state *would* have fired, wrongly. `kraftheinz.eightfold.ai`, 198 of 797: instrumented at
  `_description`, every one returned `None` (non-200), not `""` — so `detail_fetched` would have
  been `False` and the state would *not* have fired; the job pages carry a full JSON-LD
  description, so settling them would have been a lie the flag happened to avoid.
  `techrecruitment.zohorecruit.com`, 123 of 131: 4 of 4 job pages sampled in this pass serve Zoho's
  2,182-byte "sorry" error page under **HTTP 200** (re-probed wider below). `kone/careers` (workday), 1 of 923: its listing row is
  `{"bulletFields": ["R0663872"]}` — no `externalPath`, so no detail was ever attempted.
- **68 are unclassified.** `elcompanies.eightfold.ai` 65 — unstable: a second pass over the same
  board returned 6 empties with the detail returning text for all 1,395 Jobs, and the board 405s
  and falls to the spare egress mid-crawl. `trakstar` 2 — the job page is a JS shell carrying no
  description marker at all. The remaining **1** is an eightfold empty that was never attributed
  to a board; it is carried here rather than dropped so the three causes sum to 399.

A third line of evidence arrived independently the same day:
[`docs/personio/2026-08-26_descriptions-are-language-scoped.md`](../personio/2026-08-26_descriptions-are-language-scoped.md)
found **191 empty positions of 2,029** on personio and traced every one to a description that
*exists* — 187 recoverable from another language feed, the last 4 present in the job page's
JSON-LD. Its own conclusion is this ADR's: *"Settling these Jobs as 'has none' would have recorded
a falsehood — the descriptions exist."*

**So the state fires, and what it buys is ~7 skipped detail fetches per run.** That is the store's
own lifetime `null` count — an observation, not an estimate from the sample, which found 0 tech
Jobs in the category. Against that,
the same measurement prices the cost of keeping it — and the price was measured on the one scraper
where the state was actually live.

**Eightfold's flag is wrong on live data, 5 of 5.** The telekom-growthhub Jobs above are not a
near-miss; they are the removed state firing incorrectly. Instrumented at `_description` on
2026-08-26, the `position_details` API answers **HTTP 200 with no `jobDescription` field** for all
five. `_description_of` maps that to `""` — *not* `None` — and `""` is precisely what set
`detail_fetched = True`. So under the three-state design all five would have been recorded as "this
posting genuinely has none", permanently, while their public pages serve full descriptions. One of
the five passes the tech gate, so one would have reached the store and been wrong there forever.

That is the whole argument, and it does not depend on the other scrapers at all. The flag's premise
is that a completed fetch means an authoritative answer. Eightfold's own API breaks that premise:
it completes, returns 200, and omits a field the posting has. No amount of care in the *scraper*
recovers this, because the scraper is told the truth about its request and a falsehood about the
posting.

The same shape appears elsewhere, which is why extending the flag was abandoned rather than fixed:

- **Zoho** serves failed details as HTTP 200 error bodies. Re-verified independently on 2026-08-26:
  5 of 5 sampled failing ids on `techrecruitment.zohorecruit.com` returned the same 2,182-byte
  "sorry" page under 200, while 6 of 6 sampled succeeding ids returned a real ~1.7 MB record.
- **Successfactors** and **trakstar** read an HTML *parse*, so a page that did not parse and a page
  with no description are the same observation — which is why setting the flag on successfactors
  was attempted and reverted (see below).

The JSON-API scrapers (**workday**, **smartrecruiters**, **rippling**, **ripplehire**) can all tell
a failed *request* from an answered one — each keeps the raw detail object and a
`detail is not None` test would be a clean one-liner. Eightfold is the proof that this is not the
hard part. A flag meaning "the detail answered" is only as trustworthy as the *origin's* willingness
to answer honestly, and where it is wrong it writes a *permanent* falsehood, suppressing the very
signal that a description is missing. That asymmetry, not an empty category, is what decides this:
seven skipped fetches a run is less than one silently mis-settled Job — and we have now watched five
of them nearly happen on one board.

**What this does not establish.** 95 boards is a sample, not a census: the rate is bounded within
an order of magnitude, not pinned. Four things could move it and none is measured here — the two
detail-pass ATSes not probed at all, the 68 unclassified, the board draw itself (random and
unseeded, so the per-ATS counts above are **not** reproducible run-for-run and no committed
artifact backs them), and the trace's own reliability: re-probing one of its four middle-row
boards moved 5 Jobs out of the category, so the remaining unverified classifications should be
read as provisional. What is independently checkable is the mechanism, and that is what
the argument rests on: the two JSON-LD pages and the zoho re-probe cited above were all re-verified
from scratch. What the sample *does* pin is the shape: the middle row is rare, every verified
member of it fell outside the tech gate, and every large block of empties turned out to be a
failure rather than an answer.

**Only one scraper ever set the flag.** Nine scraper classes declare `has_detail_pass` — eight
scrapable, since `join` is in `DISABLED_ATS` — and `detail_fetched` is set by eightfold alone. That
is not the accident it looks like: `needs_detail`, the skip-list's only consumer, is also called by
eightfold alone (`eightfold.py:281`). So for the other seven the flag was doubly inert, and the
"re-fetched forever" cost it was meant to prevent never existed for them: they re-fetch every
detail every run regardless, settled or not.

Together these resolve what the per-run counters were really reporting. `update_descriptions` logs
~1,974 Jobs per run with no description and no stored answer. The overwhelming majority are not
postings that lack a description — they are **fetch failures**: NGC's 3,536 missing details
(ADR-0088), successfactors' unreadable pages, zoho's. Every one is correctly *not* settled. The
flag existed to separate a real population from a much larger one it is easily confused with, and
it separated them on a signal four of the seven scrapers it applied to cannot produce reliably.

## Decision

**The store maps `id -> text`. Membership means exactly one thing: we hold this Job's description.**

- `Job.detail_fetched` is removed from the model and from eightfold's three sites.
- `reconcile` collapses to two outcomes: fill from the store, or count the Job unrecorded. The
  `settled` counter and `Reconciled.settled` go with it.
- `read_store` and `_ats_held_ids` walk one shared `_entries()` generator that **yields no text
  for an entry that has none**, so a legacy `null` reads as unheld everywhere and the two readers
  cannot drift apart. This is what keeps the removal coherent: the skip-list is what tells the
  scrape not to bother, and an id on it the store cannot supply is a description lost for good.
- `settled_ids` is renamed `held_ids` (and `_ats_settled_ids` to `_ats_held_ids`). "Settled" was
  the verdict vocabulary; "held" is what `base.have_details` and `reconcile`'s own local already
  called it.

No migration step: `compact` rewrites each base file from `read_store`, so the 7 legacy entries
disappear on its next pass. It runs daily in `cleanup-index`, and that step is `continue-on-error`
— so the purge is best-effort, and the entries are harmless until it lands because every reader
already treats them as unheld.

## Consequences

**Eightfold re-fetches those 7 Jobs, and every genuinely empty posting it finds after.** They are
the only Jobs the removed state was skipping, on the only scraper that skips. The 95-board sample
found no tech Job genuinely lacking a description at all, so the store's own lifetime count of 7 is
the only measurement of the population — single digits per full sweep, and only when their boards
are in slice. This is the price of the removal, and it is paid every run rather than once.

**A genuinely description-less posting is now chased forever** — it stays in the ADR-0062 gap
ledger, so `scrape_plan` keeps reserving exploration budget for its Board. The bound is the quota,
not the ordering: `GAP_FRAC` is 0.05 of the exploration tail, so the gap picks can never grow past
that however many never-fillable ids accumulate. The ordering is *not* a second bound — `_gap_picks`
sorts by *most* unsettled first within the detail-pass class, so a Board accreting these rises
rather than sinks. If the population ever stops being noise, that is where it shows: a Board whose
unsettled count never falls despite being scraped.

**The `unrecorded` counter now means one thing** — we do not have this description — rather than
"we do not have it and could not tell you why". Its magnitude moves by single digits, since that is
how often the settle branch was firing.

**A scraper that starts consulting `have_details` no longer inherits a hidden obligation.** The old
model required it to also set `detail_fetched` or silently re-fetch description-less postings
forever; the note in `models.py` said so, and the seven scrapers it applied to never did it. There
is nothing left to forget — and nothing left to get wrong on an ATS whose empty answer is really a
failure.

## Alternatives considered

**Set the flag on the seven scrapable scrapers that never did.** Attempted first, and abandoned on the
evidence. Three (workday, smartrecruiters, rippling) read JSON APIs where an absent field is
unambiguous and the change was a clean one-liner. Successfactors was reverted: its detail is an
HTML *parse*, so a page whose title parses while its description does not is a plausible parser
failure, and settling that would permanently record "has no description" for a Job that has one.
Zoho, ripplehire and trakstar have the same ambiguity — their `None` conflates a fetch failure with
an unparseable body. Worse, the whole exercise buys nothing while `needs_detail` has one caller.

**Keep the flag and make every scraper consult `have_details`.** That would give the skip-list real
teeth — the ~414,648 ids it publishes each run would start saving fetches on the seven other
scrapable detail-pass ATSes. It
is a genuinely attractive change and it is *not* what this ADR forecloses: it can be made later,
and would then need its own decision about detail-derived fields (workday's `startDate` is the only
`posted_at` source, and `_posting_key` reads `jobReqId`, so skipping a detail renames postings —
measured 10/10 on `roche`). Bundling it here would have hidden a large behavioural change inside a
cleanup.
