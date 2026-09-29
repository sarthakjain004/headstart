"""The rules every tool in the Space MCP server's registry obeys — `headstart/space_mcp/tools/`.

Parametrised over `tools.REGISTRY`, so a tool added tomorrow is held to them without this file
being told about it. What each tool does is `tests/test_space_mcp_server.py`.
"""

from __future__ import annotations

import json
import pathlib
import re
from typing import Any

import pytest

from headstart.mcp_protocol import tool_arguments
from headstart.space_mcp import role_families, server, space_tool
from headstart.space_mcp.tools import REGISTRY

#: The JSON Schema keywords draft-07 and 2020-12 read alike — the only ones a tool may use, so
#: the default-dialect change of MCP 2025-11-25 cannot change what a schema means.
PORTABLE_KEYWORDS = {
    "type",
    "properties",
    "required",
    "additionalProperties",
    "enum",
    "minimum",
    "maximum",
    "maxLength",
    "items",
    "maxItems",
    "default",
    "description",
}

TOOLS_DIR = pathlib.Path(space_tool.__file__).parent / "tools"


def _keywords(schema: dict[str, Any]) -> set[str]:
    used = set(schema)
    for child in schema.get("properties", {}).values():
        used |= _keywords(child)
    if isinstance(schema.get("items"), dict):
        used |= _keywords(schema["items"])
    return used


@pytest.fixture(params=REGISTRY, ids=lambda tool: tool.name)
def tool(request) -> space_tool.SpaceTool:
    return request.param


def test_names_are_unique_and_every_tool_module_is_registered():
    names = [tool.name for tool in REGISTRY]
    assert len(names) == len(set(names))
    modules = {path.stem for path in TOOLS_DIR.glob("*.py") if path.stem != "__init__"}
    assert modules == set(names), (
        "a tool module is not in REGISTRY, or a name is not its module's"
    )


def test_the_listing_is_the_registry_in_order():
    assert [listed["name"] for listed in server.TOOLS] == [
        tool.name for tool in REGISTRY
    ]


def test_a_tool_has_a_title_and_says_what_it_does_within_the_clients_cut(tool):
    """Claude Code cuts a description at 2,048 characters; the rule that matters most is in the
    first sentence, before any cut."""
    assert tool.title and tool.description
    assert len(tool.description) <= 2048
    assert tool.description.split(". ")[0].strip()


def test_a_tools_input_schema_is_closed_and_portable(tool):
    schema = tool.input_schema
    assert schema["type"] == "object" and schema["additionalProperties"] is False
    assert _keywords(schema) <= PORTABLE_KEYWORDS, _keywords(schema) - PORTABLE_KEYWORDS
    for name, prop in schema["properties"].items():
        assert "type" in prop, f"{tool.name}.{name} has no type"


def test_a_tools_defaults_satisfy_its_own_schema(tool):
    filled = tool_arguments.with_defaults(tool.input_schema, {})
    assert tool_arguments.problems(tool.input_schema, filled) == []


def test_a_tool_is_read_only_and_its_listing_says_so(tool):
    """Every tool today only reads and names no Account. A tool that writes is a decision
    (`space_mcp/tools/__init__.py`), so this test is where it would have to be made."""
    listed = tool.listing()
    assert listed["annotations"]["readOnlyHint"] is True
    assert listed["annotations"]["destructiveHint"] is False
    # ADR-0334: it quotes postings thousands of employers wrote, as a web search quotes pages.
    assert listed["annotations"]["openWorldHint"] is True
    for word in ("account", "email", "token", "url", "path"):
        assert word not in tool.input_schema["properties"], word


def test_a_tool_is_introduced_by_the_server_instructions(tool):
    assert tool.when_to_use in server.INSTRUCTIONS
    assert tool.name in tool.when_to_use
    assert len(tool.when_to_use) <= 200


def test_the_instructions_stay_under_the_clients_cut():
    """Built from every tool's sentence, so they grow with the registry: at 200 characters a
    tool, the cut leaves room for about six more before sentences must shorten."""
    assert len(server.INSTRUCTIONS) <= server.INSTRUCTIONS_LIMIT


def test_the_instructions_tell_the_model_to_retry_an_edge_failure():
    """ADR-0325: Hugging Face's edge answers some hosted calls with its own page, which says 500
    under an HTTP 502, and no MCP client retries a failed POST."""
    sentence = server._INSTRUCTIONS_EDGE_RETRY
    assert sentence in server.INSTRUCTIONS
    for words in ("Hugging Face error page", "500", "502", "only reads", "up to twice"):
        assert words in sentence, words
    assert len(sentence) <= 200


#: Every key a tool's listing or the server's instructions give as an example, each one the
#: Company directory holds: all checked against the live `/companies/lookup` on 2026-09-29.
#: Round-4 critique P2-3: the example 'lever:razorpay' was no Board the directory holds, and
#: find_company said so. A new example key is checked there before it joins this set.
_EXAMPLE_KEYS_THE_DIRECTORY_HOLDS = {"greenhouse:stripe", "ashby:openai"}


def test_every_example_key_is_one_the_directory_holds():
    said = server.INSTRUCTIONS + json.dumps(
        [tool.listing() for tool in REGISTRY], ensure_ascii=False
    )
    examples = set(re.findall(r"'([a-z_]+:[^'\s]+)'", said))
    assert examples == _EXAMPLE_KEYS_THE_DIRECTORY_HOLDS


def test_a_tools_budget_is_under_the_clients_warning(tool):
    assert 0 < tool.max_chars <= space_tool.ANSWER_CEILING_CHARS


def test_an_argument_reader_reads_an_argument_the_schema_names(tool):
    assert set(tool.argument_readers) <= set(tool.input_schema["properties"])


def test_every_tool_with_a_category_reads_a_label_as_its_id():
    """The enum lists ids, and the server checks arguments against it, so a label reaches the
    tool only through the reader (ADR-0274)."""
    with_category = [t for t in REGISTRY if "category" in t.input_schema["properties"]]
    assert with_category and all(
        t.argument_readers.get("category") is role_families.resolve
        for t in with_category
    )


def test_categories_are_the_spaces_own_role_families():
    families = json.loads(role_families.FILE.read_text(encoding="utf-8"))["families"]
    schemas = [
        tool.input_schema["properties"]["category"]
        for tool in REGISTRY
        if "category" in tool.input_schema["properties"]
    ]
    assert schemas and all(
        s["enum"] == [family["name"] for family in families if not family.get("hidden")]
        for s in schemas
    )


def test_without_the_families_file_category_is_a_free_string(monkeypatch, tmp_path):
    monkeypatch.setattr(role_families, "FILE", tmp_path / "missing.json")
    role_families._taxonomy.cache_clear()
    try:
        schema = role_families.schema("A category.")
    finally:
        role_families._taxonomy.cache_clear()
    assert "enum" not in schema and schema["type"] == "string"


def test_the_families_are_read_once_so_a_missing_file_warns_once(
    monkeypatch, tmp_path, caplog
):
    monkeypatch.setattr(role_families, "FILE", tmp_path / "missing.json")
    role_families._taxonomy.cache_clear()
    try:
        role_families.schema("One.")
        role_families.schema("Two.")
    finally:
        role_families._taxonomy.cache_clear()
    assert caplog.text.count("role families not readable") == 1
