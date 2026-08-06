"""Subscriptions, one file per record, in the private `headstart-subscribers` dataset (ADR-0035).

`Store` is the whole interface: `all`, `get`, `put`, `remove`, `invites`, `allowlist`. Behind
it sit the repo layout, the JSON shape, and the HF client.

Two records, deliberately distinct. An **Invite** is what the owner writes by hand — an
address, optionally the Query to run for it. A **Subscription** is the state that Invite
produces: the same Query plus a Watermark and an unsubscribe token, which are machinery
nobody should have to hand-edit. `alerts.run.subscription_for` is the one place that turns
the first into the second.

**One file per Subscription is a correctness decision, not a scaling one.** A single JSON
blob would be read-modify-write from two writers — the Space on subscribe/unsubscribe and
the alerts run on every Watermark advance — so two signups inside one window would lose a
record, at two users. Per-record paths make writes disjoint by construction.

**The id is derived from the address, not random**, so re-subscribing updates a record
instead of growing a second one. It is a hash: the address itself never appears in a path.

The four HF calls are module-level functions so tests can replace them the way
`tests/test_http.py` replaces its session; everything above them is pure.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

from .access import normalize

PREFIX = "subscriptions/"
_ID = re.compile(r"[0-9a-f]{16}")  # exactly what subscription_id mints
ALLOWLIST_PATH = "subscriptions/allowlist.json"

# The Space `/search` parameters a Subscription may carry. `seen_within` is deliberately
# absent — it filters `first_seen`, which is exactly what the Watermark already decides, so
# honouring both would fight. `posted_within` is kept: it filters `posted_at` (when the
# employer posted), an independent constraint the user set and expects to survive.
ALLOWED_SEARCH_FILTERS = frozenset(
    {
        "remote",
        "max_years",
        "ats",
        "etype",
        "india",
        "location",
        "company",
        "has_salary",
        "posted_within",
    }
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def subscription_id(email: str) -> str:
    """A stable, non-reversing id for an address — re-subscribing overwrites one record.

    Normalized through `access.normalize`, the same function the allowlist compares with:
    if the two ever disagreed, an allowlisted address could get a second record."""
    return hashlib.sha256(normalize(email).encode("utf-8")).hexdigest()[:16]


def _kept(search_filters: dict[str, Any]) -> dict[str, str]:
    """Only the Search filters a Subscription may carry, as strings."""
    return {
        k: str(v)
        for k, v in search_filters.items()
        if k in ALLOWED_SEARCH_FILTERS and v != ""
    }


@dataclass
class Subscription:
    """One person's standing request: a verified address, a Query, Search filters, a Watermark."""

    id: str
    email: str
    query: str
    search_filters: dict[str, str] = field(default_factory=dict)
    created_at: str = ""
    watermark: str = ""  # last Digest accepted for delivery
    unsubscribe_token: str = ""

    @classmethod
    def create(
        cls,
        email: str,
        query: str,
        search_filters: dict[str, Any],
        when: str | None = None,
    ) -> "Subscription":
        """A new Subscription whose Watermark starts *now*, so its first Digest carries
        only what appears after signup — nobody is mailed the backlog."""
        stamp = when or now_iso()
        return cls(
            id=subscription_id(email),
            email=normalize(email),
            query=query.strip(),
            search_filters=_kept(search_filters),
            created_at=stamp,
            watermark=stamp,
            unsubscribe_token=secrets.token_urlsafe(24),
        )

    def revised(self, query: str, search_filters: dict[str, Any]) -> "Subscription":
        """This Subscription with a new Query and Search filters, keeping everything else.

        Re-subscribing must not mint a fresh `unsubscribe_token`: every Digest already
        delivered carries the old one, and rotating it would 404 the unsubscribe link in
        mail already sitting in someone's inbox. The Watermark is kept for the same reason
        in reverse — resetting it to now would silently skip whatever arrived since the
        last Digest."""
        return replace(self, query=query.strip(), search_filters=_kept(search_filters))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Subscription":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})

    def path(self) -> str:
        return f"{PREFIX}{self.id}.json"


@dataclass(frozen=True)
class Invite:
    """One allowlisted address, and optionally the Query the owner chose for it.

    An Invite is *permission plus intent*; a Subscription is the running state that
    permission produces (Watermark, unsubscribe token). Keeping them separate is what lets
    the allowlist stay a hand-edited file: it names people, not machinery.
    """

    email: str
    query: str = ""  # set explicitly on this entry — authoritative, overrides a sign-in
    search_filters: dict[str, str] = field(default_factory=dict)
    default_query: str = (
        ""  # the file's fallback — seeds a new record, never revises one
    )


