"""Independent source-oracle fixtures; candidate output never supplies expected truth."""

import gzip
import importlib.util
import json
import zipfile
from collections import Counter
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from headstart.ingest import index_plan
from headstart.ingest import role_family_classifier as rfc
from headstart.trends.trend_history import DELTAS

spec = importlib.util.spec_from_file_location(
    "verify_restatement",
    Path(__file__).parents[1] / "scripts/eval/verify_restatement.py",
)
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)

FIRST = "2026-10-01T00:00:00+00:00"
SECOND = "2026-10-02T00:00:00+00:00"
FP = "frozen-rules"
REVISION = "pinned-hf-revision"
BOARD = "greenhouse:acme"
FAMILY = "software-engineering"
PLACEMENT = (BOARD, FAMILY, "mid")


@pytest.fixture
def policy(tmp_path):
    head = tmp_path / "head"
    head.mkdir()
    np.savez(
        head / "head.npz",
        title_weights=np.array([[1, 0], [-1, 0]], np.float32),
        row_weights=np.zeros((2, 2), np.float32),
        bias=np.zeros(2, np.float32),
    )
    (head / "manifest.json").write_text(
        json.dumps(
            {
                "version": 1,
                "model": "fixture",
                "model_revision": "fixture",
                "row_vector": {"model": "fixture", "dim": 2},
                "families": [FAMILY, "non-tech"],
                "cutoff": 0.6,
            }
        )
    )
    result = verifier.Policy.__new__(verifier.Policy)
    result.head = rfc.Head(head)
    result.cache = rfc.Cache(1, {"backend engineer": np.array([10, -10], np.float32)})
    result.input_fingerprint = "matching-inputs"
    result.keep = {BOARD}
    result.live = index_plan.boards_by_canon(result.keep)
    result.site_jobs = {}
    result.backing = {}
    result.winners = set()
    result.watchlist = []
    return result


def source(job_id=BOARD + ":1", **changes):
    return {
        "id": job_id,
        "kind": "present",
        "title": "Backend Engineer",
        "department": "Engineering",
        "requisition": None,
        "description": "We are looking for a software engineer to build reliable software services. Three years of experience is required.",
        "experience": "3 years",
        "employment_type": None,
        "min_years": 7,
        "first_seen": FIRST,
        "vector": [0.0, 0.0],
        "title_logits": [10.0, -10.0],
        "row_logits": [0.0, 0.0],
        "reference_family": "wrong-saved-label",
        "reference_board": "greenhouse:wrong",
        "reference_band": "staff",
        **changes,
    }


