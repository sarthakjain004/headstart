"""Regression tests for the careers fingerprinter's raw-host seam."""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "fingerprint_careers", ROOT / "scripts/discover/fingerprint_careers.py"
)
assert SPEC and SPEC.loader
fp = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = fp
SPEC.loader.exec_module(fp)


def test_host_keyed_provider_patterns_keep_the_full_host_and_count_hits():
    found = {
        (ats, tenant): count
        for ats, _kind, tenant, count in fp.scan(
            "https://1-bp.icims.com/jobs/ https://1-bp.icims.com/jobs/ "
            "https://careers.acme.phenompeople.com/us/en/job/1",
            "acme.com",
        )
    }

    assert found[("icims", "1-bp.icims.com")] == 2
    assert found[("phenom", "careers.acme.phenompeople.com")] == 1


def test_per_ats_shape_rules_refuse_unusable_provider_evidence():
    assert (
        fp.normalise_tenant(
            "phenom",
            "hpe.phenompeople.com",
            "careers.hpe.com CNAME hpe.phenompeople.com",
        )
        == "careers.hpe.com"
    )
    assert (
        fp.normalise_tenant(
            "icims",
            "career.page.icims.com",
            "careers.amd.com CNAME career.page.icims.com",
        )
        == "careers.amd.com"
    )
    assert (
        fp.normalise_tenant(
            "teamtailor", "ext.teamtailor.com", "https://ext.teamtailor.com/"
        )
        == ""
    )
    assert (
        fp.normalise_tenant(
            "teamtailor", "acme.teamtailor.com", "https://acme.teamtailor.com/"
        )
        == "acme"
    )
    assert (
        fp.normalise_tenant(
            "greenhouse",
            "edge.greenhouse.io",
            "careers.acme.com CNAME edge.greenhouse.io",
        )
        == ""
    )
    assert (
        fp.normalise_tenant(
            "bamboohr", "anaqua.bamboohr.com", "https://anaqua.bamboohr.com/"
        )
        == "anaqua"
    )
    assert (
        fp.normalise_tenant("breezy", "fathom.breezy.hr", "https://fathom.breezy.hr/")
        == "fathom"
    )
    # Avature's Board is its tenant label, read off a link or off a vanity host's CNAME.
    assert (
        fp.normalise_tenant(
            "avature", "bloomberg", "https://bloomberg.avature.net/careers/SearchJobs"
        )
        == "bloomberg"
    )
    assert (
        fp.normalise_tenant(
            "avature",
            "bmcrecruit.avature.net",
            "jobs.bmc.com CNAME bmcrecruit.avature.net",
        )
        == "bmcrecruit"
    )
    assert (
        fp.normalise_tenant(
            "workday", "acme.wd1.myworkdayjobs.com", "https://acme.com/"
        )
        == ""
    )
    assert (
        fp.normalise_tenant(
            "workday",
            "https://acme.wd1.myworkdayjobs.com/Careers",
            "https://acme.com/",
        )
        == "https://acme.wd1.myworkdayjobs.com/Careers"
    )
    # ClearCompany's Board is HRM Direct; both vendor hosts reduce to the one label.
    assert (
        fp.normalise_tenant(
            "clearcompany",
            "kingarthurbaking.hrmdirect.com",
            "careers.kingarthurbaking.com CNAME kingarthurbaking.hrmdirect.com",
        )
        == "kingarthurbaking"
    )
    assert (
        fp.normalise_tenant(
            "clearcompany",
            "istate.clearcompany.com",
            "https://istate.clearcompany.com/",
        )
        == "istate"
    )
    assert (
        fp.normalise_tenant(
            "clearcompany", "www.hrmdirect.com", "https://www.hrmdirect.com/"
        )
        == ""
    )
    assert (
        fp.normalise_tenant("pyjamahr", "acme", "https://jobs.pyjamahr.com/acme")
        == "acme"
    )
    assert (
        fp.normalise_tenant(
            "pyjamahr", "jobs.pyjamahr.com", "https://jobs.pyjamahr.com/"
        )
        == ""
    )


def test_cname_chain_follows_intermediate_target_and_checks_the_host(monkeypatch):
    class Answer:
        def __init__(self, target):
            self.target = target

    class DNS:
        def resolve(self, host, _kind):
            return {
                "careers.acme.com": [Answer("edge.example.net.")],
                "edge.example.net": [Answer("acme.phenompeople.com.")],
            }.get(host, [])

    monkeypatch.setattr(fp, "_DNS", DNS())

    assert fp.cname_chain("careers.acme.com") == [
        "edge.example.net",
        "acme.phenompeople.com",
    ]
    assert any(
        ats == "bamboohr" and tenant == "anaqua.bamboohr.com"
        for ats, _kind, tenant, _signal, _evidence, _count in fp.cname_hits(
            "anaqua.bamboohr.com", "acme.com"
        )
    )
    assert any(
        ats == "icims" and tenant == "1-bp.icims.com"
        for ats, _kind, tenant, _signal, _evidence, _count in fp.cname_hits(
            "1-bp.icims.com", "icims.com", allow_provider_host=True
        )
    )


