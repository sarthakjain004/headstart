"""CAS and content checks with a local fake HF writer; these tests perform no remote writes."""

import hashlib
import runpy
import shutil
from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import requests
from huggingface_hub.errors import EntryNotFoundError, HfHubHTTPError
from test_trends_restated_history import METHODOLOGY
from test_trends_restated_history import candidate as _candidate
from test_trends_restated_history import packaged as _packaged

from headstart.boards.board_operator import company_operator
from headstart.ingest import restate_publish
from headstart.ingest.restate_baseline import committed_baseline
from headstart.trends import restated_history as artifact
from headstart.trends import trend_history

candidate = _candidate
packaged = _packaged


def remote(entry):
    return SimpleNamespace(
        rfilename=entry["path"], size=entry["size"], lfs={"sha256": entry["sha256"]}
    )


def _prepare_sources(candidate: Path, root: Path) -> None:
    for name, destination in [
        ("company_directory.json", root / "data/state/company_directory.json"),
        *((name, root / name) for name in artifact.CONFIG_FILES),
    ]:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(candidate / name, destination)


def test_prepare_augments_tick_only_metadata_atomically(
    candidate, tmp_path, monkeypatch
):
    root = tmp_path / "source"
    _prepare_sources(candidate, root)
    for name in ("company_directory.json", *artifact.CONFIG_FILES):
        (candidate / name).unlink()
    placements = candidate / "placements"
    placements.mkdir()
    (placements / "private.parquet").write_bytes(b"private per-id placements")
    metadata_path = candidate / "replay.json"
    initial = artifact.read_json(metadata_path)
    initial["files"] = [
        e for e in initial["files"] if e["path"].startswith("role_trend_board_deltas/")
    ]
    initial["quality"] = {"supported_metrics": ["stock"]}
    metadata_path.write_bytes(artifact.encoded(initial))
    replaced = []
    original_replace = Path.replace

    def replace(source, destination):
        if destination == metadata_path:
            assert source == candidate / "replay.json.tmp"
            assert artifact.read_json(metadata_path) == initial
            replaced.append(artifact.read_json(source))
        return original_replace(source, destination)

    monkeypatch.setattr(Path, "replace", replace)
    restate_publish.prepare(candidate, root)
    prepared = artifact.read_json(metadata_path)
    expected = [
        artifact.file_entry(path, path.relative_to(candidate).as_posix())
        for path in sorted(candidate.rglob("*"))
        if path.is_file() and artifact.allowed(path.relative_to(candidate).as_posix())
    ]
    assert prepared["files"] == expected
    assert replaced == [prepared]
    assert {k: v for k, v in prepared.items() if k not in ("files", "quality")} == {
        k: v for k, v in initial.items() if k not in ("files", "quality")
    }
    assert prepared["quality"] == initial["quality"] | {
        "company_labels": {"history_boards": 1, "named_boards": 1, "unnamed_boards": 0}
    }
    assert {e["path"] for e in prepared["files"]} == {
        *(e["path"] for e in initial["files"]),
        "company_directory.json",
        *artifact.CONFIG_FILES,
    }
    assert not (candidate / "replay.json.tmp").exists()


def test_prepare_failed_replace_retains_original_metadata(
    candidate, tmp_path, monkeypatch
):
    root = tmp_path / "source"
    _prepare_sources(candidate, root)
    metadata_path = candidate / "replay.json"
    before = metadata_path.read_bytes()

    def fail_replace(*args):
        raise OSError("fixture interrupted before rename")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="interrupted"):
        restate_publish.prepare(candidate, root)
    assert metadata_path.read_bytes() == before
    assert not (candidate / "replay.json.tmp").exists()


def test_package_requires_prepared_metadata_file_identity(candidate, tmp_path):
    metadata_path = candidate / "replay.json"
    metadata = artifact.read_json(metadata_path)
    metadata["files"][0]["sha256"] = "0" * 64
    metadata_path.write_bytes(artifact.encoded(metadata))
    with pytest.raises(ValueError, match="prepared replay file inventory"):
        restate_publish.package(candidate, tmp_path / "out")


