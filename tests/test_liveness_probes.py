"""Tests for check_liveness.py probers whose parsing is non-trivial (ADR-0012).

check_liveness.py is a script under scripts/validate, so we load it by path and mock its ``_get``
seam. Covers p_zoho's soft-404 classification: Zoho serves a 200 "Page does not exist" error page
(marked by ``cl-error-block``) for a gone/unpublished careers site, which must be DEAD, not UNKNOWN.
"""

from __future__ import annotations

import html
import importlib.util
import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest
from curl_cffi.requests.exceptions import HTTPError as CurlHTTPError

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


# --- clearcompany: xml.php's status settles it; a req repeats once per location -------------


def test_clearcompany_counts_distinct_reqs_not_rows(monkeypatch):
    """xml.php emits one row per req per location: the Fisher Phillips fixture is 14 rows over 2
    reqs, and the ledger counts postings."""
    body = (
        _ROOT / "tests" / "fixtures" / "clearcompany_fisherphillips.xml"
    ).read_bytes()
    calls: list[str] = []
    monkeypatch.setattr(
        cl, "_get", lambda url, headers=None: calls.append(url) or (200, body)
    )
    assert cl.p_clearcompany("fisherphillips", "") == (cl.LIVE, 2)
    assert calls == ["https://fisherphillips.hrmdirect.com/employment/xml.php"]


def test_clearcompany_an_empty_feed_is_a_live_empty_board(monkeypatch):
    """16 of 244 measured tenants: a 200 `<source>` with no `<job>` — live, nothing open."""
    empty = (
        b'<?xml version="1.0" encoding="UTF-8"?>\n<source>\n<publisher>HRM Direct</publisher>\n'
        b"<publisherurl>http:www.hrmdirect.com</publisherurl>\n</source>\n "
    )
    monkeypatch.setattr(cl, "_get", lambda url, headers=None: (200, empty))
    assert cl.p_clearcompany("absorblms", "") == (cl.LIVE, 0)


def test_clearcompany_a_404_is_dead(monkeypatch):
    """Unknown and departed tenants alike: 51 of 51 xml.php 404s had a 404 Board page too."""
    monkeypatch.setattr(cl, "_get", lambda url, headers=None: (404, b"Not Found"))
    assert cl.p_clearcompany("defymedia", "") == (cl.DEAD, None)


@pytest.mark.parametrize(
    "status, body",
    [
        (None, b""),  # a refused connection or a timeout: nothing was learned
        (
            "dns",
            b"",
        ),  # the wildcard answers nearly every label; this is the local resolver
        (500, b""),  # heartlandbehavior's xml.php gives up server-side after ~104 s
        (200, b"<html>maintenance</html>"),  # a 200 that is not the feed
    ],
)
def test_clearcompany_inconclusive_answers_stay_unknown(monkeypatch, status, body):
    monkeypatch.setattr(cl, "_get", lambda url, headers=None: (status, body))
    assert cl.p_clearcompany("heartlandbehavior", "") == (cl.UNKNOWN, None)


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


# --- adp_recruiting: the site record answers first, and its token reads the count --------------

_ADP_RM = json.loads(
    (Path(__file__).parent / "fixtures" / "adp_recruiting_responses.json").read_text(
        "utf-8"
    )
)


def _adp_rm_get(site_status, site_body, listing=(200, b'{"count": 19}'), calls=None):
    """`_get` keyed on path: the site record on myjobs.adp.com, the listing on my.adp.com."""

    def _get(url, headers=None):
        if calls is not None:
            calls.append((url, headers or {}))
        if "/career-site/" in url:
            body = site_body if isinstance(site_body, bytes) else json.dumps(site_body)
            return site_status, body if isinstance(body, bytes) else body.encode()
        return listing

    return _get


