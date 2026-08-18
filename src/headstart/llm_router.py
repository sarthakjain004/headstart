"""The one way this project calls an LLM: ``ask(prompt)`` against the llm-router (ADR-0032).

The router is a LiteLLM deployment on a private box — it holds the provider keys, picks the
real model behind a router-exposed name, and runs its own fallback chain (one provider
rate-limits, it switches to another). This client is therefore deliberately thin:

- **No retries.** The router already fails over between providers; a retry loop here would
  re-send a request the router is *already* retrying, turning one rate-limit into several.
  Absence of retry is a decision, not an oversight.
- **No SDK.** One POST to an OpenAI-compatible ``/chat/completions`` needs only stdlib
  ``urllib``, which keeps this importable (and testable) everywhere — CI's quality job
  installs no extras.
- **Not publicly reachable.** The router binds localhost on its box; off-box callers reach it
  through an SSH tunnel whose local end is the default base URL below (``docs/LLM_API.md``).

Every failure — unreachable tunnel, timeout, HTTP error, malformed reply — raises
:class:`RouterUnavailable`; callers map it to their own "temporarily off" response and must
not retry either.

Env: ``LLM_ROUTER_BASE`` (default ``http://127.0.0.1:4000/v1``), ``LITELLM_MASTER_KEY``
(the router's auth), ``LLM_ROUTER_MODEL`` (default ``agent-default``).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

# Measured against the live router (2026-08-02, the résumé prompt): a normal completion takes
# 2-5s, but provider fallback can stretch one to 79.6s — which sat over the old 60s ceiling and
# surfaced as 503s. One generation, no streaming.
_TIMEOUT = 120
# The router's default model *thinks*, and thinking bills into the completion budget: the same
# one-line answer measured 599-706 completion tokens, ~590+ of them hidden reasoning. The old
# budget of 200 was eaten before the answer began — every reply came back `finish_reason:
# length`, truncated to fragments like "Java, GraphQL". Sized so reasoning plus a one-line
# answer never hits the ceiling; visible output stays one line because the prompt says so.
_MAX_TOKENS = 4000


class RouterUnavailable(Exception):
    """The router could not be reached or did not return a usable completion."""


def ask(prompt: str) -> str:
    """One prompt in, the completion text out — or :class:`RouterUnavailable`."""
    base = os.environ.get("LLM_ROUTER_BASE", "http://127.0.0.1:4000/v1")
    body = json.dumps(
        {
            "model": os.environ.get("LLM_ROUTER_MODEL", "agent-default"),
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": _MAX_TOKENS,
            "temperature": 0,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {os.environ.get('LITELLM_MASTER_KEY', '')}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            reply = json.load(resp)
        return reply["choices"][0]["message"]["content"]
    except Exception as exc:
        raise RouterUnavailable(f"{type(exc).__name__}: {exc}") from exc
