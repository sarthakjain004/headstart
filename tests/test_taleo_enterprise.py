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

    monkeypatch.setattr(enterprise.http, "fetch", fetch)
    jobs = TaleoEnterpriseScraper(BOARD)._listing(SHELL)
    assert calls == [1, 2]
    assert [job["id"] for job in jobs] == ["1", "2", "3"]
    assert jobs[0]["department"] == "Platform"
    assert jobs[0]["posted_at"].startswith("2026-09-11")


def test_detail_description_reads_initial_history_html():
    page = '<input id="initialHistory" value="prefix!*!%3Cp%3Eignored%3C%2Fp%3E!*!%3Cp%3EFull%20job%20description%3C%2Fp%3E">'
    assert enterprise._description(page) == "ignored Full job description"