def write(path, rows, metadata, schema=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(rows, schema=schema).replace_schema_metadata(metadata)
    pq.write_table(table, path)
    return {
        "path": str(path),
        "sha256": verifier.sha256(path),
        "size": path.stat().st_size,
    }


def reference(path, rows, stamp=FIRST, previous=""):
    return write(
        path,
        rows,
        {
            b"ts": stamp.encode(),
            b"previous_tick": previous.encode(),
            b"baseline": str(not previous).lower().encode(),
            b"methodology": b'{"classifier_input_fingerprint":"matching-inputs"}',
        },
    )


def candidate(root, rows, stamp=FIRST, deltas=None, bound=True):
    metadata = {b"ts": stamp.encode()}
    if bound:
        metadata |= {
            b"rules_fingerprint": FP.encode(),
            b"input_revision": REVISION.encode(),
        }
    write(
        root / "placements" / f"{stamp[:10]}.parquet",
        rows,
        metadata,
        pa.schema([(c, pa.string()) for c in ("id", "board", "family", "band")]),
    )
    if deltas is None:
        deltas = [
            {"board": BOARD, "metric": m, "family": FAMILY, "band": "mid", "delta": 1}
            for m in ("stock", "new", "recounted_in")
        ]
    write(
        root / DELTAS / f"{stamp[:10]}.parquet",
        deltas,
        metadata,
        pa.schema(
            [
                (c, pa.int64() if c == "delta" else pa.string())
                for c in ("board", "metric", "family", "band", "delta")
            ]
        ),
    )


def universe(tmp_path, rows, stamp=FIRST):
    facts = write(
        tmp_path / "job_facts/listed.parquet",
        [
            {
                "id": r["id"],
                "kind": "listed",
                "title": r["title"],
                "department": r["department"],
            }
            for r in rows
        ],
        {b"stamp": stamp.encode()},
    )
    reads = write(
        tmp_path / "board_reads/read.parquet",
        [{"board": BOARD, "in_scope": True, "outcome": "authoritative"}],
        {b"stamp": stamp.encode()},
    )
    historical = write(
        tmp_path / "historical_job_inputs/source.parquet",
        rows,
        {
            b"stamp": stamp.encode(),
            b"source_kind": b"historical-job-inputs",
            b"methodology": b'{"classifier_input_fingerprint":"matching-inputs"}',
        },
    )
    return write(
        tmp_path / "oracle" / f"{stamp[:10]}.parquet",
        rows,
        {
            b"source_kind": b"raw-facts-served-inputs",
            b"complete": b"true",
            b"ts": stamp.encode(),
            b"input_revision": REVISION.encode(),
            b"rules_fingerprint": FP.encode(),
            b"source_files": json.dumps([facts, reads, historical]).encode(),
            b"methodology": b'{"classifier_input_fingerprint":"matching-inputs"}',
        },
    )


def inputs(ref, raw=None, tick=FIRST, reference_tick=FIRST):
    entry = {"tick": tick, "reference_tick": reference_tick, "reference": ref}
    if raw:
        entry["universe"] = raw
    return {"rules_fingerprint": FP, "input_revision": REVISION, "ticks": [entry]}


def placement(job_id=BOARD + ":1", where=PLACEMENT):
    return dict(zip(("id", "board", "family", "band"), (job_id, *where), strict=True))


def test_oracle_ignores_saved_labels_and_derives_current_band(policy):
    expected, quality, _ = policy.transform({BOARD + ":1": source()}, "matching-inputs")
    assert expected == {BOARD + ":1": PLACEMENT}
    assert quality == {"captured_float32_logits": 1}


def test_changed_head_never_reuses_old_logits(policy):
    row = source(title_logits=[-10.0, 10.0], row_logits=[-10.0, 10.0])
    expected, quality, _ = policy.transform({row["id"]: row}, "different-inputs")
    assert expected == {row["id"]: PLACEMENT}
    assert quality["float16_classifier_approximation"] == 1


@pytest.mark.parametrize(
    "experience, band, preserved", [(None, "senior", True), ("3 years", "mid", False)]
)
def test_missing_text_keeps_observed_years_only_without_complete_raw_field(
    policy, experience, band, preserved
):
    row = source(description=None, experience=experience)
    expected, quality, _ = policy.transform({row["id"]: row}, "matching-inputs")
    assert expected[row["id"]][2] == band
    assert quality["observed_english_without_text"] == 1
    assert bool(quality["observed_min_years_without_text"]) == preserved


def test_raw_row_missing_text_requires_observed_english(policy):
    row = source(kind="listed", description=None)
    with pytest.raises(ValueError, match="English evidence"):
        policy.transform({row["id"]: row}, "matching-inputs")


def test_current_tech_gate_can_remove_observed_source(policy):
    row = source(title="Retail Cashier", department="Retail")
    expected, _, reasons = policy.transform({row["id"]: row}, "matching-inputs")
    assert expected == {}
    assert reasons["tech_rejected"] == 1


def test_production_planners_keep_incumbent_and_allow_better_class(policy):
    external, campus, hidden = (
        "workday:acme/external",
        "workday:acme/campus",
        "workday:acme/hidden",
    )
    policy.keep = {external, campus, hidden}
    policy.live = index_plan.boards_by_canon(policy.keep)
    policy.site_jobs = {external: 900, campus: 40, hidden: 1000}
    campus_row = source(campus + ":R123")
    external_row = source(external + ":R123")
    expected, _, _ = policy.transform({campus_row["id"]: campus_row}, "matching-inputs")
    assert set(expected) == {campus_row["id"]}
    expected, _, _ = policy.transform(
        {r["id"]: r for r in (campus_row, external_row)}, "matching-inputs"
    )
    assert set(expected) == {campus_row["id"]}
    policy.winners = {hidden + ":R123"}
    hidden_row = source(hidden + ":R123")
    expected, _, _ = policy.transform(
        {r["id"]: r for r in (hidden_row, external_row)}, "matching-inputs"
    )
    assert set(expected) == {external_row["id"]}


def test_future_requisition_folds_then_releases_own_changed_copy(policy):
    front, backing = (
        "eightfold:jobs.acme.com",
        "taleo_enterprise:https://acme.taleo.net/careersection/external",
    )
    policy.keep = {front, backing}
    policy.live = index_plan.boards_by_canon(policy.keep)
    policy.backing = {"jobs.acme.com": (backing,)}
    f, b = source(front + ":123", requisition="R123"), source(backing + ":42")
    expected, _, _ = policy.transform({f["id"]: f, b["id"]: b}, "matching-inputs")
    assert len(expected) == 2
    b = b | {"requisition": "R123"}
    expected, _, _ = policy.transform({f["id"]: f, b["id"]: b}, "matching-inputs")
    assert set(expected) == {b["id"]}
    f = f | {"requisition": "R999"}
    expected, _, _ = policy.transform({f["id"]: f, b["id"]: b}, "matching-inputs")
    assert len(expected) == 2


def test_full_raw_oracle_addition_is_coverage_not_mismatch(tmp_path, policy):
    new_id = "lever:newcompany:2"
    policy.keep.add("lever:newcompany")
    policy.live = index_plan.boards_by_canon(policy.keep)
    rows = [source(), source(new_id)]
    ref = reference(tmp_path / "reference.parquet", rows[:1])
    raw = universe(tmp_path, rows)
    root = tmp_path / "candidate"
    candidate(
        root,
        [placement(), placement(new_id, ("lever:newcompany", FAMILY, "mid"))],
        deltas=[
            {"board": b, "metric": m, "family": FAMILY, "band": "mid", "delta": 1}
            for b in (BOARD, "lever:newcompany")
            for m in ("stock", "new", "recounted_in")
        ],
    )
    report = verifier.verify(inputs(ref, raw), tmp_path, root, policy)
    assert report["complete"] is True and report["pass"] is True
    assert report["ticks"][0]["expected_coverage_additions"] == 1
    assert report["quality"]["supported_metrics"] == [
        "stock",
        "new",
        "turnover",
        "watched_roles",
    ]


def test_reference_only_is_never_publication_complete(tmp_path, policy):
    ref = reference(tmp_path / "reference.parquet", [source()])
    root = tmp_path / "candidate"
    candidate(root, [placement()])
    report = verifier.verify(inputs(ref), tmp_path, root, policy)
    assert report["checks"]["per_id"] is True
    assert report["complete"] is False and report["pass"] is False
    assert report["quality"]["supported_metrics"] == []


def test_balanced_counts_cannot_hide_wrong_per_id_family(tmp_path, policy):
    ref = reference(tmp_path / "reference.parquet", [source()])
    raw = universe(tmp_path, [source()])
    root = tmp_path / "candidate"
    wrong = (BOARD, "qa-test", "mid")
    candidate(
        root,
        [placement(where=wrong)],
        deltas=[
            {
                "board": BOARD,
                "metric": m,
                "family": "qa-test",
                "band": "mid",
                "delta": 1,
            }
            for m in ("stock", "recounted_in")
        ],
    )
    report = verifier.verify(inputs(ref, raw), tmp_path, root, policy)
    assert report["checks"]["arithmetic"] is True
    assert report["checks"]["per_id"] is False
    assert report["pass"] is False


@pytest.mark.parametrize(
    "error, deltas",
    [
        ("negative_level", [("stock", -1), ("closed", 1)]),
        ("negative_turnover", [("stock", 1), ("closed", -1)]),
        ("turnover_identity", [("stock", 1)]),
        ("new_exceeds_stock", [("stock", 1), ("new", 2), ("recounted_in", 1)]),
    ],
)
def test_arithmetic_rejects_each_invalid_semantic(error, deltas):
    table = pa.Table.from_pylist(
        [
            {"board": BOARD, "metric": m, "family": FAMILY, "band": "mid", "delta": n}
            for m, n in deltas
        ]
    )
    assert error in verifier.check_tick(table, Counter(), {BOARD + ":1": PLACEMENT})


def test_unstamped_candidate_cannot_bind_claimed_revision(tmp_path, policy):
    ref = reference(tmp_path / "reference.parquet", [source()])
    raw = universe(tmp_path, [source()])
    root = tmp_path / "candidate"
    candidate(root, [placement()], bound=False)
    report = verifier.verify(inputs(ref, raw), tmp_path, root, policy)
    assert report["checks"]["policy_binding"] is False
    assert report["complete"] is False


def test_missing_empty_tick_is_not_silently_skipped(tmp_path, policy):
    ref = reference(tmp_path / "reference.parquet", [source()])
    root = tmp_path / "candidate"
    candidate(root, [placement()])
    candidate(root, [], stamp=SECOND, deltas=[])
    report = verifier.verify(inputs(ref), tmp_path, root, policy)
    assert report["checks"]["tick_coverage"] is False
    assert report["coverage"]["extra_ticks"] == [SECOND]


def test_narrow_cached_reference_cannot_certify_changed_policy(tmp_path, policy):
    row = {
        k: source()[k]
        for k in ("id", "kind", "reference_board", "reference_family", "reference_band")
    }
    ref = reference(tmp_path / "reference.parquet", [row])
    root = tmp_path / "candidate"
    candidate(root, [placement()])
    with pytest.raises(ValueError, match="narrow reference"):
        verifier.verify(inputs(ref), tmp_path, root, policy)


@pytest.mark.parametrize("mutation", ["hash", "parent", "self_compare"])
def test_independent_evidence_binding_rejects_invalid_source(
    tmp_path, policy, mutation
):
    root = tmp_path / "candidate"
    candidate(root, [placement()])
    ref = reference(
        (root if mutation == "self_compare" else tmp_path) / "reference.parquet",
        [source()],
        previous=SECOND if mutation == "parent" else "",
    )
    if mutation == "hash":
        ref["sha256"] = "wrong"
    with pytest.raises(ValueError):
        verifier.verify(inputs(ref), tmp_path, root, policy)


def test_inventory_traversal_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="unsafe"):
        verifier.inventory_path(
            {"path": "data/facts/../../../candidate.parquet"}, tmp_path, tmp_path
        )


