#!/usr/bin/env python3
"""Diff the Space's ``/trends`` and ``/companies/suggest`` answers between two checkouts, on one
state (ADR-0230 step 3).

Step 3 moves both routes onto ``headstart.trends.trend_history`` and promises their JSON stays byte for
byte what it was. This checks the promise on real state. It boots each checkout's Space app in a
process of its own, so each imports its own ``headstart``. The encoder, LanceDB and the HF pull
are stubbed, and the app's ``_STATE`` and ``_CONFIG`` are pointed at ``--state`` and at the
checkout's own ``config/``, where the Space image copies it. Both sides then get the same fixed
requests, and their response bodies are compared byte for byte. A request whose bodies differ
also reports how many JSON leaves differ.

Each side also reports its boot time, the app's import, which is where the Space loads its state
(the encoder and LanceDB are stubbed, so their load is not in it), and its peak RSS after boot and
after the requests.

Run:
  HF_HUB_DISABLE_XET=1 .venv/bin/python -c "from huggingface_hub import snapshot_download; \\
      snapshot_download('imPoseidon/headstart-index', repo_type='dataset', local_dir='<dir>', \\
      allow_patterns=['data/state/*'])"
  git worktree add --detach <base checkout> <base sha>
  .venv/bin/python -u scripts/eval/diff_trends_payloads_between_checkouts.py \\
      --state <dir> --old <base checkout> --new .
Exit: 0 when every request answers identically on both sides, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import subprocess
import sys
import time
import types
from pathlib import Path
from urllib.parse import quote

# The companies the pick requests name, by their directory name. The largest by openings on
# 2026-09-25, and three of them span several Boards, so a pick exercises found Boards and the
# `new` hold as well as a one-Board company.
_PICK_NAMES = ("Amazon", "Google", "NVIDIA", "Northrop Grumman", "Micron")
_SINCE = quote("2026-09-18T00:00:00Z")


def _requests(picks: list[str]) -> list[tuple[str, str]]:
    """The fixed request set, ``(name, path)``: every kind of view the Trends tab asks for."""
    one, two, five = picks[:1], picks[:2], picks[:5]

    def company(keys: list[str]) -> str:
        return "&".join(f"company={quote(key, safe='')}" for key in keys)

    return [
        ("stock", "/trends"),
        ("new", "/trends?metric=new"),
        ("stock-since", f"/trends?since={_SINCE}"),
        ("family-drill", "/trends?family=software-engineering"),
        ("family-drill-new-name", "/trends?family=ai-ml-data-science"),
        ("roles-split", "/trends?family=software-engineering&split=roles"),
        ("ats-workday", "/trends?ats=workday"),
        ("ats-two", "/trends?ats=greenhouse&ats=lever&metric=new"),
        ("comparable-base", f"/trends?coverage=comparable&base={_SINCE}"),
        ("comparable-default", "/trends?coverage=comparable&metric=new"),
        ("pick-one", f"/trends?{company(one)}"),
        ("pick-one-new", f"/trends?metric=new&{company(one)}"),
        ("pick-two", f"/trends?{company(two)}"),
        ("pick-two-family", f"/trends?family=software-engineering&{company(two)}"),
        ("pick-five", f"/trends?{company(five)}"),
        ("pick-five-new", f"/trends?metric=new&{company(five)}"),
        ("split-company", f"/trends?split=company&{company(five)}"),
        ("split-company-new", f"/trends?split=company&metric=new&{company(five)}"),
        (
            "split-company-comparable",
            f"/trends?split=company&coverage=comparable&base={_SINCE}&{company(five)}",
        ),
        ("suggest-amazon", "/companies/suggest?q=amazon"),
        ("suggest-citi", "/companies/suggest?q=citi&limit=20"),
        ("suggest-lockheed", "/companies/suggest?q=lockheed"),
        ("suggest-a", "/companies/suggest?q=a"),
    ]


def _picks(state: Path) -> list[str]:
    """Each of ``_PICK_NAMES``' directory key: the entry of that name with the most Boards."""
    directory = json.loads(
        (state / "data" / "state" / "company_directory.json").read_text(
            encoding="utf-8"
        )
    )
    keys = []
    for name in _PICK_NAMES:
        entries = [e for e in directory["companies"] if e["name"] == name]
        if not entries:
            raise SystemExit(f"no company named {name!r} in the directory")
        keys.append(max(entries, key=lambda e: len(e["boards"]))["boards"][0])
    return keys


def _peak_rss_mb() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # bytes on macOS, kilobytes on Linux
    return peak / 1e6 if sys.platform == "darwin" else peak / 1e3


class _Table:
    """Enough LanceDB for the app's import: an ATS scan, a schema and a row count."""

    schema = types.SimpleNamespace(names=["ats", "title", "first_seen"])

    def _chain(self, *a, **k):
        return self

    search = metric = select = where = order_by = limit = offset = _chain

    def to_list(self):
        return []

    def count_rows(self, filter=None):
        return 0


class _Model:
    """Enough encoder for the search cache the app warms at import."""

    def encode(self, texts, **_):
        return [types.SimpleNamespace(astype=lambda _dtype: [0.0])]


def _stub_heavy_modules() -> None:
    def module(name: str, **attrs) -> types.ModuleType:
        mod = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(mod, key, value)
        return mod

    sys.modules["lancedb"] = module(
        "lancedb",
        connect=lambda *a, **k: types.SimpleNamespace(
            open_table=lambda *a, **k: _Table()
        ),
    )
    sys.modules["sentence_transformers"] = module(
        "sentence_transformers", SentenceTransformer=lambda *a, **k: _Model()
    )
    sys.modules["huggingface_hub"] = module(
        "huggingface_hub", snapshot_download=lambda *a, **k: None
    )


