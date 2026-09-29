"""deploy-space.yml fires for exactly the modules the Space loads (ADR-0290).

Every deploy boots a new container for about six minutes, so a change the Space cannot see must
not deploy it. The Space imports about fifty `headstart` modules at load, none of them from
`scrapers`, `network` or `ingest`; the trigger used to be all of `src/headstart/**`.

The test loads `deploy/hf-space/app.py` in a fresh interpreter, with the model, the index and
the Hub download stubbed as `tests/test_space_app.py` stubs them, and reads `sys.modules`. A
fresh interpreter, because this process has already imported whatever other test files import.
Each loaded module's file must match the trigger, and no other module under `src/headstart/`
may. The Space also reads `src/headstart/ui/` and `config/` as files, which `sys.modules` cannot
see, so those two are checked by path.
"""

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


def _trigger_patterns() -> list[str]:
    """The `on.push.paths` entries, in order. Read without a YAML parser, as
    `test_space_deploy_sync.py` reads this file."""
    text = WORKFLOW.read_text(encoding="utf-8")
    block = re.search(r"^    paths:\n((?:      (?:- .*|#.*)\n)+)", text, re.MULTILINE)
    assert block, "no multi-line `paths:` list under on.push in deploy-space.yml"
    return re.findall(r'^      - "([^"]+)"', block.group(1), re.MULTILINE)


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
        f"deploy-space.yml's paths match {idle}, which the Space never loads, so a change to "
        "them restarts the Space for nothing: narrow `paths` or add a `!` pattern"
    )


def test_the_files_the_space_reads_trigger_a_deploy():
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
        f"deploy-space.yml's paths leave out {missed}, which the Space reads"
    )
