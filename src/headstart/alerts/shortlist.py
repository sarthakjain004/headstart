"""Which of a Subscription's search hits go in its Digest (ADR-0035).

Pure: rows in, rows out, no clock and no I/O. Two rules, both defensive.

The **Watermark cut** is belt-and-braces. `search` asks the Space for
`first_seen_after=<watermark>`, so in a healthy deploy every row is already new — but the
Space is deployed separately from this package, and one that has not picked up the
`first_seen_after` parameter yet would silently ignore it and answer with the whole
corpus. Re-cutting here means a lagging Space under-delivers rather than mailing someone
their entire index. Rows with no `first_seen` are dropped for the same reason: an unstamped
row (pre-ADR-0031) cannot be shown to be new.

The **cap** is a ceiling this function offers, not one it imposes on the Digest. `run` ranks
twice over (ADR-0038: "The message is capped and the spreadsheet is not"), calling this with
`cap=len(rows)` for the spreadsheet and cutting the message to `CAP` itself — so the readable-
message cut lives there, and `CAP` is the default for any caller that wants both at once.
"""

from __future__ import annotations

from typing import Any

CAP = 30


def shortlist(
    rows: list[dict[str, Any]], after: str, cap: int = CAP
) -> list[dict[str, Any]]:
    """The best `cap` rows first seen strictly after `after`, highest score first.

    `after` and `first_seen` are ISO-8601 UTC strings, compared as strings — the same
    lexicographic trick the Space's own recency clause uses.
    """
    fresh = [row for row in rows if (row.get("first_seen") or "") > after]
    fresh.sort(key=lambda row: row.get("score") or 0.0, reverse=True)
    return fresh[:cap]
