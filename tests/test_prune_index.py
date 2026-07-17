"""Prune planning (ADR-0023): off-Board eviction + case-variant dedup, and board_key mapping."""

from __future__ import annotations

from headstart.index_prune import plan_prune
from headstart.scrapers.greenhouse import GreenhouseScraper
from headstart.scrapers.workday import WorkdayScraper


def test_off_board_evicted_survivors_kept():
    keep = {"greenhouse:live"}
    off, dup = plan_prune(["greenhouse:live:1", "greenhouse:dead:2"], keep)
    assert off == ["greenhouse:dead:2"]
    assert dup == []


def test_dedup_keeps_lexmin_casing():
    # one job under two Board casings (both canonicalise into the live keep Board)
    keep = {"workday:co/site"}
    off, dup = plan_prune(["workday:co/Site:R1", "workday:co/site:R1"], keep)
    assert off == []
    assert dup == ["workday:co/site:R1"]  # 'co/Site' (S<s) is kept, 'co/site' dropped


def test_distinct_native_ids_are_not_duplicates():
    keep = {"workday:co/site"}
    _, dup = plan_prune(["workday:co/Site:R1", "workday:co/site:R2"], keep)
    assert dup == []


def test_three_way_casing_keeps_one():
    keep = {"workday:co/site"}
    ids = ["workday:co/SITE:R1", "workday:co/Site:R1", "workday:co/site:R1"]
    off, dup = plan_prune(ids, keep)
    assert off == []
    assert sorted(dup) == ["workday:co/Site:R1", "workday:co/site:R1"]  # keep 'co/SITE'


def test_board_key_default_is_ats_colon_slug():
    assert GreenhouseScraper("stripe").board_key() == "greenhouse:stripe"


def test_board_key_workday_is_company_slash_site():
    s = WorkdayScraper("https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite")
    assert s.board_key() == "workday:nvidia/NVIDIAExternalCareerSite"
