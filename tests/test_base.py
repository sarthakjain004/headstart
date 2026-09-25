import asyncio
import json
import logging

import pytest
from fake_fetcher import FakeFetcher, FakeResponse

from headstart import company_name, fanout_stats, http
from headstart.models import Job
from headstart.scrapers import base
from headstart.scrapers.base import (
    DEFAULT_REQUEST_HEADERS,
    BaseScraper,
    DetailLost,
    DetailRequest,
    DetailWithoutDescription,
)


class _StubScraper(BaseScraper):
    """Minimal concrete scraper for exercising BaseScraper instance methods."""

    ats = "stub"
    url_shape = r"https://example\.invalid/jobs/\w+"

    def url(self):
        return "https://example.invalid/jobs"

    def parse(self, raw, scraped_at):
        return []

    def _salary_field(self, raw):
        return None

    def job_url(self, native_id):
        return f"https://example.invalid/jobs/{native_id}"


def test_report_detail_gaps_logs_missing_counts(caplog):
    caplog.set_level(logging.INFO, logger="headstart.scrapers.stub")
    _StubScraper("acme").report_detail_gaps(["desc", None, None], what="descriptions")
    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.name == "headstart.scrapers.stub"
    assert record.levelno == logging.INFO
    assert "stub:acme" in record.getMessage()  # the board key
    assert "2/3 descriptions missing" in record.getMessage()


def test_report_detail_gaps_silent_when_complete(caplog):
    caplog.set_level(logging.INFO, logger="headstart.scrapers.stub")
    _StubScraper("acme").report_detail_gaps(["a", "b"], what="descriptions")
    assert caplog.records == []


def test_note_detail_loss_appends_causes_without_changing_the_leading_count(caplog):
    """The labelled causes ride on the SAME line, after a byte-identical `N/M {what} missing`.

    Several docs, probes and one ADR quote that prefix verbatim, and a scraper that has not
    opted into `note_detail_loss` must still emit exactly the line it always did — which is why
    the causes are appended rather than the line reworded."""
    caplog.set_level(logging.INFO, logger="headstart.scrapers.stub")
    scraper = _StubScraper("acme")
    scraper.note_detail_loss("HTTP 403")
    scraper.note_detail_loss("HTTP 403")
    scraper.note_detail_loss("no JSON-LD on a 200")
    scraper.report_detail_gaps([None, None, None], what="detail fields")
    message = caplog.records[0].getMessage()
    assert message == (
        "stub:acme: 3/3 detail fields missing (HTTP 403 x2, no JSON-LD on a 200 x1)"
    )


def test_every_cause_is_named_with_no_cap(caplog):
    """A cap used to keep only the top 4 causes behind a sized "…N more" tail, but measured
    production cardinality never approached it (9 distinct causes fleet-wide, ~1.2 per Board
    per run — see loss_breakdown's docstring), so it was dropped: all six causes here show,
    largest first, summing to the missing count — 10+9+8+7+6+5 == 45."""
    caplog.set_level(logging.INFO, logger="headstart.scrapers.stub")
    scraper = _StubScraper("acme")
    for cause, n in (("a", 10), ("b", 9), ("c", 8), ("d", 7), ("e", 6), ("f", 5)):
        for _ in range(n):
            scraper.note_detail_loss(cause)
    scraper.report_detail_gaps([None] * 45, what="details")
    assert (
        "45/45 details missing (a x10, b x9, c x8, d x7, e x6, f x5)"
        in caplog.records[0].getMessage()
    )


def test_an_unlabelled_remainder_is_counted_rather_than_dropped(caplog):
    """A partial tally must not read as a full account of the gap: whatever reached no label is
    named `unlabelled`, so the parenthetical always totals `missing`."""
    caplog.set_level(logging.INFO, logger="headstart.scrapers.stub")
    scraper = _StubScraper("acme")
    scraper.note_detail_loss("HTTP 500")
    scraper.report_detail_gaps([None] * 4, what="details")
    assert (
        "4/4 details missing (unlabelled x3, HTTP 500 x1)"
        in caplog.records[0].getMessage()
    )


def test_fan_out_isolates_failures_and_preserves_input_order():
    def fn(x):
        if x == 2:
            raise RuntimeError("boom")
        return x * 10

    # 2 fails -> default; results align to INPUT order despite out-of-order completion.
    assert BaseScraper.fan_out([1, 2, 3], fn, workers=3) == [10, None, 30]


def test_fan_out_empty_returns_empty():
    assert BaseScraper.fan_out([], lambda x: x) == []


def test_fan_out_uses_given_default():
    def boom(_):
        raise ValueError

    assert BaseScraper.fan_out([1], boom, default={}) == [{}]


def test_fan_out_runs_every_item():
    out = BaseScraper.fan_out(list(range(20)), lambda x: x + 1, workers=4)
    assert sorted(out) == list(range(1, 21))


# The async fan-out tests below call methods on _StubScraper instances rather than the class:
# `fan_out_async` is an instance method because its concurrency falls back to the scraper's own
# `detail_workers` declaration.


def test_fan_out_async_isolates_failures_and_preserves_input_order():
    # same contract as fan_out; fn ignores the session, so no network is touched
    async def fn(_session, x):
        if x == 2:
            raise RuntimeError("boom")
        return x * 10

    out = _StubScraper("x").fan_out_async([1, 2, 3], fn, concurrency=2)
    assert out == [10, None, 30]


