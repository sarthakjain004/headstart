"""Tests for check_liveness.py probers whose parsing is non-trivial (ADR-0012).

check_liveness.py is a script under scripts/validate, so we load it by path and mock its ``_get``
seam. Covers p_zoho's soft-404 classification: Zoho serves a 200 "Page does not exist" error page
(marked by ``cl-error-block``) for a gone/unpublished careers site, which must be DEAD, not UNKNOWN.
"""

from __future__ import annotations

import html
import importlib.util
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "check_liveness", _ROOT / "scripts" / "validate" / "check_liveness.py"
)
cl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cl)


@pytest.fixture(autouse=True)
def _no_spare_egress(monkeypatch):
    """No test in this file may reach the machine's real WARP daemon.

    `_on_429`'s bottom rung asks `spare_egress` for a different egress address before it bans a
    host, and left unpatched the breaker tests below take that literally — an early run of this
    suite kicked the local daemon four times. "Unavailable" is also exactly the state every test
    here was written against, so stubbing it restores their subject rather than changing it.
    """
    monkeypatch.setattr(
        cl,
        "spare_egress",
        SimpleNamespace(
            proxy_url=lambda: None,
            proxy_for=lambda key: None,
            generation=lambda: 0,
            mark_walled=lambda key, status: None,
            rotate=lambda key: False,
            rotations=Counter,
            egress_ips=Counter,
        ),
    )


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


def test_taleo_walks_relative_next_pages(monkeypatch):
    first = b'<div class="oracletaleocwsv2"><a href="/p/ats/careers/v2/viewRequisition?rid=1">x</a><a href="/p/ats/careers/v2/searchResults?next" class="jscroll-next">next</a>'
    second = b'<div class="oracletaleocwsv2"><a href="/p/ats/careers/v2/viewRequisition?rid=2">x</a>'
    calls = []

    def get(url, headers=None):
        calls.append(url)
        return 200, first if len(calls) == 1 else second

    monkeypatch.setattr(cl, "_get", get)
    assert cl.p_taleo_be(
        "acme", "https://phe.tbe.taleo.net/p/ats/careers/v2/searchResults?org=A&cws=1"
    ) == (cl.LIVE, 2)
    assert calls[1] == "https://phe.tbe.taleo.net/p/ats/careers/v2/searchResults?next"


def test_taleo_known_page_not_found_template_is_dead(monkeypatch):
    body = b"<title>Come Back Soon</title> You have attempted to reach a URL that no longer exists."
    monkeypatch.setattr(cl, "_get", _stub_get(200, body))
    assert cl.p_taleo_be(
        "acme", "https://phe.tbe.taleo.net/p/ats/careers/v2/searchResults?org=A&cws=1"
    ) == (cl.DEAD, None)


def test_taleo_enterprise_liveness_counts_the_scraper_listing(monkeypatch):
    class Scraper:
        def __init__(self, url, company):
            pass

        def url(self):
            return "https://acme.taleo.net/careersection/2/jobsearch.ftl?lang=en"

        def _listing(self, shell, timeout):
            assert shell == "shell"
            assert timeout == cl.TIMEOUT
            return [{"id": "1"}, {"id": "2"}]

    class Response:
        text = "shell"

        def raise_for_status(self):
            pass

    monkeypatch.setattr(cl, "TaleoEnterpriseScraper", Scraper)
    monkeypatch.setattr(cl.http, "fetch", lambda *args, **kwargs: Response())
    assert cl.p_taleo_enterprise("acme", "https://acme.taleo.net/careersection/2") == (
        cl.LIVE,
        2,
    )


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


# --- p_eightfold: a sitemap 200 with a plausible job count isn't proof the board still resolves,
# only a confirming fetch of one of its own listed job URLs is (nttdata.eightfold.ai, 2026-08-19:
# migrated off Eightfold months ago, but its sitemap still 200s with 16k stale /careers/job/ URLs
# that all 404 on fetch) ------------------------------------------------------------------------

_EF_SITEMAP = (
    b"<urlset><url><loc>https://acme.eightfold.ai/careers/job/1-a?domain=acme.com</loc></url>"
    b"<url><loc>https://acme.eightfold.ai/careers/job/2-b?domain=acme.com</loc></url>"
    b"<url><loc>https://acme.eightfold.ai/careers/job/3-c?domain=acme.com</loc></url></urlset>"
)
_EF_JOB_PAGE_LIVE = b'<script>_EF_GROUP_ID = "acme.com";</script>'


