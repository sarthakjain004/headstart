"""deploy-space.yml fires for exactly the modules the Space loads (ADR-0290).

Every deploy boots a new container for about six minutes, so a change the Space cannot see must
not deploy it. The Space imports about fifty `headstart` modules at load, none of them from
`scrapers`, `network` or `ingest`; the trigger used to be all of `src/headstart/**`.

The test loads `deploy/hf-space/app.py` in a fresh interpreter, with the model, the index and
the Hub download stubbed as `tests/test_space_app.py` stubs them, and reads `sys.modules`. A
fresh interpreter, because this process has already imported whatever other test files import.
Each loaded module's file must match the trigger, and no other module under `src/headstart/`
may. `sys.modules` after the load cannot see an import made inside a function, which runs only
when a request calls it, so every such import in a loaded module must name a module the load
already holds, or one listed as never reached by a request. The Space also reads files from
`src/headstart/ui/` and `config/`, which `sys.modules` cannot see either: both are listed whole
and checked by path.
"""

import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("flask")  # in [dev]; the app cannot load without it

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github" / "workflows" / "deploy-space.yml"
SRC = REPO / "src" / "headstart"

# Load the app with its three heavy dependencies faked, then print every headstart module's file.
_LOAD_APP = r"""
import importlib.util, json, sys, tempfile, types

def fake(name, **attrs):
    module = types.ModuleType(name)
    module.__dict__.update(attrs)
    return module

class Table:
    schema = types.SimpleNamespace(names=["ats", "title", "first_seen"])
    def search(self, *a, **k):
        return self
    select = where = limit = offset = order_by = metric = search
    def to_list(self):
        return []
    def count_rows(self, *a, **k):
        return 0

class Vector:
    def astype(self, _dtype):
        return self

class Model:
    def encode(self, *a, **k):
        return [Vector()]

state = tempfile.mkdtemp()
sys.modules["lancedb"] = fake(
    "lancedb", connect=lambda *a, **k: types.SimpleNamespace(open_table=lambda *a, **k: Table())
)
sys.modules["sentence_transformers"] = fake(
    "sentence_transformers", SentenceTransformer=lambda *a, **k: Model()
)
sys.modules["huggingface_hub"] = fake("huggingface_hub", snapshot_download=lambda *a, **k: state)
spec = importlib.util.spec_from_file_location("space_app", sys.argv[1])
spec.loader.exec_module(importlib.util.module_from_spec(spec))
print(json.dumps(sorted(
    m.__file__ for name, m in list(sys.modules.items())
    if (name == "headstart" or name.startswith("headstart.")) and getattr(m, "__file__", None)
)))
"""


# Modules a loaded module imports inside a function that no request calls, each with the reason.
_NEVER_IMPORTED_BY_A_REQUEST = {
    # By `boards.board_identity.board_key` and `board_key_of`. The Space calls only `ats_of`,
    # `lower_key` and `tenant` from that module (ADR-0290).
    "src/headstart/scrapers/registry.py",
}


def _patterns_in(text: str) -> list[str]:
    """The `on.push.paths` entries of a deploy-space.yml, in order. Read without a YAML parser,
    as `test_space_deploy_sync.py` reads this file, so an entry must be in double quotes: one
    written otherwise would be skipped, and every check below would pass without seeing it."""
    block = re.search(
        r"^    paths:\n((?:(?:      (?:- .*|#.*))?\n)+)", text, re.MULTILINE
    )
    assert block, "no multi-line `paths:` list under on.push in deploy-space.yml"
    entries = re.findall(r"^      - (.*)$", block.group(1), re.MULTILINE)
    unquoted = [entry for entry in entries if not re.fullmatch(r'"[^"]+"', entry)]
    assert not unquoted, f"write each `paths` entry in double quotes: {unquoted}"
    return [entry[1:-1] for entry in entries]


def _trigger_patterns() -> list[str]:
    return _patterns_in(WORKFLOW.read_text(encoding="utf-8"))


def _glob_regex(pattern: str) -> re.Pattern:
    """GitHub's filter glob: `**` crosses `/`, `*` does not."""
    parts = re.split(r"(\*\*|\*)", pattern)
    body = "".join(
        ".*" if p == "**" else "[^/]*" if p == "*" else re.escape(p) for p in parts
    )
    return re.compile(body + r"\Z")


def _triggers(path: str, patterns: list[str]) -> bool:
    """Whether a push touching ``path`` runs the workflow: the last matching pattern decides,
    and a `!` pattern excludes."""
    decision = False
    for pattern in patterns:
        negated = pattern.startswith("!")
        if _glob_regex(pattern.lstrip("!")).match(path):
            decision = not negated
    return decision


def _module_file(name: str) -> str | None:
    """The repo-relative file of module ``name`` under `src/`, or None when it is not one."""
    path = REPO / "src" / Path(*name.split("."))
    for candidate in (path / "__init__.py", path.with_suffix(".py")):
        if candidate.is_file():
            return candidate.relative_to(REPO).as_posix()
    return None


def _imports_inside_functions(path: Path) -> set[str]:
    """Files of the `src/` modules that ``path`` imports inside a function body."""
    package = (
        list(path.relative_to(REPO / "src").parent.parts) if SRC in path.parents else []
    )
    found = set()
    for function in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for node in ast.walk(function):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    parent = package[: len(package) - node.level + 1]
                    base = ".".join([*parent, *([base] if base else [])])
                names = [base, *(f"{base}.{alias.name}" for alias in node.names)]
            else:
                continue
            found.update(file for name in names if (file := _module_file(name)))
    return found


