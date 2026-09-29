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

import hashlib
import re

from headstart.boards import eightfold_backing
from headstart.embedding_conventions import DOC_PREFIX
from headstart.ingest.derived_meta import derive

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
    "requisition",
    "posted_at",
)


# Internal ingestion meta keys that must never reach the served table. `index sync`
# builds each add-row straight from a meta dict, and LanceDB rejects a column its schema does not
# declare — so anything added to `to_meta` without either landing in `index._schema()` or being
# listed here breaks every add. Kept beside `to_meta` because that is where the temptation is.
# `_derivations_version` is update_meta's resumable sweep checkpoint (ADR-0176). `doc_hash` is the
# fingerprint of the raw title and description a row's vector was built from (ADR-0285).
PLANNER_ONLY_FIELDS = ("has_description", "_derivations_version", "doc_hash")

#: What `update_meta` stamps as the `doc_hash` of a row whose vector is known to encode text it no
#: longer carries (ADR-0285). No real hash equals it, so `embed_plan` re-embeds the row.
STALE_DOC_HASH = "stale"


def doc_hash(job: dict) -> str:
    """A fingerprint of the text a Job's vector is built from: its title and description, as
    scraped (ADR-0285).

    Not a hash of the **Doc** (CONTEXT.md), despite the name: it reads the raw fields, not
    :func:`build_doc`'s output. A change to how the Doc is assembled would otherwise re-embed every
    Job at once, while an edit to the posting is what this tracks. The name stays because every
    stored ``meta.jsonl`` row already carries it under this key."""
    text = (
        f"{(job.get('title') or '').strip()}\n{(job.get('description') or '').strip()}"
    )
    return hashlib.blake2b(text.encode("utf-8"), digest_size=8).hexdigest()


