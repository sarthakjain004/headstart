"""Why a Job id is not in the served table now: the one account every reader of a missing id
gives — `/search?like=` and the MCP's `get_job` (ADR-0323).

A served row leaves the table in more ways than its posting closing, and some of them keep no
grace period:

- its Board's scrapes miss it twice running (ADR-0083): most often it has closed;
- `index prune` removes it at once as a duplicate of another row, which stays served under its
  own id, or because its Board is no longer read (ADR-0023);
- its Board went Dormant (ADR-0250);
- the tech filter no longer counts it as tech (ADR-0243);

and an id copied or typed wrong was never one. It imports nothing, so the MCP server, which reads
the Space over HTTP, can say the same sentence.
"""

from __future__ import annotations

#: Said after "<id> is not in the index now." — by the Space and by the MCP server alike.
WHY_NOT_SERVED = (
    "Most often it has closed: HeadStart removes a posting once two consecutive scrapes of its "
    "Board miss it. It is also removed when it repeats another listing, which stays served under "
    "its own id; when its Board went dormant or is no longer read; or when the tech filter no "
    "longer counts it as tech. Or it was never an id."
)