@pytest.fixture(scope="module")
def loaded_files() -> set[str]:
    """Repo-relative files of every headstart module the Space loads at boot."""
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            [str(REPO / "src"), os.environ.get("PYTHONPATH", "")]
        ),
    }
    for key in ("SECRET_KEY", "GOOGLE_CLIENT_ID"):
        env.pop(key, None)
    done = subprocess.run(
        [sys.executable, "-c", _LOAD_APP, str(REPO / "deploy" / "hf-space" / "app.py")],
        check=False,
        capture_output=True,
        text=True,
        cwd=REPO,
        env=env,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    files = json.loads(done.stdout.strip().splitlines()[-1])
    return {Path(f).resolve().relative_to(REPO).as_posix() for f in files}


def test_the_glob_reading_matches_githubs():
    patterns = [
        "src/headstart/serving/**",
        "src/headstart/*.py",
        "!src/headstart/__main__.py",
    ]
    assert _triggers("src/headstart/serving/job_search.py", patterns)
    assert _triggers("src/headstart/log.py", patterns)
    assert not _triggers("src/headstart/__main__.py", patterns)
    assert not _triggers("src/headstart/scrapers/lever.py", patterns)


def test_an_unquoted_path_entry_is_refused_not_skipped():
    text = '    paths:\n      - "config/**"\n\n      - src/headstart/scrapers/**\n  x: {}\n'
    with pytest.raises(AssertionError, match="double quotes"):
        _patterns_in(text)


def test_an_import_inside_a_function_is_seen(tmp_path):
    source = tmp_path / "late.py"
    source.write_text(
        "import headstart.log\n\n\ndef f():\n    from headstart.scrapers import registry\n"
    )
    assert _imports_inside_functions(source) == {
        "src/headstart/scrapers/__init__.py",
        "src/headstart/scrapers/registry.py",
    }


def test_the_app_loads_the_real_package(loaded_files):
    assert "src/headstart/serving/job_search.py" in loaded_files
    assert "src/headstart/space_mcp/server.py" in loaded_files


def test_every_module_the_space_loads_triggers_a_deploy(loaded_files):
    patterns = _trigger_patterns()
    missed = sorted(f for f in loaded_files if not _triggers(f, patterns))
    assert not missed, (
        f"the Space loads {missed} but deploy-space.yml's paths leave them out, so a change "
        "to them would reach the Space only with some later deploy: add them to `paths`"
    )


def test_every_module_a_request_could_import_is_loaded_at_boot(loaded_files):
    sources = [
        REPO / "deploy" / "hf-space" / "app.py",
        *(REPO / f for f in loaded_files),
    ]
    imported = {
        (source.relative_to(REPO).as_posix(), target)
        for source in sources
        for target in _imports_inside_functions(source)
    }
    late = sorted(
        f"{source} imports {target}"
        for source, target in imported
        if target not in loaded_files and target not in _NEVER_IMPORTED_BY_A_REQUEST
    )
    assert not late, (
        f"{late}: an import inside a function runs when a request calls it, after the load "
        "this test reads, so a change to that module would not deploy the Space. Import it "
        "at module level, or add it to _NEVER_IMPORTED_BY_A_REQUEST with the reason no "
        "request calls that function"
    )
    stale = _NEVER_IMPORTED_BY_A_REQUEST - {target for _, target in imported}
    assert not stale, f"no loaded module imports {stale} any more: drop it"


def test_no_module_the_space_never_loads_triggers_a_deploy(loaded_files):
    patterns = _trigger_patterns()
    idle = sorted(
        path
        for f in SRC.rglob("*.py")
        if "__pycache__" not in f.parts
        and (path := f.relative_to(REPO).as_posix()) not in loaded_files
        and _triggers(path, patterns)
    )
    assert not idle, (
        f"deploy-space.yml's paths match {idle}, which the Space does not load at boot, and "
        "the test above checks that no request imports them later. A change to them restarts "
        "the Space for nothing: narrow `paths`, or add a `!` pattern after the positive ones"
    )


def test_the_files_the_space_reads_from_trigger_a_deploy():
    """Every file under ui/, config/ and deploy/hf-space/. The Space reads only some of
    config/'s files, and which ones no load can see, so all of it deploys (ADR-0290)."""
    patterns = _trigger_patterns()
    read = [
        p
        for top in (SRC / "ui", REPO / "config", REPO / "deploy" / "hf-space")
        for p in top.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    ]
    assert read, "src/headstart/ui/, config/ and deploy/hf-space/ are empty"
    missed = sorted(
        path
        for p in read
        if not _triggers(path := p.relative_to(REPO).as_posix(), patterns)
    )
    assert not missed, (
        f"deploy-space.yml's paths leave out {missed}, which the Space reads files from"
    )


def test_a_branch_dispatch_cannot_cancel_mains_deploy():
    """The group cancels an earlier deploy because a newer checkout holds its changes, and only
    a newer checkout of the same ref does."""
    text = WORKFLOW.read_text(encoding="utf-8")
    group = re.search(r"^concurrency:\n  group: (.+)$", text, re.MULTILINE)
    assert group and "${{ github.ref }}" in group.group(1)