def test_same_reference_run_id_maps_exactly_not_nearest_timestamp(tmp_path):
    facts, state = tmp_path / "facts", tmp_path / "state"
    baseline = reference(facts / "trend_reference/first.parquet", [source()])
    later = reference(
        facts / "trend_reference/second.parquet", [source()], SECOND, FIRST
    )
    table = pq.read_table(later["path"])
    pq.write_table(
        table.replace_schema_metadata(
            table.schema.metadata | {b"run_id": b"123", b"run_attempt": b"1"}
        ),
        later["path"],
    )
    reads = write(
        facts / "board_reads/read.parquet",
        [{"board": BOARD}],
        {
            b"run_id": b"123",
            b"run_attempt": b"1",
            b"stamp": b"2026-10-01T23:30:00+00:00",
        },
    )
    selected = []
    for item in (baseline, later, reads):
        p = Path(item["path"])
        selected.append(
            {
                "path": "data/facts/" + str(p.relative_to(facts)),
                "sha256": verifier.sha256(p),
                "size": p.stat().st_size,
            }
        )
    checkpoint = write(
        state / "reference_state.parquet",
        [{"id": BOARD + ":1"}],
        {b"ts": SECOND.encode()},
    )
    selected.append(checkpoint | {"path": "data/state/reference_state.parquet"})
    result = verifier.verification_inputs(
        {"inputs": selected, "rules_fingerprint": FP, "input_revision": REVISION},
        facts,
        state,
    )
    assert result["ticks"][1]["tick"] == "2026-10-01T23:30:00+00:00"


def test_progress_report_never_claims_pass(tmp_path, policy):
    ref = reference(tmp_path / "reference.parquet", [source()])
    raw = universe(tmp_path, [source()])
    root = tmp_path / "candidate"
    candidate(root, [placement()])
    snapshots = []
    verifier.verify(
        inputs(ref, raw),
        tmp_path,
        root,
        policy,
        progress=lambda r: snapshots.append(json.loads(json.dumps(r))),
    )
    assert snapshots and all(
        r["complete"] is False and r["pass"] is False for r in snapshots
    )


@pytest.mark.parametrize(
    "tamper", ["invented_id", "omitted_id", "raw_field", "description"]
)
def test_raw_oracle_cannot_invent_or_hide_source_truth(tmp_path, policy, tamper):
    row = source()
    ref = reference(tmp_path / "reference.parquet", [row])
    raw = universe(tmp_path, [row])
    table = pq.read_table(raw["path"])
    altered = table.to_pylist()
    if tamper == "invented_id":
        altered.append(source(BOARD + ":unobserved"))
    elif tamper == "omitted_id":
        altered = []
    else:
        altered[0]["title" if tamper == "raw_field" else "description"] = (
            "Fabricated input"
        )
    pq.write_table(pa.Table.from_pylist(altered, schema=table.schema), raw["path"])
    raw["sha256"] = verifier.sha256(Path(raw["path"]))
    root = tmp_path / "candidate"
    candidate(root, [placement()])
    with pytest.raises(ValueError, match="raw universe|historical source input"):
        verifier.verify(inputs(ref, raw), tmp_path, root, policy)


def test_new_raw_source_without_original_text_math_evidence_stays_incomplete(
    tmp_path, policy
):
    new_id = BOARD + ":2"
    ref = reference(tmp_path / "reference.parquet", [source()])
    raw = universe(tmp_path, [source(), source(new_id)])
    table = pq.read_table(raw["path"])
    metadata = dict(table.schema.metadata)
    proofs = json.loads(metadata[b"source_files"])
    metadata[b"source_files"] = json.dumps(proofs[:2]).encode()
    pq.write_table(table.replace_schema_metadata(metadata), raw["path"])
    raw["sha256"] = verifier.sha256(Path(raw["path"]))
    root = tmp_path / "candidate"
    candidate(
        root,
        [placement(), placement(new_id)],
        deltas=[
            {"board": BOARD, "metric": m, "family": FAMILY, "band": "mid", "delta": 2}
            for m in ("stock", "recounted_in")
        ],
    )
    report = verifier.verify(inputs(ref, raw), tmp_path, root, policy)
    assert report["checks"]["source_inputs"] is False
    assert report["complete"] is False
    assert report["quality"]["input_counts"]["unverified_historical_source_inputs"] == 1


def test_raw_absence_uses_second_authoritative_read_not_next_global_tick(
    tmp_path, policy
):
    row = source()
    files = [
        write(
            tmp_path / "listed.parquet",
            [row | {"kind": "listed"}],
            {b"stamp": FIRST.encode()},
        ),
        write(
            tmp_path / "unlisted.parquet",
            [row | {"kind": "unlisted"}],
            {b"stamp": SECOND.encode()},
        ),
    ]
    proofs = [Path(f["path"]) for f in files]
    verifier.check_raw_truth(
        {row["id"]: row}, proofs, {row["id"]: row}, FIRST, SECOND, policy
    )
    third = "2026-10-03T00:00:00+00:00"
    no_read = write(
        tmp_path / "unrelated-read.parquet",
        [{"board": "lever:other", "in_scope": True, "outcome": "authoritative"}],
        {b"stamp": third.encode()},
    )
    proofs.append(Path(no_read["path"]))
    verifier.check_raw_truth(
        {row["id"]: row}, proofs, {row["id"]: row}, FIRST, third, policy
    )
    final = write(
        tmp_path / "confirm-read.parquet",
        [{"board": BOARD, "in_scope": True, "outcome": "authoritative"}],
        {b"stamp": third.encode()},
    )
    proofs.append(Path(final["path"]))
    verifier.check_raw_truth({}, proofs, {row["id"]: row}, FIRST, third, policy)