def test_prepare_trims_company_groups_to_generation_boards(candidate, tmp_path):
    root = tmp_path / "source"
    _prepare_sources(candidate, root)
    labels = root / "data/state/company_directory.json"
    original = {
        "companies": [
            {
                "name": "Acme",
                "operator": "services",
                "boards": ["lever:outside", "greenhouse:acme"],
            },
            {"name": "Later Company", "operator": "employer", "boards": ["ashby:new"]},
        ]
    }
    labels.write_bytes(artifact.encoded(original))
    restate_publish.prepare(candidate, root)
    assert artifact.read_json(candidate / "company_directory.json") == {
        "companies": [
            {
                "name": "Acme",
                "operator": company_operator(["greenhouse:acme"], "Acme"),
                "boards": ["greenhouse:acme"],
            }
        ]
    }
    assert artifact.read_json(labels) == original
    metadata = artifact.read_json(candidate / "replay.json")
    entry = next(e for e in metadata["files"] if e["path"] == "company_directory.json")
    assert entry == artifact.file_entry(candidate / entry["path"], entry["path"])


def test_prepare_rejects_duplicate_company_board_membership(candidate, tmp_path):
    root = tmp_path / "source"
    _prepare_sources(candidate, root)
    labels = root / "data/state/company_directory.json"
    labels.write_bytes(
        artifact.encoded(
            {
                "companies": [
                    {"name": "Acme", "boards": ["greenhouse:acme"]},
                    {"name": "Different company", "boards": ["greenhouse:acme"]},
                ]
            }
        )
    )
    before = (candidate / "replay.json").read_bytes()
    with pytest.raises(ValueError, match="Company directory"):
        restate_publish.prepare(candidate, root)
    assert (candidate / "replay.json").read_bytes() == before


def test_prepare_rebuilds_generation_names_and_keeps_opaque_totals(candidate, tmp_path):
    last = "2026-10-03T00:00:00+00:00"
    trend_history.record_tick(
        candidate,
        last,
        {
            ("greenhouse:acme", "stock", "software-engineering", "mid"): 7,
            (
                "lever:new-humanizable-company",
                "stock",
                "software-engineering",
                "mid",
            ): 2,
            ("breezy:1001", "stock", "software-engineering", "mid"): 3,
        },
        {},
        METHODOLOGY,
    )
    metadata_path = candidate / "replay.json"
    metadata = artifact.read_json(metadata_path)
    metadata["last_covered_tick"] = last
    metadata["quality"] = {"input_counts": {"title_only": 1}}
    metadata_path.write_bytes(artifact.encoded(metadata))
    root = tmp_path / "source"
    _prepare_sources(candidate, root)
    tick_hashes = {
        p.name: artifact.sha256(p)
        for p in (candidate / trend_history.DELTAS).glob("*.parquet")
    }
    source_hash = artifact.sha256(root / "data/state/company_directory.json")
    restate_publish.prepare(candidate, root)
    entries = artifact.read_json(candidate / "company_directory.json")["companies"]
    assert {b for entry in entries for b in entry["boards"]} == {
        "greenhouse:acme",
        "lever:new-humanizable-company",
    }
    assert (
        next(
            e["name"]
            for e in entries
            if e["boards"] == ["lever:new-humanizable-company"]
        )
        == "NEW Humanizable Company"
    )
    prepared = artifact.read_json(metadata_path)
    assert prepared["quality"] == {
        "input_counts": {"title_only": 1},
        "company_labels": {"history_boards": 3, "named_boards": 2, "unnamed_boards": 1},
    }
    assert {
        p.name: artifact.sha256(p)
        for p in (candidate / trend_history.DELTAS).glob("*.parquet")
    } == tick_hashes
    assert artifact.sha256(root / "data/state/company_directory.json") == source_hash
    loaded = trend_history.TrendHistory.load(candidate, candidate / "config")
    assert loaded.unnetted_answer(trend_history.TrendQuestion())["totals"][-1] == 12


