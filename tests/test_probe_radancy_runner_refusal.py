"""The Radancy runner probe (scripts/bench/probe_radancy_runner_refusal.py) and its workflow.

The probe runs once, on a public repository's runners, and sends a burst at a third party's hosts,
so what these pin is what makes that safe rather than what it finds: it stops a Board at the first
refusal, it never goes faster or further than its caps, it keeps no cookie value or address, and
its workflow can be started by hand only and holds no credential. Nothing here touches the network.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import re
from pathlib import Path

import pytest
from fake_fetcher import FakeResponse

_ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "probe_radancy_runner_refusal",
    _ROOT / "scripts" / "bench" / "probe_radancy_runner_refusal.py",
)
probe = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(probe)
_WORKFLOW = (_ROOT / ".github" / "workflows" / "radancy-runner-probe.yml").read_text(
    "utf-8"
)


class _Session:
    """An ``AsyncSession`` whose ``n``-th and later answers are 403."""

    def __init__(self, refuse_from: int) -> None:
        self.refuse_from = refuse_from
        self.calls = 0

    async def get(self, url: str, **kwargs: object) -> FakeResponse:
        self.calls += 1
        answer = self.calls
        await asyncio.sleep(0)  # let the other streams start, as a real answer would
        refused = answer >= self.refuse_from
        return FakeResponse(
            403 if refused else 200,
            text="Access Denied" if refused else "<html></html>",
            headers={
                "Server": "AkamaiGHost",
                "Set-Cookie": "bm_sz=SECRET; Path=/, _abck=OTHER; Path=/",
            },
        )


def _burst(refuse_from: int, urls: int = 100, width: int = 4) -> tuple[dict, _Session]:
    session = _Session(refuse_from)
    result = asyncio.run(
        probe.burst(
            session,
            [f"https://front.example/job/{i}" for i in range(urls)],
            proxy=None,
            width=width,
            deadline=float("inf"),
        )
    )
    return result, session


def test_a_burst_stops_every_stream_at_the_first_refusal():
    result, session = _burst(refuse_from=10, width=4)

    assert result["first_refusal"]["status"] == 403
    assert result["first_refusal"]["pages_ok_before"] == 9
    # Only requests already in flight when the refusal landed settle after it: at most width - 1.
    assert sum(result["after_refusal"].values()) <= 3
    assert session.calls == result["sent"] <= 9 + 4
    assert result["sent"] < 100


def test_a_burst_that_is_never_refused_sends_every_url():
    result, session = _burst(refuse_from=10_000, urls=30)

    assert result["first_refusal"] is None
    assert result["sent"] == session.calls == 30
    assert result["statuses"] == {"200": 30}


def test_a_burst_past_its_deadline_sends_nothing_more():
    session = _Session(refuse_from=10_000)
    result = asyncio.run(
        probe.burst(
            session, ["https://front.example/a"] * 20, proxy=None, width=4, deadline=0.0
        )
    )
    assert result["sent"] == 0


def test_the_kept_refusal_holds_no_cookie_value_and_no_address():
    probe._runner_ips.add("203.0.113.9")
    try:
        reply = probe.reply_of(
            FakeResponse(
                403,
                text="Access Denied for 203.0.113.9",
                headers={
                    "Server": "AkamaiGHost",
                    "X-Echo": "you are 203.0.113.9",
                    "Set-Cookie": "bm_sz=SECRET; Path=/, _abck=OTHER",
                },
            ),
            keep_body=True,
        )
    finally:
        probe._runner_ips.discard("203.0.113.9")

    kept = json.dumps(reply)
    assert "SECRET" not in kept and "OTHER" not in kept
    assert "203.0.113.9" not in kept
    assert reply["set_cookie_names"] == ["_abck", "bm_sz"]
    assert reply["headers"]["server"] == "AkamaiGHost"


@pytest.mark.parametrize(
    "value",
    [
        "https://jobs.example.com",
        "jobs.example.com/path",
        "localhost",
        "10.0.0.1",
        "a b.example.com",
        "jobs.example.com:8080",
    ],
)
def test_a_host_input_must_be_a_bare_hostname(value: str):
    with pytest.raises(SystemExit):
        probe.hosts_of(value)


def test_hosts_are_split_and_lower_cased():
    assert probe.hosts_of("Jobs.Jabil.com, careers.sysco.com") == [
        "jobs.jabil.com",
        "careers.sysco.com",
    ]


def test_the_request_counts_and_pace_are_capped_whatever_the_inputs(tmp_path: Path):
    out = tmp_path / "probe.json"
    probe.sys.argv = [
        "probe",
        "--arms",
        "",
        "--pages",
        "99999",
        "--burst-n",
        "99999",
        "--pause",
        "0.01",
        "--out",
        str(out),
    ]
    assert probe.main() == 0

    args = json.loads(out.read_text())["args"]
    assert args["pages"] == probe.PAGES_CAP == 400
    assert args["burst_n"] == probe.BURST_CAP == 400
    assert args["pause"] >= 1.0, "never faster than one a second on one Board"


def test_the_summary_names_a_refusal_and_whether_it_lasted():
    result = {
        "arms": {
            "where": {"direct": {"colo": "SJC"}},
            "sequential": {
                "jobs.example.com": {
                    "statuses": {"200": 261, "403": 1},
                    "first_refusal": {
                        "index": 261,
                        "status": 403,
                        "pages_ok_before": 261,
                        "headers": {"server": "AkamaiGHost"},
                    },
                    "ladder": [
                        {
                            "since_refusal_s": 5.1,
                            "clients": {c: {"status": 403} for c in probe.CLIENTS},
                            "control_board": {"status": 200},
                        }
                    ],
                }
            },
        }
    }
    summary = probe.render_summary(result, "2")

    assert "replica 2" in summary
    assert (
        "| sequential | jobs.example.com | direct | 261 | 403 at #261 | AkamaiGHost |"
        in summary
    )
    assert "| sequential | jobs.example.com | 5.1 | 403 | 403 | 403 | 200 |" in summary


# --- the workflow: hand-started, read-only, credential-free ---------------------------------


def test_the_workflow_starts_by_hand_and_only_by_hand():
    on_block = re.search(r"^on:\n((?:[ \t]+.*\n|\n)+)", _WORKFLOW, re.MULTILINE).group(
        1
    )
    triggers = re.findall(r"^  ([a-z_]+):", on_block, re.MULTILINE)
    assert triggers == ["workflow_dispatch"]


def test_the_workflow_can_read_the_repository_and_write_nothing():
    permissions = re.search(
        r"^permissions:\n((?:  .*\n)+)", _WORKFLOW, re.MULTILINE
    ).group(1)
    assert permissions.strip() == "contents: read"
    assert "persist-credentials: false" in _WORKFLOW


def test_the_workflow_references_no_credential():
    assert not re.search(r"\$\{\{\s*secrets\.", _WORKFLOW)
    for name in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "GITHUB_TOKEN", "LITELLM"):
        assert name not in _WORKFLOW, name


def test_the_workflow_is_capped_at_twenty_minutes_and_uploads_what_it_found():
    job_cap = re.search(r"^    timeout-minutes: (\d+)", _WORKFLOW, re.MULTILINE)
    assert job_cap and int(job_cap.group(1)) <= 20
    assert "GITHUB_STEP_SUMMARY" in _WORKFLOW
    assert re.search(r"actions/upload-artifact@[0-9a-f]{40}", _WORKFLOW)
    assert "if: always()" in _WORKFLOW


def test_every_action_the_workflow_uses_is_pinned_to_a_commit():
    uses = re.findall(r"^\s*- uses: (\S+)", _WORKFLOW, re.MULTILINE)
    assert uses
    for action in uses:
        assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", action), action


def test_the_workflow_reads_its_inputs_from_the_environment_not_the_script():
    script_lines = [
        line
        for line in _WORKFLOW.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    interpolated = [line for line in script_lines if "github.event.inputs" in line]
    assert interpolated and all(
        re.match(r"\s+[A-Z_]+: \$\{\{ github\.event\.inputs\.\w+ \}\}$", line)
        for line in interpolated
    )