def test_fan_out_async_empty_returns_empty():
    async def fn(_session, x):
        return x

    assert _StubScraper("x").fan_out_async([], fn) == []


def _spy_concurrency(monkeypatch):
    """Replace _gather_async with a no-network spy; returns the dict the width lands in."""
    seen = {}

    def spy(items, f, concurrency, default, item_done=None):
        seen["concurrency"] = concurrency

        async def _noop():
            return [default] * len(items)

        return _noop()

    monkeypatch.setattr(BaseScraper, "_gather_async", staticmethod(spy))
    return seen


async def _echo(_session, x):
    return x


def test_fan_out_async_falls_back_to_the_scrapers_own_bound(monkeypatch):
    """The trap ADR-0047 found on Eightfold and that also cost Workday: the sync path bounded
    itself to a handful of workers "since they hit one host" while the async path silently took
    the shared 100-stream default against that same host. A scraper that declares a bound must get
    that bound on both paths without having to remember a keyword at every call site."""
    seen = _spy_concurrency(monkeypatch)
    monkeypatch.delenv("HEADSTART_H2_STREAMS", raising=False)

    class _Bounded(_StubScraper):
        detail_workers = 6

    _Bounded("x").fan_out_async([1], _echo)
    assert seen["concurrency"] == 6, (
        "must inherit the scraper's own politeness bound, not 100"
    )

    class _Measured(_Bounded):
        detail_streams = 25  # measured async headroom overrides the sync bound

    _Measured("x").fan_out_async([1], _echo)
    assert seen["concurrency"] == 25

    # The operator's escape hatch outranks the declaration...
    monkeypatch.setenv("HEADSTART_H2_STREAMS", "7")
    _Measured("x").fan_out_async([1], _echo)
    assert seen["concurrency"] == 7
    # ...and an explicit argument outranks even the escape hatch — a call site that pins its
    # width (trakstar behind DataDome) is stating a host constraint no operator flag may widen.
    _Measured("x").fan_out_async([1], _echo, concurrency=3)
    assert seen["concurrency"] == 3


def test_fan_out_async_keeps_the_global_default_when_nothing_is_declared(monkeypatch):
    """A scraper with no detail pass declares no bound; it must not silently drop to something
    tiny, or every such fan-out gets slower for no reason."""
    from headstart.scrapers.base import _DEFAULT_H2_STREAMS

    seen = _spy_concurrency(monkeypatch)
    monkeypatch.delenv("HEADSTART_H2_STREAMS", raising=False)
    _StubScraper("x").fan_out_async([1], _echo)
    assert seen["concurrency"] == _DEFAULT_H2_STREAMS


def test_fan_out_async_narrows_once_this_scrapers_egress_group_has_walled(monkeypatch):
    """Every step of the chain above is static, so a shard the origin had already refused fanned
    out exactly as wide as one it was still serving — 4 of 15 shards on run 32249345870 took 80%
    of its 94,110 rate-limit retries that way (#195). The clamp keys on the group the Board fetcher binds, so
    it reaches only the scrapers whose requests carry one."""
    from headstart import spare_egress

    seen = _spy_concurrency(monkeypatch)
    monkeypatch.delenv("HEADSTART_H2_STREAMS", raising=False)

    class _Walls(_StubScraper):
        detail_streams = 25
        egress_fallback_on = frozenset({429})  # ats "stub", inherited

    spare_egress.reset()
    try:
        _Walls("x").fan_out_async([1], _echo)
        assert seen["concurrency"] == 25, (
            "unwalled, the scraper's own measured width stands"
        )
        _StubScraper("x").fan_out_async([1], _echo)
        opted_out = seen["concurrency"]

        spare_egress.mark_walled("stub", 429)
        _Walls("x").fan_out_async([1], _echo)
        assert seen["concurrency"] == spare_egress._WALLED_STREAM_WIDTH
        # ...and the same wall leaves a scraper that never opted into the fallback alone: its
        # requests name no group, so there is nothing this could have learned about its origin.
        _StubScraper("x").fan_out_async([1], _echo)
        assert seen["concurrency"] == opted_out
    finally:
        spare_egress.reset()


def test_async_fanout_enabled_on_by_default(monkeypatch):
    # ADR-0016: async is the default; HEADSTART_ASYNC_FANOUT=0 is the escape hatch to sync
    monkeypatch.delenv("HEADSTART_ASYNC_FANOUT", raising=False)
    assert BaseScraper.async_fanout_enabled() is True
    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", "0")
    assert BaseScraper.async_fanout_enabled() is False
    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", "1")
    assert BaseScraper.async_fanout_enabled() is True


# --- spare-egress opt-in (ADR-0063) ---------------------------------------------------------------


class _WalledScraper(_StubScraper):
    ats = "walled"
    egress_fallback_on = frozenset({403, 405})


def test_egress_is_inert_unless_the_scraper_opts_in():
    """Routing has to stay inert: every ATS that has never walled us must keep making exactly the
    request it made before this existed. Only ``egress_board`` — pure log attribution, no routing
    effect — rides along regardless."""
    assert _StubScraper("acme").board_fetcher.egress_binding() == {
        "egress_board": "stub:acme"
    }


