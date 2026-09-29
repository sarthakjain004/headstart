#!/usr/bin/env python3
"""Does the model retry a tool call that meets Hugging Face's edge error page? An A/B control.

Hugging Face's edge answers about one hosted MCP call in seven with its own HTML page (HTTP 502,
the page saying 500), before the Space sees the request, and no MCP client retries a failed POST
(ADR-0325). The server's instructions ask the model to retry. This measures whether it does.

Each run is one ``claude -p`` task, as ``space_mcp_eval.py`` runs it, against a Streamable HTTP
endpoint on this machine. The endpoint serves the real server (``build_server()``, reading the
deployed Space over HTTPS) and answers the first ``--failures`` ``tools/call`` POSTs of the run
with ``space_mcp_edge_retry_page.html``: the page a real 502 carried on 2026-09-29, sent with
HTTP 502, ``text/html`` and no ``X-HeadStart``, as the edge sends it. Every other request is
answered. The two arms differ only in the instructions: ``before`` drops the retry sentence,
``after`` keeps it.

Why not the hosted connector: its edge fails at random, about 14% of calls, so ten tasks meet one
or two failures and the two arms meet different ones; and the ``after`` arm could not run before
the sentence was deployed. Here every run meets the same failures at the same calls.

It invokes Claude Code, the client under test, not an LLM API from project code, so it does not
route through the llm-router.

Run (needs the network and a signed-in ``claude``; about $0.14 a run on 2026-09-29):
  python scripts/eval/space_mcp_edge_retry.py --arm before
  python scripts/eval/space_mcp_edge_retry.py --arm after --failures 2 --runs 2
One JSON line per run goes to ``experiment/space-mcp-edge-retry/artifacts/`` as it ends, with
its transcript beside it, and one line is printed; the end prints each outcome's count.
"""

from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))
from headstart.mcp_protocol import messages, streamable_http
from headstart.space_mcp import server as space_server

_spec = importlib.util.spec_from_file_location(
    "space_mcp_eval", Path(__file__).with_name("space_mcp_eval.py")
)
space_mcp_eval = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = space_mcp_eval  # @dataclass looks its own module up
_spec.loader.exec_module(space_mcp_eval)

EDGE_PAGE = Path(__file__).with_name("space_mcp_edge_retry_page.html").read_bytes()
ARTIFACTS = _ROOT / "experiment" / "space-mcp-edge-retry" / "artifacts"
#: The iteration tasks measured on 2026-09-29: every tool but get_job, no description scan.
DEFAULT_TASKS = "t01,t02,t03,t04,t05,t09,t14,t16,t19,t21"


class EdgeFailingEndpoint:
    """A local Streamable HTTP endpoint for one run: the first ``failures`` ``tools/call`` POSTs
    get the edge page, and everything else is ``server``'s answer. ``posts`` records each POST's
    method, tool and whether it was failed, so a retry by the client's transport would show."""

    def __init__(self, server: messages.Server, failures: int) -> None:
        self.posts: list[dict[str, Any]] = []
        lock = threading.Lock()
        endpoint = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def do_GET(self) -> None:  # no SSE stream, as the Space answers
                self.send_response(405)
                self.end_headers()

            do_DELETE = do_GET

            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                called = streamable_http.tool_call(body)
                with lock:
                    calls = sum(p["tool"] is not None for p in endpoint.posts)
                    failed = called is not None and calls < failures
                    endpoint.posts.append(
                        {"tool": called and called[0], "failed": failed}
                    )
                if failed:
                    status, headers, out = 502, {"Content-Type": "text/html"}, EDGE_PAGE
                else:
                    status, headers, out = streamable_http.answer(
                        dict(self.headers), body, server, frozenset()
                    )
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._httpd.server_address[1]}/mcp"

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


def _edge_page(call: Any) -> bool:
    return call.is_error and "<!DOCTYPE html" in (call.result or "")