def test_failed_http_evidence_never_settles_as_none(monkeypatch):
    monkeypatch.setattr(fp, "cname_chain", lambda _host: [])
    monkeypatch.setattr(fp, "get", lambda url, cap=fp.PAGE_CAP: ("", url, "http429"))

    row = fp.probe(
        "Acme",
        "acme.com",
        cname_hosts=("careers.acme.com",),
        generated_career_hosts=False,
    )

    assert row["status"] == "unreachable"
    assert row["pages_ok"] == 0


def test_detection_without_a_candidate_keeps_looking_for_url_evidence(monkeypatch):
    monkeypatch.setattr(
        fp,
        "cname_hits",
        lambda *_args, **_kwargs: [
            (
                "workday",
                "ats",
                "acme.wd1.myworkdayjobs.com",
                "cname",
                "careers.acme.com CNAME acme.wd1.myworkdayjobs.com",
                1,
            )
        ],
    )

    def fake_get(url, cap=fp.PAGE_CAP):
        if url.endswith("/robots.txt"):
            return "https://acme.wd1.myworkdayjobs.com/AcmeCareers", url, ""
        return "", url, "timeout"

    monkeypatch.setattr(fp, "get", fake_get)
    row = fp.probe(
        "Acme",
        "careers.acme.com",
        cname_hosts=("careers.acme.com",),
        generated_career_hosts=False,
    )

    assert row["tenant"] == "https://acme.wd1.myworkdayjobs.com/AcmeCareers"
    assert row["candidate"] == "unverified"
    assert row["signal"] == "robots"


def test_indeed_adapter_preserves_path_scoped_short_links(tmp_path):
    harvest = tmp_path / "indeed.jsonl"
    rows = [
        {
            "_ats": "vanity",
            "_apply_host": "careers.acme.com",
            "employer": {"key": "acme", "name": "Acme"},
            "recruit": {"viewJobUrl": "https://careers.acme.com/jobs/1"},
        },
        {
            "_ats": "vanity",
            "_apply_host": "careers.acme.com",
            "employer": {"key": "acme", "name": "Acme"},
            "recruit": {"viewJobUrl": "https://careers.acme.com/jobs/2"},
        },
        {
            "_ats": "vanity",
            "_apply_host": "careers.acme.com",
            "employer": {"key": "other", "name": "Other"},
            "recruit": {"viewJobUrl": "https://careers.acme.com/jobs/3"},
        },
        {
            "_ats": "greenhouse",
            "_ats_slug": None,
            "_apply_host": "grnh.se",
            "employer": {"key": "one", "name": "One"},
            "recruit": {"viewJobUrl": "https://grnh.se/one"},
        },
        {
            "_ats": "vanity",
            "_apply_host": "grnh.se",
            "employer": {"key": "two", "name": "Two"},
            "recruit": {"viewJobUrl": "https://grnh.se/two"},
        },
        {
            "_ats": "greenhouse",
            "_apply_host": "boards.greenhouse.io",
            "employer": {"key": "skip", "name": "Skip"},
            "recruit": {"viewJobUrl": "https://boards.greenhouse.io/skip"},
        },
    ]
    harvest.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )

    seeds = fp.indeed_seeds(harvest)

    assert [(seed.input_id, seed.jobs) for seed in seeds] == [
        ("careers.acme.com#employer=acme", 2),
        ("careers.acme.com#employer=other", 1),
        ("https://grnh.se/one", 1),
        ("https://grnh.se/two", 1),
    ]
    assert seeds[0].employer_keys == ("acme",)
    assert seeds[0].sample_apply_urls == (
        "https://careers.acme.com/jobs/1",
        "https://careers.acme.com/jobs/2",
    )


def test_short_link_follow_becomes_a_candidate_and_resume_retries_unreachable(
    tmp_path, monkeypatch
):
    def fake_get(url, cap=fp.PAGE_CAP):
        if url == "https://grnh.se/acme":
            return "", "https://boards.greenhouse.io/acme/jobs/1", ""
        return "", url, "timeout"

    monkeypatch.setattr(fp, "get", fake_get)
    monkeypatch.setattr(fp, "cname_chain", lambda _host: [])
    seed = fp.HostSeed(
        "Acme", "grnh.se", "https://grnh.se/acme", "https://grnh.se/acme", "acme", 1
    )
    row = fp.probe_host(seed)
    assert row["tenant"] == "acme"
    assert row["board_key"] == "greenhouse:acme"
    assert row["candidate"] == "unverified"

    out = tmp_path / "results.csv"
    args = type("Args", (), {"out": str(out), "workers": 1})()
    calls = []

    def unreachable(item):
        calls.append(item.input_id)
        return fp._error_row(item, "indeed", RuntimeError())

    fp._run_scan(args, [seed], "indeed", unreachable)
    fp._run_scan(args, [seed], "indeed", unreachable)
    assert calls == [seed.input_id, seed.input_id]

    with out.open(encoding="utf-8", newline="") as fh:
        assert csv.DictReader(fh).fieldnames == fp.FIELDS


