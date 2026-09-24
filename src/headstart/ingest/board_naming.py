"""Name a Board for a person to read: its stated company, a curated alias, or its humanised tenant.

Two stages name Boards — `hot_boards` for the Hot tab's ranked rows and `company_directory`
for every Board the Trends company filter can pick — and both must name a Board the same way,
or the company a user picks on one tab is spelled differently on the other.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

from headstart import company_name, log
from headstart.board_identity import ats_of
from headstart.company_name import LABEL_NOISE, tidy
from headstart.ingest.board_operator import tenant

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


# The words of a site name: EXTERNAL_CAREERS, CorporateCareers, Maxis-Early-Careers.
_WORDS = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")


def stated_name(company: str, board: str) -> str | None:
    """The name a Board *asserts* — a curated alias or a cased company name — else None.

    None means `display_name` has to derive one from the slug. The company directory reads the
    difference to name a multi-Board company by its stated spelling ("NVIDIA"), not the tidied
    slug of whichever Board sorts first ("Nvidia").
    """
    # The hand-written names of `config/company_names.csv`, which the scrape also serves as the
    # company. They are the company directory's only cross-ATS identity too (ADR-0185, ADR-0209).
    alias = company_name.curated(board)
    if alias:
        return alias
    stated = (company or "").strip()
    if not stated or _names_the_board(stated, board):
        return None
    # A cased name is a real one; a lowercase one is too unless it repeats the Board's own key
    # ("incident.io" on `gem:incident`, ADR-0209). Never re-case or trim either.
    if stated != stated.lower() or not company_name.echoes_board(stated, board):
        return stated
    return None


def _names_the_board(name: str, board: str) -> bool:
    """Whether a company string is really a piece of the Board's own key, not a company.

    Four shapes reach the company column: a Workday site (`_is_site`), a noise label of the
    host (`_is_noise_label`), Taleo Business Edition's ledger spelling, `GATEWAYVENT:77@phg…`,
    and the ATS's own title for the site (`_VENDOR_TITLES`).
    """
    ledger_spelling = name.lower().startswith(tenant(board).lower() + ":")
    return (
        ledger_spelling
        or _is_site(name, board)
        or _is_noise_label(name, board)
        or name.strip().casefold() in _VENDOR_TITLES.get(ats_of(board), ())
    )


#: The title an ATS gives a site it hosts, which reaches the company column as if it named the
#: employer. Measured 2026-09-24: "Oracle Taleo" named four directory companies (Scripps, PMG,
#: two PruittHealth hosts) and "Successfactors" one (TTTech). Workday and Greenhouse are left out
#: on purpose: each hires on its own product, and there the title is the company.
_VENDOR_TITLES: Final[dict[str, frozenset[str]]] = {
    "taleo_enterprise": frozenset({"oracle taleo", "taleo"}),
    "taleo_be": frozenset({"oracle taleo", "taleo"}),
    "successfactors": frozenset({"successfactors", "sap successfactors"}),
}


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
    return any(word.casefold() in LABEL_NOISE for word in _WORDS.findall(name))


def _is_noise_label(name: str, board: str) -> bool:
    """Whether ``name`` is one of the Board's own host labels, made only of board words.

    `www`, `apply` and `careers-apply` name the host; `six-group` and `sap` name the company,
    so they stay.
    """
    labels = {label.lower() for label in tenant(board).split(".") if label}
    words = [word for word in name.lower().split("-") if word]
    return name.lower() in labels and all(word in LABEL_NOISE for word in words)


def _slug(board: str) -> str:
    """The board_key's slug without a URL scheme, so its first segment is a host or tenant."""
    return company_name.without_scheme(board.split(":", 1)[-1])


def display_name(company: str, board: str) -> str | None:
    """A name a person can read, without inventing one.

    Two Boards in five carry an ATS slug rather than a resolved company name (ADR-0114), and a
    slug is very often a hostname — `careers.wipro.com`, `careers-inc.nttdata.com` — which on a
    company leaderboard reads as a bug. (`www.amazon.jobs` was the stock example until the eight
    Single source scrapers began declaring `BaseScraper.COMPANY`; they now arrive named, so this
    function no longer has to rescue them.) This drops the labels of a host that name the board or the
    vendor and keeps the first that names the company (`tidy`), reading the slug through
    `tenant`, so a Taleo Business Edition Board is named by its `org` and not its shared pod,
    and spells it the way the scrape's own fallback does (`company_name.humanised_text`,
    ADR-0209): `hpe/ACJobSite` is "HPE", not "Hpe".

    It stops there. `swa.wd1.myworkdayjobs.com/external` derives "SWA" and not "Southwest
    Airlines", because that expansion is not in the data; it comes from the curated map
    (`config/company_names.csv`), which a person wrote. A stated, mixed-case name is returned
    untouched, so `CI&T` and `HCLTech` survive — unless it is the Board's own site or ledger
    spelling (`stated_name`), which names the board, not the company.

    None where the Board states nothing and its tenant is only a code; callers skip such a
    Board rather than list it without a name.
    """
    named = stated_name(company, board)
    if named:
        return named
    # A tenant that is only a code (Oracle's pods, ADP's GUIDs) names nothing, and an empty
    # name is shown as none rather than as the code (ADR-0209).
    if not company_name.humanised(board):
        return None
    # Everything below tidies a *slug*. The cased-name guard above must not reach it: a slug
    # carries capitals of its own (`micron/External`), and treating those as a company name
    # returned the raw slug, path and all.
    name = (company or "").strip()
    from_slug = tidy(tenant(board))
    # A company that only names the board yields to the slug: SuccessFactors rows carry a noise
    # label of their own host (`www`, `apply`, `careers-apply`), and a Workday row can carry its
    # site. Only where the slug has a real name to give, though: `jobs.sap.com` tidies to
    # nothing, so its "sap" stays.
    if name and from_slug and _names_the_board(name, board):
        name = ""
    # A name of its own is spelled like the scrape's fallback; with none, this *is* that
    # fallback, so the Hot and Trends tabs and the served table spell a Board alike (ADR-0209).
    named = tidy(name) if name else None
    return (
        company_name.humanised_text(named) if named else company_name.humanised(board)
    )
