"""Tests for `headstart.scrapers.adp_recruiting` (ADP Recruiting Management, `myjobs.adp.com`).

`fixtures/adp_recruiting_responses.json` holds live responses captured 2026-09-24, trimmed (never
invented): site records cut to the keys the scraper and prober read, descriptions to 600 chars,
the detail's `meta` block dropped.

- Church Mutual (`churchmutual`) states 19 postings: a page of 10 at `$skip=0` and 9 at
  `$skip=10`, walked with the page size patched to 10. Its detail for 5001222115706 carries
  `compensationDetails` "107,000 to 160,400".
- Follett (`corpfollettexternal`)'s detail for 5001213128706 carries the pay-transparency
  amounts 40,000 / 141,700 USD.
- `detail_closed` is a detail for an id the Board does not hold (400 "Bad Request");
  `site_not_found` / `site_not_active` are two real departed sites' records;
  `site_taherinternal` is an employee-only career site.

Every assertion pins something measured in `docs/adp_recruiting/2026-09-24_myjobs-measurement.md`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from headstart.scrapers import adp_recruiting
from headstart.scrapers.adp_recruiting import ADPRecruitingScraper
from headstart.scrapers.registry import get_scraper

FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "adp_recruiting_responses.json").read_text(
        "utf-8"
    )
)
SCRAPED_AT = "2026-01-01T00:00:00+00:00"
ROWS = (
    FIXTURES["churchmutual_listing_top10_skip0"]["jobRequisitions"]
    + FIXTURES["churchmutual_listing_top10_skip10"]["jobRequisitions"]
)
TOKEN = FIXTURES["site_churchmutual"]["myJobsToken"]


def _jobs(raw: dict, slug: str = "churchmutual") -> dict:
    scraper = get_scraper("adp_recruiting", slug, slug)
    return {j.id.rsplit(":", 1)[1]: j for j in scraper.parse(raw, SCRAPED_AT)}


def _detail(key: str) -> dict:
    return FIXTURES[key]["jobRequisitions"][0]


def test_a_listing_row_and_its_detail_become_a_job():
    jobs = _jobs(
        {
            "rows": ROWS,
            "details": {"5001222115706": _detail("churchmutual_detail_5001222115706")},
        }
    )
    assert len(jobs) == 19
    job = jobs["5001222115706"]
    assert job.id == "adp_recruiting:churchmutual:5001222115706"
    assert job.ats == "adp_recruiting"
    assert job.title == "Manager - Quality Assurance & Engineering"
    assert job.department == "Information Technology"
    assert job.location == "Merrill, Wisconsin, United States"
    assert job.remote is False
    assert job.employment_type == "Full-time"
    assert job.posted_at == "2026-09-02T20:50:06Z"
    assert job.url == (
        "https://myjobs.adp.com/churchmutual/cx/job-details?reqId=5001222115706"
    )
    assert job.description
    assert job.salary == "107,000 to 160,400"


def test_every_location_is_joined_as_the_site_renders_it():
    """Three offices, each "City, State, Country"; an address with only a country keeps it."""
    jobs = _jobs({"rows": ROWS, "details": {}})
    assert jobs["5001225467006"].location == (
        "Milwaukee, Wisconsin, United States; Merrill, Wisconsin, United States; "
        "Madison, Wisconsin, United States"
    )
    assert jobs["5001219701706"].location == "United States"


def test_an_empty_address_falls_back_to_the_location_name():
    row = {
        **ROWS[0],
        "requisitionLocations": [
            {"address": {"cityName": ""}, "nameCode": {"longName": "Telework"}},
        ],
    }
    (job,) = _jobs({"rows": [row], "details": {}}).values()
    assert job.location == "Telework"


def test_remote_reads_the_location_name_as_well_as_its_address():
    """A tenant's "Remote" location carries a real address as often as not."""
    row = {
        **ROWS[0],
        "requisitionLocations": [
            {
                "address": {
                    "cityName": "Work from home",
                    "countrySubdivisionLevel1": {"longName": "Virginia"},
                    "country": {"longName": "United States"},
                },
                "nameCode": {"codeValue": "TC", "longName": "Remote"},
            }
        ],
    }
    (job,) = _jobs({"rows": [row], "details": {}}).values()
    assert job.location == "Work from home, Virginia, United States"
    assert job.remote is True