def test_egress_opt_in_keys_on_the_ats_not_the_board():
    # per-Board marking would make each of a shard's Boards spend its own attempts rediscovering
    # a wall the first one already proved (the metering is per origin, across tenants)
    binding = _WalledScraper("acme").board_fetcher.egress_binding()
    other_binding = _WalledScraper("other-board").board_fetcher.egress_binding()
    assert binding["egress_group"] == other_binding["egress_group"] == "walled"
    assert binding["egress_on"] == other_binding["egress_on"] == frozenset({403, 405})


def test_the_board_rides_along_for_attribution_only():
    """`egress_board` lets the shard report name which Boards spent the IP supply. It must not
    change the grouping: two Boards of one ATS still share a budget and a wall."""
    assert (
        _WalledScraper("acme").board_fetcher.egress_binding()["egress_board"]
        == "walled:acme"
    )
    assert (
        _WalledScraper("other").board_fetcher.egress_binding()["egress_board"]
        == "walled:other"
    )


def test_fetch_threads_egress_kwargs_into_http_fetch(monkeypatch):
    """`_fetch` is `_get`'s counterpart for a caller that needs a non-GET method, custom
    headers/timeout, or the raw ``Response`` — its request must carry the Board fetcher's egress
    binding exactly as `_get`'s does (ADR-0204)."""
    captured = {}

    def fake_fetch(method, url, **kwargs):
        captured["method"] = method
        captured["url"] = url
        captured["kwargs"] = kwargs
        return "response"

    monkeypatch.setattr(http, "fetch", fake_fetch)
    result = _WalledScraper("acme")._fetch("POST", "https://example.invalid", timeout=5)
    assert result == "response"
    assert captured["method"] == "POST"
    assert captured["url"] == "https://example.invalid"
    assert captured["kwargs"]["timeout"] == 5
    assert captured["kwargs"]["egress_group"] == "walled"
    assert captured["kwargs"]["egress_on"] == frozenset({403, 405})
    assert captured["kwargs"]["egress_board"] == "walled:acme"


def test_fetch_marks_wall_false_drops_only_the_marking(monkeypatch):
    """`marks_wall=False` passes straight through to the Board fetcher's binding: the request still carries
    `egress_group`/`egress_board` (still routed once walled) but `egress_on` is emptied, so this
    call's own failures can never be what walls the ATS."""
    captured = {}
    monkeypatch.setattr(
        http, "fetch", lambda method, url, **kw: captured.update(kw) or "response"
    )
    _WalledScraper("acme")._fetch("GET", "https://example.invalid", marks_wall=False)
    assert captured["egress_group"] == "walled"
    assert captured["egress_on"] == frozenset()
    assert captured["egress_board"] == "walled:acme"


def test_fetch_async_threads_egress_kwargs_into_http_fetch_async(monkeypatch):
    """Async counterpart: `_fetch_async` must thread the same egress binding into
    `http.fetch_async`, with `session` and `method` passed through positionally."""
    captured = {}

    async def fake_fetch_async(session, method, url, **kwargs):
        captured["session"] = session
        captured["method"] = method
        captured["url"] = url
        captured["kwargs"] = kwargs
        return "response"

    monkeypatch.setattr(http, "fetch_async", fake_fetch_async)
    sentinel_session = object()
    result = asyncio.run(
        _WalledScraper("acme")._fetch_async(
            sentinel_session, "POST", "https://example.invalid", timeout=5
        )
    )
    assert result == "response"
    assert captured["session"] is sentinel_session
    assert captured["method"] == "POST"
    assert captured["url"] == "https://example.invalid"
    assert captured["kwargs"]["timeout"] == 5
    assert captured["kwargs"]["egress_group"] == "walled"
    assert captured["kwargs"]["egress_on"] == frozenset({403, 405})
    assert captured["kwargs"]["egress_board"] == "walled:acme"


def test_eightfold_opts_in_on_the_wall_statuses():
    from headstart.scrapers.eightfold import EightfoldScraper

    assert EightfoldScraper.egress_fallback_on == frozenset({403, 405, 429})
    assert (
        EightfoldScraper("x.eightfold.ai").board_fetcher.egress_binding()[
            "egress_group"
        ]
        == "eightfold"
    )


def test_measured_429_scrapers_opt_into_spare_egress():
    """Only ATSes with a real production 429 signal opt in. Run `34767229592` (2026-09-13,
    `experiment/pipeline-detail-loss/2026-09-13_run-34767229592/spare-egress-analysis.md`) is
    the sole evidence behind this change and names only Eightfold (5,497 events / 34 Boards) and
    Oracle (1,357 events / 2 pods) — Workday/Workable predate it on their own evidence. SuccessFactors,
    Taleo BE, and Taleo Enterprise were wired the same day on zero production 429s and a
    same-day controlled probe of 1-9 requests each (`docs/pipeline/2026-09-13_429-egress-live-measurement.md`)
    that reproduced none — see docs/code-review/2026-09-15_last-5-prs-retrospective-critique.md."""
    from headstart.scrapers.oracle import OracleScraper
    from headstart.scrapers.workable import WorkableScraper
    from headstart.scrapers.workday import WorkdayScraper

    for scraper in (OracleScraper, WorkdayScraper, WorkableScraper):
        assert 429 in scraper.egress_fallback_on, scraper.__name__


def test_unevidenced_scrapers_do_not_opt_into_429_egress_yet():
    from headstart.scrapers.successfactors import SuccessFactorsScraper
    from headstart.scrapers.taleo_be import TaleoBEScraper
    from headstart.scrapers.taleo_enterprise import TaleoEnterpriseScraper

    for scraper in (SuccessFactorsScraper, TaleoBEScraper, TaleoEnterpriseScraper):
        assert 429 not in scraper.egress_fallback_on, scraper.__name__