def outcome(calls: list[Any]) -> str:
    """What the model did with the edge failures a run's calls met: ``recovered by retrying``
    (the call after the last failure repeated the failed tool and was answered), ``recovered
    elsewhere`` (some later call to another tool was answered), ``gave up`` (no call after the
    failures was answered), or ``no failure met`` (it never called a tool)."""
    failed = [i for i, call in enumerate(calls) if _edge_page(call)]
    if not failed:
        return "no failure met"
    last = failed[-1]
    after = calls[last + 1 :]
    if after and after[0].name == calls[last].name and after[0].succeeded:
        return "recovered by retrying"
    if any(call.succeeded for call in after):
        return "recovered elsewhere"
    return "gave up"


def instructions(arm: str) -> str:
    """The server's instructions with the retry sentence (``after``) or without it."""
    if arm == "after":
        return space_server.INSTRUCTIONS
    return space_server.INSTRUCTIONS.replace(
        " " + space_server._INSTRUCTIONS_EDGE_RETRY, ""
    )


def run(task: dict[str, Any], arm: str, failures: int, stem: Path) -> dict[str, Any]:
    """One ``claude -p`` run of ``task`` against a fresh endpoint; its record."""
    endpoint = EdgeFailingEndpoint(space_server.build_server(env={}), failures)
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="space-mcp-edge-retry-") as scratch:
            config = Path(scratch) / "mcp.json"
            config.write_text(
                json.dumps(space_mcp_eval.mcp_config({}, endpoint.url)),
                encoding="utf-8",
            )
            proc = subprocess.run(
                space_mcp_eval.command(task["prompt"], str(config)),
                cwd=scratch,
                env=space_mcp_eval.run_env(dict(os.environ), endpoint.url),
                capture_output=True,
                text=True,
                timeout=space_mcp_eval.TASK_TIMEOUT_S,
                check=False,  # a failed run is read from its transcript, like any other
            )
    finally:
        endpoint.close()
    transcript_path = stem.with_name(f"{stem.name}_transcript.jsonl")
    transcript_path.write_text(proc.stdout, encoding="utf-8")
    transcript = space_mcp_eval.parse(proc.stdout.splitlines())
    return {
        "task": task["id"],
        "arm": arm,
        "failures": failures,
        "outcome": outcome(transcript.calls),
        "server_status": transcript.server_status,
        "calls": [[c.name, c.succeeded, _edge_page(c)] for c in transcript.calls],
        "posts": endpoint.posts,
        "wall_s": round(time.monotonic() - started, 1),
        "cost_usd": transcript.cost_usd,
        "final_answer": transcript.final_answer,
        "transcript": str(transcript_path.relative_to(_ROOT)),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--arm", choices=["before", "after"], required=True)
    parser.add_argument("--failures", type=int, default=2)
    parser.add_argument("--tasks", default=DEFAULT_TASKS, help="comma-separated ids")
    parser.add_argument("--runs", type=int, default=1, help="runs per task")
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args(argv)

    space_server.INSTRUCTIONS = instructions(args.arm)  # build_server reads it per call
    wanted = args.tasks.split(",")
    every = json.loads(space_mcp_eval.ITERATION_TASKS.read_text(encoding="utf-8"))
    tasks = [t for t in every["tasks"] if t["id"] in wanted]
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H%MZ")
    prefix = ARTIFACTS / f"{stamp}_{args.arm}_f{args.failures}"
    results_path = prefix.with_name(f"{prefix.name}_results.jsonl")
    print(f"{len(tasks)} tasks × {args.runs}; results to {results_path}", flush=True)
    outcomes: collections.Counter[str] = collections.Counter()
    with (
        ThreadPoolExecutor(args.workers) as pool,
        results_path.open("a", encoding="utf-8") as results,
    ):
        futures = [
            pool.submit(
                run,
                task,
                args.arm,
                args.failures,
                prefix.with_name(f"{prefix.name}_{task['id']}_r{n}"),
            )
            for n in range(1, args.runs + 1)
            for task in tasks
        ]
        for future in as_completed(futures):
            record = future.result()
            outcomes[record["outcome"]] += 1
            results.write(json.dumps(record, ensure_ascii=False) + "\n")
            results.flush()
            print(
                f"{record['task']} {record['arm']} {record['outcome']} · "
                f"{len(record['calls'])} calls · {record['wall_s']:.0f}s",
                flush=True,
            )
    print("\n" + "\n".join(f"{k}: {v}" for k, v in outcomes.most_common()), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
