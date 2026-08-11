"""Tests for the dead-Board relocator (scripts/validate/relocate_dead_boards.py).

The property under test is the one whose absence would delist a live employer: the ledger must only
ever gain a destination it measured, and only ever lose a source that answered 404 itself. Both
halves have been got wrong here — a URL-shape inference that returned ``None`` for five ATSes while
the caller had already buried the source, and a refused destination that discarded the source's own
proven-dead verdict. So the shape inference is tested directly against every URL form the real
ledgers use.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "validate"))


@pytest.fixture(scope="module")
def mod():
    """Import the script by path — `scripts/` is not a package, and `check_liveness` pulls in
    `headstart.http`, so this is skipped wherever that import cannot be satisfied."""
    pytest.importorskip("curl_cffi")
    spec = importlib.util.spec_from_file_location(
        "relocate_dead_boards",
        ROOT / "scripts" / "validate" / "relocate_dead_boards.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Row:
    def __init__(self, tenant, url, checked_at="2026-08-11"):
        self.tenant, self.url, self.checked_at = tenant, url, checked_at


def _rows(*pairs, checked_at="2026-08-11"):
    return {t: _Row(t, u, checked_at) for t, u in pairs}


# every URL form the real ledgers use: the board named in a subdomain, or in a path
@pytest.mark.parametrize(
    "tenant,sample,expected",
    [
        ("1mg", "https://1mg.darwinbox.in", "https://{tenant}.darwinbox.in"),
        ("10times", "10times.freshteam.com", "{tenant}.freshteam.com"),
        ("100", "https://100.keka.com", "https://{tenant}.keka.com"),
        (
            "10xfounders",
            "10xfounders.jobs.personio.com",
            "{tenant}.jobs.personio.com",
        ),
        (
            "7-eleven-gsc",
            "https://7-eleven-gsc.ripplehire.com",
            "https://{tenant}.ripplehire.com",
        ),
        ("01c", "https://jobs.ashbyhq.com/01c", "https://jobs.ashbyhq.com/{tenant}"),
        (
            "10-west-reg",
            "https://ats.rippling.com/10-west-reg",
            "https://ats.rippling.com/{tenant}",
        ),
    ],
)
def test_url_template_handles_subdomain_and_path_boards(mod, tenant, sample, expected):
    """Anchoring the tenant to the URL's tail yielded None for every subdomain-addressed ATS —
    darwinbox, freshteam, keka, personio, ripplehire — and a None destination means a move writes
    no replacement at all."""
    assert mod.url_template("x", _rows((tenant, sample))) == expected


def test_url_template_replaces_the_last_occurrence_not_the_first(mod):
    """A tenant that also appears in the host ('ats' in ats.rippling.com) must not rewrite it."""
    rows = _rows(("ats", "https://ats.rippling.com/ats"))
    assert mod.url_template("rippling", rows) == "https://ats.rippling.com/{tenant}"


def test_url_template_prefers_the_shape_the_current_writer_uses(mod):
    """A ledger accumulates conventions. rippling's seeded rows are scheme-less and outnumber the
    ones check_liveness writes today, so a whole-file mode elects the fossil."""
    rows = {
        **_rows(("old-a", "ats.rippling.com/old-a"), checked_at="2026-07-03"),
        **_rows(("old-b", "ats.rippling.com/old-b"), checked_at="2026-07-03"),
        **_rows(("old-c", "ats.rippling.com/old-c"), checked_at="2026-07-03"),
        **_rows(("new", "https://ats.rippling.com/new"), checked_at="2026-08-11"),
    }
    assert mod.url_template("rippling", rows) == "https://ats.rippling.com/{tenant}"


def test_url_template_is_none_when_no_row_contains_its_tenant(mod):
    assert mod.url_template("x", _rows(("acme", "https://example.com/other"))) is None


def test_read_moves_accepts_comments_and_the_dead_only_sentinel(mod, tmp_path):
    path = tmp_path / "moves.txt"
    path.write_text(
        "# a comment\n"
        "\n"
        "greenhouse  aerospike  rippling  aerospike-inc  # trailing comment\n"
        "greenhouse  attune     -         -\n"
    )
    assert mod.read_moves(path) == [
        ("greenhouse", "aerospike", "rippling", "aerospike-inc"),
        ("greenhouse", "attune", "-", "-"),
    ]


def test_read_moves_rejects_a_malformed_line(mod, tmp_path):
    path = tmp_path / "moves.txt"
    path.write_text("greenhouse  aerospike  rippling\n")
    with pytest.raises(SystemExit):
        mod.read_moves(path)


def test_derives_back_accepts_a_url_the_scraper_reads_back(mod):
    assert mod.derives_back("ashby", "acme", "https://jobs.ashbyhq.com/acme")


def test_derives_back_rejects_a_url_that_reads_back_as_another_slug(mod):
    """personio and zoho derive the slug from the url — their slug is the whole host — so a
    tenant-shaped slug paired with a host-shaped url would scrape a different Board."""
    assert not mod.derives_back("personio", "acme", "acme.jobs.personio.com")
    assert mod.derives_back(
        "personio", "acme.jobs.personio.com", "acme.jobs.personio.com"
    )


def test_derives_back_rejects_an_ats_with_no_scraper(mod):
    assert not mod.derives_back("bamboohr", "acme", "https://acme.bamboohr.com")
