"""Minimal Telegram Bot API client (stdlib only)."""

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
            print(f"send to {chat_id} failed: {exc}")
