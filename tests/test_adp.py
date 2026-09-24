"""Tests for `headstart.scrapers.adp` (ADP Workforce Now career centers).

`fixtures/adp_responses.json` holds live responses captured 2026-09-23, trimmed (never invented):
`customFieldGroup` keeps only the fields the scraper reads, descriptions are cut to 600 chars.

- Cox & Palmer (`29e541b1-…/19000101_000001`) posts in `en_CA` and `fr_CA`, the same four
  postings under the same `ExternalJobID`s in each — the merge-by-id case. Its content-links
  `Locale` list, `client-features` (the `ClientName` "Cox & Palmer") and two details per
  language are recorded too.
- ManhattanLife (`d9cb495f-…/9200225097116_2`, a non-default career center) states 21 postings:
  a page of 20 at `$skip=1` and one at `$skip=21` — the page clamp and the 1-based walk.
- `detail_skeleton` is what a detail request for an id the Board does not hold returns: a 200
  with no title and no description.

Every assertion pins something measured in `docs/adp/2026-09-23_careercenter-measurement.md`.
"""

from __future__ import annotations

import json
from pathlib import Path

from headstart.scrapers.registry import get_scraper

FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "adp_responses.json").read_text("utf-8")
)
SCRAPED_AT = "2026-01-01T00:00:00+00:00"
COX = "29e541b1-08ed-43c1-87b4-9f4a909ec9b4/19000101_000001"
MANHATTAN = "d9cb495f-13ac-4fe9-b6a3-46d4af25b093/9200225097116_2"


def _rows(key: str, lang: str) -> list[dict]:
    return [{**r, "_lang": lang} for r in FIXTURES[key]["jobRequisitions"]]


def _cox_raw() -> dict:
    """What `fetch_raw` hands `parse` for Cox & Palmer: the English rows (each id's first
    language wins the merge) and the two English details recorded."""
    details = {
        k.rsplit("_", 1)[1]: v
        for k, v in FIXTURES.items()
        if k.startswith("coxpalmer_detail_en_CA_")
    }
    return {"rows": _rows("coxpalmer_listing_en_CA", "en_CA"), "details": details}


def _jobs(raw: dict, slug: str = COX) -> dict:
    scraper = get_scraper("adp", slug, slug)
    return {j.id.rsplit(":", 1)[1]: j for j in scraper.parse(raw, SCRAPED_AT)}


def test_a_listing_row_and_its_detail_become_a_job():
    jobs = _jobs(_cox_raw())
    assert len(jobs) == 4
    job = jobs["577400"]
    assert job.id == f"adp:{COX}:577400"
    assert job.ats == "adp"
    assert job.title == "Bilingual Litigation Legal Assistant"
    assert job.employment_type == "Full Time"
    assert job.posted_at.startswith("20")
    assert job.url == (
        "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html"
        "?cid=29e541b1-08ed-43c1-87b4-9f4a909ec9b4&ccId=19000101_000001&lang=en_CA"
        "&jobId=577400"
    )
    assert job.description and "Cox & Palmer" in job.description


# ------------------------------------------------------------------------------------- salary


def _paid(lo: float, hi: float, code: str, currency: str = "USD") -> dict:
    """A listing row's pay fields, in the exact shape the API serves them."""
    return {
        "payGradeRange": {
            "minimumRate": {"amountValue": lo, "currencyCode": currency},
            "maximumRate": {"amountValue": hi, "currencyCode": currency},
        },
        "customFieldGroup": {
            "codeFields": [{"codeValue": code, "nameCode": {"codeValue": "SalaryType"}}]
        },
    }


def _salary(row: dict) -> str | None:
    return get_scraper("adp", COX, COX)._salary_field(row)


