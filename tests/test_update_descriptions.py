"""Tests for the description store (headstart.ingest.update_descriptions, ADR-0050).

The store exists so a description survives a run whose fetch failed. Every test here is a
statement about one of the two states a Job can be in — we hold its text, or we do not — because
that one bit is what the skip-list and the repair both key on (ADR-0089 removed a third,
"we know it has none").
"""

from __future__ import annotations

import gzip
import json
import sys
from datetime import UTC, datetime, timedelta

import pytest

from headstart.ingest import held_refetch
from headstart.ingest import update_descriptions as ud


def _corpus(path, jobs: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(job) + "\n" for job in jobs),
        encoding="utf-8",
    )


def _rows(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _job(job_id: str, description=None) -> dict:
    return {
        "id": job_id,
        "ats": "eightfold",
        "title": "Backend Engineer",
        "description": description,
    }


def test_a_fetched_description_is_persisted(tmp_path):
    jobs = tmp_path / "tech" / "eightfold.jsonl"
    _corpus(jobs, [_job("eightfold:acme:1", "We are hiring.")])

    ud.reconcile(jobs, tmp_path / "store" / "eightfold")

    assert ud.read_store(tmp_path / "store" / "eightfold") == {
        "eightfold:acme:1": "We are hiring."
    }


def test_a_failed_fetch_is_repaired_from_the_store(tmp_path):
    """The whole point. A Job embedded without its description keeps a title-only vector forever
    (ADR-0047), so the run that loses the fetch must not be the run that embeds it."""
    jobs = tmp_path / "tech" / "eightfold.jsonl"
    store = tmp_path / "store" / "eightfold"
    _corpus(jobs, [_job("eightfold:acme:1", "We are hiring.")])
    ud.reconcile(jobs, store)

    # next run: the detail fetch 405s, so the scrape emits the Job with no description at all
    _corpus(jobs, [_job("eightfold:acme:1", None)])
    filled = ud.reconcile(jobs, store).filled

    assert filled == 1
    assert _rows(jobs)[0]["description"] == "We are hiring."


def test_a_failed_fetch_never_erases_stored_text(tmp_path):
    jobs = tmp_path / "tech" / "eightfold.jsonl"
    store = tmp_path / "store" / "eightfold"
    _corpus(jobs, [_job("eightfold:acme:1", "Original.")])
    ud.reconcile(jobs, store)

    _corpus(jobs, [_job("eightfold:acme:1", None)])
    ud.reconcile(jobs, store)

    assert ud.read_store(store)["eightfold:acme:1"] == "Original."


def test_a_re_fetched_description_supersedes_the_stored_one(tmp_path):
    """Fresh text wins — the only path by which an edited posting reaches the store."""
    jobs = tmp_path / "tech" / "eightfold.jsonl"
    store = tmp_path / "store" / "eightfold"
    _corpus(jobs, [_job("eightfold:acme:1", "Original.")])
    ud.reconcile(jobs, store)

    _corpus(jobs, [_job("eightfold:acme:1", "Edited.")])
    ud.reconcile(jobs, store)

    assert ud.read_store(store)["eightfold:acme:1"] == "Edited."
    assert (
        len(list((store).glob("*.jsonl.gz"))) == 2
    )  # append-only: nothing was rewritten


def test_a_failed_fetch_is_counted_as_unrecorded(tmp_path):
    """A Job with no fresh text and nothing stored, from the reporting side. Nothing is learned
    about it and nothing recorded, so it used to leave no trace in the log at all — the run
    reported what it filled and what it learned, and this Job was in neither."""
    jobs = tmp_path / "tech" / "eightfold.jsonl"
    store = tmp_path / "store" / "eightfold"
    _corpus(jobs, [_job("eightfold:acme:1", None)])

    assert ud.reconcile(jobs, store).unrecorded == 1


def test_a_legacy_null_entry_reads_as_unheld(tmp_path):
    """The store briefly recorded `null` for "the detail answered and this posting genuinely has
    none". That state was removed (ADR-0089) because the signal feeding it is wrong on live data:
    eightfold answers 200 with no description for postings that have one, and of the 8 `null`
    entries the state wrote over its lifetime, 5 of the 6 checkable ones name a posting whose page
    serves a real description. The entries are still on disk.

    They must read as *unheld*, not as held-with-no-text: the skip-list is what tells the scrape
    not to bother, and an id on it that the store cannot supply is a description lost for good.
    Skipping on read also makes `compact` purge them with no migration step."""
    store = tmp_path / "store" / "eightfold"
    store.mkdir(parents=True)
    with gzip.open(store / "0001.jsonl.gz", "wt", encoding="utf-8") as fh:
        fh.write(json.dumps({"id": "eightfold:acme:1", "description": None}) + "\n")

    assert ud.read_store(store) == {}, "a null entry holds no text"
    assert ud._ats_held_ids(store) == set(), "so it must not reach the skip-list either"

    jobs = tmp_path / "tech" / "eightfold.jsonl"
    _corpus(jobs, [_job("eightfold:acme:1", None)])
    assert ud.reconcile(jobs, store).unrecorded == 1, "it is outstanding, not answered"


def test_a_text_less_entry_blanks_an_earlier_one_the_same_way_in_both_readers(tmp_path):
    """The two readers must never disagree about who is held, in either fragment order.

    `read_store` removes with `dict.pop` and `_ats_held_ids` with `set.discard`, over the same
    fragments in the same order, so the pair is only safe while both are last-write-wins. If they
    diverged, the skip-list would name an id the store cannot supply — a detail never fetched
    again and a description lost for good."""
    store = tmp_path / "store" / "eightfold"
    store.mkdir(parents=True)

    def fragment(seq: int, description) -> None:
        with gzip.open(store / f"{seq:04d}.jsonl.gz", "wt", encoding="utf-8") as fh:
            fh.write(
                json.dumps({"id": "eightfold:acme:1", "description": description})
                + "\n"
            )

    fragment(1, "We are hiring.")
    fragment(2, None)  # a later entry blanks it
    assert ud.read_store(store) == {}
    assert ud._ats_held_ids(store) == set()

    fragment(3, "We are hiring again.")  # and a later one restores it
    assert ud.read_store(store) == {"eightfold:acme:1": "We are hiring again."}
    assert ud._ats_held_ids(store) == {"eightfold:acme:1"}


def test_compaction_purges_an_all_null_store(tmp_path):
    """The store's legacy nulls disappear with no migration step *only* if compact rewrites a
    directory that holds nothing else. Guarding on `held` instead walked away from that case and
    left the entries on disk for good."""
    store = tmp_path / "store" / "eightfold"
    store.mkdir(parents=True)
    with gzip.open(store / "0001.jsonl.gz", "wt", encoding="utf-8") as fh:
        fh.write(json.dumps({"id": "eightfold:acme:1", "description": None}) + "\n")

    assert ud.compact(store) == 0
    assert sorted(p.name for p in store.glob("*.jsonl.gz")) == ["base.jsonl.gz"]
    assert ud.read_store(store) == {}


def test_the_skip_list_holds_exactly_the_ids_we_have_text_for(tmp_path):
    """The list means one thing: we hold this Job's description, so fetching it again is pure
    cost. A Job we learned nothing about must stay off it, or its description is never fetched."""
    store = tmp_path / "store"
    jobs = tmp_path / "tech" / "eightfold.jsonl"
    _corpus(
        jobs,
        [
            _job("eightfold:acme:1", "We are hiring."),
            _job("eightfold:acme:2", None),  # empty this run -> nothing learned
        ],
    )
    ud.reconcile(jobs, store / "eightfold")

    out = tmp_path / "held_details.txt.gz"
    assert ud.write_held_details(store, out) == 1
    with gzip.open(out, "rt", encoding="utf-8") as fh:
        assert sorted(line.strip() for line in fh) == ["eightfold:acme:1"]


def test_compaction_folds_fragments_into_one_base(tmp_path):
    jobs = tmp_path / "tech" / "eightfold.jsonl"
    store = tmp_path / "store" / "eightfold"
    _corpus(jobs, [_job("eightfold:acme:1", "First.")])
    ud.reconcile(jobs, store)
    _corpus(jobs, [_job("eightfold:acme:1", "Second.")])
    ud.reconcile(jobs, store)

    assert ud.compact(store) == 1
    assert [p.name for p in store.glob("*.jsonl.gz")] == ["base.jsonl.gz"]
    assert ud.read_store(store) == {"eightfold:acme:1": "Second."}  # last write won


def test_nothing_learned_writes_no_fragment(tmp_path):
    """A steady-state run learns almost nothing; it must not mint a fragment per run regardless,
    or the append-only saving evaporates into file count."""
    jobs = tmp_path / "tech" / "eightfold.jsonl"
    store = tmp_path / "store" / "eightfold"
    _corpus(jobs, [_job("eightfold:acme:1", "Same.")])
    ud.reconcile(jobs, store)
    before = sorted(p.name for p in store.glob("*.jsonl.gz"))

    ud.reconcile(jobs, store)

    assert sorted(p.name for p in store.glob("*.jsonl.gz")) == before


def test_fragments_are_ordered_numerically_not_lexicographically(tmp_path):
    """Zero-padding only orders correctly while the width holds. Compaction is the thing that
    keeps the count low, and its workflow step is `continue-on-error` — so the case where the
    width is exceeded is exactly the case where compaction has been silently failing."""
    store = tmp_path / "eightfold"
    store.mkdir(parents=True)
    for name, text in (
        ("0009", "ninth"),
        ("0010", "tenth"),
        ("10000", "ten-thousandth"),
    ):
        with gzip.open(store / f"{name}.jsonl.gz", "wt", encoding="utf-8") as fh:
            fh.write(json.dumps({"id": "eightfold:acme:1", "description": text}) + "\n")

    assert ud.read_store(store)["eightfold:acme:1"] == "ten-thousandth"


def test_the_next_fragment_follows_the_highest_sequence(tmp_path):
    store = tmp_path / "eightfold"
    store.mkdir(parents=True)
    with gzip.open(store / "10000.jsonl.gz", "wt", encoding="utf-8") as fh:
        fh.write(json.dumps({"id": "eightfold:acme:1", "description": "x"}) + "\n")

    out = ud._write_fragment(store, [{"id": "eightfold:acme:2", "description": "y"}])

    assert out.name == "10001.jsonl.gz"


# --- the ADR-0062 re-derivation marking -----------------------------------------------------


def test_reconcile_reports_the_ids_whose_text_arrived(tmp_path):
    """A Job whose description arrives now still carries metadata derived without it, and nothing
    else revisits it — `embed_plan` skips embedded ids and `update_meta` sweeps only on a version
    bump. So each id whose text this run learned is queued for re-derivation (ADR-0062); a Job we
    learned nothing about has nothing new to derive from and must not be."""
    jobs = tmp_path / "tech" / "eightfold.jsonl"
    store = tmp_path / "store" / "eightfold"
    _corpus(
        jobs,
        [
            _job("eightfold:acme:1", "We are hiring."),
            _job("eightfold:acme:2", None),  # empty this run -> nothing learned
        ],
    )

    done = ud.reconcile(jobs, store)

    assert done.learned == 1
    assert set(done.rederive_ids) == {"eightfold:acme:1"}, (
        "a Job with no text learned nothing, so it must not be queued for re-derivation"
    )


def test_an_unchanged_description_is_not_requeued(tmp_path):
    """The queue must drain. A Board re-scraped with the same text has nothing new to derive
    from, so re-marking it every run would make the queue grow without bound."""
    jobs = tmp_path / "tech" / "eightfold.jsonl"
    store = tmp_path / "store" / "eightfold"
    _corpus(jobs, [_job("eightfold:acme:1", "We are hiring.")])
    ud.reconcile(jobs, store)

    _corpus(jobs, [_job("eightfold:acme:1", "We are hiring.")])
    ids = ud.reconcile(jobs, store).rederive_ids

    assert ids == []


def test_an_edited_description_is_requeued(tmp_path):
    """A changed input must reach the cascade again — the numbers were derived from the old text."""
    jobs = tmp_path / "tech" / "eightfold.jsonl"
    store = tmp_path / "store" / "eightfold"
    _corpus(jobs, [_job("eightfold:acme:1", "3+ years.")])
    ud.reconcile(jobs, store)

    _corpus(jobs, [_job("eightfold:acme:1", "8+ years.")])
    ids = ud.reconcile(jobs, store).rederive_ids

    assert ids == ["eightfold:acme:1"]


def test_reconcile_counts_a_replacement_apart_from_a_first_arrival(tmp_path):
    """ADR-0207: every run logs how many held descriptions a fetch replaced. A first arrival and a
    replacement are both `learned`, so the replacement needs its own count."""
    jobs = tmp_path / "tech" / "eightfold.jsonl"
    store = tmp_path / "store" / "eightfold"
    _corpus(jobs, [_job("eightfold:acme:1", "3+ years.")])
    ud.reconcile(jobs, store, {})

    _corpus(
        jobs,
        [_job("eightfold:acme:1", "8+ years."), _job("eightfold:acme:2", "New.")],
    )
    done = ud.reconcile(jobs, store, {})

    assert (done.learned, done.replaced, done.reverted) == (2, 1, 0)


def test_a_replacement_back_to_the_previous_text_is_counted_as_reverted(tmp_path):
    """A flip (text A, then B, then A again) is what a failing fetch path looks like, not an edit.
    The change ledger remembers each Job's previous text by hash, so a return to it is told apart
    from a new revision, and it counts every change the Job has had."""
    jobs = tmp_path / "tech" / "eightfold.jsonl"
    store = tmp_path / "store" / "eightfold"
    changes: dict = {}
    for text in ("Detail text.", "Listing text.", "Detail text."):
        _corpus(jobs, [_job("eightfold:acme:1", text)])
        done = ud.reconcile(jobs, store, changes)

    assert (done.replaced, done.reverted) == (1, 1)
    assert changes["eightfold:acme:1"].count == 2


def test_an_empty_fetch_is_no_change(tmp_path):
    jobs = tmp_path / "tech" / "eightfold.jsonl"
    store = tmp_path / "store" / "eightfold"
    changes: dict = {}
    _corpus(jobs, [_job("eightfold:acme:1", "Held.")])
    ud.reconcile(jobs, store, changes)
    _corpus(jobs, [_job("eightfold:acme:1", "  ")])
    done = ud.reconcile(jobs, store, changes)

    assert (done.replaced, done.reverted) == (0, 0)
    assert changes == {}


def test_the_change_ledger_round_trips(tmp_path):
    path = tmp_path / "description_changes.tsv.gz"
    ledger = {"eightfold:acme:1": ud.ChangeRecord(3, "abc123")}
    ud.write_changes(path, ledger)
    assert ud.read_changes(path) == ledger
    assert ud.read_changes(tmp_path / "absent.tsv.gz") == {}


def test_a_damaged_change_ledger_never_fails_the_run(tmp_path):
    """The ledger only counts. A bad line is skipped, and a file that is not gzip at all starts
    the counts again, rather than failing the step that stores this run's descriptions."""
    path = tmp_path / "description_changes.tsv.gz"
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write("eightfold:acme:1\t2\tabc\nnot a ledger line\n")
    assert ud.read_changes(path) == {"eightfold:acme:1": ud.ChangeRecord(2, "abc")}
    path.write_bytes(b"not gzip")
    assert ud.read_changes(path) == {}


def test_only_already_embedded_jobs_are_queued_to_rederive(tmp_path, monkeypatch):
    """A Job first embedded this run has its metadata written from this very description, so it
    needs no repair. Queueing every `learned` id would put every new Job on every listing-only
    Board in the queue — tens of thousands per run — and a non-empty queue makes the merge load the
    whole description store every run instead of only on a sweep."""
    jobs_dir = tmp_path / "tech"
    _corpus(
        jobs_dir / "eightfold.jsonl",
        [
            _job("eightfold:acme:old", "3+ years."),
            _job("eightfold:acme:new", "5+ years."),
        ],
    )
    prior_meta = tmp_path / "meta.jsonl"
    prior_meta.write_text(
        json.dumps({"id": "eightfold:acme:old"}) + "\n", encoding="utf-8"
    )
    queue = tmp_path / "pending_rederive.txt"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "update_descriptions",
            "--jobs",
            str(jobs_dir),
            "--store",
            str(tmp_path / "store"),
            "--held-details",
            str(tmp_path / "held.txt.gz"),
            "--pending-rederive",
            str(queue),
            "--prior-meta",
            str(prior_meta),
            "--changes",
            str(tmp_path / "changes.tsv.gz"),
            "--checked",
            str(tmp_path / "checked.tsv.gz"),
            "--refetch-due",
            str(tmp_path / "refetch_due.txt"),
        ],
    )
    ud.main()

    assert queue.read_text(encoding="utf-8").split() == ["eightfold:acme:old"]


