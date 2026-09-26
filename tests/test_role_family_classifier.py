"""Tests for the classifier that decides a row's role family from its title and its served
description vector (ADR-0220, ADR-0224).

Contracts: the head adds a row's title part and row part and abstains below its cutoff; the row
part alone can move a row whose title says otherwise; a malformed head is refused; the title
cache keeps title logits, survives a round trip and is discarded under another head; filling
encodes only missing titles, saves after each chunk and stops on its time budget; a row whose
title no run has encoded is unclassified; coverage counts served rows, not distinct titles; and
encoding batches titles shortest first but returns vectors in input order. The encoder is
stubbed, so no test downloads JobBERT.
"""

from __future__ import annotations

import json

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("pyarrow")

from headstart.ingest import role_family_classifier as rfc

_FAMILIES = ["software-engineering", "qa-test", "non-tech"]
_ROW_DIM = 2
#: a served vector the row part reads as non-tech, strongly enough to outvote a one-hot title
_NON_TECH_ROW = [0.0, 1.0]


def _head(tmp_path, cutoff=0.6, families=_FAMILIES, title_rows=None, row_dim=_ROW_DIM):
    """Title part: a one-hot title vector names its family. Row part: the first row coordinate is
    neutral and the second pulls hard towards non-tech."""
    directory = tmp_path / "head"
    directory.mkdir()
    title_weights = (
        np.eye(len(families), 3, dtype=np.float32) * 10
        if title_rows is None
        else title_rows
    )
    row_weights = np.zeros((len(families), row_dim), np.float32)
    if "non-tech" in families:
        row_weights[families.index("non-tech"), -1] = 30.0
    np.savez(
        directory / "head.npz",
        title_weights=title_weights,
        row_weights=row_weights,
        bias=np.zeros(len(families), np.float32),
    )
    (directory / "manifest.json").write_text(
        json.dumps(
            {
                "version": 7,
                "model": "stub",
                "model_revision": "stub",
                "row_vector": {"model": "stub", "dim": _ROW_DIM},
                "families": families,
                "cutoff": cutoff,
            }
        ),
        encoding="utf-8",
    )
    return rfc.Head(directory)


def _neutral_rows(n):
    return np.zeros((n, _ROW_DIM), np.float32)


def _stub_encoder(monkeypatch, calls=None):
    """Titles naming "software" go to the first family, "qa" to the second, "clerk" to non-tech;
    anything else is equidistant, so the head cannot place it."""

    def encode(titles, model, revision):
        if calls is not None:
            calls.append(list(titles))
        rows = []
        for title in titles:
            if "software" in title:
                rows.append([1.0, 0.0, 0.0])
            elif "qa" in title:
                rows.append([0.0, 1.0, 0.0])
            elif "clerk" in title:
                rows.append([0.0, 0.0, 1.0])
            else:
                rows.append([0.1, 0.1, 0.1])
        return np.array(rows, dtype=np.float32)

    monkeypatch.setattr(rfc, "encode", encode)


def test_encode_batches_shortest_first_and_returns_input_order(monkeypatch):
    torch = pytest.importorskip("torch")
    batches = []

    class Encoder:
        def tokenizer(self, titles):
            return {"input_ids": [t.split() for t in titles]}

        def preprocess(self, titles):
            batches.append(list(titles))
            return {"titles": list(titles)}

        def forward(self, features):
            assert features["text_keys"] == ["anchor"]
            rows = [[float(len(t)), float(ord(t[0]))] for t in features["titles"]]
            return {"sentence_embedding": torch.tensor(rows)}

    monkeypatch.setattr(rfc, "_encoder", lambda model, revision: Encoder())
    monkeypatch.setattr(rfc, "_ENCODE_BATCH", 2)
    # word count stands in for token count; "reliability engineer" is long in characters but
    # short in words, so a character sort would misplace it
    titles = [
        "principal staff engineer",
        "qa",
        "reliability engineer",
        "sre",
        "senior it lead",
    ]

    vectors = rfc.encode(titles, "stub", "stub")

    assert vectors.tolist() == [[len(t), ord(t[0])] for t in titles]
    assert [t for batch in batches for t in batch] == [
        "qa",
        "sre",
        "reliability engineer",
        "principal staff engineer",
        "senior it lead",
    ]
    assert rfc.encode([], "stub", "stub").shape == (0, 0)


def test_normalise_is_the_cache_key():
    assert rfc.normalise("  Senior   QA Engineer ") == "senior qa engineer"
    assert rfc.normalise(None) == ""


def test_the_head_decides_and_abstains_below_its_cutoff(tmp_path):
    head = _head(tmp_path)
    titles = head.title_logits(np.array([[1.0, 0, 0], [0.1, 0.1, 0.1]], np.float32))
    decided = head.decide(titles, head.row_logits(_neutral_rows(2)))
    assert decided[0][0] == "software-engineering" and decided[0][1] > 0.99
    assert decided[1][0] == rfc.UNCLASSIFIED


def test_the_row_part_moves_a_row_its_title_alone_would_misfile(tmp_path):
    """ADR-0224: the same software title, with a description that reads as non-tech."""
    head = _head(tmp_path)
    titles = head.title_logits(np.array([[1.0, 0, 0], [1.0, 0, 0]], np.float32))
    rows = head.row_logits(np.array([[1.0, 0.0], _NON_TECH_ROW], np.float32))
    assert [family for family, _ in head.decide(titles, rows)] == [
        "software-engineering",
        "non-tech",
    ]