def test_salary_is_the_pay_grade_range_with_its_period():
    """`payGradeRange` plus `SalaryType` HR/AN: 1,080 of 2,069 rows state one. The spelling is
    what `salary._field_generic` reads — a range, an ISO code, a phrase-shaped period."""
    from headstart import salary

    hourly = _salary(_paid(19.0, 20.5, "HR"))
    yearly = _salary(_paid(150000.0, 220000.0, "AN", "CAD"))
    assert hourly == "19-20.50 USD per-hour"
    assert yearly == "150000-220000 CAD per-year"
    assert salary.extract(hourly, None, ats="adp").min_annual == 19 * 2080
    assert salary.extract(yearly, None, ats="adp").currency == "CAD"


def test_an_up_to_range_is_a_lone_ceiling_and_is_refused():
    """ "Up to 43.46 (CAD) Hourly" arrives as min 0.0 (158 of 1,080 paid rows). Emitting it as a
    range serves 0 as the floor; emitting the ceiling alone serves it as a floor. Neither."""
    assert _salary(_paid(0.0, 43.46, "HR")) is None


def test_a_floor_with_no_ceiling_is_a_single_figure():
    """ "45000.00 (USD) Annually Onwards" arrives as max 0.0: a real floor, stated as one."""
    from headstart import salary

    value = _salary(_paid(45000.0, 0.0, "AN"))
    assert value == "45000 USD per-year"
    assert salary.extract(value, None, ats="adp").max_annual is None


def test_an_unobserved_period_yields_no_salary():
    """Only HR (668) and AN (412) were observed. Annual is the parser's default, so a daily or
    monthly figure passed through bare would be served at the wrong scale."""
    assert _salary(_paid(200.0, 250.0, "DA")) is None
    assert _salary({}) is None


# ---------------------------------------------------------------------------- location, link


def test_every_location_is_joined_and_the_blank_ones_dropped():
    """Real row (ivari, "Senior Underwriter"): the first entry is blank — all 48 location
    entries without a `shortName` in the 2,069-row sample were empty everywhere — and each name
    carries a leading space. One posting names up to 62 places; a Job cut to the first fails the
    location filter everywhere else."""
    blank = {"address": {"cityName": "", "postalCode": ""}, "nameCode": {}}
    places = [
        {"address": {"cityName": c}, "nameCode": {"shortName": f" {c}, AB, CA"}}
        for c in ("Edmonton", "Calgary")
    ]
    row = {
        **FIXTURES["coxpalmer_listing_en_CA"]["jobRequisitions"][0],
        "_lang": "en_CA",
        "requisitionLocations": [blank, *places],
    }
    (job,) = _jobs({"rows": [row], "details": {}}).values()
    assert job.location == "Edmonton, AB, CA; Calgary, AB, CA"


def test_remote_is_read_off_the_location_text():
    """No native field; 27 of 2,897 location names read "Remote- National, US"."""
    row = {
        **FIXTURES["coxpalmer_listing_en_CA"]["jobRequisitions"][0],
        "_lang": "en_US",
        "requisitionLocations": [{"nameCode": {"shortName": "Remote- National, US"}}],
    }
    (job,) = _jobs({"rows": [row], "details": {}}).values()
    assert job.remote is True


def test_the_job_url_matches_the_declared_shape():
    import re

    from headstart.scrapers.adp import ADPScraper

    scraper = get_scraper("adp", MANHATTAN, MANHATTAN)
    for lang in ("en_US", "fr_CA"):
        assert re.fullmatch(ADPScraper.url_shape, scraper.job_url("968476", lang))


# ------------------------------------------------------------------------------ the network half


class _Resp:
    def __init__(self, status: int, body: object):
        self.status_code = status
        self.text = body if isinstance(body, str) else json.dumps(body)

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            from headstart import http

            raise http.RequestsError(f"HTTP {self.status_code}")


