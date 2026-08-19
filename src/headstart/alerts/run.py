"""Send one Digest per Subscription, once per successful pipeline run (ADR-0035).

`python -m headstart.alerts.run`, wired to the pipeline by `.github/workflows/alerts.yml`.

The order below is the ADR's at-least-once decision, and it is deliberate: the Watermark
advances **only after the Transport has accepted the Digest**. A crash between the two
re-sends at most one capped Digest next run; the opposite order would swallow a window
silently and tell nobody.

Two enrolment paths feed one run — the hand-edited allowlist and the Telegram bot's
approvals (ADR-0038) — and each Subscription is delivered by exactly one Transport, so a
single Watermark stays meaningful.

One Subscription's failure never stops the rest, and a Subscription that fails is left with
its Watermark untouched, so the next run retries exactly the window it missed. Progress
prints per Subscription and flushes, per the repo's streaming-output rule — a run that
printed only at the end would hide which record was mid-flight when it died.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import replace

from . import digest, space_query, transports
from .shortlist import CAP, shortlist
from .store import Invite, Store, Subscription, now_iso, subscription_id

_REQUIRED = ("SUBSCRIBERS_REPO", "SUBSCRIBERS_TOKEN")


class TransportUnset(Exception):
    """This Subscription's transport has no secrets, so the run skips it rather than fails.

    Distinct from a delivery failure on purpose: an unconfigured transport is the
    dark-until-configured state the whole feature is built around, not something to retry
    and report red every run.
    """


def transport_for(sub: Subscription, config: Mapping[str, str]):
    """This Subscription's Transport, or `TransportUnset` if its secrets are absent.

    Resolved *before* the search rather than at delivery. A Space query is the expensive
    part of a run — `space_query.K` rows with a retry budget — and paying it for a channel
    that cannot send is pure waste; worse, if the Space is down those records raise and
    count as failures, turning the run red for a channel the repo never configured, which
    is precisely what `TransportUnset` exists to avoid.
    """
    transport = transports.for_subscription(sub)
    missing = transport.missing(config)
    if missing:
        raise TransportUnset(f"{transport.name}: {', '.join(missing)} unset")
    return transport


def send_one(
    sub: Subscription,
    store: Store,
    space: str,
    config: Mapping[str, str],
) -> int:
    """Search, shortlist, send, then advance the Watermark. Returns the rows delivered.

    The next Watermark is stamped *before* the search, not after the send. Stamping it
    afterwards would silently drop every Job indexed while this Subscription was being
    searched and delivered — they are older than the new Watermark, so no later run would
    ever offer them. Taking the stamp first can only re-offer a row next run, which is the
    at-least-once direction this feature chose.
    """
    transport = transport_for(sub, config)
    cutoff = now_iso()
    rows = space_query.newly_seen(space, sub, sub.watermark)
    # Ranked twice over, deliberately: the message carries the best `CAP`, the spreadsheet
    # carries every fresh row the Space returned. That is what makes the attachment worth
    # opening rather than a copy of what they just read — `space_query.K` is 100 against a
    # cap of 30, so two thirds of the matches were being discarded unseen.
    ranked = shortlist(rows, sub.watermark, cap=len(rows))
    picked = ranked[:CAP]
    if not picked:
        return 0

    transport.send(
        sub,
        picked,
        transports.Payload(digest.to_xlsx(ranked), len(ranked)),
        space,
        config,
    )
    # Only now: the send is the thing that must not be lost.
    sub.watermark = cutoff
    store.put(sub)
    print(f"[alerts] {sub.id}: delivered by {transport.name}", flush=True)
    return len(picked)


def subscription_for(
    invite: Invite, store: Store, accounts_with_sets: frozenset[str] = frozenset()
) -> Subscription | None:
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
    account = subscription_id(invite.email)
    if account in accounts_with_sets:
        # ADR-0069: once an Account keeps Saved sets, the Space's sets endpoints own the
        # Subscription's content — they are the only writer that keeps the projection in step
        # (ADR-0043), which is why `/subscribe` already 409s in this configuration. Re-projecting
        # from the Invite here would overwrite the emailing set's Query on every run: the Matches
        # tab would show ✉ on one set while the Digest delivered another, permanently and with
        # no way for the person to correct it. The record is read-only to this run.
        return store.get(account)

    existing = store.get(account)
    if existing is None:
        seed = invite.query or invite.default_query
        if not seed:
            return None  # invited, but nothing to search for until they sign in
        fresh = replace(
            Subscription.create(invite.email, seed, invite.search_filters),
            telegram=invite.telegram,
        )
        store.put(fresh)
        return fresh

    revised = existing
    # An entry may carry `filters` without a `query` (ADR-0035). Gating on `invite.query` meant
    # editing only the filters never took effect; fall back to the query already stored rather
    # than blanking it.
    wanted_query = invite.query or existing.query
    if (
        wanted_query != existing.query
        or invite.search_filters != existing.search_filters
    ):
        revised = existing.revised(wanted_query, invite.search_filters)
    # The allowlist owns the transport outright — there is no self-serve way to set a chat
    # id, so the file is the only thing that can ever be right about it.
    if invite.telegram != revised.telegram:
        revised = replace(revised, telegram=invite.telegram)
    if revised is not existing:
        store.put(revised)
    return revised


def telegram_subscriptions(store: Store) -> list[Subscription]:
    """Subscriptions the Telegram bot created, which no allowlist entry names (ADR-0038).

    Two enrolment paths reach one run: the allowlist, which the owner hand-edits, and the
    bot, where the master approves people who then choose their own Query. Bot records carry
    a chat id and no address, so they are exactly the ones with `telegram` set and `email`
    empty — an allowlisted person given a chat id by hand has both, and is already covered
    by their Invite. Selecting on that rather than on `telegram` alone is what stops the
    same person being delivered to twice in one run.

    A record with no Query yet — approved but hasn't sent `/q` — is skipped by `main` the
    same way an Invite with no Query is.
    """
    return [sub for sub in store.all() if sub.telegram and not sub.email]


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
    # Each transport is read but not demanded: a repo with only Telegram configured should
    # run Telegram and skip the email Subscriptions, not refuse to start (ADR-0038).
    config = {
        name: os.environ.get(name, "")
        for name in ("RESEND_API_KEY", "ALERTS_SENDER", "TELEGRAM_BOT_TOKEN")
    }

    invites = store.invites()
    chats = telegram_subscriptions(store)
    # One listing for the whole run — see Store.accounts_with_sets (ADR-0069).
    with_sets = frozenset(store.accounts_with_sets())
    print(f"[alerts] {len(invites)} invited, {len(chats)} via telegram", flush=True)

    sent = failed = skipped = 0
    for item in (*invites, *chats):
        # An Invite still has to be resolved to a Subscription, and may create one; a
        # record the bot made is already the thing to deliver. Resolution reads and may
        # write the store, so it sits inside the guard too: one person must not be able to
        # stop everybody else's Digest.
        from_allowlist = isinstance(item, Invite)
        sub_id = subscription_id(item.email) if from_allowlist else item.id
        try:
            sub = subscription_for(item, store, with_sets) if from_allowlist else item
            if sub is None or not sub.query:
                print(f"[alerts] {sub_id}: no query set yet - skipped", flush=True)
                skipped += 1
                continue
            count = send_one(sub, store, space, config)
        except TransportUnset as exc:
            # Not a failure: this is the dark-until-configured state the feature is built
            # around, so it must not turn the run red — it would be red on every run of a
            # repo deliberately using only one channel.
            print(f"[alerts] {sub_id}: {exc} - skipped", flush=True)
            skipped += 1
            continue
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

    print(
        f"[alerts] done: {sent} digest(s) sent, {skipped} skipped, {failed} failed",
        flush=True,
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
