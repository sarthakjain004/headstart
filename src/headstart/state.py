"""Bot state (subscribers, update offset, seen job ids) in a private GitHub Gist.

A Gist keeps subscriber chat ids and filters off the public repo. Configured via
env vars (set as Actions secrets):
  STATE_GIST_TOKEN — a PAT with the `gist` scope
  STATE_GIST_ID    — id of a secret gist holding a `headstart-state.json` file
"""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Any

_FILE = "headstart-state.json"


def _api(method: str, url: str, token: str, data: bytes | None = None) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "headstart-bot",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def load_state() -> dict[str, Any]:
    token = os.environ["STATE_GIST_TOKEN"]
    gist_id = os.environ["STATE_GIST_ID"]
    gist = _api("GET", f"https://api.github.com/gists/{gist_id}", token)
    content = ((gist.get("files") or {}).get(_FILE) or {}).get("content")
    if not content:
        return {}
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return {}


def save_state(state: dict[str, Any]) -> None:
    token = os.environ["STATE_GIST_TOKEN"]
    gist_id = os.environ["STATE_GIST_ID"]
    body = json.dumps(
        {"files": {_FILE: {"content": json.dumps(state, ensure_ascii=False, indent=2)}}}
    ).encode("utf-8")
    _api("PATCH", f"https://api.github.com/gists/{gist_id}", token, data=body)
