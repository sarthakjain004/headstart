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
        # workday: company/site, because that is what the scraper's slug must resolve to
        (
            "https://acme.wd1.myworkdayjobs.com/en-US/Careers",
            "myworkdayjobs.com",
            "workday",
            "acme/Careers",
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
        "https://acme.wd1.myworkdayjobs.com/External_Careers",
        "myworkdayjobs.com",
        "workday",
    )
    assert url == "https://acme.wd1.myworkdayjobs.com/External_Careers"
    assert slug.startswith("acme/")


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


def test_every_table_host_yields_the_slug_its_own_scraper_expects():
    """Guards the bug class this table keeps hitting: a host whose style emits a slug the ATS's
    scraper cannot use. The probe is built from the host, and the *expected slug* from what the
    scraper wants, so flipping a style in the table fails here rather than in a silent harvest.
    """
    expected = {
        "path": lambda host: "acme",  # the first path segment
        "sub": lambda host: (
            "acme"
        ),  # the label; the scraper recovers the host from the URL
        "host": lambda host: (
            f"acme.{host}"
        ),  # the scraper is handed the whole board host
        "workday": lambda host: (
            "acme/External_Careers"
        ),  # company/site, per board_key()
    }
    for ats, hosts in wf.ATS_HOSTS.items():
        for host, style in hosts:
            probe = {
                "path": f"https://{host}/acme/jobs/1",
                "sub": f"https://acme.{host}/jobs",
                "host": f"https://acme.{host}/careers",
                "workday": f"https://acme.wd1.{host}/en-US/External_Careers/job/1",
            }[style]
            got = wf.extract(probe, host, style)
            assert got, f"{ats}: {host} ({style}) reads nothing"
            assert got[0] == expected[style](host), (
                f"{ats}: {host} ({style}) emitted {got[0]}"
            )


def test_a_workday_slug_keeps_the_site_its_scraper_parses():
    """`WorkdayScraper.slug_from` keeps the whole careers URL and `_URL_PATTERN` demands a site
    segment, so a bare host would be a slug the scraper rejects outright."""
    slug, url = wf.extract(
        "https://2020companies.wd1.myworkdayjobs.com/en-US/External_Careers/job/x",
        "myworkdayjobs.com",
        "workday",
    )
    assert slug == "2020companies/External_Careers"
    assert url == "https://2020companies.wd1.myworkdayjobs.com/External_Careers"
    # no locale in the path is the commoner shape
    assert (
        wf.extract(
            "https://acme.wd1.myworkdayjobs.com/External_Careers",
            "myworkdayjobs.com",
            "workday",
        )[1]
        == "https://acme.wd1.myworkdayjobs.com/External_Careers"
    )
    # a bare host names no board
    assert (
        wf.extract(
            "https://acme.wd1.myworkdayjobs.com/", "myworkdayjobs.com", "workday"
        )
        is None
    )


@pytest.mark.parametrize(
    "url, expected",
    [
        # the CXS jobs endpoint is the one `/wday/` route that names a board
        (
            "https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/External_Careers/jobs",
            "acme/External_Careers",
        ),
        # a two-letter site is a site, not a locale — `ac`, `au`, `hc` are real boards
        ("https://acme.wd1.myworkdayjobs.com/ac", "acme/ac"),
        # a locale only counts as one when a site follows it
        ("https://acme.wd1.myworkdayjobs.com/en-US/Careers/job/1", "acme/Careers"),
        # sites Workday allows that hostname rules would reject
        ("https://acme.wd1.myworkdayjobs.com/1", "acme/1"),
        ("https://acme.wd1.myworkdayjobs.com/_penn-careers", "acme/_penn-careers"),
    ],
)
def test_workday_reads_the_site_shapes_that_really_occur(url, expected):
    assert wf.extract(url, "myworkdayjobs.com", "workday")[0] == expected


