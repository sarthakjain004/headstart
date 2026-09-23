#!/usr/bin/env python3
"""Write ClearCompany's alias ledger: labels that serve one HRM Direct account (ADR-0182).

`dedupe_boards.py` finds a Board published twice by following its redirect. ClearCompany has no
redirect to follow: every label an account owns answers `xml.php` with the *whole account*, under
its own brand. The signal is the reqs instead — req ids are platform-wide integers, so two labels
that share any req are one account (measured 2026-09-23: 1,881 sharing pairs over 1,304 hiring
Boards, every one sharing its whole set).

Per account the label kept is a label whose board does not redirect to a division (the account's
own site), else the one whose default division (`cust_sort1`) has the lowest id (the account's
first), alphabetical on a tie. Every other label is written to
`data/validate/aliases/clearcompany.csv` with signal `shared-reqs`.

Reads the liveness ledger's live hiring rows; one xml.php per label, then one board-page GET per
clustered label. Replaces the alias file, so re-run it after every ledger refresh.

    PYTHONPATH=src python scripts/validate/clearcompany_shared_accounts.py
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from headstart import board_aliases, http, liveness
from headstart.scrapers.base import USER_AGENT
from headstart.scrapers.clearcompany import decode_hrm_bytes, feed_reqs

ATS = "clearcompany"
#: The feed is the expensive request (up to 3.7 MB, ~20 s); 8 wide kept a full pass free of the
#: connection refusals a wider burst once drew.
_WORKERS = 8
_HEADERS = {"User-Agent": USER_AGENT}


def _reqs(label: str) -> set[str] | None:
    r = http.fetch(
        "GET",
        f"https://{label}.hrmdirect.com/employment/xml.php",
        headers=_HEADERS,
        timeout=120,
    )
    if r.status_code != 200:
        return None
    return {req for req, _rows in feed_reqs(decode_hrm_bytes(r.content))}


def _division(label: str) -> int | None:
    """The default division the label's board redirects to, or None for an account-level site."""
    r = http.fetch(
        "GET",
        f"https://{label}.hrmdirect.com/employment/job-openings.php",
        headers=_HEADERS,
        timeout=60,
        allow_redirects=False,
    )
    m = re.search(r"cust_sort1=(\d+)", r.headers.get("location") or "")
    return int(m.group(1)) if m else None


def _parallel(fn, labels):
    out = {}
    with ThreadPoolExecutor(_WORKERS) as ex:
        futures = {ex.submit(fn, label): label for label in labels}
        for n, fut in enumerate(as_completed(futures), 1):
            label = futures[fut]
            try:
                out[label] = fut.result()
            except http.RequestsError as exc:
                sys.exit(
                    f"{label}: {exc} — a partial scan would bury the wrong labels; re-run"
                )
            if n % 100 == 0:
                print(f"  {fn.__name__}: {n}/{len(futures)}", flush=True)
    return out


def main() -> None:
    ledger = liveness.load(liveness.dir_for(ROOT) / f"{ATS}.csv")
    hiring = sorted(
        v.tenant for v in ledger.values() if v.status == liveness.LIVE and v.jobs
    )
    print(f"{len(hiring)} hiring labels", flush=True)
    sets = {k: v for k, v in _parallel(_reqs, hiring).items() if v}

    owner: dict[str, str] = {}
    parent: dict[str, str] = {label: label for label in sets}

    def root(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for label, reqs in sets.items():
        for req in reqs:
            if req in owner:
                parent[root(label)] = root(owner[req])
            else:
                owner[req] = label
    accounts: dict[str, list[str]] = defaultdict(list)
    for label in sets:
        accounts[root(label)].append(label)
    shared = [labels for labels in accounts.values() if len(labels) > 1]
    print(f"{len(shared)} accounts span {sum(map(len, shared))} labels", flush=True)

    divisions = _parallel(_division, [label for labels in shared for label in labels])
    today = datetime.now(UTC).date().isoformat()
    aliases = []
    for labels in shared:
        keep = min(
            labels, key=lambda x: (divisions[x] is not None, divisions[x] or 0, x)
        )
        aliases += [
            board_aliases.Alias(ATS, label, keep, "shared-reqs", keep, today)
            for label in labels
            if label != keep
        ]
    out = board_aliases.path_for(liveness.dir_for(ROOT), ATS)
    board_aliases.write(out, aliases)
    print(f"wrote {len(aliases)} buried labels to {out.relative_to(ROOT)}", flush=True)


if __name__ == "__main__":
    main()
