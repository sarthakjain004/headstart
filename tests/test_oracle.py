"""Tests for `headstart.scrapers.oracle`.

The fixtures are two real postings from `effx.fa.ca2.oraclecloud.com`, captured 2026-09-08:
`oracle_listing.json` is the API's own listing envelope trimmed to those two, and
`oracle_details.json` maps each id to its detail response. They were chosen to cover the two
workplace codes (`ORA_REMOTE`, `ORA_ON_SITE`) and, incidentally, a tenant that phrases its
schedule in French ("Temps plein") — which the Job model keeps as the provider states it.

The module had no tests at all before this, which is how a hardcoded `CX_1` site number that was
wrong for 929 of 1,331 hiring boards, and a description field capped at 1,000 characters, both
went unnoticed. The measurements behind every assertion are in
`docs/oracle/2026-09-08_api-measurement.md`.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from headstart.models import html_to_text
from headstart.scrapers.oracle import OracleScraper
from headstart.scrapers.registry import get_scraper

FIXTURES = Path(__file__).parent / "fixtures"
SCRAPED_AT = "2026-01-01T00:00:00+00:00"
HOST = "effx.fa.ca2.oraclecloud.com"
REMOTE_ID, ONSITE_ID = "1859", "1383"


def _listing():
    with open(FIXTURES / "oracle_listing.json", encoding="utf-8") as fh:
        return json.load(fh)


def _details():
    with open(FIXTURES / "oracle_details.json", encoding="utf-8") as fh:
        return {k: v["items"][0] for k, v in json.load(fh).items()}


def _raw():
    """What `fetch_raw` returns once the detail pass has run."""
    return {
        "requisitionList": _listing()["items"][0]["requisitionList"],
        "details": _details(),
    }


def _jobs():
    return {j.id.rsplit(":", 1)[1]: j for j in _scraper().parse(_raw(), SCRAPED_AT)}


def _scraper():
    return get_scraper("oracle", HOST, "Effx")


# --------------------------------------------------------------------------- the URL contract


def test_the_listing_url_carries_no_site_number():
    """The central fix. `siteNumber` FILTERS the board to one site; omitting it returns the
    host's whole set — the exact union of every site, verified on 596 hosts with zero
    counter-examples. A site number here can only ever shrink a Board, and it fails silently
    (HTTP 200, well-formed envelope), which is what hid it for so long."""
    url = _scraper().url()
    assert "siteNumber" not in url
    assert "CX_1" not in url
    assert "finder=findReqs;limit=200,offset=0" in url
    assert url.startswith(f"https://{HOST}/hcmRestApi/resources/latest/")


def test_the_slug_is_the_bare_host_with_no_site_override():
    """The old slug accepted `host/CX_2` to pick a site. Since a site number only narrows the
    board, that parameter had no safe use — a slug containing a slash is now just a host that
    will not resolve, not a second meaning."""
    scraper = get_scraper("oracle", HOST)
    assert scraper.slug == HOST
    assert scraper.board_key() == f"oracle:{HOST}"


def test_slug_from_prefers_the_url_host_over_a_non_host_tenant():
    """Discovery writes two shapes into the pool: the host itself, and a bare company label
    whose host lives only in `url`. Returning the tenant for the second (the base default)
    yields a slug that cannot be fetched."""
    assert (
        OracleScraper.slug_from(
            "akamai", "https://akamai.fa.us2.oraclecloud.com/hcmUI/x"
        )
        == "akamai.fa.us2.oraclecloud.com"
    )
    # Already a host, no URL to help: unchanged.
    assert OracleScraper.slug_from(HOST, "") == HOST


def test_slug_from_strips_a_query_string_off_the_url():
    """Goes through `models.host_of` rather than a local split. That shared definition exists
    because the scraper, the liveness prober and the ledger repair must agree, and the one time
    they did not, a query string surviving into the slug recorded 312 Personio boards live with
    zero jobs.

    The query must follow the **host directly** to test anything: with a path in between, a
    naive `split("/")` already drops it, which is why every oracle ledger row survives a local
    split today and why this looked untestable at first. No row has this shape yet — the point
    is to pin it before one does."""
    assert (
        OracleScraper.slug_from("x", "https://eeih.fa.us2.oraclecloud.com?utm_source=a")
        == "eeih.fa.us2.oraclecloud.com"
    )


def test_the_detail_url_quotes_the_id_and_omits_the_site():
    """`ById` with a quoted id is what the careers UI itself calls; the plausible-looking
    `findReqDetailById` returns HTTP 400. `siteNumber` is ignored on this endpoint — 454
    cross-pod calls omitting it all returned their requisition."""
    url = _scraper()._detail_url("142972")
    assert 'finder=ById;Id="142972"' in url
    assert "siteNumber" not in url
    assert "recruitingCEJobRequisitionDetails" in url


def test_the_job_url_is_the_careers_page_for_that_id():
    job = _jobs()[REMOTE_ID]
    assert job.url == (
        f"https://{HOST}/hcmUI/CandidateExperience/en/sites/CX_1/job/{REMOTE_ID}"
    )


# ------------------------------------------------------------------------------ field mapping


def test_description_comes_from_the_detail_body_not_the_listing_teaser():
    """`ShortDescriptionStr` is hard-capped at exactly 1,000 characters and present on 44.4% of
    rows; `ExternalDescriptionStr` has no cap and sits at p50 4,138. Reading the listing field
    is the difference between a description and a truncated summary."""
    job = _jobs()[REMOTE_ID]
    listed = _listing()["items"][0]["requisitionList"]
    teaser = next(r for r in listed if str(r["Id"]) == REMOTE_ID)["ShortDescriptionStr"]
    detail = _details()[REMOTE_ID]["ExternalDescriptionStr"]

    assert job.description is not None
    assert len(job.description) > len(teaser)
    # Assert on content, not just length: swapping the two sources back must not be able to
    # pass because the teaser happened to be long.
    assert job.description == html_to_text(detail)
    assert job.description != html_to_text(teaser)


def test_a_missing_detail_payload_falls_back_to_the_capped_teaser():
    """Enrichment, not a hard dependency: the Job is still listed and still emitted."""
    raw = {"requisitionList": _raw()["requisitionList"], "details": {}}
    jobs = {j.id.rsplit(":", 1)[1]: j for j in _scraper().parse(raw, SCRAPED_AT)}
    assert len(jobs) == 2
    assert jobs[REMOTE_ID].description  # the teaser, but not nothing
    assert jobs[REMOTE_ID].department is None
    assert jobs[REMOTE_ID].employment_type is None


def test_remote_reads_the_workplace_code_not_the_display_label():
    """The label is tenant-customised — `ORA_REMOTE` carries both "Remote" and "Work From Home",
    `ORA_ON_SITE` both "On-site" and "Work From Office" — so only the code can be matched."""
    jobs = _jobs()
    assert jobs[REMOTE_ID].remote is True
    assert jobs[ONSITE_ID].remote is False


def test_an_on_site_code_is_not_overridden_by_a_remote_looking_location():
    """The tenant's explicit answer wins over the location guess. Measured: of 2,716 rows
    stating a type, all 109 disagreements ran the other way (tenant says remote, location does
    not), so this ordering never contradicts observed data — but it is the direction that would
    silently mislabel if it ever did."""
    listed = [
        {
            "Id": "1",
            "Title": "X",
            "WorkplaceTypeCode": "ORA_ON_SITE",
            "PrimaryLocation": "Remote - Anywhere",
        }
    ]
    (job,) = _scraper().parse({"requisitionList": listed, "details": {}}, SCRAPED_AT)
    assert job.remote is False


def test_remote_falls_back_to_the_location_when_no_code_is_stated():
    listed = [{"Id": "1", "Title": "X", "PrimaryLocation": "Remote - India"}]
    (job,) = _scraper().parse({"requisitionList": listed, "details": {}}, SCRAPED_AT)
    assert job.remote is True


def test_department_prefers_category_and_employment_type_prefers_job_schedule():
    """Both are 0.0%/1.1% on the listing and only usable from the detail payload: `Category`
    76.2% against `JobFunction` 45.0%, and `JobSchedule` 80.6% against `JobType` 0.7%."""
    jobs = _jobs()
    assert jobs[REMOTE_ID].department == "Learning"
    assert jobs[REMOTE_ID].employment_type == "Full time"
    # Kept as the provider phrases it (the Job model's contract), not normalised across ATSes.
    assert jobs[ONSITE_ID].employment_type == "Temps plein"


def test_category_outranks_job_function_when_a_posting_states_both():
    """Constructed, not captured: neither fixture posting states a `JobFunction`, so the real
    payloads cannot pin this precedence on their own. The ordering is what the corpus census
    measured — `Category` 76.2% against `JobFunction` 45.0% — and without this the two could be
    swapped silently."""
    listed = [{"Id": "1", "Title": "X"}]
    details = {
        "1": {
            "Category": "Engineering",
            "JobFunction": "Technology",
            "JobSchedule": "Full time",
            "JobType": "Permanent",
        }
    }
    (job,) = _scraper().parse(
        {"requisitionList": listed, "details": details}, SCRAPED_AT
    )
    assert job.department == "Engineering"
    assert job.employment_type == "Full time"


def test_the_core_identity_fields_come_from_the_listing():
    job = _jobs()[REMOTE_ID]
    assert job.id == f"oracle:{HOST}:{REMOTE_ID}"
    assert job.ats == "oracle"
    assert job.title == "Content Associate"
    assert job.location
    assert job.posted_at


def test_parse_still_reads_the_pre_detail_pass_envelope():
    """`fetch_raw`'s shape changed; a recorded fixture or a direct caller may still hand parse
    the API's own `{"items": [...]}` envelope."""
    jobs = _scraper().parse(_listing(), SCRAPED_AT)
    assert len(jobs) == 2
    assert all(j.title for j in jobs)


