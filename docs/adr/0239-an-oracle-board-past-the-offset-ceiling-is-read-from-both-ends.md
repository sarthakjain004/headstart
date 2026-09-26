# ADR-0239: An Oracle Board past the offset ceiling is read from both ends

**Status:** accepted · **Date:** 2026-09-26 · **Amends:**
[ADR-0121](0121-a-negligible-shortfall-is-still-an-authoritative-list.md) (its Oracle hard-cap
exception)

## Context

Oracle's requisition API serves no offset past 10,000. ADR-0121 therefore truncated any Board that
stated more, whatever share it read, because the remainder was unreachable on every run. That kept
`oracle:eluq.fa.us2` (11,436 stated) outside the eviction scope on every run, with no drain.

The walk also misread its own ceiling. It advanced its offset before testing for the empty page,
so a Board that ended on the empty page at offset 9,800 (which the API still serves) looked like
it had passed 10,000. `oracle:hcbt.fa.em2` (run 36218633315: 9,611 read of 9,621 stated) was truncated for that on
every run in 36200233818..36218633315.

## Decision

The ceiling counts as hit only when the walk's last page began past it (`offset + limit > 10,000`).

A Board that hits it is walked a second time with `sortBy=POSTING_DATES_DESC`, and the two reads
are unioned by id. The default order is oldest first. On `eluq` it returned the same 9,975 ids as
`POSTING_DATES_ASC`, so the second walk reads the other end of the Board. The union goes through
`mark_truncated_unless_negligible` like any other measured shortfall. Only the middle between the
two ends is out of reach, and a Board stating more than about 20,000 still falls short of the
tolerance and stays truncated.

Measured live on 2026-09-26: `eluq` read 9,975 ids in the default order and 10,000 newest first,
11,411 together, which is 99.8% of the 11,436 it states. `hcbt` read 9,608 of the 9,618 it stated that day and
ended on the empty page at 9,800.

## Consequences

* A Board past the ceiling costs up to 50 more listing requests. Few Boards state more than
  10,000.
* Past about 20,000 stated, the second walk still serves up to 10,000 newer postings the first
  walk could not reach, although the Board stays truncated.
* `base.py`'s `mark_truncated_unless_negligible` docstring still names Oracle's ceiling as a hard
  cap that must not come through the tolerance. That no longer holds for Oracle's union read.