class _FakeADP:
    """Routes the scraper's GETs to recorded fixtures, by path and query, and records each."""

    def __init__(self, listings: dict, *, locales=("en_CA", "fr_CA"), details=None):
        self.listings = listings  # lang -> {skip: body}
        self.locales = locales
        self.details = details or {}  # (lang, ext) -> body
        self.calls: list[tuple[str, dict]] = []
        self.refuse: list[int] = []  # statuses to answer, in order, before the real one

    def __call__(self, method, url, **kwargs):
        from urllib.parse import parse_qs, urlsplit

        parts = urlsplit(url)
        q = {k: v[0] for k, v in parse_qs(parts.query).items()}
        self.calls.append((parts.path, q))
        if self.refuse:
            return _Resp(self.refuse.pop(0), "Request blockedExceeded requests limit.")
        path = parts.path
        if path.endswith("content-links/career-center"):
            fields = [
                {"stringValue": loc, "nameCode": {"codeValue": "Locale"}}
                for loc in self.locales
            ]
            return _Resp(
                200,
                {
                    "contentLinks": [],
                    "meta": {"customFieldGroup": {"stringFields": fields}},
                },
            )
        if path.endswith("client-features"):
            return _Resp(200, FIXTURES["coxpalmer_client_features"])
        if path.endswith("/job-requisitions"):
            pages = self.listings.get(q["lang"], {})
            return _Resp(200, pages.get(int(q["$skip"]), FIXTURES["empty_envelope"]))
        ext = path.rsplit("/", 1)[1]
        return _Resp(
            200, self.details.get((q["lang"], ext), FIXTURES["detail_skeleton"])
        )


def _cox_fake(**kw) -> _FakeADP:
    details = {
        (k.split("_")[2] + "_" + k.split("_")[3], k.rsplit("_", 1)[1]): v
        for k, v in FIXTURES.items()
        if k.startswith("coxpalmer_detail_")
    }
    return _FakeADP(
        {
            "en_CA": {1: FIXTURES["coxpalmer_listing_en_CA"]},
            "fr_CA": {1: FIXTURES["coxpalmer_listing_fr_CA"]},
        },
        details=details,
        **kw,
    )


def _wired(monkeypatch, slug: str, fake: _FakeADP):
    """A scraper whose every request goes to ``fake``, on its own unpaced pacer, with the
    detail pass on the sync path (the async path is covered by the pacer tests)."""
    from headstart.scrapers import adp

    scraper = get_scraper("adp", slug, slug)
    monkeypatch.setattr(scraper, "_fetch", fake)
    monkeypatch.setattr(scraper, "pacer", adp._Pacer(0.0))
    monkeypatch.setattr(adp, "_WINDOW_S", 0.0)
    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", "0")
    return scraper


def test_every_language_the_center_lists_is_walked_and_merged_by_id(monkeypatch):
    """`lang` is a filter: Cox & Palmer answers its four postings under `en_CA` and the same four
    ids, translated, under `fr_CA`. The content-links `Locale` list names the languages (it
    covered the posting languages on 45 of 45 Boards checked); each id keeps its first — English
    — language, and the detail is asked in that language, since any other answers a skeleton."""
    fake = _cox_fake()
    scraper = _wired(monkeypatch, COX, fake)
    raw = scraper.fetch_raw()
    assert [r["_lang"] for r in raw["rows"]] == ["en_CA"] * 4
    listed = [q["lang"] for p, q in fake.calls if p.endswith("/job-requisitions")]
    assert listed == ["en_CA", "fr_CA"]
    detail_langs = {q["lang"] for p, q in fake.calls if p.rsplit("/", 1)[1].isdigit()}
    assert detail_langs == {"en_CA"}
    assert set(raw["details"]) == {"577400", "575647"}
    assert scraper.truncated is None


