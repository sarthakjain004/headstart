#!/usr/bin/env python3
"""How good is the India location filter, measured against the served LanceDB table.

The filter is a raw ``lower(location) LIKE '%alias%'`` OR-chain expanded from
:mod:`headstart.geo` (ADR-0024). It has three independent failure modes and they need three
different kinds of evidence, so this reports them separately rather than as one accuracy score:

**Field health** — a row with no location, or a placeless one ("Remote", "N/A"), can never match
any place filter however good the gazetteer is. That is a ceiling on recall owned by the
*scrapers*, not by ``geo``, which is why it is broken out per ATS.

**Recall** — India rows the filter does not match. This CANNOT be measured by widening the
gazetteer's own terms: the filter is already "india-word OR every city OR every state", so any
net built from those is a strict subset of it and reports 0 misses by construction. (An earlier
pass of this script did exactly that and printed a meaningless 100.00%.) So recall is probed with
signals *independent* of the city map - the ISO alpha-3 ``IND``, and workday's ``City, ST, IN``
subdivision form - plus a by-hand read of the top unmatched strings.

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

# ISO-3166-2 subdivision codes for India's states/UTs, as workday writes them: "Vemagal, KA, IN".
_SUBDIVISIONS = (
    "ap",
    "ar",
    "as",
    "br",
    "cg",
    "ga",
    "gj",
    "hr",
    "hp",
    "jh",
    "ka",
    "kl",
    "mp",
    "mh",
    "mn",
    "ml",
    "mz",
    "nl",
    "od",
    "pb",
    "rj",
    "sk",
    "tn",
    "ts",
    "tr",
    "uk",
    "up",
    "wb",
    "dl",
    "ch",
    "py",
    "jk",
    "la",
    "an",
    "dh",
)
_STATE_IN = re.compile(r",\s*(" + "|".join(_SUBDIVISIONS) + r")\s*,\s*in\.?\s*$")
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
_PLACELESS = (
    "remote",
    "anywhere",
    "multiple locations",
    "various",
    "worldwide",
    "global",
    "work from home",
    "wfh",
    "n/a",
    "-",
    "remote job",
    "us",
    "usa",
)


def _fold(text: str) -> str:
    """Lowercase with diacritics stripped. Used only to size what folding would buy."""
    return "".join(
        c
        for c in unicodedata.normalize("NFKD", (text or "").lower())
        if not unicodedata.combining(c)
    )


def india_clauses() -> list[tuple[str, object]]:
    """Exactly what ``geo.where("india")`` tests, as predicates over ``lower(location)``.

    Modelled off the real clause rather than off ``CITIES`` alone, because three details of it
    change the answer and reading only the city map gets each of them wrong:

    * the country term is a plain substring with its own ``NOT LIKE '%indiana%'`` guard;
    * a city can carry :data:`geo.EXCLUDE` NOT-LIKE guards ("surat" minus "surat thani"), so a
      naive substring test claims rows the filter deliberately rejects;
    * :data:`geo.REGIONS` is *not* part of it - its values are city keys, not alias substrings,
      and ``where("india")`` already iterates every city, so folding regions in would double-count.

    Order matters only for attribution (first match names the row), never for the verdict.
    """
    out: list[tuple[str, object]] = [
        ("india", lambda s: "india" in s and "indiana" not in s)
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


def is_placeless(low: str) -> bool:
    """True when the whole string is a placeless token, not merely contains one."""
    return low.strip(" .,-–—()[]") in _PLACELESS


def load_locations(db: Path, table: str) -> list[tuple[str, str]]:
    """``(location, ats)`` for every row - projected, so no vector is ever read."""
    import lancedb

    t = lancedb.connect(db).open_table(table)
    rows = t.search().select(["location", "ats"]).limit(10_000_000).to_list()
    return [(r.get("location") or "", r.get("ats") or "") for r in rows]


def report_field_health(rows: list[tuple[str, str]]) -> None:
    """How many rows the filter can even see, before asking how well it sees them."""
    total = len(rows)
    blank = sum(1 for loc, _ in rows if not loc.strip())
    placeless = sum(1 for loc, _ in rows if loc.strip() and is_placeless(loc.lower()))
    print("-- can the filter even see the row? --")
    print(f"  {'rows':38} {total:>8,}")
    print(
        f"  {'blank / missing location':38} {blank:>8,}  ({100 * blank / total:.1f}%)"
    )
    print(
        f"  {'placeless string (Remote, N/A, ...)':38} {placeless:>8,}  ({100 * placeless / total:.1f}%)"
    )
    bad = blank + placeless
    print(
        f"  {'-> unfilterable by ANY place filter':38} {bad:>8,}  ({100 * bad / total:.1f}%)"
    )

    per: dict[str, list[int]] = {}
    for loc, ats in rows:
        seen = per.setdefault(ats, [0, 0])
        seen[0] += 1
        if not loc.strip() or is_placeless(loc.lower()):
            seen[1] += 1
    print("\n  per-ATS, worst first - a scraper defect, not a gazetteer one:")
    for ats, (n, b) in sorted(per.items(), key=lambda kv: -kv[1][1] / max(kv[1][0], 1))[
        :12
    ]:
        print(
            f"    {ats:18} {b:>7,}/{n:<8,} {100 * b / max(n, 1):>5.1f}%  {'#' * round(20 * b / max(n, 1))}"
        )
    print()


def report_recall(unmatched: list[tuple[str, str]]) -> None:
    """India rows the filter misses, probed with signals independent of the city gazetteer.

    Reported as three buckets of decreasing confidence rather than one total, because the last
    one genuinely cannot be resolved from the string alone and a single number would have to
    guess. ``IND`` and ``City, ST, IN`` are unambiguous; a bare trailing ", IN" is India's
    alpha-2 *and* Indiana's USPS code, so it is split against observed Indiana cities and both
    halves are shown.
    """
    buckets: dict[str, Counter[str]] = defaultdict(Counter)
    for loc, _ in unmatched:
        low = loc.lower()
        if _STATE_IN.search(low):
            buckets["state code + IN (India, unambiguous)"][loc] += 1
        elif _IND_A3.search(low):
            buckets["IND alpha-3 (India, unambiguous)"][loc] += 1
        elif _TRAILING_IN.search(low):
            side = "Indiana" if any(u in low for u in _INDIANA) else "India"
            buckets[f"trailing ', IN' -> reads as {side}"][loc] += 1

    print("-- recall: India rows the filter MISSES --")
    firm = 0
    for name in sorted(buckets, key=lambda k: -sum(buckets[k].values())):
        c = buckets[name]
        n = sum(c.values())
        if "Indiana" not in name:
            firm += n
        print(f"  {name:44} {n:>6,}  ({len(c):,} distinct)")
        for s, k in c.most_common(5):
            print(f"       {k:>4}  {s[:66]}")
    print(f"\n  {'-> India rows missed (excl. the Indiana half)':44} {firm:>6,}")
    return firm


def report_precision(rows, clauses, unmatched_n: int) -> None:
    """Non-India rows the filter claims, in the two ways a substring design produces them."""
    bleed: Counter[str] = Counter()
    alias_only: Counter[str] = Counter()
    examples: dict[str, Counter[str]] = defaultdict(Counter)
    for loc, _ in rows:
        low = loc.lower()
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
    total = len(rows)
    print(f"rows: {total:,}   distinct locations: {len({r[0] for r in rows}):,}\n")
    report_field_health(rows)

    matched = [(loc, ats) for loc, ats in rows if matches(loc.lower(), clauses)]
    unmatched = [
        (loc, ats)
        for loc, ats in rows
        if loc.strip() and not matches(loc.lower(), clauses)
    ]
    folded_only = sum(1 for loc, _ in unmatched if matches(_fold(loc), clauses))

    by_place = Counter(matches(loc.lower(), clauses) for loc, _ in matched)
    by_ats = Counter(ats for _, ats in matched)
    print(
        f"-- the filter matches {len(matched):,} rows ({100 * len(matched) / total:.1f}% of the table) --"
    )
    print(
        f"  {'carrying no india word at all':44} "
        f"{sum(1 for l, _ in matched if not _INDIA_WORD.search(l.lower())):>6,}"
    )
    print(f"  {'recoverable by NFKD folding alone':44} {folded_only:>6,}")
    print("\n  per-ATS:")
    for a, n in by_ats.most_common(12):
        print(f"    {a:18} {n:>7,}")
    print("\n  top canonical places:")
    for p, n in by_place.most_common(12):
        print(f"    {p:22} {n:>7,}")
    print()

    firm = report_recall(unmatched)
    print(
        f"  {'-> recall against observed India rows':44} "
        f"{100 * len(matched) / (len(matched) + firm):>5.2f}%"
    )
    examples = report_precision(rows, clauses, len(unmatched))

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
