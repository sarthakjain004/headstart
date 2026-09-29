"""The role families a tool's `category` may name, and how a caller's words for one are read.

**Where the list comes from.** The Space's own `config/role_families.json`, found on every install
(ADR-0274): the wheel carries a copy beside this module (``pyproject.toml`` force-includes it,
which is what a ``uvx`` install builds), and otherwise the nearest ancestor directory holding
``config/role_families.json`` has it — the repository on an editable install, ``/app`` in the
Space's image, where the package is ``/app/headstart`` and the config ``/app/config``. Without the
file anywhere, `category` becomes a free string that the Space checks itself (``strict=1`` on a
search, ``family_known`` on a trend), and the server says so once on stderr.

**What a caller may send.** :func:`resolve` reads an id, a label, a close name ("AI/ML", "machine
learning") or a retired id (``python-development``) as one current family, case-blind; a name that
could be several families, or none, is refused with the ids and labels to choose from. Only the
current families are offered: the enum lists them, and a retired id is read as its successor.
"""

from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path
from typing import Any

from headstart.mcp_protocol.messages import ToolFailure

from .. import log

_log = log.get(__name__)

_FILE_NAME = "role_families.json"


def _candidates() -> tuple[Path, ...]:
    """Where the list might be, nearest first: the wheel's copy beside this module, then
    ``config/`` in each ancestor directory. Walked, never indexed, so a layout shallower than
    expected finds nothing rather than raising (the lesson `search_filters.fx` records)."""
    here = Path(__file__).resolve()
    return (
        here.parent / _FILE_NAME,
        *(ancestor / "config" / _FILE_NAME for ancestor in here.parents),
    )


def _located() -> Path:
    """The first candidate that exists, else the wheel's path, which the warning then names."""
    candidates = _candidates()
    return next((path for path in candidates if path.is_file()), candidates[0])


FILE = _located()


@cache
def _taxonomy() -> tuple[dict[str, str], tuple[tuple[str, str, str], ...]] | None:
    """``(labels, meanings)``: each current family's id -> label, in the file's order, and every
    ``(id, label, current id)`` a caller may mean — the current families and the retired ones,
    each retired one followed to the current family that took it over. None when unreadable."""
    try:
        taxonomy = json.loads(FILE.read_text(encoding="utf-8"))
        # A hidden family (ADR-0306) is counted but never offered: not in the enum, and a caller
        # who names it is told there is no such category.
        labels = {
            family["name"]: family["label"]
            for family in taxonomy["families"]
            if not family.get("hidden")
        }
        retired = {
            family["name"]: (family["label"], family["successor"])
            for family in taxonomy.get("retired", [])
        }
    except (OSError, ValueError, KeyError, TypeError):
        _log.warning(
            "role families not readable at %s; category is a free string", FILE
        )
        return None

    def current(name: str | None) -> str | None:
        for _ in range(len(retired) + 1):
            if name in labels or name is None:
                return name
            name = retired.get(name, (None, None))[1]
        return None

    meanings = [(name, label, name) for name, label in labels.items()]
    meanings += [
        (name, label, successor)
        for name, (label, _) in retired.items()
        if (successor := current(name))
    ]
    return labels, tuple(meanings)


def names() -> tuple[str, ...] | None:
    """The current families' ids, in the file's order; None when the file cannot be read."""
    taxonomy = _taxonomy()
    return tuple(taxonomy[0]) if taxonomy else None


def label(name: str) -> str | None:
    """A current family's label, or None."""
    taxonomy = _taxonomy()
    return taxonomy[0].get(name) if taxonomy else None


def _words(text: str) -> tuple[str, ...]:
    return tuple(re.findall(r"[a-z0-9]+", text.lower()))


def _listed(labels: dict[str, str], ids: list[str]) -> str:
    return ", ".join(f"{name} ({labels[name]})" for name in ids)


def resolve(asked: Any) -> Any:
    """The current family ``asked`` means: its id, its label, a close name or a retired id,
    case-blind. Unchanged when it is not a string (the schema check refuses it) or when there is
    no list to read against (the Space refuses it). A name that could be several families, or
    none, is a :class:`ToolFailure` listing the ids and labels to choose from.

    Words decide: "AI, ML & Data Science", "ai-ml" and "AI/ML" are all words of
    ``ai-ml-data-science`` or of the retired ``ai-ml`` it took over. An exact match of every word
    wins; otherwise every family whose id or label holds all the words asked is a candidate."""
    taxonomy = _taxonomy()
    if taxonomy is None or not isinstance(asked, str):
        return asked
    labels, meanings = taxonomy
    if asked in labels:
        return asked
    words = _words(asked)
    exact = {
        current
        for name, label_, current in meanings
        if words and words in (_words(name), _words(label_))
    }
    candidates = exact or {
        current
        for name, label_, current in meanings
        if words and set(words) <= set(_words(name)) | set(_words(label_))
    }
    if len(candidates) == 1:
        return candidates.pop()
    if candidates:
        ordered = [name for name in labels if name in candidates]
        raise ToolFailure(
            f"{asked!r} could be several job categories: {_listed(labels, ordered)}. "
            "Send one id."
        )
    raise ToolFailure(
        f"No job category is called {asked!r}. Send one of these ids: "
        f"{_listed(labels, list(labels))}."
    )


def schema(description: str) -> dict[str, Any]:
    """A `category` property: an enum of the families, or a bounded string without the file."""
    families = names()
    property_schema: dict[str, Any] = {"type": "string", "description": description}
    if families:
        property_schema["description"] += (
            " A label or close name ('AI/ML') is read as its id."
        )
        property_schema["enum"] = list(families)
    else:
        property_schema["maxLength"] = 60
    return property_schema
