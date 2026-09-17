"""Classify a Job as a software/tech role, and filter a jobs dir down to the tech subset (ADR-0017).

Every job is scraped, but only software/tech roles get embedded, indexed, and shown — that is where
the expensive work (the embedding model, the vector index) lives. This gate decides which jobs pass.

It is deliberately **recall-biased**: a non-tech job creeping through is acceptable, but dropping a
real tech job is not. Classification is on the ``title`` (+ ``department``) via regex — cheap enough
for millions of jobs. (An LLM is far too costly per job; it is the *verification* layer instead —
see ``scripts/filter/verify_tech.py``, the reasoning gate that samples the dropped pile.)

Precedence (first match wins):

  1. a strong, unambiguous software signal  -> tech      (overrides any disqualifier)
  2. a generic role token (engineer/developer/…) *with* a non-software qualifier (mechanical,
     sales, civil, …) in the title, or in a
     department that names a discipline       -> not tech
  3. a generic role token alone              -> tech      (recall: keep the ambiguous ones)
  4. a clearly-technical department, unless
     it names a hiring function, or means
     something other than software, or the
     title names a different profession       -> tech      (recall booster for vague titles)
  5. otherwise                               -> not tech

**Rules 1-3 read the title; only rule 4 reads the department.** Until 2026-09-17 rules 1 and 2 ran
over ``title + department``, so a department could settle a title question — "Software development"
contains "software dev", so a Content Creator in it scored a strong software signal. The department
has one rule, and that is where its guards live.
"""

from __future__ import annotations

import json
import logging
import multiprocessing
import os
import re
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

# Bumped whenever a pattern change below moves the tech/not-tech line for input that's already
# been scraped and filtered — the same discipline as `doc_prep.DERIVATIONS_VERSION`, and for the
# same reason: this gate's output feeds `role_trends`, whose per-tick counts silently absorb a
# widened or narrowed regex as if the market moved. Reading this value once per tick lets a
# reader tell "we changed who counts" from "conditions changed" instead of conflating the two.
# 2 (2026-09-17, `git log 277b5e2a..1fd0f843 -- src/headstart/tech_filter.py`): rules 1 and 2
# stopped reading the department, rule 4 stopped promoting a title
# that names a different profession, and the strong list gained the roles the department had been
# covering for. Net **+1.38%** on a 489,661-posting corpus (107,684 -> 109,172; +6,673 in,
# -5,185 out), so the composition moves much further than the total — see
# docs/tech-filter/2026-09-17_the-title-decides.md. This is exactly the shape the counter exists
# for: `role_trends` would otherwise read "Security Officer" leaving the index as the market
# shedding security jobs.
TECH_FILTER_VERSION = 2

