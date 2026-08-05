"""Send one Digest per Subscription, once per successful pipeline run (ADR-0035).

`python -m headstart.alerts.run`, wired to the pipeline by `.github/workflows/alerts.yml`.

The order below is the ADR's at-least-once decision, and it is deliberate: the Watermark
advances **only after Resend has accepted the Digest**. A crash between the two re-sends at
most one capped Digest next run; the opposite order would swallow a window silently and
tell nobody — the failure `bot.py` still has.

One Subscription's failure never stops the rest, and a Subscription that fails is left with
its Watermark untouched, so the next run retries exactly the window it missed. Progress
prints per Subscription and flushes, per the repo's streaming-output rule — a run that
printed only at the end would hide which address was mid-flight when it died.
"""

from __future__ import annotations

import os
import sys

from . import digest as digest_mod
from . import mail, search
from .access import is_allowed
from .shortlist import shortlist
from .store import Store, Subscription, now_iso

_REQUIRED = ("SUBSCRIBERS_REPO", "SUBSCRIBERS_TOKEN", "RESEND_API_KEY", "ALERTS_SENDER")


def unsubscribe_url(base: str, sub: Subscription) -> str:
    return f"{base.rstrip('/')}/unsubscribe?id={sub.id}&token={sub.unsubscribe_token}"


def send_one(
    sub: Subscription,
    store: Store,
    space: str,
    api_key: str,
    sender: str,
) -> int:
    """Search, shortlist, send, then advance the Watermark. Returns the rows mailed."""
    rows = search.newly_seen(space, sub, sub.notified_at)
    picked = shortlist(rows, sub.notified_at)
    if not picked:
        return 0

    body = digest_mod.render(sub, picked, unsubscribe_url(space, sub))
    try:
        attachment = digest_mod.to_xlsx(picked)
    except ImportError:  # xlsxwriter absent — the body still carries every link
        print(
            "[alerts] xlsxwriter missing; sending without the spreadsheet", flush=True
        )
        attachment = None

    mail.send(api_key, sender, sub.email, body, attachment)
    # Only now: the send is the thing that must not be lost.
    sub.notified_at = now_iso()
    store.put(sub)
    return len(picked)


def main() -> int:
    missing = [name for name in _REQUIRED if not os.environ.get(name)]
    if missing:
        print(f"alerts not configured (missing {', '.join(missing)}) - skipping")
        return 0

    space = os.environ.get("SPACE_URL", "https://imposeidon-headstart-search.hf.space")
    store = Store(os.environ["SUBSCRIBERS_REPO"], os.environ["SUBSCRIBERS_TOKEN"])
    api_key, sender = os.environ["RESEND_API_KEY"], os.environ["ALERTS_SENDER"]

    subscriptions = store.all()
    allowlist = store.allowlist()
    print(f"[alerts] {len(subscriptions)} subscription(s)", flush=True)

    sent = failed = 0
    for sub in subscriptions:
        # Re-checked every run so revoking access stops mail already flowing.
        if not is_allowed(sub.email, allowlist):
            print(f"[alerts] {sub.id}: not allowlisted - skipped", flush=True)
            continue
        try:
            count = send_one(sub, store, space, api_key, sender)
        except Exception as exc:  # noqa: BLE001 — one bad Subscription must not stop the rest
            failed += 1
            print(f"[alerts] {sub.id}: FAILED {type(exc).__name__}: {exc}", flush=True)
            continue
        sent += bool(count)
        print(
            f"[alerts] {sub.id}: {count or 'no'} new match(es)"
            f"{' - digest sent' if count else ''}",
            flush=True,
        )

    print(f"[alerts] done: {sent} digest(s) sent, {failed} failed", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
