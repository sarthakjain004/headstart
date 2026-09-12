# ADR-0141: Complete empty Boards carry authoritative scrape evidence

- Status: Accepted
- Date: 2026-09-12
- Amends ADR-0023; preserves ADR-0053 and ADR-0083.

A complete Board returning zero Jobs emits no Job id. Inferring scrape scope only
from rows therefore never records an absence, and the Live-Board prune retains its
closed Jobs. The resume `.done` journal cannot substitute: it includes failures and
is hidden from the default Actions artifact upload.

The scrape writes `authoritative_boards.txt` alongside each fragment. It records
canonical Board identities only after a successful, non-truncated Board's Jobs
have been flushed, including empty results, and flushes each entry immediately.
The join replaces the union every run; the existing corpus artifact carries it to
sync. Sync supplements the full-scrape row identities with this evidence and still
subtracts Unauthoritative Boards. The second-consecutive-absence rule is unchanged.

The user chose this durable journal over deriving empty-Board evidence solely from
the final shard reports: a shard can stop after completing a Board and before
writing its summary. Older fragments without the journal retain row-derived scope;
there is no inference that an unrecorded Board was empty. This adds no HF state
migration and does not claim to fix every missing-report truncation case.
