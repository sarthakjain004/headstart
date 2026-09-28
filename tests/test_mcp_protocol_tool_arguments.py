"""What is wrong with a tool call's arguments — `headstart/mcp_protocol/tool_arguments.py`."""

from __future__ import annotations

import pytest

from headstart.mcp_protocol import tool_arguments

SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "maxLength": 5},
        "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        "remote": {"type": "boolean"},
        "sort": {"type": "string", "enum": ["relevance", "salary"]},
        "companies": {"type": "array", "items": {"type": "string"}, "maxItems": 2},
        "floor": {"type": "integer", "minimum": 0},
    },
    "required": ["query"],
    "additionalProperties": False,
}


def test_arguments_that_fit_have_no_problems():
    arguments = {"query": "go", "limit": 50, "remote": False, "sort": "salary"}
    assert tool_arguments.problems(SCHEMA, arguments) == []


@pytest.mark.parametrize(
    ("arguments", "words"),
    [
        ({"query": "go", "account": "me"}, "unknown argument(s) account"),
        ({}, "`query` is required"),
        ({"query": "toolong"}, "at most 5 characters"),
        ({"query": "go", "limit": "10"}, "`limit` must be an integer"),
        ({"query": "go", "limit": True}, "`limit` must be an integer"),
        ({"query": "go", "limit": 0}, "from 1 to 50"),
        ({"query": "go", "floor": -1}, "at least 0"),
        ({"query": "go", "remote": "yes"}, "`remote` must be true or false"),
        ({"query": "go", "sort": "newest"}, "one of: relevance, salary"),
        ({"query": "go", "companies": ["a", "b", "c"]}, "at most 2 items"),
        ({"query": "go", "companies": ["a", 3]}, "`companies[]` must be a string"),
    ],
)
def test_each_breach_is_a_sentence_naming_the_argument(arguments, words):
    assert any(
        words in problem for problem in tool_arguments.problems(SCHEMA, arguments)
    )


def test_defaults_come_from_the_schema_and_never_override_what_was_sent():
    schema = {
        "properties": {
            "limit": {"type": "integer", "default": 10},
            "sort": {"type": "string", "default": "relevance"},
            "query": {"type": "string"},
        }
    }
    assert tool_arguments.with_defaults(schema, {"limit": 3}) == {
        "limit": 3,
        "sort": "relevance",
    }