def test_a_posting_in_a_second_language_only_is_kept_in_it(monkeypatch):
    fr = json.loads(json.dumps(FIXTURES["coxpalmer_listing_fr_CA"]))
    only_fr = fr["jobRequisitions"][0]
    for field in only_fr["customFieldGroup"]["stringFields"]:
        if field["nameCode"]["codeValue"] == "ExternalJobID":
            field["stringValue"] = "999001"
    fake = _cox_fake()
    fake.listings["fr_CA"] = {1: fr}
    raw = _wired(monkeypatch, COX, fake).fetch_raw()
    langs = {
        next(
            f["stringValue"]
            for f in r["customFieldGroup"]["stringFields"]
            if f["nameCode"]["codeValue"] == "ExternalJobID"
        ): r["_lang"]
        for r in raw["rows"]
    }
    assert langs["999001"] == "fr_CA"
    assert len(langs) == 5


def test_an_empty_locale_list_falls_back_to_the_observed_languages(monkeypatch):
    fake = _cox_fake(locales=())
    _wired(monkeypatch, COX, fake).fetch_raw()
    listed = [q["lang"] for p, q in fake.calls if p.endswith("/job-requisitions")]
    assert listed == ["en_US", "en_CA", "fr_CA", "es_US"]


def test_the_walk_is_one_based_in_pages_of_twenty_until_the_stated_total(monkeypatch):
    """`$top` clamps at 20 silently and `$skip` is 1-based (`$skip=0` drops a row): ManhattanLife
    states 21 and is read as 20 at `$skip=1`, then 1 at `$skip=21`."""
    fake = _FakeADP(
        {
            "en_US": {
                1: FIXTURES["manhattanlife_page_skip1"],
                21: FIXTURES["manhattanlife_page_skip21"],
            }
        },
        locales=("en_US",),
    )
    scraper = _wired(monkeypatch, MANHATTAN, fake)
    raw = scraper.fetch_raw()
    skips = [q["$skip"] for p, q in fake.calls if p.endswith("/job-requisitions")]
    assert skips == ["1", "21"]
    assert all(
        q["$top"] == "20" for p, q in fake.calls if p.endswith("/job-requisitions")
    )
    assert len(raw["rows"]) == 21
    assert scraper.truncated is None


def test_a_walk_short_of_the_stated_total_is_marked_truncated(monkeypatch):
    fake = _FakeADP(
        {"en_US": {1: FIXTURES["manhattanlife_page_skip1"]}}, locales=("en_US",)
    )
    scraper = _wired(monkeypatch, MANHATTAN, fake)
    scraper.fetch_raw()
    assert scraper.truncated and "20 of 21" in scraper.truncated


def test_a_closed_posting_answers_a_skeleton_and_ships_without_a_description(
    monkeypatch,
):
    """A detail for an id the Board no longer holds is a 200 with no title and no description —
    a silent empty. It is counted as a loss, the Job still ships, the Board is not truncated."""
    fake = _cox_fake()
    fake.details = {}
    scraper = _wired(monkeypatch, COX, fake)
    raw = scraper.fetch_raw()
    assert raw["details"] == {}
    assert scraper.detail_losses["no requisitionTitle on a 200"] == 4
    assert scraper.truncated is None
    assert all(j.description is None for j in scraper.parse(raw, SCRAPED_AT))


def test_the_tech_gate_and_the_description_store_skip_details(monkeypatch):
    """No department on either surface and the detail adds only the description, so
    `is_tech(title, None)` on the listing is `filter_tech`'s own question (an exact gate), and
    a description already stored is not fetched again (ADR-0048)."""
    monkeypatch.delenv("HEADSTART_TECH_GATE", raising=False)
    fake = _FakeADP(
        {
            "en_US": {
                1: FIXTURES["manhattanlife_page_skip1"],
                21: FIXTURES["manhattanlife_page_skip21"],
            }
        },
        locales=("en_US",),
    )
    scraper = _wired(monkeypatch, MANHATTAN, fake)
    scraper.have_details = frozenset()
    raw = scraper.fetch_raw()
    fetched = {
        p.rsplit("/", 1)[1] for p, q in fake.calls if p.rsplit("/", 1)[1].isdigit()
    }
    titles = {
        next(
            f["stringValue"]
            for f in r["customFieldGroup"]["stringFields"]
            if f["nameCode"]["codeValue"] == "ExternalJobID"
        ): r["requisitionTitle"]
        for r in raw["rows"]
    }
    assert {titles[i] for i in fetched} == {
        "IT Project Manager (Mid-level)",
        "Senior IT Security Analyst",
        "Systems Administrator",
    }
    held = next(iter(fetched))
    fake.calls.clear()
    scraper.have_details = frozenset({f"adp:{MANHATTAN}:{held}"})
    scraper.fetch_raw()
    again = {
        p.rsplit("/", 1)[1] for p, q in fake.calls if p.rsplit("/", 1)[1].isdigit()
    }
    assert held not in again and len(again) == len(fetched) - 1


