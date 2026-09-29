#!/usr/bin/env python3
"""Record the Space's replies behind the eval's verifier self-test (ADR-0334).

`tests/fixtures/space_mcp_eval_recorded_calls.json` holds, per task, runs a right agent could
make: its tool calls and its final answer. This script makes each run's calls against the live
Space, keeps every reply the tools and the verifiers read, judges the run as the eval would and
prints each call's first lines and the verdict as it goes. Then it writes the replies and the
moment they were read back into the fixture, which `tests/test_space_mcp_eval.py` replays with no
network.

Re-record when a run's calls change or a tool starts reading another route. When the data has
moved (a count, a company's name), the verdict line says which answer to edit.

Run:
  python scripts/eval/record_space_mcp_eval_calls.py
"""

from __future__ import annotations

import json
import sys
import threading
import time
from collections import deque
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import space_mcp_eval as ev

from headstart.space_mcp import space_client
from headstart.space_mcp.space_client import (
    SPACE_URL,
    Reply,
    SpaceClient,
    urllib_fetch,
)

#: The reply headers a replay needs: the app's marker, which tells its reply from the edge's.
_KEPT_HEADERS = ("content-type", "x-headstart")
#: Reads a minute, under the 60 the Space's read routes admit from one address (ADR-0262).
_READS_A_MINUTE = 50


def main() -> int:
    fixture = json.loads(ev.RECORDED_CALLS.read_text(encoding="utf-8"))
    tasks = {
        task["id"]: task
        for task in json.loads(ev.ITERATION_TASKS.read_text(encoding="utf-8"))["tasks"]
    }
    replies: dict[str, dict] = {}
    lock = threading.Lock()
    sent: deque[float] = deque()

    def recording(url: str, headers: Mapping[str, str], timeout_s: float) -> Reply:
        with lock:  # paced, so no read is refused and recorded as a refusal
            while len(sent) >= _READS_A_MINUTE:
                wait_s = 60 - (time.monotonic() - sent[0])
                if wait_s <= 0:
                    sent.popleft()
                    continue
                time.sleep(wait_s)
            sent.append(time.monotonic())
        reply = urllib_fetch(url, headers, timeout_s)
        if (
            reply is not None
            and reply.headers.get("x-headstart")
            and reply.status < 429
        ):
            with lock:
                replies[url] = {
                    "status": reply.status,
                    "headers": {
                        k: reply.headers[k] for k in _KEPT_HEADERS if k in reply.headers
                    },
                    "body": reply.body.decode("utf-8"),
                }
        return reply

    # A PR that raises the agent contract is recorded against the Space as deployed, one
    # contract behind this checkout; the replay stamps every reply with the checkout's own.
    space_client.AGENT_API = 0
    now = datetime.now(UTC).replace(microsecond=0)
    failed = 0
    with ev.tools_clock_at(now):
        for task_id, runs in fixture["runs"].items():
            for n, run in enumerate(runs, 1):
                transcript = ev.replayed(run, recording)
                for call in transcript.calls:
                    head = "\n    ".join((call.result or "").splitlines()[:4])
                    print(f"{task_id} run {n} {call.name}: {head}", flush=True)
                outcome, detail = ev.judge(
                    tasks[task_id],
                    transcript,
                    SpaceClient(base=SPACE_URL, fetch=recording),
                )
                failed += outcome != "pass"
                print(f"{task_id} run {n} {outcome.upper()} · {detail}\n", flush=True)
    fixture["recorded_at"] = now.isoformat()
    fixture["replies"] = dict(sorted(replies.items()))
    ev.RECORDED_CALLS.write_text(
        json.dumps(fixture, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    print(
        f"{len(replies)} replies written to {ev.RECORDED_CALLS}; {failed} runs not passing",
        flush=True,
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