def test_resume_ignores_new_job_count_and_verify_uses_bounded_liveness(
    tmp_path, monkeypatch
):
    out = tmp_path / "results.csv"
    args = type("Args", (), {"out": str(out), "workers": 1})()
    seed = fp.HostSeed("Acme", "careers.acme.com", "careers.acme.com", "", "acme", 1)
    calls = []

    def resolved(item):
        calls.append(item.jobs)
        row = dict.fromkeys(fp.FIELDS, "")
        row.update(
            {
                "company": item.company,
                "domain": item.host,
                "input_kind": "indeed",
                "input_id": item.input_id,
                "jobs": item.jobs,
                "channels": fp.CHANNELS,
                "status": "resolved",
                "ats": "greenhouse",
                "tenant": "acme",
                "board_key": "greenhouse:acme",
                "candidate": "unverified",
            }
        )
        return row

    fp._run_scan(args, [seed], "indeed", resolved)
    fp._run_scan(args, [replace(seed, jobs=2)], "indeed", resolved)
    assert calls == [1]
    assert fp._old_rows(out)[seed.input_id]["jobs"] == "2"

    with out.open("a", encoding="utf-8", newline="") as fh:
        duplicate = dict.fromkeys(fp.FIELDS, "")
        duplicate.update(
            {
                "company": "Acme duplicate",
                "domain": seed.host,
                "input_kind": "indeed",
                "input_id": seed.input_id,
                "channels": fp.CHANNELS,
                "status": "resolved",
                "ats": "greenhouse",
                "tenant": "acme",
                "board_key": "greenhouse:acme",
                "candidate": "unverified",
            }
        )
        csv.DictWriter(fh, fieldnames=fp.FIELDS).writerow(duplicate)

    fetched = []

    class Scraper:
        def board_page(self):
            return "https://board.example"

        def url(self):
            return "https://listing.example"

        def fetch_raw(self):
            fetched.append(True)
            return ["listing"]

    monkeypatch.setattr(fp.scrapable_boards, "load", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(fp.registry, "get_scraper", lambda *_args: Scraper())

    def live(tenant, url):
        fetched.append((tenant, url))
        return "live", 5

    monkeypatch.setattr(fp, "liveness_probes", lambda: {"greenhouse": live})
    monkeypatch.setattr(
        fp,
        "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError()),
    )
    verified = tmp_path / "verified.csv"
    verify_args = type(
        "Args", (), {"results": str(out), "out": str(verified), "ats": "", "workers": 1}
    )()
    fp.cmd_verify(verify_args)

    with verified.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 1
    assert rows[0]["company"] == "Acme duplicate"
    assert rows[0]["verification"] == "live"
    assert fetched == [("acme", "https://board.example")]

    with out.open("a", encoding="utf-8", newline="") as fh:
        duplicate["input_id"] = "https://grnh.se/different-shortlink"
        csv.DictWriter(fh, fieldnames=fp.FIELDS).writerow(duplicate)
    fetched.clear()
    fp.cmd_verify(verify_args)
    assert len(fetched) == 1
    with verified.open() as fh:
        assert len(list(csv.DictReader(fh))) == 2


def test_provider_url_roundtrips_and_dns_fast_path(monkeypatch):
    cases = [
        ("https://boards.greenhouse.io/acme/jobs/1", "greenhouse:acme"),
        ("https://jobs.lever.co/acme/uuid", "lever:acme"),
        ("https://jobs.ashbyhq.com/acme/uuid", "ashby:acme"),
        ("https://apply.workable.com/acme/j/1", "workable:acme"),
        ("https://acme.teamtailor.com/jobs/1", "teamtailor:acme"),
        ("https://anaqua.bamboohr.com/careers/1", "bamboohr:anaqua"),
        ("https://fathom.breezy.hr/p/1b0072dc3e0a-manager", "breezy:fathom"),
        (
            "https://kingarthurbaking.hrmdirect.com/employment/job-opening.php?req=3813473",
            "clearcompany:kingarthurbaking",
        ),
        (
            "https://chevron.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX",
            "oracle:chevron.fa.us2.oraclecloud.com",
        ),
        (
            "https://acme.wd1.myworkdayjobs.com/en-US/Careers/job/1",
            "workday:acme/Careers",
        ),
        (
            "https://wd1.myworkdaysite.com/recruiting/acme/Search/job/1",
            "workday:acme/Search",
        ),
    ]
    monkeypatch.setattr(
        fp,
        "get",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("unnecessary HTTP")),
    )
    monkeypatch.setattr(fp, "cname_chain", lambda host: ["acme.phenompeople.com"])
    from urllib.parse import urlsplit

    for url, expected in cases:
        host = urlsplit(url).hostname
        result = fp.probe_host(fp.HostSeed("Acme", host, url, url, "", 1))
        assert result["board_key"] == expected
    result = fp.probe_host(
        fp.HostSeed(
            "Acme",
            "careers.acme.com",
            "careers.acme.com",
            "https://careers.acme.com/job/1",
            "",
            1,
        )
    )
    assert result["board_key"] == "phenom:careers.acme.com"