# ---------------------------------------------------------------------------- the rate limit


def test_a_429_rests_the_process_through_the_window_and_retries(monkeypatch):
    """The host refuses the 201st request in a fixed 60 s window with a bare 429 and no
    Retry-After. One refusal rests the shared pacer and the same request is tried again."""
    from headstart.scrapers import adp

    fake = _cox_fake()
    fake.refuse = [429]
    scraper = _wired(monkeypatch, COX, fake)
    rests: list[float] = []
    monkeypatch.setattr(scraper.pacer, "rest", rests.append)
    raw = scraper.fetch_raw()
    assert rests == [adp._WINDOW_S]
    assert len(raw["rows"]) == 4 and scraper.truncated is None


def test_a_429_that_outlasts_every_window_truncates_rather_than_ends_short(monkeypatch):
    """A Board that cannot be finished must not read as complete: its missing ids would be
    evicted as closed. The rows already read still ship."""
    from headstart.scrapers import adp

    fake = _FakeADP(
        {
            "en_US": {
                1: FIXTURES["manhattanlife_page_skip1"],
                21: FIXTURES["manhattanlife_page_skip21"],
            }
        },
        locales=("en_US",),
    )
    scraper = _wired(monkeypatch, MANHATTAN, fake)
    real = fake.__call__

    def refuse_page_two(method, url, **kw):
        if "%24skip=21" in url:
            fake.calls.append(("refused", {}))
            return _Resp(429, "Request blockedExceeded requests limit.")
        return real(method, url, **kw)

    monkeypatch.setattr(scraper, "_fetch", refuse_page_two)
    raw = scraper.fetch_raw()
    assert len(raw["rows"]) == 20
    assert scraper.truncated and "rate-limited" in scraper.truncated
    assert sum(1 for p, _ in fake.calls if p == "refused") == adp._TRIES


def test_the_pacer_is_one_budget_for_every_board_in_the_process(monkeypatch):
    """`harvest` runs Boards concurrently in one process, so a per-Board delay multiplies. Two
    Boards fetched at once on the real class-level pacer start their requests `spacing` apart."""
    import threading

    from headstart.scrapers import adp

    monkeypatch.setattr(adp._PACER, "spacing", 0.05)
    monkeypatch.setattr(adp, "_WINDOW_S", 0.0)
    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", "0")
    starts: list[float] = []
    lock = threading.Lock()
    scrapers = []
    for slug in (COX, MANHATTAN):
        fake = _cox_fake()
        s = get_scraper("adp", slug, slug)

        def timed(method, url, _fake=fake, **kw):
            import time

            with lock:
                starts.append(time.monotonic())
            return _fake(method, url, **kw)

        monkeypatch.setattr(s, "_fetch", timed)
        scrapers.append(s)
    assert scrapers[0].pacer is scrapers[1].pacer is adp._PACER
    threads = [threading.Thread(target=s.fetch_raw) for s in scrapers]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(starts) == 14  # 7 per Board: content-links, two listings, four details
    # One shared budget: 14 starts take 13 slots end to end. Two private pacers would overlap
    # and finish in about half that (6 slots each, side by side). Measured on the span rather
    # than each gap, because thread wake-up jitter moves single gaps by a few milliseconds.
    assert max(starts) - min(starts) >= 13 * 0.05 * 0.9


