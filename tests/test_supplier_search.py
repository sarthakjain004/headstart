"""Tests for `headstart.scrapers.supplier_search`, the implementation `tiktok` and `bytedance`
share (ADR-0198).

Every test runs once per brand, through that brand's own scraper class and a fake `Fetcher`
standing in for the backend — so what is pinned is each Board's behaviour, not a private helper.
The envelopes mirror what both hosts answered live on 2026-09-24.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from headstart.models import html_to_text
from headstart.scrapers.bytedance import ByteDanceScraper
from headstart.scrapers.tiktok import TikTokScraper

BRANDS = [
    pytest.param(TikTokScraper, "lifeattiktok.com", id="tiktok"),
    pytest.param(ByteDanceScraper, "jobs.bytedance.com", id="bytedance"),
]
SCRAPED_AT = "2026-01-01T00:00:00+00:00"


RESULT_WINDOW = 10_000


def _paged_board_answer(postings: list[dict], count: int, clamp: int | None = None):
    """Answers each request with `postings` sliced by the body's offset and limit (at most `clamp`
    rows, if given), stating `count` — independently of how many postings exist, so a Board short
    of its own total is reproducible. Like the live backend, a request past the 10,000-row result
    window gets 0 rows and a `count` of 10,000."""

    def answer_page(offset: int, limit: int):
        if offset + limit > RESULT_WINDOW:
            return 200, {
                "code": 0,
                "data": {"job_post_list": [], "count": RESULT_WINDOW},
            }
        page = postings[offset : offset + min(limit, clamp or limit)]
        return 200, {"code": 0, "data": {"job_post_list": page, "count": count}}

    return answer_page


def _postings(how_many: int) -> list[dict]:
    return [{"id": str(number), "title": f"Job {number}"} for number in range(how_many)]


class _SupplierSearchBackend:
    """A `Fetcher` whose every POST is answered by `answer(offset, limit)` -> (status, envelope),
    recording each request it was sent."""

    def __init__(self, answer) -> None:
        self.answer = answer
        self.requests: list[SimpleNamespace] = []

    def fetch(self, method, url, **kwargs):
        body = kwargs["json"]
        self.requests.append(
            SimpleNamespace(
                method=method, url=url, headers=kwargs["headers"], body=body
            )
        )
        status, envelope = self.answer(body["offset"], body["limit"])

        def raise_for_status():
            if status >= 400:
                raise RuntimeError(f"HTTP {status}")

        return SimpleNamespace(
            status_code=status, json=lambda: envelope, raise_for_status=raise_for_status
        )

    async def fetch_async(self, *args, **kwargs):  # pragma: no cover
        raise AssertionError("the supplier search API has no detail pass")


def _scraper(scraper_class, slug, answer):
    backend = _SupplierSearchBackend(answer)
    return scraper_class(slug, fetcher=backend), backend


# --------------------------------------------------------------------------- the request


@pytest.mark.parametrize(
    "scraper_class,slug,search_url,website_path",
    [
        pytest.param(
            TikTokScraper,
            "lifeattiktok.com",
            "https://api.lifeattiktok.com/api/v1/public/supplier/search/job/posts",
            "tiktok",
            id="tiktok",
        ),
        pytest.param(
            ByteDanceScraper,
            "jobs.bytedance.com",
            "https://jobs.bytedance.com/api/v1/public/supplier/search/job/posts",
            "en",
            id="bytedance",
        ),
    ],
)
def test_each_brand_posts_its_own_board_selector_to_its_own_host(
    scraper_class, slug, search_url, website_path
):
    # One backend: the `website-path` header, not the host, picks the Board (ADR-0198), and
    # these are the two values measured live to select each brand's Board.
    scraper, backend = _scraper(
        scraper_class, slug, _paged_board_answer(_postings(3), count=3)
    )
    scraper.fetch_raw()
    (request,) = backend.requests
    assert request.method == "POST"
    assert request.url == search_url
    assert request.headers["website-path"] == website_path
    # Pins `i18n_name` to English: without it the ByteDance Board answers it in Chinese.
    assert request.headers["accept-language"] == "en-US"
    assert request.body == {"keyword": "", "limit": 200, "offset": 0}


# --------------------------------------------------------------------------- the walk


@pytest.mark.parametrize("scraper_class,slug", BRANDS)
def test_the_walk_reads_every_page_until_the_stated_count(scraper_class, slug):
    scraper, backend = _scraper(
        scraper_class, slug, _paged_board_answer(_postings(450), count=450)
    )
    posts = scraper.fetch_raw()
    assert len(posts) == 450
    assert [request.body["offset"] for request in backend.requests] == [0, 200, 400]
    assert scraper.truncated is None


@pytest.mark.parametrize("scraper_class,slug", BRANDS)
def test_a_short_page_mid_walk_neither_ends_the_walk_nor_leaves_a_gap(
    scraper_class, slug
):
    # Never seen live (no page short of the limit before the last one, full walks of both Boards
    # 2026-09-24), but a silent clamp would produce exactly this: the next page starts where the
    # short one stopped, so nothing is skipped and the walk goes on to the stated count.
    postings = _postings(450)

    def clamped_first_page(offset, limit):
        page = postings[offset : offset + (150 if offset == 0 else limit)]
        return 200, {"code": 0, "data": {"job_post_list": page, "count": 450}}

    scraper, backend = _scraper(scraper_class, slug, clamped_first_page)
    posts = scraper.fetch_raw()
    assert [request.body["offset"] for request in backend.requests] == [0, 150, 350]
    assert [post["id"] for post in posts] == [post["id"] for post in postings]
    assert scraper.truncated is None


@pytest.mark.parametrize("scraper_class,slug", BRANDS)
def test_an_empty_page_ends_the_walk_when_no_count_is_stated(scraper_class, slug):
    scraper, backend = _scraper(
        scraper_class, slug, _paged_board_answer(_postings(250), count=0)
    )
    posts = scraper.fetch_raw()
    assert len(posts) == 250
    assert [request.body["offset"] for request in backend.requests] == [0, 200, 250]
    assert scraper.truncated is None


@pytest.mark.parametrize("scraper_class,slug", BRANDS)
def test_a_board_short_of_its_own_count_is_marked_truncated(scraper_class, slug):
    scraper, _ = _scraper(
        scraper_class, slug, _paged_board_answer(_postings(200), count=300)
    )
    posts = scraper.fetch_raw()
    assert len(posts) == 200
    assert "200 of 300" in scraper.truncated


@pytest.mark.parametrize("scraper_class,slug", BRANDS)
def test_a_negligible_shortfall_stays_authoritative(scraper_class, slug):
    # 991 of 1,000 is at/above `MIN_AUTHORITATIVE_SHARE` (ADR-0121): the 9 missing ids are left
    # to ADR-0083's grace period instead.
    scraper, _ = _scraper(
        scraper_class, slug, _paged_board_answer(_postings(991), count=1000)
    )
    scraper.fetch_raw()
    assert scraper.truncated is None


@pytest.mark.parametrize(
    "stated_count",
    [
        pytest.param(12_000, id="count-states-the-real-total"),
        # Unmeasured: the backend may state 10,000 for a larger Board, as it does past the window.
        pytest.param(RESULT_WINDOW, id="count-stops-at-the-window"),
    ],
)
@pytest.mark.parametrize("scraper_class,slug", BRANDS)
def test_reaching_the_result_window_marks_truncated(scraper_class, slug, stated_count):
    # The backend serves nothing past offset + limit = 10,000 (measured 2026-09-24): a walk that
    # gets there did not see the Board end, whatever `count` says.
    scraper, backend = _scraper(
        scraper_class, slug, _paged_board_answer(_postings(12_000), count=stated_count)
    )
    posts = scraper.fetch_raw()
    assert len(posts) == RESULT_WINDOW
    assert len(backend.requests) == 50
    assert "result window" in scraper.truncated


@pytest.mark.parametrize("scraper_class,slug", BRANDS)
def test_a_clamped_walk_reads_up_to_the_window_without_asking_across_it(
    scraper_class, slug
):
    # Pages clamped to 150 rows leave offsets off the 200 grid; a request crossing 10,000 would
    # answer 0 rows and end the walk 50 rows short with nothing said.
    scraper, backend = _scraper(
        scraper_class,
        slug,
        _paged_board_answer(_postings(12_000), count=12_000, clamp=150),
    )
    posts = scraper.fetch_raw()
    assert len(posts) == RESULT_WINDOW
    assert (
        max(
            request.body["offset"] + request.body["limit"]
            for request in backend.requests
        )
        == RESULT_WINDOW
    )
    assert "result window" in scraper.truncated


# --------------------------------------------------------------------------- the envelope


@pytest.mark.parametrize("scraper_class,slug", BRANDS)
def test_a_nonzero_code_on_http_200_keeps_what_was_read_and_marks_truncated(
    scraper_class, slug
):
    # Both hosts answer a negative offset with HTTP 200 and this envelope (2026-09-24). `data:
    # null` would otherwise read as the empty page a finished Board serves.
    postings = _postings(500)

    def fails_on_the_second_page(offset, limit):
        if offset:
            return 200, {"code": -4000001, "data": None, "message": "System error"}
        return 200, {
            "code": 0,
            "data": {"job_post_list": postings[:limit], "count": 500},
        }

    scraper, _ = _scraper(scraper_class, slug, fails_on_the_second_page)
    posts = scraper.fetch_raw()
    assert len(posts) == 200
    assert "code -4000001 (System error) at offset 200" in scraper.truncated
    assert "200 postings read so far" in scraper.truncated


@pytest.mark.parametrize("scraper_class,slug", BRANDS)
def test_an_envelope_without_a_code_is_not_read_as_success(scraper_class, slug):
    scraper, _ = _scraper(
        scraper_class, slug, lambda offset, limit: (200, {"data": None})
    )
    assert scraper.fetch_raw() == []
    assert "code None" in scraper.truncated


@pytest.mark.parametrize("scraper_class,slug", BRANDS)
def test_an_http_error_raises(scraper_class, slug):
    # A missing or unknown `website-path` is HTTP 400 `invalid request`, no JSON body.
    scraper, _ = _scraper(scraper_class, slug, lambda offset, limit: (400, None))
    with pytest.raises(RuntimeError, match="HTTP 400"):
        scraper.fetch_raw()


# --------------------------------------------------------------------------- parse


def _parse_one(scraper_class, slug, posting: dict):
    scraper, _ = _scraper(scraper_class, slug, _paged_board_answer([posting], count=1))
    (job,) = scraper.fetch()
    return job


@pytest.mark.parametrize("scraper_class,slug", BRANDS)
def test_a_posting_parses_to_one_job_on_its_brands_board(scraper_class, slug):
    job = _parse_one(
        scraper_class,
        slug,
        {
            "id": "7669925556293601541",
            "title": "  Backend Engineer  ",
            "description": "About the Team",
            "requirement": "Minimum Qualifications",
            "job_category": {"en_name": "R&D", "i18n_name": "R&D"},
            "job_subject": {"en_name": "PhD Graduates - 2027 Start"},
            "recruit_type": {"en_name": "Regular", "i18n_name": "Regular"},
            "city_info": {
                "en_name": "San Jose",
                "parent": {
                    "en_name": "California",
                    "parent": {"en_name": "United States of America", "parent": None},
                },
            },
        },
    )
    assert job.id == f"{scraper_class.ats}:{slug}:7669925556293601541"
    assert job.ats == scraper_class.ats
    assert job.company == scraper_class.COMPANY
    assert job.url == scraper_class(slug).job_url("7669925556293601541")
    assert job.title == "Backend Engineer"
    assert job.description == html_to_text("About the Team\n\nMinimum Qualifications")
    assert job.location == "San Jose, California, United States of America"
    assert job.remote is False
    # `job_subject` is a campus cohort, not a team: it never stands in for the department.
    assert job.department == "R&D"
    assert job.employment_type == "Regular"
    assert job.posted_at is None  # no date field exists anywhere in this API


@pytest.mark.parametrize("scraper_class,slug", BRANDS)
def test_a_posting_without_an_id_or_a_title_is_dropped(scraper_class, slug):
    scraper, _ = _scraper(
        scraper_class,
        slug,
        _paged_board_answer(
            [
                {"id": "1", "title": "  "},
                {"id": None, "title": "No id"},
                {"id": "2", "title": "Kept"},
            ],
            count=3,
        ),
    )
    assert [job.id.rsplit(":", 1)[1] for job in scraper.fetch()] == ["2"]


@pytest.mark.parametrize("city_info", [None, {}], ids=["null", "empty"])
@pytest.mark.parametrize("scraper_class,slug", BRANDS)
def test_missing_optional_fields_parse_to_none(scraper_class, slug, city_info):
    job = _parse_one(
        scraper_class,
        slug,
        {
            "id": "3",
            "title": "Bare-bones posting",
            "recruit_type": None,
            "job_category": None,
            "job_subject": {"en_name": "Project Intern"},
            "city_info": city_info,
            "description": None,
            "requirement": None,
        },
    )
    assert job.location is None
    assert job.remote is None  # no location to judge from
    assert job.department is None
    assert job.employment_type is None
    assert job.description is None


@pytest.mark.parametrize("scraper_class,slug", BRANDS)
def test_i18n_name_stands_in_for_a_missing_or_blank_en_name(scraper_class, slug):
    job = _parse_one(
        scraper_class,
        slug,
        {
            "id": "4",
            "title": "Posting",
            "job_category": {"en_name": "  ", "i18n_name": "Operations"},
            "recruit_type": {"i18n_name": "Intern"},
            "city_info": {"i18n_name": "Seattle", "parent": {"en_name": "Washington"}},
        },
    )
    assert job.department == "Operations"
    assert job.employment_type == "Intern"
    assert job.location == "Seattle, Washington"


@pytest.mark.parametrize(
    "chain,expected",
    [
        pytest.param(["Singapore", "Singapore", "Singapore"], "Singapore", id="AAA"),
        pytest.param(["Tokyo", "Tokyo", "Japan"], "Tokyo, Japan", id="AAB"),
        pytest.param(
            ["Hong Kong (China)", "Hong Kong Island", "Hong Kong, China"],
            "Hong Kong (China), Hong Kong Island, Hong Kong, China",
            id="ABC",
        ),
        pytest.param(
            ["Singapore", "Central", "Singapore"], "Singapore, Central", id="ABA"
        ),
    ],
)
@pytest.mark.parametrize("scraper_class,slug", BRANDS)
def test_location_names_each_place_once(scraper_class, slug, chain, expected):
    city_info = None
    for name in reversed(chain):
        city_info = {"en_name": name, "parent": city_info}
    job = _parse_one(
        scraper_class, slug, {"id": "5", "title": "Posting", "city_info": city_info}
    )
    assert job.location == expected


@pytest.mark.parametrize("scraper_class,slug", BRANDS)
def test_a_single_source_board_is_its_own_alias(scraper_class, slug):
    # ADR-0139: no sibling Board to alias against, so no redirect probe is made either.
    scraper, backend = _scraper(scraper_class, slug, _paged_board_answer([], count=0))
    assert scraper.alias_key() == slug
    assert backend.requests == []


@pytest.mark.parametrize("scraper_class,slug", BRANDS)
def test_the_listing_is_the_whole_job(scraper_class, slug):
    # `description` and `requirement` are on every listed row: no detail pass to make.
    assert scraper_class.has_detail_pass is False
