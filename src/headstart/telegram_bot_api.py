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
            # `alerts.bot` is not a pipeline stage and never calls `log.setup()`, so routing
            # this through `headstart.log` would reach logging.lastResort — no tag, no
            # ::warning:: annotation, and unflushed against bot.py's own prints. The defect
            # here was the missing flush, not the print.
            print(f"send to {chat_id} failed: {exc}", flush=True)
