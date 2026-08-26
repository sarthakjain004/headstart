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
category was empty, on a 713-Job sample. Re-measuring wider refuted that: the category exists, and
finding it changes the argument for removal rather than weakening it.

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

- **9 are the middle row** — the detail answered, and the posting's *own public page* carries an
  empty description. Verified on the page's JSON-LD (`"description": ""` on
  `cbts.eightfold.ai/careers/job/1443152815632` and `trinet.eightfold.ai/careers/job/44020930`) and
  on SuccessFactors' `itemprop="description"` span, which on Hyundai Motor Europe's
  `1340431355` contains literally `<div></div>`. Boards: cbts 2, trinet 1, telekom-growthhub 5,
  jobs.hyundai-europe.com 1 — the telekom-growthhub count re-probed independently on 2026-08-26
  and reproduced exactly (219 Jobs, 5 empty). **Only 1 of the 9 is a tech role**, and only tech
  Jobs reach `data/jobs/tech/` and therefore the store — which is exactly why the store accumulated
  **7** `null` entries in its lifetime and not 700. Read "tech role" strictly as
  `tech_filter.is_tech()`, the ADR-0017 gate that decides what reaches the store, **not** as a
  human reading of the title: telekom-growthhub's 5 are all German-titled engineering posts
  (`Senior Softwareentwickler`, `Senior Datenbank-Entwickler für Oracle`, `IT-Projektleiter`, …)
  that a person would call tech and the filter scores 1 of 5. That is a pre-existing recall gap on
  German titles, out of scope here — but it means this ratio describes the *corpus*, which is the
  right frame, since only what passes the gate can ever become a `null` entry.
- **322 are failed fetches.** `kraftheinz.eightfold.ai`, 198 of 797: instrumented at
  `_description`, every one returned `None` (non-200), not `""` — so `detail_fetched` would have
  been `False` and the removed state would not have fired on any of them, and the job pages
  themselves carry a full JSON-LD description, so settling them would have been a lie.
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
own lifetime `null` count, and the 1-tech-in-9 rate above says it is the right order. Against that,
the same measurement prices the cost of keeping it. Of the **seven** scrapable detail-pass scrapers
that never set the flag, **four cannot reliably tell an empty answer from a failure** — and the
count is read off their code, not off the sample:

- **Zoho** serves failed details as HTTP 200 error bodies. Re-verified independently on 2026-08-26:
  5 of 5 sampled failing ids on `techrecruitment.zohorecruit.com` returned the same 2,182-byte
  "sorry" page under 200, while 6 of 6 sampled succeeding ids returned a real ~1.7 MB record.
- **Ripplehire** collapses the two cases in one expression —
  `[(d or {}).get("jobDesc") or None for d in details]` (`ripplehire.py:135`): a detail that failed
  and a detail that answered with no `jobDesc` both arrive as `None`.
- **Trakstar**'s job page is a JS shell carrying no description marker at all.
- **Successfactors**' detail is an HTML *parse*, so an unparseable page and an empty one look
  identical — which is why setting the flag there was attempted and reverted (see below).

The other three (**workday**, **smartrecruiters**, **rippling**) read JSON APIs where an absent
field is unambiguous. Eightfold, the one scraper that *does* set the flag, is not in either group:
its documented failure mode is a non-200 that yields `None` rather than `""`, which the flag
handles correctly — but the measurement could not classify elcompanies' 65 empties either way, and
that board moved between 65 and 6 depending on which egress it was riding. A flag that means "the
detail answered" is only as trustworthy as the scraper's ability to tell answering from failing —
and where it is wrong it writes a *permanent* falsehood, suppressing the very signal that a
description is missing. That asymmetry, not an empty category, is what decides this: seven fetches
a run is less than one silently mis-settled Job.

**What this does not establish.** 95 boards is a sample, not a census: the rate is bounded within
an order of magnitude, not pinned. Three things could move it and none is measured here — the two
detail-pass ATSes not probed at all, the 68 unclassified, and the board draw itself, which was
random and unseeded, so the per-ATS counts above are **not** reproducible run-for-run and no
committed artifact backs them. What is independently checkable is the mechanism, and that is what
the argument rests on: the two JSON-LD pages and the zoho re-probe cited above were all re-verified
from scratch. What the sample *does* pin is the shape: the middle row is rare, mostly outside the
tech gate, and every large block of empties turned out to be a failure rather than an answer.

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
the only Jobs the removed state was skipping, on the only scraper that skips — measured above at
roughly 1 tech posting in 6,249, so single digits per full sweep, and only when their boards are in
slice. This is the price of the removal, and it is paid every run rather than once.

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
