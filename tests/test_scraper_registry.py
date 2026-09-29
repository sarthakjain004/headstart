import importlib.util
import runpy
import sys
from pathlib import Path

import pytest

from headstart.boards.company_ref import CompanyRef
from headstart.scrapers.registry import DISABLED_ATS, SCRAPERS, company_from_row


def test_every_enabled_scraper_has_a_live_filter_harness_url_shape():
    path = Path(__file__).resolve().parents[1] / "scripts/eval/verify_filters.py"
    shapes = runpy.run_path(str(path))["URL_SHAPES"]
    assert set(SCRAPERS) - set(DISABLED_ATS) <= set(shapes)


def test_company_from_row_reads_the_slug_through_the_scraper():
    """ADR-0203: the Scraper decides which column its slug comes from, never the caller."""
    company = company_from_row(
        "workday", "acme", "https://acme.wd3.myworkdayjobs.com/External/"
    )
    assert company == CompanyRef(
        ats="workday", slug="https://acme.wd3.myworkdayjobs.com/External", name="acme"
    )
    assert (
        company_from_row(
            "personio", "acme", "https://acme.jobs.personio.com/job/1?language=de"
        ).slug
        == "acme.jobs.personio.com"
    )
    assert company_from_row(
        "oracle", "akamai", "https://fa-extu-saasfaprod1.fa.ocs.oraclecloud.com"
    ) == CompanyRef(
        ats="oracle", slug="fa-extu-saasfaprod1.fa.ocs.oraclecloud.com", name="akamai"
    )


def test_company_from_row_keeps_the_tenant_where_the_scraper_keys_on_it():
    assert company_from_row(
        "greenhouse", "stripe", "https://boards.greenhouse.io/stripe"
    ) == CompanyRef(ats="greenhouse", slug="stripe", name="stripe")


def test_company_from_row_refuses_an_ats_with_no_scraper():
    with pytest.raises(KeyError):
        company_from_row("no-such-ats", "acme", "")


def _load_script(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).resolve().parents[1] / relative
    )
    module = importlib.util.module_from_spec(spec)
    # A slots dataclass looks its own module up while it is built.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_both_fingerprinters_keep_the_slug_case_their_scrapers_declare():
    """The two fingerprinters used to hold one hand-kept set each, and #813 updated one of them:
    the other kept verifying live mixed-case Lever Boards as dead (#824, #827, ADR-0271)."""
    declared = {ats for ats, cls in SCRAPERS.items() if cls.keeps_slug_case}
    for relative in (
        "scripts/resolve/fingerprint.py",
        "scripts/discover/fingerprint_careers.py",
    ):
        fingerprinter = _load_script(f"keeps_slug_case_{Path(relative).stem}", relative)
        detected = set(fingerprinter.PATTERNS)
        assert fingerprinter.KEEPS_SLUG_CASE & detected == declared & detected, relative


def test_only_atses_measured_to_lose_a_board_when_lower_cased_keep_slug_case():
    """Add an ATS here only with a live measurement showing a lower-cased slug loses its Board, and
    record it in ADR-0271. Being case-sensitive is not enough: PyjamaHR is, but its slugs are all
    lower-case, so lower-casing a captured one is what finds the Board."""
    assert {ats for ats, cls in SCRAPERS.items() if cls.keeps_slug_case} == {
        "lever",
        "smartrecruiters",
    }