def stored_facts(job: dict) -> dict:
    """The :data:`META_FIELDS` a corpus row puts in the store, as :func:`to_meta` and
    ``update_meta``'s facts refresh both read them — the one place a fact is scoped on its way in.

    ``requisition`` is kept only on a Board the Eightfold pairs name (ADR-0210): nothing else can
    ever match on it, and a new value in the store rewrites the served row, vector and all, so
    stamping every row of the ATSes that state one (six when measured on v654) would rewrite
    ~216k rows on the first run for no dedup. Widen it by adding pairs, or by dropping this check.
    """
    facts = {field: job.get(field) for field in META_FIELDS}
    if facts["requisition"] and not eightfold_backing.in_scope(job["id"]):
        facts["requisition"] = None
    return facts


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
# `headstart.jobs.remote.extract`'s overlay on top of that fact rather than the fact itself, and —
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
# v15: the 2026-09-22 bug-hunt fixes, all one PR on top of the v14 bump at `90a6fc64` (full list:
# `git log 90a6fc64..19027279 -- src/headstart/salary.py src/headstart/experience.py
# src/headstart/remote.py` on that PR's branch — 2ab9e6ba in that range is #562, inert, below; a
# later commit only moves a docstring). The commits by subject, in case it lands squashed: "Read
# £/€/₹ and per-side symbols in generic salary fields", "Map HK$/S$/A$/AU$/NZ$/US$ salary symbols
# to their currency", "Treat 'not a fully remote position' as a negation", "Refuse 'up to USD X'
# ceilings instead of storing them as floors", "Convert month-denominated experience fields to
# whole years", "Stop reading a preceding HR acronym or MO state code as a period", "Read a
# one-digit comma tail as a decimal in salary numbers", "Lower the INR salary ceiling to 3 crore",
# "Ignore period hints cut from a word at the window edge", "Count a trailing HR/MO as a period
# only when it touches the figure", "Keep a k/L figure's fraction instead of rounding it away
# first", "Read contracted negations like "isn't fully remote" as negations", and "Read a bare $
# as USD in generic salary fields, as Tier 2 does", reversed by "Keep a bare $ currency-less in
# fields; only prefixed dollars resolve" (a guessed USD was wrong on ~15% of those fields). Most
# change what `extract()` returns for raw input already stored; the trailing-HR/MO guard moved no
# stored row, and the bare-$ pair nets to nothing. Measured once for the whole branch, 87bcaff9 vs
# 19027279, old vs new on every row of the 2026-09-15 LanceDB snapshot (459,291 rows, 7 days
# stale — not the live table), per ADR-0066: salary 827 rows move (316 value corrected — mostly a
# recovered ceiling, 318 currency only, 69 both, 87 None->value, 37 value->None — the INR-cap
# misreads and refused "up to" ceilings, read by hand per commit); experience 9; remote 39
# True->None. `remote` reaches only rows whose Board is scraped in the sweep run (it has no stored
# raw field to re-derive from) — a known gap, not closed here. The one other salary.py commit since
# v14, `2ab9e6ba` (#562, keka's `_field_keka` now applies `_period_multiplier`), is inert on stored
# rows: their raw keka string carries no period word, which `_period_multiplier` reads as annual
# (x1), so it needed no bump.
# v16: `geo.py` tags a location that is exactly "IN" (any case) as India — `IN_EXACT`, one
# behavioral commit on top of the v15 bump at `5a6d653c` (`git log 5a6d653c..79ca6738 --
# src/headstart/geo.py`, subject "Tag a bare IN location as India", in case it lands squashed).
# `location` itself is unchanged on those rows, so `refresh_row`'s fact resync never reaches them;
# only this sweep does. Measured old vs new `classify()` on every row of the served table pulled
# 2026-09-25 (514,163 rows), per ADR-0066: 885 rows move, all null -> "IN", none the other way —
# 847 SuccessFactors, 29 iCIMS, 7 Zoho, 2 JazzHR. 876 are India; the 9 JazzHR/Zoho rows are
# Indiana on a US state field, an accepted collision (`geo.IN_EXACT`'s comment).
# v17: `jobs/salary.py`'s Tier 2 reads an ISO code just before a bare "$" ("CAD $150,000", "CAD:
# $150,000") as the currency instead of the bare-"$" USD default — two commits on top of the v16
# bump at `6324c83c` (`git log 6324c83c..c1d48201 -- src/headstart/jobs/salary.py`, subjects "Read
# an ISO code before a bare $ as its currency" and "Resolve the ISO code before a bare $ inside
# _currency_for_symbol", in case they land squashed). Measured old vs new `from_description()` on
# the 12,021 descriptions in the store pulled 2026-09-26 with an upper-case three-letter word before
# "$", per ADR-0066: 296 move, none in amount — 271 USD->CAD, 17 USD->AUD, 1 USD->NZD, 3 USD->None
# (MXN), 4 value->None (a USD and a CAD range in one posting, now declined as the ambiguous
# multi-region case). Tier 1 is untouched, so a row whose field already answered keeps it.
# v18: `jobs/experience.py` bounds a structured field's floor and every ceiling, in both tiers, at
# 30 years (`_MAX_PLAUSIBLE_YEARS`, was 50; #697) — two commits on top of the v17 bump at
# `8a6f8f4b` (`git log 8a6f8f4b..c7fff428 -- src/headstart/jobs/experience.py`, subjects "Reject
# structured experience floors above 30 years" and "Cap every experience ceiling and a field floor
# at 30 years", in case they land squashed). Measured per ADR-0066 on the served metadata pulled
# 2026-09-28 (556,206 rows). Tier 1, old vs new `from_field()` on every raw `experience` field
# (132,631 of those rows carry one, 1,899 distinct values): 10 rows move, all field -> field save
# one: "35 years" 35 -> no answer (field -> none: the title "Web Application Developer (pending)"
# gives Tier 3 nothing), and 9 ceilings dropped with the floor kept ("8-45 years" (8, 45) -> (8,
# None), "12 - 50 Years", "10-40 years"). Tier 2: the 3 served regex rows with a ceiling above 30
# ((10, 40), (2, 35), (18, 35)) move to an open ceiling, same tier, same floor — a deterministic
# consequence of the rule, not re-run over their text. No served regex floor is above Tier 2's own
# cap of 20, and the regex rows #697 listed at 50/45/30 are no longer served.
# v19: `jobs/salary.py` (#698), two commits on top of the v18 bump at `65045ff9` (`git log
# 65045ff9..7aa3b5f2 -- src/headstart/jobs/salary.py`, subjects "Read European grouping and k/L/LPA
# units in free-text salary fields" and "Apply code-review: INR k figures are monthly; structured
# code names currency", in case they land squashed). `_field_generic` (every ATS with no calibrated
# Tier-1 parser: zoho, bamboohr, adp_recruiting, taleo_be, ...) reads a "."-grouped figure
# ("71.000,00 - 105.000,00 EUR") and a figure's own k / L / LPA / lakh / lac unit ("45k - 50k GBP",
# "10-13 LPA INR"), ignores a unit repeated after a figure written in full, and declines a "k"
# figure in rupees or in a field naming no currency (a monthly amount). And `extract()` gives a
# description figure the ISO code a structured-currency field states when the field's own amount
# fails ("40-50 EUR 1 YEAR"). Measured per ADR-0066 on the served metadata pulled 2026-09-28: old vs
# new `from_field()` on every raw `salary` (46,497 rows) moves 1,658 — 1,649 None -> value (zoho
# 1,396, bamboohr 241, adp_recruiting 9, taleo_be 3; INR 1,121, none 342, GBP 95, CAD 45, USD 26,
# EUR 12, AUD 6, AED 1, CHF 1), 6 currency None -> INR ("1,20,000 LPA"), 2 gain a dropped ceiling
# ("$80,200k - $110,000K") and 1 value -> None ("30.000.000 IDR", read as 30,000 before); the
# structured-code rule moves 11 more, currency None -> EUR (recruitee 6, smartrecruiters 5), all of
# the served rows it can reach. Tier 2 is untouched.
# v20: `jobs/salary.py` gives adp_recruiting its own Tier-1 parser (`_field_adp_recruiting`: "X to
# Y" ranges, an hour mark on each figure, "/hour", a "k" on either figure, a bare "$" as USD, and a
# period-less figure under 200 read hourly), and `_field_keka` reads a period-less INR range no
# larger than 100 as lakhs — one commit on top of the v19 bump at `304630d2` (`git log
# 304630d2..fdb78685 -- src/headstart/jobs/salary.py`, subject "Read adp_recruiting pay strings and
# keka lakhs ranges (DERIVATIONS_VERSION 20) (#769)", squash-merged). Measured old (v19) vs
# new `extract()` on every adp_recruiting and keka row of the served table read 2026-09-28 (v469),
# with its description from the store pulled the same day, per ADR-0066: adp_recruiting 228 of
# 3,030 rows move — 86 none->field; 139 field->field (123 currency only, None->USD or CAD; 6
# value only and 10 both, a recovered ceiling or an hourly figure no longer rounded before
# annualising); 3 of 9 regex->field change value or currency (one "75k-95k" loses the
# description's USD). keka 366 of 6,388 — 365 none->field lakhs, 1 regex->field where the field
# states 3-5.5 lakhs and the description 3.6-6. No other ATS's field parse moves.
# v21: `jobs/salary.py` keeps an hourly or monthly figure's fraction until after annualising
# (`_field_range_currency_interval`, `_field_gem`: "12.31 EUR HOUR" is 25,605, not 12 x 2,080),
# refuses a MONTH figure above `_MONTHLY_CEILING` (an annual pay typed under MONTH, "26728 GBP
# MONTH"), and emits MXN, ZAR, CZK and BRL with their own bounds — one commit on top of the v20
# bump at `fdb78685` (`git log fdb78685..0db2b433 -- src/headstart/jobs/salary.py`, the
# squash-merged #823). Measured old vs new `from_field()` on every served row with a salary (46,199,
# table read 2026-09-28), per ADR-0066: 533 move — 421 value only (the kept fraction, e.g.
# "46.26-59.68 USD 1 HOUR" 95,680-124,800 -> 96,221-124,134), 45 currency only (None -> MXN,
# BRL, CZK, ZAR), 46 none -> value (MXN/ZAR figures the USD-shaped bound refused), 21 value ->
# none (9 MONTH-typed annual pays, and 12 zoho/ashby ZAR or MXN figures below their currency's
# floor, e.g. "25000 MXN": monthly amounts that had been served as annual). Tier 2 moves only
# where a description names MXN before a bare "$".
# v22: `jobs/salary.py` names 15 more currencies (SGD MYR JPY PHP NGN RON HUF SAR CNY PKR NZD QAR
# COP NOK TWD) with their own bounds, reads a period-less zoho figure below a monthly-quoted
# currency's floor as monthly pay (`_field_zoho`: "30000-40000 INR" is 360,000-480,000), serves no
# currency-less description figure for a zoho field naming a code it cannot parse (zoho.py splices
# that field into the description), and reads a "to" range and a code before the ceiling in
# `_field_generic` — one commit on top of the v21 bump at `0db2b433` (`git log 0db2b433..08f81f0e --
# src/headstart/jobs/salary.py`, the squash-merged #859). Measured old vs new `extract()` on all
# 498,853 rows of served v277 with the description store pulled 2026-09-29, per ADR-0066: 2,016
# move. By tier: none->field 611, none->regex 134, regex->field 145, field->none 54, regex->none 14,
# field->regex 1, and within a tier field 1,027 and regex 30. By what moved: 745 none -> value (477
# on zoho, 442 of them monthly readings; the new codes' figures the USD-shaped bound refused); 705
# value only (a ceiling recovered by "to" or a code before it: ripplehire 473, zoho 120, bamboohr
# 101); 235 currency only (None -> a new code); 236 value and currency (216 on zoho, where a
# currency-less reading of the field or of its splice gives way to the monthly one); 27 tier only or
# tier and value (zoho "to" ranges, LPA and "10 K+ INR" now read by the field; on 4 the description
# had stated another figure); 68 value -> none, figures served with no currency that the new floors
# or the zoho guard refuse, nearly all monthly pay read as annual (zoho 34, bamboohr PHP 14,
# greenhouse JPY/PHP 8, monthly pay typed YEAR 5), with zoho's 4 GBP day rates and one stated
# "1000-3000 SGD per-month" among them. Figures naming a code with a null currency fall from 526 to
# 31.
DERIVATIONS_VERSION = 22


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
    meta = stored_facts(job)
    # Whether the Doc we are about to embed actually carried a description (ADR-0050). Recorded
    # because a vector built from a bare title is indistinguishable from a good one afterwards,
    # and `embed_plan` skips by id — so without this the degradation is permanent and invisible.
    # Planner-only: see PLANNER_ONLY_FIELDS.
    meta["has_description"] = bool((job.get("description") or "").strip())
    # Planner-only too: the text this vector encodes, so an edit can be told from a re-read of
    # the same posting and re-embedded (ADR-0285).
    meta["doc_hash"] = doc_hash(job)
    meta.update(derive(job))
    return meta