def test_the_qualifications_block_is_appended_unless_the_description_holds_it():
    row = {
        **ROWS[0],
        "jobDescription": "<p>About us.</p>",
        "jobQualifications": "<p>Python.</p>",
    }
    (job,) = _jobs({"rows": [row], "details": {}}).values()
    assert job.description == "About us.\n\nPython."
    row = {**row, "jobDescription": "<p>About us. Python.</p>"}
    (job,) = _jobs({"rows": [row], "details": {}}).values()
    assert job.description == "About us. Python."


def test_a_hidden_posting_date_stays_hidden():
    row = {k: v for k, v in ROWS[0].items() if k != "postingDate"}
    (job,) = _jobs({"rows": [row], "details": {}}).values()
    assert job.posted_at is None


# ------------------------------------------------------------------------------------- salary


def test_pay_transparency_amounts_win_and_carry_their_currency():
    scraper = get_scraper("adp_recruiting", "corpfollettexternal")
    detail = _detail("corpfollettexternal_detail_5001213128706")
    assert scraper._salary_field(detail) == "40000-141700 USD"


def test_the_compensation_string_is_passed_through_as_stated():
    from headstart import salary

    scraper = get_scraper("adp_recruiting", "churchmutual")
    stated = scraper._salary_field(_detail("churchmutual_detail_5001222115706"))
    assert stated == "107,000 to 160,400"
    span = salary.extract(stated, None, ats="adp_recruiting")
    assert span is not None and span.min_annual == 107000


def test_a_lone_pay_bound_falls_back_to_the_string_and_no_detail_is_no_salary():
    scraper = get_scraper("adp_recruiting", "churchmutual")
    detail = {
        "customFieldGroup": {
            "amountFields": [
                {
                    "amountValue": 40000,
                    "currencyCode": "USD",
                    "categoryCode": {"codeValue": adp_recruiting._PAY_MIN},
                }
            ]
        }
    }
    assert scraper._salary_field(detail) is None
    assert scraper._salary_field(None) is None


# ----------------------------------------------------------------------------------- identity


def test_the_slug_is_the_lowercased_path_word():
    assert ADPRecruitingScraper.slug_from("ChurchMutual", "") == "churchmutual"
    assert ADPRecruitingScraper.slug_from("pathgroup.", "") == "pathgroup"


def test_the_job_url_matches_the_declared_shape():
    scraper = get_scraper("adp_recruiting", "800flowerscareers")
    assert re.fullmatch(
        ADPRecruitingScraper.url_shape, scraper.job_url("5001227284300")
    )


def test_the_company_is_the_site_records_client_name_at_no_extra_request(monkeypatch):
    scraper = get_scraper("adp_recruiting", "churchmutual", "churchmutual")
    scraper._client_name = FIXTURES["site_churchmutual"]["clientName"]
    monkeypatch.setattr(scraper, "_fetch", pytest.fail)
    scraper.resolve_company()
    assert scraper.company == FIXTURES["site_churchmutual"]["clientName"]


def test_adp_itself_is_a_client_name_like_any_other():
    scraper = get_scraper("adp_recruiting", "apply", "apply")
    scraper._client_name = "ADP"
    scraper.resolve_company()
    assert scraper.company == "ADP"


# ------------------------------------------------------------------------------ the network half


class _Resp:
    def __init__(self, status: int, body: object):
        self.status_code = status
        self.text = body if isinstance(body, str) else json.dumps(body)

    def raise_for_status(self):
        if self.status_code >= 400:
            from headstart import http

            # The shape curl_cffi raises: the settled response rides on the exception.
            raise http.RequestsError(f"HTTP {self.status_code}", response=self)