def test_adp_recruiting_a_site_record_and_its_count_is_live(monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        cl, "_get", _adp_rm_get(200, _ADP_RM["site_churchmutual"], calls=calls)
    )
    assert cl.p_adp_recruiting("ChurchMutual", "") == (cl.LIVE, 19)
    (site_url, site_h), (listing_url, listing_h) = calls
    assert site_url.endswith("/career-site/churchmutual")
    assert "%24top=1" in listing_url and "%24skip=0" in listing_url
    # The token addresses the site; the language header is a filter curl_cffi would set wrong.
    assert listing_h["myjobstoken"] == _ADP_RM["site_churchmutual"]["myJobsToken"]
    assert site_h["Accept-Language"] == listing_h["Accept-Language"] == "en-US"


def test_adp_recruiting_a_zero_count_on_a_known_site_is_a_live_empty_board(monkeypatch):
    monkeypatch.setattr(
        cl,
        "_get",
        _adp_rm_get(200, _ADP_RM["site_churchmutual"], (200, b'{"count": 0}')),
    )
    assert cl.p_adp_recruiting("churchmutual", "") == (cl.LIVE, 0)


@pytest.mark.parametrize("key", ["site_not_found", "site_not_active"])
def test_adp_recruiting_a_site_record_naming_the_site_gone_is_dead(monkeypatch, key):
    monkeypatch.setattr(cl, "_get", _adp_rm_get(400, _ADP_RM[key]))
    assert cl.p_adp_recruiting("bastiansolutions", "") == (cl.DEAD, None)


def test_adp_recruiting_an_employee_only_site_is_dead(monkeypatch):
    monkeypatch.setattr(cl, "_get", _adp_rm_get(200, _ADP_RM["site_taherinternal"]))
    assert cl.p_adp_recruiting("taherinternal", "") == (cl.DEAD, None)


