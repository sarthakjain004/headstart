"""Who may use email alerts — the allowlist policy (ADR-0035).

Deny-by-default, and that is the whole point: an unreadable, missing or empty allowlist
must refuse everyone rather than open the feature to the internet. The Space checks this
at subscribe time and the alerts run re-checks it before every Digest, so striking an
address off stops mail already flowing.

Kept apart from `identity` on purpose: identity answers "is this really their address?"
(a Google fact), access answers "may they use this?" (a HeadStart decision). The alerts
run needs the second without any of the first.
"""

from __future__ import annotations

from collections.abc import Iterable


def normalize(email: str) -> str:
    """Addresses compare case-insensitively and without surrounding space."""
    return email.strip().lower()


def is_allowed(email: str, allowlist: Iterable[str]) -> bool:
    """True only if `email` is named in `allowlist`. An empty allowlist allows nobody."""
    address = normalize(email)
    if not address:
        return False
    return address in {normalize(entry) for entry in allowlist if entry}