class _FakeADP:
    """Routes the scraper's GETs to recorded fixtures by path and query, and records each."""

    def __init__(self, pages: dict, details: dict | None = None, too_big: int = 0):
        self.pages = pages  # skip -> body
        self.details = details or {}  # reqId -> body
        self.too_big = too_big  # answer 502 for any page asking more rows than this
        self.calls: list[tuple[str, dict, dict]] = []

    def __call__(self, method, url, **kwargs):
        from urllib.parse import parse_qs, urlsplit

        parts = urlsplit(url)
        q = {k: v[0] for k, v in parse_qs(parts.query).items()}
        self.calls.append((parts.path, q, kwargs.get("headers") or {}))
        if "/career-site/" in parts.path:
            return _Resp(200, FIXTURES["site_churchmutual"])
        if parts.path.endswith("apply-custom-filters"):
            if self.too_big and int(q["$top"]) > self.too_big:
                return _Resp(502, "<html>502 Bad Gateway</html>")
            return _Resp(200, self.pages[int(q["$skip"])])
        req_id = parts.path.rsplit("/", 1)[1]
        if req_id in self.details:
            return _Resp(200, self.details[req_id])
        return _Resp(400, FIXTURES["detail_closed"])


def _church_pages() -> dict:
    return {
        0: FIXTURES["churchmutual_listing_top10_skip0"],
        10: FIXTURES["churchmutual_listing_top10_skip10"],
    }


def _wired(monkeypatch, fake: _FakeADP, page: int = 10) -> ADPRecruitingScraper:
    scraper = get_scraper("adp_recruiting", "churchmutual", "churchmutual")
    monkeypatch.setattr(scraper, "_fetch", fake)
    monkeypatch.setattr(adp_recruiting, "_PAGE", page)
    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", "0")
    return scraper


def test_the_walk_is_zero_based_until_the_stated_count_with_the_sites_token(
    monkeypatch,
):
    fake = _FakeADP(_church_pages())
    raw = _wired(monkeypatch, fake).fetch_raw()
    assert [r["reqId"] for r in raw["rows"]] == [r["reqId"] for r in ROWS]
    listing = [c for c in fake.calls if c[0].endswith("apply-custom-filters")]
    assert [c[1]["$skip"] for c in listing] == ["0", "10"]
    assert all(c[2]["myjobstoken"] == TOKEN for c in listing)
    assert "jobDescription" in listing[0][1]["$select"]


def test_a_page_too_big_for_the_host_is_asked_again_smaller(monkeypatch):
    """A page past ~1 MB answers 502; the walk halves the page rather than retrying it, and
    asks the full page size again for the next page."""
    fake = _FakeADP(_church_pages(), too_big=10)
    raw = _wired(monkeypatch, fake, page=20).fetch_raw()
    assert len(raw["rows"]) == 19
    tops = [c[1]["$top"] for c in fake.calls if c[0].endswith("apply-custom-filters")]
    assert tops == ["20", "10", "20", "10"]


def test_a_page_still_refused_at_the_floor_truncates_the_board(monkeypatch):
    """Halving stops at `_MIN_PAGE` (5): 10 -> 5, and a 5-row page still refused gives up."""
    fake = _FakeADP(_church_pages(), too_big=1)
    scraper = _wired(monkeypatch, fake, page=10)
    raw = scraper.fetch_raw()
    assert raw["rows"] == []
    tops = [c[1]["$top"] for c in fake.calls if c[0].endswith("apply-custom-filters")]
    assert tops == ["10", "5"]
    assert scraper.truncated and "a 5-row page" in scraper.truncated


def test_the_page_cap_truncates_once_with_its_own_reason(monkeypatch):
    fake = _FakeADP(_church_pages())
    scraper = _wired(monkeypatch, fake)
    monkeypatch.setattr(adp_recruiting, "_MAX_PAGES", 1)
    raw = scraper.fetch_raw()
    assert len(raw["rows"]) == 10
    assert scraper.truncated == "hit the 1-page cap at 10 of 19 postings"


def test_a_site_record_with_no_token_is_an_unreadable_board(monkeypatch, caplog):
    scraper = get_scraper("adp_recruiting", "churchmutual", "churchmutual")
    record = {
        k: v for k, v in FIXTURES["site_churchmutual"].items() if k != "myJobsToken"
    }
    monkeypatch.setattr(scraper, "_fetch", lambda *a, **k: _Resp(200, record))
    with caplog.at_level("INFO"):
        assert scraper.fetch_raw() == {"rows": [], "details": {}}
    assert "expected a site record with a myJobsToken" in caplog.text


