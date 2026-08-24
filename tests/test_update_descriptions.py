"""Tests for the description store (headstart.ingest.update_descriptions, ADR-0050).

The store exists so a description survives a run whose fetch failed. Every test here is a
statement about one of the three states a Job can be in — we hold its text, we know it has none,
or we have never settled it — because those three are what the skip-list and the repair both
key on.
"""

from __future__ import annotations

import gzip
import json
import sys

from headstart.ingest import update_descriptions as ud


def _corpus(path, jobs: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(job) + "\n" for job in jobs),
        encoding="utf-8",
    )


def _rows(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _job(job_id: str, description=None, detail_fetched: bool = False) -> dict:
    return {
        "id": job_id,
        "ats": "eightfold",
        "title": "Backend Engineer",
        "description": description,
        "detail_fetched": detail_fetched,
    }


def test_a_fetched_description_is_persisted(tmp_path):
    jobs = tmp_path / "tech" / "eightfold.jsonl"
    _corpus(jobs, [_job("eightfold:acme:1", "We are hiring.", detail_fetched=True)])

    ud.reconcile(jobs, tmp_path / "store" / "eightfold")

    assert ud.read_store(tmp_path / "store" / "eightfold") == {
        "eightfold:acme:1": "We are hiring."
    }


def test_a_failed_fetch_is_repaired_from_the_store(tmp_path):
    """The whole point. A Job embedded without its description keeps a title-only vector forever
    (ADR-0047), so the run that loses the fetch must not be the run that embeds it."""
    jobs = tmp_path / "tech" / "eightfold.jsonl"
    store = tmp_path / "store" / "eightfold"
    _corpus(jobs, [_job("eightfold:acme:1", "We are hiring.", detail_fetched=True)])
    ud.reconcile(jobs, store)

    # next run: the detail fetch 405s, so the scrape emits the Job with no description at all
    _corpus(jobs, [_job("eightfold:acme:1", None, detail_fetched=False)])
    filled = ud.reconcile(jobs, store).filled

    assert filled == 1
    assert _rows(jobs)[0]["description"] == "We are hiring."


def test_a_failed_fetch_never_erases_stored_text(tmp_path):
    jobs = tmp_path / "tech" / "eightfold.jsonl"
    store = tmp_path / "store" / "eightfold"
    _corpus(jobs, [_job("eightfold:acme:1", "Original.", detail_fetched=True)])
    ud.reconcile(jobs, store)

    _corpus(jobs, [_job("eightfold:acme:1", None, detail_fetched=False)])
    ud.reconcile(jobs, store)

    assert ud.read_store(store)["eightfold:acme:1"] == "Original."


def test_a_re_fetched_description_supersedes_the_stored_one(tmp_path):
    """Fresh text wins — the only path by which an edited posting reaches the store."""
    jobs = tmp_path / "tech" / "eightfold.jsonl"
    store = tmp_path / "store" / "eightfold"
    _corpus(jobs, [_job("eightfold:acme:1", "Original.", detail_fetched=True)])
    ud.reconcile(jobs, store)

    _corpus(jobs, [_job("eightfold:acme:1", "Edited.", detail_fetched=True)])
    ud.reconcile(jobs, store)

    assert ud.read_store(store)["eightfold:acme:1"] == "Edited."
    assert (
        len(list((store).glob("*.jsonl.gz"))) == 2
    )  # append-only: nothing was rewritten


def test_a_detail_that_answered_with_no_description_is_settled(tmp_path):
    """Authoritative absence. Without recording it, a posting that genuinely has no description
    is re-fetched on every run for the rest of its life."""
    jobs = tmp_path / "tech" / "eightfold.jsonl"
    store = tmp_path / "store" / "eightfold"
    _corpus(jobs, [_job("eightfold:acme:1", None, detail_fetched=True)])

    settled = ud.reconcile(jobs, store).settled

    assert settled == 1
    assert ud.read_store(store) == {"eightfold:acme:1": None}


def test_a_failed_fetch_is_counted_as_unfetched(tmp_path):
    """The same Job as the test below, from the reporting side. It is learned nothing about and
    recorded nowhere, so it used to leave no trace in the log at all — the run reported filled,
    learned and settled, and this Job was in none of them."""
    jobs = tmp_path / "tech" / "eightfold.jsonl"
    store = tmp_path / "store" / "eightfold"
    _corpus(jobs, [_job("eightfold:acme:1", None, detail_fetched=False)])

    assert ud.reconcile(jobs, store).unfetched == 1


def test_a_settled_null_is_not_recounted_as_unfetched(tmp_path):
    """A Job already recorded as having none is answered, not outstanding — it must not inflate
    the backlog every run forever."""
    jobs = tmp_path / "tech" / "eightfold.jsonl"
    store = tmp_path / "store" / "eightfold"
    _corpus(jobs, [_job("eightfold:acme:1", None, detail_fetched=True)])
    ud.reconcile(jobs, store)  # settles it as null

    assert ud.reconcile(jobs, store).unfetched == 0


def test_a_failed_fetch_is_not_settled(tmp_path):
    """The counterpart: an empty description with no completed fetch tells us nothing, so the Job
    stays absent from the store and keeps being retried."""
    jobs = tmp_path / "tech" / "eightfold.jsonl"
    store = tmp_path / "store" / "eightfold"
    _corpus(jobs, [_job("eightfold:acme:1", None, detail_fetched=False)])

    settled = ud.reconcile(jobs, store).settled

    assert settled == 0
    assert ud.read_store(store) == {}


def test_the_skip_list_holds_both_kinds_of_settled_id(tmp_path):
    """Text we hold and absence we have confirmed are both 'nothing left to learn', so both
    belong on the list — an absence left off it is a fetch repeated forever."""
    store = tmp_path / "store"
    jobs = tmp_path / "tech" / "eightfold.jsonl"
    _corpus(
        jobs,
        [
            _job("eightfold:acme:1", "We are hiring.", detail_fetched=True),
            _job("eightfold:acme:2", None, detail_fetched=True),
            _job("eightfold:acme:3", None, detail_fetched=False),
        ],
    )
    ud.reconcile(jobs, store / "eightfold")

    out = tmp_path / "held_details.txt.gz"
    assert ud.write_held_details(store, out) == 2
    with gzip.open(out, "rt", encoding="utf-8") as fh:
        assert sorted(line.strip() for line in fh) == [
            "eightfold:acme:1",
            "eightfold:acme:2",
        ]


def test_compaction_folds_fragments_into_one_base(tmp_path):
    jobs = tmp_path / "tech" / "eightfold.jsonl"
    store = tmp_path / "store" / "eightfold"
    _corpus(jobs, [_job("eightfold:acme:1", "First.", detail_fetched=True)])
    ud.reconcile(jobs, store)
    _corpus(jobs, [_job("eightfold:acme:1", "Second.", detail_fetched=True)])
    ud.reconcile(jobs, store)

    assert ud.compact(store) == 1
    assert [p.name for p in store.glob("*.jsonl.gz")] == ["base.jsonl.gz"]
    assert ud.read_store(store) == {"eightfold:acme:1": "Second."}  # last write won


def test_nothing_learned_writes_no_fragment(tmp_path):
    """A steady-state run learns almost nothing; it must not mint a fragment per run regardless,
    or the append-only saving evaporates into file count."""
    jobs = tmp_path / "tech" / "eightfold.jsonl"
    store = tmp_path / "store" / "eightfold"
    _corpus(jobs, [_job("eightfold:acme:1", "Same.", detail_fetched=True)])
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


def test_reconcile_reports_the_ids_it_settled(tmp_path):
    """Both entry kinds are marked: a text entry gives the cascade something new to read, and an
    authoritative null is equally a settled answer it can derive from."""
    jobs = tmp_path / "tech" / "eightfold.jsonl"
    store = tmp_path / "store" / "eightfold"
    _corpus(
        jobs,
        [
            _job("eightfold:acme:1", "We are hiring.", detail_fetched=True),
            _job(
                "eightfold:acme:2", None, detail_fetched=True
            ),  # settled as having none
            _job("eightfold:acme:3", None, detail_fetched=False),  # a failed fetch
        ],
    )

    done = ud.reconcile(jobs, store)
    learned, settled, ids = done.learned, done.settled, done.rederive_ids

    assert (learned, settled) == (1, 1)
    assert set(ids) == {"eightfold:acme:1", "eightfold:acme:2"}, (
        "the failed fetch settles nothing, so it must not be queued for re-derivation"
    )


def test_an_unchanged_description_is_not_requeued(tmp_path):
    """The queue must drain. A Board re-scraped with the same text has nothing new to derive
    from, so re-marking it every run would make the queue grow without bound."""
    jobs = tmp_path / "tech" / "eightfold.jsonl"
    store = tmp_path / "store" / "eightfold"
    _corpus(jobs, [_job("eightfold:acme:1", "We are hiring.", detail_fetched=True)])
    ud.reconcile(jobs, store)

    _corpus(jobs, [_job("eightfold:acme:1", "We are hiring.", detail_fetched=True)])
    ids = ud.reconcile(jobs, store).rederive_ids

    assert ids == []


def test_an_edited_description_is_requeued(tmp_path):
    """A changed input must reach the cascade again — the numbers were derived from the old text."""
    jobs = tmp_path / "tech" / "eightfold.jsonl"
    store = tmp_path / "store" / "eightfold"
    _corpus(jobs, [_job("eightfold:acme:1", "3+ years.", detail_fetched=True)])
    ud.reconcile(jobs, store)

    _corpus(jobs, [_job("eightfold:acme:1", "8+ years.", detail_fetched=True)])
    ids = ud.reconcile(jobs, store).rederive_ids

    assert ids == ["eightfold:acme:1"]


def test_only_already_embedded_jobs_are_queued_to_rederive(tmp_path, monkeypatch):
    """A Job first embedded this run has its metadata written from this very description, so it
    needs no repair. Queueing every `learned` id would put every new Job on every listing-only
    Board in the queue — tens of thousands per run — and a non-empty queue makes the merge load the
    whole description store every run instead of only on a sweep."""
    jobs_dir = tmp_path / "tech"
    _corpus(
        jobs_dir / "eightfold.jsonl",
        [
            _job("eightfold:acme:old", "3+ years.", detail_fetched=True),
            _job("eightfold:acme:new", "5+ years.", detail_fetched=True),
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
        ],
    )
    ud.main()

    assert queue.read_text(encoding="utf-8").split() == ["eightfold:acme:old"]