def test_the_async_detail_path_draws_on_the_same_pacer(monkeypatch):
    """The detail pass is multiplexed by default; its requests must wait on the same slots the
    listing walk and every other Board use, or the async path spends a budget of its own."""
    from headstart.scrapers import adp

    fake = _cox_fake()
    scraper = get_scraper("adp", COX, COX)
    monkeypatch.setattr(scraper, "_fetch", fake)
    monkeypatch.delenv("HEADSTART_ASYNC_FANOUT", raising=False)
    reserved: list[int] = []
    pacer = adp._Pacer(0.0)
    real = pacer.reserve

    def counted():
        reserved.append(1)
        return real()

    monkeypatch.setattr(pacer, "reserve", counted)
    monkeypatch.setattr(scraper, "pacer", pacer)

    async def fetch_async(session, method, url, **kw):
        return fake(method, url, **kw)

    monkeypatch.setattr(scraper, "_fetch_async", fetch_async)
    raw = scraper.fetch_raw()
    assert set(raw["details"]) == {"577400", "575647"}
    # 1 content-links + 2 listings on the sync path, 4 details on the async one.
    assert len(reserved) == 7


# ------------------------------------------------------------------------------ company name


def test_the_company_is_the_client_name_adp_states_for_the_board(monkeypatch):
    """No page title, og: tag, JSON-LD or posting field names the employer (5 career centers
    rendered in Chromium, board, job and apply steps). `client-features` does, once per Board:
    `ClientName` was present on 120 of 120 sampled centers, and here reads "Cox & Palmer"."""
    fake = _cox_fake()
    scraper = _wired(monkeypatch, COX, fake)
    seen: list[dict] = []

    def fetch(method, url, **kw):
        seen.append(kw)
        return fake(method, url, **kw)

    monkeypatch.setattr(scraper, "_fetch", fetch)
    scraper.resolve_company()
    assert scraper.company == "Cox & Palmer"
    assert [p for p, _ in fake.calls] == [
        "/mascsr/default/careercenter/public/events/staffing/client-features"
    ]
    assert seen[0]["attempts"] == 1 and seen[0]["marks_wall"] is False


def test_a_failed_name_lookup_leaves_the_slug(monkeypatch):
    fake = _cox_fake()
    fake.refuse = [500]
    scraper = _wired(monkeypatch, COX, fake)
    scraper.resolve_company()
    assert scraper.company == COX


def test_the_vendor_name_is_never_served_as_the_employer():
    from headstart import company_name

    assert company_name.from_title("adp", "ADP", COX) is None
    assert company_name.from_title("adp", "Automatic Data Processing", COX) is None
    assert (
        company_name.from_title("adp", "2LIFE COMMUNITIES", COX) == "2LIFE COMMUNITIES"
    )


def test_a_rate_limited_name_lookup_rests_the_pacer_once_and_keeps_the_slug(
    monkeypatch,
):
    """A display name is not worth a second window (ADR-0114's one-attempt rule), but its 429
    still means the window is spent for every Board, so the pacer rests anyway."""
    fake = _cox_fake()
    fake.refuse = [429, 429, 429]
    scraper = _wired(monkeypatch, COX, fake)
    rests: list[float] = []
    monkeypatch.setattr(scraper.pacer, "rest", rests.append)
    scraper.resolve_company()
    assert scraper.company == COX
    assert len(fake.calls) == 1 and len(rests) == 1