def test_a_walk_short_of_the_stated_count_is_marked_truncated(monkeypatch):
    fake = _FakeADP(
        {
            0: FIXTURES["churchmutual_listing_top10_skip0"],
            10: {"count": 19, "jobRequisitions": []},
        }
    )
    scraper = _wired(monkeypatch, fake)
    raw = scraper.fetch_raw()
    assert len(raw["rows"]) == 10
    assert scraper.truncated and "read 10 of 19" in scraper.truncated


def test_the_async_detail_pass_reads_the_same_fields(monkeypatch):
    """The default fan-out is the multiplexed one; it asks with the token and reads the pay."""
    import asyncio

    scraper = get_scraper("adp_recruiting", "churchmutual", "churchmutual")
    seen: list[dict] = []

    async def fetch_async(session, method, url, **kwargs):
        seen.append(kwargs["headers"])
        if url.endswith("/5001222115706"):
            return _Resp(200, FIXTURES["churchmutual_detail_5001222115706"])
        return _Resp(400, FIXTURES["detail_closed"])

    monkeypatch.setattr(scraper, "_fetch_async", fetch_async)
    found = asyncio.run(scraper._detail_async(None, TOKEN, "5001222115706"))
    closed = asyncio.run(scraper._detail_async(None, TOKEN, "5009999999900"))
    assert scraper._salary_field(found) == "107,000 to 160,400"
    assert closed is None and scraper.detail_losses == {"HTTP 400": 1}
    assert all(h["myjobstoken"] == TOKEN for h in seen)
    assert all(h["Accept-Language"] == "en-US" for h in seen)


def test_an_abbreviated_work_level_is_labelled_for_the_filter():
    from headstart import employment_type

    cases = {
        "PT 129 or Less Hours": "Part-time (PT 129 or Less Hours)",
        "FT": "Full-time (FT)",
        "Regular FT": "Full-time (Regular FT)",
        "Full-time": "Full-time",
        "Variable": "Variable",
        "Software": "Software",  # no whole-word FT/PT
    }
    for stated, served in cases.items():
        (job,) = _jobs(
            {"rows": [{**ROWS[0], "workLevelCode": stated}], "details": {}}
        ).values()
        assert job.employment_type == served
    assert employment_type.flags("Part-time (PT 129 or Less Hours)")["is_part_time"]


def test_a_posting_served_twice_across_pages_counts_once(monkeypatch):
    page2 = dict(FIXTURES["churchmutual_listing_top10_skip10"])
    page2["jobRequisitions"] = [ROWS[9], *page2["jobRequisitions"]]
    fake = _FakeADP(
        {
            0: FIXTURES["churchmutual_listing_top10_skip0"],
            10: page2,
            20: {"count": 19, "jobRequisitions": []},
        }
    )
    scraper = _wired(monkeypatch, fake)
    raw = scraper.fetch_raw()
    assert len(raw["rows"]) == 19
    assert scraper.truncated is None


def test_the_tech_gate_picks_the_details_and_a_closed_one_ships_without_salary(
    monkeypatch,
):
    """The gate is exact: title and department off the listing. A closed detail answers 400;
    its Job still ships, and every detail request carries the token."""
    monkeypatch.delenv("HEADSTART_TECH_GATE", raising=False)
    fake = _FakeADP(
        _church_pages(),
        details={"5001222115706": FIXTURES["churchmutual_detail_5001222115706"]},
    )
    scraper = _wired(monkeypatch, fake)
    scraper.have_details = frozenset()
    raw = scraper.fetch_raw()
    asked = {c[0].rsplit("/", 1)[1] for c in fake.calls if "/search-meta/" in c[0]}
    assert (
        "5001222115706" in asked and "5001218033006" in asked
    )  # QA manager, network engineer
    assert "5001222163806" not in asked  # Customer Service Assistant
    assert all(
        c[2]["myjobstoken"] == TOKEN for c in fake.calls if "/search-meta/" in c[0]
    )
    assert set(raw["details"]) == {"5001222115706"}
    jobs = {j.id.rsplit(":", 1)[1]: j for j in scraper.parse(raw, SCRAPED_AT)}
    assert len(jobs) == 19
    assert jobs["5001218033006"].salary is None
    assert sum(scraper.detail_losses.values()) == len(asked) - 1