@pytest.mark.parametrize(
    "url",
    [
        # implementation/preview tenants: `WorkdayScraper._URL_PATTERN` wants `wd\\d+` and raises
        # on these, so harvesting them would hand the scraper a slug it rejects
        "https://acme.impl-wd10.myworkdayjobs.com/External_Careers",
        # `/wday/` machinery that is not the jobs endpoint
        "https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/videoLabels",
        "https://acme.wd1.myworkdayjobs.com/wday/asset/x",
        # routes under a board host that are not boards
        "https://acme.wd1.myworkdayjobs.com/assets/x.png",
        "https://acme.wd1.myworkdayjobs.com/refreshFacet",
        "https://acme.wd1.myworkdayjobs.com/.well-known/y",
        # a bare host names no board at all
        "https://acme.wd1.myworkdayjobs.com/",
    ],
)
def test_workday_rejects_what_its_scraper_would_raise_on(url):
    assert wf.extract(url, "myworkdayjobs.com", "workday") is None


def test_regional_pods_are_two_boards_but_aliased_hosts_are_one():
    """Zoho's TLDs are separate pods — 62 labels exist on two of them, 14 with both live — so
    keying on the label would silently drop the second. Greenhouse's hosts are aliases of one
    board, so there the label is exactly the right identity."""
    assert wf.dedupe_key(
        "auray", "https://auray.zohorecruit.eu", "sub"
    ) != wf.dedupe_key("auray", "https://auray.zohorecruit.com", "sub")
    assert wf.dedupe_key(
        "acme", "https://boards.greenhouse.io/acme", "path"
    ) == wf.dedupe_key("acme", "https://job-boards.greenhouse.io/acme", "path")


def test_the_sink_keeps_both_pods_of_a_split_ats(tmp_path, monkeypatch):
    monkeypatch.setattr(wf, "WB", tmp_path)
    with wf.slug_sink("zoho") as sink:
        assert sink.add(("auray", "https://auray.zohorecruit.com"), "sub")
        assert sink.add(("auray", "https://auray.zohorecruit.eu"), "sub")
        assert not sink.add(("auray", "https://auray.zohorecruit.eu"), "sub")
    rows = (tmp_path / "zoho.csv").read_text().strip().split("\n")
    assert len(rows) == 3  # header + one row per pod


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
        assert sink.add(
            ("AcmeGmbH", "https://careers.smartrecruiters.com/AcmeGmbH"), "path"
        )
        assert not sink.add(
            ("acmegmbh", "https://careers.smartrecruiters.com/acmegmbh"), "path"
        )
        sink.flush()

    written = (tmp_path / "smartrecruiters.csv").read_text()
    assert "AcmeGmbH" in written
    assert written.count("\n") == 2  # header + the one slug

    with wf.slug_sink("smartrecruiters") as sink:  # a later run reloads what is on disk
        assert not sink.add(
            ("ACMEGMBH", "https://careers.smartrecruiters.com/ACMEGMBH"), "path"
        )

    assert (tmp_path / "smartrecruiters.csv").read_text() == written


@pytest.mark.parametrize(
    "ats, row, style",
    [
        # the one that broke: a workday row's host sits *under* the table host, so matching the
        # table by equality mis-keyed it on reload and every resumed sweep re-wrote the row
        (
            "workday",
            (
                "acme/External_Careers",
                "https://acme.wd1.myworkdayjobs.com/External_Careers",
            ),
            "workday",
        ),
        ("zoho", ("acme", "https://acme.zohorecruit.eu"), "sub"),
        ("eightfold", ("acme.eightfold.ai", "https://acme.eightfold.ai"), "host"),
        ("greenhouse", ("acme", "https://boards.greenhouse.io/acme"), "path"),
        # workable's CSV holds both of its styles at once
        ("workable", ("acme", "https://apply.workable.com/acme"), "path"),
        ("workable", ("beta", "https://beta.workable.com"), "sub"),
    ],
)
def test_a_resumed_sweep_does_not_rewrite_rows_it_already_has(
    tmp_path, monkeypatch, ats, row, style
):
    """`slug_sink` must preload the *same* identity `add` computes, or resuming duplicates."""
    monkeypatch.setattr(wf, "WB", tmp_path)
    for _ in range(3):
        with wf.slug_sink(ats) as sink:
            sink.add(row, style)
    body = (tmp_path / f"{ats}.csv").read_text().strip().split("\n")
    assert len(body) == 2, f"{ats}: {len(body) - 1} rows after three identical sweeps"


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
