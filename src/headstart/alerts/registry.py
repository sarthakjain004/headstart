"""Who runs the Telegram bot, who is waiting on them, and where polling got to (ADR-0038).

One record at `telegram/registry.json` in the Subscriptions dataset. It holds three things
that have nothing to do with each other except that the bot needs all three durably: the
**master** chat, the **pending** requests awaiting their answer, and the getUpdates
**offset**.

**Approved people are deliberately not in here.** An approved person *is* a Subscription, in
`subscriptions/{id}.json` like everybody else — which is what lets `alerts.run` deliver to
them without knowing Telegram exists, and what makes revoking someone a delete of the same
file the unsubscribe link deletes. A second list of "who is allowed" would be a second
answer to a question `store` already answers.

This lives in the Subscriptions dataset rather than the Gist the bot used before, so the bot
and the alerts run share one store instead of two — the Gist held a `subscribers` map that
was the old keyword path's, and nothing now reads it.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from headstart import log

from .store import _is_absent, read_bytes, write_bytes

_log = log.get(__name__)

PATH = "telegram/registry.json"


@dataclass
class Pending:
    """Someone who has asked for alerts and is waiting on the master's answer.

    The display fields are carried so the master is asked about *a person* rather than an
    opaque number — Telegram gives them on every message, and they are not otherwise
    recoverable once the update is consumed.
    """

    chat_id: str
    username: str = ""
    name: str = ""
    asked_at: str = ""

    def describe(self) -> str:
        who = self.name or "someone"
        handle = f" (@{self.username})" if self.username else ""
        return f"{who}{handle} — id {self.chat_id}"


@dataclass
class Registry:
    master: str = ""
    pending: dict[str, Pending] = field(default_factory=dict)
    offset: int = 0
    #: Chats the master refused. Kept, because forgetting a refusal re-opens the gate: the
    #: same stranger's next `/start` would announce them again, indefinitely.
    denied: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "master": self.master,
            "pending": {k: asdict(v) for k, v in self.pending.items()},
            "offset": self.offset,
            "denied": self.denied,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Registry:
        raw = data.get("pending")
        pending = {}
        if isinstance(raw, dict):
            for chat_id, value in raw.items():
                if isinstance(value, dict):
                    known = {f for f in Pending.__dataclass_fields__}
                    pending[str(chat_id)] = Pending(
                        **{k: v for k, v in value.items() if k in known}
                    )
        raw_denied = data.get("denied")
        return cls(
            master=str(data.get("master") or ""),
            pending=pending,
            offset=int(data.get("offset") or 0),
            denied=[str(c) for c in raw_denied] if isinstance(raw_denied, list) else [],
        )


def load(repo: str, token: str) -> Registry:
    """The stored Registry, or an empty one.

    An unreadable record reads as empty, and an empty one has no master — so the next
    `/start` claims it. That is the intended failure direction only because the alternative
    is a bot that can never be set up; it is *not* a security boundary, which is why
    approval lives in `store` and not here.

    `ImportError` is deliberately **not** caught. A missing `huggingface_hub` is a broken
    install, not an absent record, and reporting it as "no registry yet" is actively
    misleading — it says the bot is fine and starting fresh while it is in fact unable to
    read or write anything. That is exactly how it read when `bot.yml` was still installing
    base deps only: a green-looking line, then a traceback ten lines later.
    """
    try:
        return Registry.from_dict(json.loads(read_bytes(repo, PATH, token)))
    except ImportError:
        raise
    except Exception as exc:  # noqa: BLE001 — every read failure answers "starting empty"
        # Absent, corrupt and Hub-unreachable all land here and all answer "starting empty",
        # which `bot.main` then *saves* over the stored record — so a read blip is written
        # down as a bot with no master and nobody pending. The exception type is the only
        # thing that separates the first-run case from the two that destroy state, and
        # `store._is_absent` is the only test that draws that line correctly:
        # `LocalEntryNotFoundError` subclasses `EntryNotFoundError` while meaning the
        # opposite — not "no such file" but "could not reach the Hub" — so an `isinstance`
        # against the parent alone files every outage as a routine first run.
        if _is_absent(exc):
            _log.info("no registry yet - first run")
        else:
            # Loud, and naming the consequence rather than the symptom: this run continues
            # with an empty Registry and `bot.main` saves it, so a transient read failure
            # *erases* the master and everyone pending. The next `/start` then claims the
            # master seat, and `/allow`, `/deny` and `/revoke` come with it. The bot polls
            # every 15 minutes, so this is one bad read away at all times.
            _log.error(
                f"registry unreadable ({type(exc).__name__}: {exc}) - starting empty, "
                "which will be SAVED OVER the stored record: the master and every pending "
                "request are lost and the next /start claims the master seat",
                exc_info=True,
            )
        return Registry()


def save(repo: str, token: str, registry: Registry) -> None:
    write_bytes(
        repo, PATH, json.dumps(registry.to_dict(), indent=2).encode("utf-8"), token
    )
