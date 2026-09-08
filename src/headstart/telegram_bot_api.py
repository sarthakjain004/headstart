"""Telegram Bot API polling client for the access bot (stdlib only).

Named for the API it wraps, not for "telegram", because `alerts/telegram.py` is the alert
*sender* and the two are not interchangeable (ADR-0038): that one fails loudly so a broken
transport shows up as a failed run. The only caller is `alerts.bot`, which polls
:meth:`TelegramClient.get_updates` for the enrolment commands.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any

from headstart import log
from headstart.alerts.store import chat_subscription_id

_log = log.get(__name__)


class TelegramClient:
    def __init__(self, token: str) -> None:
        self._base = f"https://api.telegram.org/bot{token}"

    def _call(self, method: str, params: dict[str, Any]) -> Any:
        data = json.dumps(params).encode("utf-8")
        request = urllib.request.Request(
            f"{self._base}/{method}",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram {method} failed: {payload}")
        return payload.get("result")

    def get_updates(self, offset: int = 0) -> list[dict[str, Any]]:
        return self._call("getUpdates", {"offset": offset, "timeout": 0}) or []

    def send_message(self, chat_id: str, text: str) -> None:
        # A single bad chat (user blocked the bot) must not abort the run.
        try:
            self._call(
                "sendMessage",
                {"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            )
        except Exception as exc:  # noqa: BLE001
            # An anomaly the run survives, per ADR-0039's level policy — the send is
            # swallowed so one blocked chat cannot abort polling, and WARNING is what makes
            # that visible without turning the run red. The id is hashed, never raw: this
            # runs every fifteen minutes into a public repo's Actions log, where a chat id
            # is a stable handle on a real person. It is the store's own hash, so two lines
            # about one chat still correlate.
            _log.warning(f"send to {chat_subscription_id(chat_id)} failed: {exc}")