def _eightfold_stub(
    job_responses, careers_response=(200, b"<html>no group id here</html>")
):
    """Stub _get for p_eightfold: the sitemap URL serves _EF_SITEMAP; each job URL in
    `job_responses` (keyed by its exact /careers/job/... path) serves its mapped (status, body);
    the /careers page (the _eightfold_pcsx fallback) serves `careers_response`."""

    def _get(url, headers=None):
        if url.endswith("/careers/sitemap.xml"):
            return 200, _EF_SITEMAP
        if url.endswith("/careers"):
            return careers_response
        for path, response in job_responses.items():
            if url == path:
                return response
        raise AssertionError(f"unexpected URL in eightfold stub: {url}")

    return _get


def test_eightfold_confirmed_live_counts_the_sitemap(monkeypatch):
    stub = _eightfold_stub(
        {
            "https://acme.eightfold.ai/careers/job/1-a?domain=acme.com": (
                200,
                _EF_JOB_PAGE_LIVE,
            )
        }
    )
    monkeypatch.setattr(cl, "_get", stub)
    assert cl.p_eightfold("acme.eightfold.ai", "https://acme.eightfold.ai/") == (
        cl.LIVE,
        3,
    )


def test_eightfold_second_sample_confirms_after_first_404s(monkeypatch):
    # the first-listed job is gone, but the board is otherwise healthy — the small sample must not
    # give up on the first miss
    stub = _eightfold_stub(
        {
            "https://acme.eightfold.ai/careers/job/1-a?domain=acme.com": (404, b""),
            "https://acme.eightfold.ai/careers/job/2-b?domain=acme.com": (
                200,
                _EF_JOB_PAGE_LIVE,
            ),
        }
    )
    monkeypatch.setattr(cl, "_get", stub)
    assert cl.p_eightfold("acme.eightfold.ai", "https://acme.eightfold.ai/") == (
        cl.LIVE,
        3,
    )


def test_eightfold_stale_sitemap_falls_through_to_pcsx_and_settles_dead(monkeypatch):
    # every sampled job URL 404s (a migrated tenant's sitemap, still 200ing with dead links) and
    # the /careers page carries no _EF_GROUP_ID (redirected off Eightfold entirely) -> DEAD, not
    # a false LIVE off the stale count
    stub = _eightfold_stub(
        {
            "https://acme.eightfold.ai/careers/job/1-a?domain=acme.com": (404, b""),
            "https://acme.eightfold.ai/careers/job/2-b?domain=acme.com": (404, b""),
            "https://acme.eightfold.ai/careers/job/3-c?domain=acme.com": (404, b""),
        }
    )
    monkeypatch.setattr(cl, "_get", stub)
    assert cl.p_eightfold("acme.eightfold.ai", "https://acme.eightfold.ai/") == (
        cl.DEAD,
        None,
    )


def test_eightfold_confirming_fetch_rejects_vendor_fallthrough(monkeypatch):
    # a sampled job URL that 200s onto Eightfold's own generic careers page (no real tenant board
    # behind it) carries the VENDOR's _EF_GROUP_ID, not the tenant's — same fallthrough
    # _eightfold_pcsx already guards against, so it must not count as confirmed either
    vendor_page = b'<script>_EF_GROUP_ID = "eightfold.ai";</script>'
    stub = _eightfold_stub(
        {
            "https://acme.eightfold.ai/careers/job/1-a?domain=acme.com": (
                200,
                vendor_page,
            ),
            "https://acme.eightfold.ai/careers/job/2-b?domain=acme.com": (
                200,
                vendor_page,
            ),
            "https://acme.eightfold.ai/careers/job/3-c?domain=acme.com": (
                200,
                vendor_page,
            ),
        }
    )
    monkeypatch.setattr(cl, "_get", stub)
    assert cl.p_eightfold("acme.eightfold.ai", "https://acme.eightfold.ai/") == (
        cl.DEAD,
        None,
    )


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


def _fresh_workable_gate(spacing=0.0):
    gate = cl._HostGate(8, spacing, "apply.workable.com")
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
    assert not gate.blocked()  # per-request throttling, not a ban