@pytest.mark.parametrize(
    "site_status, site_body, listing",
    [
        # One fixed host: a DNS failure is the resolver, not the site.
        ("dns", b"", (200, b'{"count": 1}')),
        # A 400 whose message was never measured on a departed site.
        (400, b'{"message":"Bad Request"}', (200, b'{"count": 1}')),
        (503, b"", (200, b'{"count": 1}')),
        (200, b"<html>wall</html>", (200, b'{"count": 1}')),
        # A live site whose listing errored: `trulitecareers` answered this 500 on 2026-09-24.
        (200, None, (500, b'{"message":"ErrCode=ERR_BAD_REQUEST"}')),
        (200, None, (200, b"<html></html>")),
    ],
)
def test_adp_recruiting_inconclusive_answers_stay_unknown(
    monkeypatch, site_status, site_body, listing
):
    body = _ADP_RM["site_churchmutual"] if site_body is None else site_body
    monkeypatch.setattr(cl, "_get", _adp_rm_get(site_status, body, listing))
    assert cl.p_adp_recruiting("churchmutual", "") == (cl.UNKNOWN, None)


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
    failing under 432 workers — 41 Boards read live an hour earlier were written dead that way —
    not a gone tenant."""
    dns = cl.http.RequestsError("Could not resolve host", code=cl._DNS_ERR)
    assert cl._is_dns(dns)
    monkeypatch.setattr(cl, "_fetch", _breezy_fetch(0, raises=dns))
    assert cl.p_breezy("kimmel-associates", "") == (cl.UNKNOWN, None)


# --- pinpoint: the listing, then a page asked the way a browser asks, redirects never followed ----

_PATH = "/en/postings/0f0cd77d-d352-43b5-b536-4cc87f2a5e82"
_ONE_POSTING = (
    b'{"data": [{"path": "/en/postings/0f0cd77d-d352-43b5-b536-4cc87f2a5e82"}]}'
)


def _pinpoint_fetch(listing, page=(200, ""), asked=None):
    """`_fetch` keyed on path: `/postings.json` answers `listing` (status, body, location); any
    other page answers `page` (status, location). Nothing may follow a redirect, and pages must
    be asked for as a browser asks (`Accept: text/html`). `asked` records the page paths."""

    def _fetch(method, url, **kw):
        assert kw.get("allow_redirects") is False
        if url.endswith("/postings.json"):
            status, body, location = listing
        else:
            assert kw["headers"]["Accept"] == "text/html"
            if asked is not None:
                asked.append(url.split(".pinpointhq.com", 1)[1])
            (status, location), body = page, b"<html></html>"
        return SimpleNamespace(
            status_code=status, content=body, headers={"location": location}
        )

    return _fetch


def test_pinpoint_a_board_whose_postings_render_is_live_with_its_count(monkeypatch):
    """With postings, the question is whether a user's click lands: the first and last postings'
    pages, not `/` — `kharon` 404s a browser on `/` while its postings render."""
    asked: list[str] = []
    three = b'{"data": [{"path": "/en/postings/a"}, {"path": "/en/postings/b"}, {"path": "/en/postings/c"}]}'
    monkeypatch.setattr(cl, "_fetch", _pinpoint_fetch((200, three, ""), asked=asked))
    assert cl.p_pinpoint("kharon", "https://kharon.pinpointhq.com") == (cl.LIVE, 3)
    assert asked == ["/en/postings/a", "/en/postings/c"]


def test_pinpoint_a_posting_redirected_to_its_vanity_host_is_live(monkeypatch):
    """158 of 692 Boards with postings 301 each posting to their own host at the same path,
    which serves it (`careers.admgroup.com`)."""
    monkeypatch.setattr(
        cl,
        "_fetch",
        _pinpoint_fetch(
            (200, _ONE_POSTING, ""), (301, f"https://careers.admgroup.com{_PATH}")
        ),
    )
    assert cl.p_pinpoint("admgroup", "") == (cl.LIVE, 1)


def test_pinpoint_a_posting_redirected_to_a_non_posting_page_is_dead(monkeypatch):
    """3 Boards (73 postings) send every posting to a company page instead
    (`10kai` -> `10000internsfoundation.com/our-programmes/`): a link we served would not land."""
    monkeypatch.setattr(
        cl,
        "_fetch",
        _pinpoint_fetch(
            (200, _ONE_POSTING, ""),
            (302, "https://10000internsfoundation.com/our-programmes/"),
        ),
    )
    assert cl.p_pinpoint("10kai", "") == (cl.DEAD, None)


def test_pinpoint_a_posting_that_404s_to_a_browser_is_dead(monkeypatch):
    """17 Boards (1,200 postings; `10kbi-23` alone 638) list postings in the JSON that 404 a
    browser — the careers site is switched off, and a link we served would be dead."""
    monkeypatch.setattr(
        cl, "_fetch", _pinpoint_fetch((200, _ONE_POSTING, ""), (404, ""))
    )
    assert cl.p_pinpoint("10kbi-23", "") == (cl.DEAD, None)


def test_pinpoint_an_empty_board_is_live_only_where_its_page_renders(monkeypatch):
    """Of 534 empty Boards asked on `/` as a browser: 145 render (live, nothing open), 282 are
    404 and 105 redirect off to the tenant's own or another ATS's site (greenhouse, linkedin) —
    no Board is published here."""
    asked: list[str] = []
    empty = (200, b'{"data":[]}', "")
    monkeypatch.setattr(cl, "_fetch", _pinpoint_fetch(empty, (200, ""), asked))
    assert cl.p_pinpoint("gain-careers", "") == (cl.LIVE, 0)
    assert asked == ["/"]
    monkeypatch.setattr(cl, "_fetch", _pinpoint_fetch(empty, (404, "")))
    assert cl.p_pinpoint("10kbi-2021", "") == (cl.DEAD, None)
    monkeypatch.setattr(
        cl, "_fetch", _pinpoint_fetch(empty, (301, "https://jobs.1840andco.com/"))
    )
    assert cl.p_pinpoint("1840andco", "") == (cl.DEAD, None)


def test_pinpoint_a_renamed_tenant_redirecting_to_another_label_is_dead(monkeypatch):
    """63 of 63 listing redirects measured went to another
    `{label}.pinpointhq.com/postings.json`; 57 of their targets are already live slugs, so
    following one would duplicate a Board."""
    monkeypatch.setattr(
        cl,
        "_fetch",
        _pinpoint_fetch((301, b"", "https://cfc.pinpointhq.com/postings.json")),
    )
    assert cl.p_pinpoint("cfcunderwriting", "") == (cl.DEAD, None)


def test_pinpoint_a_listing_redirect_anywhere_else_is_unknown(monkeypatch):
    monkeypatch.setattr(
        cl, "_fetch", _pinpoint_fetch((302, b"", "https://www.pinpointhq.com/"))
    )
    assert cl.p_pinpoint("acme", "") == (cl.UNKNOWN, None)


def test_pinpoint_an_unknown_slug_is_a_404_and_dead(monkeypatch):
    monkeypatch.setattr(cl, "_fetch", _pinpoint_fetch((404, b"<html>404</html>", "")))
    assert cl.p_pinpoint("zzqqnotatenant8127", "") == (cl.DEAD, None)


def test_pinpoint_an_unresolvable_host_is_dead_and_a_network_error_unknown(monkeypatch):
    def raising(exc):
        def _fetch(method, url, **kw):
            raise exc

        return _fetch

    dns = cl.http.RequestsError("no such host")
    dns.code = cl._DNS_ERR
    monkeypatch.setattr(cl, "_fetch", raising(dns))
    assert cl.p_pinpoint("gone", "") == (cl.DEAD, None)
    monkeypatch.setattr(cl, "_fetch", raising(cl.http.RequestsError("reset")))
    assert cl.p_pinpoint("acme", "") == (cl.UNKNOWN, None)


def test_pinpoint_inconclusive_answers_stay_unknown(monkeypatch):
    listing = (200, _ONE_POSTING, "")
    monkeypatch.setattr(cl, "_fetch", _pinpoint_fetch(listing, (503, "")))
    assert cl.p_pinpoint("acme", "") == (cl.UNKNOWN, None)
    monkeypatch.setattr(
        cl, "_fetch", _pinpoint_fetch((200, b"<html>checkpoint</html>", ""))
    )
    assert cl.p_pinpoint("acme", "") == (cl.UNKNOWN, None)
    monkeypatch.setattr(cl, "_fetch", _pinpoint_fetch((503, b"", "")))
    assert cl.p_pinpoint("acme", "") == (cl.UNKNOWN, None)
    monkeypatch.setattr(
        cl, "_fetch", lambda *a, **k: None
    )  # breaker open on the listing
    assert cl.p_pinpoint("acme", "") == (cl.UNKNOWN, None)

    def page_breaker(method, url, **kw):  # listing answers, then the breaker opens
        if url.endswith("/postings.json"):
            return SimpleNamespace(status_code=200, content=_ONE_POSTING, headers={})
        return None

    monkeypatch.setattr(cl, "_fetch", page_breaker)
    assert cl.p_pinpoint("acme", "") == (cl.UNKNOWN, None)


def test_pinpoint_boards_share_one_gate():
    """A burst of 256 concurrent connections across distinct tenants drew connection refusals
    that then held for minutes against every tenant, so the gate spans the domain."""
    assert cl._gate_key("zincwork.pinpointhq.com") == "pinpointhq.com"
    assert "pinpointhq.com" in cl._GATES


def test_pinpoint_one_failing_posting_is_not_a_dead_board(monkeypatch):
    """A posting can close between the listing and its page; one 404 must not bury a Board whose
    other postings land (`freeagent` flipped to dead once this way, and re-probed live 3 of 3).
    The first and last postings are both asked; the Board is dead only if neither lands."""
    listing = b'{"data": [{"path": "/en/postings/aaa"}, {"path": "/en/postings/bbb"}]}'

    def _fetch(method, url, **kw):
        if url.endswith("/postings.json"):
            return SimpleNamespace(status_code=200, content=listing, headers={})
        status = 404 if url.endswith("/aaa") else 200
        return SimpleNamespace(status_code=status, content=b"", headers={})

    monkeypatch.setattr(cl, "_fetch", _fetch)
    assert cl.p_pinpoint("freeagent", "") == (cl.LIVE, 2)


def test_pinpoint_an_empty_listing_is_asked_again_before_it_is_believed(monkeypatch):
    """A spurious `{"data":[]}` for a Board with postings (12 of 6,030 fetches) would otherwise
    send the probe to `/` — where a vanity-host Board's redirect reads as a site published
    elsewhere, and `jec` was written dead exactly that way."""
    answers = [b'{"data": []}', _ONE_POSTING]
    asked: list[str] = []

    def _fetch(method, url, **kw):
        if url.endswith("/postings.json"):
            return SimpleNamespace(status_code=200, content=answers.pop(0), headers={})
        asked.append(url.split(".pinpointhq.com", 1)[1])
        return SimpleNamespace(
            status_code=301,
            content=b"",
            headers={"location": f"https://careers.jec.co.uk{_PATH}"},
        )

    monkeypatch.setattr(cl, "_fetch", _fetch)
    assert cl.p_pinpoint("jec", "") == (cl.LIVE, 1)
    assert asked == [_PATH]


def test_pinpoint_a_failed_re_ask_of_an_empty_listing_is_unknown(monkeypatch):
    """The second ask settles nothing if it fails: a 503 or an open breaker is no verdict."""
    for second in (SimpleNamespace(status_code=503, content=b"", headers={}), None):
        answers = [
            SimpleNamespace(status_code=200, content=b'{"data":[]}', headers={}),
            second,
        ]
        monkeypatch.setattr(
            cl, "_fetch", lambda method, url, _a=answers, **kw: _a.pop(0)
        )
        assert cl.p_pinpoint("acme", "") == (cl.UNKNOWN, None)


# --- jibe: DNS settles dead, robots.txt is read first, the listing's totalCount is the count -----

_JIBE_ALLOW = "User-agent: *\nAllow: /\nSitemap: http://x/sitemap.xml\ncrawl-delay: 5\n"
_JIBE_DISALLOW = (
    "User-agent: *\nDisallow: /\nSitemap: http://x/sitemap.xml\ncrawl-delay: 5\n"
)


def _jibe(monkeypatch, robots, api=None):
    """`_fetch` keyed on path; `robots`/`api` are (status, body) or an exception. Records calls
    and the sleep between them."""
    calls: list[str] = []
    monkeypatch.setattr(cl.time, "sleep", lambda s: calls.append(f"sleep {s}"))

    def fake(method, url, **kw):
        calls.append(url)
        answer = robots if url.endswith("/robots.txt") else api
        if isinstance(answer, Exception):
            raise answer
        status, body = answer
        return SimpleNamespace(status_code=status, text=body)

    monkeypatch.setattr(cl, "_fetch", fake)
    return calls


def test_jibe_an_unresolvable_label_is_dead_once_public_dns_agrees(monkeypatch):
    """No A record for an unknown label (`zzzzqqq`, 101 pool labels) — but only a public
    resolver's answer counts: the macOS resolver said "no such host" for live `uhs` under load."""
    calls = _jibe(
        monkeypatch, cl.http.RequestsError("Could not resolve host", code=cl._DNS_ERR)
    )
    asked = []
    monkeypatch.setattr(cl, "_jibe_has_no_a_record", lambda h: asked.append(h) or True)
    assert cl.p_jibe("att", "https://att.jibeapply.com") == (cl.DEAD, None)
    assert calls == ["https://att.jibeapply.com/robots.txt"]
    assert asked == ["att.jibeapply.com"]