def _zoho_board(monkeypatch):
    """A zoho board with exactly one description-less record, so fetch_raw runs a detail pass."""
    from headstart.scrapers.registry import get_scraper

    page = (
        '<input type="hidden" value="'
        + "[{&quot;id&quot;:&quot;1&quot;,&quot;Posting_Title&quot;:&quot;Backend Engineer&quot;}]"
        + '" id="jobs">'
    )
    s = get_scraper("zoho", "acme.zohorecruit.com")
    monkeypatch.setattr(type(s), "_get", lambda self, url=None: page)
    return s


def test_zoho_detail_pass_takes_the_async_path_by_default(monkeypatch):
    """ADR-0016: async is the default for *every* detail-fetch scraper.

    Asserted at the seam — which fan-out the scraper actually enters — because
    `async_fanout_enabled()` is a staticmethod reading only the env: it returns the same answer
    for a scraper that never consults it, so asserting on it alone passes over the very
    regression this pins (zoho and ripplehire called `fan_out` unconditionally).
    """
    s = _zoho_board(monkeypatch)
    took = []
    monkeypatch.setattr(
        type(s),
        "fan_out_async",
        lambda self, items, fn, **k: took.append("async") or ["<p>x</p>"],
    )
    monkeypatch.setattr(
        type(s),
        "fan_out",
        lambda self, items, fn, **k: took.append("sync") or ["<p>x</p>"],
    )
    monkeypatch.delenv("HEADSTART_ASYNC_FANOUT", raising=False)

    s.fetch_raw()

    assert took == ["async"]


def test_zoho_detail_pass_falls_back_to_sync_when_the_kill_switch_is_off(monkeypatch):
    """HEADSTART_ASYNC_FANOUT=0 is the one incident-response switch for async traffic to an ATS."""
    s = _zoho_board(monkeypatch)
    took = []
    monkeypatch.setattr(
        type(s),
        "fan_out_async",
        lambda self, items, fn, **k: took.append("async") or ["<p>x</p>"],
    )
    monkeypatch.setattr(
        type(s),
        "fan_out",
        lambda self, items, fn, **k: took.append("sync") or ["<p>x</p>"],
    )
    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", "0")

    s.fetch_raw()

    assert took == ["sync"]


@pytest.mark.parametrize(
    "ats,slug", [("zoho", "acme.zohorecruit.com"), ("ripplehire", "acme")]
)
def test_detail_scrapers_declare_their_async_stream_width(ats, slug):
    """fan_out_async resolves its width from `detail_workers`; undeclared, it opens 100 streams
    against one tenant host (ADR-0047)."""
    from headstart.scrapers.registry import get_scraper

    s = get_scraper(ats, slug)
    assert s.has_detail_pass is True
    assert s.detail_workers is not None


def test_no_scraper_declares_its_own_user_agent():
    """One User-Agent, declared once in base.

    Matched on the *declaration*, not on the shared literal. The first version of this test
    grepped for base's exact string, so it caught a harmless identical copy and waved through
    the case its own docstring named — a scraper declaring a **different** UA, which is how a
    set of strings silently disagrees.
    """
    import ast
    import pathlib

    scrapers = pathlib.Path(__file__).resolve().parents[1] / "src/headstart/scrapers"
    assert scrapers.is_dir(), f"scraper package not found at {scrapers}"
    files = [p for p in sorted(scrapers.glob("*.py")) if p.name != "base.py"]
    assert len(files) > 15, f"only found {len(files)} scrapers — the glob is wrong"

    names = {"UA", "_UA", "USER_AGENT", "_USER_AGENT", "AGENT", "_AGENT"}
    offenders = []
    for path in files:
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in names:
                    offenders.append(f"{path.name}:{target.id}")
    assert offenders == [], (
        f"declared their own User-Agent instead of importing it: {offenders}"
    )


def test_every_scraper_declares_a_compilable_url_shape():
    """Every registered scraper states :attr:`~BaseScraper.url_shape` (ADR-0157) — the
    declaration ``scripts/eval/verify_filters.py``'s ``URL_SHAPES`` is generated from, so a
    scraper missing one would make that generation silently drop it rather than error. Checked
    once, here, for all of them, rather than per-ATS."""
    import re

    from headstart.scrapers.registry import SCRAPERS

    for ats, cls in SCRAPERS.items():
        shape = getattr(cls, "url_shape", None)
        assert shape, f"{ats}: no url_shape declared"
        re.compile(shape)  # raises re.error on a malformed pattern


def test_job_id_composes_board_key_and_native_id_for_every_scraper():
    """:meth:`~BaseScraper.job_id` is the one formula every scraper's ``Job.id`` uses — even the
    four whose :meth:`~BaseScraper.board_key` itself departs from the bare ``{ats}:{slug}``
    (workday, personio, taleo_be, taleo_enterprise). Pinned against a literal expected id per
    ATS, not re-derived from the scraper's own ``board_key()`` — comparing ``job_id()`` to
    ``f"{scraper.board_key()}:42"`` would be true by construction for any ``board_key()``
    output and could never catch a regression in either method (ADR-0157)."""
    from headstart.scrapers.registry import get_scraper

    for ats, slug, expected in (
        ("greenhouse", "acme", "greenhouse:acme:42"),
        (
            "workday",
            "https://acme.wd1.myworkdayjobs.com/External",
            "workday:acme/External:42",
        ),
        ("personio", "acme.jobs.personio.de", "personio:acme:42"),
        (
            "taleo_be",
            "https://acme.tbe.taleo.net/acme/ats/careers/v2/searchResults",
            "taleo_be:https://acme.tbe.taleo.net/acme/ats/careers/v2/searchResults:42",
        ),
        (
            "taleo_enterprise",
            "https://acme.taleo.net/careersection/ext/jobsearch.ftl",
            "taleo_enterprise:https://acme.taleo.net/careersection/ext:42",
        ),
    ):
        assert get_scraper(ats, slug).job_id("42") == expected


