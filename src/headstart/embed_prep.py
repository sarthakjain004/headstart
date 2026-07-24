"""Doc preparation shared by the embed step and the embed planner (ADR-0025).

The nightly pipeline builds each **Doc** — the one string embedded per Job (title +
markdown-stripped description, ``search_document:``-prefixed, ADR-0005) — and its typed
metadata (ADR-0007/0019) in *two* places now: ``scripts/embed/embed_jobs.py`` (the monolithic
``--resume`` path) and ``scripts/pipeline/plan_embed.py`` (the planner that assigns Docs to
embed shards). A sharded Doc's vector only matches the monolith's if the English gate, the
doc-text builder, and the token-length **Bucket** are byte-identical across the two — so they
live here once instead of being hand-copied. ``embed_jobs.py`` re-exports these for its own
callers and tests.

Pure and ML-free: langdetect + regex only, no torch/sentence-transformers, so the planner and
unit tests import it without the encoder stack. Tokenization (the one step that needs the
model's tokenizer) stays with each caller; this module only classifies a known token count into
a Bucket.
"""

from __future__ import annotations

import re

from langdetect import DetectorFactory, LangDetectException, detect

from headstart.experience import extract
from headstart.search import DOC_PREFIX

DetectorFactory.seed = 0  # make langdetect deterministic

_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")  # [text](url) -> text
_MD_SYNTAX = re.compile(
    r"[*`#>]+"
)  # emphasis / heading / quote markers (keep `_`: tech terms)
_WS = re.compile(r"\s+")

# Token-length Buckets (ADR-0005): a Doc is sorted into the smallest Bucket that holds it,
# measured with the real tokenizer. Shared so the planner buckets a Doc exactly as the encoder
# will pad it. The encode-side batch sizing (batch_size_for / _ATTN_BUDGET) stays in embed_jobs.
_BUCKETS = (512, 1024, 2048, 4096)
_MAX_SEQ_TOKENS = _BUCKETS[-1]

# The canonical typed metadata that rides next to each vector (ADR-0007); the corpus reader
# already yields canonical Job dicts, so this is pure selection — no per-source adapting.
_META_FIELDS = (
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


def bucket_for(n_tokens: int) -> int:
    """The smallest bucket that holds a doc of ``n_tokens`` (over-cap docs go to the top one)."""
    for bucket in _BUCKETS:
        if n_tokens <= bucket:
            return bucket
    return _BUCKETS[-1]


def clean_markdown(text: str) -> str:
    """Strip markdown syntax to plain text and collapse whitespace."""
    text = _MD_LINK.sub(r"\1", text)
    text = _MD_SYNTAX.sub(" ", text)
    return _WS.sub(" ", text).strip()


def is_english(title: str, description: str) -> bool:
    """English gate. Detect on title + a description sample (full text is needless and slow)."""
    try:
        return detect(f"{title} {description[:500]}") == "en"
    except LangDetectException:
        return False  # undetectable -> held out of the English index


def build_doc(job: dict) -> str:
    title = (job.get("title") or "").strip()
    body = clean_markdown(job.get("description") or "")
    return f"{DOC_PREFIX}{title}\n\n{body}"


def to_meta(job: dict) -> dict:
    """Canonical typed metadata (ADR-0007) + the inline experience numbers (ADR-0019).

    ``min_years`` / ``max_years`` come from the extraction cascade (field, then description,
    then seniority floor — ADR-0018) with the ``experience_source`` tier tag carried alongside;
    all three are None when nothing matched. ``employment_type`` / ``salary`` stay raw strings —
    display-only until normalized (ADR-0019).
    """
    meta = {field: job.get(field) for field in _META_FIELDS}
    span = extract(job.get("experience"), job.get("description"), job.get("title"))
    meta["min_years"] = span.min_years if span else None
    meta["max_years"] = span.max_years if span else None
    meta["experience_source"] = span.source if span else None
    return meta