def test_jibe_a_local_dns_failure_public_dns_contradicts_is_unknown(monkeypatch):
    _jibe(
        monkeypatch, cl.http.RequestsError("Could not resolve host", code=cl._DNS_ERR)
    )
    monkeypatch.setattr(cl, "_jibe_has_no_a_record", lambda h: False)
    assert cl.p_jibe("uhs", "https://uhs.jibeapply.com") == (cl.UNKNOWN, None)


def test_jibe_a_live_board_is_counted_after_the_crawl_delay(monkeypatch):
    calls = _jibe(
        monkeypatch, (200, _JIBE_ALLOW), (200, '{"jobs":[],"totalCount":20093}')
    )
    assert cl.p_jibe("costco", "https://costco.jibeapply.com") == (cl.LIVE, 20093)
    assert calls == [
        "https://costco.jibeapply.com/robots.txt",
        "sleep 5.0",
        "https://costco.jibeapply.com/api/jobs?page=1&limit=1&internal=false",
    ]


def test_jibe_an_empty_board_is_live_with_zero(monkeypatch):
    """254 clients answer `totalCount: 0` — live, nothing open."""
    _jibe(monkeypatch, (200, _JIBE_ALLOW), (200, '{"jobs":[],"totalCount":0}'))
    assert cl.p_jibe("arco", "https://arco.jibeapply.com") == (cl.LIVE, 0)


