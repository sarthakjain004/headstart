"""Tests for `headstart.scrapers.phenom`.

The fixtures are two real postings from `careers.cisco.com`, captured 2026-09-16:
`phenom_listing.json` is the `refineSearch` envelope trimmed to those two, and
`phenom_details.json` maps each id to its `jobDetail` response (with the noisy `ml_*`,
`emb_*` and `completeDescription_chunk_*` keys dropped so the fixture stays readable). They
were chosen to cover both stated workplace values — `Hybrid` and `Onsite Only` — because those
map to *different* things and one of them is not False.

Every assertion here pins something measured in
`docs/phenom/2026-09-16_widgets-api-measurement.md`, and several pin a trap the upstream
implementation this was adapted from walks into: the absent `description` key, the hardcoded
`us` prefix, and the unbounded page walk.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fake_fetcher import FakeFetcher, FakeResponse

from headstart.scrapers.phenom import _RESULT_WINDOW, PhenomScraper
from headstart.scrapers.registry import get_scraper

FIXTURES = Path(__file__).parent / "fixtures"
SCRAPED_AT = "2026-01-01T00:00:00+00:00"
HOST = "careers.cisco.com"
HYBRID_ID, ONSITE_ID = "2011157", "2006930"


def _listing() -> list[dict]:
    with open(FIXTURES / "phenom_listing.json", encoding="utf-8") as fh:
        return json.load(fh)["refineSearch"]["data"]["jobs"]


def _details() -> dict[str, dict]:
    with open(FIXTURES / "phenom_details.json", encoding="utf-8") as fh:
        return {k: v["jobDetail"]["data"]["job"] for k, v in json.load(fh).items()}


def _scraper() -> PhenomScraper:
    scraper = PhenomScraper(HOST)
    scraper._cc, scraper._lang = "us", "en"
    return scraper


def _raw() -> dict[str, Any]:
    """What `fetch_raw` returns once the detail pass has run."""
    return {"jobs": _listing(), "details": _details()}


def _jobs(raw: dict | None = None) -> dict[str, Any]:
    return {
        j.id.rsplit(":", 1)[1]: j
        for j in _scraper().parse(raw if raw is not None else _raw(), SCRAPED_AT)
    }


def test_the_scraper_is_registered_under_its_ats_name():
    assert isinstance(get_scraper("phenom", HOST), PhenomScraper)


def test_the_slug_is_the_bare_board_host():
    assert _scraper().board_key() == f"phenom:{HOST}"


def test_slug_from_prefers_the_url_host_over_a_non_host_tenant():
    # Discovery writes both shapes: a host from the fingerprint scan, a bare label from the
    # curated seed that carries the host only in `url`.
    assert PhenomScraper.slug_from("cisco", "https://careers.cisco.com/us/en") == HOST


def test_slug_from_falls_back_to_the_tenant_when_there_is_no_url():
    assert PhenomScraper.slug_from("careers.cisco.com", "") == HOST


def test_the_job_url_omits_the_cosmetic_title_slug():
    # Verified live on three tenants: `/job/{id}` renders the same posting as
    # `/job/{id}/{Title-Slug}`, and a deliberately wrong slug still resolves by id.
    assert _scraper().job_url("R-289042") == f"https://{HOST}/us/en/job/R-289042"


def test_the_job_url_carries_the_boards_own_prefix_not_a_hardcoded_us():
    # 30 of 91 reachable tenants are not `us`. A wrong prefix does not 404 — it 200s and
    # redirects to the tenant landing page — so this is the difference between a live link and
    # a dead one that looks alive.
    scraper = PhenomScraper("jobs.tjx.com")
    scraper._cc, scraper._lang = "global", "en"
    assert scraper.job_url("R2329547") == "https://jobs.tjx.com/global/en/job/R2329547"


def test_the_listing_url_carries_the_boards_own_prefix():
    scraper = PhenomScraper("jobs.bell.ca")
    scraper._cc, scraper._lang = "ca", "en"
    assert scraper.url() == "https://jobs.bell.ca/ca/en/search-results"


def test_description_comes_from_the_detail_body_never_the_listing_teaser():
    job = _jobs()[HYBRID_ID]
    teaser = next(j for j in _listing() if j["jobId"] == HYBRID_ID)["descriptionTeaser"]
    assert job.description is not None
    # The trap the upstream parser falls into: it reads `item["description"]`, which the listing
    # does not have, then falls back to the teaser — serving a marketing blurb as the body.
    assert len(job.description) > len(teaser) * 3
    assert "<" not in job.description  # html_to_text ran


def test_a_posting_with_no_detail_payload_serves_no_description_rather_than_the_teaser():
    # Falling back to the teaser would be worse than nothing: it would mark the Job described,
    # so the ADR-0050 store would never fetch the real body.
    job = _jobs({"jobs": _listing(), "details": {}})[HYBRID_ID]
    assert job.description is None


def test_hybrid_is_not_remote_and_is_not_explicitly_onsite():
    # `workday._remote_from`'s convention: hybrid is None, because calling it False overstates
    # what the Board said.
    assert _jobs()[HYBRID_ID].remote is None


def test_an_onsite_only_posting_reads_as_not_remote():
    assert _jobs()[ONSITE_ID].remote is False


def test_remote_is_none_when_the_board_states_no_workplace_field():
    listed = [
        {k: v for k, v in j.items() if k not in ("remote", "RemoteType")}
        for j in _listing()
    ]
    assert _jobs({"jobs": listed, "details": _details()})[HYBRID_ID].remote is None


def test_partial_remote_eligibility_is_hybrid_and_remote_type_is_read():
    """Honda states "Remote Eligible up to 20%" (a mostly on-site policy) and Sutter Health
    spells the key `remoteType` — both live 2026-09-22."""
    from headstart.scrapers.phenom import _remote

    assert _remote({}, {"remote": "Remote Eligible up to 20%"}) is None
    assert _remote({}, {"remote": "100% Onsite"}) is False
    assert _remote({"remoteType": "Remote"}, {}) is True
    assert _remote({"remoteType": "Onsite"}, {}) is False


def _titled(scraper, title: str):
    """Point the scraper's one `resolve_company` request at a canned page title."""

    class _Response:
        status_code = 200
        text = f"<html><head><title>{title}</title></head></html>"

    scraper._fetch = lambda *a, **k: _Response()
    return scraper


