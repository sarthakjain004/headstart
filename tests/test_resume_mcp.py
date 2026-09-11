"""The Résumé MCP server (ADR-0137) — its account binding, its transport and its wording.

The four HF calls are replaced the way `tests/test_alerts_store.py` replaces them, so the
account boundary is exercised without a network and without touching a real record.

What is *not* here: the block-by-block reading itself. That is JavaScript by decision
(ADR-0137), and `tests/js/resume_mcp_inspect_document.test.js` covers it on the Node step,
which CI runs unconditionally. The one test below that crosses into it is gated on `node`
being present and will skip rather than fail — the precedent `tests/test_readme_schema.py`
sets. Nothing here needs a package the base install lacks.
"""

from __future__ import annotations

import inspect
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

from headstart.alerts import store as st
from headstart.resume_mcp import account as acct
from headstart.resume_mcp import server as srv

MINE, THEIRS = "lee@example.com", "someone.else@example.com"
DOC_ID, THEIR_DOC_ID = "rm8k2p1a9x", "rq4w7v2b0z"


def _document(doc_id=DOC_ID, name="Backend SWE"):
    """A small but real Résumé document — the shape `resume_document.js` writes."""
    return {
        "schema": 1,
        "id": doc_id,
        "name": name,
        "layoutId": "headless-headhunter",
        "createdAt": "2026-09-01T10:00:00.000Z",
        "updatedAt": "2026-09-10T12:00:00.000Z",
        "root": {
            "id": "n0",
            "type": "__root__",
            "slot": None,
            "geometry": {},
            "children": [
                {
                    "id": "n1",
                    "type": "header",
                    "slot": "main",
                    "geometry": {},
                    "children": [],
                },
                {
                    "id": "n2",
                    "type": "section",
                    "slot": "main",
                    "geometry": {},
                    "children": [
                        {
                            "id": "n3",
                            "type": "bullet",
                            "slot": None,
                            "geometry": {},
                            "children": [],
                        }
                    ],
                },
            ],
        },
        "content": {
            "n1": {"fullName": "Lee Korelitz", "phone": "", "email": "lee@example.com"},
            "n2": {"title": "Work History"},
            "n3": {"text": "Shipped the payments rewrite.", "role": False},
        },
        "variants": {"n3": {"v1": {"text": "Rewrote payments at scale."}}},
        "tailorings": [
            {
                "id": "t1",
                "name": "Stripe backend",
                "jobId": "job-123",
                "picks": {"n3": "v1"},
                "hidden": [],
            },
            {
                "id": "t2",
                "name": "Datadog SRE",
                "jobId": None,
                "picks": {},
                "hidden": ["n3"],
            },
        ],
        "activeTailoring": None,
        "hidden": [],
        "theme": {},
        "sync": True,
        "rev": 4,
    }


class _Hub:
    """A dict standing in for the Subscriptions dataset's file tree."""

    def __init__(self, files):
        self.files = dict(files)

    def install(self, monkeypatch):
        monkeypatch.setattr(st, "_list_files", lambda repo, token: list(self.files))
        monkeypatch.setattr(st, "_read", lambda repo, path, token: self.files[path])
        monkeypatch.setattr(
            st, "_write", lambda *a: pytest.fail("this server must never write")
        )
        monkeypatch.setattr(
            st, "_delete", lambda *a: pytest.fail("this server must never delete")
        )
        return self


def _path(email, doc_id):
    return f"{st.RESUMES_PREFIX}{st.subscription_id(email)}/{doc_id}.json"


@pytest.fixture
def account(monkeypatch):
    """The bound Account, over a dataset that also holds somebody else's résumé."""
    _Hub(
        {
            _path(MINE, DOC_ID): json.dumps(_document()).encode(),
            _path(THEIRS, THEIR_DOC_ID): json.dumps(
                _document(THEIR_DOC_ID, "Not mine")
            ).encode(),
        }
    ).install(monkeypatch)
    return acct.open_account(
        {
            acct.EMAIL_VAR: MINE,
            acct.REPO_VAR: "acme/subs",
            acct.TOKEN_VAR: "tok",
        }
    )


# ---- the account boundary -----------------------------------------------------------


