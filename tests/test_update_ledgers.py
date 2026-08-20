"""Tests for the failures subcommand (headstart.ingest.update_ledgers).

The seam these cover is the one the ledger's own unit tests cannot: `board_failures.update` is
pure and already pinned, but it only behaves correctly if `failures` hands it the two sets in the
*same key space*. Shard reports key `{ats}:{slug}` — and a Workday slug is a whole careers URL —
while the corpus keys `board_key()`. Pair them wrongly and nothing raises: gone-verdicts simply
never meet the successes that should clear them, and a live Board accrues strikes forever.
"""

from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

from headstart import board_description_gap
from headstart.ingest import board_failures as bf
from headstart.ingest.update_ledgers import failures, gap

_WORKDAY_URL = "https://x.wd1.myworkdayjobs.com/Careers"
_WORKDAY_BOARD = "workday:x/Careers"


def _run(tmp_path: Path, *, errors=None, boards_ok=None, jobs=(), ledger=None):
    frag = tmp_path / "fragments" / "shard-0"
    frag.mkdir(parents=True, exist_ok=True)
    (frag / "_shard_report.json").write_text(
        json.dumps({"errors": errors or {}, "boards_ok": boards_ok or []}),
        encoding="utf-8",
    )
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir(exist_ok=True)
    (jobs_dir / "greenhouse.jsonl").write_text(
        "".join(json.dumps({"id": i}) + "\n" for i in jobs), encoding="utf-8"
    )
    path = ledger or tmp_path / "board_failures.csv"
    failures(
        argparse.Namespace(fragments=tmp_path / "fragments", jobs=jobs_dir, ledger=path)
    )
    return bf.load(path)


def test_a_gone_error_is_keyed_the_way_the_corpus_keys_it(tmp_path):
    """The Workday case: the report says `workday:https://…/Careers`, the corpus says
    `workday:x/Careers`. Without normalisation the strike lands on a key no success can reach."""
    rows = _run(
        tmp_path, errors={f"workday:{_WORKDAY_URL}": "HTTPError: HTTP Error 404: "}
    )
    assert list(rows) == [_WORKDAY_BOARD]
    assert rows[_WORKDAY_BOARD].strikes == 1


def test_only_the_gone_class_takes_a_strike(tmp_path):
    rows = _run(
        tmp_path,
        errors={
            "greenhouse:dead": "HTTPError: HTTP Error 404: ",
            "greenhouse:limited": "HTTPError: HTTP Error 429: ",
            "greenhouse:slow": "Timeout: timed out",
        },
    )
    assert list(rows) == ["greenhouse:dead"]


def test_a_zero_job_success_clears_a_streak(tmp_path):
    """boards_ok is the whole point: this Board produced no corpus lines, so without it the run
    is indistinguishable from one that never scraped the Board, and its strikes would persist."""
    ledger = tmp_path / "board_failures.csv"
    bf.save(
        ledger, {"greenhouse:quiet": bf.Failure(4, "HTTPError: HTTP Error 404: ", "t")}
    )
    rows = _run(tmp_path, boards_ok=["greenhouse:quiet"], ledger=ledger)
    assert rows == {}, "an alive-but-empty scrape must clear, not carry, the streak"


def test_corpus_lines_also_clear_a_streak(tmp_path):
    """Belt and braces for reports written before boards_ok existed."""
    ledger = tmp_path / "board_failures.csv"
    bf.save(
        ledger, {"greenhouse:busy": bf.Failure(4, "HTTPError: HTTP Error 404: ", "t")}
    )
    rows = _run(tmp_path, jobs=["greenhouse:busy:1"], ledger=ledger)
    assert rows == {}


def test_an_untouched_board_keeps_its_row(tmp_path):
    ledger = tmp_path / "board_failures.csv"
    before = {"greenhouse:elsewhere": bf.Failure(3, "HTTPError: HTTP Error 404: ", "t")}
    bf.save(ledger, before)
    assert _run(tmp_path, ledger=ledger) == before