# --- the ADR-0166 pre-detail tech gate ------------------------------------------------


def test_tech_detail_wanted_is_off_outside_the_pipeline_and_armed_inside_it():
    """``have_details`` is the one signal that says "the pipeline is running".

    Eight scripts construct scrapers directly and three read a Board's completeness as a health
    metric, so a gate that fired for them would have those three report a collapse that is not
    real. ``tech_gate_enabled`` is the separate kill switch, and is on by default because two
    call sites were already gating in production before this seam existed."""
    from headstart.scrapers.registry import get_scraper

    items = [{"t": "Backend Engineer"}, {"t": "Housekeeper"}]
    scraper = get_scraper("smartrecruiters", "acme")

    assert scraper.have_details is None
    assert scraper.tech_detail_wanted(items, lambda i: i["t"]) == items, (
        "direct caller keeps the Board"
    )

    scraper.have_details = frozenset()
    assert scraper.tech_detail_wanted(items, lambda i: i["t"]) == [items[0]]
    assert scraper.telemetry["tech_gated_details"] == 1


def test_tech_detail_wanted_reads_department_so_a_vague_title_is_not_dropped():
    """`tech_filter` rule 4 promotes a vague title on a technical department, and the gate has
    to honour it or it silently drops those postings.

    This is the whole reason oracle, zoho, icims, bamboohr and jobvite cannot take this gate:
    their department arrives on the *detail*, so a gate cannot see it. Measured over the real
    2026-09-17 corpus, a department-blind gate drops 46.0% of oracle's tech postings and 47.4%
    of zoho's — docs/pipeline/2026-09-17_pre-detail-tech-gate-measurement.md."""
    from headstart.scrapers.registry import get_scraper

    scraper = get_scraper("smartrecruiters", "acme")
    scraper.have_details = frozenset()
    vague = [{"t": "System Technician", "d": "Information Technology"}]

    assert (
        scraper.tech_detail_wanted(vague, lambda i: i["t"], lambda i: i["d"]) == vague
    )
    assert scraper.tech_detail_wanted(vague, lambda i: i["t"]) == [], (
        "without the department the same posting is dropped — the recall cliff"
    )


def test_tech_detail_wanted_kill_switch_restores_the_whole_board(monkeypatch):
    from headstart.scrapers.registry import get_scraper

    scraper = get_scraper("smartrecruiters", "acme")
    scraper.have_details = frozenset()
    items = [{"t": "Housekeeper"}]

    assert scraper.tech_detail_wanted(items, lambda i: i["t"]) == []
    monkeypatch.setenv("HEADSTART_TECH_GATE", "0")
    assert scraper.tech_detail_wanted(items, lambda i: i["t"]) == items


def test_attach_details_pairs_against_the_fetched_subset_not_the_full_list():
    """ADR-0048's alignment trap, at the seam rather than at nine call sites.

    ``zip(items, results)`` is the bug: both are lists of the right shape, so a subset fan-out
    pairs every result with the wrong item and nothing raises. Items that were never fetched must
    come back with an empty detail, not a neighbour's."""
    from headstart.scrapers.base import BaseScraper

    items = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    fetched = [items[1]]  # only "b" passed the gate
    BaseScraper.attach_details(items, fetched, [{"description": "b body"}])

    assert items[1]["_detail"] == {"description": "b body"}
    assert items[0]["_detail"] == {} and items[2]["_detail"] == {}


# --- the ADR-0201 Detail pass -----------------------------------------------------------


class _DetailStub(_StubScraper):
    """A Scraper whose Detail pass reads `/detail/{id}` as JSON and loses a page with no body."""

    def detail_request(self, row):
        if not row.get("id"):
            raise DetailLost("no job id")
        return DetailRequest(f"https://example.invalid/detail/{row['id']}")

    def read_detail(self, row, response):
        body = json.loads(response.text)
        if "body" not in body:
            raise DetailLost("no body on a 200")
        return body["body"]


def _detail_route(method, url, kwargs):
    native_id = url.rsplit("/", 1)[1]
    return {
        "ok": FakeResponse(text='{"body": "text of ok"}'),
        "gone": FakeResponse(404, "{}"),
        "empty": FakeResponse(text="{}"),
        "garbled": FakeResponse(text="<html>not json"),
        "refused": http.RequestsError("connection refused"),
    }[native_id]