def test_ungated_host_bypasses_open_breaker(monkeypatch):
    gate = _fresh_workable_gate()
    gate.trip(10_000, "test")  # workable is banned...
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


def test_headerless_429s_ease_the_rate_then_trip_at_the_floor(monkeypatch):
    """A bare 429 — no Retry-After, no challenge markers — must still stop us eventually.

    This replaces a consecutive-strikes counter. That could not work on a gate many boards share:
    any one of hundreds of threads getting a 200 reset it, so reaching the threshold was luck —
    measured, 1,800 rejections across 2,000 requests left the counter at 5. Easing to the floor is
    the honest signal instead, and it cannot be reset by a neighbour's success.
    """
    gate = _fresh_workable_gate(spacing=cl._MIN_SPACING)
    monkeypatch.setattr(cl.http, "fetch", lambda m, u, **kw: _Resp(429, {}))
    while gate.spacing < cl._MAX_SPACING:  # each 429 halves the rate
        assert cl.p_workable("acme", "") == (cl.UNKNOWN, None)
        assert not gate.blocked(), "must keep trying while easing can still help"
    cl.p_workable("acme", "")  # refused at the floor -> nothing left to try
    assert gate.blocked()


def test_a_neighbours_success_cannot_undo_the_easing(monkeypatch):
    """The old streak counter reset on any 200, which is why it never fired on a shared gate."""
    gate = _fresh_workable_gate(spacing=cl._MIN_SPACING)
    responses = [_Resp(429, {}), _Resp(200, {}, b'{"jobs": []}'), _Resp(429, {})]
    monkeypatch.setattr(cl.http, "fetch", lambda m, u, **kw: responses.pop(0))
    cl.p_workable("acme", "")
    eased_to = gate.spacing
    assert cl.p_workable("other", "") == (cl.LIVE, 0)
    assert gate.spacing == eased_to, "a success must not speed the gate back up"
    cl.p_workable("third", "")
    assert gate.spacing > eased_to, "and the next 429 keeps easing from where it was"


# --- pyjamahr: an unknown slug answers 200 with count 0, so the board page settles a zero ------


def _pyjamahr_get(api_status, api_body, page_status=None, calls=None):
    """`_get` keyed on host: the listing on api.pyjamahr.com, the board page on jobs.pyjamahr.com."""

    def _get(url, headers=None):
        if calls is not None:
            calls.append(url)
        if "api.pyjamahr.com" in url:
            return api_status, api_body
        return page_status, b"<html><title>Acme</title></html>"

    return _get


def test_pyjamahr_a_nonzero_count_is_live_without_touching_the_board_page(monkeypatch):
    """`count` is the Board's whole total whatever `limit` the probe asked for; a positive one is
    proof of a tenant, so the second request is never spent."""
    calls: list[str] = []
    monkeypatch.setattr(
        cl,
        "_get",
        _pyjamahr_get(
            200, b'{"count": 124, "next": null, "results": [{}]}', calls=calls
        ),
    )
    assert cl.p_pyjamahr("tulip-group", "https://jobs.pyjamahr.com/tulip-group") == (
        cl.LIVE,
        124,
    )
    assert (
        len(calls) == 1
        and "limit=1" in calls[0]
        and "company_slug=tulip-group" in calls[0]
    )


def test_pyjamahr_a_zero_count_with_a_board_page_is_a_live_empty_board(monkeypatch):
    """Measured: 75 real tenants answer `count: 0` and a 200 board page — live, nothing open."""
    monkeypatch.setattr(
        cl, "_get", _pyjamahr_get(200, b'{"count": 0, "results": []}', 200)
    )
    assert cl.p_pyjamahr("volopay", "") == (cl.LIVE, 0)


def test_pyjamahr_a_zero_count_without_a_board_page_is_dead(monkeypatch):
    """The same `count: 0` envelope an unknown slug gets — only the page's 404 tells them apart."""
    monkeypatch.setattr(
        cl, "_get", _pyjamahr_get(200, b'{"count": 0, "results": []}', 404)
    )
    assert cl.p_pyjamahr("notacompany123", "") == (cl.DEAD, None)


