"""Discovery retains the real public Board coordinates of the three new ATSes."""

import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/discover"))
import cc_miner
import wayback_feeder


@pytest.mark.parametrize(
    ("ats", "url", "slug", "board"),
    [
        (
            "pageup",
            "https://careers.pageuppeople.com/1083/cw/en/job/495865",
            "1083/cw/en",
            "https://careers.pageuppeople.com/1083/cw/en/listing/",
        ),
        (
            "pageup",
            "https://careers.pageuppeople.com/mob/1083/cw/en/listing/",
            "1083/cw/en",
            "https://careers.pageuppeople.com/1083/cw/en/listing/",
        ),
        (
            "manatal",
            "https://www.careers-page.com/manatal/job/L8597V4V",
            "manatal",
            "https://www.careers-page.com/manatal",
        ),
        (
            "manatal",
            "https://manatal.careers-page.com/jobs/8be30254-7422-4ae0-9aa2-3ea3c0453c0a",
            "manatal.careers-page.com",
            "https://manatal.careers-page.com/",
        ),
    ],
)
def test_archive_parsers_preserve_public_board_identity(ats, url, slug, board):
    host = "careers.pageuppeople.com" if ats == "pageup" else "careers-page.com"
    assert wayback_feeder.extract(url, host, ats) == (slug, board)
    spec = cc_miner.ATS_PATTERNS[ats]
    found = {}
    cc_miner.extract_tenants(
        spec, [re.compile(p, re.IGNORECASE) for p in spec["patterns"]], [url], found
    )
    assert found == {slug: board}


def test_discovery_rejects_a_pageup_internal_channel():
    assert (
        wayback_feeder.extract(
            "https://careers.pageuppeople.com/1083/ci/en/job/1",
            "careers.pageuppeople.com",
            "pageup",
        )
        is None
    )


@pytest.mark.parametrize(
    "path",
    ["0%2C%200%2C%200", "getjoburl(job)", "catalyst-labs%5D", "llms.txt", "robots.txt"],
)
def test_manatal_archive_captures_do_not_turn_css_or_pasted_text_into_boards(path):
    assert (
        wayback_feeder.extract(
            f"https://www.careers-page.com/{path}", "careers-page.com", "manatal"
        )
        is None
    )


def test_both_fingerprinters_find_the_three_public_shapes():
    page = "https://recruiterflow.com/rfcareers/jobs/166 https://careers.pageuppeople.com/mob/1083/cw/en/job/495865 https://www.careers-page.com/manatal/job/L8597V4V https://manatal.careers-page.com/jobs/8be30254-7422-4ae0-9aa2-3ea3c0453c0a"
    wanted = {
        ("recruiterflow", "rfcareers"),
        ("pageup", "1083/cw/en"),
        ("manatal", "manatal"),
        ("manatal", "manatal.careers-page.com"),
    }
    for rel in [
        "scripts/resolve/fingerprint.py",
        "scripts/discover/fingerprint_careers.py",
    ]:
        spec = importlib.util.spec_from_file_location(
            "new_ats_" + Path(rel).stem, ROOT / rel
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        found = (
            module.detect(page)
            if hasattr(module, "detect")
            else {
                (ats, slug) for ats, _kind, slug, _n in module.scan(page, "example.com")
            }
        )
        assert wanted == found
