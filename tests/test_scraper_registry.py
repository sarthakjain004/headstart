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


#: One mixed-case link per ATS that declares `keeps_slug_case`, and the slug it names.
_MIXED_CASE_LINKS = {
    # `api.lever.co/v0/postings/CesiumAstro` lists 309, `cesiumastro` is "Document not found"
    "lever": ("https://jobs.lever.co/CesiumAstro/5f1c3a8e", "CesiumAstro"),
    # the ledger holds this Board as `01Systems`, and a lower-cased slug lands as a second row
    "smartrecruiters": ("https://careers.smartrecruiters.com/01Systems", "01Systems"),
}


@pytest.fixture(scope="module")
def fingerprinters():
    """What each careers-page fingerprinter reads out of a page, as a set of (ats, slug)."""
    resolve = _load_script(
        "keeps_slug_case_fingerprint", "scripts/resolve/fingerprint.py"
    )
    careers = _load_script(
        "keeps_slug_case_fingerprint_careers", "scripts/discover/fingerprint_careers.py"
    )
    return {
        "resolve/fingerprint.py": resolve.detect,
        "discover/fingerprint_careers.py": lambda page: {
            (ats, slug) for ats, _kind, slug, _n in careers.scan(page, "example.org")
        },
    }


@pytest.mark.parametrize(
    "ats", sorted(ats for ats, cls in SCRAPERS.items() if cls.keeps_slug_case)
)
def test_both_fingerprinters_keep_a_declared_atss_slug_in_its_case(fingerprinters, ats):
    """The two fingerprinters used to hold one hand-kept set each, and #813 updated one of them:
    the other kept verifying live mixed-case Lever Boards as dead (#824, #827, ADR-0271)."""
    assert ats in _MIXED_CASE_LINKS, (
        f"add a mixed-case {ats} link that names a real Board"
    )
    link, slug = _MIXED_CASE_LINKS[ats]
    for name, read in fingerprinters.items():
        assert (ats, slug) in read(f'<a href="{link}">Jobs</a>'), name


def test_both_fingerprinters_lower_case_an_undeclared_atss_slug(fingerprinters):
    """Ashby leaves `keeps_slug_case` False: `Elveo` and `elveo` list the same 21 postings."""
    for name, read in fingerprinters.items():
        found = read('<a href="https://jobs.ashbyhq.com/Elveo">Jobs</a>')
        assert ("ashby", "elveo") in found and ("ashby", "Elveo") not in found, name


def test_only_atses_measured_to_lose_a_board_when_lower_cased_keep_slug_case():
    """Add an ATS here only with a live measurement showing a lower-cased slug loses its Board, and
    record it in ADR-0271. Being case-sensitive is not enough: PyjamaHR is, but its slugs are all
    lower-case, so lower-casing a captured one is what finds the Board."""
    assert {ats for ats, cls in SCRAPERS.items() if cls.keeps_slug_case} == {
        "lever",
        "smartrecruiters",
    }
