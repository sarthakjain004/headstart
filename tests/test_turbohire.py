"""Tests for `headstart.scrapers.turbohire`.

The fixtures are real responses captured 2026-09-30. `turbohire_clickguard_*` is the whole of
`clickguard.turbohire.co` (6 postings): its listing, and each posting's detail. The
`turbohire_i4consulting_*` pair is 3 of `i4consulting.turbohire.co`'s 102 postings, chosen for
what clickguard lacks: a stated `FTE` type, the Roles & Responsibilities / Eligibility sections,
and a posting with no description anywhere. `turbohire_org.json` is Flipkart's organization
record. Fields the scraper never reads (the organization blob on every row, recruiter names and
e-mails, pipeline stages) are trimmed out; nothing is invented.

Every assertion pins something measured in `docs/turbohire/2026-09-30_career-api-measurement.md`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fake_fetcher import FakeFetcher, FakeResponse

from headstart.network import http
from headstart.scrapers.base import BoardUnreadable
from headstart.scrapers.registry import detail_pass_atses, get_scraper
from headstart.scrapers.turbohire import TurboHireScraper

FIXTURES = Path(__file__).parent / "fixtures"
SCRAPED_AT = "2026-01-01T00:00:00+00:00"
GTM = (
    "5dc6ae04-d1df-4f1d-af2a-dacca1126c7b"  # tech, remote, CTC hidden from job seekers
)
SDR = "9ec6f99b-1fee-48b9-81f7-6acddd3474e8"  # CONTRACT, MinExp only, monthly CTC shown
HEAD_MKT = (
    "b304bd08-1856-429b-8f56-0f21816b33b3"  # Type UNSPECIFIED, JobTypeV2 "Full Time"
)
PRINCIPAL = "85442abc-47b3-4781-8dbc-da24ed1a949e"  # FTE, annual CTC shown, remote
AFFILIATE = "a5383551-b14a-4340-a841-84de7b4c3083"  # R&R + Eligibility sections
UI_DEV = "db33a48e-be32-4a0e-a241-acddd637aead"  # tech, no description anywhere


def _load(name: str) -> dict:
    with open(FIXTURES / f"turbohire_{name}.json", encoding="utf-8") as fh:
        return json.load(fh)


def _raw(board: str) -> dict:
    """What `fetch_raw` returns once the detail pass has run."""
    return {"listing": _load(f"{board}_listing"), "details": _load(f"{board}_details")}


def _jobs(board: str = "clickguard", raw: dict | None = None) -> dict:
    scraper = get_scraper("turbohire", board, board)
    return {
        j.id.rsplit(":", 1)[1]: j for j in scraper.parse(raw or _raw(board), SCRAPED_AT)
    }


def test_every_listed_posting_is_a_job_keyed_by_its_job_id():
    jobs = _jobs()
    listed = [r["JobId"] for r in _load("clickguard_listing")["Result"]]
    assert sorted(jobs) == sorted(listed)
    assert jobs[GTM].id == f"turbohire:clickguard:{GTM}"
    assert jobs[GTM].title == "GTM Engineer - Demand Generation"
    assert jobs[GTM].department == "Leadership and Management"


def test_the_job_url_is_the_board_hosts_public_job_page_and_matches_the_shape():
    """The career page links each posting as `/job/publicjobs/{JobIdObfuscated}` on the Board's
    own host (the SPA's `getPublicPageUrl`); that page server-renders "[Hiring For]: {title}".
    The token is already percent-encoded (`%2F`) and is used as the API returns it."""
    obf = {
        r["JobId"]: r["JobIdObfuscated"] for r in _load("clickguard_listing")["Result"]
    }
    for job_id, job in _jobs().items():
        assert (
            job.url == f"https://clickguard.turbohire.co/job/publicjobs/{obf[job_id]}"
        )
        assert re.fullmatch(TurboHireScraper.url_shape, job.url)


# ------------------------------------------------------------------------------ field mapping


def test_location_joins_every_address_and_remote_reads_the_remote_job_marker():
    """`Location` is a JSON string holding a list of places; 117 of 1,268 postings name more than
    one (up to 25). TurboHire spells a remote posting as the place "Remote Job" (42 postings), and
    no field states it otherwise, so `is_remote` over the joined text is the only reader."""
    jobs = _jobs()
    assert jobs[SDR].location == "Remote Job; Mexico; Colombia; Brazil; Latin America"
    assert jobs[SDR].remote is True
    i4 = _jobs("i4consulting")
    assert i4[AFFILIATE].location == "Bangalore, Karnataka, India"
    assert i4[AFFILIATE].remote is False


def test_description_is_the_details_and_carries_its_sections():
    """The listing's `JobDescV2` is cut at 500 characters (877 of 1,268); the detail carries the
    whole text. Where a posting states Roles & Responsibilities and Eligibility too, the job page
    renders all three under their headings, and so does the description."""
    jobs = _jobs()
    assert len(jobs[GTM].description) > 3000
    affiliate = _jobs("i4consulting")[AFFILIATE].description
    assert "Roles & Responsibilities" in affiliate
    assert "Eligibility" in affiliate


def test_a_posting_with_no_description_anywhere_ships_without_one():
    """202 of 1,268 postings state none, on the listing or the detail."""
    assert _jobs("i4consulting")[UI_DEV].description is None


def test_a_missing_detail_still_emits_the_job_from_the_listing():
    raw = _raw("clickguard")
    raw["details"] = {}
    job = _jobs(raw=raw)[GTM]
    assert job.title == "GTM Engineer - Demand Generation"
    assert job.description is None and job.employment_type is None


def test_employment_type_is_the_details_type_code_else_its_job_type_label():
    """The listing's `Type` is UNSPECIFIED on 1,266 of 1,268 rows; the detail's states FTE (580),
    CONTRACT (12), INTERN (6), CONTRACT_TO_HIRE (1). Where the code is UNSPECIFIED the detail's
    `JobTypeV2` label sometimes states it (61 rows, all "Full Time")."""
    jobs = _jobs()
    assert jobs[SDR].employment_type == "Contract"
    assert jobs[HEAD_MKT].employment_type == "Full Time"
    assert _jobs("i4consulting")[PRINCIPAL].employment_type == "Full Time"


def test_experience_is_the_listing_bounds_as_the_page_states_them():
    """The job page renders "Min - Max Years" and "Min+ Years"; 1,197 rows state both bounds,
    5 only the floor, 65 neither."""
    jobs = _jobs()
    assert jobs[GTM].experience == "5-10 years"
    assert jobs[SDR].experience == "2+ years"


def test_posted_at_is_the_career_page_publish_date_else_the_creation_date():
    """`PublishedDate` equals the detail's `PublishedDates.CAREERPAGE` on all 827 rows stating
    either; the other 441 state only `CreatedDate`, which carries no zone but is UTC (it precedes
    the zoned publish dates on 2,888 of 2,888 pairs, 92 of them by under a minute)."""
    assert _jobs()[GTM].posted_at == "2026-08-12T18:33:58.0104087Z"
    assert _jobs("i4consulting")[PRINCIPAL].posted_at == "2022-05-25T10:03:28.62Z"


def test_salary_is_what_the_job_page_shows():
    """The page shows `CTCInfo` unless `HiddenFrom` names JobSeekers (`getCTCString`); 794 of
    1,185 stated ones are hidden that way. The detail carries it; the listing's is blanked."""
    jobs = _jobs()
    assert jobs[SDR].salary == "1000-1500 USD per-month"
    assert jobs[GTM].salary is None
    i4 = _jobs("i4consulting")
    assert i4[PRINCIPAL].salary == "2000000-3500000 INR per-year"
    assert i4[AFFILIATE].salary is None
    assert i4[UI_DEV].salary is None


# --------------------------------------------------------------------------------- the fetch


ORG_ID = "4d757ba0-3d57-448a-b82c-238ed87ac90f"
TOKEN = {"access_token": "tok-1", "expires_in": 3600, "token_type": "Bearer"}


def _route(listing: dict, details: dict | None = None, *, org_status: int = 200):
    """The four endpoints a Board's fetch touches, answered from the fixtures."""

    def route(method, url, kwargs):
        if url == "https://api.turbohire.co/api/token/noauth":
            return FakeResponse(text=json.dumps(TOKEN))
        if url.startswith("https://api.turbohire.co/api/publicorganizations?"):
            if org_status != 200:
                return FakeResponse(org_status, "")
            return FakeResponse(text=json.dumps(_load("org")))
        if url.startswith("https://api.turbohire.co/api/careerpagev2/filteredjobs?"):
            return FakeResponse(text=json.dumps(listing))
        if url.startswith("https://api.turbohire.co/api/publicjobs?"):
            job_id = url.split("jobId=", 1)[1].split("&", 1)[0]
            if details is None or job_id not in details:
                return FakeResponse(
                    404, "The resource you are looking for has been removed"
                )
            return FakeResponse(text=json.dumps(details[job_id]))
        return FakeResponse(599, f"unexpected request {method} {url}")

    return route


def _fetching(route) -> tuple[TurboHireScraper, FakeFetcher]:
    fetcher = FakeFetcher(route)
    return TurboHireScraper("flipkart", "flipkart", fetcher=fetcher), fetcher


def test_the_fetch_asks_for_a_token_resolves_the_label_and_posts_for_the_career_page(
    monkeypatch,
):
    """The token endpoint answers 403 without a Referer on a `*.turbohire.co` host (Origin alone
    is not enough); the token is anonymous and not tenant-bound. The label resolves to the org
    GUID the listing is keyed on; `pageType=0` is the public career page — `1`, the internal job
    page, listed 7,556 Flipkart postings no outsider can see."""
    monkeypatch.setenv("HEADSTART_TECH_GATE", "0")
    scraper, fetcher = _fetching(
        _route(_load("clickguard_listing"), _load("clickguard_details"))
    )
    raw = scraper.fetch_raw()
    token, org, listing, *details = fetcher.requests
    assert token.method == "GET"
    assert token.kwargs["headers"]["Referer"] == "https://flipkart.turbohire.co/"
    # An empty `Bearer ` is answered 401 by the token endpoint itself (measured).
    assert "Authorization" not in token.kwargs["headers"]
    assert (
        org.url
        == "https://api.turbohire.co/api/publicorganizations?accountName=flipkart"
    )
    assert org.kwargs["headers"]["Authorization"] == "Bearer tok-1"
    assert listing.method == "POST"
    assert listing.url == (
        "https://api.turbohire.co/api/careerpagev2/filteredjobs"
        f"?orgId={ORG_ID}&pageType=0"
    )
    assert listing.kwargs["json"] == {}
    assert listing.kwargs["headers"]["Authorization"] == "Bearer tok-1"
    assert len(details) == 6
    assert all(d.kwargs["headers"]["Authorization"] == "Bearer tok-1" for d in details)
    assert all(d.url.endswith("&fieldVisibility=CareerPage") for d in details)
    assert set(raw["details"]) == {
        r["JobId"] for r in _load("clickguard_listing")["Result"]
    }


def test_the_organization_names_the_company():
    """`OrgName` is the career page's own `<title>` (65 of 65 resolved labels)."""
    scraper, _ = _fetching(_route({"Total": 0, "Result": []}))
    scraper.fetch_raw()
    assert scraper.company == "Flipkart Internet Private Limited"


def test_an_unknown_label_is_a_gone_board():
    """Every subdomain serves the SPA with 200, but `publicorganizations?accountName=` answers
    404 with an empty body for a label no organization holds (39 of 104 pool labels)."""
    scraper, _ = _fetching(_route({"Total": 0, "Result": []}, org_status=404))
    with pytest.raises(http.RequestsError, match="410"):
        scraper.fetch_raw()


def test_a_listing_short_of_its_total_is_marked_truncated(monkeypatch):
    """One POST returns the whole Board: `Total` equalled the rows served on 65 of 65 Boards, and
    the SPA itself pages client-side. A shortfall is the API changing under us."""
    monkeypatch.setenv("HEADSTART_TECH_GATE", "0")
    listing = {**_load("clickguard_listing"), "Total": 60}
    scraper, _ = _fetching(_route(listing, _load("clickguard_details")))
    scraper.fetch_raw()
    assert scraper.truncated is not None


def test_a_listing_without_a_result_list_is_an_unreadable_board():
    scraper, _ = _fetching(_route({"Message": "An error has occurred."}))
    with pytest.raises(BoardUnreadable):
        scraper.fetch_raw()


def test_a_failed_detail_is_a_counted_gap_not_a_failed_board(monkeypatch):
    monkeypatch.setenv("HEADSTART_TECH_GATE", "0")
    scraper, _ = _fetching(_route(_load("clickguard_listing"), {}))
    raw = scraper.fetch_raw()
    assert raw["details"] == {}
    assert scraper.detail_losses == {"HTTP 404": 6}
    assert scraper.truncated is None
    assert len(scraper.parse(raw, SCRAPED_AT)) == 6


def test_the_tech_gate_fetches_only_tech_details(monkeypatch):
    """Exact here: the detail's `JobTitle` and `Department` equalled the listing's on 1,268 of
    1,268 postings, so the gate asks `filter_tech`'s question with its own inputs. One of
    clickguard's six is tech."""
    monkeypatch.delenv("HEADSTART_TECH_GATE", raising=False)
    scraper, _ = _fetching(
        _route(_load("clickguard_listing"), _load("clickguard_details"))
    )
    scraper.have_details = frozenset()
    raw = scraper.fetch_raw()
    assert set(raw["details"]) == {GTM}
    assert len(scraper.parse(raw, SCRAPED_AT)) == 6


def test_the_scraper_declares_a_detail_pass():
    assert TurboHireScraper.has_detail_pass is True
    assert "turbohire" in detail_pass_atses()