# 1. Strong, software-specific signals. A match here means tech regardless of any disqualifier.
_STRONG_TERMS = [
    r"software (engineer|developer|dev|architect)",
    r"\b(swe|sde|sdet)\b",
    r"full[\s-]?stack",
    r"back[\s-]?end",
    r"front[\s-]?end",
    r"\bfullstack\b",
    r"web (developer|engineer)",
    r"mobile (developer|engineer)",
    r"(ios|android) (developer|engineer)",
    r"machine learning",
    r"deep learning",
    r"generative ai",
    r"\bllm\b",
    r"large language model",
    r"computer vision",
    r"\bnlp\b",
    r"\b(ai|ml)[\s/&,-]*(engineer|scientist|researcher|developer|ops|platform)",
    r"data (engineer|scientist)",
    r"data science",
    r"\bmlops\b",
    r"\bdevops\b",
    r"dev ops",
    r"\bsre\b",
    r"site reliability",
    r"platform engineer",
    r"infrastructure engineer",
    r"cloud (engineer|architect|developer)",
    r"(security|appsec) engineer",
    r"application security",
    r"(qa|test) (engineer|automation)",
    r"quality engineer",
    r"\bsdet\b",
    r"automation engineer",
    r"embedded (software|engineer|developer|systems)",
    r"\bfirmware\b",
    (
        r"(software|systems?|solutions?|technical|technology|cloud|data|security|platform"
        r"|enterprise|integration|application|infrastructure|network|devops|ai|ml|iam|api"
        r"|java|\.net|dotnet|python|salesforce|servicenow|sap|azure|aws|oracle|mobile|frontend"
        r"|backend|full[\s-]?stack) architect"
    ),
    r"\bprogrammer\b",
    r"\bblockchain\b",
    r"smart contract",
    r"\bweb3\b",
    r"game (developer|engineer|programmer)",
    r"api (developer|engineer)",
    # `systems?`, not `systems`: "System Administrator" and "System Engineer" are the commoner
    # singular spellings and were falling through — measured on a jobvite board serving
    # "IT System Administrator (TS/SCI with Polygraph)", a cleared sysadmin role, as non-tech.
    r"(systems?|network|database|devops|cloud|linux) (administrator|admin|engineer)",
    r"engineering manager",
    r"(director|vp|vice president|head) of (engineering|ai|ml|data|software|platform|infrastructure|technology|security)",
    r"\bcto\b",
    r"tech(nical)? lead",
    r"(ai|ml|data|software|cloud|security|systems|chief|principal|staff) technologist",
    r"developer (advocate|relations)",
    r"\bdevrel\b",
    r"(react|angular|vue|node|python|java|golang|rust|kubernetes) (developer|engineer)",
    # Roles whose title names the discipline without using "engineer"/"developer" — these were
    # reaching the index only because their *department* said "Technology", which is what makes
    # them invisible to a pre-detail gate (ADR-0166).
    r"(penetration|software|automation|qa|game|performance) tester",
    r"\bpentest(er)?\b",
    r"scrum master",
    r"(systems?|business systems|technical|data|security|soc|cyber|network|application) analyst",
    (
        r"\b(it|ict)[\s/-]+(manager|director|support|specialist|analyst|technician"
        r"|administrator|lead|engineer|operations|officer|consultant|coordinator)\b"
    ),
    r"(information|business) systems",
    r"\binformation security\b",
    r"\binfosec\b",
    r"help[\s-]?desk",
    r"desktop support",
    (
        r"(technology|technical) (support|operations|lead|specialist|writer|consultant"
        r"|program manager|project manager|product manager)"
    ),
    r"(power ?bi|tableau|looker|qlik) (developer|analyst|specialist|consultant)",
    (
        r"(salesforce|servicenow|sharepoint|sap|abap|apex|workday|netsuite|dynamics) "
        r"(developer|administrator|consultant|analyst|specialist|architect|lead)"
    ),
    r"\b(etl|rpa|middleware|integration) (developer|specialist|consultant|lead)\b",
    r"database (administrator|analyst|specialist|developer)",
    r"\bdba\b",
]
_STRONG = re.compile("|".join(_STRONG_TERMS), re.IGNORECASE)

# 2. Generic role tokens — ambiguous on their own; tech unless a non-software qualifier is present.
_GENERIC = re.compile(r"\b(engineer|engineering|developer|programmer)\b", re.IGNORECASE)

# 3. Non-software qualifiers that turn a generic "…engineer" into a non-tech role. Kept to the
#    unambiguous non-software engineering disciplines + sales.
#
#    This is read from the title, and from the department only after _ORG_NOT_ROLE is stripped —
#    see there. It used to be read from the concatenation, on the premise that the disqualifier
#    "never drops a genuine software role (which would already have tripped a strong signal above
#    anyway)". That premise is untrue by construction: a title tripping a strong signal returns
#    before this branch, so the only titles the disqualifier ever sees are the ambiguous ones the
#    strong list does not cover — and for those, an org label was deciding the answer.
_NON_SOFTWARE = re.compile(
    r"\b("
    r"sales|mechanical|civil|chemical|electrical|industrial|biomedical|biochemical|structural"
    r"|aerospace|petroleum|geotechnical|mining|marine|nuclear|agricultural|metallurg|materials"
    r"|hardware|hvac|plumbing|welding|drilling|mechanic|manufacturing"
    r")\b",
    re.IGNORECASE,
)