def test_jibe_a_disallowing_host_is_never_read(monkeypatch):
    """carrefour serves `Disallow: /`: UNKNOWN, and no listing request is made."""
    calls = _jibe(monkeypatch, (200, _JIBE_DISALLOW), (200, '{"totalCount":930}'))
    assert cl.p_jibe("carrefour", "https://carrefour.jibeapply.com") == (
        cl.UNKNOWN,
        None,
    )
    assert calls == ["https://carrefour.jibeapply.com/robots.txt"]


def test_jibe_an_unreachable_robots_file_is_unknown(monkeypatch):
    """RFC 9309: a 5xx robots.txt permits nothing (`uri` answers 500)."""
    calls = _jibe(monkeypatch, (500, "Internal Server Error"))
    assert cl.p_jibe("uri", "https://uri.jibeapply.com") == (cl.UNKNOWN, None)
    assert len(calls) == 1


def test_jibe_a_listing_404_is_unknown_not_dead(monkeypatch):
    """21 resolving labels answer the listing with 404 (dycom's board lives under `/dycom/`)."""
    _jibe(
        monkeypatch,
        (200, _JIBE_ALLOW),
        (404, '{"data":{"error":"An unexpected error occurred"}}'),
    )
    assert cl.p_jibe("dycom", "https://dycom.jibeapply.com") == (cl.UNKNOWN, None)


