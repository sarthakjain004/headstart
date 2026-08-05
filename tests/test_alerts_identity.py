"""Google sign-in verification — the `verifier` seam stands in for Google (ADR-0035)."""

import pytest

from headstart.alerts.identity import IdentityError, verify

CLIENT_ID = "client-123"


def _claims(**over):
    base = {"email": "Ada@Example.com", "email_verified": True, "aud": CLIENT_ID}
    base.update(over)
    return lambda credential, client_id: base


def test_returns_the_verified_address_lowercased():
    assert verify("cred", CLIENT_ID, _claims()) == "ada@example.com"


def test_missing_credential_is_refused_without_calling_google():
    def explode(credential, client_id):  # pragma: no cover - must never run
        raise AssertionError("verifier called for an empty credential")

    with pytest.raises(IdentityError):
        verify("", CLIENT_ID, explode)


def test_unverified_address_is_refused():
    # Google accepted the token, but the holder never proved they own the address.
    with pytest.raises(IdentityError, match="not verified"):
        verify("cred", CLIENT_ID, _claims(email_verified=False))


def test_token_without_an_email_is_refused():
    with pytest.raises(IdentityError, match="no email"):
        verify("cred", CLIENT_ID, _claims(email=""))


def test_google_rejection_becomes_identity_error():
    def reject(credential, client_id):
        raise ValueError("Token expired")

    with pytest.raises(IdentityError, match="could not be verified"):
        verify("cred", CLIENT_ID, reject)
