"""Frozen helper installation must survive older production planner snapshots."""

import ast
import importlib.util
import inspect
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from headstart.ingest import index_plan, role_family_classifier

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location(
    "frozen_runner", ROOT / "scripts/eval/run_frozen_restatement.py"
)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def load(path):
    spec = importlib.util.spec_from_file_location("frozen_planner_probe", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_nonempty_rank_probe_catches_transitive_helper_missing_in_older_snapshot(
    tmp_path,
):
    directory = tmp_path / "src/headstart/ingest"
    directory.mkdir(parents=True)
    tree = ast.parse(Path(index_plan.__file__).read_text())
    tree.body = [
        node
        for node in tree.body
        if not isinstance(node, ast.FunctionDef)
        or node.name not in {"_survivor_key", "duplicate_ranks"}
    ]
    planner = directory / "index_plan.py"
    old = ast.unparse(tree)
    planner.write_text(old + "\n" + inspect.getsource(index_plan.duplicate_ranks))
    before = load(planner)
    with pytest.raises(NameError, match="_survivor_key"):
        before.duplicate_ranks(["greenhouse:probe:1"], {"greenhouse:probe"})
    planner.write_text(old)
    (directory / "board_dormancy.py").write_text(
        "class PostedDates:\n    _newest = {}\n"
    )
    (directory / "role_family_classifier.py").write_text(
        Path(role_family_classifier.__file__).read_text()
    )
    runner.install_adapters(ROOT, tmp_path)
    after = load(planner)
    assert after.duplicate_ranks(["greenhouse:probe:1"], {"greenhouse:probe"})
    # Frozen decisions already present in the snapshot are retained.
    assert inspect.getsource(after._placement).strip() in ast.unparse(tree)


def _legacy_classifier(path):
    tree = ast.parse(Path(role_family_classifier.__file__).read_text())
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Head":
            node.body = [
                n for n in node.body if getattr(n, "name", None) != "inputs_fingerprint"
            ]
            init = next(n for n in node.body if getattr(n, "name", None) == "__init__")
            init.body = [
                n
                for n in init.body
                if not (
                    isinstance(n, ast.AnnAssign)
                    and isinstance(n.target, ast.Attribute)
                    and n.target.attr == "row_vector_revision"
                )
            ]
        if isinstance(node, ast.ClassDef) and node.name == "Cache":
            node.body = [
                n
                for n in node.body
                if not (
                    isinstance(n, ast.AnnAssign)
                    and isinstance(n.target, ast.Name)
                    and n.target.id == "inputs_fingerprint"
                )
            ]
    legacy_io = ast.parse("""
def load_cache(path, version):
    return Cache(version, {})
def save_cache(path, cache):
    pass
""").body
    tree.body = [
        n
        for n in tree.body
        if getattr(n, "name", None) not in {"load_cache", "save_cache"}
    ] + legacy_io
    path.write_text(ast.unparse(tree))


def test_classifier_adapter_changes_only_metadata_and_cache_helpers(tmp_path):
    import numpy as np

    path = tmp_path / "role_family_classifier.py"
    _legacy_classifier(path)
    old = load(path)
    old_decision = inspect.getsource(old.decide_rows_scored)
    old_head_decision = inspect.getsource(old.Head.decide)
    old_math = inspect.getsource(old.Head.row_logits)
    with pytest.raises(TypeError):
        old.load_cache(tmp_path / "absent.parquet", 1, "fingerprint")
    runner.install_classifier_adapters(path)
    adapted = load(path)
    head = adapted.Head(ROOT / "config/role_family_classifier")
    assert head.row_vector_revision is None
    assert (
        head.inputs_fingerprint
        == role_family_classifier.Head(
            ROOT / "config/role_family_classifier"
        ).inputs_fingerprint
    )
    assert inspect.getsource(adapted.decide_rows_scored) == old_decision
    assert inspect.getsource(adapted.Head.decide) == old_head_decision
    assert inspect.getsource(adapted.Head.row_logits) == old_math
    values = np.zeros((1, head.row_vector_dim), np.float32)
    np.testing.assert_array_equal(
        head.row_logits(values),
        old.Head(ROOT / "config/role_family_classifier").row_logits(values),
    )
    cache = adapted.Cache(
        head.version,
        {"probe": np.zeros(len(head.families), np.float32)},
        head.inputs_fingerprint,
    )
    cache_path = tmp_path / "cache.parquet"
    adapted.save_cache(cache_path, cache, inputs_fingerprint=head.inputs_fingerprint)
    assert adapted.load_cache(
        cache_path, head.version, head.inputs_fingerprint
    ).title_logits.keys() == {"probe"}
    assert not adapted.load_cache(cache_path, head.version, "different").title_logits
    with pytest.raises(ValueError, match="cannot retag"):
        adapted.save_cache(cache_path, cache, inputs_fingerprint="different")
    before = path.read_bytes()
    runner.install_classifier_adapters(path)
    assert path.read_bytes() == before


def test_metadata_fingerprint_uses_frozen_math_and_archived_revision(tmp_path):
    path = tmp_path / "role_family_classifier.py"
    _legacy_classifier(path)
    source = path.read_text().replace(
        "return row_vectors @ self._row_weights.T + self._bias",
        "return row_vectors @ self._row_weights.T + self._bias + 1",
    )
    path.write_text(source)
    directory = tmp_path / "head"
    directory.mkdir()
    shutil.copy2(ROOT / "config/role_family_classifier/head.npz", directory)
    manifest = json.loads(
        (ROOT / "config/role_family_classifier/manifest.json").read_text()
    )
    manifest["row_vector"]["revision"] = "archived-revision"
    (directory / "manifest.json").write_text(json.dumps(manifest))
    runner.install_classifier_adapters(path)
    adapted = load(path).Head(directory)
    current = role_family_classifier.Head(directory)
    assert adapted.row_vector_revision == "archived-revision"
    assert adapted.inputs_fingerprint != current.inputs_fingerprint
    assert "self._bias + 1" in inspect.getsource(adapted.row_logits)


def test_already_compatible_classifier_is_untouched(tmp_path):
    path = tmp_path / "role_family_classifier.py"
    source = Path(role_family_classifier.__file__).read_bytes()
    path.write_bytes(source)
    runner.install_classifier_adapters(path)
    assert path.read_bytes() == source


def test_actual_archived_rule_preflight(tmp_path):
    archives = list(
        (
            ROOT
            / "experiment/restate-memory-2026-10-02/artifacts/frozen-rule-inputs/data/facts/reference_rules"
        ).glob("*.zip")
    )
    if not archives:
        pytest.skip("local captured rule archive is unavailable")
    with zipfile.ZipFile(archives[0]) as archive:
        archive.extractall(tmp_path)
    path = tmp_path / "src/headstart/ingest/role_family_classifier.py"
    original_probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "from pathlib import Path; from headstart.ingest.role_family_classifier import Head; print(Head(Path('config/role_family_classifier')).inputs_fingerprint)",
        ],
        cwd=tmp_path,
        env=os.environ | {"PYTHONPATH": str(tmp_path / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert original_probe.returncode != 0
    assert "has no attribute 'inputs_fingerprint'" in original_probe.stderr
    before = ast.parse(path.read_text())
    runner.install_adapters(ROOT, tmp_path)
    after = ast.parse(path.read_text())
    # Original frozen nodes are retained byte-for-AST; additions cannot replace decisions.
    assert [ast.dump(n) for n in after.body[: len(before.body)]] == [
        ast.dump(n) for n in before.body
    ]
    runner.preflight_adapters(tmp_path)