def test_main_summarises_descriptions_restored_and_still_unknown(tmp_path, monkeypatch):
    jobs_dir = tmp_path / "tech"
    store = tmp_path / "store"
    stored_jobs = tmp_path / "stored.jsonl"
    _corpus(stored_jobs, [_job("eightfold:acme:stored", "Stored text.")])
    ud.reconcile(stored_jobs, store / "eightfold")
    _corpus(
        jobs_dir / "eightfold.jsonl",
        [
            _job("eightfold:acme:stored", None),
            _job("eightfold:acme:fresh", "Fresh text."),
            _job("eightfold:acme:unknown", None),
        ],
    )
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "update_descriptions",
            "--jobs",
            str(jobs_dir),
            "--store",
            str(store),
            "--held-details",
            str(tmp_path / "held.txt.gz"),
            "--pending-rederive",
            str(tmp_path / "pending.txt"),
            "--prior-meta",
            str(tmp_path / "absent-meta.jsonl"),
            "--changes",
            str(tmp_path / "changes.tsv.gz"),
            "--checked",
            str(tmp_path / "checked.tsv.gz"),
            "--refetch-due",
            str(tmp_path / "refetch_due.txt"),
        ],
    )

    assert ud.main() == 0

    text = summary.read_text(encoding="utf-8")
    assert "**1** description restored from the store" in text
    assert "**1** description learned from fresh detail fetches" in text
    assert "**1** Job still has an unknown description" in text


