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

from .store import _read, _write

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

    def to_dict(self) -> dict[str, Any]:
        return {
            "master": self.master,
            "pending": {k: asdict(v) for k, v in self.pending.items()},
            "offset": self.offset,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Registry":
        raw = data.get("pending")
        pending = {}
        if isinstance(raw, dict):
            for chat_id, value in raw.items():
                if isinstance(value, dict):
                    known = {f for f in Pending.__dataclass_fields__}
                    pending[str(chat_id)] = Pending(
                        **{k: v for k, v in value.items() if k in known}
                    )
        return cls(
            master=str(data.get("master") or ""),
            pending=pending,
            offset=int(data.get("offset") or 0),
        )


def load(repo: str, token: str) -> Registry:
    """The stored Registry, or an empty one.

    An unreadable record reads as empty, and an empty one has no master — so the next
    `/start` claims it. That is the intended failure direction only because the alternative
    is a bot that can never be set up; it is *not* a security boundary, which is why
    approval lives in `store` and not here.
    """
    try:
        return Registry.from_dict(json.loads(_read(repo, PATH, token)))
    except Exception as exc:  # noqa: BLE001 — absent on first run is the normal case
        print(
            f"[bot] no registry yet ({type(exc).__name__}) - starting empty", flush=True
        )
        return Registry()


def save(repo: str, token: str, registry: Registry) -> None:
    _write(repo, PATH, json.dumps(registry.to_dict(), indent=2).encode("utf-8"), token)