def test_taleo_url_families_resolve_to_the_supported_board_identities():
    enterprise = (
        "https://hdr.taleo.net/careersection/ex/jobdetail.ftl?job=192135&lang=en"
    )
    business = (
        "https://phf.tbe.taleo.net/phf01/ats/careers/v2/viewRequisition"
        "?org=COVESTIC2&cws=37&rid=13732"
    )

    enterprise_hits = fp.scan(enterprise, "indeed.com", allow_provider_host=True)
    business_hits = fp.scan(business, "indeed.com", allow_provider_host=True)

    assert (
        "taleo_enterprise",
        "ats",
        "https://hdr.taleo.net/careersection/ex",
        1,
    ) in enterprise_hits
    assert (
        "taleo_be",
        "ats",
        (
            "https://phf.tbe.taleo.net/phf01/ats/careers/v2/searchResults"
            "?org=COVESTIC2&cws=37"
        ),
        1,
    ) in business_hits
    assert fp.candidate_identity(
        "taleo_enterprise",
        "https://hdr.taleo.net/careersection/ex",
        "HDR",
    ) == (
        "taleo_enterprise:https://hdr.taleo.net/careersection/ex",
        "unverified",
    )
    host_only = fp.scan(
        "Taleo careers are hosted at hdr.taleo.net",
        "acme.com",
        allow_provider_host=True,
    )
    assert not any(ats in {"taleo_be", "taleo_enterprise"} for ats, *_ in host_only)
    missing_section = (
        "https://manpower.taleo.net/careersection/jobdetail.ftl?lang=en&job=0034737"
    )
    assert fp.taleo_board_from_url(missing_section) is None
    assert not any(
        ats in {"taleo_be", "taleo_enterprise"}
        for ats, *_ in fp.scan(missing_section, "indeed.com", allow_provider_host=True)
    )
    assert fp.candidate_identity(
        "taleo_be",
        "https://phf.tbe.taleo.net/phf01/ats/careers/v2/searchResults"
        "?org=COVESTIC2&cws=37",
        "Covestic",
    ) == (
        (
            "taleo_be:https://phf.tbe.taleo.net/phf01/ats/careers/v2/searchResults"
            "?org=COVESTIC2&cws=37"
        ),
        "unverified",
    )


def test_indeed_adapter_reclassifies_generic_taleo_per_board(tmp_path):
    harvest = tmp_path / "indeed.jsonl"
    urls = [
        (
            "https://phf.tbe.taleo.net/phf01/ats/careers/v2/viewRequisition"
            "?org=COVESTIC2&cws=37&rid=13732"
        ),
        (
            "https://phf.tbe.taleo.net/phf01/ats/careers/v2/viewRequisition"
            "?org=COVESTIC2&cws=37&rid=13561"
        ),
        (
            "https://phf.tbe.taleo.net/phf01/ats/careers/v2/viewRequisition"
            "?org=OTHER&cws=12&rid=1"
        ),
        "https://hdr.taleo.net/careersection/ex/jobdetail.ftl?job=192135",
        "https://hdr.taleo.net/careersection/austin_tx/jobdetail.ftl?job=791512",
    ]
    harvest.write_text(
        "".join(
            json.dumps(
                {
                    "_ats": "taleo",
                    "_ats_slug": url.split("/", 3)[2],
                    "_apply_host": url.split("/", 3)[2],
                    "employer": {"key": "acme", "name": "Acme"},
                    "recruit": {"viewJobUrl": url},
                }
            )
            + "\n"
            for url in urls
        ),
        encoding="utf-8",
    )

    seeds = fp.indeed_seeds(harvest)

    assert {seed.input_id: seed.jobs for seed in seeds} == {
        (
            "taleo_be:https://phf.tbe.taleo.net/phf01/ats/careers/v2/"
            "searchResults?org=COVESTIC2&cws=37"
        ): 2,
        (
            "taleo_be:https://phf.tbe.taleo.net/phf01/ats/careers/v2/"
            "searchResults?org=OTHER&cws=12"
        ): 1,
        "taleo_enterprise:https://hdr.taleo.net/careersection/austin_tx": 1,
        "taleo_enterprise:https://hdr.taleo.net/careersection/ex": 1,
    }


