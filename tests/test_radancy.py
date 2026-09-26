"""Tests for the Radancy TalentBrew career-front scraper (headstart.scrapers.radancy).

The fixture is a real capture from `jobs.takeda.com` (2026-09-26): its sitemap cut to three
non-job entries and four postings, and each posting's page cut to what the scraper reads — the
`<title>`, the `gtm_tbcn_*` meta tags, the JSON-LD block and the Apply button's `apply-url`.
The other URLs below are real sitemap entries from other fronts, quoted where a test needs a
shape the fixture Board does not have.
"""

from __future__ import annotations

import json
import logging
import pathlib
import re

import pytest
from fake_fetcher import FakeFetcher, FakeResponse

from headstart.jobs.salary import from_field
from headstart.scrapers import radancy
from headstart.scrapers.radancy import (
    _HeldBoards,
    _iso_date,
    _page_fields,
    _salary,
    backing_board,
    sitemap_rows,
)
from headstart.scrapers.registry import get_scraper

_FIXTURE = json.loads(
    (pathlib.Path(__file__).parent / "fixtures" / "radancy_takeda.json").read_text()
)
_HOST = _FIXTURE["host"]
_SCRAPED_AT = "2026-09-26T00:00:00+00:00"
_HELD = _HeldBoards(
    frozenset(
        {
            "workday:takeda/external",
            "icims:experienced-arm.icims.com",
            "taleo_enterprise:https://uhg.taleo.net/careersection/10000",
            "smartrecruiters:mattelinc",
            "successfactors:jobs.netapp.com",
            "workday:stemcell/external_careers",
            "radancy:jobs.sanofi.com",
            "avature:synopsys",
        }
    )
)


def _route(method: str, url: str, kwargs: dict) -> FakeResponse:
    if url.endswith("/search-jobs"):
        return FakeResponse(
            text='<section id="search-results" data-total-job-results="4">'
        )
    if url.endswith("/sitemap.xml"):
        return FakeResponse(content=b"\xef\xbb\xbf" + _FIXTURE["sitemap_xml"].encode())
    return FakeResponse(text=_FIXTURE["pages"][url.rsplit("/", 1)[1]])


