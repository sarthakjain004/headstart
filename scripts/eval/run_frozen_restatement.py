"""Reproduce a baseline window using its preserved rule code and Board ledgers.

The replay implementation comes from the reviewed branch; production rule modules,
configuration and ledgers come from the captured archive. Inputs and outputs remain
in the runner and never modify the live HF dataset.
"""

from __future__ import annotations

import ast
import inspect
import json
import os
import resource
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import zipfile
from pathlib import Path

import pyarrow.parquet as pq

from headstart.ingest import role_family_classifier
from headstart.ingest.board_dormancy import PostedDates
from headstart.ingest.index_plan import _survivor_key, duplicate_ranks
from headstart.ingest.restate_baseline import committed_baseline


def install_classifier_adapters(path: Path) -> None:
    """Append metadata/cache compatibility, never replace frozen classification math.

    The fingerprint helper introspects the frozen title/row logits and normalise,
    not today's implementations. Missing row revision stays unknown. Cache storage
    helpers gain today's fingerprint contract; decision/encoding helpers stay frozen.
    """
    original = path.read_text()
    marker = "# Frozen replay classifier metadata/cache compatibility."
    if marker in original:
        return
    tree = ast.parse(original)
    definitions = {
        n.name: n for n in tree.body if isinstance(n, (ast.ClassDef, ast.FunctionDef))
    }
    head = definitions["Head"]
    methods = {n.name: n for n in head.body if isinstance(n, ast.FunctionDef)}
    cache = definitions["Cache"]
    fingerprinted_cache = any(
        isinstance(n, ast.AnnAssign)
        and isinstance(n.target, ast.Name)
        and n.target.id == "inputs_fingerprint"
        for n in cache.body
    ) and all(
        "inputs_fingerprint"
        in {
            a.arg
            for a in definitions[name].args.args + definitions[name].args.kwonlyargs
        }
        for name in ("load_cache", "save_cache")
    )
    additions = []
    if not any(
        isinstance(n, ast.Attribute) and n.attr == "row_vector_revision"
        for n in ast.walk(methods["__init__"])
    ):
        additions.append(
            textwrap.dedent("""\
            _frozen_head_init = Head.__init__
            def _head_init_with_revision(self, directory):
                _frozen_head_init(self, directory)
                manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
                self.row_vector_revision = manifest["row_vector"].get("revision")
            Head.__init__ = _head_init_with_revision
        """)
        )
    if "inputs_fingerprint" not in methods:
        additions += [
            "import ast, hashlib, inspect, textwrap\n",
            textwrap.dedent(
                inspect.getsource(role_family_classifier.Head.inputs_fingerprint.fget)
            ),
            "Head.inputs_fingerprint = inputs_fingerprint\n",
        ]
    if not fingerprinted_cache:
        additions += [
            inspect.getsource(helper)
            for helper in (
                role_family_classifier.Cache,
                role_family_classifier.load_cache,
                role_family_classifier.save_cache,
            )
        ]
    if additions:
        path.write_text(original + "\n\n" + marker + "\n" + "\n\n".join(additions))


def install_adapters(root: Path, frozen: Path) -> None:
    """Expose replay helpers while leaving frozen production decisions intact."""
    for module in (root / "src/headstart/ingest").glob("restate_*.py"):
        shutil.copy2(module, frozen / "src/headstart/ingest" / module.name)
    # Immutable observation metadata/content lookup, not a production decision rule.
    shutil.copy2(
        root / "src/headstart/ingest/description_facts.py",
        frozen / "src/headstart/ingest/description_facts.py",
    )
    planner = frozen / "src/headstart/ingest/index_plan.py"
    original = planner.read_text()
    with planner.open("a") as stream:
        if "def _survivor_key(" not in original:
            stream.write("\n\n" + inspect.getsource(_survivor_key))
        if "def duplicate_ranks(" not in original:
            stream.write("\n\n" + inspect.getsource(duplicate_ranks))
    dormancy = frozen / "src/headstart/ingest/board_dormancy.py"
    if "    def newest(" not in dormancy.read_text():
        with dormancy.open("a") as stream:
            stream.write(
                "\n\n"
                + textwrap.dedent(inspect.getsource(PostedDates.newest))
                + "\nPostedDates.newest = newest\n"
            )
    install_classifier_adapters(
        frozen / "src/headstart/ingest/role_family_classifier.py"
    )


