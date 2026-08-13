"""Tests for the Wayback feeder's shared half (scripts/discover/wayback_feeder.py).

It is a script under `scripts/discover`, so we put that directory on the path and import it by
name, the way `test_datadome_transcript.py` does for `scripts/scrape`.

These check the rules that decide what counts as a Company's slug. Each one exists because the
harvest got it wrong at some point: the slug's case, the datacenter in a Workday host, dots and
underscores in a path slug, files served from a board root, Greenhouse's widget route, and the
two ATSes whose slug is the whole host rather than the label.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "discover"))

import wayback_feeder as wf  # noqa: E402


@pytest.mark.parametrize(
    "url, host, style, expected",
    [
        # sub: the label is the slug, the URL is the host as seen
        ("https://acme.zohorecruit.eu/j", "zohorecruit.eu", "sub", "acme"),
        ("https://foo.jobs.personio.de/job/1", "jobs.personio.de", "sub", "foo"),
        ("https://tenant.hire.trakstar.com/", "hire.trakstar.com", "sub", "tenant"),
        # path: the first segment is the slug
        ("https://apply.workable.com/acme/j/1", "apply.workable.com", "path", "acme"),
        ("https://ats.rippling.com/acme", "ats.rippling.com", "path", "acme"),
        # workday: the label is the slug but the datacenter must survive in the URL
        (
            "https://acme.wd1.myworkdayjobs.com/en-US/Careers",
            "myworkdayjobs.com",
            "workday",
            "acme",
        ),
    ],
)
def test_extract_reads_the_slug_for_each_style(url, host, style, expected):
    assert wf.extract(url, host, style)[0] == expected


@pytest.mark.parametrize(
    "url, host, style",
    [
        ("https://www.zohorecruit.com/x", "zohorecruit.com", "sub"),  # infra label
        ("https://a.b.freshteam.com/j", "freshteam.com", "sub"),  # deeper subdomain
        ("ftp://acme.keka.com/x", "keka.com", "sub"),  # not http(s)
        ("https://boards.greenhouse.io", "boards.greenhouse.io", "path"),  # no segment
        (
            "https://other.example.com/acme",
            "boards.greenhouse.io",
            "path",
        ),  # wrong host
        ("https://apply.workable.com/x", "workable.com", "sub"),  # `apply` is infra
    ],
)
def test_extract_rejects_what_is_not_a_slug(url, host, style):
    assert wf.extract(url, host, style) is None


def test_a_workday_url_keeps_its_datacenter():
    """`{slug}.{host}` cannot rebuild `acme.wd1.myworkdayjobs.com`, so the URL seen is kept."""
    slug, url = wf.extract(
        "https://acme.wd1.myworkdayjobs.com/x", "myworkdayjobs.com", "workday"
    )
    assert (slug, url) == ("acme", "https://acme.wd1.myworkdayjobs.com")


def test_a_path_slug_keeps_the_case_the_ats_gave_it():
    """8,737 of SmartRecruiters' 12,706 ledger slugs are mixed-case; lowercasing breaks them."""
    slug, url = wf.extract(
        "https://careers.smartrecruiters.com/RedBullGmbH",
        "careers.smartrecruiters.com",
        "path",
    )
    assert slug == "RedBullGmbH"
    assert url.endswith("/RedBullGmbH")


def test_a_host_style_slug_is_the_whole_host():
    """Eightfold and SuccessFactors are handed the host as their slug (`https://{slug}/careers`),
    so the label alone would name a board their scraper cannot fetch."""
    assert wf.extract(
        "https://10xgenomics.eightfold.ai/careers", "eightfold.ai", "host"
    ) == (
        "10xgenomics.eightfold.ai",
        "https://10xgenomics.eightfold.ai",
    )
    assert wf.extract("https://133723.jobs2web.com/", "jobs2web.com", "host") == (
        "133723.jobs2web.com",
        "https://133723.jobs2web.com",
    )


@pytest.mark.parametrize(
    "slug", ["adept.ai", "abstraction.games", "edged_infrastructure"]
)
def test_a_path_slug_may_hold_a_dot_or_underscore(slug):
    """Ashby and Lever allow domain-shaped slugs, Greenhouse and Rippling underscored ones."""
    assert (
        wf.extract(f"https://jobs.ashbyhq.com/{slug}", "jobs.ashbyhq.com", "path")[0]
        == slug
    )


@pytest.mark.parametrize("label", ["a.b", "a_b"])
def test_a_subdomain_label_may_not(label):
    """Neither character is legal in a hostname label, so `sub` stays strict."""
    assert (
        wf.extract(f"https://{label}.freshteam.com/x", "freshteam.com", "sub") is None
    )


@pytest.mark.parametrize(
    "name",
    [
        "ads.txt",
        "app-ads.txt",
        "humans.txt",
        "index.html",
        "manifest.json",
        "main.abc123.js",
    ],
)
def test_a_file_served_from_the_board_root_is_not_a_slug(name):
    """Allowing dots made these shape-identical to a slug, so they go by extension."""
    assert (
        wf.extract(f"https://jobs.ashbyhq.com/{name}", "jobs.ashbyhq.com", "path")
        is None
    )


