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

from . import digest, mail, space_query
from .shortlist import shortlist
from .store import Invite, Store, Subscription, now_iso, subscription_id

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
    """Search, shortlist, send, then advance the Watermark. Returns the rows mailed.

    The next Watermark is stamped *before* the search, not after the send. Stamping it
    afterwards would silently drop every Job indexed while this Subscription was being
    searched and mailed — they are older than the new Watermark, so no later run would ever
    offer them. Taking the stamp first can only re-offer a row next run, which is the
    at-least-once direction this feature chose.
    """
    cutoff = now_iso()
    rows = space_query.newly_seen(space, sub, sub.watermark)
    picked = shortlist(rows, sub.watermark)
    if not picked:
        return 0

    body = digest.render(sub, picked, unsubscribe_url(space, sub))
    mail.send(api_key, sender, sub.email, body, digest.to_xlsx(picked))
    # Only now: the send is the thing that must not be lost.
    sub.watermark = cutoff
    store.put(sub)
    return len(picked)


def subscription_for(invite: Invite, store: Store) -> Subscription | None:
    """The Subscription this Invite should send against, created on first sight.

    An Invite that names a Query of its own is **authoritative**: the allowlist is the
    owner's one edit path, so changing a Query there takes effect next run. It is applied
    through `revised`, which keeps the Watermark and the unsubscribe token — so no window is
    skipped and no link in mail already delivered goes dead. The file's `default_query` is
    deliberately *not* authoritative: it seeds somebody with no record yet, and is ignored
    for anyone who has one, so a default can never overwrite what they chose at sign-in.

    Both the created and the revised record are stored **before any send**. Its Watermark
    starts at now and `send_one` persists only after a Digest goes out — so a first run that
    matches nothing would otherwise leave the record unwritten, re-create it with a fresh
    Watermark next run, and restart the window forever. For a revision the stakes are lower
    (the search already used the new Query) but the stored record is the durable view of
    intent, and the one `/subscribe` reads back, so letting it drift stale is its own bug.
    """
    existing = store.get(subscription_id(invite.email))
    if existing is None:
        seed = invite.query or invite.default_query
        if not seed:
            return None  # invited, but nothing to search for until they sign in
        fresh = Subscription.create(invite.email, seed, invite.search_filters)
        store.put(fresh)
        return fresh
    if invite.query and (
        invite.query != existing.query
        or invite.search_filters != existing.search_filters
    ):
        revised = existing.revised(invite.query, invite.search_filters)
        store.put(revised)
        return revised
    return existing


def main() -> int:
    missing = [name for name in _REQUIRED if not os.environ.get(name)]
    if missing:
        print(
            f"alerts not configured (missing {', '.join(missing)}) - skipping",
            flush=True,
        )
        return 0

    space = os.environ.get("SPACE_URL", "https://imposeidon-headstart-search.hf.space")
    store = Store(os.environ["SUBSCRIBERS_REPO"], os.environ["SUBSCRIBERS_TOKEN"])
    api_key, sender = os.environ["RESEND_API_KEY"], os.environ["ALERTS_SENDER"]

    # The allowlist drives the run, rather than being a filter over stored Subscriptions.
    # That inverts one thing deliberately: an address struck off the list is never reached
    # at all, so removal stops mail without hunting down the record — and an address added
    # to it starts receiving without anyone signing in.
    invites = store.invites()
    print(f"[alerts] {len(invites)} invited", flush=True)

    sent = failed = 0
    for invite in invites:
        # `subscription_for` reads and may write the store, so it sits inside the guard
        # too: resolving one person must not be able to stop everybody else's Digest.
        sub_id = subscription_id(invite.email)
        try:
            sub = subscription_for(invite, store)
            if sub is None:
                print(
                    f"[alerts] {sub_id}: invited but no query yet - skipped", flush=True
                )
                continue
            count = send_one(sub, store, space, api_key, sender)
        except Exception as exc:  # noqa: BLE001 — one bad Subscription must not stop the rest
            failed += 1
            print(f"[alerts] {sub_id}: FAILED {type(exc).__name__}: {exc}", flush=True)
            continue
        sent += bool(count)
        print(
            f"[alerts] {sub_id}: {count or 'no'} new match(es)"
            f"{' - digest sent' if count else ''}",
            flush=True,
        )

    print(f"[alerts] done: {sent} digest(s) sent, {failed} failed", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