def preflight_adapters(frozen: Path) -> None:
    """Exercise actual frozen metadata/cache calls offline before a full replay."""
    probe = """\
        import json
        import tempfile
        from pathlib import Path
        import numpy as np
        from headstart.ingest.index_plan import duplicate_ranks
        from headstart.ingest.board_dormancy import PostedDates
        from headstart.ingest import role_family_classifier as classifier
        from headstart.ingest import restate_inputs, restate_place
        assert duplicate_ranks(['greenhouse:probe:1'], {'greenhouse:probe'})
        PostedDates().newest('greenhouse:probe')
        directory = Path('config/role_family_classifier')
        head = classifier.Head(directory)
        manifest = json.loads((directory / 'manifest.json').read_text())
        assert head.row_vector_revision == manifest['row_vector'].get('revision')
        fingerprint = head.inputs_fingerprint
        assert len(fingerprint) == 64
        row = head.row_logits(np.zeros((1, head.row_vector_dim), np.float32))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'cache.parquet'
            cache = classifier.load_cache(path, head.version, fingerprint)
            assert cache.inputs_fingerprint == fingerprint
            cache.title_logits['probe'] = np.zeros(len(head.families), np.float32)
            classifier.save_cache(path, cache, inputs_fingerprint=fingerprint)
            loaded = classifier.load_cache(path, head.version, fingerprint)
            np.testing.assert_array_equal(loaded.title_logits['probe'], cache.title_logits['probe'])
            assert not classifier.load_cache(path, head.version, 'mismatch').title_logits
            try:
                classifier.save_cache(path, loaded, inputs_fingerprint='mismatch')
            except ValueError:
                pass
            else:
                raise AssertionError('cache accepted different mathematical inputs')
            effective = classifier.Cache(loaded.version, dict(loaded.title_logits), loaded.inputs_fingerprint)
            assert len(classifier.decide_rows_scored(effective, head, ['probe'], row)) == 1
            # Imports inside load_description_sources must run even with no observations.
            class DescriptionProbe:
                def __init__(self):
                    self.rows = []
                def add_description(self, stamp, job_id, content_hash, text):
                    self.rows.append((stamp, job_id, content_hash, text))
            sources = DescriptionProbe()
            facts, store = Path(tmp) / 'facts', Path(tmp) / 'descriptions'
            restate_inputs.load_description_sources(sources, facts, store)
            assert sources.rows == []
            import gzip
            import pyarrow as pa
            import pyarrow.parquet as pq
            from headstart.ingest import description_facts
            stamp = '2026-10-03T01:00:00+00:00'
            observed = '2026-10-03T02:00:00+00:00'
            texts = {'greenhouse:probe:1': 'Current.', 'greenhouse:probe:2': 'Earlier.', 'greenhouse:probe:3': 'Unavailable.'}
            identities = [{'id': job_id, 'description_hash': description_facts.description_hash(text), 'observed_at': observed, 'run_id': 'preflight', 'run_attempt': '1', 'code_sha': 'fixture'} for job_id, text in texts.items()]
            for name in ('description_facts/greenhouse', 'description_archive/greenhouse', 'job_facts'):
                (facts / name).mkdir(parents=True)
            pq.write_table(pa.Table.from_pylist(identities), facts / 'description_facts/greenhouse/fixture.parquet')
            pq.write_table(pa.Table.from_pylist([{'id': 'greenhouse:probe:2', 'description_hash': identities[1]['description_hash'], 'description': 'Earlier.'}]), facts / 'description_archive/greenhouse/fixture.parquet')
            pq.write_table(pa.table({'id': ['probe']}, metadata={b'run_id': b'preflight', b'run_attempt': b'1', b'stamp': stamp.encode()}), facts / 'job_facts/fixture.parquet')
            (store / 'greenhouse').mkdir(parents=True)
            with gzip.open(store / 'greenhouse/0001.jsonl.gz', 'wt', encoding='utf-8') as stream:
                for job_id in texts:
                    stream.write(json.dumps({'id': job_id, 'description': 'Current.' if job_id.endswith(':1') else 'Future.'}) + '\\n')
            restate_inputs.load_description_sources(sources, facts, store)
            assert sources.rows == [(stamp, r['id'], r['description_hash'], text) for r, text in zip(identities, ['Current.', 'Earlier.', None], strict=True)]
    """
    subprocess.run(
        [sys.executable, "-c", textwrap.dedent(probe)],
        cwd=frozen,
        env=os.environ | {"PYTHONPATH": str(frozen / "src"), "HF_HUB_OFFLINE": "1"},
        check=True,
    )