def test_the_board_page_is_the_landing_page_for_this_boards_prefix():
    assert _scraper().board_page() == f"https://{HOST}/us/en"


def test_the_company_is_read_from_the_board_page_title():
    scraper = _titled(_scraper(), "Careers at Cisco")
    scraper.resolve_company()
    assert scraper.company == "Cisco"


def test_a_title_with_a_second_clause_does_not_swallow_it_into_the_name():
    # "Careers at Zelis | Zelis Jobs" — `_CAREERS_WRAPPER`'s `$`-anchored group would take the
    # whole tail, which is why phenom has its own patterns.
    scraper = _titled(
        PhenomScraper("careers.zelis.com"), "Careers at Zelis | Zelis Jobs"
    )
    scraper.resolve_company()
    assert scraper.company == "Zelis"


def test_a_colon_tagline_is_not_part_of_the_name():
    scraper = _titled(
        PhenomScraper("careers.omnicable.com"), "OmniCable Careers: Play to Win"
    )
    scraper.resolve_company()
    assert scraper.company == "OmniCable"


def test_an_unwrapped_title_leaves_the_slug_alone_rather_than_guessing():
    # `Home | BAE Systems` states no wrapper this ATS uses; ADR-0114's floor is that a slug is
    # never replaced by a non-name.
    scraper = _titled(PhenomScraper("jobs.baesystems.com"), "Home | BAE Systems")
    scraper.resolve_company()
    assert scraper.company == "jobs.baesystems.com"