def test_a_head_whose_weights_and_manifest_disagree_is_refused(tmp_path):
    with pytest.raises(ValueError, match="disagree"):
        _head(tmp_path, title_rows=np.zeros((2, 3), np.float32))


def test_a_head_whose_row_weights_miss_the_row_vector_width_is_refused(tmp_path):
    with pytest.raises(ValueError, match="disagree"):
        _head(tmp_path, row_dim=_ROW_DIM + 1)


def test_a_head_that_trained_the_abstain_family_is_refused(tmp_path):
    with pytest.raises(ValueError, match="cutoff produces"):
        _head(tmp_path, families=["software-engineering", "qa-test", rfc.UNCLASSIFIED])


def test_the_cache_round_trips_and_is_discarded_under_another_head(tmp_path):
    path = tmp_path / "cache.parquet"
    logits = np.array([0.5, -1.25, 3.0], np.float32)
    rfc.save_cache(path, rfc.Cache(7, {"qa engineer": logits}))
    loaded = rfc.load_cache(path, 7).title_logits
    assert list(loaded) == ["qa engineer"]
    assert loaded["qa engineer"].tolist() == logits.tolist()
    assert rfc.load_cache(path, 8).title_logits == {}


def test_an_unreadable_cache_starts_empty(tmp_path):
    path = tmp_path / "cache.parquet"
    path.write_bytes(b"not parquet")
    assert rfc.load_cache(path, 7).title_logits == {}


def test_fill_encodes_only_missing_titles_and_saves_each_chunk(tmp_path, monkeypatch):
    calls: list[list[str]] = []
    _stub_encoder(monkeypatch, calls)
    monkeypatch.setattr(rfc, "_FILL_CHUNK", 2)
    head = _head(tmp_path)
    cache = rfc.Cache(7, {"software engineer": np.array([10.0, 0, 0], np.float32)})
    saved: list[int] = []
    titles = ["Software Engineer", "QA Lead", "Store Clerk", "Engineer II", None]
    added = rfc.fill(
        cache,
        head,
        titles,
        budget_seconds=60,
        checkpoint=lambda c: saved.append(len(c.title_logits)),
    )
    assert added == 4  # "" (from None), "engineer ii", "qa lead", "store clerk"
    assert calls == [["", "engineer ii"], ["qa lead", "store clerk"]]
    assert saved == [3, 5]
    assert rfc.decide_rows(cache, head, titles, head.row_logits(_neutral_rows(5))) == [
        "software-engineering",
        "qa-test",
        "non-tech",
        rfc.UNCLASSIFIED,  # the head cannot place "engineer ii"
        rfc.UNCLASSIFIED,
    ]


def test_fill_stops_when_its_budget_is_spent(tmp_path, monkeypatch):
    _stub_encoder(monkeypatch)
    monkeypatch.setattr(rfc, "_FILL_CHUNK", 1)
    clock = iter([0.0, 0.0, 100.0, 100.0])
    monkeypatch.setattr(rfc.time, "monotonic", lambda: next(clock))
    cache = rfc.Cache(7, {})
    added = rfc.fill(
        cache, _head(tmp_path), ["a software role", "a qa role"], 50, lambda c: None
    )
    assert added == 1 and len(cache.title_logits) == 1


def test_a_row_whose_title_no_run_has_encoded_counts_as_unclassified(tmp_path):
    head = _head(tmp_path)
    cache = rfc.Cache(7, {"qa engineer": np.array([0, 10.0, 0], np.float32)})
    rows = head.row_logits(np.array([[1.0, 0.0], _NON_TECH_ROW], np.float32))
    assert rfc.decide_rows(cache, head, ["Staff Engineer", "QA Engineer"], rows) == [
        rfc.UNCLASSIFIED,
        "non-tech",  # the row part outvotes the qa title
    ]


def test_coverage_counts_served_rows_not_distinct_titles():
    cache = rfc.Cache(7, {"qa engineer": np.zeros(3, np.float32)})
    assert (
        rfc.coverage(
            cache, ["QA Engineer", "qa engineer", "QA ENGINEER", "Staff Engineer"]
        )
        == 0.75
    )


def test_the_pipelines_model_cache_key_names_the_heads_model_revision():
    """The merge job caches JobBERT under a key written by hand in pipeline.yml. A new head at a
    new revision must change that key, or every run restores the old weights and re-downloads."""
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    manifest = json.loads(
        (repo / "config" / "role_family_classifier" / "manifest.json").read_text(
            "utf-8"
        )
    )
    workflow = (repo / ".github" / "workflows" / "pipeline.yml").read_text("utf-8")
    assert f"key: hf-model-jobbert-v2-{manifest['model_revision'][:12]}" in workflow


def test_the_shipped_head_agrees_with_the_curated_family_list():
    from pathlib import Path

    from headstart.trends import role_taxonomy

    repo = Path(__file__).resolve().parent.parent
    head = rfc.Head(repo / "config" / "role_family_classifier")
    head.check_families(
        role_taxonomy.load_families(repo / "config" / "role_families.json")
    )
