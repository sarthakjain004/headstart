# Durability follow-up for #154 — APPLIED (#157)

`_EIGHTFOLD_ALIAS_LOSERS` and `_is_eightfold_alias_loser` now live in `check_liveness.py`, wired
into `run_pass`'s pre-probe gate beside `is_nonprod`. What follows is the sketch that preceded
them, kept for the reasoning; the code is the source of truth, and the "regenerate, don't
hand-maintain" note at the bottom is still open.

`scripts/validate/dedupe_eightfold_aliases.py --apply` marks a duplicate hostname `dead` in the
ledger, which gets its rows evicted by the next `index prune`. That marking is **not durable**:
`check_liveness.py` re-probes `dead` entries on `DEAD_TTL_DAYS` (90 days by default), these hosts
genuinely answer 200 with real jobs, and a plain re-probe would flip them back to `live` —
silently re-admitting the duplicate.

ADR-0034 solved the identical shape of problem (non-prod boards resurrecting on re-probe) with a
pre-probe skip, `_NONPROD_TENANTS`, checked before any HTTP is spent. The same shape belongs here.
Sketch, to be adapted into `check_liveness.py` by whoever next touches that file — **do not apply
this verbatim without reading the surrounding code first**, since the file has moved since this
was drafted:

```python
# Eightfold tenants that are a live duplicate of another live tenant under a different vanity
# hostname — same underlying board, same _EF_GROUP_ID, byte-identical job-id set (#154). A plain
# re-probe would find them genuinely answering 200 and resurrect the duplicate; skip them before
# spending an HTTP request, mirroring _NONPROD_TENANTS's shape (ADR-0034).
_EIGHTFOLD_ALIAS_LOSERS = {
    "nvidia.eightfold.ai",
    "qualcomm.eightfold.ai",
    "micron.eightfold.ai",
    "hsbc.eightfold.ai",
    "vodafone.eightfold.ai",
    "dsm.eightfold.ai",
}


def _is_eightfold_alias_loser(ats: str, tenant: str) -> bool:
    return ats == "eightfold" and tenant in _EIGHTFOLD_ALIAS_LOSERS
```

...called from the same pre-probe gate `_is_nonprod` already sits behind, so a loser dies for
free on every check without ever reaching the network — durable regardless of what the live host
would actually answer.

**This list must be regenerated, not hand-maintained forever.** It is a snapshot of
`dedupe_eightfold_aliases.py`'s 2026-08-16 output. New eightfold tenants can form new alias
clusters over time (a company onboarding a second vanity domain, for instance), which this static
set cannot see. Re-run `dedupe_eightfold_aliases.py` periodically — it re-verifies overlap fresh
every time — and fold any new losers into this set, or turn it into a small data file
(`config/eightfold_alias_losers.json` or similar) that `check_liveness.py` reads at import time,
so refreshing it doesn't require a code change to this file every time.