def test_a_board_already_carrying_a_real_name_keeps_it():
    # ADR-0114: a slug is only ever replaced, never the reverse.
    scraper = _titled(PhenomScraper(HOST, company="Cisco Systems"), "Careers at Cisco")
    scraper.resolve_company()
    assert scraper.company == "Cisco Systems"


def test_the_company_survives_a_run_that_fetches_no_details():
    """ADR-0048 skips the detail fetch for a Job we already hold, so a steady-state Board
    fetches none. The name must not depend on one having been fetched."""
    scraper = _titled(_scraper(), "Careers at Cisco")
    scraper.parse({"jobs": _listing(), "details": {}}, SCRAPED_AT)
    scraper.resolve_company()
    assert scraper.company == "Cisco"


def test_the_core_fields_come_from_the_listing():
    job = _jobs()[HYBRID_ID]
    listed = next(j for j in _listing() if j["jobId"] == HYBRID_ID)
    assert job.title == listed["title"]
    assert job.department == listed["category"]
    assert job.employment_type == listed["type"]
    assert job.posted_at == listed["postedDate"]
    assert job.ats == "phenom"


def test_a_row_with_no_native_id_is_skipped_rather_than_served_a_broken_link():
    jobs = _scraper().parse({"jobs": [{"title": "No id"}], "details": {}}, SCRAPED_AT)
    assert jobs == []


def test_no_salary_is_claimed_from_a_tenant_specific_field():
    # 43 of 91 tenants carry *some* salary-ish key and not one is usable: `salary: "Salary"`,
    # `payment: "0"`, `compensationGrade: "C5"`. Tier-2 regex reads the prose ones out of the
    # description instead.
    assert _scraper()._salary_field(_raw()) is None
    assert all(j.salary is None for j in _jobs().values())


def test_the_page_walk_stops_at_the_result_window(monkeypatch):
    """A Board bigger than the window is read to the edge and reported truncated.

    Past `from + size >= 10000` the endpoint answers 200 with an empty page **and
    `totalHits: 0`**, so a walk that re-read the total each page would call a 19,649-posting
    Board finished at 9,500. The total is therefore read once, from page one.
    """
    scraper = _scraper()
    calls: list[tuple[int, int]] = []

    def fake(payload):
        start, size = payload["from"], payload["size"]
        calls.append((start, size))
        if start + size >= _RESULT_WINDOW:
            return {"refineSearch": {"totalHits": 0, "data": {"jobs": []}}}
        rows = [{"jobId": f"J{start + i}"} for i in range(size)]
        return {"refineSearch": {"totalHits": 10339, "data": {"jobs": rows}}}

    monkeypatch.setattr(scraper, "_widgets", fake)
    jobs = scraper._listing()
    assert len(jobs) == _RESULT_WINDOW - 1  # the last reachable index is 9,998
    assert scraper.truncated is not None
    assert "10339" in scraper.truncated
    assert all(start + size < _RESULT_WINDOW for start, size in calls)


def test_a_board_only_just_over_the_window_is_still_truncated(monkeypatch):
    """The hard cap is unconditional, and this is the case that proves it is.

    At `totalHits` just over the window the crawl reads 9,999 of 10,050 — **99.5%**, above
    `MIN_AUTHORITATIVE_SHARE` — so the tolerant ADR-0121 verdict would wave it through. It must
    not: those 51 postings are unreachable, not merely unread, and letting the Board stay
    authoritative feeds them to eviction. (The 10,339 case above cannot pin this: 96.7% is under
    the threshold, so both verdicts agree there and swapping them leaves the suite green.)
    """
    scraper = _scraper()

    def fake(payload):
        start, size = payload["from"], payload["size"]
        if start + size >= _RESULT_WINDOW:
            return {"refineSearch": {"totalHits": 0, "data": {"jobs": []}}}
        rows = [{"jobId": f"J{start + i}"} for i in range(size)]
        return {"refineSearch": {"totalHits": 10_050, "data": {"jobs": rows}}}

    monkeypatch.setattr(scraper, "_widgets", fake)
    jobs = scraper._listing()
    assert len(jobs) == _RESULT_WINDOW - 1
    assert (
        len(jobs) / 10_050 > 0.99
    )  # the tolerant verdict would NOT have truncated this
    assert scraper.truncated is not None
    assert "result window" in scraper.truncated


