# ADR-0089: The description store holds text, not verdicts — drop `detail_fetched`

**Status:** accepted · **Date:** 2026-08-26 · **Relates to:**
[ADR-0050](0050-persist-descriptions-across-runs.md) (the store this narrows, and the three-state
design it introduced), [ADR-0048](0048-skip-details-we-already-hold.md) (the skip-list the removed
state fed), [ADR-0062](0062-drain-the-description-gap.md) (the queue that still fires on
arriving text), [ADR-0088](0088-a-lost-detail-is-not-a-truncation.md) (the same week's finding that
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

Two facts, both measured 2026-08-26, say that row does not earn its complexity.

**The category is empty in practice.** Across **713 live Jobs** sampled from **28 boards on 12
ATSes** (rippling, smartrecruiters, ripplehire, ashby, lever, recruitee, teamtailor, workable,
successfactors ×4 tenants), **zero** carried an empty description. The store agrees: of **328,930**
entries accumulated over its lifetime, **7** are `null` — 0.002%, all eightfold.

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
(0 of 713) that is noise. If it ever stops being noise, the gap ledger is where it will show:
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