# -------------------------------------------------------------------------------- pagination


class _FakeListing:
    """Serves pages from a canned id list, recording every offset asked for.

    Reads the offset out of the scraper's own `url()` — `_listing` calls `_get()` bare and lets
    the base default to it — so these tests exercise the real URL builder rather than a
    re-derived one, and a `url()` that stopped advancing its offset would fail here.
    """

    def __init__(self, total_ids, page_size, reported_total=None, lies_has_more=False):
        self.ids = [str(i) for i in range(total_ids)]
        self.page_size = page_size
        self.reported_total = total_ids if reported_total is None else reported_total
        self.lies_has_more = lies_has_more
        self.offsets = []
        self.short_at = set()
        # Whether to model the API's 10,000-row offset ceiling. On by default because that is
        # what Oracle does; switched off only to exercise `_MAX_PAGES`, which the ceiling
        # otherwise reaches first and so hides.
        self.ceiling = True
        self.scraper = None

    def __call__(self, url=None):
        offset = int((url or self.scraper.url()).split("offset=")[1])
        self.offsets.append(offset)
        if self.ceiling and offset + self.page_size > 10_000:
            page = []  # the API's own offset ceiling: a blank envelope, not an error
        else:
            page = self.ids[offset : offset + self.page_size]
        if (
            offset in self.short_at
        ):  # serve this page one row light, as Oracle really does
            page = page[:-1]
        return json.dumps(
            {
                "items": [
                    {
                        "TotalJobsCount": self.reported_total,
                        "requisitionList": [
                            {"Id": i, "Title": f"job {i}"} for i in page
                        ],
                    }
                ],
                "hasMore": False if self.lies_has_more else None,
            }
        )