@pytest.fixture(autouse=True)
def _held_boards(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(radancy, "_scrapable_boards", lambda: _HELD)


# --- the listing ---------------------------------------------------------------------------


def testsitemap_rows_keep_jobs_and_skip_facet_pages() -> None:
    rows = sitemap_rows(_FIXTURE["sitemap_xml"], _HOST)
    assert [job_id for job_id, _ in rows] == [
        "101095894992",
        "100714265472",
        "100390698160",
        "94242537120",
    ]
    assert all(url.startswith("https://jobs.takeda.com/job/") for _, url in rows)


@pytest.mark.parametrize(
    "url",
    [
        "https://carrieres.walmart.ca/emploi/levis/can-prepose-au-traitement-des-commandes-omni/37255/101172783184",
        "https://careers.cargill.com/en/job/mason-city/testing-only-no-applications-accepted-professional/23251/84516473040",
        "https://jobs.intuit.com/job/mountain-view/staff-machine-learning-engineer/27595/87369450000",
    ],
)
def test_a_localised_or_prefixed_job_path_is_a_job(url: str) -> None:
    host = url.split("/")[2]
    assert sitemap_rows(f"<urlset><url><loc>{url}</loc></url></urlset>", host) == [
        (url.rsplit("/", 1)[1], url)
    ]


@pytest.mark.parametrize(
    "url",
    [
        "https://jobs.intuit.com",
        "https://jobs.intuit.com/saved-jobs",
        "https://jobs.intuit.com/category/software-engineering-jobs/27595/68357/1",
        "https://jobs.intuit.com/employment/san-diego-new-college-grad-jobs/27595/9205760/6252001-5332921-5391832-5391811/4",
        "https://jobs.intuit.com/business/custom_fields.externalreferencecodefacet/24372/27595/5",
        "https://careers.cargill.com/en/business/is_manager/production%2520supervisor/23251/5",
        "https://jobs.intuit.com/location/remote-jobs/27595/1000000000100/2",
    ],
)
def test_a_facet_or_content_page_is_not_a_job(url: str) -> None:
    host = url.split("/")[2]
    assert sitemap_rows(f"<urlset><url><loc>{url}</loc></url></urlset>", host) == []


def testsitemap_rows_dedupe_an_id() -> None:
    url = "https://jobs.takeda.com/job/boston/director-clinical-operations/1113/101095894992"
    xml = f"<urlset><url><loc>{url}</loc></url><url><loc>{url}</loc></url></urlset>"
    assert len(sitemap_rows(xml, _HOST)) == 1


def test_an_alias_hosts_sitemap_lists_none_of_its_own_jobs() -> None:
    # www.takedajobs.com's /sitemap.xml redirects to jobs.takeda.com's (2026-09-26).
    assert sitemap_rows(_FIXTURE["sitemap_xml"], "www.takedajobs.com") == []


# --- the job page --------------------------------------------------------------------------


def test_fetch_reads_every_field_off_the_job_pages() -> None:
    scraper = get_scraper("radancy", _HOST, fetcher=FakeFetcher(_route))
    jobs = {job.id: job for job in scraper.fetch()}

    assert set(jobs) == {f"radancy:jobs.takeda.com:{i}" for i in _FIXTURE["pages"]}
    job = jobs["radancy:jobs.takeda.com:94242537120"]
    assert job.title == "Specialty Business Manager IBD-(Cincinnati, OH)"
    assert job.company == "Takeda Pharmaceutical"
    assert job.department == "Sales"
    assert job.posted_at == "2026-08-26"
    assert job.employment_type == "Full time"
    assert job.salary == "63.51-87.31 USD hourly"
    assert job.location == "Ohio - Virtual, Ohio, United States; Remote"
    assert job.url == (
        "https://jobs.takeda.com/job/ohio/specialty-business-manager-ibd-cincinnati-oh"
        "/1113/94242537120"
    )
    assert len(job.description or "") > 1000
    tech = jobs["radancy:jobs.takeda.com:100390698160"]
    assert tech.department == "Data, Digital and Technology"  # entity unescaped
    assert tech.salary is None  # a currency with no amount states no salary


def test_a_lost_page_drops_its_job_and_marks_the_board_truncated() -> None:
    def route(method: str, url: str, kwargs: dict) -> FakeResponse:
        if url.endswith("94242537120"):
            return FakeResponse(text="<html><title>Oops</title></html>")
        return _route(method, url, kwargs)

    scraper = get_scraper("radancy", _HOST, fetcher=FakeFetcher(route))
    jobs = scraper.fetch()

    assert len(jobs) == 3
    assert scraper.detail_losses == {
        "no JobPosting JSON-LD or job meta tags on a 200": 1
    }
    assert scraper.truncated == "1/4 job pages unreadable"


def test_a_capped_sitemap_marks_the_board_short_by_the_stated_total() -> None:
    # jobs.walgreens.com, 2026-09-26: 500 job URLs in the sitemap, 22,544 stated.
    def route(method: str, url: str, kwargs: dict) -> FakeResponse:
        if url.endswith("/search-jobs"):
            return FakeResponse(text='<section data-total-job-results="22544">')
        return _route(method, url, kwargs)

    scraper = get_scraper("radancy", _HOST, fetcher=FakeFetcher(route))
    assert len(scraper.fetch()) == 4
    assert (
        scraper.truncated
        == "the sitemap lists 4 of the 22544 postings the front states"
    )


def test_a_results_page_on_another_host_states_nothing_for_this_board() -> None:
    def route(method: str, url: str, kwargs: dict) -> FakeResponse:
        if url.endswith("/search-jobs"):
            return FakeResponse(
                text='<section data-total-job-results="245">',
                url="https://www.disneycareers.com/en/search-jobs?acm=26715",
            )
        return _route(method, url, kwargs)

    scraper = get_scraper("radancy", _HOST, fetcher=FakeFetcher(route))
    scraper.fetch()
    assert scraper.truncated is None


def test_an_empty_sitemap_reads_no_jobs() -> None:
    # empregos.allianceautomotive.eu, 2026-09-26: `data-total-job-results="0"`.
    xml = (
        "<urlset><url><loc>https://empregos.allianceautomotive.eu</loc></url>"
        "<url><loc>https://empregos.allianceautomotive.eu/saved-jobs</loc></url></urlset>"
    )
    scraper = get_scraper(
        "radancy",
        "empregos.allianceautomotive.eu",
        fetcher=FakeFetcher(lambda m, u, k: FakeResponse(text=xml)),
    )
    assert scraper.fetch() == []
    assert scraper.truncated is None


def test_job_url_matches_url_shape() -> None:
    shape = re.compile(get_scraper("radancy", _HOST).url_shape)
    urls = [url for _, url in sitemap_rows(_FIXTURE["sitemap_xml"], _HOST)] + [
        "https://careers.cargill.com/en/job/mason-city/testing-only-no-applications-accepted-professional/23251/84516473040"
    ]
    for url in urls:
        assert shape.fullmatch(get_scraper("radancy", _HOST).job_url(url))


def test_slug_is_the_front_host() -> None:
    cls = type(get_scraper("radancy", _HOST))
    assert (
        cls.slug_from("jobs.intuit.com", "https://jobs.intuit.com/")
        == "jobs.intuit.com"
    )
    assert cls.slug_from("Jobs.Intuit.com", "") == "jobs.intuit.com"


@pytest.mark.parametrize(
    ("value", "iso"),
    [
        ("2026-9-3", "2026-09-03"),
        ("2026-09-25", "2026-09-25"),
        ("", None),
        (None, None),
    ],
)
def test_iso_date_pads_talentbrews_unpadded_date(
    value: str | None, iso: str | None
) -> None:
    assert _iso_date(value) == iso


def test_page_fields_join_every_place() -> None:
    fields = _page_fields(_FIXTURE["pages"]["101095894992"])
    assert fields is not None
    assert (
        fields["location"]
        == "Massachusetts - Virtual, Massachusetts, United States; Remote"
    )


# --- salary --------------------------------------------------------------------------------


def _node(**value: float) -> dict:
    return {"currency": "USD", "value": {"unitText": "", **value}}


def test_salary_period_comes_from_magnitude() -> None:
    assert (
        _salary(_node(minValue=177000, maxValue=278080)) == "177000-278080 USD yearly"
    )
    assert _salary(_node(minValue=16.92, maxValue=26.6)) == "16.92-26.6 USD hourly"


@pytest.mark.parametrize(
    "node",
    [
        _node(maxValue=90000),  # a ceiling alone would read as a floor
        _node(minValue=40, maxValue=90000),  # no period fits both
        _node(),  # a currency with no amount
        None,
    ],
)
def test_salary_refuses_what_states_no_coherent_range(node: dict | None) -> None:
    assert _salary(node) is None


def test_emitted_salary_round_trips_through_the_repo_parser() -> None:
    span = from_field("63.51-87.31 USD hourly", ats="radancy")
    assert span is not None and span.currency == "USD"
    assert span.min_annual is not None and span.min_annual > 100_000


# --- Front duplication ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("apply_url", "board"),
    [
        (
            "https://takeda.wd502.myworkdayjobs.com/External/job/CZE---Most/LKA-KA--pro-plazma-centrum-CHOMUTOV---FLEXIBILN-VAZEK_R0181856/apply",
            "workday:takeda/external",
        ),
        (
            "https://stemcell.wd3.myworkdayjobs.com/en-US/External_Careers/job/Canada---Ontario-Remote-Home-Office/Account-Manager--Immunology_R0007253/apply",
            "workday:stemcell/external_careers",
        ),
        (
            "https://experienced-arm.icims.com/jobs/18903/senior-infrastructure-automation-tools-engineer/job/login",
            "icims:experienced-arm.icims.com",
        ),
        (
            "https://uhg.taleo.net/careersection/10000/jobapply.ftl?job=2382212",
            "taleo_enterprise:https://uhg.taleo.net/careersection/10000",
        ),
        (
            "https://jobs.smartrecruiters.com/MattelInc/744000150773689-american-girl-restaurant-dish-washer-seasonal-part-time-?oga=true",
            "smartrecruiters:mattelinc",
        ),
        (
            "https://jobs.netapp.com/job/San-Jose-Director%2C-Software-Engineer-CA-95128/1422658200/?feedId=386800&tcsource=apply",
            "successfactors:jobs.netapp.com",
        ),
        # SuccessFactors' own apply form names a company id, not the RMK host: unresolved.
        (
            "https://career2.successfactors.eu/sfcareer/jobreqcareer?jobId=331288&company=cargill&locale=en_US",
            None,
        ),
        (
            "https://intuit.avature.net/externalCareers/JobApplication?pipelineId=23933",
            None,
        ),
        (
            "https://synopsys.avature.net/careers/Login?jobId=18266&source=&tags=&user=&formValues",
            "avature:synopsys",
        ),
        (
            "https://amgen.wd1.myworkdayjobs.com/Careers/job/US---California---Thousand-Oaks/Associate-Manufacturing_R-256887/apply",
            None,  # not held
        ),
        (
            "https://jobs.sanofi.com/sys/apply/job/application/2649/44048303680?languageCode=en",
            None,  # TalentBrew's own apply form, on the front itself
        ),
        (None, None),
    ],
)
def test_backing_board_resolves_held_boards_only(
    apply_url: str | None, board: str | None
) -> None:
    assert backing_board(apply_url, _HELD) == board


