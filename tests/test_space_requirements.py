"""What the deployed Space image must contain, asserted from the repo (ADR-0035).

Runtime-only dependency bugs are invisible to the rest of the suite by construction: the
alerts tests inject fakes for every seam that touches the network, `tests/test_space_app.py`
stubs `sys.modules` to import `app.py` at all, and CI installs no extras. So a package the
Space needs *at request time* is asserted here, against the requirements file itself.
"""

from pathlib import Path

_SPACE_REQUIREMENTS = (
    Path(__file__).resolve().parents[1] / "deploy" / "hf-space" / "requirements.txt"
)


def test_requirements_ask_for_the_google_auth_requests_extra():
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