def main():
    root = Path.cwd()
    facts = root / "data/facts"
    state = root / "data/state"
    baseline = committed_baseline(facts, state)
    if baseline is None:
        raise ValueError(
            "no committed baseline; cannot certify exact starting coverage"
        )
    metadata = pq.read_schema(baseline).metadata
    fingerprint = json.loads(metadata[b"methodology"])["rules_fingerprint"]
    diagnostics = root / "data/restate-diagnostics"
    diagnostics.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    archive = facts / "reference_rules" / f"{fingerprint}.zip"
    with tempfile.TemporaryDirectory(prefix="frozen-trends-rules-") as tmp:
        frozen = Path(tmp)
        with zipfile.ZipFile(archive) as z:
            for name in z.namelist():
                if Path(name).is_absolute() or ".." in Path(name).parts:
                    raise ValueError("unsafe rule archive member")
            z.extractall(frozen)
        install_adapters(root, frozen)
        env = os.environ | {"PYTHONPATH": str(frozen / "src")}
        preflight_adapters(frozen)
        print("Frozen replay adapter preflight passed", flush=True)
        # Metadata adoption and cache fills must not mutate the current-rules cache.
        title_cache = frozen / "replay-title-cache.parquet"
        if (state / "role_title_families.parquet").exists():
            shutil.copy2(state / "role_title_families.parquet", title_cache)
        command = [
            sys.executable,
            "-u",
            "-m",
            "headstart.ingest.restate_run",
            "--facts",
            str(facts),
            "--baseline",
            str(baseline),
            "--board-failures",
            str(state / "board_failures.csv"),
            "--title-cache",
            str(title_cache),
            "--db",
            str(root / "data/lancedb"),
            "--descriptions",
            str(root / "data/descriptions"),
            "--out",
            str(root / "data/restated"),
            "--encode-budget-seconds",
            os.environ.get("ENCODE_BUDGET_SECONDS", "1800"),
        ]
        print(
            f"Frozen rule fingerprint {fingerprint}; baseline {baseline.name}",
            flush=True,
        )
        process = subprocess.Popen(command, cwd=frozen, env=env)
        peak_mb = 0
        samples = (diagnostics / "resources.jsonl").open("w")
        while process.poll() is None:
            status = Path(f"/proc/{process.pid}/status")
            if status.exists():
                fields = dict(
                    line.split(":", 1)
                    for line in status.read_text().splitlines()
                    if ":" in line
                )
                resident_mb = int(fields.get("VmRSS", "0 kB").split()[0]) // 1024
                peak_mb = max(
                    peak_mb, int(fields.get("VmHWM", "0 kB").split()[0]) // 1024
                )
                samples.write(
                    json.dumps(
                        {
                            "elapsed_seconds": time.monotonic() - started,
                            "rss_mb": resident_mb,
                            "peak_mb": peak_mb,
                        }
                    )
                    + "\n"
                )
                samples.flush()
                print(
                    f"Replay resources: rss={resident_mb} MB peak={peak_mb} MB",
                    flush=True,
                )
                if resident_mb > 12 * 1024:
                    process.kill()
                    process.wait()
                    (diagnostics / "summary.json").write_text(
                        json.dumps(
                            {
                                "peak_mb": peak_mb,
                                "exit": process.returncode,
                                "watchdog_killed": True,
                            }
                        )
                    )
                    raise MemoryError(
                        f"replay exceeded 12 GB resident memory; peak={peak_mb} MB"
                    )
            time.sleep(5)
        samples.close()
        peak_mb = max(
            peak_mb, resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss // 1024
        )
        peak_bytes = max(
            peak_mb * 1024**2,
            resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss * 1024,
        )
        (diagnostics / "summary.json").write_text(
            json.dumps(
                {
                    "peak_mb": peak_mb,
                    "peak_rss_bytes": peak_bytes,
                    "exit": process.returncode,
                    "watchdog_killed": False,
                    "elapsed_seconds": time.monotonic() - started,
                    "rules_fingerprint": fingerprint,
                    "baseline": baseline.name,
                    "memory_target_bytes": 10_000_000_000,
                },
                indent=2,
            )
        )
        print(
            f"Replay exit={process.returncode}; peak resident memory={peak_mb} MB",
            flush=True,
        )
        if process.returncode == 0 and os.environ.get("GITHUB_OUTPUT"):
            with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
                output.write("completed=true\n")
        return process.returncode or int(peak_bytes >= 10_000_000_000)


if __name__ == "__main__":
    raise SystemExit(main())