def test_host_keyed_markup_uses_serving_host():
    for ats, token in [
        ("phenom", "cdn.phenompeople.com"),
        ("successfactors", "rmkcdn"),
    ]:
        assert (
            fp.normalise_tenant(ats, token, "https://careers.acme.com/jobs/1")
            == "careers.acme.com"
        )


def test_corporate_provider_links_keep_the_linked_board_host():
    assert (
        fp.normalise_tenant("icims", "careers-acme.icims.com", "https://acme.com/")
        == "careers-acme.icims.com"
    )
    assert (
        fp.normalise_tenant("phenom", "acme.phenompeople.com", "https://acme.com/")
        == "acme.phenompeople.com"
    )


def test_radancy_and_real_workdaysite_shapes():
    assert not fp.scan("https://foo.phenompeople.com.evil.example/", "acme.com")
    for url in [
        "https://wd5.myworkdaysite.com/recruiting/uw/UWHires",
        "https://wd5.myworkdaysite.com/wday/cxs/uw/UWHires/jobs",
    ]:
        assert (
            "workday",
            "ats",
            "https://uw.wd5.myworkdayjobs.com/UWHires",
            1,
        ) in fp.scan(url, "uw.edu")
    assert fp.scan(
        "https://careers-amgen-com.talentbrew.com/assets/app.js", "amgen.com"
    )[0][:2] == ("radancy", "ats")
    assert fp.candidate_identity("radancy", "", "G4S") == ("", "unsupported")


def test_social_page_cannot_supply_an_employers_ats(monkeypatch):
    monkeypatch.setattr(fp, "cname_chain", lambda _: [])
    calls = []

    def get(url, cap=fp.PAGE_CAP):
        calls.append(url)
        if "instagram.com" in url:
            return "facebook.csod.com", url, ""
        return (
            '<a href="https://www.instagram.com/lifeatacme/">Life at Acme</a>',
            url,
            "",
        )

    monkeypatch.setattr(fp, "get", get)
    row = fp.probe("Acme", "acme.com", generated_career_hosts=False)
    assert row["ats"] != "cornerstone"
    assert not any("instagram.com" in url for url in calls)


def test_dns_failure_is_preserved_for_retry(monkeypatch):
    class DNS:
        def resolve(self, *_args):
            raise TimeoutError()

    monkeypatch.setattr(fp, "_DNS", DNS())
    monkeypatch.setattr(
        fp, "get", lambda url, cap=fp.PAGE_CAP: ("<html>clean</html>", url, "")
    )
    row = fp.probe(
        "Acme", "acme.com", cname_hosts=("acme.com",), generated_career_hosts=False
    )
    assert row["pages_err"] > 0
    assert "dns-failures" in row["note"]


def test_deep_psl_and_certificate_company_boundaries():
    assert fp.reg_domain("jobs.example.co.uk") == "example.co.uk"
    assert fp.reg_domain("jobs.school.k12.ca.us") == "school.k12.ca.us"
    assert fp.reg_domain("one.github.io") != fp.reg_domain("two.github.io")
    assert fp.certificate_career_hosts(
        "www.acme.co.uk",
        ["careers.acme.co.uk", "careers.rival.co.uk", "jobs.other.com"],
    ) == ["careers.acme.co.uk"]


def test_api_requires_provider_schema_and_preserves_host():
    from fingerprint_deep import api_signatures

    calls = []

    def post(url, headers, body):
        calls.append(url)
        if url.endswith("/widgets"):
            return {"refineSearch": {"totalHits": 3, "data": {"jobs": []}}}, ""
        raise AssertionError("should stop on Phenom")

    hits, _ = api_signatures("careers.acme.com", "", None, post)
    assert hits == [("phenom", "careers.acme.com", "https://careers.acme.com/widgets")]
    hits, _ = api_signatures(
        "careers.acme.com",
        "",
        lambda url: ('{"count": 9}', url, ""),
        lambda *a: ({"count": 9}, ""),
    )
    assert hits == []