# 3b. Non-software words that name the ORG rather than the role, and so must not veto from a
#     department. A hardware org employs the engineers whose work is code — RTL design, design
#     verification, physical design are all HDL/EDA, i.e. software by any reading — so "Hardware
#     Engineering" in `department` says who the role reports to, not what it is. Measured over the
#     332,383-row pre-filter snapshot this recovers 287 rows, 0 lost (ADR-0068).
#
#     `sales` is deliberately NOT here: a "Solutions Engineer" under Sales is the pre-sales role
#     this filter already classifies non-tech when the title says so ("Sales Engineer"), so there
#     the department corroborates rather than misleads.
_ORG_NOT_ROLE = re.compile(r"\bhardware\b", re.IGNORECASE)

# 4. Departments that clearly denote software/tech — a recall booster for otherwise-vague titles.
_TECH_DEPT = re.compile(
    r"\b(engineering|software|technology|developer|data|platform|infrastructure"
    r"|information technology|\bit\b|r&d|devops|security)\b",
    re.IGNORECASE,
)

# 4b. Departments naming a *hiring* function, which must not act as that recall booster: such a
#     label says who does the recruiting, not what discipline the role is in, so a technical word
#     landing inside one is incidental. "Human Data Recruitment" is a team that recruits humans to
#     produce data, and `\bdata\b` matching it promoted 2,427 crowdwork listings into the tech
#     index off a single Board. Reasoning, measurements and rejected alternatives: ADR-0087.
#
#     Scoped to rule 4, and not a disqualifier — a title naming a software role still passes on
#     its own signal at rules 1-3.
#
#     `sourcing` is deliberately NOT here, though it is a hiring term of art: in Department labels
#     it overwhelmingly means procurement, not candidates. All 10 live occurrences in a
#     418-Board, 22,573-job survey were supply-chain ("Category Sourcing", "Sourcing & Quality",
#     "Global Sourcing", "Product Sourcing"), so including it would veto on the wrong meaning.
# 4c. Departments whose technical-looking word does not mean software. `security` promotes
#     physical guards ("Security Officer" x1,338 on one sweep) and `engineering` promotes the
#     trades out of "Engineering & Facilities" (plumbers, painters, carpenters, electricians).
#     Neither title carries a software signal of its own, so rule 4 is the only thing keeping
#     them and it is keeping them for the wrong reason.
#
#     Scoped to rule 4 exactly like :data:`_HIRING_DEPT`, and not a disqualifier: a genuine
#     software title inside a facilities org still passes on its own signal at rules 1-3, which
#     is what keeps "Software Engineer, Facilities Systems" working.
#
#     **Its marginal contribution is 1,457 of the change's 5,185 removals**, measured by ablation
#     over the 2026-09-17 corpus — not the 3,030 rows it matches, because `_NON_TECH_ROLE` already
#     refuses most of those by title. Security Officer, Plumber, Painter, Carpenter and Electrician
#     are all in that list too and would be refused without this rule; what only *this* rule
#     catches is the title that names no profession at all — a bare "Technician", "General
#     Technician", "Maintenance Manager", "Security Site Supervisor", or the hotel trades
#     ("Laundry & Kitchen Technician", "Technicien(-ne) de maintenance").
#     See docs/tech-filter/2026-09-17_the-title-decides.md.
_NOT_TECH_DEPT = re.compile(
    # No trailing \b: the plural is the common spelling ("Security Officers" is the department,
    # "Security Officer" the title) and a closing boundary fails on it.
    # `security guard`, not bare `guard`: the bare word matched the department
    # "Guardian Data Platform".
    r"\b(security officer|loss prevention|physical security|security guard|safety"
    r"|facilit|maintenance|janitor|custodial|housekeep|hotel)",
    re.IGNORECASE,
)

