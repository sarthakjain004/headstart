"""Tests for the Job facts writer (headstart.ingest.job_facts, ADR-0330).

The facts are what each scrape saw, recorded once: a Job first listed, changed, no longer listed by
an authoritative read of its Board, or on a Board that left `index prune`'s keep-set. A Board the run did not
read, or read short, is no evidence, so its Jobs stay listed and write nothing.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from headstart.boards.board_identity import lower_key
from headstart.ingest import board_failures
from headstart.ingest import job_facts as jf
from headstart.ingest.observability import ShardReport
from headstart.jobs.job import Job

BOARD_A = "greenhouse:acme"
BOARD_B = "lever:globex"


def _job(job_id: str, title: str = "Backend Engineer", **fields) -> dict:
    return {
        "id": job_id,
        "ats": job_id.split(":", 1)[0],
        "company": "Acme",
        "title": title,
        "location": "Remote",
        "remote": True,
        "department": "Engineering",
        "url": f"https://example.com/{job_id}",
        "posted_at": "2026-09-01",
        "scraped_at": "2026-09-29T06:00:00+00:00",
        "description": "Build things.",
        **fields,
    }


def _scope(authoritative: set[str], keep_set: set[str] | None = None) -> jf.RunScope:
    return jf.RunScope(
        authoritative=frozenset(lower_key(b) for b in authoritative),
        keep_set=None
        if keep_set is None
        else frozenset(lower_key(b) for b in keep_set),
        live={lower_key(b): b for b in keep_set or ()},
    )


def _run(
    tmp_path: Path,
    stamp: str,
    jobs: list[tuple[str, dict]],
    scope: set[str],
    keep_set: set[str] | None = None,
) -> jf.RunFacts:
    lines = jf.ScrapedLines(tmp_path / "scratch.parquet")
    for board, job in jobs:
        lines.see(board, job)
    return jf.record_run(
        lines.close(), tmp_path / "facts", stamp, [], _scope(scope, keep_set)
    )


def _facts(tmp_path: Path, stamp: str) -> dict[str, dict]:
    table = pq.read_table(tmp_path / "facts" / jf.JOB_FACTS / jf.file_name(stamp))
    return {row["id"]: row for row in table.to_pylist()}


def _listed(tmp_path: Path) -> set[str]:
    return set(pq.read_table(tmp_path / "facts" / jf.LISTED_JOBS)["id"].to_pylist())


T1 = "2026-09-29T06:00:00+00:00"
T2 = "2026-09-29T07:00:00+00:00"
T3 = "2026-09-29T08:00:00+00:00"


def test_a_first_run_lists_every_job_with_its_raw_fields(tmp_path):
    recorded = _run(
        tmp_path,
        T1,
        [
            (BOARD_A, _job(f"{BOARD_A}:1")),
            (BOARD_B, _job(f"{BOARD_B}:2", description=None)),
        ],
        {BOARD_A, BOARD_B},
    )

    assert (recorded.listed, recorded.changed, recorded.unlisted) == (2, 0, 0)
    facts = _facts(tmp_path, T1)
    assert facts[f"{BOARD_A}:1"]["kind"] == "listed"
    assert facts[f"{BOARD_A}:1"]["title"] == "Backend Engineer"
    assert facts[f"{BOARD_A}:1"]["remote"] is True
    assert facts[f"{BOARD_A}:1"]["has_description"] is True
    assert facts[f"{BOARD_B}:2"]["has_description"] is False
    assert _listed(tmp_path) == {f"{BOARD_A}:1", f"{BOARD_B}:2"}


def test_a_job_whose_raw_fields_moved_is_a_changed_fact_carrying_the_new_values(
    tmp_path,
):
    _run(tmp_path, T1, [(BOARD_A, _job(f"{BOARD_A}:1"))], {BOARD_A})
    recorded = _run(
        tmp_path, T2, [(BOARD_A, _job(f"{BOARD_A}:1", department="Data"))], {BOARD_A}
    )

    assert (recorded.listed, recorded.changed, recorded.unlisted) == (0, 1, 0)
    assert _facts(tmp_path, T2)[f"{BOARD_A}:1"]["department"] == "Data"


def test_a_description_appearing_or_going_is_not_a_fact(tmp_path):
    """ADR-0048: a Job whose text the store already holds is scraped without it, and with it again
    on a re-fetch, so its presence moves with our skip-list, not with the posting."""
    _run(tmp_path, T1, [(BOARD_A, _job(f"{BOARD_A}:1", description=None))], {BOARD_A})
    recorded = _run(tmp_path, T2, [(BOARD_A, _job(f"{BOARD_A}:1"))], {BOARD_A})

    assert recorded.changed == 0


def test_a_description_edit_alone_is_not_a_fact(tmp_path):
    _run(tmp_path, T1, [(BOARD_A, _job(f"{BOARD_A}:1"))], {BOARD_A})
    recorded = _run(
        tmp_path,
        T2,
        [(BOARD_A, _job(f"{BOARD_A}:1", description="Build other things."))],
        {BOARD_A},
    )

    assert (recorded.listed, recorded.changed, recorded.unlisted) == (0, 0, 0)
    assert _facts(tmp_path, T2) == {}


def test_a_job_an_authoritative_read_missed_is_unlisted_and_leaves_the_listed_set(
    tmp_path,
):
    _run(
        tmp_path,
        T1,
        [(BOARD_A, _job(f"{BOARD_A}:1")), (BOARD_A, _job(f"{BOARD_A}:2"))],
        {BOARD_A},
    )
    recorded = _run(tmp_path, T2, [(BOARD_A, _job(f"{BOARD_A}:1"))], {BOARD_A})

    assert recorded.unlisted == 1
    fact = _facts(tmp_path, T2)[f"{BOARD_A}:2"]
    assert fact["kind"] == "unlisted"
    assert fact["board"] == BOARD_A
    assert fact["title"] is None
    assert _listed(tmp_path) == {f"{BOARD_A}:1"}


def test_a_board_read_short_or_not_read_is_no_evidence_its_jobs_went(tmp_path):
    _run(
        tmp_path,
        T1,
        [(BOARD_A, _job(f"{BOARD_A}:1")), (BOARD_B, _job(f"{BOARD_B}:2"))],
        {BOARD_A, BOARD_B},
    )
    # BOARD_A was read short (out of scope), BOARD_B not read at all.
    recorded = _run(tmp_path, T2, [], set())

    assert (recorded.listed, recorded.changed, recorded.unlisted) == (0, 0, 0)
    assert _listed(tmp_path) == {f"{BOARD_A}:1", f"{BOARD_B}:2"}


def test_a_board_read_clean_with_no_jobs_unlists_everything_it_held(tmp_path):
    _run(tmp_path, T1, [(BOARD_A, _job(f"{BOARD_A}:1"))], {BOARD_A})
    recorded = _run(tmp_path, T2, [], {BOARD_A})

    assert recorded.unlisted == 1
    assert _listed(tmp_path) == set()


def test_an_id_stored_under_another_casing_is_still_in_its_boards_scope(tmp_path):
    """ADR-0243: ids are matched to the scope case-folded, as `index sync` matches them."""
    _run(tmp_path, T1, [(BOARD_A, _job(f"{BOARD_A.upper()}:1"))], {BOARD_A})
    recorded = _run(tmp_path, T2, [], {BOARD_A})

    assert recorded.unlisted == 1


def test_a_job_whose_board_left_prunes_keep_set_is_off_board(tmp_path):
    """Like `index prune`'s off-Board sweep: a Board no run will read again must not keep its
    Jobs listed for good. A Board still in the keep-set but unread keeps them."""
    _run(
        tmp_path,
        T1,
        [(BOARD_A, _job(f"{BOARD_A}:1")), (BOARD_B, _job(f"{BOARD_B}:2"))],
        {BOARD_A, BOARD_B},
    )
    recorded = _run(tmp_path, T2, [], set(), keep_set={BOARD_A})

    assert (recorded.unlisted, recorded.off_board) == (0, 1)
    assert _facts(tmp_path, T2)[f"{BOARD_B}:2"]["kind"] == "off_board"
    assert _listed(tmp_path) == {f"{BOARD_A}:1"}


def test_with_no_trusted_keep_set_nothing_is_off_board(tmp_path):
    _run(tmp_path, T1, [(BOARD_A, _job(f"{BOARD_A}:1"))], {BOARD_A})
    recorded = _run(tmp_path, T2, [], set(), keep_set=None)

    assert recorded.off_board == 0
    assert _listed(tmp_path) == {f"{BOARD_A}:1"}


def _live(n: int) -> dict[str, str]:
    boards = [f"greenhouse:co{i}" for i in range(n)]
    return {lower_key(b): b for b in boards}


def test_the_scope_is_the_boards_read_less_the_unauthoritative_ones_case_folded():
    scope = jf.RunScope.of(
        {"greenhouse:Acme", "lever:globex"}, {"lever:globex"}, {}, {}
    )

    assert scope.authoritative == {"greenhouse:acme"}


def test_the_keep_set_leaves_out_boards_reconfirmed_gone_after_parole():
    """ADR-0206: `index prune` evicts a Board whose parole re-confirmed it gone, and the facts shed
    its Jobs with it. A Board quarantined once is still in both keep-sets."""
    live = _live(jf.MIN_KEEP_BOARDS + 2)
    failures = {
        "greenhouse:co0": board_failures.Failure(
            board_failures.QUARANTINE_AT + 1, "404", "2026-09-20"
        ),
        "greenhouse:co1": board_failures.Failure(
            board_failures.QUARANTINE_AT, "404", "2026-09-20"
        ),
    }

    scope = jf.RunScope.of(set(), set(), live, failures)

    assert scope.keep_set is not None
    assert "greenhouse:co0" not in scope.keep_set
    assert "greenhouse:co1" in scope.keep_set
    assert scope.absent_as("greenhouse:co0:7") == "off_board"
    assert scope.absent_as("greenhouse:co1:7") is None


def test_a_keep_set_prune_would_refuse_sheds_nothing():
    """`index prune` refuses a keep-set under MIN_KEEP_BOARDS as a broken ledger; so do the facts."""
    scope = jf.RunScope.of(set(), set(), _live(jf.MIN_KEEP_BOARDS - 1), {})

    assert scope.keep_set is None
    assert scope.absent_as("lever:nowhere:1") is None


def test_a_job_listed_again_after_it_went_is_listed_once_more(tmp_path):
    _run(tmp_path, T1, [(BOARD_A, _job(f"{BOARD_A}:1"))], {BOARD_A})
    _run(tmp_path, T2, [], {BOARD_A})
    recorded = _run(tmp_path, T3, [(BOARD_A, _job(f"{BOARD_A}:1"))], {BOARD_A})

    assert recorded.listed == 1
    assert _facts(tmp_path, T3)[f"{BOARD_A}:1"]["kind"] == "listed"


def test_an_id_the_scrape_returned_twice_is_one_fact(tmp_path):
    recorded = _run(
        tmp_path,
        T1,
        [
            (BOARD_A, _job(f"{BOARD_A}:1")),
            (BOARD_A, _job(f"{BOARD_A}:1", title="Other")),
        ],
        {BOARD_A},
    )

    assert recorded.listed == 1
    assert _facts(tmp_path, T1)[f"{BOARD_A}:1"]["title"] == "Backend Engineer"


def test_every_file_names_its_run_and_scope_rule(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_SHA", "abc123")
    monkeypatch.setenv("GITHUB_RUN_ID", "42")
    _run(tmp_path, T1, [(BOARD_A, _job(f"{BOARD_A}:1"))], {BOARD_A})

    for path in (
        tmp_path / "facts" / jf.JOB_FACTS / jf.file_name(T1),
        tmp_path / "facts" / jf.BOARD_READS / jf.file_name(T1),
        tmp_path / "facts" / jf.LISTED_JOBS,
    ):
        meta = pq.read_schema(path).metadata
        assert meta[b"stamp"] == T1.encode()
        assert meta[b"scope_version"] == str(jf.SCOPE_VERSION).encode()
        assert meta[b"code_sha"] == b"abc123"
        assert meta[b"run_id"] == b"42"


def test_no_staged_file_is_left_behind(tmp_path):
    _run(tmp_path, T1, [(BOARD_A, _job(f"{BOARD_A}:1"))], {BOARD_A})

    assert not list((tmp_path / "facts").rglob("*.tmp"))


def test_a_scratch_writer_that_fails_keeps_the_failure_instead_of_raising(
    tmp_path, monkeypatch
):
    lines = jf.ScrapedLines(tmp_path / "scratch.parquet")
    monkeypatch.setattr(jf, "_BATCH", 1)

    def fail():
        raise OSError("disk full")

    monkeypatch.setattr(lines, "_flush", fail)
    lines.see(BOARD_A, _job(f"{BOARD_A}:1"))
    lines.see(BOARD_A, _job(f"{BOARD_A}:2"))
    lines.close()

    assert isinstance(lines.failure, OSError)


def test_a_failed_write_leaves_no_facts_and_the_older_listed_set(tmp_path, monkeypatch):
    """All or nothing: the next run diffs against the older set and writes these changes again,
    so a half-written run can neither lose a change nor record it twice."""
    _run(tmp_path, T1, [(BOARD_A, _job(f"{BOARD_A}:1"))], {BOARD_A})
    write = jf._write_staged

    def fail_on_the_listed_set(table, path, stamp):
        if path.name == jf.LISTED_JOBS:
            raise OSError("disk full")
        write(table, path, stamp)

    monkeypatch.setattr(jf, "_write_staged", fail_on_the_listed_set)
    with pytest.raises(OSError):
        _run(tmp_path, T2, [(BOARD_A, _job(f"{BOARD_A}:2"))], {BOARD_A})

    assert not (tmp_path / "facts" / jf.JOB_FACTS / jf.file_name(T2)).exists()
    assert not (tmp_path / "facts" / jf.BOARD_READS / jf.file_name(T2)).exists()
    assert _listed(tmp_path) == {f"{BOARD_A}:1"}


@pytest.mark.parametrize(
    ("report", "outcome", "reason", "in_scope"),
    [
        (ShardReport(boards_ok=[BOARD_A]), "authoritative", None, True),
        (
            ShardReport(boards_ok=[BOARD_A], truncated={BOARD_A: "listing cap"}),
            "truncated",
            "listing cap",
            False,
        ),
        (
            ShardReport(errors={BOARD_A: "HTTPError: 503"}),
            "error",
            "HTTPError: 503",
            False,
        ),
    ],
)
def test_a_board_read_records_its_outcome(report, outcome, reason, in_scope):
    report.observations[BOARD_A] = {"stated_total": 12}
    report.board_seconds[BOARD_A] = 1.5
    scope = _scope({BOARD_A} if in_scope else set())

    (read,) = jf.board_reads([report], {BOARD_A: 3}, scope)

    assert (read.scraper_key, read.board, read.outcome, read.reason) == (
        BOARD_A,
        BOARD_A,
        outcome,
        reason,
    )
    assert read.in_scope is in_scope
    assert (read.lines, read.stated_total, read.seconds) == (3, 12, 1.5)


def test_a_malformed_observation_reads_as_no_stated_total():
    """ADR-0154: `observability` reads a non-dict observation as none; so do the Board reads."""
    report = ShardReport(boards_ok=[BOARD_A], observations={BOARD_A: "garbage"})

    (read,) = jf.board_reads([report], {}, _scope({BOARD_A}))

    assert read.stated_total is None


def test_every_job_field_but_its_identity_time_and_text_is_a_raw_field():
    names = {f.name for f in dataclasses.fields(Job)}

    assert set(jf.RAW_FIELDS) == names - {"id", "ats", "scraped_at", "description"}


def test_the_fields_hash_is_stable_and_ignores_fields_no_rule_reads():
    job = _job(f"{BOARD_A}:1")

    assert jf.fields_hash(job) == jf.fields_hash(dict(job, scraped_at="later"))
    assert jf.fields_hash(job) != jf.fields_hash(dict(job, title="Frontend Engineer"))
    # A field a Job does not state leaves the hash alone, so adding one to `Job` does not turn
    # every listed Job into a changed fact.
    assert jf.fields_hash(job) == jf.fields_hash(dict(job, salary=None))


def test_no_vector_is_archived_when_nothing_is_dropped(tmp_path):
    assert jf.archive_vectors(tmp_path, T1, [], None, "model") == 0
    assert not (tmp_path / jf.JOB_VECTORS).exists()
