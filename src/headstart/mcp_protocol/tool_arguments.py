"""What is wrong with one ``tools/call``'s arguments, read against the tool's own input schema.

A client is free to ignore a schema, so a server re-checks what it was sent; a silently dropped or
mistyped argument is how a caller comes to believe it asked for something it did not. Each problem
is a sentence the model can act on, and the caller answers them as one failed tool result
(2025-11-25: input validation is a tool execution error, not a protocol error).

Only the JSON Schema keywords HeadStart's tool schemas use are read — ``type``, ``properties``,
``required``, ``additionalProperties``, ``enum``, ``minimum``, ``maximum``, ``maxLength``,
``items`` and ``maxItems`` — the ones draft-07 and 2020-12 read alike.
"""

from __future__ import annotations

from typing import Any

_TYPE_WORDS = {
    "string": "a string",
    "integer": "an integer",
    "boolean": "true or false",
    "array": "a list",
    "object": "an object",
}


def _is(kind: str, value: Any) -> bool:
    if kind == "integer":
        # `True` is an `int` in Python and a boolean in JSON; the schema means the JSON one.
        return isinstance(value, int) and not isinstance(value, bool)
    return isinstance(
        value,
        {"string": str, "boolean": bool, "array": list, "object": dict}.get(
            kind, object
        ),
    )


def _value_problem(name: str, schema: dict[str, Any], value: Any) -> str | None:
    kind = schema.get("type")
    if kind and not _is(kind, value):
        return f"`{name}` must be {_TYPE_WORDS.get(kind, kind)}."
    if "enum" in schema and value not in schema["enum"]:
        allowed = ", ".join(str(v) for v in schema["enum"])
        return f"`{name}` must be one of: {allowed}."
    if kind == "integer":
        low, high = schema.get("minimum"), schema.get("maximum")
        if (low is not None and value < low) or (high is not None and value > high):
            span = (
                f"from {low} to {high}" if low is not None and high is not None else ""
            )
            span = span or (f"at least {low}" if low is not None else f"at most {high}")
            return f"`{name}` must be {span}."
    if kind == "string" and "maxLength" in schema and len(value) > schema["maxLength"]:
        return f"`{name}` must be at most {schema['maxLength']} characters."
    if kind == "array":
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            return f"`{name}` takes at most {schema['maxItems']} items."
        for item in value:
            if problem := _value_problem(f"{name}[]", schema.get("items", {}), item):
                return problem
    return None


def with_defaults(
    input_schema: dict[str, Any], arguments: dict[str, Any]
) -> dict[str, Any]:
    """``arguments`` with every property the call left out set to its schema ``default`` — so a
    default is written once, in the schema the client reads, and the code reads it from there."""
    defaults = {
        name: schema["default"]
        for name, schema in input_schema.get("properties", {}).items()
        if "default" in schema
    }
    return {**defaults, **arguments}


def problems(input_schema: dict[str, Any], arguments: dict[str, Any]) -> list[str]:
    """Every way ``arguments`` breaks ``input_schema``, as sentences; empty when they fit."""
    properties: dict[str, Any] = input_schema.get("properties", {})
    found = []
    unknown = sorted(set(arguments) - set(properties))
    if unknown and input_schema.get("additionalProperties") is False:
        takes = ", ".join(sorted(properties)) or "no arguments"
        found.append(
            f"unknown argument(s) {', '.join(unknown)}; this tool takes {takes}."
        )
    for name in input_schema.get("required", []):
        if name not in arguments:
            found.append(f"`{name}` is required.")
    for name, value in arguments.items():
        if name in properties and (
            problem := _value_problem(name, properties[name], value)
        ):
            found.append(problem)
    return found