def test_known_removed_reference_id_is_not_excused_as_new_coverage(tmp_path, policy):
    first = reference(tmp_path / "first.parquet", [source()])
    second = reference(
        tmp_path / "second.parquet", [source() | {"kind": "removed"}], SECOND, FIRST
    )
    root = tmp_path / "candidate"
    candidate(root, [placement()])
    candidate(root, [placement()], SECOND, deltas=[])
    request = inputs(first)
    request["ticks"].append(
        inputs(second, tick=SECOND, reference_tick=SECOND)["ticks"][0]
    )
    report = verifier.verify(request, tmp_path, root, policy)
    assert report["ticks"][1]["placement_differences"] == 1
    assert report["checks"]["per_id"] is False


def package_fixture(tmp_path):
    facts, state, root = tmp_path / "facts", tmp_path / "state", tmp_path / "candidate"
    row = source()
    ref = reference(facts / "trend_reference/reference.parquet", [row])
    raw = universe(facts, [row])
    candidate(root, [placement()], bound=False)
    config = tmp_path / "rules/config"
    config.mkdir(parents=True)
    (root / "config").mkdir()
    for name in ("role_families.json", "role_watchlist.json"):
        (root / "config" / name).write_text("{}")
        (config / name).write_text("{}")
    (root / "company_directory.json").write_text(
        json.dumps({"companies": [{"name": "Acme", "boards": [BOARD]}]})
    )
    selected = []
    for path in facts.rglob("*.parquet"):
        selected.append(
            {
                "path": "data/facts/" + str(path.relative_to(facts)),
                "sha256": verifier.sha256(path),
                "size": path.stat().st_size,
            }
        )
    checkpoint = write(
        state / "reference_state.parquet",
        [{"id": row["id"], "digest": "observed"}],
        {b"ts": FIRST.encode()},
    )
    selected.append(checkpoint | {"path": "data/state/reference_state.parquet"})
    paths = {item["path"]: item for item in selected}
    ref_name = "data/facts/" + str(Path(ref["path"]).relative_to(facts))
    raw_name = "data/facts/" + str(Path(raw["path"]).relative_to(facts))
    files = [
        {
            "path": str(path.relative_to(root)),
            "sha256": verifier.sha256(path),
            "size": path.stat().st_size,
        }
        for path in (root / DELTAS).glob("*.parquet")
    ]
    for name in (
        "company_directory.json",
        "config/role_families.json",
        "config/role_watchlist.json",
    ):
        path = verifier.artifact_path(root, {"path": name})
        files.append(
            {"path": name, "sha256": verifier.sha256(path), "size": path.stat().st_size}
        )
    metadata = {
        "rules_fingerprint": FP,
        "input_revision": REVISION,
        "first_covered_tick": FIRST,
        "last_covered_tick": FIRST,
        "files": sorted(files, key=lambda item: item["path"]),
        "inputs": sorted(selected, key=lambda item: item["path"]),
        "verification_ticks": [
            {
                "tick": FIRST,
                "reference_tick": FIRST,
                "reference": paths[ref_name],
                "universe": paths[raw_name],
            }
        ],
    }
    replay = root / "replay.json"
    replay.write_text(json.dumps(metadata))
    return facts, state, root, replay, metadata


def test_approved_cli_binds_exact_publication_inventory(tmp_path, policy, monkeypatch):
    import sys

    facts, state, root, replay, metadata = package_fixture(tmp_path)
    report_path = root / "validation.json"
    monkeypatch.setattr(verifier, "Policy", lambda *_: policy)
    monkeypatch.setattr(verifier, "rules_fingerprint", lambda _: FP)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "verify",
            "--facts",
            str(facts),
            "--state",
            str(state),
            "--candidate",
            str(root),
            "--metadata",
            str(replay),
            "--report",
            str(report_path),
            "--rules-root",
            str(tmp_path / "rules"),
        ],
    )
    assert verifier.main() == 0
    report = json.loads(report_path.read_text())
    assert report["schema_version"] == 1
    assert report["pass"] is True and report["complete"] is True
    assert (
        report["files"] == metadata["files"] and report["inputs"] == metadata["inputs"]
    )
    assert report["first_covered_tick"] == report["last_covered_tick"] == FIRST
    assert report["quality"]["supported_metrics"] == [
        "stock",
        "new",
        "turnover",
        "watched_roles",
    ]
    # An interrupted/mutated rerun replaces an old passing report before checking.
    (root / "company_directory.json").write_text('{"companies":[]}')
    assert verifier.main() == 1
    assert json.loads(report_path.read_text())["pass"] is False
    report_path.write_text('{"complete":true,"pass":true}')
    replay.write_text("not valid JSON")
    assert verifier.main() == 1
    assert json.loads(report_path.read_text())["pass"] is False


def test_publication_rejects_company_labels_outside_history(tmp_path):
    facts, state, root, _, metadata = package_fixture(tmp_path)
    path = root / "company_directory.json"
    path.write_text(
        json.dumps({"companies": [{"name": "Invented", "boards": ["lever:unknown"]}]})
    )
    for item in metadata["files"]:
        if item["path"] == "company_directory.json":
            item.update({"sha256": verifier.sha256(path), "size": path.stat().st_size})
    with pytest.raises(ValueError, match="Company labels"):
        verifier.publication_bindings(metadata, facts, state, root)


def test_dormancy_truth_reads_non_tech_dates_and_waits_for_second_read(
    tmp_path, policy
):
    old = source(posted_at="2020-01-01")
    second = "2026-10-02T00:00:00+00:00"
    third = "2026-10-03T00:00:00+00:00"
    listing = write(
        tmp_path / "listed.parquet",
        [old | {"kind": "listed"}],
        {b"stamp": FIRST.encode()},
    )
    read = write(
        tmp_path / "first-read.parquet",
        [{"board": BOARD, "in_scope": True, "outcome": "authoritative"}],
        {b"stamp": second.encode()},
    )
    proofs = [Path(item["path"]) for item in (listing, read)]
    verifier.check_raw_truth(
        {old["id"]: old}, proofs, {old["id"]: old}, FIRST, second, policy
    )
    last = write(
        tmp_path / "second-read.parquet",
        [{"board": BOARD, "in_scope": True, "outcome": "authoritative"}],
        {b"stamp": third.encode()},
    )
    proofs.append(Path(last["path"]))
    verifier.check_raw_truth({}, proofs, {old["id"]: old}, FIRST, third, policy)
    fresh_non_tech = source(
        BOARD + ":retail",
        title="Cashier",
        department="Retail",
        posted_at="2026-10-03",
        first_seen=third,
    )
    revival = write(
        tmp_path / "fresh-non-tech.parquet",
        [fresh_non_tech | {"kind": "listed"}],
        {b"stamp": third.encode()},
    )
    proofs.append(Path(revival["path"]))
    verifier.check_raw_truth(
        {old["id"]: old, fresh_non_tech["id"]: fresh_non_tech},
        proofs,
        {old["id"]: old},
        FIRST,
        third,
        policy,
    )


