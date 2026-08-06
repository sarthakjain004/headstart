"""Google sign-in verification — the `verifier` seam stands in for Google (ADR-0035)."""

from pathlib import Path

import pytest

from headstart.alerts.identity import IdentityError, verify

CLIENT_ID = "client-123"

_SPACE_REQUIREMENTS = (
    Path(__file__).resolve().parents[1] / "deploy" / "hf-space" / "requirements.txt"
)


def test_space_requirements_ask_for_the_google_auth_requests_extra():
    """The Space image must carry `requests`, or every sign-in 401s.

    `_google_verifier` imports `google.auth.transport.requests`, and that module raises a
    bare `ImportError` — not `ModuleNotFoundError` — when `requests` is absent, because
    google-auth declares it as the `[requests]` **extra** rather than a core dependency.
    Nothing else in the image supplies it: `huggingface_hub` moved to httpx. So plain
    `google-auth` installs cleanly, imports fine everywhere the tests look, and then turns
    every real sign-in into "sign-in could not be verified: ImportError" at runtime.

    Every seam that would otherwise catch this is blind here — the `verifier` argument
    stands in for Google in the tests above, `tests/test_space_app.py` stubs `sys.modules`,
    and CI installs no extras. The requirements line is the only place left to check.
    """
    line = next(
        (
            entry.strip()
            for entry in _SPACE_REQUIREMENTS.read_text().splitlines()
            if entry.strip().startswith("google-auth")
        ),
        None,
    )
    assert line is not None, "the Space needs google-auth to verify sign-ins at all"
    assert line.startswith("google-auth[requests]"), (
        f"{line!r} leaves the Space without `requests`; use google-auth[requests]"
    )


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
