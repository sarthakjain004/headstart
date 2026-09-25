import runpy
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
