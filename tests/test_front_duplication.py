"""Tests for resolving a Career front's apply URL to its Backing Board
(headstart.scrapers.front_duplication). The apply URLs are real ones, read off Radancy job pages
on 2026-09-26 and Happydance job pages on 2026-09-28."""

from __future__ import annotations

import pytest

from headstart.scrapers.front_duplication import ScrapableBoardIndex, backing_board

_HELD = ScrapableBoardIndex(
    frozenset(
        {
            "workday:takeda/external",
            "workday:cat/caterpillarcareers",
            "workday:baird/careers",
            "icims:experienced-arm.icims.com",
            "taleo_enterprise:https://uhg.taleo.net/careersection/10000",
            "smartrecruiters:mattelinc",
            "successfactors:jobs.netapp.com",
            "workday:stemcell/external_careers",
            "radancy:jobs.sanofi.com",
            "happydance:careers.box.com",
            "avature:synopsys",
            "greenhouse:acme",
        }
    )
)


@pytest.mark.parametrize(
    ("apply_url", "board"),
    [
        (
            "https://takeda.wd502.myworkdayjobs.com/External/job/CZE---Most/LKA-KA--pro-plazma-centrum-CHOMUTOV---FLEXIBILN-VAZEK_R0181856/apply",
            "workday:takeda/external",
        ),
        (
            "https://stemcell.wd3.myworkdayjobs.com/en-US/External_Careers/job/Canada---Ontario-Remote-Home-Office/Account-Manager--Immunology_R0007253/apply",
            "workday:stemcell/external_careers",
        ),
        (
            "https://cat.wd5.myworkdayjobs.com/en-US/CaterpillarCareers/job/Chennai-Tamil-Nadu-India/Engineer_R0000378543/apply",
            "workday:cat/caterpillarcareers",
        ),
        (
            "https://wd1.myworkdaysite.com/en-US/recruiting/baird/Careers/job/WI-Milwaukee/Internship---Business-Coordinator--Year-Round-_R20261000-1",
            "workday:baird/careers",
        ),
        ("https://wd5.myworkdaysite.com/en-US/recruiting/prismahealth", None),
        (
            "https://experienced-arm.icims.com/jobs/18903/senior-infrastructure-automation-tools-engineer/job/login",
            "icims:experienced-arm.icims.com",
        ),
        (
            "https://uhg.taleo.net/careersection/10000/jobapply.ftl?job=2382212",
            "taleo_enterprise:https://uhg.taleo.net/careersection/10000",
        ),
        (
            "https://cognizant.taleo.net/careersection/Lateral/jobapply.ftl?job=00070210791&lang=en",
            None,  # not held
        ),
        (
            "https://jobs.smartrecruiters.com/MattelInc/744000150773689-american-girl-restaurant-dish-washer-seasonal-part-time-?oga=true",
            "smartrecruiters:mattelinc",
        ),
        (
            "https://jobs.netapp.com/job/San-Jose-Director%2C-Software-Engineer-CA-95128/1422658200/?feedId=386800&tcsource=apply",
            "successfactors:jobs.netapp.com",
        ),
        # SuccessFactors' own apply form names a company id, not the RMK host: unresolved.
        (
            "https://career2.successfactors.eu/sfcareer/jobreqcareer?jobId=331288&company=cargill&locale=en_US",
            None,
        ),
        (
            "https://intuit.avature.net/externalCareers/JobApplication?pipelineId=23933",
            None,
        ),
        (
            "https://synopsys.avature.net/careers/Login?jobId=18266&source=&tags=&user=&formValues",
            "avature:synopsys",
        ),
        (
            "https://amgen.wd1.myworkdayjobs.com/Careers/job/US---California---Thousand-Oaks/Associate-Manufacturing_R-256887/apply",
            None,  # not held
        ),
        # A front is never a Backing Board, even when its Apply button names its own host.
        (
            "https://jobs.sanofi.com/sys/apply/job/application/2649/44048303680?languageCode=en",
            None,
        ),
        ("https://careers.box.com/en/jobs/apply/?id=7515890", None),
        ("/en/jobs/apply/?id=7515890", None),  # relative: no host to resolve
        (
            "https://boards.greenhouse.io/embed/job_app?for=acme&token=1",
            "greenhouse:acme",
        ),
        (None, None),
    ],
)
def test_backing_board_resolves_held_boards_only(
    apply_url: str | None, board: str | None
) -> None:
    assert backing_board(apply_url, _HELD) == board
