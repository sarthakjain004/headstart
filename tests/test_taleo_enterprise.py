from headstart import http
from headstart.scrapers import taleo_enterprise as enterprise
from headstart.scrapers.taleo_enterprise import TaleoEnterpriseScraper

BOARD = "https://acme.taleo.net/careersection/2"
SHELL = """<html><title>Careers | Acme Corp</title><script>portalNo: '123'</script>
<table id="jobs"><th>Requisition Title</th><th>Location</th><th>Department</th><th>Posting Date</th></table></html>"""


def _page(rows, page, total=3):
    return {
        "requisitionList": rows,
        "pagingData": {"currentPageNo": page, "pageSize": 2, "totalCount": total},
    }


def test_canonical_section_slug():
    assert (
        TaleoEnterpriseScraper.slug_from("ignored", BOARD + "/jobsearch.ftl?lang=en")
        == BOARD
    )


def test_company_falls_back_to_logo_when_shell_title_is_generic():
    shell = '<title>Job Search</title><img alt="Valero Logo" src="logo.svg">'
    assert enterprise._company(shell, "valero") == "Valero"


def test_company_ignores_chrome_icons_without_a_logo_src():
    """Live hyundaicapital/easyjet regression: a generic-title shell with no logo <img>
    used to fall through to chrome icon alt text ("Create an RSS feed") or the generic
    title itself ("Job Search"); both must now come back None so the caller's existing
    self.company survives instead of being overwritten with garbage."""
    shell = (
        "<title>Job Search</title>"
        '<img alt="Close" src="icon_help_close.png">'
        '<img alt="Create an RSS feed" src="ico-rss.png">'
        '<img alt="Access the online help" src="ico-help.png">'
    )
    assert enterprise._company(shell, "hyundaicapital") is None


def test_company_returns_none_when_title_is_generic_and_no_images_present():
    assert enterprise._company("<title>Job Search</title>", "acme") is None


def test_company_reads_the_second_title_tag_via_company_name_patterns():
    """Enterprise shells serve the chrome placeholder first and the tenant's real title
    second (see `company_name`'s module docstring) — `_company` must read the *last*
    ``<title>`` tag, not the first, and run it through the shared pattern registry."""
    shell = "<title>Job Search</title><title>Careers  |  D.R. Horton</title>"
    assert enterprise._company(shell, "drhorton") == "D.R. Horton"


def test_company_prefers_the_title_over_the_logo_when_both_are_present():
    shell = (
        "<title>Job Search</title><title>Valero - Careers</title>"
        '<img alt="Something Else Logo" src="logo.svg">'
    )
    assert enterprise._company(shell, "valero") == "Valero"


def test_listing_stops_at_stated_page_count_not_repeated_overflow(monkeypatch):
    pages = [
        _page(
            [
                {
                    "jobId": "1",
                    "column": [
                        "Engineer",
                        '["US-TX-Austin"]',
                        "Platform",
                        "Sep 11, 2026",
                    ],
                    "linkedColumn": 0,
                    "locationsColumns": [1],
                },
                {
                    "jobId": "2",
                    "column": [
                        "Designer",
                        '["US-CA-Los Angeles"]',
                        "Design",
                        "Sep 10, 2026",
                    ],
                    "linkedColumn": 0,
                    "locationsColumns": [1],
                },
            ],
            1,
            total=4,
        ),
        _page(
            [
                {
                    "jobId": "3",
                    "column": [
                        "Analyst",
                        '["US-NY-New York"]',
                        "Finance",
                        "Sep 9, 2026",
                    ],
                    "linkedColumn": 0,
                    "locationsColumns": [1],
                }
            ],
            2,
            total=4,
        ),
    ]
    calls = []

    class Response:
        def __init__(self, data):
            self.data = data

        def raise_for_status(self):
            pass

        def json(self):
            return self.data

    def fetch(method, url, **kwargs):
        assert (
            url
            == "https://acme.taleo.net/careersection/rest/jobboard/searchjobs?lang=en&portal=123"
        )
        calls.append(kwargs["json"]["pageNo"])
        return Response(pages.pop(0))

    monkeypatch.setattr(http, "fetch", fetch)
    scraper = TaleoEnterpriseScraper(BOARD)
    jobs = scraper._listing(SHELL)
    assert calls == [1, 2]
    assert [job["id"] for job in jobs] == ["1", "2", "3"]
    assert jobs[0]["department"] == "Platform"
    assert jobs[0]["posted_at"].startswith("2026-09-11")
    assert scraper.truncated is None
    assert scraper.telemetry == {"stated_total": 4, "unique_jobs": 3}