def test_deep_api_and_browser_integration_are_explicit(monkeypatch):
    monkeypatch.setattr(fp, "cname_chain", lambda _: [])
    monkeypatch.setattr(fp, "get", lambda url, cap=fp.PAGE_CAP: ("", url, "http404"))
    monkeypatch.setattr(fp, "certificate_names", lambda _: (["careers.rival.com"], ""))
    monkeypatch.setattr(
        fp,
        "api_signatures",
        lambda *a: (
            [("zwayam", "careers.acme.com", "https://public.zwayam.com/jobs/search")],
            [],
        ),
    )
    row = fp.probe("Acme", "careers.acme.com", generated_career_hosts=False, deep=True)
    assert row["board_key"] == "zwayam:careers.acme.com"
    assert row["signal"] == "api"
    assert row["certificate_hosts"] == "careers.rival.com"
    assert row["channels"] != fp.CHANNELS
    monkeypatch.setattr(fp, "api_signatures", lambda *a: ([], []))
    monkeypatch.setattr(
        fp,
        "browser_page",
        lambda u: ('<a href="https://jobs.lever.co/acme">Jobs</a>', u, [], ""),
    )
    row = fp.probe("Acme", "careers.acme.com", generated_career_hosts=False, deep=True)
    assert row["board_key"] == "lever:acme"
    assert row["signal"] == "browser"


def test_name_only_slug_match_is_not_affiliation(monkeypatch):
    monkeypatch.setattr(fp, "cname_chain", lambda _: [])
    monkeypatch.setattr(
        fp, "get", lambda url, cap=fp.PAGE_CAP: ('{"jobs":[{"id":1}]}', url, "")
    )
    monkeypatch.setattr(fp, "slug_confirms", lambda *a: (True, "name=Google"))
    row = fp.probe("Google", "careers.google.com", generated_career_hosts=False)
    assert row["signal"] == "slugprobe"
    assert row["candidate"] == "inferred-needs-affiliation"


def test_deep_uses_successful_apply_destination_not_a_404(monkeypatch):
    monkeypatch.setattr(fp, "cname_chain", lambda _: [])
    target = "https://careers.acme.com/app/search"

    def get(url, cap=fp.PAGE_CAP):
        if url == "https://wrapper.example/job/1":
            return "<html>SPA</html>", target, ""
        return "<html>custom 404</html>", url, "http404"

    monkeypatch.setattr(fp, "get", get)
    monkeypatch.setattr(fp, "certificate_names", lambda _: ([], ""))
    selected = []

    def api(host, *_args):
        selected.append(host)
        return [], []

    def browser(url):
        selected.append(url)
        return "https://jobs.lever.co/acme", url, [], ""

    monkeypatch.setattr(fp, "api_signatures", api)
    monkeypatch.setattr(fp, "browser_page", browser)
    row = fp.probe_host(
        fp.HostSeed(
            "Acme",
            "wrapper.example",
            "wrapper.example",
            "https://wrapper.example/job/1",
            "acme",
            1,
        ),
        deep=True,
    )
    assert selected == ["careers.acme.com", target]
    assert row["board_key"] == "lever:acme"


def test_zwayam_api_rejects_null_and_generic_count_objects():
    from fingerprint_deep import api_signatures

    for data in [None, {"totalCount": 0}]:
        hits, _ = api_signatures(
            "acme.com",
            "",
            lambda u: ("{}", u, ""),
            lambda *a, payload=data: ({"code": 200, "data": payload}, ""),
        )
        assert not hits
    hits, _ = api_signatures(
        "careers.acme.com",
        "",
        lambda u: ("{}", u, ""),
        lambda *a: ({"code": 200, "data": {"totalCount": 0, "data": []}}, ""),
    )
    assert hits[0][:2] == ("zwayam", "careers.acme.com")


def test_frozen_mixed_employer_wrapper_is_never_a_mapping(monkeypatch):
    monkeypatch.setattr(fp, "cname_chain", lambda _: [])
    monkeypatch.setattr(
        fp, "get", lambda url, cap=fp.PAGE_CAP: ("", "https://jobs.lever.co/acme/1", "")
    )
    row = fp.probe_host(
        fp.HostSeed(
            "Other",
            "wrapper.example",
            "wrapper.example",
            "https://wrapper.example/1",
            "other",
            5,
            employer_keys=("other", "acme"),
        )
    )
    assert row["ats"] == "lever"
    assert row["candidate"] == "ambiguous-employers"