def test_a_board_inside_the_window_is_not_called_truncated(monkeypatch):
    scraper = _scraper()

    def fake(payload):
        start, size = payload["from"], payload["size"]
        rows = [{"jobId": f"J{start + i}"} for i in range(min(size, 88 - start))]
        return {"refineSearch": {"totalHits": 88, "data": {"jobs": rows}}}

    monkeypatch.setattr(scraper, "_widgets", fake)
    assert len(scraper._listing()) == 88
    assert scraper.truncated is None


def test_a_walk_that_ends_short_of_the_stated_total_is_reported(monkeypatch):
    """A shortfall inside the window is measurable, so it goes to the ADR-0121 tolerant verdict.

    Without this the Board would look complete and `index sync` would read the difference as
    delistings — the failure ADR-0053 exists to prevent.
    """
    scraper = _scraper()

    def fake(payload):
        # states 500, serves 50 and then nothing
        rows = [{"jobId": f"J{i}"} for i in range(50)] if payload["from"] == 0 else []
        return {"refineSearch": {"totalHits": 500, "data": {"jobs": rows}}}

    monkeypatch.setattr(scraper, "_widgets", fake)
    assert len(scraper._listing()) == 50
    assert scraper.truncated is not None
    assert "50 of 500" in scraper.truncated


def test_a_negligible_shortfall_leaves_the_board_authoritative(monkeypatch):
    """At or above MIN_AUTHORITATIVE_SHARE the list stays authoritative and the missing ids
    fall to ADR-0083's per-Job grace period instead of excluding the whole Board."""
    scraper = _scraper()

    def fake(payload):
        rows = [{"jobId": f"J{i}"} for i in range(999)] if payload["from"] == 0 else []
        return {"refineSearch": {"totalHits": 1000, "data": {"jobs": rows}}}

    monkeypatch.setattr(scraper, "_widgets", fake)
    assert len(scraper._listing()) == 999
    assert scraper.truncated is None


def test_a_repeated_row_is_not_served_twice(monkeypatch):
    """The walk is an offset crawl over a live index, so a posting added mid-walk shifts every
    later page and re-serves a row already read."""
    scraper = _scraper()
    pages = [
        [{"jobId": "A"}, {"jobId": "B"}],
        [{"jobId": "B"}, {"jobId": "C"}],
        [],
    ]

    def fake(payload):
        page = pages.pop(0) if pages else []
        return {"refineSearch": {"totalHits": 4, "data": {"jobs": page}}}

    monkeypatch.setattr(scraper, "_widgets", fake)
    assert [str(j["jobId"]) for j in scraper._listing()] == ["A", "B", "C"]


def _recorded_detail_responses() -> dict[str, dict]:
    """Each fixture posting's whole `jobDetail` response, keyed by its id."""
    with open(FIXTURES / "phenom_details.json", encoding="utf-8") as fixture_file:
        return json.load(fixture_file)


