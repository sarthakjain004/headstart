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

from headstart.ingest.derived_meta import derive
from headstart.search import DOC_PREFIX

_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")  # [text](url) -> text
# Emphasis / heading / quote markers (keep `_`: tech terms). A `#` right after a letter is kept
# for the same reason: it is C# or F#, not a heading.
_MD_SYNTAX = re.compile(r"[*`>]+|(?<![A-Za-z])#+")
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


# Internal ingestion meta keys that must never reach the served table. `index sync`
# builds each add-row straight from a meta dict, and LanceDB rejects a column its schema does not
# declare — so anything added to `to_meta` without either landing in `index._schema()` or being
# listed here breaks every add. Kept beside `to_meta` because that is where the temptation is.
# `_derivations_version` is update_meta's resumable sweep checkpoint (ADR-0176).
PLANNER_ONLY_FIELDS = ("has_description", "_derivations_version")


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
#
# `remote` is a fourth family with a different shape (ADR-0118 amends ADR-0061's fact/derivation
# table for it): its raw ATS-native value IS a fact, but the served column holds
# `headstart.remote.extract`'s overlay on top of that fact rather than the fact itself, and —
# unlike every field above — is deliberately EXCLUDED from `update_meta.FACT_FIELDS`
# (`_FACT_WITH_OVERLAY`), so it is NOT refreshed unconditionally every run the way `location` or
# `salary` are. Only a sweep or an explicit re-derive queue entry touches it, same cadence as
# every derivation below — a bug caught in review: including it in the unconditional per-run
# resync let a JD-derived `True` silently revert to the raw fact's current value on the very next
# ordinary re-scrape, since an ordinary run holds no description text to re-derive from. The
# overlay itself still needs no separate "was this field already correct" guard the way
# experience/salary's `_rederive_without_text` does — it is one-directional (`False`/`None` ->
# `True` only, see `remote`'s module docstring), so recomputing it fresh each sweep can only
# confirm or improve on the stored value, never downgrade it.
# v2: Tier 2 answers with the smallest stated requirement rather than the first (ADR-0079).
# v3: added the salary cascade (min_salary_annual/max_salary_annual/salary_currency/salary_source).
# v4: covers 10 salary.py-changing commits since v3 that none bumped this despite each measurably
# changing `extract()`'s output on its own mandatory cross-ATS diff (workday through rippling, full
# list: `git log 42665d9..24dee34 -- src/headstart/salary.py` — a fixed range, not `..HEAD`, which
# would drift as later commits land) plus keka's own pass (AED currency, leading-currency-code
# labels, "stipend"/"ctc" labels, an "L"/lakh numeric shorthand, and a 401(k) false-positive guard
# that also corrects the same pre-existing false positive on 8 already-merged ATSes — see
# docs/salary-extraction/keka.md). One bump sweeps in all of it; the counter has no way to
# distinguish which change it's covering.
# v5: the exact same gap recurred (full list: `git log 2b6ccd8..e14f412 -- src/headstart/salary.py`
# — 3 commits, none bumped this). Two made real, measurable changes: darwinbox's pass fixed a
# lakhs-vs-absolute magnitude bug in its own field parser (zero cross-ATS effect, but darwinbox's
# own pre-existing rows need a sweep to pick up the fix — see docs/salary-extraction/darwinbox.md);
# trakstar's pass fixed `_resolve()`'s own tie-break to prefer the more complete span, moving 82
# already-shipped values across 5 ATSes (18 ashby, 53 greenhouse, 1 smartrecruiters, 1 teamtailor,
# 9 zoho — re-verified directly against the frozen corpora, not transcribed from memory) from a
# floor-only reading to the correct, fuller one — see docs/salary-extraction/trakstar.md.
# Successfactors' pass was comment-only, no sweep-worthy change. This bump was prompted by a real
# user report (a live Ashby posting whose stated "€90,000 – €110,000 per year" wasn't reflected in
# served data) — `extract()` itself returns the correct span for that exact text today, confirming
# there's no code defect to chase there. But that posting's own description carries only ONE salary
# mention, so `_resolve()` never reaches the multi-span tie-break v5 actually fixes — this bump is
# confirmed to correct 82 OTHER already-shipped values across 5 ATSes, not shown to explain that
# specific posting, whose own gap (never-yet-scraped vs. genuinely stale vs. something else) is
# still open and tracked separately, not resolved by this change.
# v6: a new full-HF-corpus recall audit (docs/salary-extraction/full-corpus-audit.md — a new
# initiative distinct from the per-ATS passes, see that doc's own opening for the brief) found and
# fixed three real gaps (full list: `git log a9d73be..b676a3e -- src/headstart/salary.py` — one
# commit), verified via a full-corpus diff (391,134 jobs, every ATS, not a per-ATS sample): (1)
# `_LABELED`'s own "for X Y Z" filler cap was too tight (3 words) to reach real phrasing like "for
# this role across Switzerland" (4 words), silently missing the whole match; widened to 4 words —
# calibrated against the real distribution, not guessed, and deliberately not wider (a first,
# wider attempt reached distant, unrelated mentions and corrupted a European trailing-symbol
# format and a missing-separator source typo — both found and reverted via the same diff). (2)
# `_BARE_BETWEEN` only recognized a currency SYMBOL, not a CODE — "between CAD 82,000 and/- CAD
# 100,000" fell through entirely. (3) a bare "CA$"/"C$" prefix (Canadian dollars) was silently
# defaulting to USD across every pattern that captures a currency symbol (`_SYM`, shared by all
# of them) — this needed two attempts: the first version only checked for the prefix in the
# overall matched text, which worked only when an unrelated earlier part of the same pattern
# happened to have already consumed the "CA"/"C" letters (code review caught this — a real
# phrasing with the identical prefix, differently placed, still fell through); fixed properly by
# folding the prefix into `_SYM` itself, so every caller captures it directly. Net, full-corpus
# effect: 366 gained, 21 lost (all confirmed genuine multi-region ambiguity across every pattern
# that captures a span, not just `_LABELED` — a too-narrow symbol/filler match used to be
# accidentally blind to the second mention), 153 changed (147 currency-only corrections, 6 real
# value shifts, all the same already-established multi-region mechanism) — see
# docs/salary-extraction/full-corpus-audit.md for the complete accounting.
#
# v7: `_LPA`'s range separator only recognized the hyphen forms, so a word-separated range
# ("10 To 12 LPA" — Zoho Recruit's own salary-widget phrasing, live-verified) matched only the
# high number as a bare, hi-less figure: a 10-12 range read as a floor of 12 with no ceiling.
# Fixed by accepting `\bto\b` alongside `[-–]` (`git log 0d030f7..98ad53d --
# src/headstart/salary.py` — one commit). Not a structural guarantee — a doubled "to" ("5 to 10
# to 20 LPA") shifts which `lo` the match captures, not just adds a `hi` — but that shape does
# not occur in real postings: verified against 526 local LPA-bearing records with zero
# disagreements between old and new.
#
# v8: added `headstart.remote.extract` — the JD-supersedes-field overlay described above. Not a
# fix to an existing derivation; a new fourth family sharing this counter for the first time
# (ADR-0118). Measured against the live served table (335,543 rows) joined to the full
# description store (493,629 JDs, 98.1% coverage): AT LEAST 7,439 already-indexed rows have
# `remote` False or None today while the JD confidently says remote (818 where the field was
# None -- 94% on Ashby, whose `workplaceType` field goes unset even at companies, like ClickHouse
# and Redis, that describe themselves as remote-first in the JD text; 6,621 where the field says
# False outright, concentrated on greenhouse and zoho) -- a lower bound, taken against an earlier
# version of `remote._REMOTE_EXPLICIT` that missed the "this is a remote position" word order
# (fixed after measuring, pinned by `test_this_is_a_remote_position`); a sweep on real data would
# find at least this many. A sweep is required to reach any of them, since a fix landing here
# reaches new Jobs for free but not rows already stored before it shipped.
#
# v9: added `headstart.geo.classify` -> the `country` column ("IN" | null), materializing the
# India filter's country-level rule instead of leaving it a query-time `regexp_like` alternation
# (ADR-0138). Unlike `remote` (v8), `country` has no competing raw ATS field to protect from
# silent reversion -- it is a pure function of `location`, which is already a fact resynced every
# run -- so it follows the plain `min_years`/salary derivation shape, not `remote`'s overlay one.
# Prompted by `experiment/lancedb-scalar-index/LOG.md` (2026-09-07): `geo.where("india")`
# measured unindexed at 352.6ms (count_rows) / 1,338.1ms (vector page) -- 7-13x every other filter
# in that session, and no scalar index type can serve a `regexp_like` alternation, so only a
# materialized column can fix it. `classify()` reads the same CITIES/STATES/IND_FORMS/
# SUBDIVISIONS/*_EXCLUDE constants `where("india")` does, so the two cannot independently drift
# on what counts as India.
#
# v10: `taleo_be.py` now reads the board's own workplace-arrangement field (when a tenant states
# one) into the `remote` fact instead of always falling straight to `is_remote(location)` (PR
# #460, on top of the v9 bump at `8bbca863`). This
# changes what the scraper emits as `job.get("remote")` for already-scraped TBE rows, and `remote`
# is excluded from `update_meta.FACT_FIELDS` (`_FACT_WITH_OVERLAY` above) precisely so that a
# rescrape alone can never resync it -- only a sweep triggered by this counter reaches rows
# indexed before the fix. Measured live 2026-09-15 across 80 distinct tenants (~280 detail pages,
# all 7 shard hosts): 2/80 state a discrete field, under two different label spellings each with
# its own value vocabulary -- "Workplace Arrangement" (Hybrid/In-Office) and "Location Type"
# (Onsite/Remote); "Hybrid" stays None, matching `workday._remote_from`'s convention.
#
# v11: `salary.py`'s `_field_darwinbox` currency gate widened from a hardcoded "INR" check to
# any code `_CURRENCY_CODE` already recognizes (USD/EUR/GBP/CAD/...). Needed here, unlike
# darwinbox.py's own structured-field change (which changes the raw `Job.salary` text itself,
# so `refresh_row`'s `salary_inputs_moved` already reaches it for free — see smartrecruiters.py's
# docstring for that mechanism): a darwinbox tenant already scraped with a non-INR
# `salary_range` string in `Job.salary` — unchanged raw input — now parses where it used to
# return None outright, which is exactly the "unchanged input starts parsing differently" case
# this counter exists for.
#
# ADR-0146 moved the composition below (the four extractors into these nine keys) into
# `headstart.ingest.derived_meta`, shared verbatim with `update_meta.refresh_row`'s held-text
# branches — no version bump, because the two assemblies were compared line-for-line against
# each other before the move and were already identical; `tests/test_derived_meta.py` now pins
# that agreement so a future edit to one path cannot silently diverge from the other again.
#
# v12: `geo.py` gained two `EXCLUDE` guards and four `INDIA_EXCLUDE` terms after an external
# 230k-posting sweep found live false "IN" classifications: `CITIES["goa"]` matched Brazilian
# "lagoa" (lagoon) inside Alagoas/Lagoa Santa/etc., `CITIES["anand"]` matched "Sananduva"
# (Brazil) and "Canandaigua" (NY, US), and `INDIA_EXCLUDE` was missing "indian creek"/
# "indianwood"/"indian street"/"indiantown" (US place names). All four were unguarded
# substring collisions, not new aliases — an already-scraped Job whose `location` is one of
# these strings currently serves `country = "IN"` and needs this sweep to correct it; `location`
# itself is unchanged, so `refresh_row`'s unconditional fact resync never reaches it. (`git log
# 9d6a840b..a9b71af5 -- src/headstart/geo.py` — one behavioral commit; the other commit in that
# range, c9f9484f, only reworded `classify`'s docstring for ADR-0146's move, already covered by
# the paragraph above.)
#
# v13: `experience.py`'s `_GAP`/`_WORDS` character classes widened to include `#` (alongside the
# `+` `_GAP` already had, for "C++"), so "C#" sitting between a stated number and the work word or
# literal "experience" that anchors it no longer strands the match — "3+ years with C# .Net
# Software Development" read None before this. On top of the v12 bump at `919cc3c9`. A description
# already stored for an already-scraped Job is unchanged raw input that now parses differently, the
# textbook case this counter exists for; live-confirmed on a Zoho posting, and measured full-corpus
# old vs new (not just coverage, per ADR-0066): zero regressions, 39 new answers, 60 corrected ones
# — see docs/experience-extraction/2026-09-16_symbol-gap-full-corpus-measurement.md.
# v14: `salary.py` gained `_RANGE_PERIOD_EACH`, a range shape where a currency code+symbol and
# "per year" repeat on EACH side of the dash ("USD $122,000 per year - USD $135,000 per year") —
# real, common Uber phrasing that previously broke `_LABELED`'s own connector (the "per year" in
# the gap isn't a dash/"to", so `_LABELED` matched only the floor and stopped there, since a
# single floor-only span already reads as "resolved"). Tried before `_LABELED` in the cascade for
# that reason. An already-stored Job's description is unchanged raw input that now resolves a
# ceiling the old code silently dropped — measured full-corpus old vs new on a live Uber board
# sweep (535 postings, 2026-09-22), not just coverage: 207 records improved (174 floor-only ->
# full, 33 None -> full), zero regressions (no record lost a value or had an already-resolved
# min/max change). On top of the v13 bump at `ea577fff`.
DERIVATIONS_VERSION = 14