def test_only_the_bound_accounts_resumes_are_reachable(account):
    listed = srv.call(account, "list_resumes", {})
    assert "Backend SWE" in listed
    assert "Not mine" not in listed

    with pytest.raises(srv.ToolFailure) as failure:
        srv.call(account, "get_resume", {"document_id": THEIR_DOC_ID})
    assert THEIR_DOC_ID in str(failure.value)


def test_no_tool_takes_an_account_and_naming_one_is_refused(account):
    for tool in srv.TOOLS:
        schema = tool["inputSchema"]
        assert schema["additionalProperties"] is False, tool["name"]
        for name in schema["properties"]:
            assert not any(
                word in name.lower() for word in ("account", "email", "path", "repo")
            ), f"{tool['name']} takes {name}"

    # And the refusal is enforced here too, because a client is free to ignore a schema.
    for arguments in (
        {"account": st.subscription_id(THEIRS)},
        {"document_id": DOC_ID, "email": THEIRS},
    ):
        with pytest.raises(srv.ToolFailure) as failure:
            srv.call(account, "get_resume", arguments)
        assert "no tool takes an account" in str(failure.value)


def test_the_account_object_offers_no_way_to_name_another(account):
    """The binding is the class's shape, not a check somebody has to remember to write."""
    for name in ("documents", "ids", "document"):
        signature = inspect.signature(getattr(account, name))
        assert not any(
            word in parameter.lower()
            for parameter in signature.parameters
            for word in ("account", "email")
        ), f"Account.{name}{signature}"


# ---- the limitations the output has to state ----------------------------------------


def test_the_listing_says_unsynced_resumes_are_invisible(account, monkeypatch):
    assert "off by default" in srv.call(account, "list_resumes", {})

    # And the empty case most of all: nothing there looks like "you have no résumés".
    monkeypatch.setattr(acct.Account, "documents", lambda self: [])
    empty = srv.call(account, "list_resumes", {})
    assert "off by default" in empty and "cannot see it" in empty


def test_every_answer_says_the_browser_may_be_ahead(account, monkeypatch):
    """All three tools, including the one whose real path needs `node` — stubbed here so the
    note is guarded everywhere, not only where the reading itself runs."""
    monkeypatch.setattr(srv, "read_document", lambda document, view: {"view": view})
    monkeypatch.setattr(srv, "render", lambda facts: "outline")
    for name, arguments in (
        ("list_resumes", {}),
        ("get_resume", {"document_id": DOC_ID}),
        ("inspect_resume", {"document_id": DOC_ID}),
    ):
        assert srv.FRESHNESS_NOTE in srv.call(account, name, arguments), name


def test_a_record_that_will_not_read_is_counted_not_swallowed(account, monkeypatch):
    """`Store.resumes_for` skips a record it cannot parse, silently. The id listing is the
    count that does not lie, so the gap between them is reported."""
    monkeypatch.setattr(acct.Account, "ids", lambda self: {DOC_ID, "rbrokenrecord"})
    listed = srv.call(account, "list_resumes", {})
    assert "1 further record(s)" in listed
    assert "NOT listed above" in listed


def test_a_filed_but_unreadable_document_is_not_reported_as_missing(
    account, monkeypatch
):
    """Three facts wear one None. Telling someone their résumé is gone during a Hub outage is
    the one of the three that must never be guessed."""
    monkeypatch.setattr(acct.Account, "document", lambda self, doc_id: None)

    with pytest.raises(srv.ToolFailure) as filed:
        srv.call(account, "get_resume", {"document_id": DOC_ID})
    assert "could not be read" in str(filed.value)
    assert "It is not missing." in str(filed.value)

    with pytest.raises(srv.ToolFailure) as absent:
        srv.call(account, "get_resume", {"document_id": "rnosuchdoc"})
    assert "keeps no synced résumé" in str(absent.value)


def test_every_tool_has_a_handler_and_no_handler_lacks_a_tool():
    assert set(srv.HANDLERS) == {t["name"] for t in srv.TOOLS}


def test_the_listing_names_the_tailorings_and_the_revision(account):
    listed = srv.call(account, "list_resumes", {})
    assert "'Stripe backend'" in listed
    assert "account revision 4" in listed