def test_front_duplication_is_logged_and_recorded(
    caplog: pytest.LogCaptureFixture,
) -> None:
    scraper = get_scraper("radancy", _HOST, fetcher=FakeFetcher(_route))
    with caplog.at_level(logging.INFO):
        jobs = scraper.fetch()

    assert len(jobs) == 4  # every posting is kept: duplication is measured, never gated
    assert scraper.telemetry["front_postings"] == 4
    assert scraper.telemetry["front_duplicated"] == 4
    assert (
        "radancy:jobs.takeda.com: Front duplication 4/4 postings apply on a Scrapable Board "
        "(workday:takeda/external 4)"
    ) in caplog.text


def test_front_duplication_is_not_reported_without_a_ledger(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(radancy, "_scrapable_boards", lambda: _HeldBoards(frozenset()))
    scraper = get_scraper("radancy", _HOST, fetcher=FakeFetcher(_route))
    with caplog.at_level(logging.INFO):
        scraper.fetch()
    assert "Front duplication" not in caplog.text
    assert "front_duplicated" not in scraper.telemetry


def test_a_page_without_jsonld_is_read_from_talentbrews_meta_tags() -> None:
    # jobs.jabil.com, 2026-09-26: its template writes no JSON-LD on any job page.
    page = (
        pathlib.Path(__file__).parent
        / "fixtures"
        / "radancy_jabil_job_page_without_jsonld.html"
    ).read_text()
    fields = _page_fields(page)
    assert fields is not None
    assert fields["title"] == "Operator II"
    assert fields["department"] == "Manufacturing"
    assert fields["location"] == "Gurnee, Illinois, United States"
    assert fields["posted_at"] == "2026-08-18"
    assert fields["title_company"] == "Jabil"
    assert fields["apply_url"].startswith(
        "https://jabil.wd5.myworkdayjobs.com/Jabil_Careers/"
    )
    body = fields["description"]
    assert body.lstrip().startswith("<p>KEY RESPONSIBILITIES:")
    assert body.count("<div") == body.count("</div>")  # nested divs kept whole


def test_meta_places_split_on_the_pipe() -> None:
    page = (
        '<meta name="gtm_tbcn_jobtitle" content="Nurse">'
        '<meta name="gtm_tbcn_location" '
        'content="Sherman~Illinois~United States|Grafton~Illinois~United States">'
    )
    fields = _page_fields(page)
    assert fields is not None
    assert fields["location"] == (
        "Sherman, Illinois, United States; Grafton, Illinois, United States"
    )
