"""Tests for the Wayback fold (scripts/merge/merge_wayback_into_tenants.py).

It is a script under `scripts/merge`, so we put that directory on the path and import it by name,
the way `test_wayback_feeder.py` does for `scripts/discover`.

The fold's whole value is that it is *additive*: the pool it writes into holds rows from four
other sources (`cc`, `cc2026`, `harvest`, `fingerprint`) that no other file records, and the
older `merge_tenants.py` destroys them by rebuilding each file from cc ∪ wayback. Measured
2026-08-14, that rebuild would drop 26,824 rows to gain 20,926. So these tests pin the
properties that make this script safe to re-run: nothing is dropped, no URL is overwritten, and
a second run is a no-op.

The Workday case is separate and load-bearing: the harvest keys tenants as `{company}/{site}`
while the pool carries a display slug, so the two only line up on the board URL. Keying on the
tenant there would append a duplicate row for a board the pool already has.
"""

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "merge"))

import merge_wayback_into_tenants as mw  # noqa: E402


def write_csv(path: Path, header, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def read_pool(path: Path):
    """``tenant -> (url, source)``."""
    with path.open(encoding="utf-8") as f:
        return {r["tenant"]: (r["url"], r["source"]) for r in csv.DictReader(f)}


def run(tmp_path, monkeypatch, *, harvest, pool=None, ats="greenhouse"):
    """Fold `harvest` into `pool` in a temp tree; return the resulting pool rows."""
    wb, merged = tmp_path / "wayback-ats", tmp_path / "ats-tenants-merged"
    write_csv(wb / f"{ats}.csv", ["ats", "tenant", "url"], harvest)
    if pool is not None:
        write_csv(merged / f"{ats}.csv", ["ats", "tenant", "url", "source"], pool)
    monkeypatch.setattr(mw, "WAYBACK", wb)
    monkeypatch.setattr(mw, "MERGED", merged)
    mw.main()
    return read_pool(merged / f"{ats}.csv")


def test_a_tenant_the_pool_lacks_is_added_and_tagged(tmp_path, monkeypatch):
    out = run(
        tmp_path,
        monkeypatch,
        harvest=[["greenhouse", "acme", "https://job-boards.greenhouse.io/acme"]],
        pool=[],
    )
    assert out["acme"] == ("https://job-boards.greenhouse.io/acme", "wayback2026")


def test_a_tenant_the_pool_has_is_retagged_not_duplicated(tmp_path, monkeypatch):
    out = run(
        tmp_path,
        monkeypatch,
        harvest=[["greenhouse", "acme", "https://job-boards.greenhouse.io/acme"]],
        pool=[["greenhouse", "acme", "https://boards.greenhouse.io/acme", "harvest"]],
    )
    assert len(out) == 1
    # the source gains the tag; the URL the pool already had is left alone
    assert out["acme"] == ("https://boards.greenhouse.io/acme", "harvest+wayback2026")


def test_no_pool_row_is_ever_dropped(tmp_path, monkeypatch):
    """A row from a source this harvest knows nothing about must survive untouched."""
    out = run(
        tmp_path,
        monkeypatch,
        harvest=[["greenhouse", "acme", "https://job-boards.greenhouse.io/acme"]],
        pool=[
            ["greenhouse", "acme", "https://job-boards.greenhouse.io/acme", "cc2026"],
            [
                "greenhouse",
                "onlyinpool",
                "https://job-boards.greenhouse.io/x",
                "fingerprint",
            ],
        ],
    )
    assert out["onlyinpool"] == ("https://job-boards.greenhouse.io/x", "fingerprint")


def test_second_run_is_a_no_op(tmp_path, monkeypatch):
    harvest = [["greenhouse", "acme", "https://job-boards.greenhouse.io/acme"]]
    pool = [["greenhouse", "old", "https://job-boards.greenhouse.io/old", "cc"]]
    first = run(tmp_path, monkeypatch, harvest=harvest, pool=pool)
    # re-fold the same harvest over the pool the first run produced
    second = run(
        tmp_path,
        monkeypatch,
        harvest=harvest,
        pool=[["greenhouse", t, u, s] for t, (u, s) in sorted(first.items())],
    )
    assert second == first


def test_an_already_tagged_row_is_not_tagged_twice(tmp_path, monkeypatch):
    out = run(
        tmp_path,
        monkeypatch,
        harvest=[["greenhouse", "acme", "https://job-boards.greenhouse.io/acme"]],
        pool=[
            [
                "greenhouse",
                "acme",
                "https://job-boards.greenhouse.io/acme",
                "cc+wayback2026",
            ]
        ],
    )
    assert out["acme"][1] == "cc+wayback2026"


def test_tenant_matching_is_case_insensitive(tmp_path, monkeypatch):
    out = run(
        tmp_path,
        monkeypatch,
        harvest=[["greenhouse", "AcMe", "https://job-boards.greenhouse.io/AcMe"]],
        pool=[["greenhouse", "acme", "https://job-boards.greenhouse.io/acme", "cc"]],
    )
    assert len(out) == 1, "a case variant is the same board, not a new one"


def test_workday_dedupes_on_board_url_not_tenant(tmp_path, monkeypatch):
    """The pool's display slug and the harvest's `{company}/{site}` name one board."""
    out = run(
        tmp_path,
        monkeypatch,
        ats="workday",
        harvest=[
            [
                "workday",
                "accenture/accenturecareers",
                "https://accenture.wd103.myworkdayjobs.com/accenturecareers",
            ]
        ],
        pool=[
            [
                "workday",
                "accenture",
                "https://accenture.wd103.myworkdayjobs.com/accenturecareers",
                "harvest",
            ]
        ],
    )
    assert len(out) == 1, "keying on the tenant would have appended a duplicate board"
    assert out["accenture"][1] == "harvest+wayback2026"


def test_workday_board_url_ignores_scheme_and_case(tmp_path, monkeypatch):
    out = run(
        tmp_path,
        monkeypatch,
        ats="workday",
        harvest=[
            ["workday", "acme/Careers", "https://acme.wd1.myworkdayjobs.com/Careers"]
        ],
        pool=[
            ["workday", "acme-slug", "http://ACME.wd1.myworkdayjobs.com/careers/", "cc"]
        ],
    )
    assert len(out) == 1


def test_workday_genuinely_new_board_is_added(tmp_path, monkeypatch):
    out = run(
        tmp_path,
        monkeypatch,
        ats="workday",
        harvest=[
            ["workday", "acme/Careers", "https://acme.wd1.myworkdayjobs.com/Careers"]
        ],
        pool=[["workday", "other", "https://other.wd5.myworkdayjobs.com/jobs", "cc"]],
    )
    assert len(out) == 2
    assert out["acme/Careers"] == (
        "https://acme.wd1.myworkdayjobs.com/Careers",
        "wayback2026",
    )


def test_a_header_only_harvest_leaves_the_pool_alone(tmp_path, monkeypatch):
    """turbohire.csv is a header and nothing else — it must not blank the pool's rows."""
    out = run(
        tmp_path,
        monkeypatch,
        ats="turbohire",
        harvest=[],
        pool=[["turbohire", "acme", "https://acme.turbohire.co", "wayback"]],
    )
    assert out == {"acme": ("https://acme.turbohire.co", "wayback")}


def test_an_ats_the_pool_lacks_gets_a_new_file(tmp_path, monkeypatch):
    out = run(
        tmp_path,
        monkeypatch,
        ats="teamtailor",
        harvest=[["teamtailor", "acme", "https://acme.teamtailor.com"]],
        pool=None,
    )
    assert out == {"acme": ("https://acme.teamtailor.com", "wayback2026")}


def test_url_less_workday_rows_are_not_falsely_confirmed(tmp_path, monkeypatch):
    """A Workday row with no URL has no identity, so it can never be "re-confirmed".

    `pool_key` returns "" for such a row, and the pool holds 398 of them. Without a falsy guard
    every one of those would match a single url-less harvest row and take a `+wayback2026` tag
    this harvest never earned — 398 rows of invented provenance.
    """
    out = run(
        tmp_path,
        monkeypatch,
        ats="workday",
        harvest=[["workday", "ghost", ""]],  # a harvest row with no URL
        pool=[
            ["workday", "amd", "", "harvest"],
            ["workday", "ibm", "", "harvest"],
            ["workday", "real", "https://real.wd1.myworkdayjobs.com/careers", "cc"],
        ],
    )
    assert out["amd"] == ("", "harvest"), "must not gain a tag it never earned"
    assert out["ibm"] == ("", "harvest")
    assert len(out) == 3, "the url-less harvest row has no identity and must be skipped"


def test_url_less_pool_rows_all_survive(tmp_path, monkeypatch):
    """They collide on the empty key, so a set-based dedupe would collapse them into one."""
    out = run(
        tmp_path,
        monkeypatch,
        ats="workday",
        harvest=[
            ["workday", "acme/Careers", "https://acme.wd1.myworkdayjobs.com/Careers"]
        ],
        pool=[["workday", n, "", "harvest"] for n in ("amd", "ibm", "intuit", "okta")],
    )
    assert {"amd", "ibm", "intuit", "okta"} <= set(out), (
        "no url-less row may be dropped"
    )