def test_a_slot_claimed_before_a_rest_is_not_spent_inside_the_window():
    """A request queued before another Board's 429 would otherwise fire into the refused window.
    Waking inside a rest re-claims a slot, which the rest has already put past its end."""
    import threading
    import time

    from headstart.scrapers import adp

    pacer = adp._Pacer(0.05)
    pacer.reserve()  # someone else holds the current slot, so the next waiter queues 50 ms
    started = time.monotonic()
    woke: list[float] = []
    waiter = threading.Thread(
        target=lambda: (pacer.wait(), woke.append(time.monotonic() - started))
    )
    waiter.start()
    time.sleep(0.01)
    pacer.rest(0.3)  # another Board's 429 lands while the waiter sleeps on its old slot
    waiter.join()
    assert woke[0] >= 0.3


# ------------------------------------------------------------------------ walk edges, merge order


def _one_lang(pages: dict) -> _FakeADP:
    return _FakeADP({"en_US": pages}, locales=("en_US",))


def test_a_posting_repeated_across_pages_does_not_end_the_walk_early(monkeypatch):
    """Progress is unique ids: a row that shifts onto the next page mid-walk is read twice, and
    counting rows would call 21 of 21 read after seeing 20 distinct postings."""
    first = FIXTURES["manhattanlife_page_skip1"]
    repeat = {**FIXTURES["manhattanlife_page_skip21"]}
    repeat["jobRequisitions"] = [first["jobRequisitions"][-1]]
    fake = _one_lang({1: first, 21: repeat, 22: FIXTURES["manhattanlife_page_skip21"]})
    scraper = _wired(monkeypatch, MANHATTAN, fake)
    raw = scraper.fetch_raw()
    skips = [q["$skip"] for p, q in fake.calls if p.endswith("/job-requisitions")]
    assert skips == ["1", "21", "22"]
    assert len(raw["rows"]) == 21 and scraper.truncated is None


def test_rows_without_a_stated_total_are_not_reported_whole(monkeypatch):
    """Every page with rows measured stated a positive total. One that states none, or 0, gives
    no way to know the list is whole, so the Board leaves the eviction scope (ADR-0053)."""
    for meta in ({}, {"meta": {"totalNumber": 0}}):
        page = {
            "jobRequisitions": FIXTURES["manhattanlife_page_skip1"]["jobRequisitions"]
        }
        page.update(meta)
        scraper = _wired(monkeypatch, MANHATTAN, _one_lang({1: page}))
        raw = scraper.fetch_raw()
        assert len(raw["rows"]) == 20
        assert scraper.truncated and "no stated total" in scraper.truncated


def test_languages_put_english_first_then_alphabetical():
    """A translated posting keeps its English version; between `en_CA` and `en_US` (both used on
    2 of 155 Boards) the order is alphabetical — arbitrary but stable."""
    from headstart.scrapers.adp import languages_of

    body = {
        "meta": {
            "customFieldGroup": {
                "stringFields": [
                    {"stringValue": v, "nameCode": {"codeValue": "Locale"}}
                    for v in ("fr_CA", "en_US", "es_US", "en_CA", "en_US")
                ]
            }
        }
    }
    assert languages_of(body) == ["en_CA", "en_US", "es_US", "fr_CA"]


def test_an_hourly_rate_labelled_annual_by_the_tenant_yields_no_salary():
    """ "18.00 To 20.00 (CAD) Annually" is hourly money under `AN`. It is emitted as stated and
    the parser's plausibility floor refuses it, rather than serving $18 a year."""
    from headstart import salary

    value = _salary(_paid(18.0, 20.0, "AN", "CAD"))
    assert value == "18-20 CAD per-year"
    assert salary.extract(value, None, ats="adp") is None


def test_the_async_path_re_claims_a_slot_that_a_rest_overtook():
    import asyncio
    import time

    from headstart.scrapers import adp

    pacer = adp._Pacer(0.05)
    pacer.reserve()

    async def run() -> float:
        started = time.monotonic()
        waiter = asyncio.create_task(pacer.wait_async())
        await asyncio.sleep(0.01)
        pacer.rest(0.3)
        await waiter
        return time.monotonic() - started

    assert asyncio.run(run()) >= 0.3