def test_blank_header_keeps_later_columns_aligned():
    headers = enterprise._aligned_headers(
        ["Icons", "Title", None, "Department", "Actions"],
        ["Engineer", "hidden", "Platform"],
    )
    assert (
        enterprise._column(headers, ["Engineer", "hidden", "Platform"], ("department",))
        == "Platform"
    )


def test_detail_vector_supplies_authoritative_fields():
    values = ["" for _ in range(26)]
    labels = ["reqlistitem.no" for _ in range(26)]
    labels[11] = labels[12] = "reqlistitem.description"
    labels[13] = labels[14] = "reqlistitem.qualification"
    labels[15] = labels[16] = "reqlistitem.jobfield"
    labels[17] = labels[18] = "reqlistitem.primarylocation"
    labels[19] = labels[20] = "reqlistitem.otherlocations"
    labels[23] = labels[24] = "reqlistitem.jobschedule"
    labels[25] = "reqlistitem.postingdate"
    values[11] = "!*!%3Cp%3EDescription%3C%2Fp%3E"
    values[13] = "!*!%3Cp%3EQualifications%3C%2Fp%3E"
    values[15] = "Engineering"
    values[17], values[19] = "US-TX-Austin", "US-TX-Dallas"
    values[23] = "Full-time"
    values[25] = "Sep 11, 2026, 5:18:01 PM"
    page = (
        "_hlid: ["
        + ",".join(repr(label) for label in labels)
        + "],"
        + (
            "api.fillList('requisitionDescriptionInterface', 'descRequisition', ["
            + ",".join(repr(value) for value in values)
            + "]);"
        )
    )
    detail = enterprise._parse_detail_page(page)
    assert detail == {
        "description": "Description Qualifications",
        "department": "Engineering",
        "experience": None,
        "location": "US-TX-Austin; US-TX-Dallas",
        "employment_type": "Full-time",
        "posted_at": "2026-09-11T17:18:01+00:00",
        "salary": None,
    }


def test_detail_reads_jobtype_into_experience():
    """Live Burns & McDonnell sample (job 649588, 2026-09-16): `reqlistitem.jobtype` states
    "New Grad" where the tenant populates it. Hyatt's own shells never carry this label at all
    (checked live against three Hyatt jobdetail pages the same day) — tenant-optional, not
    universal — so the field stays None there, same as any other unpopulated label."""
    values = ["" for _ in range(12)]
    labels = ["reqlistitem.no" for _ in range(12)]
    labels[9] = "reqlistitem.description"
    labels[10] = "reqlistitem.jobtype"
    labels[11] = "reqlistitem.jobfield"
    values[9] = "!*!%3Cp%3EDescription%3C%2Fp%3E"
    values[10] = "New Grad"
    values[11] = "Telecommunications"
    page = (
        "_hlid: ["
        + ",".join(repr(label) for label in labels)
        + "],"
        + (
            "api.fillList('requisitionDescriptionInterface', 'descRequisition', ["
            + ",".join(repr(value) for value in values)
            + "]);"
        )
    )
    detail = enterprise._parse_detail_page(page)
    assert detail["experience"] == "New Grad"


