"""The Inspection — one Résumé document read block by block, and how it reads (ADR-0136).

Two halves, and the split is the whole point. :func:`inspect` gets the *facts* by running
`inspect_document.js` under `node`, because every rule that decides them is JavaScript and
ADR-0136 refused to own a second copy of any of them. :func:`render` turns those facts into
the outline a caller sees, and decides nothing — it has no opinion about which block prints or
what a Component Type's fields are, only about indentation and wording.

No third-party import: `node` is a subprocess and the payload is JSON, so this runs wherever
the base install runs.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

#: The Node script beside this module. Kept next to the Python that runs it rather than with
#: the browser scripts it loads, because it is not part of the Résumé tab — nothing in the
#: product loads it, and a file in `static/resume/` is a file the page might one day serve.
SCRIPT = Path(__file__).with_name("inspect_document.js")

#: A document is at most 512 KB (`store.MAX_RESUME_BYTES`) and this reads one, so a run that
#: has not answered by now is wedged rather than busy.
TIMEOUT_S = 30


class Unreadable(Exception):
    """No block-by-block reading could be produced — `node` is missing or would not run, or
    the model refused the record (malformed, or no such version).

    One class for both, because there is one thing to do about either: say which it was and
    point at `get_resume`. In particular there is no Python fallback reading. ADR-0136's
    decision is that `resolve` and the component catalogue have one implementation, and a
    second-best answer that quietly disagrees with the Résumé tab is the failure that decision
    exists to prevent.
    """


def inspect(document: dict[str, Any], view: str = "master") -> dict[str, Any]:
    """The facts about `document` as `view` reads it — ``"master"``, or a Tailoring's id or
    name. Raises :class:`Unreadable` when no reading can be produced."""
    if shutil.which("node") is None:
        raise Unreadable(
            "`node` is not on this machine's PATH. The block-by-block view runs the Résumé "
            "tab's own JavaScript rather than a second copy of it (ADR-0136), so it needs "
            "Node. Install Node, or use get_resume for the stored JSON."
        )
    try:
        done = subprocess.run(
            ["node", str(SCRIPT), view],
            input=json.dumps(document),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
            # A non-zero exit is how the script reports a document it cannot read, and its
            # reason is on stdout as JSON — so the exit code is read below, not raised here.
            check=False,
        )
    except FileNotFoundError as exc:  # `which` said yes and exec still failed
        raise Unreadable(f"`node` could not be run: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise Unreadable(
            f"reading the document took longer than {TIMEOUT_S}s and was stopped"
        ) from exc
    try:
        answer = json.loads(done.stdout)
    except ValueError as exc:
        # stderr, not stdout: a crash before the handler writes its JSON leaves the stack there.
        detail = (done.stderr or "").strip()[
            :500
        ] or f"exit {done.returncode}, no output"
        raise Unreadable(f"reading the document failed: {detail}") from exc
    if isinstance(answer, dict) and answer.get("error"):
        raise Unreadable(str(answer["error"]))
    return answer


# ---- rendering ------------------------------------------------------------------------


def _value(value: Any) -> str:
    """One field's value, for a line of the outline. Says which kind of nothing it is —
    an empty box and an absent key look identical once both print as blank."""
    if value is None:
        return "(not set)"
    if isinstance(value, bool):
        return "yes" if value else "no"
    text = str(value)
    return text if text.strip() else "(empty)"


def _flags(block: dict[str, Any], viewing_master: bool) -> str:
    """Why a block does not print, in the words that name the right lever.

    Three distinct states share one symptom — the block is not on the page — and they are
    fixed in three different places, so they are never collapsed into "hidden"."""
    out = []
    if block["hidden_on_master"]:
        out.append("OFF for the whole résumé")
    if block["hidden_by_this_version"]:
        out.append("OFF for this version")
    if not block["prints"] and not out:
        out.append("not printed — it sits inside a block that is off")
    if block["reworded_by"] and viewing_master:
        out.append("reworded by " + ", ".join(f'"{n}"' for n in block["reworded_by"]))
    return "  —  " + "; ".join(out) if out else ""


def render(facts: dict[str, Any]) -> str:
    """The Inspection as text: a heading, then every block in document order."""
    viewing = facts["viewing"]
    master = viewing["kind"] == "master"
    lines = [
        f"Résumé {facts['name']!r} — id {facts['id']}",
        (
            f"layout {facts['layout_id']} · schema {facts['schema']} · "
            f"last edit {facts['updated_at']} · account revision {facts['rev']}"
        ),
        "Reading: the master résumé."
        if master
        else f"Reading: the version {viewing['name']!r} ({viewing['id']}).",
    ]
    if facts["theme"]:
        lines.append(f"Layout settings dialled in on this document: {facts['theme']}")

    tailorings = facts["tailorings"]
    if tailorings:
        lines.append(f"Versions ({len(tailorings)}):")
        for t in tailorings:
            job = f", made for job {t['job_id']}" if t["job_id"] else ""
            lines.append(
                f"  · {t['name']!r} ({t['id']}) — {t['reworded_blocks']} block(s) reworded, "
                f"{t['hidden_blocks']} left out{job}"
            )
    else:
        lines.append("Versions: none — this résumé has only its master.")

    blocks = facts["blocks"]
    printing = sum(1 for b in blocks if b["prints"])
    lines.append(f"{len(blocks)} block(s); {printing} print in this view.")
    if facts["unknown_types"]:
        lines.append(
            "Blocks whose Component Type this build does not know: "
            + ", ".join(facts["unknown_types"])
            + " — their fields are listed below under 'not declared by this type'."
        )
    lines.append("")

    for block in blocks:
        pad = "  " * (block["depth"] - 1)
        name = block["label"] or block["type"]
        where = f" in slot {block['slot']}" if block["slot"] else ""
        lines.append(
            f"{pad}{name} ({block['type']}) [{block['id']}]{where}"
            f"{_flags(block, master)}"
        )
        for field in block["fields"]:
            line = f"{pad}    {field['label']}: {_value(field['value'])}"
            if "master_value" in field:
                line += f"   [master says: {_value(field['master_value'])}]"
            lines.append(line)
        if block["undeclared_fields"]:
            for key, value in block["undeclared_fields"].items():
                lines.append(
                    f"{pad}    {key}: {_value(value)}   [not declared by this type]"
                )
        if block["geometry"]:
            lines.append(f"{pad}    (position/size: {block['geometry']})")
    return "\n".join(lines)