def test_pyjamahr_inconclusive_answers_stay_unknown(monkeypatch):
    # The API down: nothing was learned.
    monkeypatch.setattr(cl, "_get", _pyjamahr_get(503, b""))
    assert cl.p_pyjamahr("acme", "") == (cl.UNKNOWN, None)
    # A 200 that is not the envelope (a wall page, say) is not a count of zero.
    monkeypatch.setattr(cl, "_get", _pyjamahr_get(200, b"<html>checkpoint</html>", 200))
    assert cl.p_pyjamahr("acme", "") == (cl.UNKNOWN, None)
    # A zero count whose board page could not be read is neither empty nor gone yet.
    monkeypatch.setattr(
        cl, "_get", _pyjamahr_get(200, b'{"count": 0, "results": []}', 503)
    )
    assert cl.p_pyjamahr("acme", "") == (cl.UNKNOWN, None)


# --- breezy: the listing's status settles it, redirects not followed ------------------------------


def _breezy_fetch(status, content=b"", calls=None, raises=None):
    def _fetch(method, url, **kw):
        if calls is not None:
            calls.append((url, kw))
        if raises is not None:
            raise raises
        return _Resp(status, content=content)

    return _fetch


def test_breezy_a_listing_is_live_with_its_length_in_one_request(monkeypatch):
    """The plain `/json` (no `verbose`: the count needs no descriptions), asked without following
    redirects. 2,174 of the 4,794 pool tenants answered a non-empty list."""
    calls: list = []
    monkeypatch.setattr(
        cl, "_fetch", _breezy_fetch(200, b'[{"id": "a"}, {"id": "b"}]', calls)
    )
    assert cl.p_breezy("fathom", "fathom.breezy.hr") == (cl.LIVE, 2)
    [(url, kw)] = calls
    assert url == "https://fathom.breezy.hr/json"
    assert kw["allow_redirects"] is False


def test_breezy_an_empty_list_is_a_live_board_hiring_nobody(monkeypatch):
    """Measured: 1,703 tenants answer exactly `[]`."""
    monkeypatch.setattr(cl, "_fetch", _breezy_fetch(200, b"[]"))
    assert cl.p_breezy("arduino", "") == (cl.LIVE, 0)


def test_breezy_a_404_is_dead(monkeypatch):
    """917 tenants, and an invented label, answer the same 3,265-byte "Career portal not found"."""
    monkeypatch.setattr(cl, "_fetch", _breezy_fetch(404, b"<html>not found</html>"))
    assert cl.p_breezy("inngest", "") == (cl.DEAD, None)


def test_breezy_anything_unmeasured_stays_unknown(monkeypatch):
    # No tenant redirected in the census, so a redirect is news, not a verdict.
    monkeypatch.setattr(cl, "_fetch", _breezy_fetch(302))
    assert cl.p_breezy("acme", "") == (cl.UNKNOWN, None)
    # A 200 that is not a JSON list is not a count of zero.
    monkeypatch.setattr(cl, "_fetch", _breezy_fetch(200, b"<html>checkpoint</html>"))
    assert cl.p_breezy("acme", "") == (cl.UNKNOWN, None)
    monkeypatch.setattr(cl, "_fetch", _breezy_fetch(503))
    assert cl.p_breezy("acme", "") == (cl.UNKNOWN, None)
    monkeypatch.setattr(cl, "_fetch", lambda *a, **k: None)  # breaker open
    assert cl.p_breezy("acme", "") == (cl.UNKNOWN, None)
    monkeypatch.setattr(
        cl, "_fetch", _breezy_fetch(0, raises=cl.http.RequestsError("timed out"))
    )
    assert cl.p_breezy("acme", "") == (cl.UNKNOWN, None)


def test_breezy_a_dns_failure_is_unknown_because_every_label_resolves(monkeypatch):
    """`*.breezy.hr` is a wildcard record, so curl's code 6 on a Board host is the local resolver
    failing under 432 workers — 41 live Boards were written dead that way — not a gone tenant."""
    dns = cl.http.RequestsError("Could not resolve host", code=cl._DNS_ERR)
    assert cl._is_dns(dns)
    monkeypatch.setattr(cl, "_fetch", _breezy_fetch(0, raises=dns))
    assert cl.p_breezy("kimmel-associates", "") == (cl.UNKNOWN, None)