def test_jibe_a_non_json_listing_is_unknown(monkeypatch):
    """fedex answers the listing with an Okta SSO form."""
    _jibe(
        monkeypatch,
        (200, _JIBE_ALLOW),
        (200, "<html><form action='https://purpleid.okta.com'>"),
    )
    assert cl.p_jibe("fedex", "https://fedex.jibeapply.com") == (cl.UNKNOWN, None)


# --- adp: content-links says dead-or-published and which languages; the listings count ---------

_ADP = "7d58836c-11dd-4415-9de0-63b918b88652/19000101_000001"
_ADP_URL = (
    "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html"
    "?cid=7d58836c-11dd-4415-9de0-63b918b88652&ccId=19000101_000001"
)


def _adp_links(published=True, locales=("en_US",)) -> bytes:
    import json

    return json.dumps(
        {
            "contentLinks": [],
            "meta": {
                "customFieldGroup": {
                    "indicatorFields": [
                        {
                            "indicatorValue": published,
                            "nameCode": {"codeValue": "PublishedIndicator"},
                        }
                    ],
                    "stringFields": [
                        {"stringValue": loc, "nameCode": {"codeValue": "Locale"}}
                        for loc in locales
                    ],
                }
            },
        }
    ).encode()


def _adp_get(links_status, links_body=b"", totals=None, calls=None):
    """`_get` for adp: content-links, then one `$top=1` listing per language."""

    def _get(url, headers=None):
        if calls is not None:
            calls.append(url)
        if "content-links" in url:
            return links_status, links_body
        lang = url.split("lang=")[1].split("&")[0]
        n = (totals or {}).get(lang)
        body = (
            b'{"jobRequisitions":[]}'
            if n is None
            else f'{{"jobRequisitions":[{{}}],"meta":{{"totalNumber":{n}}}}}'.encode()
        )
        return 200, body

    return _get