# ---- what a caller gets ---------------------------------------------------------------


def test_get_returns_the_stored_document_unreshaped(account):
    answer = srv.call(account, "get_resume", {"document_id": DOC_ID})
    body = answer[answer.index("{") :]
    assert json.loads(body) == _document()


def test_an_unknown_document_id_answers_a_sentence(account):
    with pytest.raises(srv.ToolFailure) as failure:
        srv.call(account, "get_resume", {"document_id": "rnosuchdoc"})
    assert "keeps no synced résumé" in str(failure.value)


def test_a_missing_document_id_says_where_to_get_one(account):
    """The store's own id guard would refuse it too, answering None — but "this account keeps
    no résumé with id ''" reads as an answer about the account rather than about the call."""
    with pytest.raises(srv.ToolFailure) as failure:
        srv.call(account, "get_resume", {})
    assert "document_id is required" in str(failure.value)


@pytest.mark.skipif(shutil.which("node") is None, reason="the reading needs node")
def test_inspect_reads_the_stored_document_through_the_real_model(account):
    answer = srv.call(account, "inspect_resume", {"document_id": DOC_ID})
    # The component catalogue's labels, which exist only in JavaScript — proof the answer came
    # from the model rather than from a Python reading of the JSON.
    assert "Name & contact (header)" in answer
    assert "Full name: Lee Korelitz" in answer
    assert 'reworded by "Stripe backend"' in answer
    # Both ways a version overrides a block, answered from the master in one call.
    assert 'left out by "Datadog SRE"' in answer

    tailored = srv.call(
        account, "inspect_resume", {"document_id": DOC_ID, "version": "Stripe backend"}
    )
    assert "Rewrote payments at scale." in tailored
    assert "master says: Shipped the payments rewrite." in tailored


@pytest.mark.skipif(shutil.which("node") is None, reason="the reading needs node")
def test_an_unknown_version_names_the_ones_that_exist(account):
    with pytest.raises(srv.ToolFailure) as failure:
        srv.call(
            account, "inspect_resume", {"document_id": DOC_ID, "version": "Datadog"}
        )
    assert "Stripe backend" in str(failure.value)


def test_without_node_the_reading_says_so_instead_of_guessing(account, monkeypatch):
    """No Python fallback reading: ADR-0137's decision is one implementation of the rule, and
    a second-best answer that quietly disagrees with the Résumé tab is what that refuses."""
    monkeypatch.setattr(srv, "read_document", _raise_no_node)
    with pytest.raises(srv.ToolFailure) as failure:
        srv.call(account, "inspect_resume", {"document_id": DOC_ID})
    assert "get_resume" in str(failure.value)


def _raise_no_node(*_args, **_kwargs):
    from headstart.resume_mcp.inspection import Unreadable

    raise Unreadable("`node` is not on this machine's PATH. … use get_resume …")


def test_render_says_which_kind_of_nothing_a_field_holds():
    from headstart.resume_mcp.inspection import _value

    assert _value(None) == "(not set)"
    assert _value("") == "(empty)"
    assert _value("   ") == "(empty)"
    assert _value(False) == "no"
    assert _value(True) == "yes"


# ---- the transport --------------------------------------------------------------------


def test_initialize_and_tools_list_answer(account):
    hello = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"}, account)
    assert hello["result"]["serverInfo"]["name"] == srv.NAME
    assert "tools" in hello["result"]["capabilities"]

    listed = srv.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, account)
    assert [t["name"] for t in listed["result"]["tools"]] == [
        "list_resumes",
        "get_resume",
        "inspect_resume",
    ]


def test_a_notification_gets_no_reply_and_an_unknown_method_gets_an_error(account):
    assert (
        srv.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, account)
        is None
    )
    unknown = srv.handle(
        {"jsonrpc": "2.0", "id": 3, "method": "resources/list"}, account
    )
    assert unknown["error"]["code"] == -32601


def test_a_tool_failure_is_a_result_not_a_protocol_error(account):
    answer = srv.handle(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "get_resume",
                "arguments": {"document_id": "rnosuchdoc"},
            },
        },
        account,
    )
    assert "error" not in answer
    assert answer["result"]["isError"] is True
    assert "keeps no synced résumé" in answer["result"]["content"][0]["text"]