def _rotation_run(tmp_path, monkeypatch, corpus: list[dict], at) -> None:
    """One `update_descriptions` run over an eightfold corpus, with every path in tmp_path."""
    monkeypatch.setattr(held_refetch, "now", lambda: at)
    _corpus(tmp_path / "tech" / "eightfold.jsonl", corpus)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "update_descriptions",
            "--jobs",
            str(tmp_path / "tech"),
            "--store",
            str(tmp_path / "store"),
            "--held-details",
            str(tmp_path / "held.txt.gz"),
            "--pending-rederive",
            str(tmp_path / "pending.txt"),
            "--prior-meta",
            str(tmp_path / "absent-meta.jsonl"),
            "--changes",
            str(tmp_path / "changes.tsv.gz"),
            "--checked",
            str(tmp_path / "checked.tsv.gz"),
            "--refetch-due",
            str(tmp_path / "refetch_due.txt"),
        ],
    )
    assert ud.main() == 0


def _skip_list(tmp_path) -> set[str]:
    with gzip.open(tmp_path / "held.txt.gz", "rt", encoding="utf-8") as fh:
        return {line.strip() for line in fh if line.strip()}


_AT = datetime(2026, 9, 25, 12, tzinfo=UTC)
_PERIOD = timedelta(days=held_refetch.PERIOD_DAYS)