def parse_allowlist(data: Any) -> list[Invite]:
    """The Invites in an allowlist document.

    Two entry shapes, both valid. A **bare string** is the original shape and still means
    "may sign in and choose their own Query" — that is the self-serve path. An **object**
    (`{"email": …, "query": …, "filters": {…}}`) additionally carries what to search for, so
    the owner can enrol someone who will never sign in at all.

    A top-level `default_query` is kept **separate from an entry's own Query** rather than
    folded into it, and the distinction is load-bearing. An entry's own Query is a statement
    about that person, so it overrides whatever they last chose. A default is a statement
    about nobody in particular — so it may *seed* someone who has no record yet, but must
    never revise one. Folding the two together silently overwrote a signed-in person's own
    Query on every run, forever.

    Anything unrecognised is dropped rather than raising: this file is hand-edited, and one
    malformed entry must not deny everybody — that failure belongs to a missing file, where
    it is deliberate, not to a stray comma.
    """
    entries = data.get("allowed") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        return []
    default = str(data.get("default_query") or "") if isinstance(data, dict) else ""

    out: list[Invite] = []
    seen: set[str] = set()
    for entry in entries:
        if isinstance(entry, str):
            email, query, raw_filters = entry, "", None
        elif isinstance(entry, dict):
            email = str(entry.get("email") or "")
            query = str(entry.get("query") or "")
            raw_filters = entry.get("filters")
        else:
            continue
        # Normalized and de-duplicated here rather than at the comparison, because this
        # list now *drives* sending: two casings of one address would mail that person
        # twice, and both would derive the same `subscription_id`. First entry wins.
        address = normalize(email)
        if not address or address in seen:
            continue
        seen.add(address)
        out.append(
            Invite(
                email=address,
                query=query.strip(),
                search_filters=_kept(
                    raw_filters if isinstance(raw_filters, dict) else {}
                ),
                default_query=default.strip(),
            )
        )
    return out


def _hf(token: str):
    from huggingface_hub import HfApi

    return HfApi(token=token)


def _list_files(repo: str, token: str) -> list[str]:
    return _hf(token).list_repo_files(repo, repo_type="dataset")


def _read(repo: str, path: str, token: str) -> bytes:
    from huggingface_hub import hf_hub_download

    local = hf_hub_download(repo, path, repo_type="dataset", token=token)
    with open(local, "rb") as handle:
        return handle.read()


def _write(repo: str, path: str, data: bytes, token: str) -> None:
    import io

    _hf(token).upload_file(
        path_or_fileobj=io.BytesIO(data),
        path_in_repo=path,
        repo_id=repo,
        repo_type="dataset",
    )


def _delete(repo: str, path: str, token: str) -> None:
    _hf(token).delete_file(path_in_repo=path, repo_id=repo, repo_type="dataset")


class Store:
    """Subscriptions in one private dataset. Construct with the repo id and a write token."""

    def __init__(self, repo: str, token: str) -> None:
        self._repo = repo
        self._token = token

    def all(self) -> list[Subscription]:
        """Every stored Subscription. A record that will not parse is skipped, not fatal —
        one bad file must not stop everyone else's Digest."""
        out: list[Subscription] = []
        for path in _list_files(self._repo, self._token):
            if not path.startswith(PREFIX) or path == ALLOWLIST_PATH:
                continue
            try:
                out.append(
                    Subscription.from_dict(
                        json.loads(_read(self._repo, path, self._token))
                    )
                )
            except Exception as exc:  # noqa: BLE001 — a malformed record is data, not a crash
                print(f"[alerts] skipping unreadable {path}: {exc}", flush=True)
        return out

    def get(self, sub_id: str) -> Subscription | None:
        """One Subscription by id, or None. Reads a single file rather than the whole set.

        The id is checked against the shape `subscription_id` mints *before* it reaches a
        repo path: it arrives from a query string, and an unchecked value would let a caller
        name any file in the repo (`allowlist`, or a `../` traversal). Parsing is inside the
        guard too, so a file that is not a Subscription answers None rather than raising."""
        if not _ID.fullmatch(sub_id):
            return None
        try:
            data = json.loads(_read(self._repo, f"{PREFIX}{sub_id}.json", self._token))
            return Subscription.from_dict(data)
        except Exception:  # noqa: BLE001 — absent, unreadable and not-a-Subscription are one answer
            return None

    def put(self, sub: Subscription) -> None:
        _write(
            self._repo,
            sub.path(),
            json.dumps(sub.to_dict(), indent=2).encode("utf-8"),
            self._token,
        )

    def remove(self, sub_id: str) -> None:
        _delete(self._repo, f"{PREFIX}{sub_id}.json", self._token)

    def invites(self) -> list[Invite]:
        """Everyone the allowlist names, with whatever Query the owner set for them.

        Unreadable or missing reads as empty, and an empty list invites nobody — the same
        deny-by-default direction `allowlist` has always had, so a fetch blip can never
        open the feature up or start mailing strangers."""
        try:
            data = json.loads(_read(self._repo, ALLOWLIST_PATH, self._token))
        except Exception as exc:  # noqa: BLE001 — absent list must deny, not raise
            print(f"[alerts] allowlist unreadable ({exc}) — denying all", flush=True)
            return []
        return parse_allowlist(data)

    def allowlist(self) -> list[str]:
        """Just the permitted addresses, for `access.is_allowed` at signup."""
        return [invite.email for invite in self.invites()]