@pytest.mark.parametrize("async_fanout", ["1", "0"])
def test_run_detail_pass_labels_every_loss_on_either_transport(
    monkeypatch, async_fanout
):
    """One request description, two transports, one set of outcomes: the drift between
    hand-written sync and async twins (a header sent on one path only) cannot recur."""
    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", async_fanout)
    fetcher = FakeFetcher(_detail_route)
    scraper = _DetailStub("x", fetcher=fetcher)
    rows = [{"id": i} for i in ("ok", "gone", "empty", "garbled", "refused")] + [{}]

    details = scraper.run_detail_pass(
        rows, key_of=lambda row: row.get("id"), what="pages"
    )

    assert dict(details) == {"ok": "text of ok"}
    assert details.missing == 5
    assert scraper.detail_losses == {
        "HTTP 404": 1,
        "no body on a 200": 1,
        "JSONDecodeError": 1,
        "RequestException": 1,
        "no job id": 1,
    }
    assert scraper.telemetry["detail_attempted"] == 5
    assert all(
        request.kwargs["headers"] == dict(DEFAULT_REQUEST_HEADERS)
        and request.kwargs["timeout"] == 30
        for request in fetcher.requests
    )


class _Clock:
    """A monotonic clock the detail routes advance, so the stall window is measured in fetches."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def _stalling_route(clock, step):
    def route(method, url, kwargs):
        clock.now += step
        return _detail_route(method, url, kwargs)

    return route


@pytest.mark.parametrize("async_fanout", ["1", "0"])
def test_run_detail_pass_breaks_off_when_no_detail_succeeds_for_the_stall_window(
    monkeypatch, async_fanout
):
    """Run 36003741124: `oracle:egud`'s detail pass ran 56 min after its listing and the shard's
    budget killed it. Once nothing has succeeded for the window, the rest is skipped, labelled."""
    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", async_fanout)
    clock = _Clock()
    monkeypatch.setattr(base, "_detail_clock", clock)
    scraper = _DetailStub("x", fetcher=FakeFetcher(_stalling_route(clock, 400.0)))
    scraper.detail_workers = 1
    rows = [{"id": "refused"} for _ in range(4)]

    details = scraper.run_detail_pass(
        rows, key_of=lambda row: row.get("id"), what="pages", concurrency=1
    )

    assert details.missing == 4
    assert scraper.detail_losses == {
        "RequestException": 2,
        base.DETAIL_STALLED: 2,
    }
    assert scraper.telemetry["detail_stalled"] == 2
    assert scraper.telemetry["detail_attempted"] == 2  # skipped items formed no request


@pytest.mark.parametrize("async_fanout", ["1", "0"])
def test_run_detail_pass_keeps_going_while_details_still_succeed(
    monkeypatch, async_fanout
):
    """A slow pass that is still landing details is not a stall: `oracle:ejwl` legitimately
    spends ~26 min, and a wall-clock cap would cut it where this does not."""
    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", async_fanout)
    clock = _Clock()
    monkeypatch.setattr(base, "_detail_clock", clock)
    scraper = _DetailStub("x", fetcher=FakeFetcher(_stalling_route(clock, 400.0)))
    scraper.detail_workers = 1
    rows = [{"id": i} for i in ("refused", "ok", "refused", "refused")]

    scraper.run_detail_pass(
        rows, key_of=lambda row: row.get("id"), what="pages", concurrency=1
    )

    assert base.DETAIL_STALLED not in scraper.detail_losses
    assert scraper.telemetry["detail_stalled"] == 0


def test_run_detail_pass_bounds_a_detail_that_never_returns(monkeypatch):
    """Every request carries a timeout, but a multiplexed stream can still hang past it; one
    stuck item must not hold the whole pass, and so the whole shard, open."""
    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", "1")
    monkeypatch.setattr(base, "_DETAIL_ITEM_TIMEOUT_S", 0.05)
    scraper = _DetailStub("x", fetcher=FakeFetcher(_detail_route))

    async def never(session, item):
        await asyncio.sleep(3600)

    monkeypatch.setattr(scraper, "_fetch_detail_outcome_async", never)

    details = scraper.run_detail_pass(
        [{"id": "ok"}], key_of=lambda row: row.get("id"), what="pages"
    )

    assert details.missing == 1
    assert scraper.detail_losses == {base.DETAIL_TIMED_OUT: 1}


def test_run_detail_pass_gates_on_the_listing_and_skips_held_details(monkeypatch):
    monkeypatch.delenv("HEADSTART_TECH_GATE", raising=False)
    fetcher = FakeFetcher(
        lambda method, url, kwargs: FakeResponse(text='{"body": "b"}')
    )
    scraper = _DetailStub("x", fetcher=fetcher)
    scraper.have_details = frozenset({"stub:x:held"})
    rows = [
        {"id": "held", "title": "Backend Engineer"},
        {"id": "new", "title": "Backend Engineer"},
        {"id": "chef", "title": "Head Chef"},
    ]

    details = scraper.run_detail_pass(
        rows,
        key_of=lambda row: row["id"],
        what="pages",
        title_of=lambda row: row["title"],
        skip_held=True,
    )

    assert fetcher.urls() == ["https://example.invalid/detail/new"]
    assert dict(details) == {"new": "b"} and details.missing == 0
    assert scraper.telemetry["tech_gated_details"] == 1


def test_run_detail_pass_records_the_thread_path_width(monkeypatch):
    """`fan_out` alone records nothing; a Board on the thread path must still say at what width
    its details ran (ADR-0167 was decided from that line)."""
    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", "0")
    fanout_stats.reset()
    scraper = _DetailStub(
        "x",
        fetcher=FakeFetcher(
            lambda method, url, kwargs: FakeResponse(text='{"body": "b"}')
        ),
    )
    scraper.detail_workers = 3

    scraper.run_detail_pass(
        [{"id": "a"}, {"id": "b"}], key_of=lambda row: row["id"], what="detail pages"
    )

    assert fanout_stats.stats()[("stub details", 3)]["items"] == 2
    fanout_stats.reset()


def test_run_detail_pass_pins_the_multiplexed_width_when_asked(monkeypatch):
    seen = _spy_concurrency(monkeypatch)
    monkeypatch.setenv("HEADSTART_H2_STREAMS", "64")
    scraper = _DetailStub("x", fetcher=FakeFetcher(_detail_route))

    scraper.run_detail_pass(
        [{"id": "ok"}], key_of=lambda row: row["id"], what="detail pages", concurrency=4
    )

    assert seen["concurrency"] == 4


def test_run_detail_pass_over_nothing_records_no_batch_on_either_transport(monkeypatch):
    """A Board the tech gate empties must not add a zero batch: `fanout_stats.report` promises
    to stay silent when nothing fanned out."""
    fanout_stats.reset()
    for async_fanout in ("1", "0"):
        monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", async_fanout)
        scraper = _DetailStub("x", fetcher=FakeFetcher(_detail_route))
        details = scraper.run_detail_pass(
            [], key_of=lambda row: row["id"], what="detail pages"
        )
        assert dict(details) == {} and details.missing == 0
    assert fanout_stats.stats() == {}


class _Landed:
    """A settled response that ended its redirects on ``url``."""

    def __init__(self, url):
        self.url = url

    def close(self):
        pass


def test_default_alias_key_is_the_lowercased_landing_host(monkeypatch):
    seen = {}

    def fetch(method, url, **kwargs):
        seen.update(kwargs, url=url)
        return _Landed("https://Careers.Example.com/jobs?x=1")

    monkeypatch.setattr(http, "fetch", fetch)
    scraper = _StubScraper("acme")
    assert scraper.alias_key() == "careers.example.com"
    assert seen["url"] == scraper.url()
    # It names its Board in the retry log and neither routes nor walls (ADR-0203).
    assert seen["egress_board"] == scraper.board_key()
    assert "egress_group" not in seen and "egress_on" not in seen


def test_alias_key_of_landing_is_the_one_step_a_scraper_overrides(monkeypatch):
    """The fetch stays the base's; only the key read off the landing URL changes."""

    class _WholeUrlKeyed(_StubScraper):
        @staticmethod
        def alias_key_of_landing(landing_url):
            return landing_url.upper()

    monkeypatch.setattr(
        http, "fetch", lambda *args, **kwargs: _Landed("https://x.example/a")
    )
    assert _WholeUrlKeyed("acme").alias_key() == "HTTPS://X.EXAMPLE/A"


