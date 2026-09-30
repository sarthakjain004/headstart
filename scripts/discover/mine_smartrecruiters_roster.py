#!/usr/bin/env python3
"""SmartRecruiters miner: the MIT-licensed roster of Board identifiers kept in `amikai/openings-mcp`.

`internal/provider/smartrecruiters/companies.yaml` in github.com/amikai/openings-mcp (MIT, its own
commit says it came from prefix enumeration of SmartRecruiters' company-name search plus posting-API
verification) lists 11,258 `{company, company_identifier}` pairs. `company_identifier` is the
case-sensitive Board slug the posting API takes (`/v1/companies/{identifier}/postings`), so a row is
already a candidate in the ledger's own spelling. Measured 2026-09-29, 59.8% of it is unheld.

This reads one public file, one GET of 772 KB (cached under `data/wayback-ats/`; `--refresh` reads
it again). It never calls SmartRecruiters' `sr-jobs/company-lookup`: that is an unofficial endpoint
of the job-search app, not part of the documented posting API, so the miner takes the same names
from the roster instead. (The research, section 2.1, reads SmartRecruiters' candidate-terms clause
on automated access as governing candidate accounts, and the lookup returns company names and ids,
not user data; whether to call the lookup anyway is the owner's decision.) Liveness is settled
afterwards by `check_liveness.py`, through the posting API the scraper itself reads.

Drops `SRTest*` (SmartRecruiters' own test clients) and stages the rest through `candidate_pool`,
in the ledger's majority spelling `careers.smartrecruiters.com/{identifier}`.

Run:   python -u scripts/discover/mine_smartrecruiters_roster.py [--refresh]
Then:  LIVENESS_WORKERS=8 python scripts/validate/check_liveness.py --dir data/wayback-ats smartrecruiters
"""

from __future__ import annotations

import argparse
import json
import re

from candidate_pool import POOL, stage_unheld
from discovery_fetch import fetch_cached

ROSTER_URL = "https://raw.githubusercontent.com/amikai/openings-mcp/HEAD/internal/provider/smartrecruiters/companies.yaml"
CACHE = POOL / ".smartrecruiters_roster.yaml"

_IDENTIFIER = re.compile(r"^\s*company_identifier:\s*(.+?)\s*$", re.MULTILINE)


def identifiers(text: str) -> list[str]:
    """Every `company_identifier` in the roster, `SRTest*` clients dropped, order kept.

    Read with a regex rather than a YAML parser: the file is one `company_identifier:` line per
    company, quoted as JSON when it needs quoting, and PyYAML is no declared dependency."""
    found = [
        str(json.loads(raw) if raw.startswith('"') else raw.strip("'"))
        for raw in _IDENTIFIER.findall(text)
    ]
    return [ident for ident in found if not ident.lower().startswith("srtest")]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--refresh", action="store_true", help="read the roster again")
    args = parser.parse_args()
    text = fetch_cached(ROSTER_URL, CACHE, refresh=args.refresh).decode("utf-8")
    idents = identifiers(text)
    print(f"roster: {len(idents)} identifiers after dropping SRTest*", flush=True)
    rows = [(ident, f"careers.smartrecruiters.com/{ident}") for ident in idents]
    print(
        dict(stage_unheld("smartrecruiters", rows, "smartrecruiters_roster")),
        flush=True,
    )


if __name__ == "__main__":
    main()
