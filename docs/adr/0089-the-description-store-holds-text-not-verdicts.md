# ADR-0089: The description store holds text, not verdicts — drop `detail_fetched`

**Status:** accepted · **Date:** 2026-08-26 · **Amends:**
[ADR-0050](0050-persist-descriptions-across-runs.md) (the store this narrows, and the three-state
design it introduced), [ADR-0062](0062-drain-the-description-gap.md) (only text is queued for
re-derivation now) · **Relates to:**
[ADR-0048](0048-skip-details-we-already-hold.md) (the skip-list the removed
state fed), [ADR-0088](0088-a-lost-detail-is-not-a-truncation.md) (the same week's finding that
a lost detail is a fetch failure, not an absence)

> **Amended 2026-08-30 by [ADR-0097](0097-a-postings-id-comes-from-the-listing-never-the-detail.md).**
> The paragraph below names two blockers to extending `have_details` to Workday. One is gone:
> `_posting_key` no longer reads the detail's `jobReqId`, so skipping a detail no longer renames a
> posting. `startDate` remains the only `posted_at` source, so the change is still not a switch.

## Context

ADR-0050 gave the description store three states, because "this Job has no description" has two
causes that look identical in the corpus — both are an empty string:

| store entry | meaning | skip the detail? |
| --- | --- | --- |
| text | we hold this description | yes |
| `null` | the detail answered; this posting genuinely has none | yes |
| absent | never settled | no — fetch again |

The middle row existed so a genuinely description-less posting would stop being re-fetched every
run forever. It was gated on `Job.detail_fetched`, which the scraper set: `True` only when a
per-Job detail fetch *completed*.

**The premise is false, and the store already holds the falsehood to prove it.**

### The falsehood, and how it got there

`eightfold:telekom-growthhub.eightfold.ai:563465371804571` is recorded as *authoritatively has no
description*. It is a **Senior DevOps Engineer (m/w/d)** posting, and its own public page carries a
**5,677-character** JSON-LD description right now (verified 2026-08-26 — the page serves two
`JobPosting` blocks and this is the larger, which is why the repro below takes the max).

The mechanism is exact. `_description_of` returns `data.get("jobDescription") or ""` — so an API
200 whose payload omits `jobDescription` yields `""`, not `None`. `detail_fetched` was
`desc is not None`, so `"" is not None` is **`True`**: the detail "answered", the store settled the
Job, and it joined the skip-list. Eightfold then stopped fetching it — permanently, because nothing
re-opens a settled Job.

Reproduce it (the store is HF-backed and gitignored, so it must be pulled first — without the
fetch the first command prints an empty list and reads as a false negative):

```bash
python -m headstart.ingest.state_fetch 'data/descriptions/*'

# every null the store holds, and this ATS's own entry count
python -c "
import gzip, json, sys; sys.path.insert(0, 'src')
from pathlib import Path
from headstart.ingest.update_descriptions import _fragments
held = {}
for f in _fragments(Path('data/descriptions/eightfold')):
    for line in gzip.open(f, 'rt', encoding='utf-8'):
        if line.strip():
            r = json.loads(line); held[r['id']] = r.get('description')
print(len(held), 'eightfold entries;', [k for k, v in held.items() if v is None])"

# what the posting actually serves
curl -s https://telekom-growthhub.eightfold.ai/careers/job/563465371804571 \
  | python3 -c "import json,re,sys; b=[json.loads(m) for m in re.findall(r'application/ld\+json\">(.*?)</script>', sys.stdin.read(), re.S)]; print(max(len(x.get('description') or '') for x in b if x.get('@type')=='JobPosting'))"
```

### Every entry the state ever wrote, checked

The state's entire lifetime output is **8** `null` entries, all eightfold, out of **417,773**
entries across the whole store (measured 2026-08-26 — and the same 417,773 the current pipeline's
`skip-list: … Jobs held` line reports, because the skip-list is the store's key set; after this
change it publishes 8 fewer). Eight is small enough to check exhaustively, so it was: each
posting's public page fetched, its JSON-LD `JobPosting` read.