def test_job_matching_is_exact_and_tenant_scoped():
    from fingerprint_job_evidence import match_listing

    urls = {
        "https://careers.acme.com/jobview/role-123",
        "https://careers.acme.com/#!/job-view/role-123",
        "https://careers.rival.com/jobview/role-123",
        "https://careers.acme.com/jobview/role-1234",
    }
    payload = {"data": {"data": [{"_source": {"jobUrl": "role-123"}}]}}
    assert match_listing("zwayam", "careers.acme.com", payload, urls) == [
        "https://careers.acme.com/#!/job-view/role-123",
        "https://careers.acme.com/jobview/role-123",
    ]
    urls = {
        "https://acme.wd1.myworkdayjobs.com/en-US/Careers/job/City/Engineer_R1",
        "https://acme.wd1.myworkdayjobs.com/Other/job/City/Engineer_R1",
        "https://wd5.myworkdaysite.com/recruiting/acme/Careers/job/City/Engineer_R1",
        "https://wd5.myworkdaysite.com/recruiting/rival/Careers/job/City/Engineer_R1",
        "https://wd5.myworkdaysite.com/recruiting/acme/Other/job/City/Engineer_R1",
    }
    assert match_listing(
        "workday",
        "https://acme.wd1.myworkdayjobs.com/Careers",
        {"jobPostings": [{"externalPath": "/job/City/Engineer_R1"}]},
        urls,
    ) == [
        "https://acme.wd1.myworkdayjobs.com/en-US/Careers/job/City/Engineer_R1",
        "https://wd5.myworkdaysite.com/recruiting/acme/Careers/job/City/Engineer_R1",
    ]
    assert match_listing(
        "greenhouse",
        "acme",
        {"jobs": [{"id": 123}]},
        {
            "https://boards.greenhouse.io/rival/jobs/123",
            "https://boards.greenhouse.io/acme/jobs/123",
        },
    ) == ["https://boards.greenhouse.io/acme/jobs/123"]


def test_job_check_is_bounded_and_absence_is_inconclusive():
    from fingerprint_job_evidence import check_jobs

    calls = []

    def post(*args):
        calls.append(args)
        return {"code": 200, "data": {"data": []}}, ""

    state, matches = check_jobs(
        "zwayam",
        "careers.acme.com",
        {"https://careers.acme.com/jobview/missing"},
        lambda url: ("", url, "http404"),
        post,
        max_pages=999,
    )
    assert len(calls) == 3
    assert (state, matches) == ("no-match-in-bounded-sample", [])


def test_shortlink_job_evidence_follows_the_source_and_reports_the_source():
    from fingerprint_job_evidence import check_jobs

    source = "https://grnh.se/acme123"
    final = "https://boards.greenhouse.io/acme/jobs/123"
    calls = []

    def get(url):
        calls.append(url)
        if url == source:
            return "", final, ""
        assert url == "https://boards-api.greenhouse.io/v1/boards/acme/jobs"
        return '{"jobs":[{"id":123}]}', url, ""

    assert check_jobs("greenhouse", "acme", {source}, get, None) == (
        "matched-job",
        [source],
    )
    assert calls == ["https://boards-api.greenhouse.io/v1/boards/acme/jobs", source]


def test_verification_job_evidence_does_not_leak_between_inputs(tmp_path, monkeypatch):
    rows = []
    for i in (1, 2):
        row = dict.fromkeys(fp.FIELDS, "")
        row.update(
            input_id=str(i),
            domain="boards.greenhouse.io",
            candidate="unverified",
            ats="greenhouse",
            tenant="acme",
            board_key="greenhouse:acme",
            sample_apply_urls=f"https://boards.greenhouse.io/acme/jobs/{i}",
        )
        rows.append(row)
    source, output = tmp_path / "source.csv", tmp_path / "verified.csv"
    with source.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fp.FIELDS)
        w.writeheader()
        w.writerows(rows)

    class Scraper:
        def board_page(self):
            return "https://boards.greenhouse.io/acme"

    monkeypatch.setattr(fp.registry, "get_scraper", lambda *a: Scraper())
    monkeypatch.setattr(fp.scrapable_boards, "load", lambda *a, **k: [])
    monkeypatch.setattr(
        fp, "liveness_probes", lambda: {"greenhouse": lambda *a: ("live", 2)}
    )
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        return '{"jobs":[{"id":1},{"id":2}]}', url, ""

    monkeypatch.setattr(fp, "get", get)
    fp.cmd_verify(
        type(
            "Args",
            (),
            {"results": str(source), "out": str(output), "workers": 2, "ats": ""},
        )()
    )
    with output.open() as fh:
        verified = list(csv.DictReader(fh))
    assert len(calls) == 1
    assert all(
        r["matched_apply_urls"] == r["sample_apply_urls"]
        and r["job_evidence"] == "matched-job"
        for r in verified
    )


