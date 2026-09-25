"""Tests for `headstart.scrapers.tesla`.

The fixture (`tesla_careers_state.json`) is five real postings trimmed from a live capture of
`GET https://www.tesla.com/cua-api/apps/careers/state`, captured 2026-09-11 via the CDP-capture
route the module docstring describes (no ordinary HTTP client can reach this endpoint — see
`docs/tesla/2026-09-11_api-measurement.md`). They were chosen to cover all four employment types
(`y`: fulltime/parttime/intern/seasonal) and, incidentally, a location id (`32046`, "Delivery
Operations Advisor") that is genuinely absent from `lookup.locations` in the live payload — one
of the 4 (of 1,309 distinct ids referenced by a real listing set) measured missing — so `parse`
must resolve that one to `None` rather than raising or dropping the Job.

The Chrome itself is faked (`_chrome_factory`) and the in-page fetch is replaced at `_read_batch`:
what is tested is the detail pass around them and the egress policy that decides when the Chrome
is relaunched on the spare egress. The live behaviour is recorded in ADR-0228.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from headstart import spare_egress
from headstart.scrapers import tesla
from headstart.scrapers.base import DetailBatchWalled, DetailLost, DetailRequest
from headstart.scrapers.registry import get_scraper
from headstart.scrapers.tesla import SLUG, TeslaScraper, TeslaWalled

FIXTURES = Path(__file__).parent / "fixtures"
SCRAPED_AT = "2026-01-01T00:00:00+00:00"


def _raw():
    with open(FIXTURES / "tesla_careers_state.json", encoding="utf-8") as fh:
        return json.load(fh)


def _jobs():
    scraper = get_scraper("tesla", SLUG, "Tesla")
    return {j.id.rsplit(":", 1)[1]: j for j in scraper.parse(_raw(), SCRAPED_AT)}


def test_registered_under_tesla():
    assert isinstance(get_scraper("tesla", SLUG, "Tesla"), TeslaScraper)


def test_slug_is_fixed_regardless_of_the_ledger_row():
    # ADR-0139: never discovered, never varying — whatever a ledger row's own tenant/url columns
    # say, the slug is always the one fixed host.
    assert TeslaScraper.slug_from("anything", "https://not-tesla.example/") == SLUG


def test_url_is_the_careers_search_page():
    scraper = TeslaScraper(SLUG, "Tesla")
    assert scraper.url() == "https://www.tesla.com/careers/search/"


def test_alias_key_is_the_boards_own_slug():
    # No sibling tenant to alias against (ADR-0139) — must not fall through to the base
    # implementation's live probe, which would hit the same Akamai wall this scraper works
    # around (see the module docstring).
    scraper = TeslaScraper(SLUG, "Tesla")
    assert scraper.alias_key() == SLUG


def test_every_employment_type_maps_through_the_lookup():
    jobs = _jobs()
    assert jobs["224501"].employment_type == "fulltime"
    assert jobs["277989"].employment_type == "parttime"
    assert jobs["281634"].employment_type == "intern"
    assert jobs["283222"].employment_type == "seasonal"


def test_department_resolves_through_the_lookup():
    job = _jobs()["224501"]
    assert job.department == "Tesla AI"


def test_location_resolves_through_the_lookup():
    job = _jobs()["224501"]
    assert job.location == "Palo Alto, California"


def test_a_location_id_missing_from_the_lookup_resolves_to_none():
    # id 32046 (on "Delivery Operations Advisor") is one of the 4 ids a live listing set
    # referenced that `lookup.locations` did not carry (module docstring).
    job = _jobs()["282326"]
    assert job.location is None
    assert job.remote is None  # is_remote(None) — no location to judge from


def test_no_posted_at_is_ever_emitted():
    # Neither the listing nor the per-job detail payload states a posting date (module
    # docstring) — `postUntilDate`/`pu` is a deadline, not a posted-at, and must not be
    # substituted in.
    for job in _jobs().values():
        assert job.posted_at is None


def test_description_is_none_in_this_version():
    # has_detail_pass is False: no per-job description fetch is attempted (module docstring).
    for job in _jobs().values():
        assert job.description is None


def test_job_url_slugifies_the_title_and_keeps_the_trailing_id():
    job = _jobs()["224501"]
    assert (
        job.url
        == "https://www.tesla.com/careers/search/job/ai-engineer-manipulation-optimus-224501"
    )


def test_a_title_with_no_alnum_characters_falls_back_to_the_bare_id():
    from headstart.scrapers.tesla import _job_url

    assert _job_url("123", "!!!") == "https://www.tesla.com/careers/search/job/123"


def test_job_id_is_namespaced_by_ats_and_slug():
    job = _jobs()["224501"]
    assert job.id == f"tesla:{SLUG}:224501"


def test_company_is_whatever_the_caller_passed():
    job = _jobs()["224501"]
    assert job.company == "Tesla"


def test_a_listing_missing_a_title_is_dropped():
    scraper = TeslaScraper(SLUG, "Tesla")
    raw = {
        "listings": [{"id": "1", "t": "", "dp": None, "l": None, "y": None}],
        "lookup": {},
    }
    assert scraper.parse(raw, SCRAPED_AT) == []


def test_a_listing_missing_an_id_is_dropped():
    scraper = TeslaScraper(SLUG, "Tesla")
    raw = {"listings": [{"id": None, "t": "Some Role"}], "lookup": {}}
    assert scraper.parse(raw, SCRAPED_AT) == []


def test_a_payload_with_no_listings_key_is_unreadable_not_empty():
    # A 200 with a body that isn't the state document's usual shape must not read as "this
    # board has zero jobs" — that would silently evict every already-indexed Tesla row.
    scraper = TeslaScraper(SLUG, "Tesla")
    assert scraper.parse({"cpr_chlge": "true"}, SCRAPED_AT) == []
    assert (
        scraper.truncated is None
    )  # unreadable, not truncated — see note_unreadable_board


def test_a_listings_key_present_but_empty_is_truncated_not_authoritative():
    # This board has never measured anywhere near zero (8,105-8,115 across two live runs) — an
    # empty-but-present `listings` is a capture defect, and with no stated total to measure a
    # shortfall against, it must mark_truncated rather than be accepted as the real state.
    scraper = TeslaScraper(SLUG, "Tesla")
    assert scraper.parse({"listings": [], "lookup": {}}, SCRAPED_AT) == []
    assert scraper.truncated is not None


# --- the batched detail pass (ADR-0228) --------------------------------------------------


class _FakeChrome:
    async def __aenter__(self):
        return self

    async def start(self):
        return None

    async def __aexit__(self, *exc):
        return None


@pytest.fixture
def chrome_launches(monkeypatch):
    """A fake Chrome, and the route (proxy) each launch was given."""
    launches = []

    def factory():
        launches.append(tesla._route)
        return _FakeChrome()

    monkeypatch.setattr(tesla, "_chrome_factory", factory)
    spare_egress.reset()
    tesla.shutdown()
    tesla._route = None
    yield launches
    tesla.shutdown()
    tesla._route = None
    spare_egress.reset()


def _detail_payload(**sections):
    return json.dumps(
        {
            "jobDescription": sections.get("description", "<p>Build <b>cars</b></p>"),
            "jobResponsibilities": sections.get(
                "responsibilities", "<ul><li>Weld</li></ul>"
            ),
            "jobRequirements": sections.get("requirements", ""),
            "jobCompensationAndBenefits": sections.get("compensation", "<p>$100k</p>"),
        }
    )


def test_a_detail_is_the_four_html_sections_as_one_text():
    scraper = TeslaScraper(SLUG, "Tesla")
    response = tesla._BatchResponse(200, _detail_payload())

    detail = scraper.read_detail({"id": "1"}, response)

    assert detail == {"description": "Build cars\n\nWeld\n\n$100k"}


def test_a_detail_with_no_section_text_is_lost_not_kept_empty():
    scraper = TeslaScraper(SLUG, "Tesla")
    empty = tesla._BatchResponse(
        200, _detail_payload(description="", responsibilities="", compensation="")
    )
    with pytest.raises(DetailLost, match="no description"):
        scraper.read_detail({"id": "1"}, empty)


def test_a_listing_with_no_id_forms_no_detail_request():
    scraper = TeslaScraper(SLUG, "Tesla")
    with pytest.raises(DetailLost, match="no job id"):
        scraper.detail_request({"t": "Engineer"})
    assert scraper.detail_request({"id": "42"}).url.endswith("/cua-api/careers/job/42")


def test_the_pass_reads_batches_of_tech_listings_and_parse_carries_the_description(
    monkeypatch, chrome_launches
):
    monkeypatch.setattr(tesla, "_BATCH_PAUSE_S", 0)
    state = _raw()
    state["listings"] += [
        {"id": "999", "t": "Store Barista", "dp": None, "l": None, "y": None},
        {"id": "998", "t": "Software Engineer", "dp": None, "l": None, "y": None},
    ]
    monkeypatch.setattr(tesla, "_fetch_state_json", lambda: state)
    asked = []

    def read_batch(urls, page_url):
        asked.append((list(urls), page_url))
        return [{"s": 200, "t": _detail_payload()} for _ in urls]

    monkeypatch.setattr(tesla, "_read_batch", read_batch)
    monkeypatch.delenv("HEADSTART_TECH_GATE", raising=False)
    scraper = TeslaScraper(SLUG, "Tesla")
    scraper.have_details = frozenset({"tesla:www.tesla.com:998"})

    jobs = scraper.parse(scraper.fetch_raw(), SCRAPED_AT)

    fetched = {url.rsplit("/", 1)[1] for urls, _ in asked for url in urls}
    described = {j.id.rsplit(":", 1)[1] for j in jobs if j.description}
    assert fetched == described == {"224501"}
    assert "999" not in fetched  # the ADR-0166 gate left the non-tech listing out
    assert (
        "998" not in fetched
    )  # ADR-0048: a tech Job whose description is already held
    assert any(j.id.endswith(":999") for j in jobs)  # ... but the Job is still listed
    assert all("/careers/search/job/" in page for _, page in asked)
    assert all(len(urls) <= tesla._BATCH_SIZE for urls, _ in asked)


def test_a_batch_maps_each_answer_and_a_network_error_is_an_exception(
    monkeypatch, chrome_launches
):
    monkeypatch.setattr(tesla, "_BATCH_PAUSE_S", 0)
    rows = [
        {"s": 200, "t": "{}"},
        {"s": 404, "t": ""},
        {"s": -1, "t": "Failed to fetch"},
    ]
    monkeypatch.setattr(tesla, "_read_batch", lambda urls, page_url: rows)
    scraper = TeslaScraper(SLUG, "Tesla")
    scraper._job_pages = {f"{tesla._DETAIL_URL}{i}": "page" for i in range(3)}

    out = scraper.fetch_detail_batch(
        [DetailRequest(f"{tesla._DETAIL_URL}{i}") for i in range(3)]
    )

    assert [getattr(r, "status_code", None) for r in out[:2]] == [200, 404]
    assert isinstance(out[2], RuntimeError) and str(out[2]) == "Failed to fetch"


def test_a_wall_on_every_route_stops_the_pass_rather_than_failing_the_board(
    monkeypatch,
):
    def walled(urls, page_url):
        raise TeslaWalled(403)

    monkeypatch.setattr(tesla, "_with_egress", lambda operation: operation())
    monkeypatch.setattr(tesla, "_read_batch", walled)
    scraper = TeslaScraper(SLUG, "Tesla")
    scraper._job_pages = {f"{tesla._DETAIL_URL}1": "page"}

    with pytest.raises(DetailBatchWalled, match="403"):
        scraper.fetch_detail_batch([DetailRequest(f"{tesla._DETAIL_URL}1")])


# --- the egress policy: direct first, the spare egress once the origin walls -------------

_SPARE = "socks5h://127.0.0.1:40000"


def _walls_then_answers(walls):
    seen = {"n": 0}

    def operation():
        seen["n"] += 1
        if seen["n"] <= walls:
            raise TeslaWalled(403)
        return "answered"

    return operation, seen


def test_the_first_wall_relaunches_the_chrome_on_the_spare_egress(chrome_launches):
    spare_egress.use_daemon(spare_egress.InMemoryEgressDaemon(_SPARE))
    operation, seen = _walls_then_answers(1)

    assert tesla._with_egress(operation) == "answered"

    assert chrome_launches == [None, _SPARE]  # direct first, then --proxy-server
    assert "tesla" in spare_egress.walled_groups()
    assert seen["n"] == 2


def test_a_wall_with_no_spare_egress_is_raised_after_one_attempt(chrome_launches):
    operation, seen = _walls_then_answers(9)

    with pytest.raises(TeslaWalled):
        tesla._with_egress(operation)

    assert seen["n"] == 1 and chrome_launches == [None]


def test_each_wall_on_the_spare_egress_rotates_it_until_the_attempts_are_spent(
    chrome_launches, monkeypatch
):
    spare_egress.use_daemon(spare_egress.InMemoryEgressDaemon(_SPARE))
    rotations = []
    monkeypatch.setattr(
        spare_egress, "rotate", lambda board=None, **kw: rotations.append(board) or True
    )
    operation, seen = _walls_then_answers(99)

    with pytest.raises(TeslaWalled):
        tesla._with_egress(operation)

    assert seen["n"] == tesla._EGRESS_ATTEMPTS + 1
    assert rotations == [SLUG] * (tesla._EGRESS_ATTEMPTS - 1)
    assert chrome_launches == [None] + [_SPARE] * tesla._EGRESS_ATTEMPTS


def test_a_rotation_that_yields_no_fresh_ip_gives_up_at_once(
    chrome_launches, monkeypatch
):
    spare_egress.use_daemon(spare_egress.InMemoryEgressDaemon(_SPARE))
    monkeypatch.setattr(spare_egress, "rotate", lambda board=None, **kw: False)
    operation, seen = _walls_then_answers(99)

    with pytest.raises(TeslaWalled):
        tesla._with_egress(operation)

    assert seen["n"] == 2  # direct, then the spare egress; its rotation failed


def test_the_chrome_is_launched_with_the_spare_egress_proxy_as_socks5(monkeypatch):
    added = []

    class _Options:
        start_timeout = 0

        def add_argument(self, arg):
            added.append(arg)

    monkeypatch.setitem(
        __import__("sys").modules,
        "pydoll.browser.options",
        type("m", (), {"ChromiumOptions": _Options}),
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "pydoll.browser",
        type("m", (), {"Chrome": lambda options: "chrome"}),
    )
    monkeypatch.setattr(tesla, "_route", _SPARE)

    assert tesla._default_chrome() == "chrome"
    assert "--proxy-server=socks5://127.0.0.1:40000" in added


def test_a_scraper_built_outside_the_pipeline_reads_no_details(monkeypatch):
    # have_details is None outside the pipeline: the tech gate and the held skip are off, so the
    # pass would fetch every posting from one IP — and one overshoot blocks the origin.
    monkeypatch.setattr(tesla, "_fetch_state_json", _raw)
    monkeypatch.setattr(
        tesla, "_read_batch", lambda *a: pytest.fail("a direct caller fetched details")
    )
    scraper = TeslaScraper(SLUG, "Tesla")

    jobs = scraper.parse(scraper.fetch_raw(), SCRAPED_AT)

    assert jobs and all(j.description is None for j in jobs)


class _FakeTab:
    """A tab that answers each in-page batch with the next scripted list of statuses."""

    def __init__(self, *batches):
        self.batches = list(batches)
        self.navigations = 0

    async def go_to(self, url, timeout=0):
        self.navigations += 1

    async def execute_script(self, script, **kwargs):
        rows = [{"s": s, "t": "{}"} for s in self.batches.pop(0)]
        return {"result": {"result": {"value": json.dumps(rows)}}}


def _read_with(monkeypatch, chrome_launches, *batches):
    monkeypatch.setattr(tesla, "_SETTLE_S", 0)
    tesla._ensure_started()
    tab = _FakeTab(*batches)
    tesla._tab = tab
    urls = [f"{tesla._DETAIL_URL}{i}" for i in range(len(batches[0]))]
    return tab, lambda: tesla._read_batch(urls, "https://example.invalid/job")


def test_a_batch_of_answers_is_read_without_navigating_again(
    monkeypatch, chrome_launches
):
    tab, read = _read_with(monkeypatch, chrome_launches, [200, 200, 404, 200])

    assert [r["s"] for r in read()] == [200, 200, 404, 200]
    assert (
        tab.navigations == 0
    )  # the tab was warmed when it opened; a lone 404 is a closed posting


def test_a_mostly_refused_batch_navigates_again_and_keeps_the_second_answer(
    monkeypatch, chrome_launches
):
    tab, read = _read_with(
        monkeypatch, chrome_launches, [404, 404, 404, 200], [200] * 4
    )

    assert [r["s"] for r in read()] == [200] * 4
    assert tab.navigations == 1


def test_a_batch_still_mostly_refused_after_navigating_is_a_wall(
    monkeypatch, chrome_launches
):
    tab, read = _read_with(monkeypatch, chrome_launches, [404] * 4, [404] * 4)

    with pytest.raises(TeslaWalled) as wall:
        read()

    assert wall.value.status == 404 and tab.navigations == 1


def test_one_403_in_a_batch_is_a_wall_and_discards_the_batch(
    monkeypatch, chrome_launches
):
    tab, read = _read_with(monkeypatch, chrome_launches, [200, 200, 403, 200])

    with pytest.raises(TeslaWalled) as wall:
        read()

    assert wall.value.status == 403 and tab.navigations == 0