def test_a_held_job_leaves_the_skip_list_a_period_after_its_last_fetch(
    tmp_path, monkeypatch
):
    """ADR-0211. Eightfold skips the detail of a Job whose description is held, so an edit was
    never fetched again. A held Job whose last fetch is a period old is left off the skip-list,
    and the next scrape fetches it."""
    _rotation_run(tmp_path, monkeypatch, [_job("eightfold:acme:1", "Held.")], _AT)
    assert "eightfold:acme:1" in _skip_list(tmp_path)  # fetched this run

    _rotation_run(
        tmp_path, monkeypatch, [_job("eightfold:acme:1", None)], _AT + _PERIOD
    )
    assert "eightfold:acme:1" not in _skip_list(tmp_path)
    assert (tmp_path / "refetch_due.txt").read_text().split() == ["eightfold:acme:1"]


def test_a_re_fetch_that_comes_back_empty_keeps_the_held_text_and_waits_a_period(
    tmp_path, monkeypatch
):
    """The due Job was asked for and came back empty. Its held text stays (ADR-0050/0089), and
    it counts as checked, so a posting whose detail always answers empty is not fetched on every
    scrape."""
    _rotation_run(tmp_path, monkeypatch, [_job("eightfold:acme:1", "Held.")], _AT)
    later = _AT + _PERIOD
    _rotation_run(tmp_path, monkeypatch, [_job("eightfold:acme:1", None)], later)
    _rotation_run(tmp_path, monkeypatch, [_job("eightfold:acme:1", None)], later)

    assert ud.read_store(tmp_path / "store" / "eightfold") == {
        "eightfold:acme:1": "Held."
    }
    assert "eightfold:acme:1" in _skip_list(tmp_path)