def test_prepare_all_opaque_generation_has_empty_named_directory(candidate, tmp_path):
    for path in (candidate / trend_history.DELTAS).glob("*.parquet"):
        table = pq.read_table(path)
        table = table.set_column(
            table.schema.get_field_index("board"),
            "board",
            pa.array(["breezy:1001"] * len(table)),
        )
        pq.write_table(table, path)
    root = tmp_path / "source"
    _prepare_sources(candidate, root)
    hashes = {
        p.name: artifact.sha256(p)
        for p in (candidate / trend_history.DELTAS).glob("*.parquet")
    }
    restate_publish.prepare(candidate, root)
    assert artifact.read_json(candidate / "company_directory.json") == {"companies": []}
    assert artifact.read_json(candidate / "replay.json")["quality"][
        "company_labels"
    ] == {"history_boards": 1, "named_boards": 0, "unnamed_boards": 1}
    assert {
        p.name: artifact.sha256(p)
        for p in (candidate / trend_history.DELTAS).glob("*.parquet")
    } == hashes


@pytest.mark.parametrize("opaque", [False, True])
def test_prepared_coverage_matches_oracle_header_and_serving(
    candidate, tmp_path, opaque
):
    """Synthetic report fixture checks the contract, not production replay correctness."""
    if opaque:
        for path in (candidate / trend_history.DELTAS).glob("*.parquet"):
            table = pq.read_table(path)
            table = table.set_column(
                table.schema.get_field_index("board"),
                "board",
                pa.array(["breezy:1001"] * len(table)),
            )
            pq.write_table(table, path)
    root = tmp_path / "source"
    _prepare_sources(candidate, root)
    facts = root / "data/facts"
    source = facts / "job_facts/fixture.parquet"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"contract fixture only")
    metadata_path = candidate / "replay.json"
    metadata = artifact.read_json(metadata_path)
    metadata["inputs"] = [
        artifact.file_entry(source, "data/facts/job_facts/fixture.parquet")
    ]
    metadata_path.write_bytes(artifact.encoded(metadata))
    restate_publish.prepare(candidate, root)
    metadata = artifact.read_json(metadata_path)
    oracle = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "scripts/eval/verify_restatement.py")
    )
    labels = oracle["publication_bindings"](
        metadata, facts, root / "data/state", candidate
    )
    assert metadata["quality"]["company_labels"] == labels
    report_path = candidate / "validation.json"
    report = artifact.read_json(report_path)
    report.update(
        {
            "files": metadata["files"],
            "inputs": metadata["inputs"],
            "quality": {"supported_metrics": ["stock"], "company_labels": labels},
        }
    )
    report_path.write_bytes(artifact.encoded(report))
    publication = tmp_path / "publication"
    directory, pointer = restate_publish.package(candidate, publication)
    assert (
        artifact.validate_generation(directory, pointer)["quality"]["company_labels"]
        == labels
    )
    legacy = trend_history.TrendHistory()
    selected = artifact.load(publication, Path("config"), legacy)
    assert selected.restated is not None
    answer = selected.answer(selected.restated, trend_history.TrendQuestion())
    assert answer["totals"][-1] == 7
    assert answer["history"]["quality"]["company_labels"]["unnamed_boards"] == int(
        opaque
    )


@pytest.fixture
def pinned_fetch(tmp_path, monkeypatch):
    import huggingface_hub

    from headstart.ingest import state_fetch

    def parquet(metadata):
        sink = pa.BufferOutputStream()
        pq.write_table(
            pa.table({"id": ["fixture"]}).replace_schema_metadata(metadata), sink
        )
        return sink.getvalue().to_pybytes()

    payloads = {
        "data/state/reference_state.parquet": parquet({b"ts": b"2026-10-02"}),
        "data/facts/trend_reference/baseline.parquet": parquet(
            {b"ts": b"2026-10-01", b"previous_tick": b"", b"baseline": b"true"}
        ),
        "data/facts/trend_reference/next.parquet": parquet(
            {
                b"ts": b"2026-10-02",
                b"previous_tick": b"2026-10-01",
                b"baseline": b"false",
            }
        ),
        "data/facts/job_facts/one.parquet": b"raw fixture facts",
        "data/descriptions/greenhouse/one.parquet": b"pinned fixture text",
        "data/lancedb/jobs.lance/data/one.lance": b"unused live vectors",
    }
    calls = []
    root = tmp_path / "runner"
    root.mkdir()

    def info(*args, **kwargs):
        return SimpleNamespace(
            sha="f" * 40,
            siblings=[
                SimpleNamespace(
                    rfilename=name,
                    size=len(body),
                    lfs={"sha256": hashlib.sha256(body).hexdigest()},
                )
                for name, body in payloads.items()
            ],
        )

    def download(url, path, size, headers):
        name = path.relative_to(root).as_posix()
        calls.append((name, url))
        path.write_bytes(payloads[name])

    monkeypatch.setattr(
        huggingface_hub, "HfApi", lambda **kwargs: SimpleNamespace(repo_info=info)
    )
    monkeypatch.setattr(huggingface_hub, "get_token", lambda: None)
    monkeypatch.setattr(
        huggingface_hub,
        "hf_hub_url",
        lambda repo, name, **kwargs: f"{kwargs['revision']}:{name}",
    )
    monkeypatch.setattr(state_fetch, "_fetch_whole", download)
    monkeypatch.setattr(state_fetch, "_fetch_ranged", download)
    monkeypatch.delenv("GITHUB_ENV", raising=False)
    return root, payloads, calls, parquet


