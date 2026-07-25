"""Telegram bot: command handling + new-job notifications.

The pure functions (`handle_update`, `build_notifications`) take state + inputs and
return replies / state mutations with no I/O, so they're unit-tested without a
network. `main()` wires them to the Telegram API and the Gist-backed state store,
and is what the scheduled workflow runs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from headstart.filters import Filter, matches

WELCOME = (
    "HeadStart job alerts. I'll message you when new roles match your filters.\n\n"
    "/q <keywords> — title/company keywords (e.g. /q backend engineer)\n"
    "/location <text> — e.g. /location india\n"
    "/remote — toggle remote-only\n"
    "/company <text> — restrict to one company\n"
    "/ats <greenhouse|lever|ashby>\n"
    "/status — show your filters\n"
    "/clear — clear all filters\n"
    "/stop — unsubscribe"
)

_MAX_ALERTS = 10  # cap roles per subscriber per run to avoid flooding


def handle_update(
    update: dict[str, Any], subscribers: dict[str, dict[str, Any]]
) -> list[tuple[str, str]]:
    """Process one Telegram update, mutating `subscribers`. Returns [(chat_id, reply)]."""
    message = update.get("message") or update.get("edited_message")
    if not message:
        return []
    text = (message.get("text") or "").strip()
    chat_id = str((message.get("chat") or {}).get("id") or "")
    if not chat_id or not text:
        return []

    cmd, _, arg = text.partition(" ")
    cmd = cmd.lower().lstrip("/")
    arg = arg.strip()

    if cmd == "start":
        subscribers.setdefault(chat_id, Filter().to_dict())
        return [(chat_id, WELCOME)]
    if cmd == "stop":
        subscribers.pop(chat_id, None)
        return [(chat_id, "Unsubscribed. Send /start to resubscribe.")]

    # Any other command auto-registers a first-time chat.
    f = Filter.from_dict(subscribers.get(chat_id))

    if cmd == "q":
        f.q = arg or None
    elif cmd == "location":
        f.location = arg or None
    elif cmd == "company":
        f.company = arg or None
    elif cmd == "ats":
        if arg.lower() not in {"greenhouse", "lever", "ashby", ""}:
            return [(chat_id, "Source must be greenhouse, lever, or ashby.")]
        f.ats = arg.lower() or None
    elif cmd == "remote":
        f.remote = not f.remote
    elif cmd == "clear":
        f = Filter()
    elif cmd == "status":
        subscribers.setdefault(chat_id, f.to_dict())
        return [(chat_id, f"Your filters — {f.describe()}")]
    else:
        subscribers.setdefault(chat_id, f.to_dict())
        return [(chat_id, WELCOME)]

    subscribers[chat_id] = f.to_dict()
    return [(chat_id, f"Updated. Now filtering — {f.describe()}")]


def _format_alerts(chat_id: str, hits: list[dict[str, Any]]) -> list[tuple[str, str]]:
    lines = [f"{len(hits)} new role(s) matching your filters:"]
    for j in hits[:_MAX_ALERTS]:
        loc = f" ({j['location']})" if j.get("location") else ""
        lines.append(
            f"- {j.get('title', 'Role')} - {j.get('company', '')}{loc}\n{j.get('url', '')}"
        )
    if len(hits) > _MAX_ALERTS:
        lines.append(f"...and {len(hits) - _MAX_ALERTS} more.")
    return [(chat_id, "\n".join(lines))]


def build_notifications(
    jobs: list[dict[str, Any]], state: dict[str, Any]
) -> list[tuple[str, str]]:
    """Find jobs new since last run and message matching subscribers.

    Mutates `state['seen_job_ids']`. On first population (empty seen set) it seeds
    the set and sends nothing, so subscribers aren't blasted with the full backlog.
    """
    seen = set(state.get("seen_job_ids") or [])
    current_ids = [j["id"] for j in jobs]
    if not seen:
        state["seen_job_ids"] = current_ids
        return []

    new_jobs = [j for j in jobs if j["id"] not in seen]
    state["seen_job_ids"] = current_ids  # prune to current feed to bound growth

    out: list[tuple[str, str]] = []
    for chat_id, fdict in (state.get("subscribers") or {}).items():
        f = Filter.from_dict(fdict)
        hits = [j for j in new_jobs if matches(j, f)]
        if hits:
            out.extend(_format_alerts(chat_id, hits))
    return out


def _load_feed_jobs() -> list[dict[str, Any]]:
    path = Path(__file__).resolve().parents[2] / "docs" / "jobs.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("jobs", [])


def main() -> None:
    import os

    from headstart.state import load_state, save_state
    from headstart.telegram import TelegramClient

    client = TelegramClient(os.environ["TELEGRAM_BOT_TOKEN"])
    state = load_state()
    state.setdefault("subscribers", {})
    state.setdefault("offset", 0)
    state.setdefault("seen_job_ids", [])

    replies: list[tuple[str, str]] = []
    for update in client.get_updates(offset=state["offset"]):
        state["offset"] = update["update_id"] + 1
        replies.extend(handle_update(update, state["subscribers"]))

    replies.extend(build_notifications(_load_feed_jobs(), state))

    for chat_id, text in replies:
        client.send_message(chat_id, text)
    save_state(state)
    print(
        f"updates handled, {len(replies)} messages sent, "
        f"{len(state['subscribers'])} subscriber(s)"
    )


if __name__ == "__main__":
    main()
