#!/usr/bin/env python3
"""How good is the India location filter, measured against the served LanceDB table.

The filter is a raw ``lower(location) LIKE '%alias%'`` OR-chain expanded from
:mod:`headstart.geo` (ADR-0024). It has three independent failure modes and they need three
different kinds of evidence, so this reports them separately rather than as one accuracy score:

**Field health** — a row with no location, or a placeless one ("Remote", "N/A"), can never match
any place filter however good the gazetteer is. That is a ceiling on recall owned by the
*scrapers*, not by ``geo``, which is why it is broken out per ATS.

**Recall** — India rows the filter does not match, and the hardest of the three to measure
honestly. It cannot be judged against the gazetteer's own terms: the filter is already
"india-word OR every city OR every state", so any net built from those is a strict subset of it
and reports 0 misses by construction. (An earlier pass did exactly that and printed a meaningless
100.00%.) The country-tag probes here were once independent of it, but ADR-0086 moved them INTO
the filter, so they now read near zero by construction too — kept as a regression check on that
change, not as a recall figure. **The by-hand read of ``--unmatched`` is the only real bound.**

**Precision** — non-India rows the filter claims. Every alias is a substring match, so this
reports the two ways that bites: the country clause itself (``LIKE '%india%'`` matches *Indian*
Head, MD) and rows resting on a city alias alone.

Everything reads ``location`` alone, projected without vectors, so the whole audit touches a few
tens of MB of a multi-GB table.

Run:  python scripts/eval/location_filter_audit.py
      python scripts/eval/location_filter_audit.py --unmatched 120   # read the residue by hand
      python scripts/eval/location_filter_audit.py --suspects bengaluru
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from headstart import geo

# Word-boundary "india", used ONLY to size the substring bleed below - never as the filter's
# own test, which is a plain substring and deliberately so ("IN_India_WFH" has no word boundary
# around india yet is plainly an India row).
_INDIA_WORD = re.compile(r"\bindia\b")

# The subdivision tail comes straight from geo. A private copy lived here and had ALREADY
# drifted - 35 entries against geo's 40, missing every ISO code added in the same PR - so
# ', TG, IN' rows were invisible to this probe. Sourcing it removes the drift by construction.
_STATE_IN = re.compile(r",\s*(" + "|".join(geo.SUBDIVISIONS) + r")\s*,\s*in\.?\s*$")
_IND_A3 = re.compile(r"\bind\b")  # ISO alpha-3; no US collision, unlike bare "IN"
_TRAILING_IN = re.compile(r",\s*in\.?\s*$")

# Indiana cities seen in this corpus' trailing-", IN" rows. Used to split that bucket, which is
# genuinely ambiguous: "IN" is India's alpha-2 AND Indiana's USPS abbreviation, so the bucket is
# reported as two halves rather than silently assigned to either country.
_INDIANA = (
    "indianapolis",
    "fort wayne",
    "lafayette",
    "carmel",
    "mooresville",
    "crane",
    "jeffersonville",
    "plainfield",
    "bloomington",
    "evansville",
    "south bend",
    "fishers",
    "noblesville",
    "greenwood",
    "terre haute",
    "muncie",
    "kokomo",
    "elkhart",
    "anderson",
    "westfield",
    "valparaiso",
    "middlebury",
    "portage",
    "clinton",
    "seymour",
    "hammond",
    "warrick",
    "mount comfort",
)

# Whole-string placeless tokens. Substring would be wrong: "Remote - India" and "Remote, KA, IN"
# both carry real geography. Only a string that is *nothing but* the token is unfilterable.
# Deliberately excludes "us"/"usa": those name a country and ARE filterable, so counting them
# here would inflate the unfilterable ceiling. Also excludes "-", which is unreachable — the
# strip below removes it before the lookup, leaving "".
# A whole string that names no place. English is not enough here: several ATSes localize the
# marker they write into `location`, and a French or German one counted as a *valid place* is a
# silent under-report of exactly the defect this section measures. Measured 2026-08-25 over 138
# live recruitee Boards / 1,922 offers, where the marker appears instead of a place on 476 of
# them: `Poste a distance` 269, `Remote job` 101, `Homeoffice` 80, `Werken op afstand` 15, plus
# `Trabajo a distancia`, `Praca zdalna`, `Trabalho remoto`, `Lavoro da remoto` in smaller numbers.
# Listing only the English one scored the other 375 as places.
#
# The list is not claimed exhaustive — independent samples kept turning up locales the previous
# one missed, which is why the scrapers detect these structurally rather than by string (see
# `recruitee._is_remote_sentinel`). Here a list is still the right shape: this is a *measurement*
# of already-served rows, with no per-row structured fields to compare against.
_PLACELESS = (
    "",
    "remote",
    "anywhere",
    "multiple locations",
    "various",
    "worldwide",
    "global",
    "work from home",
    "wfh",
    "n/a",
    "remote job",
    "poste a distance",  # fr
    "homeoffice",  # de
    "home office",
    "werken op afstand",  # nl
    "trabajo a distancia",  # es
    "trabalho remoto",  # pt
    "lavoro da remoto",  # it
    "praca zdalna",  # pl
)


def _fold(text: str) -> str:
    """Lowercase with diacritics stripped.

    Used to size what folding would buy on the recall probe, and by :func:`is_placeless` so the
    localized markers above need one spelling rather than two ("Poste à distance" folds onto the
    listed "poste a distance").
    """
    return "".join(
        c
        for c in unicodedata.normalize("NFKD", (text or "").lower())
        if not unicodedata.combining(c)
    )


def _like(pattern: str):
    """A SQL LIKE pattern as a Python predicate, so the model cannot drift from the clause.

    ``%`` is the only wildcard these clauses use. Building the predicates FROM ``geo``'s own
    constants rather than restating them is the whole point: an earlier version of this file
    hardcoded its own copy of the term list, and when ``geo`` grew three clauses (ADR-0086) the
    copy silently stopped describing the filter — caught only because
    :func:`verify_model_against_sql` refuses to continue on a mismatch.
    """
    rx = re.compile("".join(".*" if c == "%" else re.escape(c) for c in pattern) + "$")
    return lambda s: rx.match(s) is not None


def india_clauses() -> list[tuple[str, object]]:
    """Exactly what ``geo.where("india")`` tests, as predicates over ``lower(location)``.

    Every term comes from ``geo`` itself. The structure still has to be restated here, and four
    details of it change the answer:

    * the country term is a plain SUBSTRING carrying its own :data:`geo.INDIA_EXCLUDE` guards
      (``indiana``, ``indian head``, …) — not a word-boundary test, which would lose
      ``IN_India_WFH``;
    * ISO alpha-3 ``IND`` matches only in :data:`geo.IND_FORMS` positions, guarded by
      :data:`geo.IND_EXCLUDE` because ``IND`` is also Indianapolis's IATA code;
    * a city can carry :data:`geo.EXCLUDE` NOT-LIKE guards ("surat" minus "surat thani");
    * :data:`geo.REGIONS` is *not* part of it — its values are city keys, not alias substrings,
      and ``where("india")`` already iterates every city.

    Order matters only for attribution (first match names the row), never for the verdict.
    """
    ind_forms = [_like(f) for f in geo.IND_FORMS]
    out: list[tuple[str, object]] = [
        (
            "india",
            lambda s: "india" in s and not any(b in s for b in geo.INDIA_EXCLUDE),
        ),
        (
            "IND",
            lambda s: (
                (s == "ind" or any(f(s) for f in ind_forms))
                and not any(b in s for b in geo.IND_EXCLUDE)
            ),
        ),
        (
            "subdivision",
            lambda s: any(s.endswith(f", {c}, in") for c in geo.SUBDIVISIONS),
        ),
    ]
    for city, aliases in geo.CITIES.items():
        bad = geo.EXCLUDE.get(city, ())
        out.append(
            (
                city,
                lambda s, a=aliases, b=bad: (
                    any(x in s for x in a) and not any(y in s for y in b)
                ),
            )
        )
    out.append(("state", lambda s: any(st in s for st in geo.STATES)))
    return out


def matches(text: str, clauses: list[tuple[str, object]]) -> str | None:
    """What the filter matches this string on, or None. ``text`` must already be lowered."""
    for label, pred in clauses:
        if pred(text):
            return label
    return None


def _lower(loc: str | None) -> str:
    """The string as SQL sees it. NULL and "" both match nothing, so both fold to ""."""
    return (loc or "").lower()


def is_placeless(low: str) -> bool:
    """True when the whole string is a placeless token, not merely contains one."""
    return _fold(low).strip(" .,-–—()[]") in _PLACELESS


def load_locations(db: Path, table: str) -> list[tuple[str | None, str]]:
    """``(location, ats)`` for every row - projected, so no vector is ever read.

    ``location`` is kept as ``None`` rather than coerced to ``""``. They are different defects
    and the distinction was asked for: a NULL is a row whose scraper never wrote the field at
    all, an empty string is one that wrote a blank. Collapsing them makes "how many rows have
    no location field" unanswerable.
    """
    import lancedb

    t = lancedb.connect(db).open_table(table)
    rows = t.search().select(["location", "ats"]).limit(10_000_000).to_list()
    return [(r.get("location"), r.get("ats") or "") for r in rows]


def verify_model_against_sql(
    db: Path, table: str, rows: list[tuple[str | None, str]], clauses
) -> None:
    """Assert the Python model returns exactly what the production SQL clause returns.

    Everything downstream is a claim about ``geo.where("india")``, so the model standing in for
    it has to be measured equal to it, not argued equal to it — and this is the one line that
    can do that, since LanceDB will execute the real clause. A divergence here (a diacritic the
    Python ``in`` handles differently from SQL ``LIKE``, say) would invalidate every number
    below, so it fails loudly rather than warning.
    """
    import lancedb

    t = lancedb.connect(db).open_table(table)
    n_sql = t.count_rows(filter=geo.where("india"))
    n_py = sum(1 for loc, _ in rows if matches(_lower(loc), clauses))
    verdict = "agree" if n_sql == n_py else f"DISAGREE by {abs(n_sql - n_py):,}"
    print(f"model check: SQL {n_sql:,} vs python {n_py:,} -> {verdict}\n", flush=True)
    if n_sql != n_py:
        raise SystemExit(
            "the audit's model does not reproduce geo.where('india'); fix it first"
        )


def report_field_health(rows: list[tuple[str | None, str]]) -> None:
    """How many rows the filter can even see, before asking how well it sees them.

    NULL and empty-string are counted apart on purpose: a NULL is a scraper that never wrote the
    field, an empty string is one that wrote a blank. Both are unfilterable, but they are
    different bugs to go and fix.
    """
    total = len(rows)
    null = sum(1 for loc, _ in rows if loc is None)
    empty = sum(1 for loc, _ in rows if loc is not None and not loc.strip())
    placeless = sum(
        1 for loc, _ in rows if loc and loc.strip() and is_placeless(_lower(loc))
    )
    print("-- can the filter even see the row? --", flush=True)
    print(f"  {'rows':38} {total:>8,}")
    print(
        f"  {'no location field at all (NULL)':38} {null:>8,}  ({100 * null / total:.1f}%)"
    )
    print(f"  {'field present but empty':38} {empty:>8,}  ({100 * empty / total:.1f}%)")
    print(
        f"  {'placeless string (Remote, N/A, ...)':38} {placeless:>8,}  ({100 * placeless / total:.1f}%)"
    )
    bad = null + empty + placeless
    print(
        f"  {'-> unfilterable by ANY place filter':38} {bad:>8,}  ({100 * bad / total:.1f}%)"
    )

    per: dict[str, list[int]] = {}
    for loc, ats in rows:
        seen = per.setdefault(ats, [0, 0])
        seen[0] += 1
        if not (loc or "").strip() or is_placeless(_lower(loc)):
            seen[1] += 1
    print("\n  per-ATS, worst first - a scraper defect, not a gazetteer one:")
    for ats, (n, b) in sorted(per.items(), key=lambda kv: -kv[1][1] / max(kv[1][0], 1))[
        :12
    ]:
        print(
            f"    {ats:18} {b:>7,}/{n:<8,} {100 * b / max(n, 1):>5.1f}%  {'#' * round(20 * b / max(n, 1))}"
        )
    print()


def report_recall(unmatched: list[tuple[str | None, str]]) -> None:
    """India rows the filter misses, probed with country-tag signals.

    **These probes are no longer independent of the filter.** ADR-0086 moved the ``IND`` and
    ``City, ST, IN`` signals *into* ``geo``, so the buckets built on them are now largely inside
    the thing being audited and read near zero by construction — the same trap that made an
    earlier version of this file print a meaningless 100.00%. They are kept because near-zero is
    now the useful reading: it is a regression check on ADR-0086, not a recall measurement.

    A probe hit that the filter rejects via :data:`geo.IND_EXCLUDE` or :data:`geo.INDIA_EXCLUDE`
    is **not** a miss — it is a guard doing its job (the airport-code string, "Grayslake, Ind").
    Counting those as misses would report the fix as a defect, so they get their own bucket.
    """
    guards = tuple(geo.IND_EXCLUDE) + tuple(geo.INDIA_EXCLUDE)
    buckets: dict[str, Counter[str]] = defaultdict(Counter)
    for loc, _ in unmatched:
        s_low = _lower(loc)
        if _STATE_IN.search(s_low):
            key = "subdivision tail (absorbed by ADR-0086)"
        elif _IND_A3.search(s_low):
            key = "IND alpha-3 (absorbed by ADR-0086)"
        elif _TRAILING_IN.search(s_low):
            side = "Indiana" if any(u in s_low for u in _INDIANA) else "India"
            key = f"trailing ', IN' -> reads as {side}"
        else:
            continue
        if any(g in s_low for g in guards) and "trailing" not in key:
            key = "correctly rejected by a guard (NOT a miss)"
        buckets[key][loc or ""] += 1

    print("-- recall probes --", flush=True)
    residual = 0
    for name in sorted(buckets, key=lambda k: -sum(buckets[k].values())):
        c = buckets[name]
        n = sum(c.values())
        if "absorbed" in name:
            residual += n
        print(f"  {name:44} {n:>6,}  ({len(c):,} distinct)")
        for st, k in c.most_common(5):
            print(f"       {k:>4}  {st[:66]}")
    ambiguous = sum(
        sum(c.values()) for k, c in buckets.items() if k.startswith("trailing")
    )
    print(f"\n  {'-> candidates to READ, not a count to trust':44} {residual:>6,}")
    print(f"  {'   unresolvable from the string alone':44} {ambiguous:>6,}")
    print(
        "     The probe (\\bind\\b) is LOOSER than the filter, which anchors IND to the country-tag\n"
        "     positions — so this bucket mixes real residue ('IND, Remote', 'Client Site - IND -\n"
        "     AMD') with rows the anchoring rightly rejects ('Grayslake, Ind' Illinois, 'King\n"
        "     Street Ind Estate' UK). It is a list to read, never a total to quote.\n"
        "     The ambiguous bucket is a trailing ', IN' — India's alpha-2 AND Indiana's USPS\n"
        "     code, not decidable from the location string. Neither is a recall figure: with the\n"
        "     probes now inside the filter, the only honest bound on unseen misses is the\n"
        "     by-hand read of --unmatched.",
        flush=True,
    )


# The collisions geo.py's own docstring names as knowingly accepted. Each is (canonical place,
# the foreign string that collides). They are the highest-prior false positives in the whole
# design and were previously asserted negligible without ever being counted.
_ACCEPTED_COLLISIONS = (
    ("hyderabad", "pakistan"),
    ("kochi", "japan"),
    ("thane", "thanet"),
    ("madras", "oregon"),
    ("madras", ", or"),
)


def report_accepted_collisions(rows: list[tuple[str | None, str]]) -> None:
    """Count what geo.py's knowingly-accepted collisions actually cost today.

    ``geo.py`` lists these as "accepted as negligible for a tech-jobs corpus". Negligible is a
    quantity, so it should be a measured one - and it is cheap to measure, since each collision
    has a naming signature (the other country or US state) that the India row will not carry.
    """
    print(
        "\n-- geo.py's knowingly-accepted collisions, actually counted --", flush=True
    )
    total = 0
    for place, marker in _ACCEPTED_COLLISIONS:
        hits = [
            loc
            for loc, _ in rows
            if loc and place in _lower(loc) and marker in _lower(loc)
        ]
        total += len(hits)
        eg = ", ".join(sorted({h for h in hits})[:2]) or "-"
        print(f"  {place + ' x ' + marker:26} {len(hits):>5,}  e.g. {eg[:58]}")
    print(f"  {'-> total':26} {total:>5,}")


def report_precision(
    rows: list[tuple[str | None, str]], clauses: list[tuple[str, object]]
) -> dict[str, Counter[str]]:
    """Non-India rows the filter claims, in the two ways a substring design produces them."""
    bleed: Counter[str] = Counter()
    alias_only: Counter[str] = Counter()
    examples: dict[str, Counter[str]] = defaultdict(Counter)
    for loc, _ in rows:
        low = _lower(loc)
        place = matches(low, clauses)
        if place == "india" and not _INDIA_WORD.search(low):
            bleed[loc] += 1
        # Nothing but the alias says this row is Indian - no country word, no state name.
        elif (
            place
            and place not in ("india", "state")
            and "india" not in low
            and not any(s in low for s in geo.STATES)
        ):
            alias_only[place] += 1
            examples[place][loc] += 1

    print("\n-- precision: rows the filter claims that may not be India --")
    print(
        f"  {'country clause is a SUBSTRING (LIKE %india%)':46} {sum(bleed.values()):>6,}"
    )
    print(f"  {'  i.e. matched on india inside another word':46}")
    for s, n in bleed.most_common(8):
        print(f"       {n:>4}  {s[:66]}")
    print(
        f"\n  {'resting on a city alias ALONE (read, do not trust)':46} {sum(alias_only.values()):>6,}"
    )
    for place, n in alias_only.most_common(10):
        eg = ", ".join(s for s, _ in examples[place].most_common(2))
        print(f"    {place:16} {n:>7,}  e.g. {eg[:74]}")
    return examples


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(REPO / "data" / "lancedb"))
    ap.add_argument("--table", default="jobs")
    ap.add_argument(
        "--unmatched",
        type=int,
        default=0,
        help="print the top N unmatched strings, to read by hand",
    )
    ap.add_argument(
        "--suspects", help="dump every distinct string this place claimed alone"
    )
    args = ap.parse_args()

    clauses = india_clauses()
    rows = load_locations(Path(args.db), args.table)
    verify_model_against_sql(Path(args.db), args.table, rows, clauses)
    total = len(rows)
    print(
        f"rows: {total:,}   distinct locations: {len({r[0] for r in rows}):,}\n",
        flush=True,
    )
    report_field_health(rows)

    # One classification pass. This ran three times over the 317k rows before; measured 8.92s
    # -> 4.44s with identical output. Re-running the audit is the whole point of shipping it,
    # so its runtime is a feature.
    matched: list[tuple[str | None, str]] = []
    unmatched: list[tuple[str | None, str]] = []
    by_place: Counter[str] = Counter()
    for loc, ats in rows:
        place = matches(_lower(loc), clauses)
        if place:
            matched.append((loc, ats))
            by_place[place] += 1
        elif (loc or "").strip():
            unmatched.append((loc, ats))
    folded_only = sum(1 for loc, _ in unmatched if matches(_fold(loc), clauses))
    by_ats = Counter(ats for _, ats in matched)
    print(
        f"-- the filter matches {len(matched):,} rows ({100 * len(matched) / total:.1f}% of the table) --"
    )
    print(
        f"  {'carrying no india word at all':44} "
        f"{sum(1 for l, _ in matched if not _INDIA_WORD.search(_lower(l))):>6,}"
    )
    print(f"  {'recoverable by NFKD folding alone':44} {folded_only:>6,}")
    print("\n  per-ATS:")
    for a, n in by_ats.most_common(12):
        print(f"    {a:18} {n:>7,}")
    print("\n  top canonical places:")
    for p, n in by_place.most_common(12):
        print(f"    {p:22} {n:>7,}")
    print()

    report_recall(unmatched)
    examples = report_precision(rows, clauses)
    report_accepted_collisions(rows)

    if args.unmatched:
        c = Counter(loc for loc, _ in unmatched)
        print(
            f"\n--- top {args.unmatched} unmatched strings of {len(c):,} distinct ---"
        )
        for s, n in c.most_common(args.unmatched):
            print(f"  {n:>5}  {s[:78]}")

    if args.suspects:
        strings = examples.get(args.suspects, Counter())
        print(
            f"\n--- {args.suspects}: {len(strings):,} distinct strings on the alias alone ---"
        )
        for s, n in strings.most_common(60):
            print(f"  {n:>5}  {s}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
