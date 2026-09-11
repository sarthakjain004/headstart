"""The one Account this server may read, and the only way it reaches the store (ADR-0136).

The single-account rule is not a check somewhere in the request path — it is this class's
shape. An ``Account`` is constructed from an address and then *holds* the derived id; the two
methods it offers take a document id and nothing else. There is no parameter anywhere in this
module through which another Account could be named, so the server above cannot pass one by
mistake and a future tool cannot add one without editing this file and noticing why.

`alerts.store.Store` is the client. There is no second one: the paths, the traversal guard,
the size bound and the "unreadable is not absent" rule all live there already, and a résumé
reader with its own copy of them is a résumé reader that will disagree with the product about
which file is whose.
"""

from __future__ import annotations

import os
from typing import Any

from ..alerts.store import Store, subscription_id

#: The address whose résumés this server serves. There is no default and no fallback: an
#: unset value is a refusal to start reading, never a guess at whose account is meant.
EMAIL_VAR = "HEADSTART_ACCOUNT_EMAIL"

#: The Subscriptions dataset and a token for it — the same two names `alerts.run` and
#: `alerts.bot` already read, so a machine set up for either is set up for this.
REPO_VAR = "SUBSCRIBERS_REPO"
TOKEN_VAR = "SUBSCRIBERS_TOKEN"


class Unconfigured(Exception):
    """The server has no credentials, so it can read nobody's résumés.

    Carried rather than raised at startup: an MCP server that exits on a missing variable
    shows up in the client as a server that will not connect, which says nothing about what
    is wrong. The server starts, lists its tools, and says this sentence when one is called.
    """


class Account:
    """One Account's Résumé documents. Bound at construction; nothing rebinds it."""

    def __init__(self, email: str, store: Store) -> None:
        self.email = email
        #: `subscription_id`, the same derivation the Space and the alerts run use — so this
        #: reads the exact directory the browser's sync wrote to, and the address itself
        #: never appears in a path.
        self.id = subscription_id(email)
        self._store = store

    def documents(self) -> list[dict[str, Any]]:
        """Every Account copy this Account keeps, newest edit first, exactly as stored."""
        return self._store.resumes_for(self.id)

    def document(self, document_id: str) -> dict[str, Any] | None:
        """One Account copy by id, or None when this Account keeps no such document.

        None covers three facts the store cannot tell apart — no such document, a corrupt
        record, the Hub unreachable — which is `Store.get_resume`'s documented behaviour and
        not something to re-decide here. The reader above says "this Account has no résumé
        with that id", which is true of all three from where it stands.
        """
        return self._store.get_resume(self.id, document_id)


def open_account(env: dict[str, str] | None = None) -> Account:
    """The Account named by the environment. Raises :class:`Unconfigured` with a message
    written to be read by whoever has to fix it."""
    env = os.environ if env is None else env
    missing = [name for name in (EMAIL_VAR, REPO_VAR, TOKEN_VAR) if not env.get(name)]
    if missing:
        raise Unconfigured(
            "This server reads one account's résumés and it does not know whose. "
            f"Set {', '.join(missing)} in the MCP server's environment "
            f"({EMAIL_VAR} is the address you sign in to HeadStart with; "
            f"{REPO_VAR} and {TOKEN_VAR} are the Subscriptions dataset and a token that "
            "can read it). See docs/agents/resume-mcp-server.md."
        )
    return Account(env[EMAIL_VAR], Store(env[REPO_VAR], env[TOKEN_VAR]))
