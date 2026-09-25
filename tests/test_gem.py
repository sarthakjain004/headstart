"""Tests for `headstart.scrapers.gem`.

The fixtures are four real postings from `jobs.gem.com/shef-com`, captured 2026-09-16:
`gem_listing.json` is the `JobBoardList` response, `gem_details.json` maps each `extId` to its
`ExternalJobPostingQuery` response. Chosen because the board's four postings cover all three
`locationType` values (`REMOTE`, `HYBRID` x2, `IN_OFFICE`), one multi-location posting, and one
with a real `compensationHtml`.

Every assertion here pins something measured in
`docs/gem/2026-09-16_graphql-api-measurement.md`, including a trap the upstream implementation
this was adapted from (kalil0321/ats-scrapers) falls into: preferring `startDateTs` as a
posted-date fallback, which is measured always-null for anything the public listing can see.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from headstart.scrapers.gem import GemScraper
from headstart.scrapers.registry import get_scraper

FIXTURES = Path(__file__).parent / "fixtures"
SCRAPED_AT = "2026-01-01T00:00:00+00:00"
SLUG = "shef-com"
REMOTE_ID = "am9icG9zdDrr7FZuqmRcmF9aI406HpQ4"  # "Personal Chef"
HYBRID_ID = "am9icG9zdDpfHfomMwhgi_z8_5e1eD4l"  # "Director, Customer Acquisition"
MULTI_LOC_ID = (
    "am9icG9zdDpu4lUutmIsb6ynrBBgrclm"  # "Senior Full-Stack Software Engineer"
)
IN_OFFICE_ID = "am9icG9zdDq3Tt17SHpzYWdSYen2UONp"  # "Brand Ambassador (NYC Metro Area)"


def _listing() -> list[dict]:
    with open(FIXTURES / "gem_listing.json", encoding="utf-8") as fh:
        return json.load(fh)["jobPostings"]


def _details() -> dict[str, dict]:
    with open(FIXTURES / "gem_details.json", encoding="utf-8") as fh:
        return json.load(fh)


def _scraper() -> GemScraper:
    return GemScraper(SLUG)


def _raw(details: dict | None = None) -> dict[str, Any]:
    """What `fetch_raw` returns once the detail pass has run."""
    return {"jobs": _listing(), "details": _details() if details is None else details}


def _jobs(raw: dict | None = None) -> dict[str, Any]:
    return {
        j.id.rsplit(":", 1)[1]: j
        for j in _scraper().parse(raw if raw is not None else _raw(), SCRAPED_AT)
    }


def test_the_scraper_is_registered_under_its_ats_name():
    assert isinstance(get_scraper("gem", SLUG), GemScraper)


def test_the_slug_is_the_bare_board_path():
    assert _scraper().board_key() == f"gem:{SLUG}"


def test_url_is_the_board_page():
    assert _scraper().url() == f"https://jobs.gem.com/{SLUG}"


def test_board_page_matches_url():
    scraper = _scraper()
    assert scraper.board_page() == scraper.url()


def test_the_job_url_is_slug_plus_ext_id():
    assert _scraper().job_url(REMOTE_ID) == f"https://jobs.gem.com/{SLUG}/{REMOTE_ID}"


def test_url_shape_matches_every_job_url_this_scraper_produces():
    import re

    assert re.fullmatch(GemScraper.url_shape, _scraper().job_url(REMOTE_ID))
    # extIds are sometimes plain UUIDs (dashes), sometimes the opaque base64-shaped form — both
    # must match, since the shape auto-generates verify_filters.py's URL_SHAPES (ADR-0157).
    assert re.fullmatch(
        GemScraper.url_shape, _scraper().job_url("550e8400-e29b-41d4-a716-446655440000")
    )


def test_a_row_with_no_ext_id_is_skipped_rather_than_served_a_broken_link():
    jobs = _scraper().parse({"jobs": [{"title": "No id"}], "details": {}}, SCRAPED_AT)
    assert jobs == []


# --- remote: locationType outranks the per-location isRemote flag -------------------------------


def test_remote_type_reads_true():
    # Real fixture: this posting's own location states isRemote=False, yet locationType=REMOTE is
    # what's real (measured: 9.0% of a 3,533-posting sample disagree this way, and titles confirm
    # locationType is the one telling the truth).
    job = _jobs()[REMOTE_ID]
    assert job.remote is True


def test_hybrid_type_reads_none_not_false():
    # Matches phenom._remote's convention: hybrid is neither remote nor confidently on-site.
    assert _jobs()[HYBRID_ID].remote is None


def test_in_office_type_reads_false():
    assert _jobs()[IN_OFFICE_ID].remote is False


def test_remote_falls_back_to_is_remote_flag_when_location_type_absent():
    listed = [
        {**j, "job": {**j["job"], "locationType": None}}
        for j in _listing()
        if j["extId"] == REMOTE_ID
    ]
    # No detail fetched either, so `parse()`'s `job_obj.get("locationType") or
    # detail_job.get("locationType")` has nothing to fall through to but the fallback path.
    job = _jobs({"jobs": listed, "details": {}})[REMOTE_ID]
    # This fixture's own location states isRemote=False, so the fallback correctly reads False
    # once locationType itself is unavailable — a different (and, per the measurement, less
    # reliable) answer than locationType gave, which is exactly why locationType is preferred.
    assert job.remote is False


def test_remote_is_none_with_no_location_type_and_no_locations():
    jobs = _scraper().parse(
        {
            "jobs": [{"extId": "x1", "title": "T", "job": {}, "locations": []}],
            "details": {},
        },
        SCRAPED_AT,
    )
    assert jobs[0].remote is None


# --- location: first location, name preferred over city/isoCountry ------------------------------


def test_location_uses_the_first_locations_own_name():
    assert _jobs()[HYBRID_ID].location == "New York"


def test_location_with_multiple_locations_uses_the_first_only():
    job = _jobs()[MULTI_LOC_ID]
    listed = next(j for j in _listing() if j["extId"] == MULTI_LOC_ID)
    assert job.location == listed["locations"][0]["name"] == "San Francisco"


def test_location_falls_back_to_city_country_join_when_name_is_blank():
    jobs = _scraper().parse(
        {
            "jobs": [
                {
                    "extId": "x1",
                    "title": "T",
                    "job": {},
                    "locations": [
                        {
                            "name": "",
                            "city": "Berlin",
                            "isoCountry": "DEU",
                            "isRemote": False,
                        }
                    ],
                }
            ],
            "details": {},
        },
        SCRAPED_AT,
    )
    assert jobs[0].location == "Berlin, DEU"


def test_location_is_none_with_no_locations():
    jobs = _scraper().parse(
        {
            "jobs": [{"extId": "x1", "title": "T", "job": {}, "locations": []}],
            "details": {},
        },
        SCRAPED_AT,
    )
    assert jobs[0].location is None


# --- core listing-sourced fields ------------------------------------------------------------


def test_title_department_and_employment_type_come_from_the_listing():
    job = _jobs()[HYBRID_ID]
    listed = next(j for j in _listing() if j["extId"] == HYBRID_ID)
    assert job.title == listed["title"] == "Director, Customer Acquisition"
    assert job.department == listed["job"]["department"]["name"] == "Marketing"
    assert job.ats == "gem"


def test_department_is_none_when_the_listing_states_none():
    assert _jobs()[REMOTE_ID].department is None


def test_core_fields_still_populate_when_the_detail_fetch_is_missing():
    # Unlike description/posted_at/salary, title/location/remote/department/employment_type all
    # come from the listing — a Job whose detail pass failed keeps them (see the module docstring
    # on why gem always fetches detail anyway, but this is what makes that choice not load-bearing
    # for these particular fields).
    job = _jobs({"jobs": _listing(), "details": {}})[HYBRID_ID]
    assert job.title == "Director, Customer Acquisition"
    assert job.department == "Marketing"
    assert job.remote is None  # HYBRID


# --- detail-only fields: description, posted_at, salary -----------------------------------------


def test_description_comes_from_the_detail_body():
    job = _jobs()[HYBRID_ID]
    assert job.description is not None
    assert "<" not in job.description  # html_to_text ran
    assert len(job.description) > 100


def test_description_is_none_when_the_detail_fetch_is_missing():
    job = _jobs({"jobs": _listing(), "details": {}})[HYBRID_ID]
    assert job.description is None


def test_posted_at_reads_first_published_ts_sec():
    assert _jobs()[REMOTE_ID].posted_at == "2025-05-14T17:07:57+00:00"


def test_posted_at_is_none_without_a_detail_fetch():
    # startDateTs is NOT used as a fallback — measured 0/159 populated across the real sample
    # (see the module docstring); it is not even requested by the detail query.
    job = _jobs({"jobs": _listing(), "details": {}})[REMOTE_ID]
    assert job.posted_at is None


def test_salary_field_is_the_stripped_compensation_html():
    job = _jobs()[MULTI_LOC_ID]
    assert job.salary is not None
    assert "<" not in job.salary
    assert "150,000" in job.salary


def test_salary_field_is_none_when_the_posting_states_none():
    assert _jobs()[HYBRID_ID].salary is None


def test_salary_field_reads_from_the_raw_detail_dict_directly():
    scraper = _scraper()
    assert scraper._salary_field(None) is None
    assert scraper._salary_field({}) is None
    assert (
        scraper._salary_field({"compensationHtml": "<div>$1 – $2 per year</div>"})
        == "$1 – $2 per year"
    )


# --- company name resolution ---------------------------------------------------------------------


def test_company_resolves_from_the_careers_wrapped_title(monkeypatch):
    scraper = GemScraper(SLUG, company=SLUG)
    monkeypatch.setattr(scraper, "board_page", lambda: "https://jobs.gem.com/shef-com")
    monkeypatch.setattr(
        scraper,
        "_fetch",
        lambda *a, **k: type(
            "R", (), {"status_code": 200, "text": "<title>Shef Careers</title>"}
        )(),
    )
    scraper.resolve_company()
    assert scraper.company == "Shef"


def test_a_board_already_carrying_a_real_name_keeps_it(monkeypatch):
    scraper = GemScraper(SLUG, company="Shef Inc.")
    monkeypatch.setattr(
        scraper,
        "_fetch",
        lambda *a, **k: type(
            "R", (), {"status_code": 200, "text": "<title>Come work at Shef!</title>"}
        )(),
    )
    scraper.resolve_company()
    assert scraper.company == "Shef Inc."


# --- fetch_raw: detail batching and gap reporting ------------------------------------------------


def test_fetch_raw_batches_detail_requests_and_reports_gaps(monkeypatch):
    scraper = _scraper()
    listed = _listing()
    monkeypatch.setattr(scraper, "_listing", lambda: listed)
    monkeypatch.setattr(scraper, "async_fanout_enabled", staticmethod(lambda: False))

    calls: list[list[str]] = []

    def fake_batch(batch):
        calls.append(list(batch))
        # Succeed for every id except one, to exercise report_detail_gaps' missing count.
        return {eid: {"title": "T", "job": {}} for eid in batch if eid != IN_OFFICE_ID}

    monkeypatch.setattr(scraper, "_detail_batch", fake_batch)
    raw = scraper.fetch_raw()

    assert len(calls) == 1  # 4 postings, batch size 100 -> one request
    assert set(calls[0]) == {REMOTE_ID, HYBRID_ID, MULTI_LOC_ID, IN_OFFICE_ID}
    assert IN_OFFICE_ID not in raw["details"]
    assert scraper.telemetry["detail_losses"] == 1


def test_fetch_raw_splits_into_multiple_batches_above_the_batch_size(monkeypatch):
    from headstart.scrapers import gem as gem_module

    scraper = _scraper()
    listed = [
        {"extId": f"id{i}", "title": "T", "job": {}, "locations": []}
        for i in range(250)
    ]
    monkeypatch.setattr(scraper, "_listing", lambda: listed)
    monkeypatch.setattr(scraper, "async_fanout_enabled", staticmethod(lambda: False))

    calls: list[list[str]] = []

    def fake_batch(batch):
        calls.append(list(batch))
        return {eid: {"title": "T", "job": {}} for eid in batch}

    monkeypatch.setattr(scraper, "_detail_batch", fake_batch)
    scraper.fetch_raw()

    # `fan_out` runs batches through a thread pool, so the ORDER `calls` were appended in isn't
    # guaranteed to match submission order (only the final results array is input-aligned) —
    # sizes, sorted, are what pins the chunking itself.
    assert sorted(len(c) for c in calls) == sorted(
        [gem_module._DETAIL_BATCH_SIZE, gem_module._DETAIL_BATCH_SIZE, 50]
    )


def test_listing_envelope_missing_entirely_is_reported_not_crashed(monkeypatch):
    scraper = _scraper()
    monkeypatch.setattr(scraper, "_graphql", lambda payload: [{"data": {}}])
    assert scraper._listing() == []


def test_an_empty_board_is_not_reported_as_unreadable(monkeypatch):
    # The ordinary "nothing open right now" shape (data present, jobPostings empty) is NOT the
    # same as a malformed envelope — see the module docstring on live-vs-empty boards.
    scraper = _scraper()
    monkeypatch.setattr(
        scraper,
        "_graphql",
        lambda payload: [{"data": {"oatsExternalJobPostings": {"jobPostings": []}}}],
    )
    assert scraper._listing() == []


def test_gem_refuses_its_own_ats_sandboxes(monkeypatch):
    from headstart.network import http
    from headstart.scrapers.gem import GemScraper

    page = SimpleNamespace(
        status_code=200, text="<title>ats_sandbox_yello.co Careers</title>"
    )
    monkeypatch.setattr(http, "fetch", lambda *a, **k: page)
    scraper = GemScraper("atssandboxyello-co")
    scraper.resolve_company()
    assert scraper.company == "atssandboxyello-co"


def test_a_graphql_error_envelope_labels_every_id_in_the_batch():
    """A `{"errors": ...}` answer is not one result per request; its ids are labelled, not
    left `unlabelled` or raised into the fan-out's catch-all."""
    scraper = _scraper()
    assert scraper._apply_detail_results(["a", "b"], {"errors": []}) == {}
    assert scraper.detail_losses == {"GraphQL errors envelope": 2}


def test_a_short_batch_answer_labels_the_ids_it_ran_out_before():
    scraper = _scraper()
    detail = {"data": {"oatsExternalJobPosting": {"id": "a"}}}
    assert scraper._apply_detail_results(["a", "b", "c"], [detail]) == {
        "a": {"id": "a"}
    }
    assert scraper.detail_losses == {"short batch answer": 2}