def test_candidate_schema_is_checked_before_counting(tmp_path):
    root = tmp_path / "candidate"
    candidate(root, [placement()])
    path = next((root / DELTAS).glob("*.parquet"))
    table = pq.read_table(path)
    table = table.set_column(4, "delta", pa.array([1.0, 1.0, 1.0]))
    pq.write_table(table, path)
    with pytest.raises(ValueError, match="schema mismatch"):
        verifier.candidate_ticks(root, FP, REVISION)


def test_legacy_captured_math_reuse_requires_matching_archived_weights_and_normalise(
    tmp_path, policy
):
    head = tmp_path / "head"
    current = policy.head.inputs_fingerprint
    archive = tmp_path / "rules.zip"
    source_path = Path(rfc.__file__)

    def capture(source_text):
        with zipfile.ZipFile(archive, "w") as output:
            output.write(
                head / "manifest.json", "config/role_family_classifier/manifest.json"
            )
            output.write(head / "head.npz", "config/role_family_classifier/head.npz")
            output.writestr(
                "src/headstart/ingest/role_family_classifier.py", source_text
            )

    capture(source_path.read_text())
    request = {
        "pinned_inputs": {"data/facts/reference_rules/old.zip": {"path": str(archive)}}
    }
    assert (
        verifier.captured_fingerprint({"rules_fingerprint": "old"}, request) == current
    )
    capture(source_path.read_text().replace(".lower().split()", ".upper().split()"))
    assert (
        verifier.captured_fingerprint({"rules_fingerprint": "old"}, request) != current
    )


def test_source_proof_cannot_use_future_version_text(tmp_path):
    row = source()
    future = write(
        tmp_path / "future.parquet",
        [row | {"valid_from": SECOND}],
        {b"stamp": SECOND.encode(), b"source_kind": b"historical-job-inputs"},
    )
    assert (
        verifier.check_source_inputs(
            {row["id"]: row}, {}, [Path(future["path"])], FIRST, {}
        )
        == 1
    )


def test_default_cli_derives_new_coverage_from_raw_facts_without_universe(
    tmp_path, policy, monkeypatch
):
    import sys

    facts, state, root, replay, metadata = package_fixture(tmp_path)
    new = source(BOARD + ":2")
    extra = write(
        facts / "job_facts/extra.parquet",
        [new | {"kind": "listed"}],
        {b"stamp": FIRST.encode()},
    )
    history = write(
        facts / "historical_job_inputs/extra.parquet",
        [new],
        {
            b"stamp": FIRST.encode(),
            b"source_kind": b"historical-job-inputs",
            b"methodology": b'{"classifier_input_fingerprint":"matching-inputs"}',
        },
    )
    for item in (extra, history):
        item["path"] = "data/facts/" + str(Path(item["path"]).relative_to(facts))
        metadata["inputs"].append(item)
    metadata["inputs"].sort(key=lambda item: item["path"])
    metadata["verification_ticks"][0].pop("universe")
    candidate(
        root,
        [placement(), placement(new["id"])],
        bound=False,
        deltas=[
            {
                "board": BOARD,
                "metric": metric,
                "family": FAMILY,
                "band": "mid",
                "delta": 2,
            }
            for metric in ("stock", "new", "recounted_in")
        ],
    )
    for item in metadata["files"]:
        path = verifier.artifact_path(root, item)
        item.update({"sha256": verifier.sha256(path), "size": path.stat().st_size})
    replay.write_text(json.dumps(metadata))
    monkeypatch.setattr(verifier, "Policy", lambda *_: policy)
    monkeypatch.setattr(verifier, "rules_fingerprint", lambda _: FP)
    report_path = root / "validation.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "verify",
            "--facts",
            str(facts),
            "--state",
            str(state),
            "--candidate",
            str(root),
            "--metadata",
            str(replay),
            "--report",
            str(report_path),
            "--rules-root",
            str(tmp_path / "rules"),
        ],
    )
    assert verifier.main() == 0
    report = json.loads(report_path.read_text())
    assert report["complete"] is True and report["pass"] is True
    assert report["ticks"][0]["expected_coverage_additions"] == 1
    assert report["ticks"][0]["expected"] == 2


def test_baseline_incumbent_survives_higher_priority_same_class_raw_copy(
    tmp_path, policy
):
    main, sub = "workday:acme/external", "workday:acme/campus"
    policy.keep = {main, sub}
    policy.live = index_plan.boards_by_canon(policy.keep)
    policy.site_jobs = {main: 900, sub: 40}
    incumbent, other = source(sub + ":R123"), source(main + ":R123")
    ref = reference(tmp_path / "reference.parquet", [incumbent])
    raw = universe(tmp_path, [incumbent, other])
    root = tmp_path / "candidate"
    candidate(
        root,
        [placement(incumbent["id"], (sub, FAMILY, "mid"))],
        deltas=[
            {"board": sub, "metric": m, "family": FAMILY, "band": "mid", "delta": 1}
            for m in ("stock", "new", "recounted_in")
        ],
    )
    report = verifier.verify(inputs(ref, raw), tmp_path, root, policy)
    assert report["pass"] is True


def test_semantic_new_uses_original_ages_and_unknowns_not_baseline_reset(
    tmp_path, policy
):
    rows = [
        source(first_seen="2026-09-01T00:00:00+00:00"),
        source(BOARD + ":2", first_seen=FIRST),
        source(BOARD + ":3", first_seen=None),
    ]
    ref = reference(tmp_path / "reference.parquet", rows)
    raw = universe(tmp_path, rows)
    root = tmp_path / "candidate"
    candidate(
        root,
        [placement(row["id"]) for row in rows],
        deltas=[
            {"board": BOARD, "metric": m, "family": FAMILY, "band": "mid", "delta": n}
            for m, n in (("stock", 3), ("new", 1), ("recounted_in", 3))
        ],
    )
    report = verifier.verify(inputs(ref, raw), tmp_path, root, policy)
    assert report["pass"] is True
    candidate(
        root,
        [placement(row["id"]) for row in rows],
        deltas=[
            {"board": BOARD, "metric": m, "family": FAMILY, "band": "mid", "delta": 3}
            for m in ("stock", "new", "recounted_in")
        ],
    )
    report = verifier.verify(inputs(ref, raw), tmp_path, root, policy)
    assert report["checks"]["semantic_levels"] is False
    assert report["ticks"][0]["level_differences"]["count"] == 1