def test_alias_key_is_none_when_the_landing_url_cannot_be_read(monkeypatch):
    class _Refusing(_StubScraper):
        @staticmethod
        def alias_key_of_landing(landing_url):
            raise ValueError(f"not this ATS: {landing_url}")

    monkeypatch.setattr(
        http, "fetch", lambda *args, **kwargs: _Landed("https://elsewhere.example/")
    )
    assert _Refusing("acme").alias_key() is None


class _BodylessDetailStub(_StubScraper):
    """A Scraper whose detail pages may state a location but no description."""

    def detail_request(self, row):
        return DetailRequest(f"https://example.invalid/detail/{row['id']}")

    def read_detail(self, row, response):
        detail = json.loads(response.text)
        if not detail.get("description"):
            return DetailWithoutDescription(detail, "no description on the page")
        return detail


@pytest.mark.parametrize("async_fanout", ["1", "0"])
def test_a_detail_without_description_is_kept_but_counted_as_a_gap(
    monkeypatch, async_fanout
):
    """Its other fields are real, so the mapping keeps them; the pass exists for the
    description, so the gap line and `.missing` count it, under its own label."""
    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", async_fanout)
    pages = {
        "full": '{"description": "body", "location": "Pune"}',
        "bodyless": '{"description": null, "location": "Anchorage"}',
    }
    fetcher = FakeFetcher(
        lambda method, url, kwargs: FakeResponse(text=pages[url.rsplit("/", 1)[1]])
    )
    scraper = _BodylessDetailStub("x", fetcher=fetcher)

    details = scraper.run_detail_pass(
        [{"id": "full"}, {"id": "bodyless"}], key_of=lambda row: row["id"], what="pages"
    )

    assert details["bodyless"] == {"description": None, "location": "Anchorage"}
    assert details["full"]["description"] == "body"
    assert details.missing == 1
    assert scraper.detail_losses == {"no description on the page": 1}
    assert scraper.fetch_detail({"id": "bodyless"}) == details["bodyless"]
    assert scraper.detail_losses == {"no description on the page": 2}, (
        "the sampler's per-item path labels the loss too"
    )


class _NamingScraper(_StubScraper):
    """Two postings: one stating a company per posting, one falling back to ``self.company``."""

    stated: str | None = None
    board_title: str | None = None

    def fetch_raw(self):
        return [{"id": "1", "company": self.stated}, {"id": "2"}]

    def resolve_company(self):
        if self.board_title:
            self.company = self.board_title

    def parse(self, raw, scraped_at):
        return [
            Job(
                id=self.job_id(row["id"]),
                ats=self.ats,
                company=row.get("company") or self.company,
                title="Engineer",
                location=None,
                remote=None,
                department=None,
                url=self.job_url(row["id"]),
                posted_at=None,
                scraped_at=scraped_at,
            )
            for row in raw
        ]