def test_a_board_reaches_quarantine_only_after_five_consecutive_runs(tmp_path):
    ledger = tmp_path / "board_failures.csv"
    for n in range(1, bf.QUARANTINE_AT + 1):
        rows = _run(
            tmp_path,
            errors={"greenhouse:dead": "HTTPError: HTTP Error 404: "},
            ledger=ledger,
        )
        assert bool(bf.quarantined(rows)) == (n >= bf.QUARANTINE_AT), (
            f"quarantined after {n} run(s); must take {bf.QUARANTINE_AT}"
        )


# ── gap (ADR-0062) ────────────────────────────────────────────────────────────────────────
#
# The seam here mirrors the failures one: `gap` reads ids from two stores written by different
# stages and has to pair them in the same key space. It also has to tell a *missing* description
# store from an empty one — the join downloads it on a warn-only fallback, so getting that wrong
# would mark every Board gap-ful from a failed download.


def _gap_run(
    tmp_path, *, meta_rows, settled, scraped=None, unauthoritative=None, ledger=None
):
    meta = tmp_path / "meta.jsonl"
    meta.write_text("".join(json.dumps(r) + "\n" for r in meta_rows), encoding="utf-8")
    store = tmp_path / "descriptions"
    for ats, ids in settled.items():
        d = store / ats
        d.mkdir(parents=True, exist_ok=True)
        with gzip.open(d / "0001.jsonl.gz", "wt", encoding="utf-8") as fh:
            for job_id in ids:
                fh.write(json.dumps({"id": job_id, "description": "text"}) + "\n")
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir(exist_ok=True)
    for ats, ids in (scraped or {}).items():
        (jobs_dir / f"{ats}.jsonl").write_text(
            "".join(json.dumps({"id": i}) + "\n" for i in ids), encoding="utf-8"
        )
    boards = tmp_path / "unauthoritative_boards.json"
    boards.write_text(json.dumps(unauthoritative or {}), encoding="utf-8")
    path = ledger or tmp_path / "board_description_gap.csv"
    gap(
        argparse.Namespace(
            meta=meta,
            descriptions=store,
            jobs=jobs_dir,
            unauthoritative_boards=boards,
            ledger=path,
        )
    )
    return path


def test_gap_counts_only_rows_the_store_has_not_settled(tmp_path):
    path = _gap_run(
        tmp_path,
        meta_rows=[
            {"id": "greenhouse:acme:1", "ats": "greenhouse"},
            {"id": "greenhouse:acme:2", "ats": "greenhouse"},
            {"id": "greenhouse:acme:3", "ats": "greenhouse"},
            {"id": "lever:beta:9", "ats": "lever"},
        ],
        settled={"greenhouse": ["greenhouse:acme:2"]},
    )
    assert board_description_gap.load(path) == {
        "greenhouse:acme": 2,
        "lever:beta": 1,
    }


def test_gap_keys_workday_the_way_the_slice_looks_it_up(tmp_path):
    """`board_of` splits off only the last segment, so a Workday id whose slug is a whole URL
    still yields the board_key shape `pick_boards` resolves through `board_identity` — lowercased,
    which is the form the quota looks up."""
    path = _gap_run(
        tmp_path,
        meta_rows=[{"id": f"workday:{_WORKDAY_URL}:REQ-1", "ats": "workday"}],
        settled={"greenhouse": ["unrelated"]},
    )
    assert list(board_description_gap.load(path)) == [f"workday:{_WORKDAY_URL}".lower()]


def test_gap_folds_case_variant_boards_into_one_row(tmp_path):
    """ADR-0023: `.../External` and `.../external` are one Board. Keyed as observed they would be
    two half-counts, and the slice — which resolves through `board_identity` — would match at
    most one of them."""
    path = _gap_run(
        tmp_path,
        meta_rows=[
            {"id": "workday:ngc/Northrop_Grumman_External_Site:1", "ats": "workday"},
            {"id": "workday:ngc/northrop_grumman_external_site:2", "ats": "workday"},
        ],
        settled={"greenhouse": ["unrelated"]},
    )
    assert board_description_gap.load(path) == {
        "workday:ngc/northrop_grumman_external_site": 2
    }


