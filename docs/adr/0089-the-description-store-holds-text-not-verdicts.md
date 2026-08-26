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

**Nobody has found a Job in the middle row.** Two live samples, taken independently, and the store
itself:

| sample | boards | Jobs | empty `description` | traced to the middle row |
| --- | --- | --- | --- | --- |
| first pass — rippling, smartrecruiters, ripplehire, ashby, lever, recruitee, teamtailor, workable, successfactors (4 tenants) | 28 | 713 | 0 | 0 |
| second pass — zoho, workday, trakstar, eightfold | 35 | 2,730 | 323 | 0 |
| the store itself, lifetime | — | 328,930 entries | — | 7 `null`, all eightfold (0.002%) |

The first pass is the weaker of the two and should not be read alone: **five of its nine ATSes
(ashby, lever, recruitee, teamtailor, workable) have no detail pass at all**, so they cannot
exhibit "the detail answered with none" by construction, and it omitted every ATS known to produce
empties. The second pass was taken to cover exactly that gap — the four detail-pass ATSes the
first missed — and it found empties in quantity. Every one traces to a *failed fetch*:

- **kraftheinz.eightfold.ai, 198 of 797.** Instrumented at `_description`, all 198 returned
  `None` (non-200), not `""`. On the one scraper that sets the flag, `detail_fetched` would have
  been `False` for all 198 and the removed state would not have fired for a single one. The job
  pages themselves carry a full JSON-LD `description`, so settling them would have been a lie.
- **techrecruitment.zohorecruit.com, 123 of 131.** 4 of 4 sampled job pages serve Zoho's
  2,182-byte "sorry" error page under **HTTP 200** — the exact failure-as-success this ADR's
  rejected alternative names for zoho.
- **kone/careers (workday), 1 of 923.** Its listing row is `{"bulletFields": ["R0663872"]}` — no
  `externalPath`, so no detail was ever attempted.

A fourth line of evidence, same date, arrived independently:
[`docs/personio/2026-08-26_descriptions-are-language-scoped.md`](../personio/2026-08-26_descriptions-are-language-scoped.md)
found **191 empty positions of 2,029** on personio and traced every one to a description that
exists — 187 recoverable from another language feed, the remaining 4 present in the job page's
JSON-LD. Its own conclusion is this ADR's: *"Settling these Jobs as 'has none' would have recorded
a falsehood — the descriptions exist."*

So the honest claim is not "the category is empty" but: **3,443 live Jobs across 63 boards and 13
ATSes produced 323 empty descriptions and not one of them was a posting that genuinely has none.**
Every empty was a fetch that failed — which is the same thing the store's own 7-in-328,930 says,
from the other direction. Both live passes ran the real scrapers via
`registry.get_scraper(ats, slug, slug).fetch()` over boards drawn at random from
`data/validate/liveness/{ats}.csv`; the second's per-board results and the `_description`
instrumentation are reproducible the same way.

**What this does not establish.** Absence of evidence at this sample size is not proof the
population is zero — a posting with genuinely no description may exist and simply not have been
drawn. The claim being relied on is narrower and is enough: the *removed state never fired on it*.
Every empty found, on the one scraper that implemented the flag, would have had
`detail_fetched == False`.

**Only one scraper ever set the flag.** Nine declare `has_detail_pass`; `detail_fetched` is set by
eightfold alone. That is not the accident it looks like: `needs_detail` — the skip-list's only
consumer — is also called by eightfold alone (`eightfold.py:281`). So for the other eight the flag
was doubly inert, and the "re-fetched forever" cost it was meant to prevent never existed for them:
they re-fetch every detail every run regardless, settled or not.

Together these resolve what the per-run counters were really reporting. `update_descriptions` logs
~1,974 Jobs per run with no description and no stored answer. Those are not postings that lack a
description — they are **fetch failures**: NGC's 3,536 missing details (ADR-0088), successfactors'
unreadable pages, zoho's. Every one is correctly *not* settled. The flag exists to separate a
population from an empty set.

## Decision

**The store maps `id -> text`. Membership means exactly one thing: we hold this Job's description.**

- `Job.detail_fetched` is removed from the model and from eightfold's three sites.
- `reconcile` collapses to two outcomes: fill from the store, or count the Job unrecorded. The
  `settled` counter and `Reconciled.settled` go with it.
- `read_store` and `_ats_held_ids` **skip an entry with no text**, so a legacy `null` reads as
  unheld everywhere. This is what keeps the removal coherent: the skip-list is what tells the
  scrape not to bother, and an id on it the store cannot supply is a description lost for good.
- `settled_ids` is renamed `held_ids` (and `_ats_settled_ids` to `_ats_held_ids`). "Settled" was
  the verdict vocabulary; "held" is what `base.have_details` and `reconcile`'s own local already
  called it.

No migration step: `compact` rewrites each base file from `read_store`, so the 7 legacy entries
disappear on its next pass.

## Consequences

**Eightfold re-fetches those 7 Jobs.** They are the only Jobs the removed state was skipping, on
the only scraper that skips. Seven detail fetches per run, when their boards are in slice.

**A genuinely description-less posting is now chased forever** — it stays in the ADR-0062 gap
ledger, so `scrape_plan` keeps reserving exploration budget for its Board. At the measured rate
(none in 3,443 live Jobs) that is noise. If it ever stops being noise, the gap ledger is where it
will show:
a Board whose unsettled count never falls despite being scraped.

**The `unrecorded` counter now means one thing** — we do not have this description — rather than
"we do not have it and could not tell you why". Its magnitude is unchanged, because the settle
branch was firing ~0 times.

**A scraper that starts consulting `have_details` no longer inherits a hidden obligation.** The old
model required it to also set `detail_fetched` or silently re-fetch description-less postings
forever; the note in `models.py` said so, and the eight scrapers it applied to never did it. There
is nothing left to forget.

## Alternatives considered

**Set the flag on the eight scrapers that never did.** Attempted first, and abandoned on the
evidence. Three (workday, smartrecruiters, rippling) read JSON APIs where an absent field is
unambiguous and the change was a clean one-liner. Successfactors was reverted: its detail is an
HTML *parse*, so a page whose title parses while its description does not is a plausible parser
failure, and settling that would permanently record "has no description" for a Job that has one.
Zoho, ripplehire and trakstar have the same ambiguity — their `None` conflates a fetch failure with
an unparseable body. Worse, the whole exercise buys nothing while `needs_detail` has one caller.

**Keep the flag and make every scraper consult `have_details`.** That would give the skip-list real
teeth — the ~414,648 ids it publishes each run would start saving fetches on eight more ATSes. It
is a genuinely attractive change and it is *not* what this ADR forecloses: it can be made later,
and would then need its own decision about detail-derived fields (workday's `startDate` is the only
`posted_at` source, and `_posting_key` reads `jobReqId`, so skipping a detail renames postings —
measured 10/10 on `roche`). Bundling it here would have hidden a large behavioural change inside a
cleanup.