def test_baseline_opened_cannot_pass_even_when_identity_balances(tmp_path, policy):
    ref = reference(tmp_path / "reference.parquet", [source()])
    raw = universe(tmp_path, [source()])
    root = tmp_path / "candidate"
    candidate(
        root,
        [placement()],
        deltas=[
            {"board": BOARD, "metric": m, "family": FAMILY, "band": "mid", "delta": 1}
            for m in ("stock", "new", "opened")
        ],
    )
    report = verifier.verify(inputs(ref, raw), tmp_path, root, policy)
    assert report["checks"]["arithmetic"] is True
    assert report["checks"]["semantic_turnover"] is False


def test_current_watch_overlay_counts_only_tech_and_current_band(tmp_path, policy):
    from headstart.trends.role_taxonomy import WatchRole

    policy.watchlist = [WatchRole("backend", "Backend", FAMILY, [r"backend"])]
    row = source()
    ref = reference(tmp_path / "reference.parquet", [row])
    raw = universe(tmp_path, [row])
    root = tmp_path / "candidate"
    candidate(
        root,
        [placement()],
        deltas=[
            {"board": BOARD, "metric": m, "family": f, "band": "mid", "delta": 1}
            for f in (FAMILY, "watch:backend")
            for m in ("stock", "new", "recounted_in")
        ],
    )
    report = verifier.verify(inputs(ref, raw), tmp_path, root, policy)
    assert report["pass"] is True and report["quality"]["watched_roles"] is True
    row = source(
        description="이 직무는 소프트웨어 서비스를 설계하고 개발하는 역할입니다. 팀과 함께 안정적인 웹 서비스를 만들고 운영합니다. "
        * 12
    )
    ref = reference(tmp_path / "reference-korean.parquet", [row])
    raw = universe(tmp_path, [row])
    candidate(root, [], deltas=[])
    report = verifier.verify(inputs(ref, raw), tmp_path, root, policy)
    assert report["pass"] is True
    assert report["ticks"][0]["expected"] == 0
    assert report["ticks"][0]["attributions"]["english_rejected"] == 1


def test_independent_turnover_treats_suppressed_version_release_as_recount(policy):
    front, backing = (
        "eightfold:jobs.acme.com",
        "taleo_enterprise:https://acme.taleo.net/careersection/external",
    )
    policy.keep = {front, backing}
    policy.live = index_plan.boards_by_canon(policy.keep)
    policy.backing = {"jobs.acme.com": (backing,)}
    f, b = (
        source(front + ":123", requisition="R123"),
        source(backing + ":42", requisition="R123"),
    )
    old_source = {r["id"]: r for r in (f, b)}
    expected, _, _ = policy.transform(old_source, "matching-inputs")
    _, before = verifier.oracle_levels(expected, old_source, policy, FIRST)
    groups = {i: policy.groups[i] for i in policy.winners}
    f = f | {"requisition": "R999"}
    now_source = {r["id"]: r for r in (f, b)}
    expected, _, _ = policy.transform(now_source, "matching-inputs")
    _, now = verifier.oracle_levels(expected, now_source, policy, SECOND)
    moves = verifier.oracle_turnover(
        before,
        now,
        FIRST,
        groups,
        policy,
        set(old_source),
        {"first_reads": {front: FIRST, backing: FIRST}},
        SECOND,
    )
    assert moves[("recounted_in", front, FAMILY, "mid")] == 1
    assert not any(k[0] == "opened" for k in moves)


def test_new_age_boundary_uses_established_production_definition(policy):
    row = source(first_seen="2026-09-24T00:00:00+00:00")
    expected, _, _ = policy.transform({row["id"]: row}, "matching-inputs")
    levels, _ = verifier.oracle_levels(expected, {row["id"]: row}, policy, FIRST)
    assert levels[("new", BOARD, FAMILY, "mid")] == 1


def test_per_id_age_swap_cannot_hide_behind_matching_new_totals(tmp_path, policy):
    old, young = source(first_seen="2026-09-01T00:00:00+00:00"), source(BOARD + ":2")
    ref = reference(tmp_path / "reference.parquet", [old, young])
    raw = universe(tmp_path, [old, young])
    root = tmp_path / "candidate"
    candidate(
        root,
        [placement(old["id"]), placement(young["id"])],
        deltas=[
            {"board": BOARD, "metric": m, "family": FAMILY, "band": "mid", "delta": n}
            for m, n in (("stock", 2), ("new", 1), ("recounted_in", 2))
        ],
    )
    path = next((root / "placements").glob("*.parquet"))
    table = pq.read_table(path).append_column(
        "first_seen", pa.array([young["first_seen"], old["first_seen"]])
    )
    pq.write_table(table, path)
    report = verifier.verify(inputs(ref, raw), tmp_path, root, policy)
    assert report["checks"]["semantic_levels"] is True
    assert report["checks"]["per_id_age"] is False
    assert report["ticks"][0]["age_differences"]["count"] == 2


def test_independent_turnover_books_real_listing_and_confirmed_closure(policy):
    row = source(first_seen=None)
    source_rows = {row["id"]: row}
    expected, _, _ = policy.transform(source_rows, "matching-inputs")
    _, now = verifier.oracle_levels(expected, source_rows, policy, SECOND)
    moves = verifier.oracle_turnover(
        {},
        now,
        FIRST,
        {},
        policy,
        set(),
        {"first_reads": {BOARD: FIRST}, "listed_at": {row["id"]: SECOND}},
        SECOND,
    )
    assert moves[("opened", *PLACEMENT)] == 1
    groups = {i: policy.groups[i] for i in policy.winners}
    policy.transform({}, "matching-inputs")
    moves = verifier.oracle_turnover(
        now,
        {},
        SECOND,
        groups,
        policy,
        {row["id"]},
        {"removed": {row["id"]: ("2026-10-03T00:00:00+00:00", "unlisted")}},
        "2026-10-03T00:00:00+00:00",
    )
    assert moves[("closed", *PLACEMENT)] == 1


def test_native_math_fp_preserves_float32_parts_under_current_policy(policy):
    policy.input_fingerprint = policy.head.inputs_fingerprint
    row = source(row_logits=[0.0, 0.0])
    expected, quality, _ = policy.transform(
        {row["id"]: row}, policy.head.inputs_fingerprint
    )
    assert expected[row["id"]] == PLACEMENT
    assert quality["captured_float32_logits"] == 1
    assert quality["float16_classifier_approximation"] == 0