# 4d. Roles whose title says plainly that they are not software, so a technical department must
#     not promote them. This is what makes "Administrative Assistant" in "Software Engineering"
#     a non-tech job: the department says who they sit with, the title says what they do, and on
#     this question the title wins.
#
#     Deliberately a list of *clear* non-software roles, not of merely vague ones — the gate stays
#     recall-biased, so "Analyst" or "Associate" in a Software Engineering department is still
#     kept. Only titles that name a different profession are refused.
_NON_TECH_ROLE = re.compile(
    r"\b("
    r"administrative assistant|admin assistant|executive assistant|personal assistant"
    r"|receptionist|secretar\w+|office (manager|assistant|administrator)"
    r"|account(ant|ing)|bookkeep\w+|payroll|auditor|tax \w+"
    r"|sales (executive|manager|representative|associate|consultant|director)"
    r"|business development|account (manager|executive)|telecaller|telesales"
    r"|customer (service|support|success|care)|call cent\w+"
    # `human resources`, not `\bhr\b`: the initialism refused `HR Technology Manager` while
    # `HRIS Specialist` stayed — an inconsistency with no defence.
    r"|recruit\w+|human resources|talent acquisition"
    r"|content (writer|creator)|copywriter|social media|graphic design\w*"
    r"|nurse|nursing|physician|pharmacist|therapist|caregiver|medical assistant"
    r"|driver|warehouse|forklift|cashier|janitor\w*|housekeep\w+|custodian"
    r"|security officer|security guard|loss prevention"
    # No bare `server`: it refused `Windows Server Administrator`, `SQL Server Specialist` and
    # `Server Support Specialist`, all of which the filter kept before. In job titles the machine
    # sense dominates and the restaurant sense is rare.
    r"|chef|cook|bartender|barista|waiter|waitress"
    r"|teacher|tutor|instructor|lecturer"
    # No `technician - \w+`: it reads as "a technician of some trade" but matched
    # `Technician - Software` and `Technician - Network Operations`, and was brittle anyway —
    # it needed spaces and an ASCII hyphen, so `Technician-HVAC` and `Technician – HVAC` missed.
    r"|plumber|painter|carpenter|electrician|welder|machinist"
    r")\b",
    re.IGNORECASE,
)

_HIRING_DEPT = re.compile(
    r"\b(recruit\w*|staffing|talent acquisition)\b", re.IGNORECASE
)


@dataclass(frozen=True, slots=True)
class Verdict:
    """Whether a job is a tech role, and the rule that decided it (for the verification gate)."""

    is_tech: bool
    reason: str


def classify(title: str | None, department: str | None = None) -> Verdict:
    """Decide whether a job is a software/tech role, with the reason (recall-biased; see module doc)."""
    dept = (department or "").strip()
    title_text = (title or "").strip()
    # Rules 1 and 2 read the **title**, not the title and department concatenated. Reading both
    # let a department decide a title question: "Software development" contains "software dev",
    # so `Content Creator` in it scored a strong software signal, and `Engineering & Facilities`
    # contains "engineering", so `Plumber` scored a generic one. The department already has its
    # own rule below, with its own guards; letting it also fire rules 1-2 counted it twice and
    # bypassed those guards. Measured over the 489,661-posting pre-filter corpus of 2026-09-17,
    # **~8,900** postings were reaching the index on a department-only rule-1/2 match.
    if _STRONG.search(title_text):
        return Verdict(True, "strong-software-signal")
    if _GENERIC.search(title_text):
        # A department vetoes only through a discipline that names the role; strip the org-only
        # words first, so "Hardware and Mechanical Engineering" still vetoes on `mechanical`.
        if _NON_SOFTWARE.search(title_text) or _NON_SOFTWARE.search(
            _ORG_NOT_ROLE.sub(" ", dept)
        ):
            return Verdict(False, "generic-token-but-non-software")
        return Verdict(True, "generic-tech-token")
    if (
        dept
        and _TECH_DEPT.search(dept)
        and not _HIRING_DEPT.search(dept)
        and not _NOT_TECH_DEPT.search(dept)
        and not _NON_TECH_ROLE.search(title_text)
    ):
        return Verdict(True, "tech-department")
    return Verdict(False, "no-tech-signal")


