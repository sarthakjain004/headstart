"""The role families a tool's `category` may name: the file the Space reads them from.

`config/role_families.json` sits beside the package on an editable install, which is how
`docs/agents/space-mcp-server.md` installs this server. Without it (a non-editable install),
`category` becomes a free string that the Space checks itself (`strict=1` on a search, and
`family_known` on a trend), and the server says so once on stderr.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

import headstart

from .. import log

_log = log.get(__name__)

FILE = Path(headstart.__file__).resolve().parents[2] / "config" / "role_families.json"


@cache
def names() -> tuple[str, ...] | None:
    """The families' ids, in the file's order; None when the file cannot be read."""
    try:
        families = json.loads(FILE.read_text(encoding="utf-8"))["families"]
        return tuple(family["name"] for family in families)
    except (OSError, ValueError, KeyError, TypeError):
        _log.warning(
            "role families not readable at %s; category is a free string", FILE
        )
        return None


def schema(description: str) -> dict[str, Any]:
    """A `category` property: an enum of the families, or a bounded string without the file."""
    families = names()
    property_schema: dict[str, Any] = {"type": "string", "description": description}
    if families:
        property_schema["enum"] = list(families)
    else:
        property_schema["maxLength"] = 60
    return property_schema