def test_observed_seniority_fallback_is_rederived_like_current_source_policy(policy):
    row = source(
        description=None,
        experience="Senior",
        experience_source="seniority",
        min_years=20,
    )
    expected, quality, _ = policy.transform({row["id"]: row}, "matching-inputs")
    derived = verifier.derived_meta.derive(row | {"ats": "greenhouse"})
    assert policy.years[row["id"]] == derived["min_years"]
    assert expected[row["id"]][2] == verifier.role_taxonomy.band(
        derived["min_years"], row["title"], None
    )
    assert quality["observed_min_years_without_text"] == 0


def test_native_fp_title_cache_loading_never_mutates_pinned_evidence(
    tmp_path, monkeypatch
):
    root = Path(__file__).parents[1]
    head = rfc.Head(root / "config/role_family_classifier")
    path = tmp_path / "title-cache.parquet"
    cache = rfc.Cache(
        head.version, {"backend engineer": np.ones(len(head.families), np.float32)}
    )
    rfc.save_cache(path, cache)
    before = path.read_bytes()
    monkeypatch.setattr(index_plan, "live_keep_set", lambda _: {BOARD})
    monkeypatch.setattr(index_plan, "workday_site_jobs", lambda _: {})
    monkeypatch.setattr(verifier.eightfold_backing, "load", lambda _: {})
    selected = verifier.Policy(root, path, tmp_path)
    assert selected.input_fingerprint == head.inputs_fingerprint
    assert selected.cache.title_logits
    assert path.read_bytes() == before
    rfc.save_cache(path, cache, inputs_fingerprint="mismatched-mathematics")
    before = path.read_bytes()
    selected = verifier.Policy(root, path, tmp_path)
    assert selected.cache.title_logits == {}
    assert path.read_bytes() == before


def pinned_native_inputs(facts, store):
    selected = {}
    for root, prefix in ((facts, "data/facts/"), (store, "data/descriptions/")):
        for path in root.rglob("*"):
            if path.is_file():
                selected[prefix + path.relative_to(root).as_posix()] = {
                    "path": str(path),
                    "sha256": verifier.sha256(path),
                }
    return {"facts_root": str(facts), "pinned_inputs": selected}


def write_current(store, rows):
    path = store / "greenhouse/base.jsonl.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt") as stream:
        for row in rows:
            stream.write(json.dumps(row) + "\n")
    return path


def test_native_same_run_and_attempt_resolves_superseded_original_text(
    tmp_path, monkeypatch
):
    facts, store = tmp_path / "facts", tmp_path / "descriptions"
    row = source()
    older = "Original software description requiring 3 years of experience."
    later = "Changed software description requiring 8 years of experience."
    monkeypatch.setenv("GITHUB_RUN_ID", "77")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    write(
        facts / "job_facts/first.parquet",
        [row | {"kind": "listed"}],
        {b"stamp": FIRST.encode(), b"run_id": b"77", b"run_attempt": b"1"},
    )
    verifier.description_facts.record(
        facts,
        "greenhouse",
        [{"id": row["id"], "description": older}],
        [],
        "2026-10-01T00:30:00+00:00",
    )
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    write(
        facts / "job_facts/second.parquet",
        [row | {"kind": "changed"}],
        {b"stamp": SECOND.encode(), b"run_id": b"77", b"run_attempt": b"2"},
    )
    verifier.description_facts.record(
        facts,
        "greenhouse",
        [{"id": row["id"], "description": later}],
        [{"id": row["id"], "description": older}],
        "2026-10-02T00:30:00+00:00",
    )
    write_current(store, [{"id": row["id"], "description": later}])
    inputs = pinned_native_inputs(facts, store)
    assert (
        verifier.native_description_inputs(inputs, FIRST, {row["id"]})[row["id"]][
            "description"
        ]
        == older
    )
    assert (
        verifier.native_description_inputs(inputs, SECOND, {row["id"]})[row["id"]][
            "description"
        ]
        == later
    )
    assert (
        verifier.native_description_inputs(
            inputs, "2026-09-30T23:59:59+00:00", {row["id"]}
        )
        == {}
    )


def test_native_descriptor_retains_previous_vector_math_not_future(
    tmp_path, monkeypatch, policy
):
    facts, store = tmp_path / "facts", tmp_path / "descriptions"
    monkeypatch.delenv("GITHUB_RUN_ID", raising=False)
    monkeypatch.delenv("GITHUB_RUN_ATTEMPT", raising=False)
    job_id = BOARD + ":1"
    text2 = "We build software services and require 3 years of experience."
    text3 = "We build software services and require 8 years of experience."
    third, fourth = "2026-10-03T00:00:00+00:00", "2026-10-04T00:00:00+00:00"
    verifier.description_facts.record(
        facts, "greenhouse", [{"id": job_id, "description": text2}], [], SECOND
    )
    verifier.description_facts.record(
        facts,
        "greenhouse",
        [{"id": job_id, "description": text3}],
        [{"id": job_id, "description": text2}],
        third,
    )
    write_current(store, [{"id": job_id, "description": text3}])
    first = source(
        vector=np.array([1, 1], np.float16),
        classifier_input_fingerprint="matching-inputs",
        _source_observed_at=FIRST,
    )
    future = source(vector=np.array([4, 4], np.float16), _source_observed_at=fourth)
    inputs = pinned_native_inputs(facts, store)
    for tick, text in ((SECOND, text2), (third, text3)):
        rows, unknown = verifier.materialize_raw_inputs(
            {job_id: source(experience=None)}, {job_id: first}, [], tick, inputs
        )
        assert not unknown
        assert np.array_equal(rows[job_id]["vector"], [1, 1])
        assert rows[job_id]["row_logits"] == first["row_logits"]
        assert rows[job_id]["description"] == text
        policy.transform(rows, "matching-inputs")
        assert policy.years[job_id] == (3 if tick == SECOND else 8)
    # At the later reference, older descriptors cannot overwrite newer observed
    # text/vector/math. Chronological caller supplies only the latest source.
    rows, unknown = verifier.materialize_raw_inputs(
        {job_id: source(experience=None)}, {job_id: future}, [], fourth, inputs
    )
    assert not unknown
    assert np.array_equal(rows[job_id]["vector"], [4, 4])
    assert rows[job_id]["description"] == future["description"]