def _serve_fixture_board(
    detail_answers: dict[str, dict],
) -> tuple[PhenomScraper, FakeFetcher]:
    """The two fixture postings behind one `/widgets` endpoint: the listing POST answers them as a
    whole Board, and each detail POST answers from ``detail_answers`` by the `jobId` in its body."""
    listed = _listing()

    def route(method: str, url: str, kwargs: dict) -> FakeResponse:
        if method == "GET":  # the locale-prefix probe, answered where it was asked
            return FakeResponse(text="<html></html>")
        payload = kwargs["json"]
        if payload["ddoKey"] == "refineSearch":
            rows = listed if payload["from"] == 0 else []
            envelope = {"totalHits": len(listed), "data": {"jobs": rows}}
            return FakeResponse(text=json.dumps({"refineSearch": envelope}))
        return FakeResponse(text=json.dumps(detail_answers[payload["jobId"]]))

    fetcher = FakeFetcher(route)
    return PhenomScraper(HOST, fetcher=fetcher), fetcher


def _detail_posts(fetcher: FakeFetcher) -> list[Any]:
    return [
        request
        for request in fetcher.requests
        if request.method == "POST" and request.kwargs["json"]["ddoKey"] == "jobDetail"
    ]


@pytest.mark.parametrize("async_fanout_switch", ["1", "0"])
def test_the_detail_pass_posts_one_job_detail_body_per_posting_on_either_transport(
    monkeypatch, async_fanout_switch
):
    """Both transports send the same request: a POST to `/widgets` with the `jobDetail` payload,
    the widget headers and the 45 s timeout the listing POST uses."""
    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", async_fanout_switch)
    scraper, fetcher = _serve_fixture_board(_recorded_detail_responses())

    raw = scraper.fetch_raw()

    assert raw["details"] == _details()
    detail_posts = _detail_posts(fetcher)
    assert sorted(post.kwargs["json"]["jobId"] for post in detail_posts) == sorted(
        [HYBRID_ID, ONSITE_ID]
    )
    for post in detail_posts:
        assert post.url == f"https://{HOST}/widgets"
        assert post.kwargs["headers"] == PhenomScraper._WIDGET_HEADERS
        assert post.kwargs["timeout"] == PhenomScraper._WIDGET_TIMEOUT == 45
        assert post.kwargs["json"]["pageName"] == "job-details"
    assert scraper.detail_losses == {}


def test_a_held_description_is_not_fetched_again(monkeypatch):
    """ADR-0048: everything else the detail supplies the listing also states, so a Job whose
    description the store holds is skipped rather than re-fetched. The tech gate is switched off
    so the skip alone decides what is fetched."""
    monkeypatch.setenv("HEADSTART_TECH_GATE", "0")
    scraper, fetcher = _serve_fixture_board(_recorded_detail_responses())
    scraper.have_details = {f"phenom:{HOST}:{ONSITE_ID}"}

    raw = scraper.fetch_raw()

    assert [post.kwargs["json"]["jobId"] for post in _detail_posts(fetcher)] == [
        HYBRID_ID
    ]
    assert set(raw["details"]) == {HYBRID_ID}


def test_an_unknown_id_records_a_detail_loss_rather_than_raising():
    # A bogus id is a 200 whose envelope simply has no `job` key — a silent empty, not an error.
    no_job = {"jobDetail": {"status": 200, "data": {}}}
    scraper, _fetcher = _serve_fixture_board({HYBRID_ID: no_job, ONSITE_ID: no_job})
    raw = scraper.fetch_raw()
    assert raw["details"] == {}
    assert scraper.detail_losses == {"no job on a 200": 2}


def test_the_prefix_probe_falls_back_when_the_board_will_not_answer(monkeypatch):
    """A failed probe costs link accuracy on that Board, not its postings."""
    from headstart import http

    scraper = _scraper()

    def boom(*args, **kwargs):
        raise http.RequestsError("nope")

    monkeypatch.setattr(scraper, "_fetch", boom)
    assert scraper._prefix() == ("us", "en")


def test_the_prefix_is_read_off_the_url_the_board_redirects_to(monkeypatch):
    scraper = _scraper()
    monkeypatch.setattr(
        scraper,
        "_fetch",
        lambda *a, **k: type(
            "R", (), {"url": "https://jobs.tjx.com/global/en/search-results"}
        )(),
    )
    assert scraper._prefix() == ("global", "en")