def to_meta(job: dict) -> dict:
    """Canonical typed metadata (ADR-0007) + the inline experience numbers (ADR-0019).

    The nine derived keys (``remote``, ``country``, ``min_years``/``max_years``/
    ``experience_source``, ``min_salary_annual``/``max_salary_annual``/``salary_currency``/
    ``salary_source``) come from :func:`headstart.ingest.derived_meta.derive` — the one
    composition of the four extractors (ADR-0146) also used by ``update_meta.refresh_row`` to
    repair an already-stored row, so the two cannot independently drift on what an extractor's
    result means. See that module's docstring for what each family reads and how ``None``
    behaves; ``employment_type`` / ``salary`` stay raw strings here — display-only (ADR-0019).

    The derived fields are re-computable from the facts beside them, which is what lets
    ``update_meta`` repair them in place later; see :data:`DERIVATIONS_VERSION`.
    """
    meta = {field: job.get(field) for field in META_FIELDS}
    # Whether the Doc we are about to embed actually carried a description (ADR-0050). Recorded
    # because a vector built from a bare title is indistinguishable from a good one afterwards,
    # and `embed_plan` skips by id — so without this the degradation is permanent and invisible.
    # Planner-only: see PLANNER_ONLY_FIELDS.
    meta["has_description"] = bool((job.get("description") or "").strip())
    meta.update(derive(job))
    return meta