def test_bootstrap_and_wrong_attempt_keep_actual_observation_time(
    tmp_path, monkeypatch
):
    facts, store = tmp_path / "facts", tmp_path / "descriptions"
    row = source()
    write(
        facts / "job_facts/first.parquet",
        [row],
        {b"stamp": FIRST.encode(), b"run_id": b"77", b"run_attempt": b"1"},
    )
    monkeypatch.setenv("GITHUB_RUN_ID", "77")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    write_current(store, [row])
    verifier.description_facts.seed_existing(
        facts, "greenhouse", {row["id"]: row["description"]}, SECOND
    )
    inputs = pinned_native_inputs(facts, store)
    assert verifier.native_description_inputs(inputs, FIRST, {row["id"]}) == {}
    assert (
        verifier.native_description_inputs(inputs, SECOND, {row["id"]})[row["id"]][
            "observed_at"
        ]
        == SECOND
    )


def test_producer_description_inventory_uses_exact_hashes_and_rejects_escape(tmp_path):
    facts, state, root, _, metadata = package_fixture(tmp_path)
    path = write_current(tmp_path / "descriptions", [source()])
    metadata["inputs"].append(
        {
            "path": "data/descriptions/greenhouse/base.jsonl.gz",
            "size": path.stat().st_size,
            "sha256": verifier.sha256(path),
        }
    )
    metadata["inputs"].sort(key=lambda item: item["path"])
    verifier.publication_bindings(metadata, facts, state, root)
    with gzip.open(path, "at") as stream:
        stream.write(json.dumps(source(BOARD + ":2")) + "\n")
    with pytest.raises(ValueError, match="inventory content mismatch"):
        verifier.publication_bindings(metadata, facts, state, root)
    with pytest.raises(ValueError, match="unsafe"):
        verifier.inventory_path(
            {"path": "data/descriptions/../../secret"}, facts, state
        )


def test_raw_fact_known_widened_role_recovers_without_reference_anchor(policy):
    row = source(
        title="Senior Software Engineer - Backend Services",
        experience=None,
    )
    policy.cache.title_logits[verifier.rfc.normalise(row["title"])] = np.array(
        [10, -10], np.float32
    )
    rows, unknown = verifier.materialize_raw_inputs({row["id"]: row}, {}, [], FIRST, {})
    assert not unknown
    expected, quality, _ = policy.transform(rows)
    assert expected[row["id"]][1] == FAMILY
    assert quality["title_only_without_vector"] == 1
    assert quality["title_only_language_judgement"] == 1


def test_changed_raw_title_discards_prior_title_logits_and_encodes_independently(
    policy, monkeypatch
):
    old = source(classifier_input_fingerprint="matching-inputs")
    raw = source(title="New Platform Software Engineer")
    rows, unknown = verifier.materialize_raw_inputs(
        {raw["id"]: raw}, {old["id"]: old}, [], FIRST, {}
    )
    assert not unknown and rows[raw["id"]]["title_logits"] is None
    assert np.array_equal(rows[raw["id"]]["vector"], old["vector"])
    calls = []

    def encode(titles, model, revision):
        calls.append((titles, model, revision))
        return np.array([[10, 0]], np.float32)

    monkeypatch.setattr(rfc, "encode", encode)
    expected, quality, _ = policy.transform(rows)
    assert expected[raw["id"]][1] == FAMILY
    assert quality["independently_encoded_titles"] == 1
    assert calls == [
        ([rfc.normalise(raw["title"])], policy.head.model, policy.head.model_revision)
    ]


def test_independent_title_budget_failure_is_not_unclassified_pass(policy):
    row = source(
        title="New Platform Software Engineer", classifier_input_fingerprint=None
    )
    policy.encode_budget_seconds = 0
    with pytest.raises(ValueError, match="title coverage incomplete"):
        policy.transform({row["id"]: row})


def test_committed_chain_ignores_orphan_reference_and_pending_raw_tail(tmp_path):
    facts, state, _, _, metadata = package_fixture(tmp_path)
    metadata.pop("verification_ticks")
    orphan = reference(
        facts / "trend_reference/orphan.parquet",
        [source(title="Future wrong title")],
        SECOND,
        FIRST,
    )
    tail = write(
        facts / "job_facts/tail.parquet",
        [source() | {"kind": "changed"}],
        {b"stamp": SECOND.encode(), b"run_id": b"uncommitted", b"run_attempt": b"1"},
    )
    for item in (orphan, tail):
        metadata["inputs"].append(
            item
            | {"path": "data/facts/" + Path(item["path"]).relative_to(facts).as_posix()}
        )
    selected = verifier.verification_inputs(metadata, facts, state)
    assert [entry["tick"] for entry in selected["ticks"]] == [FIRST]
    assert selected["unexported_raw_ticks"] == []


def test_committed_prefix_consumes_intermediate_facts_without_exporting_them(tmp_path):
    facts, state, _, _, metadata = package_fixture(tmp_path)
    metadata.pop("verification_ticks")
    third = "2026-10-03T00:00:00+00:00"
    final = reference(facts / "trend_reference/final.parquet", [source()], third, FIRST)
    table = pq.read_table(final["path"])
    pq.write_table(
        table.replace_schema_metadata(
            table.schema.metadata | {b"run_id": b"33", b"run_attempt": b"2"}
        ),
        final["path"],
    )
    items = [
        write(
            facts / "job_facts/intermediate.parquet",
            [source() | {"kind": "changed"}],
            {b"stamp": SECOND.encode()},
        ),
        write(
            facts / "job_facts/committed.parquet",
            [source() | {"kind": "changed"}],
            {b"stamp": third.encode(), b"run_id": b"33", b"run_attempt": b"2"},
        ),
    ]
    for item in [final, *items]:
        path = Path(item["path"])
        metadata["inputs"].append(
            {
                "path": "data/facts/" + path.relative_to(facts).as_posix(),
                "sha256": verifier.sha256(path),
                "size": path.stat().st_size,
            }
        )
    pq.write_table(
        pa.Table.from_pylist([{"id": BOARD + ":1"}]).replace_schema_metadata(
            {b"ts": third.encode()}
        ),
        state / "reference_state.parquet",
    )
    selected = verifier.verification_inputs(metadata, facts, state)
    assert [entry["tick"] for entry in selected["ticks"]] == [FIRST, third]
    assert selected["unexported_raw_ticks"] == [SECOND]


def test_dense_reference_updates_do_not_accumulate_source_history(tmp_path, policy):
    first = reference(tmp_path / "first.parquet", [source()])
    second = reference(
        tmp_path / "second.parquet",
        [
            source(
                description="We build software services and require three years of experience."
            )
        ],
        SECOND,
        FIRST,
    )
    root = tmp_path / "candidate"
    candidate(root, [placement()])
    candidate(root, [placement()], SECOND, deltas=[])
    request = inputs(first)
    request["ticks"].append(
        inputs(second, tick=SECOND, reference_tick=SECOND)["ticks"][0]
    )
    verifier.verify(request, tmp_path, root, policy)
    assert "reference_observations" not in request