| store entry | its page serves | verdict |
| --- | --- | --- |
| `telekom-growthhub…:563465371804571` — Senior DevOps Engineer (m/w/d) | 5,677 chars | **wrong** |
| `infineon…:563808971791761` — Senior Engineer Verification | 2,790 chars | **wrong** |
| `infineon…:563808971860630` — Senior Engineer Reliability Product Testing | 2,865 chars | **wrong** |
| `infineon…:563808971852819` — Senior Engineer Test Engineering (f/m/div) | 2,896 chars | **wrong** |
| `infineon…:563808971806890` — Devops Architect | 2,923 chars | **wrong** |
| `infineon…:563808971860633` — Staff Engineer Engineering & Project | `description: ""` | **correct** |
| `twilio…:1099552052378` | no `JobPosting` JSON-LD | unverifiable |
| `infineon…:563808971794175` | no `JobPosting` JSON-LD | unverifiable |

**Five of the six checkable entries are wrong.** The sixth is right, and it is the only *tech*
posting ever confirmed to be in the category this state existed to record — the wider sample below
turned up three more that are still verifiable, none of them tech. The flag is not occasionally
unlucky: over its whole lifetime it wrote five falsehoods to buy that one true record.

It is not confined to postings already in the store. A full census of `telekom-growthhub` on
2026-08-26 — all 219 Jobs, every detail fetched, no request errors — found **5** whose
`position_details` answers **HTTP 200 with no `jobDescription`** while their public pages carry
4,281–5,677 characters of German text (measured on all five). All five would have been settled
"has none" permanently. **Exactly one of the five passes the tech gate**
(`tech_filter.is_tech(title, department)` — the ADR-0017 gate that decides what reaches
`data/jobs/tech/` and therefore the store), which is why one is in the store and the other four
never could be: `reconcile` only ever sees the tech corpus. That one is the first row above.

### Why no scraper can be trusted with the flag

The flag's premise is that a completed fetch means an authoritative answer. Eightfold's own API
breaks it: the request completes, returns 200, and omits a field the posting has. No amount of care
in the *scraper* recovers this, because the scraper is told the truth about its request and a
falsehood about the posting.

The same shape appears elsewhere, which is why extending the flag was abandoned rather than fixed:

- **Zoho** serves failed details as HTTP 200 error bodies. Re-verified 2026-08-26: 5 of 5 sampled
  failing ids on `techrecruitment.zohorecruit.com` returned the same 2,182-byte "sorry" page under
  200, while 6 of 6 sampled succeeding ids returned a real ~1.7 MB record.
- **Successfactors** and **trakstar** read an HTML *parse*, so a page that did not parse and a page
  with no description are the same observation — which is why setting the flag on successfactors
  was attempted and reverted (see below).

Where it is wrong the flag writes a *permanent* falsehood, suppressing the very signal that a
description is missing. That asymmetry is what decides this: eight skipped fetches a run is less
than one silently mis-settled Job — and the census above finds five.

### How rare the middle row actually is

Rare, but not empty — and the store's own census above is now the proof of both halves, since one
of its eight entries is a genuine case and five are not. An earlier draft of this ADR claimed the
category did not exist at all, on a 713-Job sample; re-measuring wider refuted that. Three of the
wider pass's cases were verified on live JSON-LD and still serve an empty description today:
`cbts.eightfold.ai/careers/job/1443152815632`, a *Senior Project Manager*;
`trinet.eightfold.ai/careers/job/44020930`, a *Sales Development Representative*; and, on
SuccessFactors' `itemprop="description"` span, Hyundai Motor Europe's `1340431355`, which contains
literally `<div></div>`. None of the three is a tech role.

**That wider pass's per-ATS counts are deliberately not reproduced here.** Its board draw was
random and unseeded, no committed artifact backs it, and it is not reproducible run-for-run — so
every restatement of it was a rate nobody could re-derive. That is not hypothetical: a claim this
sample refuted survived in five other files after the ADR itself had dropped it, and the numbers
above are quoted here instead precisely because anyone can re-run them. What the wider pass
established qualitatively stands and is evidenced elsewhere in this document: every *large* block
of empty descriptions traced to a failure rather than an answer, zoho's "sorry" pages under HTTP
200 being the clearest case.

A third line of evidence arrived independently the same day:
[`docs/personio/2026-08-26_descriptions-are-language-scoped.md`](../personio/2026-08-26_descriptions-are-language-scoped.md)
found **191 empty positions of 2,029** on personio and traced every one to a description that
*exists*. Its own conclusion is this ADR's: *"Settling these Jobs as 'has none' would have recorded
a falsehood — the descriptions exist."*

### Only one scraper ever set the flag

