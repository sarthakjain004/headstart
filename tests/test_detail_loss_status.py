"""A detail loss must record the origin's status, not just the exception's class name.

`note_detail_loss(type(exc).__name__)` throws away the one field that makes a loss actionable.
It is why zwayam's 100%-loss detail pass read as `HTTPError x11135` for five consecutive runs
while the real answer — an Akamai edge `403` on one path — was never in any log.

The same defect was found and fixed for Oracle on 2026-09-12; it survived in twelve other
scrapers because nothing stopped it coming back. The guard below is what stops it.
"""

import re
from pathlib import Path

import pytest

from headstart.scrapers.base import classify_exception

SCRAPERS = Path(__file__).resolve().parents[1] / "src" / "headstart" / "scrapers"

#: The anti-pattern: discarding the response status and recording the bare exception class.
_DISCARDS_STATUS = re.compile(r"note_detail_loss\(\s*type\(\s*\w+\s*\)\.__name__\s*\)")


class _Response:
    def __init__(self, status_code):
        self.status_code = status_code


class _HTTPError(Exception):
    def __init__(self, status_code):
        super().__init__(f"HTTP Error {status_code}")
        self.response = _Response(status_code)


def test_classify_keeps_the_status_the_origin_gave():
    assert classify_exception(_HTTPError(403)) == "HTTP 403"
    assert classify_exception(_HTTPError(429)) == "HTTP 429"


def test_classify_falls_back_to_the_class_when_there_is_no_response():
    """A transport error never reached an origin, so there is no status to keep."""
    assert classify_exception(TimeoutError("no response")) == "TimeoutError"


def test_note_detail_exception_is_never_less_informative():
    """The substitution applied across the scrapers must be strictly a superset."""
    for exc in (_HTTPError(500), TimeoutError("x"), ValueError("y")):
        classified = classify_exception(exc)
        assert (
            classified == f"HTTP {exc.response.status_code}"
            if hasattr(exc, "response")
            else classified == type(exc).__name__
        )


@pytest.mark.parametrize("path", sorted(SCRAPERS.glob("*.py")), ids=lambda p: p.name)
def test_no_scraper_discards_the_response_status(path):
    """Guard: use `note_detail_exception(exc)`, which keeps the status when the origin gave one."""
    offenders = [
        line_no
        for line_no, line in enumerate(path.read_text().splitlines(), 1)
        if _DISCARDS_STATUS.search(line)
    ]
    assert not offenders, (
        f"{path.name} discards the response status at line(s) "
        f"{', '.join(map(str, offenders))}; call note_detail_exception(exc) instead"
    )
