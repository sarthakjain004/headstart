# ADR-0070: SmartRecruiters does not cap a board at 100 postings

**Status:** accepted · **Date:** 2026-08-20 · **Amends:**
[ADR-0015](0015-async-multiplexed-fan-out.md) (its premise that SmartRecruiters caps a board's
list at 100 postings) · **Relates to:**
[ADR-0053](0053-scope-eviction-on-scrape-outcome.md)

## Context

ADR-0015 sized the multiplexing window against a stated ceiling:

> SmartRecruiters caps its list at 100 postings/board, so per-board detail count — and thus the
> multiplexing window — is bounded

That is not what the API does. The `?limit=100` in `scrapers/smartrecruiters.py` is *our* page
size, not the provider's ceiling, and the response carries `offset`, `limit` and `totalFound`
alongside `content` — it advertises paging and reports the board's true size.

Measured live 2026-08-20, three boards, `GET
api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100[&offset=N]`:

| slug | `totalFound` | returned | page 0 ids | page 1 ids | overlap |
|---|---:|---:|---:|---:|---:|
| `dominos` | 24,556 | 100 | 100 | 100 | 0 |
| `crossmark1` | 21,199 | 100 | 100 | 100 | 0 |
| `groupementmousquetaires` | 9,812 | 100 | 100 | 100 | 0 |

`offset=100` returns a further 100 **distinct** ids on every board — zero overlap with page 0. The
rest of the board is reachable and we never ask for it.

The harm is not the missed coverage; it is that the miss is silent. ADR-0053 turns on exactly this
distinction:

> A scraper that returns a list it knows is short says so, and a Board whose list is not
> authoritative … is removed from the eviction scope entirely.

`fetch_raw` did one GET, no offset loop and no `mark_truncated`, so every posting behind the page
read to `index sync` as a **delisting** — 24,456 false delistings on `dominos` alone, every run,
followed by a re-add whenever the row came back. This is the flap ADR-0053 was written to stop,
in the one scraper that never reported it. `totalFound` was in the payload the whole time: the
knowledge existed and died at the parse.

Sample caveat: three boards, chosen because a prior mining pass flagged them as large. The
`offset` mechanism is uniform API behaviour, but the fleet-wide figure below is a ledger estimate,
not a measurement of every board.

## Decision

**The SmartRecruiters scraper marks itself truncated whenever `totalFound` exceeds the postings it
returned, and the 100-posting page stays.** Truncation marking and pagination are separable, and
only the first is unambiguously correct today.

Marking is pure correctness: it costs one comparison, recovers no jobs, and stops the eviction
scope from treating unread postings as delistings. Paginating is a **cost** decision, not a
correctness one — the detail pass is one request per posting, so reading `dominos` whole is 24,556
detail fetches for one board against a storage budget CLAUDE.md names as this workflow's binding
constraint. An estimated 807 live SmartRecruiters boards hold more than 100 postings, on the order
of 478,000 unread. Turning that on silently, inside a review, would be the same class of mistake as
the premise this ADR corrects: a large change justified by a number nobody re-measured.

Rejected: **paginate now, bounded by a page cap** (the darwinbox `_MAX_PAGES` shape). It is the
likely eventual answer and it fits the repo's existing pattern, but picking the cap *is* the cost
decision, and a cap chosen to feel safe would set the corpus's SmartRecruiters share by accident.
It is raised as its own issue with these numbers attached.

Rejected: **treat a full page as the truncation signal** (`len(content) == limit`), the darwinbox
heuristic. Unnecessary here — `totalFound` is exact, so a board holding exactly 100 postings is
correctly *not* marked, where the heuristic would falsely strip it from the eviction scope and
leave its real delistings unpruned forever.

## Consequences

SmartRecruiters boards larger than one page leave the eviction scope, so their stale rows now
persist rather than being pruned. That is the trade ADR-0053 already chose — a stale row is a
worse search result, a false delisting is a lost job — and it is bounded by the truncation flag
travelling to the per-board report, where it is visible rather than silent.

ADR-0015's measured speed conclusions stand: the ~14% matched-concurrency and ~38% width-100
numbers were measured, not derived from the ceiling. Only the claim that the window is *bounded*
by the provider falls, and with the page size unchanged the effective window is unchanged too.