def _paged(monkeypatch, fake):
    scraper = _scraper()
    fake.scraper = scraper
    monkeypatch.setattr(scraper, "_get", fake)
    # Detail pass off: pagination is what is under test here.
    monkeypatch.setattr(scraper, "fan_out", lambda items, fn, **kw: [None] * len(items))
    monkeypatch.setattr(
        scraper, "fan_out_async", lambda items, fn, **kw: [None] * len(items)
    )
    return scraper


def test_pagination_walks_every_page_until_the_total_is_met(monkeypatch):
    fake = _FakeListing(total_ids=450, page_size=200)
    scraper = _paged(monkeypatch, fake)
    raw = scraper.fetch_raw()
    assert len(raw["requisitionList"]) == 450
    assert fake.offsets == [0, 200, 400]
    assert scraper.truncated is None


def test_has_more_false_does_not_stop_a_board_that_is_not_done(monkeypatch):
    """Measured: `hasMore` came back false on a 248-posting board whose first page held 200.
    `TotalJobsCount` is the only honest terminator."""
    fake = _FakeListing(total_ids=248, page_size=200, lies_has_more=True)
    scraper = _paged(monkeypatch, fake)
    raw = scraper.fetch_raw()
    assert len(raw["requisitionList"]) == 248


def test_a_short_page_does_not_end_the_walk(monkeypatch):
    """The bug this class of terminator had. Oracle serves under-full pages mid-walk —
    `ebxr.fa.us2` answers offset 0 with 199 rows against a total of 420, reproducibly — and
    treating that as the end read 199 of 420. Measured across 40 multi-page boards, 12% hit one
    and 3,421 of 28,715 postings were lost."""
    fake = _FakeListing(total_ids=420, page_size=200)
    fake.short_at = {0}  # page 0 comes back with 199
    scraper = _paged(monkeypatch, fake)
    raw = scraper.fetch_raw()
    # 199 + 200 + 20 = 419, one short of the stated total, which is inside the slack.
    assert len(raw["requisitionList"]) == 419
    # Four fetches, not three: landing under the total costs one extra request to see the empty
    # page that proves the Board is exhausted. That is the price of the fix, paid by the Boards
    # whose count does not land exactly on the total (9 of 55 multi-page Boards measured) and by
    # any Board stating no total at all — a class measured at 0 of 120 Hiring Boards, so
    # theoretical.
    assert fake.offsets == [0, 200, 400, 600]
    assert scraper.truncated is None