def test_adp_counts_postings_across_every_language_the_center_lists(monkeypatch):
    """`lang` is a filter, so an `en_CA` center reads empty under `en_US`; the count is the sum
    over the center's own languages (Lifemark: 460 en_CA, 17 fr_CA)."""
    calls: list[str] = []
    monkeypatch.setattr(
        cl,
        "_get",
        _adp_get(
            200,
            _adp_links(locales=("en_CA", "fr_CA")),
            {"en_CA": 460, "fr_CA": 17},
            calls,
        ),
    )
    assert cl.p_adp(_ADP, _ADP_URL) == (cl.LIVE, 477)
    assert len(calls) == 3 and "%24top=1" in calls[1]


def test_adp_a_published_center_with_nothing_open_is_live_and_empty(monkeypatch):
    monkeypatch.setattr(cl, "_get", _adp_get(200, _adp_links(), {}))
    assert cl.p_adp(_ADP, _ADP_URL) == (cl.LIVE, 0)


def test_adp_an_unknown_client_is_dead(monkeypatch):
    """A `cid` ADP does not know is a 404 openresty page — the one response measured on it."""
    monkeypatch.setattr(cl, "_get", _adp_get(404, b"<html>404 Not Found</html>"))
    assert cl.p_adp(_ADP, _ADP_URL) == (cl.DEAD, None)


def test_adp_an_unpublished_career_center_is_dead(monkeypatch):
    """A `ccId` the client does not have answers 200 with `PublishedIndicator` false; its
    listing is otherwise byte-identical to an empty center's."""
    calls: list[str] = []
    monkeypatch.setattr(
        cl, "_get", _adp_get(200, _adp_links(published=False), calls=calls)
    )
    assert cl.p_adp(_ADP, _ADP_URL) == (cl.DEAD, None)
    assert len(calls) == 1


def test_adp_a_refused_or_unreadable_answer_is_unknown(monkeypatch):
    monkeypatch.setattr(
        cl, "_get", _adp_get(429, b"Request blockedExceeded requests limit.")
    )
    assert cl.p_adp(_ADP, _ADP_URL) == (cl.UNKNOWN, None)
    monkeypatch.setattr(cl, "_get", _adp_get(200, b"<html>not json</html>"))
    assert cl.p_adp(_ADP, _ADP_URL) == (cl.UNKNOWN, None)


def test_adp_host_is_seeded_at_the_scrapers_pace():
    gate = cl._GATES["workforcenow.adp.com"]
    assert gate.spacing == 0.4


def test_adp_a_listing_404_after_a_published_center_is_unknown_and_noted(monkeypatch):
    """`_get` notes every non-200 except 404/410, which settle DEAD elsewhere; here the center
    is published, so the listing's 404 is unexplained and must still leave a reason."""

    def _get(url, headers=None):
        if "content-links" in url:
            return 200, _adp_links()
        return 404, b"<html>404</html>"

    monkeypatch.setattr(cl, "_get", _get)
    cl._reasons.clear()
    assert cl.p_adp(_ADP, _ADP_URL) == (cl.UNKNOWN, None)
    assert any(reason == "listing-http-404" for _, reason in cl._reasons)


# --- cornerstone: the scraper's own walk through `_fetch` ----------------------------------------

_CSOD = json.loads(
    (_ROOT / "tests" / "fixtures" / "cornerstone_boards.json").read_text(
        encoding="utf-8"
    )
)["ama-assn"]


class _CsodResponse:
    def __init__(self, status, body="", headers=None):
        self.status_code = status
        self.text = body if isinstance(body, str) else json.dumps(body)
        self.content = self.text.encode()
        self.headers = headers or {}

    def json(self):
        return json.loads(self.content)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise CurlHTTPError(f"HTTP {self.status_code}", 0, self)