def _boot(checkout: Path, state: Path) -> types.ModuleType:
    """The checkout's Space app, imported with its state and config paths pointed here."""
    source = (checkout / "deploy" / "hf-space" / "app.py").read_text(encoding="utf-8")
    for line, replacement in (
        ('_STATE = Path("/app/state")', f"_STATE = Path({str(state)!r})"),
        (
            '_CONFIG = Path(__file__).parent / "config"',
            f"_CONFIG = Path({str(checkout / 'config')!r})",
        ),
    ):
        if source.count(line) != 1:
            raise SystemExit(f"{checkout}: expected exactly one `{line}` in app.py")
        source = source.replace(line, replacement)
    _stub_heavy_modules()
    module = types.ModuleType("space_app")
    module.__file__ = str(checkout / "deploy" / "hf-space" / "app.py")
    sys.modules["space_app"] = module
    exec(compile(source, module.__file__, "exec"), module.__dict__)  # noqa: S102
    return module


def _serve(checkout: Path, state: Path, out: Path) -> None:
    """One side: boot the app, answer every request, and write each body under ``out``."""
    out.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    app = _boot(checkout, state)
    boot = {
        "boot_seconds": round(time.perf_counter() - started, 2),
        "peak_rss_mb_after_boot": round(_peak_rss_mb(), 1),
    }
    print(
        f"booted in {boot['boot_seconds']}s, peak RSS {boot['peak_rss_mb_after_boot']} MB",
        flush=True,
    )
    client = app.app.test_client()
    with (out / "responses.jsonl").open("w", encoding="utf-8") as index:
        for name, path in _requests(_picks(state)):
            started = time.perf_counter()
            response = client.get(path)
            seconds = round(time.perf_counter() - started, 2)
            (out / f"{name}.json").write_bytes(response.data)
            index.write(
                json.dumps(
                    {
                        "name": name,
                        "path": path,
                        "status": response.status_code,
                        "seconds": seconds,
                    }
                )
                + "\n"
            )
            index.flush()
            print(
                f"  {name}: {response.status_code} in {seconds}s ({len(response.data):,} bytes)",
                flush=True,
            )
    boot["peak_rss_mb_after_requests"] = round(_peak_rss_mb(), 1)
    (out / "boot.json").write_text(json.dumps(boot), encoding="utf-8")


def _leaf_differences(a, b) -> int:
    """How many JSON leaves differ between ``a`` and ``b`` (a missing one counts as one)."""
    if isinstance(a, dict) and isinstance(b, dict):
        return sum(_leaf_differences(a.get(k), b.get(k)) for k in a.keys() | b.keys())
    if isinstance(a, list) and isinstance(b, list):
        longer = max(len(a), len(b))
        return sum(
            _leaf_differences(
                a[i] if i < len(a) else None, b[i] if i < len(b) else None
            )
            for i in range(longer)
        )
    return 0 if a == b and type(a) is type(b) else 1


def _run_side(label: str, checkout: Path, state: Path, out: Path) -> dict:
    print(f"== {label}: {checkout}", flush=True)
    env = {**os.environ, "PYTHONPATH": str(checkout / "src")}
    subprocess.run(
        [
            sys.executable,
            "-u",
            __file__,
            "--serve",
            str(checkout),
            "--state",
            str(state),
            "--out",
            str(out),
        ],
        env=env,
        check=True,
    )
    return json.loads((out / "boot.json").read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--state", type=Path, required=True, help="a directory holding data/state/"
    )
    ap.add_argument("--old", type=Path, help="the checkout before the change")
    ap.add_argument("--new", type=Path, help="the checkout with the change")
    ap.add_argument(
        "--out", type=Path, help="where the bodies go (default: beside --state)"
    )
    ap.add_argument("--serve", type=Path, help=argparse.SUPPRESS)
    args = ap.parse_args()
    state = args.state.resolve()
    if args.serve:
        _serve(args.serve.resolve(), state, args.out)
        return 0
    if not (args.old and args.new):
        ap.error("--old and --new are both required")
    out = (args.out or state.parent / "trends_payload_diff").resolve()
    print(f"picks: {_picks(state)}", flush=True)
    sides = {
        label: _run_side(label, checkout.resolve(), state, out / label)
        for label, checkout in (("old", args.old), ("new", args.new))
    }
    statuses = {
        label: {
            row["name"]: row
            for row in map(
                json.loads, (out / label / "responses.jsonl").open(encoding="utf-8")
            )
        }
        for label in sides
    }
    differing = 0
    print(
        "\nrequest | status old/new | seconds old/new | identical bytes | differing leaves",
        flush=True,
    )
    for name, path in _requests(_picks(state)):
        old_body = (out / "old" / f"{name}.json").read_bytes()
        new_body = (out / "new" / f"{name}.json").read_bytes()
        old_row, new_row = statuses["old"][name], statuses["new"][name]
        same = old_body == new_body and old_row["status"] == new_row["status"]
        leaves = (
            0 if same else _leaf_differences(json.loads(old_body), json.loads(new_body))
        )
        differing += not same
        print(
            f"{name} ({path}) | {old_row['status']}/{new_row['status']} | "
            f"{old_row['seconds']}/{new_row['seconds']} | {same} | {leaves}",
            flush=True,
        )
    for label, boot in sides.items():
        print(f"{label}: {boot}", flush=True)
    print(
        f"\n{differing} of {len(_requests(_picks(state)))} requests differ", flush=True
    )
    return 1 if differing else 0


if __name__ == "__main__":
    raise SystemExit(main())
