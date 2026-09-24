"""Name a Board for a person to read: its stated company, a curated alias, or its tidied slug.

Two stages name Boards — `hot_boards` for the Hot tab's ranked rows and `company_directory`
for every Board the Trends company filter can pick — and both must name a Board the same way,
or the company a user picks on one tab is spelled differently on the other.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

from headstart import log

_log = log.get(__name__)


def board_names(db: Path, table_name: str) -> dict[str, str]:
    """``board_key -> the company name its rows carry``, or an empty map if unreadable.

    A real name is missing on two Boards in five: 60.3% of the 34,223 Boards measured on
    2026-09-24 carry a cased company name (ADR-0114, ADR-0172), and the rest fall back to their
    slug. Unreadable rather than fatal: each caller decides what an empty map means for it.

    Each caller scans the whole table, `hot_boards` and `company_directory` once per run each.
    That is affordable because of where they run: `role_trends`, the stage before both on the
    same merge VM, already reads every row *including the 768-d vector column*, and two string
    columns over the same rows are strictly cheaper. If either moves off that VM, revisit it.
    """
    try:
        import lancedb

        handle = lancedb.connect(str(db)).open_table(table_name)
        rows = (
            handle.search()
            .select(["id", "company"])
            .limit(handle.count_rows())
            .to_arrow()
        )
    except Exception as exc:  # noqa: BLE001 - a missing or half-written table must not be fatal
        _log.warning(f"no company names readable from {table_name} ({exc})")
        return {}
    names: dict[str, str] = {}
    for job_id, company in zip(
        rows["id"].to_pylist(), rows["company"].to_pylist(), strict=True
    ):
        if company:
            names.setdefault(job_id.rsplit(":", 1)[0], company)
    return names


#: Host labels that name the *board* rather than the company, and so are never the answer.
#: Vendor labels and TLDs sit here too: `micron.wd5.myworkdayjobs.com` and
#: `lockheed.jobs.hr.cloud.sap` both have to reduce to their first real word.
_LABEL_NOISE = frozenset(
    {
        "www",
        "careers",
        "career",
        "jobs",
        "job",
        "apply",
        "join",
        "opportunities",
        "hire",
        "hiring",
        "talent",
        "work",
        "working",
        "recruiting",
        "recruitment",
        "internal",
        "internaljobs",
        "external",
        "search",
        "inc",
        "ltd",
        "llc",
        "corp",
        "group",
        "global",
        "en",
        "us",
        # vendor hosts and the public suffixes behind them
        "myworkdayjobs",
        "icims",
        "eightfold",
        "zohorecruit",
        "openings",
        "taleo",
        "tbe",
        "oraclecloud",
        "ocs",
        "fa",
        "sap",
        "cloud",
        "hr",
        "wd",
        "smartrecruiters",
        "com",
        "net",
        "org",
        "io",
        "co",
        "ai",
        "in",
        "eu",
        "uk",
        "de",
        "ca",
    }
)
_WD_POD = re.compile(r"^wd\d+$")  # micron.wd5.myworkdayjobs.com
# Taleo Enterprise's slug is a whole URL.
_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)
# The words of a site name: EXTERNAL_CAREERS, CorporateCareers, Maxis-Early-Careers.
_WORDS = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")


#: Hand-written names for Boards whose slug cannot produce one, keyed by board_key.
#:
#: Same principle as `board_operator`'s curated list and the same bounded scope: these are
#: Boards seen at the *head of a lens*, where a wrong name is read by everyone. The slug simply
#: does not carry the company — `workday:bah/BAH_Jobs` is Booz Allen Hamilton and
#: `workday:globalhr/REC_RTX_Ext_Gateway` is RTX — and no derivation recovers that.
#:
#: It also does the work cross-ATS identity would: Lockheed Martin reaches the Expansion lens
#: on **both** Eightfold and SuccessFactors, and before this map they ranked first and second
#: as "Lockheed Martin" and "Lockheed". Mapping both to one name lets `_collapse_same_company`
#: see them as one company. That is a display-level patch over a real gap — the index has no
#: cross-ATS Board identity (`index_plan.evict_duplicate` groups *within* a Board) — so a pair
#: not listed here still shows twice. Add pairs as the head of a lens surfaces them.
#:
#: It is also the company directory's only cross-ATS identity (ADR-0185): Boards sharing an
#: alias are one company there. That makes them one *pick*, not additive counts — Lockheed's
#: two Boards list the same requisitions (1,248 of 1,249 distinct titles shared, 2026-09-23).
DISPLAY_ALIASES: Final[dict[str, str]] = {
    "eightfold:lockheedmartin.eightfold.ai": "Lockheed Martin",
    "successfactors:lockheed.jobs.hr.cloud.sap": "Lockheed Martin",
    "workday:globalhr/REC_RTX_Ext_Gateway": "RTX",
    "workday:bah/BAH_Jobs": "Booz Allen Hamilton",
    "workday:swa/external": "Southwest Airlines",
    "workday:caci/external": "CACI",
    "workday:gdit/External_Career_Site": "GDIT",
    "workday:ngc/Northrop_Grumman_External_Site": "Northrop Grumman",
    "successfactors:careers.hcltech.com": "HCLTech",
    "successfactors:careers.capgemini.com": "Capgemini",
    "successfactors:careers.wipro.com": "Wipro",
    "successfactors:careers-inc.nttdata.com": "NTT Data",
}


def stated_name(company: str, board: str) -> str | None:
    """The name a Board *asserts* — a curated alias or a cased company name — else None.

    None means `display_name` has to derive one from the slug. The company directory reads the
    difference to name a multi-Board company by its stated spelling ("NVIDIA"), not the tidied
    slug of whichever Board sorts first ("Nvidia").
    """
    alias = DISPLAY_ALIASES.get(board)
    if alias:
        return alias
    stated = (company or "").strip()
    if stated and stated != stated.lower() and not _is_site(stated, board):
        return stated  # a real, cased company name — never re-case or trim it
    return None


def _is_site(name: str, board: str) -> bool:
    """Whether ``name`` is the Board's own site segment, worded as a site rather than a brand.

    A Workday site can land in the company column, cased (Boeing's `EXTERNAL_CAREERS`). The
    site alone is not the tell: `jiostar/JioStar` and `gresearch/g-research` carry a better
    name in the site than in the tenant. Measured over the 32,829 named Boards, the sites that
    are not names are the ones worded like one — `CorporateCareers`, `OCLC_Careers`,
    `DarktaceExternal` — so a noise word is what decides.
    """
    if name.casefold() not in {part.casefold() for part in _slug(board).split("/")[1:]}:
        return False
    return any(word.casefold() in _LABEL_NOISE for word in _WORDS.findall(name))


def _host(board: str) -> set[str]:
    """The labels of the slug's host (or its whole first segment, when it has no dots)."""
    head = _slug(board).split("/", 1)[0]
    return {label.casefold() for label in head.split(".") if label}


def _slug(board: str) -> str:
    """The board_key's slug without a URL scheme, so its first segment is a host or tenant."""
    return _SCHEME.sub("", board.split(":", 1)[-1])


def display_name(company: str, board: str) -> str:
    """A name a person can read, without inventing one.

    Two Boards in five carry an ATS slug rather than a resolved company name (ADR-0114), and a
    slug is very often a hostname — `careers.wipro.com`, `careers-inc.nttdata.com` — which on a
    company leaderboard reads as a bug. (`www.amazon.jobs` was the stock example until the eight
    Single source scrapers began declaring `BaseScraper.COMPANY`; they now arrive named, so this
    function no longer has to rescue them.) This drops the labels of a host that name the board or the
    vendor and keeps the first that names the company.

    Picking the *first non-noise label* rather than the registrable domain is deliberate, and
    both conventions appear in the data: `careers-inc.nttdata.com` puts the company second,
    while `lockheed.jobs.hr.cloud.sap` puts it first. Taking the label before the public suffix
    reads the latter as "Cloud"; taking the first label reads the former as "Careers-Inc".

    It stops at tidying. `swa.wd1.myworkdayjobs.com/external` becomes "Swa" and not "Southwest
    Airlines", because that expansion is not in the data and a leaderboard that guesses company
    names is worse than one that shows an honest slug. Anything already mixed-case is returned
    untouched, so `CI&T` and `HCLTech` survive.
    """
    named = stated_name(company, board)
    if named:
        return named
    # Everything below tidies a *slug*. The cased-name guard above must not reach it: a slug
    # carries capitals of its own (`micron/External`), and treating those as a company name
    # returned the raw slug, path and all.
    name = (company or "").strip()
    # A company that is only a piece of the Board's own slug names the board, not the company:
    # SuccessFactors rows carry their host's first label (`www`, `apply`, `job`), and a
    # Workday row can carry its site. Tidying the whole slug finds the company instead.
    if not name or name.casefold() in _host(board) or _is_site(name, board):
        name = board.split(":", 1)[-1]
    # Taleo Enterprise's slug is a whole URL; split on "/" first, it tidied to "Https:".
    head = _SCHEME.sub("", name).split("/", 1)[0]
    if "." in head:
        labels = [
            label
            for label in head.split(".")
            if label and label not in _LABEL_NOISE and not _WD_POD.match(label)
        ]
        # A prefixed label still carries the company after its noise word (`careers-inc` ->
        # `inc`, dropped above; `jobs-bylight` -> `bylight`).
        for label in labels:
            parts = [p for p in label.split("-") if p and p not in _LABEL_NOISE]
            if parts:
                head = "-".join(parts)
                break
        else:
            head = labels[0] if labels else head
    return head.replace("-", " ").replace("_", " ").strip().title() or name