def test_an_unexpected_crash_is_reported_rather_than_killing_the_session(
    account, monkeypatch
):
    monkeypatch.setattr(
        acct.Account,
        "documents",
        lambda self: (_ for _ in ()).throw(RuntimeError("hub down")),
    )
    answer = srv.handle(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "list_resumes", "arguments": {}},
        },
        account,
    )
    assert answer["result"]["isError"] is True
    assert "RuntimeError: hub down" in answer["result"]["content"][0]["text"]


def test_a_real_client_handshake_over_a_real_subprocess():
    """The transport is hand-written (ADR-0136), so it is measured rather than reasoned about:
    a real `python -m headstart.resume_mcp`, real pipes, a real initialize/tools-list exchange.
    Run without credentials — what is under test is the protocol and the rule that stdout
    carries nothing but protocol, which the stderr explanation must not violate."""
    requests = "".join(
        json.dumps(m) + "\n"
        for m in (
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        )
    )
    done = subprocess.run(
        [sys.executable, "-m", "headstart.resume_mcp"],
        input=requests,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        cwd=pathlib.Path(__file__).resolve().parent.parent,
        env={**os.environ, "PYTHONPATH": "src", acct.EMAIL_VAR: "", acct.TOKEN_VAR: ""},
    )
    replies = [json.loads(line) for line in done.stdout.splitlines()]
    assert [r["id"] for r in replies] == [1, 2], done.stderr
    assert replies[0]["result"]["protocolVersion"] == srv.PROTOCOL_VERSION
    assert len(replies[1]["result"]["tools"]) == 3


def test_serve_answers_each_line_and_ignores_blank_ones(account):
    stdin = io.StringIO(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        + "\n\n"
        + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
        + "\n"
        + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"})
        + "\n"
    )
    stdout = io.StringIO()
    srv.serve(stdin, stdout, account)
    replies = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert [r["id"] for r in replies] == [1, 2]


def test_an_unparseable_line_is_answered_not_fatal(account):
    stdout = io.StringIO()
    srv.serve(io.StringIO("{not json\n"), stdout, account)
    assert json.loads(stdout.getvalue())["error"]["code"] == -32700


# ---- no credentials -------------------------------------------------------------------


def test_missing_configuration_names_every_variable_it_needs():
    with pytest.raises(acct.Unconfigured) as failure:
        acct.open_account({acct.REPO_VAR: "acme/subs"})
    named = str(failure.value).split("Set ", 1)[1].split(" in the", 1)[0]
    # The two that are absent, and not the one that is set — a message that lists everything
    # sends whoever reads it hunting for a variable that is already there.
    assert sorted(named.split(", ")) == sorted([acct.EMAIL_VAR, acct.TOKEN_VAR])


def test_a_base_install_names_the_extra_rather_than_failing_on_the_first_call(
    monkeypatch,
):
    """`store` imports `huggingface_hub` lazily inside `_hf`, and it is in the `alerts` extra —
    so a plain `pip install -e .` imports this package fine and then answers every tool call
    with a bare ModuleNotFoundError from four frames down. Checked at the door instead."""
    monkeypatch.setattr(acct.importlib.util, "find_spec", lambda name: None)
    with pytest.raises(acct.Unconfigured) as failure:
        acct.open_account(
            {acct.EMAIL_VAR: MINE, acct.REPO_VAR: "acme/subs", acct.TOKEN_VAR: "tok"}
        )
    assert '".[alerts]"' in str(failure.value)


def test_without_credentials_the_server_still_lists_its_tools_and_explains_itself():
    """A server that exits on a missing variable shows up in the client as one that will not
    connect, which tells whoever has to fix it nothing at all."""
    reason = acct.Unconfigured("set HEADSTART_ACCOUNT_EMAIL")

    listed = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, reason)
    assert len(listed["result"]["tools"]) == 3

    called = srv.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "list_resumes", "arguments": {}},
        },
        reason,
    )
    assert called["result"]["isError"] is True
    assert "HEADSTART_ACCOUNT_EMAIL" in called["result"]["content"][0]["text"]