def is_tech(title: str | None, department: str | None = None) -> bool:
    """Recall-biased tech/non-tech decision on a job's title (+ department)."""
    return classify(title, department).is_tech


def _filter_file(pair: tuple[Path, Path]) -> tuple[str, int, int]:
    """Filter one ``{ats}.jsonl`` into its tech subset, returning ``(ats, kept, total)``.

    Module-level and single-argument so :func:`filter_jobs` can hand it to a process pool; the
    body is what that loop always did, lifted unchanged.
    """
    src, dst = pair
    kept = total = 0
    with (
        src.open(encoding="utf-8") as fin,
        dst.open("w", encoding="utf-8") as fout,
    ):
        for line in fin:
            line = line.strip()
            if not line:
                continue
            total += 1
            job = json.loads(line)
            if is_tech(job.get("title"), job.get("department")):
                fout.write(json.dumps(job, ensure_ascii=False) + "\n")
                kept += 1
        fout.flush()
    return src.stem, kept, total


def filter_jobs(
    src_dir: str | Path, dst_dir: str | Path, *, workers: int | None = None
) -> dict[str, tuple[int, int]]:
    """Filter every ``{src_dir}/{ats}.jsonl`` down to its tech rows in ``{dst_dir}/{ats}.jsonl``.

    Streams line-by-line (never buffering a whole file) and flushes per file, per the repo's
    incremental-output rule. Returns ``{ats: (kept, total)}``. Non-tech rows are dropped; the source
    files (the full scrape output) are left untouched.

    **On a mid-file failure (a malformed line), one difference from the prior single-threaded
    version**: pooled, sibling files already in flight still finish and get written before the
    exception propagates; inline, the loop stops at the failing file and nothing after it (in
    submission order) is written. Verified: a forced-largest bad file raises in both, but the
    pooled run leaves 3 good ``dst`` files on disk where the inline run leaves 0. Benign either
    way — the stage aborts on the exception regardless (``filter_tech.main`` has no partial-output
    contract) — but it is a real behaviour difference, not "no behaviour change".

    One ATS file is one independent unit of work, so the files fan out across a process pool —
    the same shape ``update_meta``'s sweep uses, and for the same reason: this stage sits on
    ``join``'s serial critical path, where it measured 185 s of a 930 s job on the 2026-09-16
    nightly (2,095,569 rows at 11,327 rows/s).

    **Submitted largest-file-first** (an LPT schedule, by byte size as the cost proxy — cheaper
    to read than counting rows and the two track closely). This is a real but partial share of the
    speed-up, not "the whole of it" as an earlier version of this docstring claimed: the work is
    heavily skewed — ``workday`` alone was 500,679 of the corpus's ~2.1M rows — and at 4 workers
    that file is just *under* an even share, so an LPT schedule lands close to the even share.
    Alphabetical submission still parallelises the other files; it only straggles on the last one
    started, worth roughly the file's own runtime minus what the other workers absorbed while it
    waited its turn — a real cost, but well short of the full serial 185 s.

    ``workers`` defaults to the machine's CPU count; 1 (or a single input file) runs inline, with
    no pool, since pool start-up would then cost more than it saves. Also the seam the
    pooled-vs-inline equivalence test uses — ``filter_jobs_and_report`` never threads it, so
    production always gets the default.

    Uses an explicit **spawn** context for the pool, not the platform default. `__main__.main()`
    calls this right after ``scrape_all``, whose ``ThreadPoolExecutor`` is torn down with
    ``shutdown(wait=False, ...)`` (harvest.py) — the worker threads are signalled to stop but not
    guaranteed to have exited before this function runs. On Linux (`ubuntu-latest`, every CI
    caller) the default start method is *fork*, which duplicates the whole process including any
    still-live thread and whatever lock it might hold mid-teardown — the exact deadlock hazard
    Python's own multiprocessing docs warn about for a multi-threaded parent. Forcing spawn avoids
    it unconditionally, for every caller, rather than relying on a caller-specific safety argument
    that a future caller could quietly invalidate.
    """
    src_dir, dst_dir = Path(src_dir), Path(dst_dir)
    dst_dir.mkdir(parents=True, exist_ok=True)
    # Largest first: an LPT schedule. `report` sorts, so completion order never reaches the log.
    pairs = sorted(
        ((src, dst_dir / src.name) for src in src_dir.glob("*.jsonl")),
        key=lambda pair: pair[0].stat().st_size,
        reverse=True,
    )
    if workers is None:
        workers = os.cpu_count() or 1
    stats: dict[str, tuple[int, int]] = {}
    if workers <= 1 or len(pairs) <= 1:
        for pair in pairs:
            ats, kept, total = _filter_file(pair)
            stats[ats] = (kept, total)
        return stats
    ctx = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(
        max_workers=min(workers, len(pairs)), mp_context=ctx
    ) as pool:
        futures = [pool.submit(_filter_file, pair) for pair in pairs]
        # Results collected as each file lands, not blocking on the slowest submitted first
        # (a plain `pool.map` would preserve submission order and wait on shard 0 even if shard 3
        # finishes first). `report` still logs only once, at the end, same as before this change.
        for future in as_completed(futures):
            ats, kept, total = future.result()
            stats[ats] = (kept, total)
    return stats