def test_a_walk_ending_just_under_the_total_is_not_called_truncated(monkeypatch):
    """The API's counter is slightly inflated: of 55 multi-page Boards walked to an empty page,
    46 matched it exactly and 9 fell short. Marking those truncated every run would park them in
    ADR-0053's exclusion scope, which has no drain."""
    fake = _FakeListing(total_ids=298, page_size=200, reported_total=300)
    scraper = _paged(monkeypatch, fake)
    scraper.fetch_raw()
    assert scraper.truncated is None


def test_the_slack_scales_with_the_number_of_pages_walked(monkeypatch):
    """Where the constant actually decides, and why it is per-page rather than flat.

    A flat slack of 2 was the first attempt, and a review measured it wrong: the shortfall
    grows with the walk (7 rows over 15 pages, 5 over 20, 4 over 8), so a flat figure fits
    small Boards and falsely truncates large ones on every run. Both halves are pinned here —
    a gap equal to the page count is tolerated, one row more is not."""
    # 3 pages walked (200 + 94 + the empty one), so a 6-row gap sits exactly on the allowance.
    fake = _FakeListing(total_ids=294, page_size=200, reported_total=300)
    scraper = _paged(monkeypatch, fake)
    scraper.fetch_raw()
    assert scraper.truncated is None

    # Same walk, one row further under: now it is reported.
    fake = _FakeListing(total_ids=293, page_size=200, reported_total=300)
    scraper = _paged(monkeypatch, fake)
    scraper.fetch_raw()
    assert scraper.truncated and "293 of 300" in scraper.truncated

    # And the scaling itself, which the two cases above cannot see: a *shorter* walk earns a
    # *smaller* allowance, so the same 6-row gap over 2 pages IS reported. Without this, a flat
    # slack of 6 would satisfy both halves above and the per-page property would be untested.
    fake = _FakeListing(total_ids=194, page_size=200, reported_total=200)
    scraper = _paged(monkeypatch, fake)
    scraper.fetch_raw()
    assert scraper.truncated and "194 of 200" in scraper.truncated


def test_the_offset_ceiling_is_reported_even_when_the_slack_would_swallow_it(
    monkeypatch,
):
    """The slack must not mask the API's own ceiling.

    Oracle serves no offset past 10,000. A Board stating 10,001-10,102 therefore reads exactly
    10,000 — and 51 pages of allowance would swallow that gap, serving a knowingly short list as
    if it were whole. That is the single thing ADR-0053 exists to prevent, so the ceiling is
    reported whatever the slack says.
    """
    fake = _FakeListing(total_ids=10_050, page_size=200, reported_total=10_050)
    scraper = _paged(monkeypatch, fake)
    raw = scraper.fetch_raw()
    assert len(raw["requisitionList"]) == 10_000  # the ceiling, not the Board's end
    assert scraper.truncated and "no offset past 10,000" in scraper.truncated


def test_a_materially_short_walk_is_still_called_truncated(monkeypatch):
    """The allowance is a row per page, not a licence to lose hundreds. Real case: `etud.fa.us8`
    states 114 and serves 89 in a single page, and that 25-row gap must still be reported."""
    fake = _FakeListing(total_ids=150, page_size=200, reported_total=900)
    scraper = _paged(monkeypatch, fake)
    scraper.fetch_raw()
    assert scraper.truncated and "150 of 900" in scraper.truncated


def test_an_empty_page_ends_the_walk_when_no_total_is_stated(monkeypatch):
    """The `total and ...` guard: without it `len(reqs) >= 0` is true and the walk stops after
    one page — a silent truncation wearing the natural-end branch's clothes."""
    fake = _FakeListing(total_ids=250, page_size=200, reported_total=0)
    scraper = _paged(monkeypatch, fake)
    raw = scraper.fetch_raw()
    assert len(raw["requisitionList"]) == 250
    # With no total to satisfy, only an empty page can end the walk.
    assert fake.offsets == [0, 200, 400]