def test_fetch_omits_lance_and_validates_committed_baseline(pinned_fetch):
    root, payloads, calls, _ = pinned_fetch
    result = restate_publish.fetch("fixture", root)
    expected = set(payloads) - {"data/lancedb/jobs.lance/data/one.lance"}
    assert {name for name, _ in calls} == expected
    assert {entry["path"] for entry in result["inputs"]} == expected
    assert all(url.startswith(result["input_revision"] + ":") for _, url in calls)
    assert (
        committed_baseline(root / "data/facts", root / "data/state")
        == root / "data/facts/trend_reference/baseline.parquet"
    )


def test_fetch_requested_revision_records_resolved_sha(pinned_fetch, monkeypatch):
    import huggingface_hub

    root, _, calls, _ = pinned_fetch
    original_api = huggingface_hub.HfApi
    requested = []

    def api(**kwargs):
        original = original_api(**kwargs)

        def info(*args, **options):
            requested.append(options["revision"])
            return original.repo_info(*args, **options)

        return SimpleNamespace(repo_info=info)

    monkeypatch.setattr(huggingface_hub, "HfApi", api)
    result = restate_publish.fetch("fixture", root, revision="selected-ref")
    assert requested == ["selected-ref"]
    assert result["input_revision"] == "f" * 40
    assert (
        artifact.read_json(root / "data/restated-inputs.json")["input_revision"]
        == "f" * 40
    )
    assert all(url.startswith("f" * 40 + ":") for _, url in calls)


@pytest.mark.parametrize("failure", ["missing", "broken"])
def test_fetch_missing_or_broken_baseline_fails_before_remaining_inputs(
    pinned_fetch, failure
):
    root, payloads, calls, parquet = pinned_fetch
    if failure == "missing":
        del payloads["data/state/reference_state.parquet"]
    else:
        payloads["data/state/reference_state.parquet"] = parquet(
            {b"ts": b"missing-parent"}
        )
    with pytest.raises(ValueError, match="baseline"):
        restate_publish.fetch("fixture", root)
    assert not any(
        name.startswith(
            ("data/descriptions/", "data/lancedb/", "data/facts/job_facts/")
        )
        for name, _ in calls
    )
    assert not (root / "data/restated-inputs.json").exists()


def test_immutable_inputs_allow_new_pipeline_content(packaged):
    _, directory, _ = packaged
    inputs = artifact.read_json(directory / "manifest.json")["inputs"]
    changed_state = {
        "path": "data/state/reference_state.parquet",
        "size": 99,
        "sha256": "0" * 64,
    }
    restate_publish.check_inputs(
        [*inputs, changed_state],
        [
            remote(inputs[0]),
            remote({**inputs[0], "path": "data/facts/job_facts/new.parquet"}),
        ],
    )
    for siblings in ([], [remote({**inputs[0], "sha256": "0" * 64})]):
        with pytest.raises(ValueError, match="input"):
            restate_publish.check_inputs(inputs, siblings)


@pytest.mark.parametrize("directory", ["description_facts", "description_archive"])
def test_selected_native_description_inputs_cannot_change(directory):
    entry = {
        "path": f"data/facts/{directory}/lever/content.parquet",
        "size": 2,
        "sha256": "a" * 64,
    }
    restate_publish.check_inputs([entry], [remote(entry)])
    with pytest.raises(ValueError, match="hash changed"):
        restate_publish.check_inputs([entry], [remote(entry | {"sha256": "b" * 64})])


