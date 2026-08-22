"""Doc preparation shared by the embed step and the embed planner (ADR-0025).

The nightly pipeline builds each **Doc** — the one string embedded per Job (title +
markdown-stripped description, ``search_document:``-prefixed, ADR-0005) — and its typed
metadata (ADR-0007/0019) in *two* places now: ``headstart.ingest.embed_run`` (the monolithic
``--resume`` path) and ``headstart.ingest.embed_plan`` (the planner that assigns Docs to
embed shards). A sharded Doc's vector only matches the monolith's if the English gate, the
doc-text builder, and the token-length **Bucket** are byte-identical across the two — so they
live here once instead of being hand-copied. ``embed_run.py`` re-exports these for its own
callers and tests.

Pure and ML-free: regex only at import time, no torch/sentence-transformers, so the planner and
unit tests import it without the encoder stack. ``langdetect`` is the one non-base dependency and
is imported *inside* :func:`is_english`, so everything else here — ``META_FIELDS``,
``DERIVATIONS_VERSION``, ``to_meta`` — is importable on a base install too. Tokenization (the one
step that needs the model's tokenizer) stays with each caller; this module only classifies a known
token count into a Bucket.
"""

from __future__ import annotations

import re

from headstart.experience import extract
from headstart.salary import extract as extract_salary
from headstart.search import DOC_PREFIX

_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")  # [text](url) -> text
_MD_SYNTAX = re.compile(
    r"[*`#>]+"
)  # emphasis / heading / quote markers (keep `_`: tech terms)
_WS = re.compile(r"\s+")

# Token-length Buckets (ADR-0005): a Doc is sorted into the smallest Bucket that holds it,
# measured with the real tokenizer. Shared so the planner buckets a Doc exactly as the encoder
# will pad it. The encode-side batch sizing (batch_size_for / _ATTN_BUDGET) stays in embed_run.
BUCKETS = (512, 1024, 2048, 4096)
MAX_SEQ_TOKENS = BUCKETS[-1]

# The canonical typed metadata that rides next to each vector (ADR-0007); the corpus reader
# already yields canonical Job dicts, so this is pure selection — no per-source adapting.
META_FIELDS = (
    "id",
    "ats",
    "company",
    "title",
    "location",
    "remote",
    "employment_type",
    "experience",
    "salary",
    "department",
    "url",
    "posted_at",
)


# Meta keys that exist for the *planner* and must never reach the served table. `index sync`
# builds each add-row straight from a meta dict, and LanceDB rejects a column its schema does not
# declare — so anything added to `to_meta` without either landing in `index._schema()` or being
# listed here breaks every add. Kept beside `to_meta` because that is where the temptation is.
PLANNER_ONLY_FIELDS = ("has_description",)


def bucket_for(n_tokens: int) -> int:
    """The smallest bucket that holds a doc of ``n_tokens`` (over-cap docs go to the top one)."""
    for bucket in BUCKETS:
        if n_tokens <= bucket:
            return bucket
    return BUCKETS[-1]


def clean_markdown(text: str) -> str:
    """Strip markdown syntax to plain text and collapse whitespace."""
    text = _MD_LINK.sub(r"\1", text)
    text = _MD_SYNTAX.sub(" ", text)
    return _WS.sub(" ", text).strip()


def is_english(title: str, description: str) -> bool:
    """English gate. Detect on title + a description sample (full text is needless and slow).

    ``langdetect`` is imported here rather than at module scope so the rest of this module —
    ``META_FIELDS``, ``DERIVATIONS_VERSION``, ``to_meta`` — stays importable on a base install.
    CI's quality job installs base deps only, and a top-level import made every consumer of those
    constants uninstallable there, which is how the ADR-0061 refresh tests came to be skipped.
    The import is cached after the first call, so the per-Doc cost is a dict lookup.
    """
    from langdetect import DetectorFactory, LangDetectException, detect

    DetectorFactory.seed = (
        0  # deterministic; idempotent, so setting it per call is free
    )
    try:
        return detect(f"{title} {description[:500]}") == "en"
    except LangDetectException:
        return False  # undetectable -> held out of the English index


def build_doc(job: dict) -> str:
    title = (job.get("title") or "").strip()
    body = clean_markdown(job.get("description") or "")
    return f"{DOC_PREFIX}{title}\n\n{body}"


# How many times the *derived* columns' definition has changed (ADR-0061). Bump this in the same
# change that alters what `experience.extract` or `salary.extract` returns, and `update_meta`
# re-derives every already-stored row whose description we hold — otherwise a fix reaches new Jobs
# only, because `embed_plan` skips ids it has already embedded. Only the derivations below depend
# on it; facts refresh unconditionally. One shared counter for both families (simpler than two
# watermarks; the wasted recompute on an unrelated bump is cheap regex work, not network/LLM cost
# — revisit only if that stops being true).
# v2: Tier 2 answers with the smallest stated requirement rather than the first (ADR-0079).
# v3: added the salary cascade (min_salary_annual/max_salary_annual/salary_currency/salary_source).
# v4: covers 10 salary.py-changing commits since v3 that none bumped this despite each measurably
# changing `extract()`'s output on its own mandatory cross-ATS diff (workday through rippling, full
# list: `git log 42665d9..HEAD -- src/headstart/salary.py`) plus keka's own pass (AED currency,
# leading-currency-code labels, "stipend"/"ctc" labels, an "L"/lakh numeric shorthand, and a 401(k)
# false-positive guard that also corrects the same pre-existing false positive on 8 already-merged
# ATSes — see docs/salary-extraction/keka.md). One bump sweeps in all of it; the counter has no way
# to distinguish which change it's covering.
DERIVATIONS_VERSION = 4


def to_meta(job: dict) -> dict:
    """Canonical typed metadata (ADR-0007) + the inline experience numbers (ADR-0019).

    ``min_years`` / ``max_years`` come from the extraction cascade (field, then description,
    then seniority floor — ADR-0018) with the ``experience_source`` tier tag carried alongside;
    all three are None when nothing matched. ``min_salary_annual`` / ``max_salary_annual`` /
    ``salary_currency`` come from the salary cascade (field, then description — no seniority
    tier, see ``headstart.salary``'s module docstring) with ``salary_source`` alongside; all four
    are None when nothing matched, and None is never treated as exclusionary. ``employment_type``
    / ``salary`` stay raw strings — display-only (ADR-0019).

    The derived fields are re-computable from the facts beside them, which is what lets
    ``update_meta`` repair them in place later; see :data:`DERIVATIONS_VERSION`.
    """
    meta = {field: job.get(field) for field in META_FIELDS}
    # Whether the Doc we are about to embed actually carried a description (ADR-0050). Recorded
    # because a vector built from a bare title is indistinguishable from a good one afterwards,
    # and `embed_plan` skips by id — so without this the degradation is permanent and invisible.
    # Planner-only: see PLANNER_ONLY_FIELDS.
    meta["has_description"] = bool((job.get("description") or "").strip())
    span = extract(job.get("experience"), job.get("description"), job.get("title"))
    meta["min_years"] = span.min_years if span else None
    meta["max_years"] = span.max_years if span else None
    meta["experience_source"] = span.source if span else None
    salary_span = extract_salary(
        job.get("salary"), job.get("description"), job.get("ats")
    )
    meta["min_salary_annual"] = salary_span.min_annual if salary_span else None
    meta["max_salary_annual"] = salary_span.max_annual if salary_span else None
    meta["salary_currency"] = salary_span.currency if salary_span else None
    meta["salary_source"] = salary_span.source if salary_span else None
    return meta