def report(
    stats: dict[str, tuple[int, int]], dst_dir: str | Path, logger: logging.Logger
) -> None:
    """Log ``filter_jobs``' per-run table plus the two zero-output warnings, through ``logger``.

    Takes the caller's logger rather than opening one of its own: the pipeline-stage entry point
    (``headstart.ingest.filter_tech``) owns the ``[filter_tech]`` tag (ADR-0039), and this module
    is not that entry point — logging through the passed-in logger keeps every line under that
    tag, unchanged from before this reporting logic lived here.
    """
    logger.info(f"{'ATS':<16}{'kept':>9}{'total':>9}{'kept%':>8}")
    kept = total = 0
    empty = []
    for ats, (k, t) in sorted(stats.items()):
        kept += k
        total += t
        if t:
            logger.info(f"{ats:<16}{k:>9}{t:>9}{100 * k / t:>7.1f}%")
        else:
            # An ATS that scraped nothing used to be skipped here, leaving a wholly broken
            # scraper no trace in this table at all.
            #
            # Every ATS reaching `stats` was in this run's slice: `filter_jobs` keys off
            # `src_dir.glob("*.jsonl")`, and `harvest` opens one handle per ATS *in the shard's
            # list* precisely so a zero-yield ATS still leaves an empty file. An ATS outside the
            # slice has no file at all and never lands here — so "not in the slice" is not one of
            # the readings, and offering it would blunt the signal this line exists to give.
            #
            # Deferral IS one, though: `harvest` opens those handles before the resume filter, so
            # an ATS whose every Board was deferred by a budget kill also leaves an empty file and
            # arrives here having been neither attempted nor empty. `scrape_join`'s own
            # "deferred boards" line is where that is diagnosed.
            empty.append(ats)
    if empty:
        logger.warning(
            f"{len(empty)} ATS(es) were in this run's slice but contributed zero rows: "
            f"{', '.join(empty)} — their boards failed, were deferred, or are genuinely empty"
        )
    if total:
        logger.info(
            f"{'TOTAL':<16}{kept:>9}{total:>9}{100 * kept / total:>7.1f}%"
            f"  (dropped {total - kept} non-tech) -> {dst_dir}"
        )
    else:
        # A zero-row run used to be near-silent: the table printed its header and stopped, which
        # is a hard shape to notice in a green log. Everything downstream reads this corpus, so
        # say it plainly. Not an abort — this stage does not own that call.
        logger.error(f"no rows at all reached the tech filter -> {dst_dir} is empty")


def filter_jobs_and_report(
    src_dir: str | Path, dst_dir: str | Path, logger: logging.Logger
) -> dict[str, tuple[int, int]]:
    """``filter_jobs`` plus its run report (see ``report``) — what ``filter_tech.main()`` runs."""
    stats = filter_jobs(src_dir, dst_dir)
    report(stats, dst_dir, logger)
    return stats
