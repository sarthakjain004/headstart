"""Turn a Google sign-in credential into a verified address — and nothing else (ADR-0035).

`verify(credential, client_id)` is the interface. Behind it: Google's signing-key fetch,
the RS256 signature check, and the issuer/audience/expiry checks that make the token mean
anything. Skipping any of those turns a signed assertion into a free-text field, which is
why this does not hand-roll the JWT — `google-auth` is the reference implementation.

The `verifier` seam keeps this testable without network or a real Google token, the way
`profile_extract.extract(text, ask=…)` does. Scope is identity only: whether a verified
address is *allowed* to use alerts is `access`'s question.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .access import normalize


class IdentityError(Exception):
    """The credential was missing, malformed, unverified, or not issued for this app."""


def _google_verifier(credential: str, client_id: str) -> dict[str, Any]:
    # Imported here so the Space (and CI, which installs no extras) can import this module
    # without google-auth present until a sign-in actually happens.
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    return id_token.verify_oauth2_token(
        credential, google_requests.Request(), client_id
    )


def verify(
    credential: str,
    client_id: str,
    verifier: Callable[[str, str], dict[str, Any]] | None = None,
) -> str:
    """The Google-verified email address behind `credential`, lowercased.

    Raises :class:`IdentityError` for anything short of a valid, verified address —
    including a token Google accepts but whose `email_verified` is false, which is an
    address the holder has not proven they own.
    """
    if not credential:
        raise IdentityError("no sign-in credential was sent")
    try:
        claims = (verifier or _google_verifier)(credential, client_id)
    except Exception as exc:
        raise IdentityError(
            f"sign-in could not be verified: {type(exc).__name__}"
        ) from exc

    email = normalize(str(claims.get("email") or ""))
    if not email:
        raise IdentityError("sign-in carried no email address")
    if not claims.get("email_verified"):
        raise IdentityError("that Google account's address is not verified")
    return email