def _companies(scraper):
    return [job.company for job in scraper.fetch()]


def test_a_board_no_source_names_is_served_under_its_humanised_tenant(monkeypatch):
    """ADR-0212: the slug is never served. It was, on 232,533 of 520,566 rows of table v121."""
    monkeypatch.setattr(company_name, "curated_names", dict)
    assert _companies(_NamingScraper("careers-gd-ais.icims.com")) == ["GD AIS"] * 2
    # the ledger's name is the slug's spelling, and just as much an identifier
    assert _companies(_NamingScraper("acme-corp", "acme-corp")) == ["Acme Corp"] * 2
    # a tenant that is only a code is served as no company rather than as the code
    assert _companies(_NamingScraper("37053934")) == ["", ""]


def test_a_name_stated_during_the_fetch_is_served_as_stated(monkeypatch):
    monkeypatch.setattr(company_name, "curated_names", dict)
    titled = _NamingScraper("acme")
    titled.board_title = "Acme Robotics"
    assert _companies(titled) == ["Acme Robotics"] * 2
    # a posting's own field stays as the company typed it, even where it equals the slug
    fielded = _NamingScraper("sunday")
    fielded.stated = "sunday"
    assert _companies(fielded) == ["sunday", "Sunday"]
    # ...bar an all-caps legal name, which is title-cased from any source
    legal = _NamingScraper("impronics")
    legal.stated = "IMPRONICS DIGITECH PRIVATE LIMITED"
    assert _companies(legal) == ["Impronics Digitech Private Limited", "Impronics"]


def test_a_padded_posting_company_falls_back_to_the_boards_name(monkeypatch):
    """rippling's `agora` states "   ": truthy, so ``field or self.company`` served it, empty."""
    monkeypatch.setattr(company_name, "curated_names", dict)
    padded = _NamingScraper("agora")
    padded.stated = "   "
    assert _companies(padded) == ["Agora"] * 2


def test_a_curated_name_overrides_every_source_and_skips_the_title_fetch(monkeypatch):
    monkeypatch.setattr(company_name, "curated_names", lambda: {"stub:gmv": "GMV"})
    scraper = _NamingScraper("gmv")
    scraper.stated = "GMV Innovating Solutions S.L."
    scraper.board_title = "Career site"
    assert _companies(scraper) == ["GMV"] * 2
    unfetched = _NamingScraper("gmv")
    unfetched.resolve_company = lambda: pytest.fail("a curated Board fetched its title")
    assert _companies(unfetched) == ["GMV"] * 2


# --- ADR-0228: a batch transport for an origin that admits one warmed tab ---------------


class _BatchStub(_DetailStub):
    """A Scraper whose details go through `fetch_detail_batch`, two ids at a time."""

    detail_batch_size = 2

    def fetch_detail_batch(self, requests):
        ids = [request.url.rsplit("/", 1)[1] for request in requests]
        self.batches.append(ids)
        return self.answer(ids)


def _batch_scraper(answer):
    scraper = _BatchStub("x")
    scraper.batches = []
    scraper.answer = answer
    return scraper


def _answer_from_route(ids):
    return [
        _detail_route("GET", f"https://example.invalid/detail/{i}", {}) for i in ids
    ]


def test_run_detail_pass_sends_a_batch_transports_items_in_order_and_labels_every_loss():
    scraper = _batch_scraper(_answer_from_route)
    rows = [{"id": i} for i in ("ok", "gone", "empty", "refused")]

    details = scraper.run_detail_pass(
        rows, key_of=lambda row: row.get("id"), what="pages"
    )

    assert scraper.batches == [["ok", "gone"], ["empty", "refused"]]
    assert dict(details) == {"ok": "text of ok"}
    assert details.missing == 3
    assert scraper.detail_losses == {
        "HTTP 404": 1,
        "no body on a 200": 1,
        "RequestException": 1,
    }


def test_run_detail_pass_stops_a_batch_transport_at_a_wall_and_keeps_what_landed():
    def answer(ids):
        if ids == ["b1", "b2"]:
            raise base.DetailBatchWalled("the origin answered 403 on every route tried")
        return [FakeResponse(text='{"body": "text"}') for _ in ids]

    scraper = _batch_scraper(answer)
    rows = [{"id": i} for i in ("a1", "a2", "b1", "b2", "c1")]

    details = scraper.run_detail_pass(
        rows, key_of=lambda row: row.get("id"), what="pages"
    )

    assert scraper.batches == [
        ["a1", "a2"],
        ["b1", "b2"],
    ]  # nothing sent after the wall
    assert sorted(details) == ["a1", "a2"]
    assert details.missing == 3
    assert scraper.detail_losses == {base.DETAIL_WALLED: 3}
    assert scraper.telemetry["detail_attempted"] == 2


def test_run_detail_pass_keeps_an_unformed_request_out_of_a_batch():
    scraper = _batch_scraper(
        lambda ids: [FakeResponse(text='{"body": "t"}')] * len(ids)
    )
    rows = [{"id": "a"}, {}, {"id": "b"}]

    details = scraper.run_detail_pass(
        rows, key_of=lambda row: row.get("id"), what="pages"
    )

    assert scraper.batches == [["a"], ["b"]]  # the id-less row formed no request
    assert sorted(details) == ["a", "b"]
    assert scraper.detail_losses == {"no job id": 1}
