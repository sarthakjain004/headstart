"""Tests for check_liveness.py probers whose parsing is non-trivial (ADR-0012).

check_liveness.py is a script under scripts/validate, so we load it by path and mock its ``_get``
seam. Covers p_zoho's soft-404 classification: Zoho serves a 200 "Page does not exist" error page
(marked by ``cl-error-block``) for a gone/unpublished careers site, which must be DEAD, not UNKNOWN.
"""

from __future__ import annotations

import html
import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "check_liveness", _ROOT / "scripts" / "validate" / "check_liveness.py"
)
cl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cl)


def _stub_get(status, body):
    def _get(url, headers=None):
        return status, body

    return _get


def test_zoho_error_page_is_dead(monkeypatch):
    body = b"<html><head><style>.cl-error-block{}</style></head><body>Page does not exist</body></html>"
    monkeypatch.setattr(cl, "_get", _stub_get(200, body))
    assert cl.p_zoho("acme", "https://acme.zohorecruit.com") == (cl.DEAD, None)


def test_zoho_live_page_counts_jobs(monkeypatch):
    val = html.escape('[{"id": 1}, {"id": 2}, {"id": 3}]')
    body = f'<input type="hidden" value="{val}" id="jobs">'.encode()
    monkeypatch.setattr(cl, "_get", _stub_get(200, body))
    assert cl.p_zoho("acme", "https://acme.zohorecruit.com") == (cl.LIVE, 3)


def test_zoho_200_without_jobs_or_error_is_unknown(monkeypatch):
    # a 200 that is neither the error page nor a jobs page -> genuinely can't tell -> re-probe
    monkeypatch.setattr(cl, "_get", _stub_get(200, b"<html>nothing useful here</html>"))
    assert cl.p_zoho("acme", "https://acme.zohorecruit.com") == (cl.UNKNOWN, None)


def test_zoho_404_is_dead(monkeypatch):
    monkeypatch.setattr(cl, "_get", _stub_get(404, b""))
    assert cl.p_zoho("acme", "https://acme.zohorecruit.com") == (cl.DEAD, None)