def test_a_board_short_of_its_own_total_is_marked_truncated(monkeypatch):
    """A short list that looks complete is what `index sync` reads as a delisting (ADR-0053)."""
    fake = _FakeListing(total_ids=150, page_size=200, reported_total=900)
    scraper = _paged(monkeypatch, fake)
    scraper.fetch_raw()
    assert scraper.truncated
    assert "150 of 900" in scraper.truncated


def test_hitting_the_page_cap_marks_truncated(monkeypatch):
    """A backstop that no real Board reaches: the API's offset ceiling stops a walk at 50 pages,
    half of `_MAX_PAGES`. Kept because the ceiling is measured on three Boards, not guaranteed
    across every tenant, and an unbounded pagination loop is not something to leave to that."""
    fake = _FakeListing(total_ids=10**6, page_size=200)
    fake.ceiling = False
    scraper = _paged(monkeypatch, fake)
    scraper.fetch_raw()
    assert scraper.truncated
    assert "page cap" in scraper.truncated


def test_a_detail_gap_does_not_mark_the_board_truncated(monkeypatch):
    """ADR-0053 is about the *list*, not the fields. Every posting is still listed and emitted,
    so the Board is whole even when no detail payload arrives."""
    fake = _FakeListing(total_ids=10, page_size=200)
    scraper = _scraper()
    fake.scraper = scraper
    monkeypatch.setattr(scraper, "_get", fake)
    monkeypatch.setattr(scraper, "fan_out", lambda items, fn, **kw: [None] * len(items))
    monkeypatch.setattr(
        scraper, "fan_out_async", lambda items, fn, **kw: [None] * len(items)
    )
    raw = scraper.fetch_raw()
    assert len(raw["requisitionList"]) == 10
    assert raw["details"] == {}
    assert scraper.truncated is None


def test_an_unknown_id_returns_none_rather_than_raising():
    """An id the tenant does not have answers 200 with `items: []`, not 404 — a real outcome to
    fold into the detail-gap count, not an error.

    And the empty answer is labelled, not merely counted: a Board whose ids have all gone stale
    and a Board the pod is refusing produce the same number of gaps."""
    scraper = OracleScraper("fa-abcd.fa.us2.oraclecloud.com")
    assert scraper._first_item(json.dumps({"items": []})) is None
    assert scraper.detail_losses == Counter({"no items on a 200": 1})
    assert scraper._first_item(json.dumps({"items": [{"Id": "7"}]})) == {"Id": "7"}
    assert scraper.detail_losses == Counter({"no items on a 200": 1})


def test_a_slug_that_still_carries_a_site_suffix_is_not_split_apart():
    """Migrated from `test_scrapers.py::test_oracle_parse`, which pinned the opposite: it passed
    `{host}/CX_2` and expected the site to be split off the slug and written into the job URL.
    That contract is gone — a site number only ever narrowed the Board — so a slug like this is
    now just a hostname that will not resolve, and nothing silently reinterprets it. The old
    fixture is kept because its ids (`NAG_002`) are the underscore shape that shows `\\d+` is the
    wrong id pattern."""
    slug = "fa-etqo-saasfaprod1.fa.ocs.oraclecloud.com/CX_2"
    with open(FIXTURES / "oracle_fa-etqo_cx2.json", encoding="utf-8") as fh:
        jobs = get_scraper("oracle", slug, "Oracle CE Tenant").parse(
            json.load(fh), SCRAPED_AT
        )
    assert len(jobs) == 2
    job = jobs[0]
    assert job.id == f"oracle:{slug}:NAG_002"
    assert (
        job.company == "Oracle CE Tenant"
    )  # LegalEmployer empty -> fallback to company
    assert job.title == "Executive - Non Voice - Nagpur"
    assert job.posted_at == "2026-03-16"
    # No detail payload in this fixture, so the capped teaser is all there is — HTML-stripped.
    assert job.description and "</" not in job.description


def test_the_scraper_declares_a_detail_pass():
    """`ExternalDescriptionStr` is unreachable from the listing at any expand, so a description
    genuinely can go missing here — which is what `has_detail_pass` tells the embed planner
    (ADR-0050)."""
    assert OracleScraper.has_detail_pass is True
    from headstart.scrapers.registry import detail_pass_atses

    assert "oracle" in detail_pass_atses()
