"""Shared HTTP mechanics for Digest delivery; transports keep their own failure contracts."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


def reason(exc: Exception) -> str:
    """Bound the error body and omit the HTTP URL, which can contain a Telegram token."""
    if isinstance(exc, urllib.error.HTTPError):
        try:
            body = exc.read().decode("utf-8", "replace").strip()[:200]
        except Exception:  # noqa: BLE001 — retain the status even if its body is unreadable
            body = ""
        return f"HTTP {exc.code}" + (f": {body}" if body else "")
    return f"{type(exc).__name__}: {exc}"


def post(url: str, body: bytes, headers: dict[str, str]) -> dict[str, Any]:
    """One POST, with no retry: retrying delivery can duplicate a Digest."""
    request = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)