def _csod_fetch(home=200, search_status=200, calls=None):
    """`_fetch` answering ama-assn's recorded surface (tests/fixtures/cornerstone_boards.json)."""

    def fetch(method, url, **kw):
        if calls is not None:
            calls.append((method, url, kw))
        if "/home?c=" in url:
            if home == "dns":
                raise cl.http.RequestsError("Could not resolve host", 6)
            if home == 302:
                return _CsodResponse(302, "", {"location": "/ui/error"})
            return _CsodResponse(200, _CSOD["home"])
        if "/careersites/" in url:
            answer = _CSOD["careersites"].get(
                url.rsplit("/", 1)[1], {"status": 404, "body": {}}
            )
            return _CsodResponse(answer["status"], answer["body"])
        if url.endswith("rec-job-search/external/jobs"):
            if search_status != 200:
                return _CsodResponse(search_status, "")
            page = _CSOD["search"].get(str(kw["json"]["careerSitePageId"]))
            if page is None or kw["json"]["pageNumber"] > 1:
                return _CsodResponse(
                    200, {"data": {"totalCount": 0, "requisitions": []}}
                )
            return _CsodResponse(200, page)
        raise AssertionError(url)

    return fetch


def test_cornerstone_counts_the_union_of_a_boards_sites(monkeypatch):
    """ama-assn lists 3 postings on site 2 and 2 on site 3, one of them on both: 4, not 5."""
    calls = []
    monkeypatch.setattr(cl, "_fetch", _csod_fetch(calls=calls))
    assert cl.p_cornerstone("ama-assn", "https://ama-assn.csod.com") == (cl.LIVE, 4)
    assert all("timeout" not in kw for _, _, kw in calls)  # `_fetch` sets its own


def test_cornerstone_a_host_that_does_not_resolve_is_dead(monkeypatch):
    monkeypatch.setattr(cl, "_fetch", _csod_fetch(home="dns"))
    assert cl.p_cornerstone("a2dominion", "") == (cl.DEAD, None)


def test_cornerstone_a_corp_with_no_career_site_is_dead(monkeypatch):
    """Every career-site page on ids 1-3 redirects to `/ui/error` (an LMS-only corp)."""
    calls = []
    monkeypatch.setattr(cl, "_fetch", _csod_fetch(home=302, calls=calls))
    assert cl.p_cornerstone("atlascopco", "") == (cl.DEAD, None)
    assert len(calls) == 3 and all(
        kw.get("allow_redirects") is False for _, _, kw in calls
    )


def test_cornerstone_inconclusive_answers_stay_unknown(monkeypatch):
    monkeypatch.setattr(cl, "_fetch", _csod_fetch(search_status=503))
    assert cl.p_cornerstone("ama-assn", "") == (cl.UNKNOWN, None)
    monkeypatch.setattr(cl, "_fetch", lambda method, url, **kw: None)  # breaker open
    assert cl.p_cornerstone("ama-assn", "") == (cl.UNKNOWN, None)
    monkeypatch.setattr(
        cl,
        "_fetch",
        lambda method, url, **kw: _CsodResponse(200, "<html>no context</html>"),
    )
    assert cl.p_cornerstone("ama-assn", "") == (cl.UNKNOWN, None)


def test_adp_a_dns_failure_is_unknown_not_dead(monkeypatch):
    """Every ADP Board is on one fixed host, so a resolver failure names no dead tenant."""
    monkeypatch.setattr(cl, "_get", _adp_get("dns"))
    assert cl.p_adp(_ADP, _ADP_URL) == (cl.UNKNOWN, None)


def test_adp_a_center_closed_to_external_candidates_is_dead(monkeypatch):
    """The listing answers 403 "Job listing is not allowed for external candidates." for a
    center its client keeps internal: nothing on it is public."""

    def _get(url, headers=None):
        if "content-links" in url:
            return 200, _adp_links()
        return 403, b"Job listing is not allowed for external candidates."

    monkeypatch.setattr(cl, "_get", _get)
    assert cl.p_adp(_ADP, _ADP_URL) == (cl.DEAD, None)


def test_adp_an_adp_side_500_is_unknown(monkeypatch):
    """32 of 40 sampled first-pass unknowns were a 500 on both calls — re-probed, never buried."""
    monkeypatch.setattr(cl, "_get", _adp_get(500, b'{"status":500}'))
    assert cl.p_adp(_ADP, _ADP_URL) == (cl.UNKNOWN, None)
