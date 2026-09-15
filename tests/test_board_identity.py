"""Tests for the canonical Board-identity module (headstart.board_identity, ADR-0155).

Two directions, both covered: construct (:func:`board_key` strict, :func:`board_identity`
lenient-with-fallback, :func:`board_key_of` strict-from-a-raw-string) and parse
(:func:`board_of`'s guess, including the ADR-0049 colon-ambiguity failure mode it deliberately
does not try to fix). Plus the two small conveniences, :func:`ats_of` and :func:`lower_key`.
"""

from __future__ import annotations

import pytest

from headstart import board_identity
from headstart.board_identity import (
    ats_of,
    board_key,
    board_key_of,
    board_of,
    lower_key,
)
from headstart.config import CompanyRef


def test_board_key_is_the_scrapers_own_answer():
    assert board_key(CompanyRef(ats="greenhouse", slug="stripe", name="Stripe")) == (
        "greenhouse:stripe"
    )


def test_board_key_raises_on_a_slug_its_scraper_cannot_parse():
    """The strict form: a Workday slug that isn't a careers URL has no `{company}/{site}` to
    derive, so `board_key` raises rather than guessing — the form
    `index_plan.live_keep_set` needs, since a fabricated key would never match a real Job-id
    prefix and would be worse than dropping the Board."""
    with pytest.raises(ValueError):
        board_key(CompanyRef(ats="workday", slug="not-a-url", name=""))


def test_board_identity_falls_back_to_the_plain_key_and_never_raises():
    key = board_identity.board_identity(
        CompanyRef(ats="workday", slug="not-a-url", name="")
    )
    assert key == "workday:not-a-url"


def test_identity_failures_are_capped_not_unbounded(monkeypatch, caplog):
    """One line per distinct Board is still `N_Boards` lines for one broken scraper.

    The bound is what keeps a `board_key()` regression from costing a five-figure log again —
    21,122 lines a run, measured 2026-09-09, when `board_cost._rekeyed` was still asking. That
    caller is gone, but the bound is not about it: it stands for the case it was written for, a
    scraper whose `board_key()` starts raising on its own slugs. Pinned against the cap rather
    than a literal so the two cannot drift apart.
    """
    monkeypatch.setattr(board_identity, "_IDENTITY_FAILURES_SEEN", set())
    cap = board_identity._IDENTITY_REPORT_CAP
    with caplog.at_level("INFO", logger="headstart.board_identity"):
        for i in range(cap * 5):
            board_identity.board_identity(
                CompanyRef(ats="workday", slug=f"bad-{i}", name="")
            )

    # Only this module's records: another logger's line would otherwise be counted as one of
    # ours.
    mine = [r for r in caplog.records if r.name == "headstart.board_identity"]
    named = [r for r in mine if "board_key() failed" in r.message]
    assert len(named) == cap, f"expected {cap} named, got {len(named)}"
    assert [r for r in mine if "further board_key() failures" in r.message]
    # Once, not once per Board past the cap — the flood this bound exists to stop.
    assert len(mine) == cap + 1


def test_a_genuinely_malformed_slug_is_still_reported(monkeypatch, caplog):
    """The liveness-ledger population the report was written for, where a raise really does mean
    a slug nothing can parse. It outlived `board_cost._rekeyed` and the `report_failure` opt-out
    that once had to be kept from silencing it, so it is now simply what the reporter does."""
    monkeypatch.setattr(board_identity, "_IDENTITY_FAILURES_SEEN", set())
    with caplog.at_level("INFO", logger="headstart.board_identity"):
        board_identity.board_identity(
            CompanyRef(ats="workday", slug="not-a-url", name="")
        )
    assert [r for r in caplog.records if "board_key() failed" in r.message]


def test_board_key_of_normalises_the_report_key_space():
    """Shard reports key errors `{ats}:{slug}` where a Workday slug is a whole URL; the corpus
    side keys `board_key()`. The two must land in one key space or gone-verdicts and produced-sets
    silently never pair — the same conversion `scrape_join` applies for eviction scope."""
    assert (
        board_key_of("workday:https://x.wd1.myworkdayjobs.com/Careers")
        == "workday:x/Careers"
    )
    assert board_key_of("greenhouse:hibu") == "greenhouse:hibu"
    assert board_key_of("notanats:whatever") is None
    assert board_key_of(":slug") is None
    assert board_key_of("greenhouse:") is None


def test_board_key_of_never_falls_back_to_a_synthetic_key():
    """Unlike `board_identity`, an unresolvable raw key returns `None` rather than a plain
    `ats:slug` — a synthetic fallback here would silently reintroduce the two-keyspace bug
    ADR-0059/ADR-0096 removed, since nothing a real scrape ever produces carries that spelling."""
    assert board_key_of("workday:not-a-url") is None


def test_board_of_simple():
    assert board_of("greenhouse:stripe:1") == "greenhouse:stripe"


def test_board_of_preserves_colon_in_slug():
    # Workday slugs are full URLs (colons galore); only the trailing native id is stripped
    wd = "workday:https://acme.wd1.myworkdayjobs.com/careers:R123"
    assert board_of(wd) == "workday:https://acme.wd1.myworkdayjobs.com/careers"


def test_board_of_is_a_guess_that_a_colon_bearing_native_id_defeats():
    """ADR-0049's documented, deliberately-unfixed failure mode: a real Workday native id can
    itself carry a colon (`REQ: 228`), so splitting on the *last* colon attributes the row to a
    Board that does not exist. This is not a bug to silently patch here — ADR-0049 fixed the two
    real call sites by matching a Job id against a live keep-set by *prefix*
    (`index_plan.resolve_board`) rather than by parsing, and kept `board_of` exactly this
    imprecise for the self-comparing callers that have no keep-set to match against."""
    real_board = "workday:dmainc/DMA"
    job_id = f"{real_board}:REQ: 228"
    phantom = board_of(job_id)
    assert phantom == "workday:dmainc/DMA:REQ"
    assert phantom != real_board  # the guess names a Board that does not exist


def test_ats_of_reads_the_first_segment_of_any_key_shape():
    # a board key, a Job id, and a scrape-list `{ats}:{slug}` key all share one first segment
    assert ats_of("greenhouse:stripe") == "greenhouse"
    assert ats_of("greenhouse:stripe:123") == "greenhouse"
    assert ats_of("workday:https://acme.wd1.myworkdayjobs.com/careers") == "workday"


def test_ats_of_coerces_non_str_input():
    """Several callers split a key read back out of JSON, where it is typed `Any`."""
    assert ats_of(123) == "123"  # no colon at all: the whole (coerced) value


def test_lower_key_is_plain_lower_not_casefold():
    assert lower_key("GreenHouse:Stripe") == "greenhouse:stripe"
    # str.casefold() would map this differently; lower_key must not silently switch algorithms
    assert lower_key("İ") == "İ".lower()
