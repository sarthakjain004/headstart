# ADR-0142: Subscription opt-outs survive record replacement

- Status: Accepted
- Date: 2026-09-12
- Amends ADR-0035, ADR-0043 and ADR-0069.

Deleting a seeded Subscription looked like first enrollment on the next alerts
run. An unreadable record also returned `None`, resetting its Watermark/token.
A delivery finishing after a settings edit could overwrite the newer intent.

The user chose to keep HF, add durable opt-out intent, and make Subscription writes
conditional. `subscription_opt_outs/{id}.json` survives removal of the Subscription.
Only explicit enable clears it; routine Invite reconciliation and edits cannot.
Stop/enable changes to the marker and record land in one commit. Confirmed missing
files return `None`; unreadable Subscription/opt-out state raises and blocks writes.

Saved-set adoption, edits, deletion, email toggles and unsubscribe batch their related
records against one immutable snapshot too. Set flags and the Subscription projection
commit together, closing ADR-0043's multi-file toggle crash window. During such an
action an unreadable set is fatal to that action, rather than silently omitted from
the set of flags to change. Invalid requests need no snapshot fetch; it is acquired
on the first validated record read or write.

Each Store remembers the bytes it read. Before changing a Subscription it compares
the affected record and marker at one immutable Hub revision, then commits against
that revision using `parent_commit`. A changed record yields a conflict; a failure
after the comparison does not trigger a blind retry. An unseen record may be
created, never overwritten. HTTP callers receive 409 for a known conflict or 503
for an unavailable/uncertain operation; the alerts run isolates failure per Account.

This uses the Hub's documented [conditional commit contract](https://huggingface.co/docs/huggingface_hub/package_reference/hf_api#huggingface_hub.HfApi.create_commit).
The adapter is verified with a simulated revision change; no production write was
performed for validation. Repository-wide concurrent commits can conservatively
reject unrelated edits too. Retry means a new read of current intent.

Delivery remains at-least-once: a successful send followed by a rejected Watermark
write may be delivered again. A message already in flight cannot be recalled.
This closes stale Subscription overwrites and partial Saved-set projection changes.
Moving to a transactional database is not part of this change. The atomic Store
scope reads its starting snapshot; it does not expose staged writes to subsequent
reads and is used once per Space action.
