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
     that department names a hiring function  -> tech      (recall booster for vague titles)
  5. otherwise                               -> not tech
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

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
    r"(software|systems|solutions|technical|cloud|data|security|platform|enterprise|integration) architect",
    r"\bprogrammer\b",
    r"\bblockchain\b",
    r"smart contract",
    r"\bweb3\b",
    r"game (developer|engineer|programmer)",
    r"api (developer|engineer)",
    r"(systems|network|database|devops|cloud|linux) (administrator|admin|engineer)",
    r"engineering manager",
    r"(director|vp|vice president|head) of (engineering|ai|ml|data|software|platform|infrastructure|technology|security)",
    r"\bcto\b",
    r"tech(nical)? lead",
    r"(ai|ml|data|software|cloud|security|systems|chief|principal|staff) technologist",
    r"developer (advocate|relations)",
    r"\bdevrel\b",
    r"(react|angular|vue|node|python|java|golang|rust|kubernetes) (developer|engineer)",
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
    text = f"{title_text} {dept}"
    if _STRONG.search(text):
        return Verdict(True, "strong-software-signal")
    if _GENERIC.search(text):
        # A department vetoes only through a discipline that names the role; strip the org-only
        # words first, so "Hardware and Mechanical Engineering" still vetoes on `mechanical`.
        if _NON_SOFTWARE.search(title_text) or _NON_SOFTWARE.search(
            _ORG_NOT_ROLE.sub(" ", dept)
        ):
            return Verdict(False, "generic-token-but-non-software")
        return Verdict(True, "generic-tech-token")
    if dept and _TECH_DEPT.search(dept) and not _HIRING_DEPT.search(dept):
        return Verdict(True, "tech-department")
    return Verdict(False, "no-tech-signal")


def is_tech(title: str | None, department: str | None = None) -> bool:
    """Recall-biased tech/non-tech decision on a job's title (+ department)."""
    return classify(title, department).is_tech


def filter_jobs(src_dir: str | Path, dst_dir: str | Path) -> dict[str, tuple[int, int]]:
    """Filter every ``{src_dir}/{ats}.jsonl`` down to its tech rows in ``{dst_dir}/{ats}.jsonl``.

    Streams line-by-line (never buffering a whole file) and flushes per file, per the repo's
    incremental-output rule. Returns ``{ats: (kept, total)}``. Non-tech rows are dropped; the source
    files (the full scrape output) are left untouched.
    """
    src_dir, dst_dir = Path(src_dir), Path(dst_dir)
    dst_dir.mkdir(parents=True, exist_ok=True)
    stats: dict[str, tuple[int, int]] = {}
    for src in sorted(src_dir.glob("*.jsonl")):
        kept = total = 0
        with (
            src.open(encoding="utf-8") as fin,
            (dst_dir / src.name).open("w", encoding="utf-8") as fout,
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
        stats[src.stem] = (kept, total)
    return stats