def test_a_torn_corpus_line_names_its_file_and_line(tmp_path):
    """Still fatal, but no longer a bare JSONDecodeError that names neither."""
    jobs = tmp_path / "lever.jsonl"
    jobs.write_text('{"id": "lever:a:1"}\n{"id": \n', encoding="utf-8")
    with pytest.raises(ValueError, match=rf"{jobs} line 2: "):
        ud.reconcile(jobs, tmp_path / "store" / "lever")


def test_malformed_change_ledger_lines_are_counted(tmp_path, caplog):
    path = tmp_path / "changes.tsv.gz"
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write("lever:a:1\t2\tabc\ntorn\n")
    with caplog.at_level("INFO", logger=ud.__name__):
        assert list(ud.read_changes(path)) == ["lever:a:1"]
    assert caplog.messages == [f"{path}: skipped 1 malformed line(s)"]


def test_main_turns_a_torn_line_into_a_named_abort(tmp_path, monkeypatch, caplog):
    """An `::error::` naming the file and line, not a bare traceback."""
    jobs_dir = tmp_path / "tech"
    jobs_dir.mkdir()
    (jobs_dir / "lever.jsonl").write_text('{"id": \n', encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "update_descriptions",
            "--jobs",
            str(jobs_dir),
            "--store",
            str(tmp_path / "store"),
            "--prior-meta",
            str(tmp_path / "absent-meta.jsonl"),
        ],
    )
    with caplog.at_level("INFO", logger=ud.__name__), pytest.raises(SystemExit):
        ud.main()
    assert "no embedding metadata at" in caplog.text
    errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert len(errors) == 1
    assert errors[0].startswith(
        f"description store update aborted: {jobs_dir / 'lever.jsonl'} line 1: "
    )


def test_a_value_error_that_is_not_a_torn_record_keeps_its_traceback(monkeypatch):
    """Only a torn record is worded as an abort; any other ValueError is a bug, and `log.fail`
    would have swallowed the stack that names its file and line."""

    def buggy() -> int:
        raise ValueError("a code bug, not a torn line")

    monkeypatch.setattr(ud, "_update_store", buggy)
    with pytest.raises(ValueError, match="a code bug"):
        ud.main()
