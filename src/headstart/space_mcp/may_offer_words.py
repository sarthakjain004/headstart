"""How an answer says why a job may offer visa sponsorship rather than offers it (ADR-0367): one
phrasing, which a search row and a read by id both give (ADR-0370)."""

from __future__ import annotations

from headstart.jobs import work_authorization

#: Each of `work_authorization.MAY_OFFER_REASONS` in words, in that order.
MAY_OFFER_WORDS = {
    work_authorization.HEDGED: "hedged",
    work_authorization.TRANSFER_ONLY: "a visa transfer only",
    work_authorization.SCOPE_UNREAD: (
        "limited to a country or level this job's place or title does not show"
    ),
}


def not_firm(because: list[str]) -> str:
    """Why a job's sponsorship is not a firm offer, as words: "not a firm offer: hedged; a visa
    transfer only", or "not a firm offer" where the Space names no reason."""
    said = "; ".join(MAY_OFFER_WORDS.get(reason, reason) for reason in because)
    return f"not a firm offer: {said}" if said else "not a firm offer"
