"""The rules every tool in the Space MCP server's registry obeys — `headstart/space_mcp/tools/`.

Parametrised over `tools.REGISTRY`, so a tool added tomorrow is held to them without this file
being told about it. What each tool does is `tests/test_space_mcp_server.py`.
"""

from __future__ import annotations

import json
import pathlib
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
    for word in ("account", "email", "token", "url", "path"):
        assert word not in tool.input_schema["properties"], word


def test_a_tool_is_introduced_by_the_server_instructions(tool):
    assert tool.when_to_use in server.INSTRUCTIONS
    assert tool.name in tool.when_to_use
    assert len(tool.when_to_use) <= 250


def test_the_instructions_stay_under_the_clients_cut():
    assert len(server.INSTRUCTIONS) <= 1000


def test_a_tools_budget_is_under_the_clients_warning(tool):
    assert 0 < tool.max_chars <= space_tool.ANSWER_CEILING_CHARS


def test_categories_are_the_spaces_own_role_families():
    families = json.loads(role_families.FILE.read_text(encoding="utf-8"))["families"]
    schemas = [
        tool.input_schema["properties"]["category"]
        for tool in REGISTRY
        if "category" in tool.input_schema["properties"]
    ]
    assert schemas and all(
        s["enum"] == [family["name"] for family in families] for s in schemas
    )


def test_without_the_families_file_category_is_a_free_string(monkeypatch, tmp_path):
    monkeypatch.setattr(role_families, "FILE", tmp_path / "missing.json")
    schema = role_families.schema("A category.")
    assert "enum" not in schema and schema["type"] == "string"
