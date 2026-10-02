"""Frozen helper installation must survive older production planner snapshots."""

import ast
import importlib.util
import inspect
import sys
from pathlib import Path

import pytest

from headstart.ingest import index_plan

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
    runner.install_adapters(ROOT, tmp_path)
    after = load(planner)
    assert after.duplicate_ranks(["greenhouse:probe:1"], {"greenhouse:probe"})
    # Frozen decisions already present in the snapshot are retained.
    assert inspect.getsource(after._placement).strip() in ast.unparse(tree)
