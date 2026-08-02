#!/usr/bin/env bash
# Boot the Space: best-effort llm-router tunnel, then the app — ALWAYS the app (ADR-0032).
#
# Deliberately NO `set -e`. The tunnel serves one optional endpoint (/resume-to-query); search
# is the product. A router outage, a bad key, or a dead box must cost that one endpoint a 503,
# never the container. docs/LLM_API.md's recipe gates boot on the tunnel — this is the
# degrade-don't-die variant of it.
#
# Secrets/env (Space → Settings → Variables and secrets):
#   OCI_SSH_KEY        private key for the router box; unset → no tunnel, résumé endpoint 503s
#   LLM_ROUTER_SSH     user@host of the box — a secret, NOT defaulted here: this repo is
#                      public and the box's address does not belong in it
#   LITELLM_MASTER_KEY router auth, read by llm_router.py
#   RESUME_PASSWORD    the beta gate, read by app.py
#   LLM_ROUTER_MODEL   optional, read by llm_router.py (default agent-default)
#   LLM_ROUTER_BASE    optional, read by llm_router.py (default http://127.0.0.1:4000/v1 — the
#                      tunnel's local end; overriding it strands the -L 4000 forward below)

if [ -n "$OCI_SSH_KEY" ] && [ -z "$LLM_ROUTER_SSH" ]; then
  echo "WARN: OCI_SSH_KEY set but LLM_ROUTER_SSH unset — no tunnel; résumé search will 503" >&2
fi
if [ -n "$OCI_SSH_KEY" ] && [ -n "$LLM_ROUTER_SSH" ]; then
  mkdir -p ~/.ssh
  printf '%s\n' "$OCI_SSH_KEY" > ~/.ssh/router_key
  chmod 600 ~/.ssh/router_key

  # autossh keeps the tunnel alive across the Space's sleep/wake cycles; -f backgrounds it.
  autossh -M 0 -f -N \
    -o "ServerAliveInterval=30" -o "ServerAliveCountMax=3" \
    -o "ExitOnForwardFailure=yes" -o "StrictHostKeyChecking=accept-new" \
    -i ~/.ssh/router_key \
    -L 4000:127.0.0.1:4000 "$LLM_ROUTER_SSH" \
    || echo "WARN: tunnel failed to start — résumé search will 503" >&2

  # Wait briefly for the router to answer through the tunnel; report either way and move on.
  # python, not curl: curl isn't in the slim image and python already is.
  for _ in $(seq 1 15); do
    python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:4000/health/liveliness', timeout=2)" \
      2>/dev/null && { echo "router tunnel up"; break; }
    sleep 2
  done
elif [ -z "$OCI_SSH_KEY" ]; then
  echo "OCI_SSH_KEY unset — no router tunnel; résumé search will 503" >&2
fi

exec python app.py