def test_ttec_detail_layout_keeps_absent_fields_null():
    """Live TTEC control has 26 values, with description at 10 and no job-field/schedule."""
    values = ["" for _ in range(26)]
    labels = ["reqlistitem.no" for _ in range(26)]
    labels[9] = "reqlistitem.title"
    labels[10] = "reqlistitem.description"
    labels[11] = labels[12] = "reqlistitem.primarylocation"
    labels[13] = labels[14] = "reqlistitem.otherlocations"
    values[10] = "!*!%3Cp%3ETTEC%20description%3C%2Fp%3E"
    values[11] = values[12] = "India"
    values[13] = values[14] = "India-Gujarat-Ahmedabad"
    page = (
        "_hlid: ["
        + ",".join(repr(label) for label in labels)
        + "],"
        + (
            "api.fillList('requisitionDescriptionInterface', 'descRequisition', ["
            + ",".join(repr(value) for value in values)
            + "]);"
        )
    )
    assert enterprise._parse_detail_page(page) == {
        "description": "TTEC description",
        "department": None,
        "experience": None,
        "location": "India; India-Gujarat-Ahmedabad",
        "employment_type": None,
        "posted_at": None,
        "salary": None,
    }


def test_salary_field_range_with_both_bounds():
    # Real live sample (careerglobalhc job 121008, 2026-09-15): payvalue + maximumsalary, no
    # currency/payfrequencybasis stated for this tenant.
    assert (
        enterprise._salary_field("45,000.00", "65,000.00", None, None)
        == "45,000.00-65,000.00"
    )


def test_salary_field_single_value_no_ceiling():
    # Real live sample (tas-tgh job 684681, 2026-09-15): payvalue only, an hourly rate with no
    # payfrequencybasis stated — reported as a floor-only figure, not an exact point.
    assert enterprise._salary_field("19.00", None, None, None) == "19.00"


def test_salary_field_currency_full_name_and_frequency_passed_through():
    # Real live sample (hyatt job 3134015, 2026-09-15): currency states the ISO code inline in
    # parentheses ("US Dollar (USD)") rather than a bare code — passed through as-is since
    # salary.py's _CURRENCY_CODE finds it regardless of the surrounding words.
    assert (
        enterprise._salary_field("18.00", None, "US Dollar (USD)", "Hourly")
        == "18.00 US Dollar (USD) Hourly"
    )
    from headstart.salary import SalarySpan, extract

    assert extract(
        "18.00 US Dollar (USD) Hourly", None, "taleo_enterprise"
    ) == SalarySpan(37_440, None, "USD", "field")


def test_salary_field_ceiling_without_floor_is_refused():
    # A maximumsalary with no payvalue must never be reported as a lone figure — the same
    # ceiling-vs-floor risk iCIMS's own JSON-LD parser refuses, since salary.py's _field_generic
    # (taleo_enterprise has no dedicated Tier-1 parser) has no way to tell a bare number is a
    # stated ceiling rather than the whole truth.
    assert (
        enterprise._salary_field(None, "65,000.00", "US Dollar (USD)", "Yearly") is None
    )


def test_salary_field_absent_entirely():
    assert enterprise._salary_field(None, None, None, None) is None


def test_alias_key_uses_full_career_section(monkeypatch):
    class Response:
        url = "https://acme.taleo.net/careersection/2/jobsearch.ftl?lang=en"

        def close(self):
            pass

    monkeypatch.setattr(http, "fetch", lambda *args, **kwargs: Response())
    assert TaleoEnterpriseScraper(
        "https://acme.taleo.net/careersection/2"
    ).alias_key() == ("https://acme.taleo.net/careersection/2")


def test_listing_row_carries_the_contest_number(monkeypatch):
    """`jobId` is the posting's id and `contestNo` the requisition number the recruiter sees —
    the one an Eightfold career site in front of this section states as `atsJobId` (Premier
    Health `978472` / `111166`, 2026-09-24; ADR-0191)."""
    row = {
        "jobId": "978472",
        "contestNo": "111166",
        "column": ["Registered Nurse", '["Middletown"]', "", "Sep 11, 2026"],
        "linkedColumn": 0,
        "locationsColumns": [1],
    }
    monkeypatch.setattr(
        http,
        "fetch",
        lambda *a, **k: type(
            "R",
            (),
            {
                "raise_for_status": lambda s: None,
                "json": lambda s: _page([row], 1, total=1),
            },
        )(),
    )
    jobs = TaleoEnterpriseScraper(BOARD)._listing(SHELL)
    assert [(j["id"], j["contest_no"]) for j in jobs] == [("978472", "111166")]
