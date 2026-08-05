"""Subscriptions, one file per record, in the private `headstart-subscribers` dataset (ADR-0035).

`Store` is the whole interface: `all`, `get`, `put`, `remove`, `allowlist`. Behind it sit the
repo layout, the JSON shape, and the HF client.

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
import secrets
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

PREFIX = "subscriptions/"
ALLOWLIST_PATH = "subscriptions/allowlist.json"

# The Space `/search` parameters a Subscription may carry. Recency is deliberately absent:
# `posted_within`/`seen_within` would fight the Watermark, which is what decides "new" here.
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
    }
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def subscription_id(email: str) -> str:
    """A stable, non-reversing id for an address — re-subscribing overwrites one record."""
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()[:16]


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
            email=email.strip().lower(),
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
        """One Subscription by id, or None. Reads a single file rather than the whole set."""
        try:
            data = json.loads(_read(self._repo, f"{PREFIX}{sub_id}.json", self._token))
        except Exception:  # noqa: BLE001 — absent or unreadable are the same answer here
            return None
        return Subscription.from_dict(data)

    def put(self, sub: Subscription) -> None:
        _write(
            self._repo,
            sub.path(),
            json.dumps(sub.to_dict(), indent=2).encode("utf-8"),
            self._token,
        )

    def remove(self, sub_id: str) -> None:
        _delete(self._repo, f"{PREFIX}{sub_id}.json", self._token)

    def allowlist(self) -> list[str]:
        """The permitted addresses. Unreadable or missing reads as empty — and
        `access.is_allowed` refuses everyone on an empty list, which is the intent."""
        try:
            data = json.loads(_read(self._repo, ALLOWLIST_PATH, self._token))
        except Exception as exc:  # noqa: BLE001 — absent list must deny, not raise
            print(f"[alerts] allowlist unreadable ({exc}) — denying all", flush=True)
            return []
        entries = data.get("allowed") if isinstance(data, dict) else data
        return [str(e) for e in entries] if isinstance(entries, list) else []