def test_a_direct_jibe_client_host_is_the_jibe_board_named_by_its_label(monkeypatch):
    """`{client}.jibeapply.com` is a Jibe Board keyed by the bare label, while a vanity CNAME
    target (`careers.rm.com.jibeapply.com`) keeps the zone's existing `icims` routing."""
    monkeypatch.setattr(
        fp,
        "get",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("unnecessary HTTP")),
    )
    monkeypatch.setattr(fp, "cname_chain", lambda _host: [])
    by_url = fp.probe_host(
        fp.HostSeed(
            "Costco",
            "costco.jibeapply.com",
            "costco.jibeapply.com",
            "https://costco.jibeapply.com/jobs/123",
            "",
            1,
        )
    )
    by_dns = fp.probe_host(
        fp.HostSeed("Costco", "costco.jibeapply.com", "costco.jibeapply.com", "", "", 1)
    )
    assert (by_url["board_key"], by_url["tenant"]) == ("jibe:costco", "costco")
    assert (by_dns["board_key"], by_dns["tenant"]) == ("jibe:costco", "costco")

    monkeypatch.setattr(
        fp, "cname_chain", lambda _host: ["careers.rm.com.jibeapply.com"]
    )
    vanity = fp.probe_host(
        fp.HostSeed("RM", "careers.rm.com", "careers.rm.com", "", "", 1)
    )
    assert vanity["ats"] == "icims"

    assert fp.scan("https://careers.rm.com.jibeapply.com/jobs", "rm.com") == []
    assert (
        fp.normalise_tenant(
            "jibe", "costco.jibeapply.com", "https://costco.jibeapply.com/"
        )
        == "costco"
    )
    assert fp.normalise_tenant("jibe", "www.jibeapply.com", "https://x/") == ""


def test_an_adp_career_center_link_yields_its_cid_ccid_board():
    """ADP Workforce Now keys a Board by two query values, in any order and, in HTML, joined by
    `&amp;`. Only a career-center link names one; ADP Recruiting Management
    (`recruiting.adp.com`, `myjobs.adp.com`) is a different platform, `adp_recruiting`."""
    cid = "7d58836c-11dd-4415-9de0-63b918b88652"
    page = (
        '<a href="https://workforcenow.adp.com/mascsr/default/mdf/recruitment/'
        f'recruitment.html?lang=en_US&amp;ccId=19000101_000001&amp;cid={cid}">Jobs</a>'
    )
    assert fp.scan(page, "2lifecommunities.org") == [
        ("adp", "ats", f"{cid}/19000101_000001", 1)
    ]
    assert fp.candidate_identity("adp", f"{cid}/19000101_000001", "2Life")[1] == (
        "unverified"
    )
    assert fp.scan("https://myjobs.adp.com/pathgroup/cx", "pathgroup.com") == [
        ("adp_recruiting", "ats", "pathgroup", 1)
    ]


def test_an_adp_recruiting_link_yields_its_career_site_slug():
    """ADP Recruiting Management keys a Board on the path word of `myjobs.adp.com/{slug}/cx`.
    The API's own `public/` path names no site, and a legacy `recruiting.adp.com` link names a
    client number rather than a site, which detects the ATS with no Board."""
    assert fp.scan(
        '<a href="https://myjobs.adp.com/PathGroup/cx/job-listing">Jobs</a>', "x.com"
    ) == [("adp_recruiting", "ats", "pathgroup", 1)]
    assert fp.scan("https://myjobs.adp.com/public/staffing/v1/career-site/x", "x") == []
    assert fp.scan(
        "https://recruiting.adp.com/srccar/public/RTI.home?c=1110541", "x.com"
    ) == [("adp_recruiting", "ats", "", 1)]
    assert fp.candidate_identity("adp_recruiting", "pathgroup", "PathGroup") == (
        "adp_recruiting:pathgroup",
        "unverified",
    )


def test_a_cornerstone_career_site_link_yields_the_corp_label_board():
    """`cornerstone.py` keys a Board on the `{corp}.csod.com` label, the tenant, not per site."""
    html = (
        '<a href="https://aswatsoneurope.csod.com/ux/ats/careersite/16/home'
        '?c=aswatsoneurope">Jobs</a>'
    )
    assert fp.scan(html, "aswatson.com") == [
        ("cornerstone", "ats", "aswatsoneurope", 1)
    ]
    assert fp.candidate_identity("cornerstone", "aswatsoneurope", "A.S. Watson") == (
        "cornerstone:aswatsoneurope",
        "unverified",
    )


def test_a_peoplestrong_portal_link_yields_the_scrapers_lowercased_label():
    """`peoplestrong.py` keys a Board on the portal's subdomain label, lowercased; the vendor's own
    `www` and `static` hosts on the same zone are not portals."""
    page = (
        '<a href="https://HDFCErgoCareers.peoplestrong.com/job/joblist">Jobs</a>'
        '<a href="https://www.peoplestrong.com/careers">PeopleStrong</a>'
        '<script src="https://static.peoplestrong.com/app.js"></script>'
    )
    assert fp.scan(page, "hdfcergo.com") == [
        ("peoplestrong", "ats", "hdfcergocareers", 1)
    ]