def test_git_blob_inputs():
    entry = {
        "path": "data/facts/reference_rules/small.zip",
        "size": 2,
        "sha256": "a" * 64,
        "git_blob": "git",
    }
    restate_publish.check_inputs(
        [entry],
        [SimpleNamespace(rfilename=entry["path"], size=2, lfs=None, blob_id="git")],
    )
    with pytest.raises(ValueError, match="hash"):
        restate_publish.check_inputs(
            [entry],
            [
                SimpleNamespace(
                    rfilename=entry["path"], size=2, lfs=None, blob_id="changed"
                )
            ],
        )


def test_newer_manifest_never_replaced(packaged):
    _, _, pointer = packaged
    with pytest.raises(ValueError, match="newer covered"):
        restate_publish.check_newer(
            {**pointer, "generation": "other", "last_covered_tick": "2027"}, pointer
        )
    with pytest.raises(ValueError, match="rules revision"):
        restate_publish.check_newer(
            {**pointer, "generation": "other", "rules_code_sha": "new"},
            pointer,
            ancestor=lambda *a: False,
        )


def test_atomic_cas_retries_unrelated_pipeline_commit(packaged):
    root, directory, pointer = packaged
    inputs = artifact.read_json(directory / "manifest.json")["inputs"]
    heads, writes = [], []

    def info(*a, **k):
        head = f"head-{len(heads)}"
        heads.append(head)
        return SimpleNamespace(sha=head, siblings=[remote(inputs[0])])

    def download(*a, **k):
        raise EntryNotFoundError("absent")

    def commit(**kwargs):
        writes.append(kwargs)
        if len(writes) == 1:
            response = requests.Response()
            response.status_code = 409
            raise HfHubHTTPError("CAS conflict", response=response)
        return SimpleNamespace(oid="committed")

    api = SimpleNamespace(repo_info=info, create_commit=commit)
    assert (
        restate_publish.publish("repo", root, pointer, api=api, download=download)
        == "committed"
    )
    assert [w["parent_commit"] for w in writes] == ["head-0", "head-1"]
    paths = [op.path_in_repo for op in writes[-1]["operations"]]
    assert artifact.CURRENT in paths
    assert any(p.endswith("/manifest.json") for p in paths)
    assert any(p.endswith("/validation.json") for p in paths)
    assert all(
        type(op).__name__ == "CommitOperationAdd" for op in writes[-1]["operations"]
    )


def test_race_new_generation_blocks_retry(packaged, tmp_path):
    root, directory, pointer = packaged
    inputs = artifact.read_json(directory / "manifest.json")["inputs"]
    newer = tmp_path / "newer.json"
    newer.write_bytes(
        artifact.encoded(
            {**pointer, "generation": "other", "last_covered_tick": "2027"}
        )
    )
    writes = []

    def download(*a, **k):
        if not writes:
            raise EntryNotFoundError("absent")
        return str(newer)

    def commit(**kwargs):
        writes.append(kwargs)
        response = requests.Response()
        response.status_code = 409
        raise HfHubHTTPError("conflict", response=response)

    api = SimpleNamespace(
        repo_info=lambda *a, **k: SimpleNamespace(
            sha="head", siblings=[remote(inputs[0])]
        ),
        create_commit=commit,
    )
    with pytest.raises(ValueError, match="newer covered"):
        restate_publish.publish("repo", root, pointer, api=api, download=download)
    assert len(writes) == 1


def test_workflow_is_current_rules_serialized_release_gated():
    path = (
        Path(__file__).resolve().parents[1]
        / ".github/workflows/publish-restated-trends.yml"
    )
    source = path.read_text()
    assert "cancel-in-progress: false" in source
    assert "ref: main" in source
    assert (
        "headstart.ingest.restate_run --out data/restated --encode-budget-seconds 1800"
        in source
    )
    assert "HEADSTART_RESTATE_CODE_SHA=$(git rev-parse HEAD)" in source
    assert "${{ github.sha }}" not in source
    assert "verify_restatement.py" in source
    assert "RESTATED_TRENDS_PUBLICATION_ENABLED == 'true'" in source
    assert "compare_restated_trends.py" not in source
    assert "restart_space" not in source
    assert (
        "schedule:" in source and "workflow_dispatch:" in source and "push:" in source
    )
