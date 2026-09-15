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


def test_transformers_is_pinned_not_left_to_float():
    """`transformers` unpinned resolves whatever PyPI serves at build time, and that has
    already broken the Space once (#469): nomic's *remote* modeling code calls
    `get_extended_attention_mask`, which newer `transformers` stops `NomicBertModel`
    inheriting — every `.encode()` call throws `AttributeError`, after the model has already
    loaded successfully, so the container boots looking healthy and only fails on the first
    real search. `pyproject.toml`'s `embed` extra caps this same break with `transformers<5.13`
    (measured 2026-09-10), but that cap never reaches this file — a separate,
    `sentence-transformers==5.7.0`-pinned requirements file for a different install path.

    Not asserting the exact bound here, only that one exists: the bound is a measured fact
    that belongs beside the pin it protects, not duplicated into a docstring that could drift
    from it silently.
    """
    line = next(
        (
            entry.strip()
            for entry in _SPACE_REQUIREMENTS.read_text().splitlines()
            if entry.strip().startswith("transformers")
        ),
        None,
    )
    assert line is not None, "the Space needs transformers pinned, not left to float"
    assert any(op in line for op in ("==", "<")), (
        f"{line!r} does not bound the resolved version — an unbounded or >=-only spec "
        "still floats onto a future break"
    )