def test_gap_skips_a_disabled_ats(tmp_path):
    """`join` is in DISABLED_ATS, so no slice can ever scrape it — reserving slots for its rows
    would spend the quota on Boards that can only leave the index by eviction."""
    path = _gap_run(
        tmp_path,
        meta_rows=[
            {"id": "join:de-co:1", "ats": "join"},
            {"id": "ashby:real:1", "ats": "ashby"},
        ],
        settled={"greenhouse": ["unrelated"]},
    )
    assert board_description_gap.load(path) == {"ashby:real": 1}


def test_gap_skips_an_id_its_own_board_scraped_without_re_emitting(tmp_path):
    """#185: the posting expired off a healthy Board, and `reconcile()` only ever sees ids the
    current scrape returned — so it can never settle, and counting it reserves quota nothing can
    spend. A Board this run never scraped (`greenhouse:elsewhere`) is no evidence and keeps its
    row: that is the partial-harvest rule."""
    path = _gap_run(
        tmp_path,
        meta_rows=[
            {"id": "lever:jobgether:expired", "ats": "lever"},
            {"id": "lever:jobgether:live", "ats": "lever"},
            {"id": "greenhouse:elsewhere:1", "ats": "greenhouse"},
        ],
        settled={"greenhouse": ["unrelated"]},
        scraped={"lever": ["lever:jobgether:live"]},
    )
    assert board_description_gap.load(path) == {
        "lever:jobgether": 1,
        "greenhouse:elsewhere": 1,
    }


def test_gap_keeps_the_ids_of_a_board_whose_scrape_was_not_authoritative(tmp_path):
    """The safety half: a truncated or raised scrape emits the lines it did get, so its missing
    ids look identical to expired ones. ADR-0053 already names those Boards; their Jobs stay
    unsettled rather than being reaped on a scrape that failed."""
    path = _gap_run(
        tmp_path,
        meta_rows=[
            {"id": "lever:jobgether:expired", "ats": "lever"},
            {"id": "lever:jobgether:live", "ats": "lever"},
        ],
        settled={"greenhouse": ["unrelated"]},
        scraped={"lever": ["lever:jobgether:live"]},
        unauthoritative={"lever:jobgether": "HTTPError: HTTP Error 429: "},
    )
    assert board_description_gap.load(path) == {"lever:jobgether": 2}


def test_gap_protects_an_unauthoritative_board_whose_ids_carry_a_colon(tmp_path):
    """ADR-0049's case, and the one that makes the protection worth pinning twice: a Workday
    native id like `REQ: 228` makes `board_of` name a Board that does not exist, which no
    `board_key()`-shaped unauthoritative entry can match. Resolved by prefix instead, so a
    truncated Workday scrape cannot reap the very ids its shape hides."""
    path = _gap_run(
        tmp_path,
        meta_rows=[
            {"id": f"{_WORKDAY_BOARD}:REQ: 228", "ats": "workday"},
            {"id": f"{_WORKDAY_BOARD}:REQ: 229", "ats": "workday"},
        ],
        settled={"greenhouse": ["unrelated"]},
        scraped={"workday": [f"{_WORKDAY_BOARD}:REQ: 229"]},
        unauthoritative={_WORKDAY_BOARD: "truncated: HTTP Error 429: "},
    )
    assert board_description_gap.load(path) == {f"{_WORKDAY_BOARD}:req".lower(): 2}


def test_a_missing_store_leaves_the_ledger_alone(tmp_path):
    """The failure that would otherwise mark every Board gap-ful: the join fetches the store on
    `|| echo ::warning::`, so an empty store means a lost download, not a settled corpus."""
    ledger = tmp_path / "board_description_gap.csv"
    board_description_gap.save(ledger, {"greenhouse:prior": 7}, today="2026-08-17")
    _gap_run(
        tmp_path,
        meta_rows=[{"id": "greenhouse:acme:1", "ats": "greenhouse"}],
        settled={},
        ledger=ledger,
    )
    assert board_description_gap.load(ledger) == {"greenhouse:prior": 7}


def test_no_meta_yet_writes_nothing(tmp_path):
    ledger = tmp_path / "gap.csv"
    gap(
        argparse.Namespace(
            meta=tmp_path / "absent.jsonl",
            descriptions=tmp_path / "descriptions",
            ledger=ledger,
        )
    )
    assert not ledger.exists()
