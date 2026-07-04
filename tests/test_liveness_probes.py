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


def _join_stub(page_props, jobs_rowcount=None):
    """Stub _get for p_join: the company page carries __NEXT_DATA__.pageProps; the jobs API returns
    a pagination.rowCount."""
    import json

    page = (
        '<script id="__NEXT_DATA__" type="application/json">'
        + json.dumps({"props": {"pageProps": page_props}})
        + "</script>"
    ).encode()

    def _get(url, headers=None):
        if "/api/public/companies/" in url:
            return 200, json.dumps({"pagination": {"rowCount": jobs_rowcount}}).encode()
        return 200, page

    return _get


def test_join_soft404_is_dead(monkeypatch):
    # join.com serves a 200 Next.js error page (statusCode 404/410) for a gone company
    monkeypatch.setattr(
        cl,
        "_get",
        _join_stub({"statusCode": 404, "error": {"msg": "Entity not found"}}),
    )
    assert cl.p_join("gone", "https://join.com/companies/gone") == (cl.DEAD, None)
    monkeypatch.setattr(
        cl,
        "_get",
        _join_stub({"statusCode": 410, "error": {"msg": "Resource deleted"}}),
    )
    assert cl.p_join("gone", "https://join.com/companies/gone") == (cl.DEAD, None)


def test_join_live_company_counts_jobs(monkeypatch):
    monkeypatch.setattr(
        cl,
        "_get",
        _join_stub({"initialState": {"company": {"id": 123}}}, jobs_rowcount=5),
    )
    assert cl.p_join("acme", "https://join.com/companies/acme") == (cl.LIVE, 5)


def _workday_post_stub(live_instance=None, total=3, dead_status=422):
    """Stub _post for p_workday: 200+{total} on the CXS URL for `live_instance`, else dead_status."""

    def _post(url, body, headers):
        if live_instance and f".{live_instance}." in url:
            return 200, {"total": total}
        return dead_status, None

    return _post


def test_workday_hinted_instance_live(monkeypatch):
    monkeypatch.setattr(cl, "_post", _workday_post_stub(live_instance="wd3", total=7))
    assert cl.p_workday("acme", "https://acme.wd3.myworkdayjobs.com/careers") == (
        cl.LIVE,
        7,
    )


def test_workday_migrated_recovered_on_sweep(monkeypatch):
    # hinted wd3 422s; the DC sweep finds the tenant live on wd103
    monkeypatch.setattr(
        cl, "_post", _workday_post_stub(live_instance="wd103", total=2000)
    )
    assert cl.p_workday("acme", "https://acme.wd3.myworkdayjobs.com/careers") == (
        cl.LIVE,
        2000,
    )


def test_workday_gone_everywhere_is_dead(monkeypatch):
    # 422 on every data center -> definitive "not here" -> DEAD
    monkeypatch.setattr(
        cl, "_post", _workday_post_stub(live_instance=None, dead_status=422)
    )
    assert cl.p_workday("gone", "https://gone.wd3.myworkdayjobs.com/careers") == (
        cl.DEAD,
        None,
    )


def test_workday_transient_everywhere_is_unknown(monkeypatch):
    # a timeout (None status) is not definitive -> UNKNOWN (re-probe), never DEAD
    monkeypatch.setattr(
        cl, "_post", _workday_post_stub(live_instance=None, dead_status=None)
    )
    assert cl.p_workday("slow", "https://slow.wd3.myworkdayjobs.com/careers") == (
        cl.UNKNOWN,
        None,
    )


def test_workday_dns_is_unknown_not_dead(monkeypatch):
    # a dns failure is OUR network (the *.wdN wildcard always resolves), never a dead board — so a
    # network outage during a run can't produce false-deads
    monkeypatch.setattr(
        cl, "_post", _workday_post_stub(live_instance=None, dead_status="dns")
    )
    assert cl.p_workday("x", "https://x.wd3.myworkdayjobs.com/careers") == (
        cl.UNKNOWN,
        None,
    )