Nine scraper classes declare `has_detail_pass` — eight scrapable, since `join` is in
`DISABLED_ATS` — and `detail_fetched` is set by eightfold alone. That is not the accident it looks
like: `needs_detail`, the skip-list's only consumer, is also called by eightfold alone
(`eightfold.py:281`). So for the other seven the flag was doubly inert, and the "re-fetched
forever" cost it was meant to prevent never existed for them: they re-fetch every detail every run
regardless, settled or not.

That also resolves what the per-run counters were really reporting. `update_descriptions` logs
**1,974–2,165** Jobs per run with no description and no stored answer (five consecutive runs,
2026-08-26). They are not postings that lack a description — they are **fetch failures**, and the
per-ATS split names them: successfactors 844–872 every run, zoho 390–409, workday 111–615,
smartrecruiters 119–132, and personio anywhere from 1 to 343 (it swings run to run as its Boards
enter and leave the slice). Eightfold, the one scraper that ever set the flag,
contributes **0 or 1**. Workday's large detail losses (ADR-0088) mostly never reach this counter at
all — they are repaired from the store instead, 2,810–7,862 `filled` per run — which is the store
doing precisely the job it was built for. Every one of these is correctly *not* settled, and across
those five runs the settle branch fired **0** times.

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

No migration step: `compact` rewrites each base file from `read_store`, so the 8 legacy entries
disappear on its next pass. It runs daily in `cleanup-index`, and that step is `continue-on-error`
— so the purge is best-effort, and the entries are harmless until it lands because every reader
already treats them as unheld.

## Consequences

**Eightfold re-fetches those 8 Jobs, and every genuinely empty posting it finds after.** They are
the only Jobs the removed state was skipping, on the only scraper that skips: eight detail fetches
per full sweep, and only when their boards are in slice. That is the whole measured price, and it
is paid every run rather than once.

Five of the eight are confirmed wrong and two unverifiable; **one is a confirmed member**,
`infineon…:563808971860633`, whose page really does serve an empty description. So the population
those eight entries stood in for is somewhere between one and three Jobs, and what the state
bought over its whole lifetime was one true record against five false ones. It is that ratio, not
the count, the removal is priced against.

**A genuinely description-less posting is now chased forever** — it stays in the ADR-0062 gap
ledger, so `scrape_plan` keeps reserving exploration budget for its Board. The bound is the quota,
not the ordering: `GAP_FRAC` is 0.05 of the exploration tail, so the gap picks can never grow past
that however many never-fillable ids accumulate. The ordering is *not* a second bound — `_gap_picks`
sorts by *most* unsettled first within the detail-pass class, so a Board accreting these rises
rather than sinks. If the population ever stops being noise, that is where it shows: a Board whose
unsettled count never falls despite being scraped.

**The `unrecorded` counter now means one thing** — we do not have this description — rather than
"we do not have it and could not tell you why". Its magnitude does not move at all on today's data:
the settle branch logged `settled 0 as having none` in each of the five runs measured above.

**A scraper that starts consulting `have_details` no longer inherits a hidden obligation.** The old
model required it to also set `detail_fetched` or silently re-fetch description-less postings
forever; the note in `models.py` said so, and the seven scrapers it applied to never did it. There
is nothing left to forget — and nothing left to get wrong on an ATS whose empty answer is really a
failure.

## Alternatives considered

**Set the flag on the seven scrapable scrapers that never did.** Attempted first, and abandoned on
the evidence. Successfactors was reverted: its detail is an HTML *parse*, so a page whose title
parses while its description does not is a plausible parser failure, and settling that would
permanently record "has no description" for a Job that has one. Zoho and trakstar have the same
ambiguity, and ripplehire's `_job_detail` already returns `None` both for a failed request and for
an answered response carrying no `jobVO`, so it cannot express the distinction as written. Whatever
each scraper could be made to report, eightfold is the proof that the honesty required is the
*origin's*, not the scraper's — and the whole exercise buys nothing while `needs_detail` has one
caller.

**Keep the flag and make every scraper consult `have_details`.** That would give the skip-list real
teeth — the 417,773 ids it publishes each run (2026-08-26, growing 500–800 a run; keeping the
flag keeps the legacy nulls on the list too) would start saving fetches on the seven other
scrapable detail-pass ATSes. It is a genuinely attractive change
and it is *not* what this ADR forecloses: it can be made later, and would then need its own
decision about detail-derived fields (workday's `startDate` is the only
`posted_at` source, and `_posting_key` reads `jobReqId`, so skipping a detail renames postings —
measured 10/10 on `roche`). Bundling it here would have hidden a large behavioural change inside a
cleanup.