def test_the_greenhouse_widget_route_yields_the_company_it_embeds():
    """`/embed/job_board?for=X` names X as surely as a board URL does."""
    assert wf.extract(
        "https://boards.greenhouse.io/embed/job_board?for=3playmedia&error=true",
        "boards.greenhouse.io",
        "path",
    ) == ("3playmedia", "https://boards.greenhouse.io/3playmedia")


@pytest.mark.parametrize(
    "url",
    [
        "https://boards.greenhouse.io/embed/job_board",  # no `for=` names nobody
        "https://boards.greenhouse.io/embed/job_board?for=",
        "https://boards.greenhouse.io/embed/job_board?for=ads.txt",
    ],
)
def test_a_widget_route_naming_nobody_is_dropped(url):
    assert wf.extract(url, "boards.greenhouse.io", "path") is None


def test_non_ascii_is_not_a_slug():
    """CDX serves raw UTF-8 paths; `str.isalnum()` alone would admit them."""
    assert not wf.valid("Café")
    assert not wf.valid("日本")
    assert wf.valid("Acme-Corp")


def test_every_table_host_is_reachable_by_its_own_style():
    """A host paired with the wrong style silently harvests nothing, which no run would report."""
    for ats, hosts in wf.ATS_HOSTS.items():
        for host, style in hosts:
            probe = (
                f"https://{host}/acme"
                if style == "path"
                else f"https://acme.wd1.{host}/x"
                if style == "workday"
                else f"https://acme.{host}/x"
            )
            assert wf.extract(probe, host, style), (
                f"{ats}: {host} ({style}) reads nothing"
            )


def test_hosts_for_falls_back_to_the_table_and_can_be_overridden():
    assert wf.hosts_for("workable") == [
        ("apply.workable.com", "path"),
        ("workable.com", "sub"),
    ]
    assert wf.hosts_for("workable", "workable.com") == [("workable.com", "sub")]
    # an ATS with no scraper yet is still sweepable by hand
    assert wf.hosts_for("turbohire", "turbohire.co", "sub") == [("turbohire.co", "sub")]


@pytest.mark.parametrize(
    "args", [("nope", None, None), ("zoho", "nope.com", None), ("zoho", None, "sub")]
)
def test_hosts_for_refuses_what_it_cannot_resolve(args):
    with pytest.raises(SystemExit):
        wf.hosts_for(*args)


def test_the_sink_dedupes_case_insensitively_across_runs(tmp_path, monkeypatch):
    """The pair that makes case preservation safe: slugs are written as the ATS wrote them, but
    matched case-insensitively, so a second run cannot re-add `AcmeGmbH` as `acmegmbh`."""
    monkeypatch.setattr(wf, "WB", tmp_path)

    with wf.slug_sink("smartrecruiters") as sink:
        assert sink.add(("AcmeGmbH", "https://careers.smartrecruiters.com/AcmeGmbH"))
        assert not sink.add(
            ("acmegmbh", "https://careers.smartrecruiters.com/acmegmbh")
        )
        sink.flush()

    written = (tmp_path / "smartrecruiters.csv").read_text()
    assert "AcmeGmbH" in written
    assert written.count("\n") == 2  # header + the one slug

    with wf.slug_sink("smartrecruiters") as sink:  # a later run reloads what is on disk
        assert not sink.add(
            ("ACMEGMBH", "https://careers.smartrecruiters.com/ACMEGMBH")
        )

    assert (tmp_path / "smartrecruiters.csv").read_text() == written


def test_a_single_host_ats_adopts_its_pre_per_host_cursor(
    tmp_path, monkeypatch, capsys
):
    """State files gained the host in their name; for a one-host ATS the old file can only have
    come from that host, so it is carried over rather than silently restarting the sweep."""
    monkeypatch.setattr(wf, "WB", tmp_path)
    (tmp_path / ".ashby_pages_done").write_text("0\n1\n2\n")

    wf.adopt_legacy_state("ashby", "pages_done")

    adopted = tmp_path / ".ashby_jobs.ashbyhq.com_pages_done"
    assert adopted.read_text() == "0\n1\n2\n"
    assert not (tmp_path / ".ashby_pages_done").exists()
    assert "adopted" in capsys.readouterr().out


def test_a_multi_host_ats_is_told_rather_than_guessed_for(
    tmp_path, monkeypatch, capsys
):
    """With several hosts there is no way to tell which the cursor belongs to, and crediting the
    wrong one would skip pages that were never read — so it says so and leaves the file alone."""
    monkeypatch.setattr(wf, "WB", tmp_path)
    (tmp_path / ".zoho_resume").write_text("cursor")

    wf.adopt_legacy_state("zoho", "resume")

    assert (tmp_path / ".zoho_resume").exists()  # untouched
    assert "names no host" in capsys.readouterr().out