def test_workday_one_transient_blocks_dead(monkeypatch):
    # the no-false-dead guarantee: even with 18 DCs saying 422, a single timeout means we couldn't
    # rule out a live instance there (it might be the migrated one) -> UNKNOWN, never DEAD
    def _post(url, body, headers):
        return (
            (None, None) if ".wd103." in url else (422, None)
        )  # wd103 times out; rest say gone

    monkeypatch.setattr(cl, "_post", _post)
    assert cl.p_workday("x", "https://x.wd3.myworkdayjobs.com/careers") == (
        cl.UNKNOWN,
        None,
    )


# --- shared-host gate + circuit breaker (workable's Cloudflare per-IP ban) ----------------------


class _Resp:
    def __init__(self, status, headers=None, content=b""):
        self.status_code = status
        self.headers = headers or {}
        self.content = content


def _fresh_workable_gate():
    gate = cl._HostGate(8, 0.0)
    cl._GATES["apply.workable.com"] = gate
    return gate


def test_long_retry_after_trips_breaker_and_short_circuits(monkeypatch):
    _fresh_workable_gate()
    calls = {"n": 0}

    def fetch(method, url, **kw):
        calls["n"] += 1
        return _Resp(429, {"Retry-After": "72000"})

    monkeypatch.setattr(cl.http, "fetch", fetch)
    # the banning 429 itself is UNKNOWN (never DEAD), and it opens the breaker
    assert cl.p_workable("acme", "") == (cl.UNKNOWN, None)
    # further probes short-circuit: still UNKNOWN, and no network request is made
    assert cl.p_workable("other", "") == (cl.UNKNOWN, None)
    assert calls["n"] == 1


def test_short_retry_after_does_not_trip(monkeypatch):
    gate = _fresh_workable_gate()
    monkeypatch.setattr(
        cl.http, "fetch", lambda m, u, **kw: _Resp(429, {"Retry-After": "2"})
    )
    assert cl.p_workable("acme", "") == (cl.UNKNOWN, None)
    assert gate.banned_until == 0.0  # per-request throttling, not a ban


def test_ungated_host_bypasses_open_breaker(monkeypatch):
    gate = _fresh_workable_gate()
    gate.banned_until = float("inf")  # workable is banned...
    monkeypatch.setattr(cl.http, "fetch", lambda m, u, **kw: _Resp(200, {}, b"[]"))
    # ...but greenhouse (ungated) still probes normally
    assert cl.p_greenhouse("acme", "") == (cl.LIVE, 0)


def test_retry_after_parsing():
    assert cl._retry_after_s("72000") == 72000
    assert cl._retry_after_s(None) is None
    assert cl._retry_after_s("garbage") is None
    # HTTP-date form parses to a non-negative delta (a past date clamps to 0)
    assert cl._retry_after_s("Wed, 21 Oct 2015 07:28:00 GMT") == 0


def test_cf_challenge_trips_breaker_without_retry_after(monkeypatch):
    _fresh_workable_gate()
    calls = {"n": 0}

    def fetch(method, url, **kw):
        calls["n"] += 1
        return _Resp(429, {"cf-mitigated": "challenge"})

    monkeypatch.setattr(cl.http, "fetch", fetch)
    assert cl.p_workable("acme", "") == (cl.UNKNOWN, None)
    assert cl.p_workable("other", "") == (cl.UNKNOWN, None)  # breaker open, no request
    assert calls["n"] == 1


def test_plain_429_streak_trips_breaker(monkeypatch):
    gate = _fresh_workable_gate()
    monkeypatch.setattr(cl.http, "fetch", lambda m, u, **kw: _Resp(429, {}))
    for _ in range(cl._STRIKES_TO_TRIP):
        assert cl.p_workable("acme", "") == (cl.UNKNOWN, None)
    assert gate.banned_until > 0  # streak tripped it even with no headers at all


def test_success_resets_the_429_streak(monkeypatch):
    gate = _fresh_workable_gate()
    responses = [_Resp(429, {})] * (cl._STRIKES_TO_TRIP - 1) + [
        _Resp(200, {}, b'{"jobs": []}')
    ]
    monkeypatch.setattr(cl.http, "fetch", lambda m, u, **kw: responses.pop(0))
    for _ in range(cl._STRIKES_TO_TRIP - 1):
        cl.p_workable("acme", "")
    assert cl.p_workable("acme", "") == (cl.LIVE, 0)
    assert gate.strikes == 0 and gate.banned_until == 0.0
